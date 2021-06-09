# SPDX-FileCopyrightText: Copyright (c) 2019-2020 Center for High Performance Computing <helpdesk@chpc.utah.edu>
#
# SPDX-License-Identifier: GPL-2.0-only

"""
Classes and methods related to monitoring the overall usage on the machine.
"""

import collections
import itertools
import logging
import multiprocessing
import time

import actions
from cfgparser import cfg
import sysinfo
import timers
import usage

logger = logging.getLogger("arbiter." + __name__)


class HighUsageWatcher:
    """
    Watches for high usage on the machine based on high-watermark thresholds
    and when this occurs, logs per-user usage as well as sending an email to
    the admins.
    """

    def __init__(self, logdb_obj):
        """
        Initializes the watcher with the given logdb instance.

        logdb_obj: logdb.LogDB
            A LogDB object to use.
        """
        self.logdb_obj = logdb_obj
        # Amount of historical events to consider high usage for
        self.threshold_period = cfg.high_usage_watcher.threshold_period
        # The amount of time before a high usage warning to log out about user
        # usage; add a sensible margin for recording historical data
        self.warning_context = 8 + self.threshold_period
        self.history = collections.deque(maxlen=self.threshold_period)
        for _ in range(self.threshold_period):
            # Base case so high usage on startup gets called out in theshold
            # period, rather than immediately due to a lack of data
            self.history.appendleft(usage.metrics.copy())

        cpu_count = multiprocessing.cpu_count()
        if cfg.high_usage_watcher.div_cpu_thresholds_by_threads_per_core:
            cpu_count /= sysinfo.threads_per_core
        self.cpu_threshold = cfg.high_usage_watcher.cpu_usage_threshold * cpu_count
        self.mem_threshold = cfg.high_usage_watcher.mem_usage_threshold

        self.user_count = cfg.high_usage_watcher.user_count
        self.timer = timers.TimeRecorder()
        self.timeout = cfg.high_usage_watcher.timeout

    def add_usage(self, allusers):
        """
        Given a cginfo.StaticAllUsersSlice instance, adds it to the internal
        history.

        allusers: cginfo.StaticAllUsersSlice
            The recent usage of the user.slice/ cgroup
        """
        self.history.appendleft(allusers.usage)

    def send_email_if_high_usage(self, user_dict):
        """
        Watches for high usage and returns whether appropriate action needs to
        be taken if high usage has been detected.

        user_dict: dict
            A dictionary of user.User objects, identified by their uid.
        """
        if self.timer.delta() > 0:
            return

        is_high_usage = all(
            event["cpu"] > self.cpu_threshold * 100
            or event["mem"] > self.mem_threshold * 100
            for event in self.history
        )
        if not is_high_usage:
            return

        high_usage_users = self.get_high_usage_users(user_dict)
        self.send_high_usage_email(high_usage_users)
        self.timer.start_now(self.timeout)

    def get_high_usage_users(self, user_dict):
        """
        Returns a list of high usage users.

        user_dict: {}
            A dictionary of user.User() objects, identified by their uid.
        """
        # We're going to judge everyone by their usage relative to the
        # entirety of the machine, instead of status quotas, since we only
        # care about usage, not a persons status.
        return usage.rel_sorted(
            user_dict.values(),
            multiprocessing.cpu_count() * 100, 100,
            key=lambda user_obj: user_obj.last_cgroup_usage(),
            reverse=True
        )[:self.user_count]

    def send_high_usage_email(self, users):
        """
        Sends a email about high usage on the machine.

        users: {}
            A dictionary of user.User objects, identified by their uid.
        """
        high_usage = self.history[0]
        logger.info("Sending an overall high usage email")
        actions.send_high_usage_email(users, high_usage["cpu"], high_usage["mem"])

        logger.debug("Usage that caused warning: %s", high_usage)

        uid_name_set = {user_obj.uid_name for user_obj in users}
        logger.debug("Logging high usage users: %s", uid_name_set)

        # FIXME: Add a high_usage_warning with the user.slice usage, instead
        #        of just user-$UID.slice usage
        timestamp = int(time.time())
        for user_obj in users:
            self.logdb_obj.add_action(
                "high_usage_warning",
                user_obj.uid,
                user_obj.history_iter(self.warning_context),
                timestamp
            )
