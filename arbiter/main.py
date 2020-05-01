#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only

"""
Version 2 of Arbiter; uses cgroups for monitoring and managing behavior.
"""

import grp
import os
import subprocess
import sys
import time
import collections
import datetime
import logging
import permissions
import collector
from cfgparser import cfg, shared
import cinfo
import logdb
import usage
import statuses
import high_usage_watcher
import actions
import triggers

logger = logging.getLogger("arbiter." + __name__)


def run(args):
    """
    The main loop of arbiter that collects information and evaluates users.
    """
    create_databases()
    logdb_rotation_timer = rotate_logdb()

    acct_slice = cinfo.UserSlice(args.acct_uid) if args.acct_uid else None

    # Setup collector to get usage information from cgroups and processes.
    # (when .run(), this information is return into User() objects)
    poll_interval = (cfg.general.arbiter_refresh /
                     cfg.general.history_per_refresh /
                     cfg.general.poll)
    logger.debug("Initializing the collector with an interval of %ss, %s "
                 "history points and %s polls per point (polls every %ss)",
                 cfg.general.arbiter_refresh, cfg.general.history_per_refresh,
                 cfg.general.poll, poll_interval)
    collector_obj = collector.Collector(
        cfg.general.history_per_refresh,
        cfg.general.arbiter_refresh // cfg.general.history_per_refresh,
        poll=cfg.general.poll,
        rhel7_compat=args.rhel7_compat
    )

    # Get all the old badness scores (made up of different metrics in a dict)
    db_badness = statuses.read_badness()
    allusers_hist = collections.deque(maxlen=cfg.high_usage_watcher.threshold_period)
    for _ in range(cfg.high_usage_watcher.threshold_period):
        allusers_hist.appendleft(usage.metrics.copy())
    high_usage_timer = collector.TimeRecorder()

    # Record last update to exit file
    last_exit_file_update = -1
    if args.exit_file and os.path.exists(args.exit_file):
        last_exit_file_update = os.path.getctime(args.exit_file)

    # Analyze the information that has been collected
    while True:
        allusers, users = collector_obj.run()
        allusers_hist.appendleft(allusers.usage)

        if exit_file_updated(args.exit_file, last_exit_file_update):
            exit_time = os.path.getctime(args.exit_file)
            logger.error(
                "Exiting because %s was updated at %s",
                args.exit_file,
                datetime.datetime.utcfromtimestamp(exit_time).isoformat()
            )
            # 143 is constructed via 128 (typically used to indicate a exit on
            # a signal) + 15 (the signal recieved, typically SIGTERM, arbiter
            # doesn't actually recieve this, but we pretend that it did from
            # the exit file).
            sys.exit(143)

        # It's really annoying to have inconsistent logs dates, so we'll
        # always create empty ones that will be filled as needed.
        if logdb_rotation_timer.delta <= 0:
            logdb_rotation_timer = rotate_logdb()

        # If accounting flag and the cpu or mem hierachy doesn't exist
        if (args.acct_uid and
                not any(map(acct_slice.controller_exists, ("memory", "cpu")))):
            logger.warning("Persistent user has disappared. Attempting to "
                           "recreate the slice...")
            permissions.turn_on_cgroups_acct(args.acct_uid)

        # For each user, add information and evaluate them
        for uid, user_obj in users.copy().items():
            status = user_obj.status
            cgroup = user_obj.cgroup
            uid_name = user_obj.uid_name

            if user_obj.new():
                logger.debug("%s is new and has status: %s", uid_name, status)

                if uid in db_badness:
                    badness_entry = db_badness[uid]
                    last_updated = badness_entry.pop("timestamp", 0)
                    timeout = cfg.badness.imported_badness_timeout
                    non_zero_badness = any(b != 0 for b in badness_entry.values())
                    if last_updated > time.time() - timeout and non_zero_badness:
                        logger.debug("%s's badness are being imported: %s",
                                     uid_name, badness_entry)
                        user_obj.set_badness(badness_entry, last_updated)
                    else:
                        statuses.remove_badness(uid)

            if not cfg.general.debug_mode and cgroup.active():
                if args.sudo_permissions:
                    set_permissions(user_obj)
                set_quotas(user_obj)

            # Evaluate active users
            latest_badness = user_obj.badness_history[0]["badness"]
            totally_good = all(b == 0 for b in latest_badness.values())
            in_penalty = statuses.lookup_is_penalty(status.current)
            has_occurrences = status.occurrences > 0
            should_be_deleted = (
                not cgroup.active()
                and totally_good
                and not in_penalty
                and not has_occurrences
            )
            if should_be_deleted:
                logger.debug("No longer tracking %s (logged out and had good "
                             "behavior)", uid_name)
                collector_obj.delete_user(uid)
            else:
                add_badness(user_obj)
                triggers.evaluate(user_obj)

        # Watch for high usage (overall, not user-specific) on the node
        # check if there is high usage on the node and send email if applicable
        if (cfg.high_usage_watcher.high_usage_watcher
                and high_usage_timer.delta <= 0
                and high_usage_watcher.is_high_usage(allusers_hist)):
            high_usage_watcher.send_high_usage_email(allusers.usage, users)
            high_usage_timer.start_now(cfg.high_usage_watcher.timeout)


