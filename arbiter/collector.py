# SPDX-FileCopyrightText: Copyright (c) 2019-2020 Center for High Performance Computing <helpdesk@chpc.utah.edu>
#
# SPDX-License-Identifier: GPL-2.0-only

"""
A module containing objects/methods for collecting information.
"""

import collections
import logging
import time

import cginfo
import pidinfo
import sysinfo
import timers
import usage
import user
from cfgparser import cfg

logger = logging.getLogger("arbiter." + __name__)


class Collector(object):
    """
    A object that collects information about cgroups and processes and writes
    that information to a user.User() object.
    """

    def __init__(self, repetitions, interval, poll=3, rhel7_compat=False):
        """
        Initializes the collector. Run the collector by doing self.run().

        repetitions: int
            How many times to collect information over the interval.
        interval: int
            The interval to collect between.
        poll: int
            The number of polls to average over the interval when measuring
            processes. Must be greater than 2.
        rhel7_compat: bool
            Whether or not to use rhel7 configuration for backwards
            compatability.
        """
        self.repetitions = repetitions
        self.poll = poll if poll >= 2 else 2
        self.interval = interval
        self.users = {}
        self.allusers = cginfo.StaticAllUsersSlice()
        self.allusers_hist = []
        self.rhel7_compat = rhel7_compat
        # Keep track of seen no passwd users so we don't spam the debug log
        self.no_passwd_uids = set()

    def delete_user(self, uid):
        """
        Deletes the given user such that information is not collected on them
        until they are present on the system again.

        uid: int
            A uid of a user to remove.
        """
        del self.users[uid]

    def refresh_users(self):
        """
        Refreshes the internal users dictionary by adding new users that
        haven't been seen before.
        """
        active_uids = cginfo.current_cgroup_uids(min_uid=cfg.general.min_uid)

        # Sometimes there are users with sessions on a machine that aren't in
        # ldap because they've been removed up there at some point (have no
        # passwd entry). This causes problems down the line when arbiter tries
        # to query info about them (e.g. email addr,  username, groupnames,
        # realname). We'll warn about them once and ignore them.
        found_no_passwd_uids = set(
            uid for uid in active_uids if not sysinfo.passwd_entry(uid)
        )
        for uid in self.no_passwd_uids.symmetric_difference(found_no_passwd_uids):
            logger.warning("Found a user without a passwd entry, ignoring: %s", uid)
        self.no_passwd_uids.update(found_no_passwd_uids)

        self.users.update(
            {uid: user.User(uid)
             for uid in active_uids
             if uid not in self.users and uid not in found_no_passwd_uids}
        )

    def _pre_run(self):
        """
        Initializes allusers_hist to be empty.
        """
        self.allusers_hist = []

    def _post_run(self):
        """
        Computes the total usage from the sum of user cpu and memory.
        """
        if self.allusers_hist:
            self.allusers = usage.average(*self.allusers_hist)
            if self.rhel7_compat:
                self.allusers.usage["mem"] = sum(
                    user_obj.last_proc_usage()[1]  # cpu, mem
                    for user_obj in self.users.values()
                )
        else:
            self.allusers = cginfo.StaticAllUsersSlice()

    def collect(self):
        """
        Collects information into each user's history.
        """
        waittime = self.interval / self.poll
        timer = timers.TimeRecorder()
        collect_timestamp = int(time.time())

        allusers_instant_histories = []
        gen_instant_histories = collections.defaultdict(list)
        processes_instant_histories = collections.defaultdict(
            lambda: collections.defaultdict(list)
        )
        # Collect usage in a poll
        for _ in range(0, self.poll):
            timer.start_now(waittime)

            # Collect Overall General Metrics: CPU, Memory (AllUsersSliceInstance())
            try:
                allusers_instant_histories.append(cginfo.AllUsersSliceInstance())
            except OSError as err:
                logger.debug(err)
                continue

            for uid, user_obj in self.users.items():
                # Collect General Metrics: CPU, Memory (UserSliceInstance())
                pids = []
                try:
                    user_slice = cginfo.UserSliceInstance(
                        uid,
                        memsw=cfg.processes.memsw
                    )
                    pids = user_slice.pids()
                    gen_instant_histories[user_obj].append(user_slice)
                except OSError:
                    continue

                # Collect Process Metrics: CPU, Memory (ProcessInstance())

                # Reading smaps is insanely slow (when done for 2,000 procs,
                # 180GiB of usage, takes ~30s!), so we'll only selectively do
                # this for certain processes with a large enough shared memory
                # size for overcounting of RSS to make a difference.
                pss_thresh = 0
                if cfg.processes.pss:
                    pss_thresh = cfg.processes.pss_threshold

                # Normally clockticks is called within ProcessInstance(...),
                # but with thousands of processes, this adds up significantly.
                # We lose some accuracy here, particuarly because parsing
                # smaps can cause seconds of latency with large shared memory
                # usage patterns without smaps_rollup, but this should be
                # isolated to high shmem users and cgroup accuracy should save
                # us here
                clockticks = sysinfo.clockticks()
                for pid in pids:
                    try:
                        processes_instant_histories[user_obj][pid].append(
                            pidinfo.ProcessInstance(pid, pss=cfg.processes.pss,
                                                    swap=cfg.processes.memsw,
                                                    clockticks=clockticks,
                                                    selective_pss_threshold=pss_thresh)
                        )
                    except OSError as err:
                        if err.errno == 13:
                            # Likely don't have permission to read from
                            # /proc/<pid>/smaps
                            logger.warning(err)

            delta = timer.delta()
            if delta <= 0:
                logger.debug("Timing of collection poll is behind by %.5f seconds", -delta)
            time.sleep(max(0, delta))

        # Average the usage over all the polls
        div_by = self.poll - 1
        # Average AllUsersSliceInstance() into StaticAllUsersSlice()
        if allusers_instant_histories:
            self.allusers_hist.append(
                usage.average(
                    *usage.combine(*allusers_instant_histories),
                    by=div_by
                )
            )

        # Finally update the user objects with calculated usage
        for user_obj in self.users.values():
            # -1 since we have one less static obj than instant obj after combo
            # Average UserSliceInstance() into StaticUserSlice()
            static_user_slice = cginfo.StaticUserSlice(user_obj.uid)
            if len(gen_instant_histories[user_obj]) >= 2:
                # Cannot avg a single instantaneous user slice
                static_user_slice = usage.average(
                    *usage.combine(*gen_instant_histories[user_obj]),
                    by=div_by
                )

            # Average ProcessInstance() into StaticProcess()
            per_process_usage = {
                pid: usage.average(
                    *usage.combine(*processes),
                    by=div_by
                )
                for pid, processes in processes_instant_histories[user_obj].items()
                if len(processes) >= 2   # Cannot avg a single instant process
            }
            user_obj.add_usage(
                collect_timestamp,
                static_user_slice,
                per_process_usage,
                rhel7_compat=self.rhel7_compat
            )

    def run(self):
        """
        Runs the collector self.repetitions times, with each collection over
        the self.interval. Returns a StaticUserSlice that is the sum of all
        the users and a dictionary of users identified by their uid.
        """
        self.refresh_users()
        self._pre_run()

        for _ in range(self.repetitions):
            self.collect()

        self._post_run()
        return self.allusers, self.users
