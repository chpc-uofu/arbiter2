# SPDX-FileCopyrightText: Copyright (c) 2019-2020 Center for High Performance Computing <helpdesk@chpc.utah.edu>
#
# SPDX-License-Identifier: GPL-2.0-only

"""
Makes decisions on what actions should be made based on a user.User() (Actions
are "triggered"). These triggers, and the action calls are defined in
evaluate(). This method is called every arbiter interval for each active user.
"""

import logging
import time

from cfgparser import cfg
import actions
import integrations
import statusdb
import sysinfo

logger = logging.getLogger("arbiter." + __name__)
service_logger = logging.getLogger("arbiter_service")


def evaluate(user_obj, statusdb_obj, logdb_obj):
    """
    When run, checks the specified triggers and takes the specified action
    associated.

    user_obj: user.User()
        A user to evaluate.
    statusdb_obj: statusdb.StatusDB
        A StatusDB object to use.
    logdb_obj: logdb.LogDB
        A LogDB object to use.
    """
    username = "{} ({})".format(*integrations._get_name(user_obj.uid))

    if user_obj.status.in_penalty():
        logger.debug("%s has status: %s", user_obj.uid_name, user_obj.status)

    # Only evaluate users who are not in penalty
    if not user_obj.status.in_penalty():

        # We found ourseleves a violation!
        if user_obj.badness_obj.is_violation():
            upgrade_penalty(user_obj, username, statusdb_obj, logdb_obj)

        # If the user is being bad, no violation just yet
        elif user_obj.badness_obj.is_bad():
            log_user_badness(user_obj, username)
            if user_obj.status.has_occurrences():
                reset_occurrences_timeout(user_obj, username, statusdb_obj)

        # The user is being good and occurrences has timed out
        elif user_obj.status.has_occurrences() and user_obj.status.occurrences_expired():
            lower_occurrences(user_obj, username, statusdb_obj)

    # Lower status for bad users past a certain time
    elif user_obj.status.penalty_expired():
        downgrade_penalty(user_obj, username, statusdb_obj)

    # If their in penalty, but haven't been released
    else:
        timeleft = int(time.time()) - user_obj.status.timestamp
        logger.debug("%s has spent: %s seconds in penalty of a required %s",
                     user_obj.uid_name, timeleft, user_obj.status.penalty_timeout())


def upgrade_penalty(user_obj, username, statusdb_obj, logdb_obj):
    """
    Applies a penalty status to the user and sets lowered cgroup quotas.

    user_obj: user.User()
        The user to update in statusdb.
    username: str
        The user's username.
    statusdb_obj: statusdb.StatusDB
        A StatusDB object to use.
    logdb_obj: logdb.LogDB
        A LogDB object to use.
    """
    logger.info("Increasing the penalty status of %s", user_obj.uid_name)
    if not user_obj.status.authoritative():
        logger.debug("Overriding previous authority %s of %s to upgrade "
                     "penalty on %s", user_obj.status.authority,
                     user_obj.uid_name, sysinfo.hostname)

    new_status_group = user_obj.status.upgrade_penalty()
    user_obj.badness_obj.reset()  # Penalized users are not evaluated; set to 0.0

    # Want to ensure badness drops to 0.0 in statusdb after violation,
    # otherwise if there is db + arbiter failure then the user will get a 100
    # badness when their penalty is dropped after arbiter restarts (possibly
    # resulting in another violation immediately after). No garuantee here,
    # but slightly safer than bulk update
    try_update_statusdb_for_user(user_obj, statusdb_obj, include_badness=True)

    if cfg.general.debug_mode:
        logger.debug("Not setting quotas because debug mode is on.")
    else:
        actions.set_quotas(user_obj)

    # Note which hosts the quotas will apply on
    syncing_hosts = statusdb_obj.known_syncing_hosts()

    # Add the record of the action to the database
    logdb_obj.add_action(new_status_group, user_obj.uid,
                         user_obj.history_iter(), int(time.time()))

    actions.user_warning_email(user_obj, new_status_group, syncing_hosts)
    service_logger.info("User %s was put in: %s", username, new_status_group)