def exit_file_updated(exit_file, last_updated):
    """
    Returns whether the exit file has been updated and is owned by the group
    name specified in the configuration.

    exit_file: str
        The file/directory to check.
    last_updated: float
        The last time the file was updated.
    """
    owned_by_group = False
    if exit_file and os.path.exists(exit_file):
        file_gid = os.stat(exit_file).st_gid
        owned_by_group = grp.getgrgid(file_gid).gr_name == cfg.self.groupname
    return owned_by_group and os.path.getctime(exit_file) > last_updated


def add_badness(user_obj):
    """
    Calculates a badness score for the user and sets that value in the user
    object.

    user_obj: user.User()
        The user object corresponding to a user.
    """
    record_time = int(time.time())
    # Calculate the delta badness dictionary based on latest collector data
    delta_badness = triggers.calc_badness(user_obj)

    # Take the delta badness delta and calculate the resulting badness dict
    # Also, limit to 0 and 100
    prev_badness = user_obj.badness_history[0]["badness"]
    new_badness = {
        metric: min(100.0, max(0.0, score + delta_badness[metric]))
        for metric, score in prev_badness.items()
    }
    # Penalty status doesn't accrue badness
    if statuses.lookup_is_penalty(user_obj.status.current):
        delta_badness = new_badness = {metric: 0.0 for metric in new_badness}

    # Update the status database and the user object
    user_obj.add_badness(new_badness, delta_badness, record_time)
    if any(score != 0.0 for score in new_badness.values()):
        statuses.add_badness(user_obj.uid, record_time, new_badness)
    # Remove from the status database if badness score has dropped to zero
    elif all(score != 0.0 for score in prev_badness.values()):
        statuses.remove_badness(user_obj.uid)


def mostly_eq(lvalue, rvalue, fudge=0.05):
    """
    Returns whether the two values are mostly equal based on the fudge factor.

    lvalue: float
        The value on the left.
    rvalue: float
        The value on the right.
    fudge: float
        The margin of error to account for. i.e. the fudge factor
    """
    return lvalue >= rvalue * (1 - fudge) and lvalue <= rvalue * (1 + fudge)


def create_databases():
    """
    Checks whether the databases defined in configuration exists and creates
    ones that don't.
    """
    statusdb_path = cfg.database.log_location + "/" + shared.statusdb_name
    if not os.path.isfile(statusdb_path):
        logger.info("Failed to find status database; creating one at %s",
                    statusdb_path)
        statuses.create_status_database(statusdb_path, shared.status_tablename,
                                        shared.badness_tablename)


def set_permissions(user_obj):
    """
    Makes sure the user's cgroup files have the correct write permissions.

    user_obj: user.User()
        The user object corresponding to a user.
    """
    uid = user_obj.uid
    uid_name = user_obj.uid_name
    groupname = cfg.self.groupname
    memsw = cfg.processes.memsw

    # Set permissions
    if not permissions.has_write_permissions(uid, memsw):
        warning = "Failed to set file permissions for {}".format(uid_name)
        logger.debug("Setting file permissions for %s", uid_name)
        try:
            permissions.set_file_permissions(uid, groupname, memsw)
        except subprocess.CalledProcessError:
            logger.warning("%s due to a permissions error", warning)
        except FileNotFoundError:
            logger.debug("%s due to the cgroup file not existing", warning)
        except OSError as err:
            logger.debug("%s due to %s", warning, err)


