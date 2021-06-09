#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2019-2020 Center for High Performance Computing <helpdesk@chpc.utah.edu>
#
# SPDX-License-Identifier: GPL-2.0-only

"""
Version 2 of Arbiter; uses cgroups for monitoring and managing behavior.
"""

import logging
import subprocess
import sys

from cfgparser import cfg, shared
import actions
import collector
import exit_file_watcher
import high_usage_watcher
import integrations
import logdb
import permissions
import statusdb
import sysinfo
import triggers

logger = logging.getLogger("arbiter." + __name__)
service_logger = logging.getLogger("arbiter_service")
null_logger = logging.getLogger("null")


def run(args):
    """
    The main loop of arbiter that collects information and evaluates users.
    """
    statusdb_obj, statusdb_cleaner_obj = create_statusdb()
    logdb_rotation_timer, logdb_obj = create_logdb()
    collector_obj = create_collector(args.rhel7_compat)
    if cfg.high_usage_watcher.high_usage_watcher:
        logger.info("Will watch for high usage")
        high_usage_watcher_obj = high_usage_watcher.HighUsageWatcher(logdb_obj)

    initial_badness = read_initial_badness(statusdb_obj)

    if cfg.general.debug_mode:
        logger.info("Permissions and quotas won't be set since debug mode is "
                    "on.")
    elif args.acct_uid:
        acct_slice = permissions.AccountingUserSlice(args.acct_uid, logger)

    # Watch for changes to the optional given exit file; exit if updated
    if args.exit_file:
        logger.info("Will watch for changes to exit file %s", args.exit_file)
        exit_file_watcher_obj = exit_file_watcher.ExitFileWatcher(args.exit_file)

    # Analyze the information that has been collected
    while True:
        allusers, users = collector_obj.run()
        if cfg.high_usage_watcher.high_usage_watcher:
            high_usage_watcher_obj.add_usage(allusers)

        if args.exit_file and exit_file_watcher_obj.has_been_updated():
            # 143 is constructed via 128 (typically used to indicate a exit on
            # a signal) + 15 (the signal recieved, typically SIGTERM, arbiter
            # doesn't actually recieve this, but we pretend that it did from
            # the exit file).
            sys.exit(143)

        # It's really annoying to have inconsistent logs dates, so we'll
        # always create empty ones that will be filled as needed.
        if logdb_rotation_timer.expired():
            logdb_rotation_timer = logdb_obj.rotate()

        # If accounting flag and the cpu or mem hierachy doesn't exist
        if not cfg.general.debug_mode and args.acct_uid:
            acct_slice.create_slice_if_needed()

        # For each user, add information and evaluate them
        # .copy() -> we delete user objects while iterating; not a deep copy
        for user_obj in users.copy().values():
            if user_obj.new():
                new_user_actions(user_obj, initial_badness, statusdb_obj)

            should_delete = evaluate_user(user_obj, statusdb_obj, logdb_obj,
                                          args.sudo_permissions)
            if should_delete:
                collector_obj.delete_user(user_obj.uid)

        # Watch for high usage (overall, not user-specific) on the node,
        # apply before sync so that the per-user quotas we show are reflective
        # of the current state
        if cfg.high_usage_watcher.high_usage_watcher:
            high_usage_watcher_obj.send_email_if_high_usage(users)

        # Once we've evaluated everyone, sync the badness in bulk
        sync_badness(users, statusdb_obj)

        # Note: This cannot be done before evaluating users since the
        #       synchronization algorithm implictly relies on authoritative
        #       hosts (hosts where the user originally got in penalty on)
        #       lowering penalties and sending emails before trying to sync
        sync_statuses(users, statusdb_obj)

        # This periodic cleanup ensures that in case of Arbiter or network
        # failure we still ensure statusdb doesn't contain unnecessary values
        statusdb_cleaner_obj.cleanup_if_needed()


def sync_badness(users, statusdb_obj):
    """
    Given a dictionary of user.User objects identified by their uid,
    syncronizes user badness with the database.

    users: dict
        A dictionary of user.User objects, identified by their uid.
    statusdb_obj: statusdb.StatusDB
        A StatusDB object to use.
    """
    user_badness = {
        uid: user_obj.badness_obj
        for uid, user_obj in users.items()
    }
    try:
        statusdb_obj.write_badness(user_badness)
    except statusdb.common_db_errors as err:
        logger.warning("Failed to bulk update badness in statusdb: %s", err)


def sync_statuses(users, statusdb_obj):
    """
    Given a dictionary of user.User objects identified by their uid,
    syncronizes the statuses with the database.

    users: dict
        A dictionary of user.User objects, identified by their uid.
    statusdb_obj: statusdb.StatusDB
        A StatusDB object to use.
    """
    user_statuses = {uid: user_obj.status for uid, user_obj in users.items()}
    try:
        # Ensure other hosts are aware of our changes. We update statuses in
        # triggers.py, but because the database can fail, we should try and
        # write out our statuses every interval here
        statusdb_obj.write_status(user_statuses)

        # Now we'll sync and compare our statuses with other hosts
        modified_user_hosts = statusdb_obj.synchronize_status_from_other_hosts(user_statuses)
    except statusdb.common_db_errors as err:
        logger.warning("Failed to synchronize user statuses from "
                       "statusdb: %s", err)
        return

    for uid, repl_hostname in modified_user_hosts.items():
        # If we updated our own host, no need to log that
        if repl_hostname == sysinfo.hostname:
            continue

        username = "{} ({})".format(*integrations._get_name(uid))
        service_logger.info("User %s's status on %s was synced from %s",
                            username, sysinfo.hostname,
                            repl_hostname)