def log_user_badness(user_obj, username):
    """
    Logs out information to the debug and service logs about the user's
    badness.

    user_obj: user.User()
        The user to update in statusdb.
    username: str
        The user's username.
    """
    logger.debug("%s has nonzero badness: %s", user_obj.uid_name,
                 user_obj.badness_obj)
    service_logger.info("User %s has nonzero badness: %s", username,
                        user_obj.badness_obj.score())

    whlist_cpu_usage, whlist_mem_usage = user_obj.last_proc_usage(whitelisted=True)
    logger.debug("Whitelisted Usage: cpu %s, mem %s", whlist_cpu_usage,
                 whlist_mem_usage)
    cpu_cgroup_usage, mem_cgroup_usage = user_obj.last_cgroup_usage()
    logger.debug("Real Usage: cpu %s, mem %s", cpu_cgroup_usage,
                 mem_cgroup_usage)


def reset_occurrences_timeout(user_obj, username, statusdb_obj):
    """
    Resets the occurrences timeout for the user.

    user_obj: user.User()
        The user to update in statusdb.
    username: str
        The user's username.
    statusdb_obj: statusdb.StatusDB
        A StatusDB object to use.
    """
    # The user's usage should stay below the threshold in order to
    # for the occurrences timeout to continue.
    user_obj.status.reset_occurrences_timeout()
    try_update_statusdb_for_user(user_obj, statusdb_obj)
    logger.info("Resetting the occurrences timeout of %s", user_obj.uid_name)
    service_logger.info("User %s penalty occurrences timeout has been reset "
                        "due to nonzero badness", username)


def lower_occurrences(user_obj, username, statusdb_obj):
    """
    Lowers the occurrences count for the user.

    user_obj: user.User()
        The user to update in statusdb.
    username: str
        The user's username.
    statusdb_obj: statusdb.StatusDB
        A StatusDB object to use.
    """
    user_obj.status.lower_occurrences()
    try_update_statusdb_for_user(user_obj, statusdb_obj)
    logger.info("Lowering the occurrences count of %s", user_obj.uid_name)
    service_logger.info("User %s penalty occurrences has lowered to: "
                        "%s", username, user_obj.status.occurrences)


def downgrade_penalty(user_obj, username, statusdb_obj):
    """
    Downgrades a penalty tier for the user.

    user_obj: user.User()
        The user to update in statusdb.
    username: str
        The user's username.
    statusdb_obj: statusdb.StatusDB
        A StatusDB object to use.
    """
    logger.info("Decreasing the penalty status of %s", user_obj.uid_name)
    # When downgrading penalty we make the status authoritative, but we
    # don't want to rely on this authoritativeness for whether to send a
    # email
    old_authority = user_obj.status.authority
    was_authoritative = user_obj.status.authoritative()
    new_status_group = user_obj.status.downgrade_penalty()

    # Ensure user has a fresh start; this _shouldn't_ be needed since we
    # drop badness to zero on a violation, but if for whatever reason that
    # fails (e.g. db write failure -> arbiter failure) then we don't want
    # to remember their old badness
    user_obj.badness_obj.reset()
    try_update_statusdb_for_user(user_obj, statusdb_obj, include_badness=True)

    if cfg.general.debug_mode:
        logger.debug("Not setting quotas for %s because debug mode is on.",
                     user_obj.uid_name)
    else:
        actions.set_quotas(user_obj)

    if was_authoritative:
        # The other will send emails
        actions.user_nice_email(user_obj.uid, new_status_group)
        service_logger.info("User %s is now in: %s", username,
                            new_status_group)
    else:
        logger.debug("Not sending emails because %s is not authoritative on %s (%s is)",
                     user_obj.uid_name, sysinfo.hostname, old_authority)


def try_update_statusdb_for_user(user_obj, statusdb_obj, include_badness=True):
    """
    Attempts to update the user's badness and status in statusdb. This should
    be used sparingly as bulk updates is more preferable.

    user_obj: user.User()
        The user to update in statusdb.
    include_badness: bool
        Whether to write out the badness as well as status.
    """
    try:
        statusdb_obj.set_status(user_obj.uid, user_obj.status)
        if include_badness:
            statusdb_obj.set_badness(user_obj.uid, user_obj.badness_obj)
    except statusdb.common_db_errors as err:
        logger.debug("Failed to update the user's new status/badness in "
                     "statusdb for %s: %s", user_obj.uid_name, err)
