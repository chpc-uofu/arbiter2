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
import logging
import permissions
from collector import Collector, TimeRecorder
from cfgparser import cfg, shared
import cinfo
import usage
import statuses
import high_usage_watcher
import actions
import datetime
import triggers

logger = logging.getLogger("arbiter." + __name__)


def run(args):
    """
    The main loop of arbiter that collects information and evaluates users.
    """
    create_databases()

    if cfg.general.debug_mode:
        logger.info("Permissions and quotas won't be set since debug mode is "
                    "on.")
    acct_slice = cinfo.UserSlice(args.acct_uid) if args.acct_uid else None

    # Setup collector to get usage information from cgroups and processes.
    # (when .run(), this information is return into User() objects)
    collector = Collector(
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
    high_usage_timer = TimeRecorder()

    # Record last update to exit file
    last_exit_file_update = -1
    if args.exit_file and os.path.exists(args.exit_file):
        last_exit_file_update = os.path.getctime(args.exit_file)

    # Analyze the information that has been collected
    while True:
        allusers, users = collector.run()
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

                # check if their badness hasn't expired
                if valid_db_badness(uid, db_badness):
                    user_db_badness = db_badness[uid]
                    timestamp = user_db_badness.pop("timestamp")
                    logger.debug("%s's badness are being imported: %s",
                                 uid_name, user_db_badness)
                    user_obj.set_badness(user_db_badness, timestamp)

            if not cfg.general.debug_mode and cgroup.active():
                if args.sudo_permissions:
                    set_permissions(user_obj)
                set_quotas(user_obj)

            # Evaluate active users
            latest_badness = user_obj.badness_history[0]["badness"]
            has_been_bad = any(b != 0.0 for b in latest_badness.values())
            totally_good = all(b == 0 for b in latest_badness.values())
            in_penalty = statuses.lookup_is_penalty(status.current)
            if cgroup.active() or has_been_bad or in_penalty:
                add_badness(user_obj)
                triggers.evaluate(user_obj)

            # Remove inactive users
            elif not cgroup.active() and totally_good and not in_penalty:
                logger.debug("No longer tracking %s (logged out and had good "
                             "behavior)", uid_name)
                collector.delete_user(uid)

        # Watch for high usage (overall, not user-specific) on the node
        # check if there is high usage on the node and send email if applicable
        if (cfg.high_usage_watcher.high_usage_watcher
                and high_usage_timer.delta <= 0
                and high_usage_watcher.is_high_usage(allusers_hist)):
            high_usage_watcher.send_high_usage_email(allusers.usage, users)
            high_usage_timer.start(cfg.high_usage_watcher.timeout)


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
    new_badness = {
        metric: min(100.0, max(0.0, score + delta_badness[metric]))
        for metric, score in user_obj.badness_history[0]["badness"].items()
    }
    # Penalty status doesn't accrue badness
    if statuses.lookup_is_penalty(user_obj.status.current):
        delta_badness = new_badness = {metric: 0.0 for metric in new_badness}

    # Update the status database and the user object
    statuses.add_badness(user_obj.uid, record_time, new_badness)
    user_obj.add_badness(new_badness, delta_badness, record_time)


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


def valid_db_badness(uid, db_badness):
    """
    Returns whether the user's badness from the badness dictionary is valid.

    uid: int
        The uid of the user to check.
    badness_db_dict: {}
        A dictionary pulled from the badness database.
    """
    user_db_badness = db_badness.get(uid, {})
    user_db_badness_copy = user_db_badness.copy()
    last_updated = user_db_badness_copy.pop("timestamp", 0)
    invalid_badness_time = cfg.badness.imported_badness_timeout + last_updated
    return (
        invalid_badness_time > time.time()
        and sum(user_db_badness_copy.values()) > 0
    )


def create_databases():
    """
    Checks whether the databases defined in configuration exists and creates
    ones that don't.
    """
    # TODO: Only the status database is created here. logdb implicitly creates
    #       one if it doesn't exist in it's functions. This should be made
    #       consistent.
    status_file = cfg.database.log_location + "/" + shared.statusdb_name
    if not os.path.isfile(status_file):
        logger.info("Failed to find status database; creating one at %s",
                    status_file)
        statuses.create_status_database(status_file, shared.status_tablename,
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
            actions.update_status(cgroup, status.current, status.default)
        except FileNotFoundError:
            logger.debug("Limit could no be set because the user disappeared")
        except OSError as err:
            logger.debug("Limit could not be set because %s", err)