def evaluate_user(user_obj, statusdb_obj, logdb_obj, sudoers):
    """
    Evaluates a user based on their usage and takes the appropriate actions.
    Returns whether the user should be deleted.

    user_obj: user.User()
        The user object corresponding to a user.
    statusdb_obj: statusdb.StatusDB
        A StatusDB object to use.
    logdb_obj: logdb.LogDB
        A LogDB object to use.
    sudoers: bool
        Whether to use sudoer permissions.
    """
    if not cfg.general.debug_mode and user_obj.cgroup.active():
        if sudoers:
            set_permissions(user_obj)
        actions.set_quotas(user_obj)

    if not user_obj.needs_tracking():
        logger.debug("No longer tracking %s (logged out and had good "
                     "behavior)", user_obj.uid_name)
        return True

    user_obj.update_badness_from_last_usage()
    triggers.evaluate(user_obj, statusdb_obj, logdb_obj)
    return False


def new_user_actions(user_obj, initial_badness, statusdb_obj):
    """
    Runs actions against new users.

    user_obj: user.User()
        The user object corresponding to a user.
    initial_badness: dict
        A dictionary of per-user badness objects from startup.
    statusdb_obj: statusdb.StatusDB
        A StatusDB object to use.
    """
    logger.debug("%s is new and has status: %s", user_obj.uid_name, user_obj.status)
    if user_obj.uid not in initial_badness or user_obj.status.in_penalty():
        return

    badness_obj = initial_badness[user_obj.uid]
    if not badness_obj.expired():
        logger.debug("%s's badness are being imported: %s",
                     user_obj.uid_name, badness_obj)
        user_obj.set_badness(badness_obj)
    else:
        # We don't really want/need it in the database if it's expired
        try:
            statusdb_obj.remove_badness(user_obj.uid)
        except statusdb.common_db_errors as err:
            logger.warning("Failed to remove %s's out of date badness in "
                           "statusdb: %s", user_obj.uid_name, err)


def read_initial_badness(statusdb_obj):
    """
    Returns the initial per-user badness objects.

    statusdb_obj: statusdb.StatusDB
        A StatusDB object to use.
    """
    try:
        return statusdb_obj.read_badness()
    except statusdb.common_db_errors as err:
        logger.warning("Failed to read initial badness scores from statusdb: "
                       "%s", err)
        return {}


def create_statusdb():
    """
    Checks whether the statusdb defined in configuration exists and creates
    it if it doesn't. Returns a statusdb.StatusDB object.
    """
    statusdb_obj = statusdb.lookup_statusdb(cfg_db_consistency=True)
    # May throw an error, but we kinda need the database to be up...
    was_created, is_v2 = statusdb_obj.create_status_database_if_needed(v2=True)

    if was_created:
        logger.info("Failed to find statusdb database; created one at %s",
                    statusdb_obj.redacted_url())
    elif is_v2:
        logger.info("Using existing statusdb database at %s",
                    statusdb_obj.redacted_url())
    else:
        logger.info("Using existing v1 statusdb database at %s (no syncing)",
                    statusdb_obj.redacted_url())

    cleanup_interval = 60 * 60  # Every hour or so seems reasonable
    statusdb_cleaner_obj = statusdb.StatusDBCleaner(statusdb_obj, cleanup_interval)
    return statusdb_obj, statusdb_cleaner_obj


def create_logdb():
    """
    Creates a logdb based on the last rotation and returns a logdb.LogDB
    object as well as a rotation timer.
    """
    rotate_period_days = cfg.database.log_rotate_period
    logdb_path_fmt = cfg.database.log_location + "/" + shared.logdb_name
    logdb_obj = logdb.RotatingLogDB(logdb_path_fmt, rotate_period_days)

    # May throw an error, but we're not smart enough to lazily create it
    rotation_timer = logdb_obj.rotate_if_needed()
    return rotation_timer, logdb_obj


def create_collector(rhel7_compat):
    """
    Creates a collector object based on the configuration.
    """
    # Setup collector to get usage information from cgroups and processes.
    # (when .run(), this information is return into User() objects)
    poll_interval = (cfg.general.arbiter_refresh /
                     cfg.general.history_per_refresh /
                     cfg.general.poll)
    logger.debug("Initializing the collector with an interval of %ss, %s "
                 "history points and %s polls per point (polls every %ss)",
                 cfg.general.arbiter_refresh, cfg.general.history_per_refresh,
                 cfg.general.poll, poll_interval)
    return collector.Collector(
        cfg.general.history_per_refresh,
        cfg.general.arbiter_refresh // cfg.general.history_per_refresh,
        poll=cfg.general.poll,
        rhel7_compat=rhel7_compat
    )


def set_permissions(user_obj):
    """
    Makes sure the user's cgroup files have the correct write permissions.

    user_obj: user.User()
        The user object corresponding to a user.
    """
    if cfg.general.debug_mode:
        return

    uid = user_obj.uid
    uid_name = user_obj.uid_name
    groupname = cfg.self.groupname
    memsw = cfg.processes.memsw

    # Set permissions. There is a race condition here with the check and
    # setting, but we catch these types of things
    if not permissions.has_write_permissions(uid, memsw, null_logger):
        warning = "Failed to set file permissions for {}".format(uid_name)
        logger.debug("Setting file permissions for %s", uid_name)
        try:
            permissions.set_file_permissions(uid, groupname, memsw, logger)
        except subprocess.CalledProcessError:
            logger.warning("%s due to a permissions error", warning)
        except FileNotFoundError:
            logger.debug("%s due to the cgroup file not existing", warning)
        except OSError as err:
            logger.debug("%s due to %s", warning, err)