def set_quotas(user_obj):
    """
    Makes sure the user has their appropriate quotas.

    user_obj: user.User()
        The user object corresponding to a user.
    """
    uid = user_obj.uid
    memsw = cfg.processes.memsw
    status = user_obj.status
    cgroup = user_obj.cgroup

    try:
        # mostly_eq() because we can't set a lower memory limit (e.g. putting
        # someone in penalty) than a cgroup already has, and our subsequent
        # attempts to set this limit lower will fail quite often. It's enough
        # to call it done if it's mostly equal
        eq_cpu_quota = mostly_eq(user_obj.cpu_quota, cgroup.cpu_quota())
        eq_mem_quota = mostly_eq(user_obj.mem_quota,
                                 cinfo.bytes_to_pct(cgroup.mem_quota(memsw)))
    except OSError:
        # If user disappears, don't want to set limit
        return

    # Apply quotas
    if not eq_cpu_quota or not eq_mem_quota:
        logger.debug("Applying limits for %s", uid)
        try:
            actions.update_status(user_obj, status.current)
        except FileNotFoundError:
            logger.debug("Limit could no be set because the user disappeared")
        except OSError as err:
            logger.debug("Limit could not be set because %s", err)


def rotate_logdb():
    """
    Rotates the logdb database and returns a new date timer for when logdb
    should be rotated again.
    """
    today = datetime.date.today()
    rotate_period = datetime.timedelta(days=cfg.database.log_rotate_period)
    logdb_rotation_timer = DateRecorder()
    logdb_path_fmt = cfg.database.log_location + "/" + shared.logdb_name
    last_rotation = logdb.last_rotation_date(logdb_path_fmt, shared.log_datefmt)
    next_logdb_date = last_rotation + rotate_period

    # We haven't ever created a logdb
    if last_rotation == datetime.date.min:
        new_logdb_path = logdb_path_fmt.format(
            today.strftime(shared.log_datefmt)
        )
        logger.info("Failed to find logdb database; creating one at %s",
                    new_logdb_path)
        logdb.create_log_database(new_logdb_path)
        logdb_rotation_timer.start_at(next_logdb_date, rotate_period)

    # We need a logdb rotation today
    elif next_logdb_date == today:
        new_logdb_path = logdb_path_fmt.format(
            next_logdb_date.strftime(shared.log_datefmt)
        )
        logger.info("Last logdb rotation was on %s; creating new empty "
                    "database at %s.", last_rotation, new_logdb_path)
        logdb.create_log_database(new_logdb_path)
        logdb_rotation_timer.start_at(next_logdb_date, rotate_period)

    # We missed a logdb rotation
    elif next_logdb_date < today:
        missed_delta = (today - rotate_period) - last_rotation
        new_aligned_logdb_date = today - (missed_delta % rotate_period)
        new_aligned_logdb_path = logdb_path_fmt.format(
            new_aligned_logdb_date.strftime(shared.log_datefmt)
        )
        logger.info("Last logdb rotation was on %s, %s day%s more than the "
                    "rotate period; creating new empty and aligned database "
                    "at %s.", last_rotation, missed_delta.days,
                    "s" if missed_delta.days > 1 else "",
                    new_aligned_logdb_path)
        logdb.create_log_database(new_aligned_logdb_path)
        logdb_rotation_timer.start_at(new_aligned_logdb_date, rotate_period)

    # We don't need a logdb rotation
    else:
        logger.info("Last logdb rotation was on %s; using existing database.",
                    last_rotation)
        logdb_rotation_timer.start_at(last_rotation, rotate_period)
    return logdb_rotation_timer


class DateRecorder(collector.TimeRecorder):
    """
    Accurately record changes in dates.
    """

    def __init__(self):
        super().__init__()
        self.start_time = datetime.date.today()
        self.waittime = datetime.timedelta(days=0)

    def start_now(self, waittime):
        """
        Starts the time recorder.
        """
        self.start_time = datetime.date.today()
        self.waittime = waittime

    def start_at(self, start_date, waittime):
        """
        Starts the time recorder at a specified datetime.
        """
        self.start_time = start_date
        self.waittime = waittime

    @property
    def delta(self):
        """
        Returns how much waiting is left.
        """
        return (self.waittime - self.time_since_start).days

    @property
    def time_since_start(self):
        """
        Returns the amount of time since the start time in a timedelta.
        """
        return datetime.date.today() - self.start_time
