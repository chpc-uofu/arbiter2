# SPDX-FileCopyrightText: Copyright (c) 2019-2020 Center for High Performance Computing <helpdesk@chpc.utah.edu>
#
# SPDX-License-Identifier: GPL-2.0-only

"""
A module with utilities for storing information related to a specific user.
"""

import collections
import copy
import itertools
import os

import badness
import cginfo
import pidinfo
import statuses
import sysinfo
import usage
from cfgparser import cfg, shared


def get_whitelist(status_group):
    """
    Returns the whitelist for the status group plus the global whitelist as a
    set.
    """
    status_group_prop = statuses.lookup_status_prop(status_group)
    whlist = set(cfg.processes.whitelist)
    whlist.update(status_group_prop.whitelist)

    if cfg.processes.whitelist_other_processes:
        whlist.add(shared.other_processes_label)

    whlist_files = [
        cfg.processes.whitelist_file, status_group_prop.whitelist_file
    ]
    for wfile in whlist_files:
        if wfile and os.path.isfile(wfile):
            with open(wfile, "r") as f:
                whlist.update(item.strip() for item in f.readlines())
    return whlist


proc_owner_whitelist = set(cfg.processes.proc_owner_whitelist)
whitelist = {}
for status_group in cfg.status.order + cfg.status.penalty.order:
    whitelist[status_group] = get_whitelist(status_group)


class User:
    """
    Contains information related to an user.

    Properties:
    uid: int
        The user's uid.
    gids: [int, ]
        A list of gids that the user belongs to.
    cgroup: cginfo.UserSlice()
        A cgroup obj representing the user.
    uid_name: str
        A uid, username pair (e.g. 1000 (username)).
    history: collections.deque(dict, )
        A list of history events ordered chronologically (i.e. most recent is
        first). History events are formatted as:
            {"time": float,
             "mem": float,
             "cpu": float,
             "pids": {int (pid): pidinfo.StaticProcess()}}
    badness_obj: badness.Badness()
        A badness object that scores usage and determines whether a violation
        has occurred.
    status: statuses.Status()
        The status of the user. See statuses.get_status() for format.
    cpu_quota: float
        The user's cpu quota (as a percent of a single core) based on their
        current status group.
    mem_quota: float
        The user's memory quota (as a percentage of the entire machine) based
        on their current status group.
    """
    __slots__ = ["uid", "gids", "cgroup", "username", "uid_name", "history",
                 "badness_obj", "status"]

    def __init__(self, uid):
        """
        Initializes a user.

        uid: int
            The user's uid
        """
        self.uid = uid
        self.gids = sysinfo.query_gids(self.uid)  # Assume this is fixed
        self.cgroup = cginfo.UserSlice(self.uid)
        self.username = "?"
        try:
            passwd = sysinfo.getpwuid_cached(uid)
            self.username = passwd.pw_name
        except KeyError:
            pass
        self.uid_name = "{} ({})".format(self.uid, self.username)
        # Placeholder until all statuses are bulk updated via main.py
        self.status = statuses.lookup_empty_status(self.uid)
        self.history = collections.deque(maxlen=cfg.badness.max_history_kept)
        self.badness_obj = badness.Badness()

    def history_iter(self, max_events=None):
        """
        Iterates over a copy of the user's history. If max_events is given, up
        to the given number of events is yielded.

        max_events: int
            The maximum number of events to iterate over.
        """
        if not max_events:
            num_events = len(self.history)
        else:
            num_events = min(max_events, len(self.history))

        for event in itertools.islice(self.history, num_events):
            yield copy.deepcopy(event)

    def add_usage(self, collect_timestamp, cgroup_usage, per_process_usage,
                  rhel7_compat=False):
        """
        Adds the given usage to the user's internal usage history.

        collect_timestamp: int
            An epoch timestamp for the time the usage was collected at.
        cgroup_usage: cginfo.StaticUserSlice
            A cginfo.StaticUserSlice object representing the user's cgroup
            usage.
        per_process_usage: dict
            A dictionary of per-process (per per) usage, with the keys being
            pid and the values being pidinfo.StaticProcess objects.
        rhel7_compat: bool
            Flag for whether to throw away the memory cgroup usage and replace
            it with the sum of process memory due to it being tainted with
            kernel memory.
        """
        # Irrational paranoia about dicts and objects being pointers...
        copied_per_process_usage = copy.deepcopy(per_process_usage)
        self.history.appendleft({
            "time": collect_timestamp,
            "cpu": cgroup_usage.usage["cpu"],
            "mem": cgroup_usage.usage["mem"],
            "pids": copied_per_process_usage
        })

        summed_proc = pidinfo.StaticProcess(-1)  # Basically zero usage
        if len(copied_per_process_usage) > 0:
            # StaticProcess objects can be arbitrarily added together; usage
            # is added, resulting combined metadata has no inutuitive meaning
            summed_proc = sum(self.history[0]["pids"].values())

        if rhel7_compat:
            self.history[0]["mem"] = summed_proc.usage["mem"]

        # Add a mark ('*') to the end of all whitelisted process names
        self.mark_whitelisted_processes(self.history[0]["pids"].values())

        # Add "other processes", our notion of what we don't know: the
        # difference between cgroup usage (accurate) and process usage (not
        # that); can be whitelisted in config so users only get called out
        # for usage we can identify the source of*
        #
        # *this is particularly relevent for whitelisting of compilers since
        # there are lots of short high usage processes which cannot be
        # identified easily
        self.history[0]["pids"][-1] = pidinfo.StaticProcess(
            -1,
            usage={
                "cpu": max(self.history[0]["cpu"] - summed_proc.usage["cpu"], 0),
                "mem": max(self.history[0]["mem"] - summed_proc.usage["mem"], 0)
            },
            name=shared.other_processes_label + "**",
            owner=self.uid
        )

    def update_badness_from_last_usage(self):
        """
        Creates a new badness score from the last usage added.
        """
        # Badness scores are zero when you are in penalty
        if self.status.in_penalty():
            return

        # Calculate the delta badness dictionary based on latest collector data
        cpu_usage, mem_usage = self.last_cgroup_usage()
        whlist_cpu_usage, _ = self.last_proc_usage(whitelisted=True)

        # Only subtract whlist_cpu_usage, since too much memory usage is still
        # bad, regardless of whether it's whitelisted (it cannot be throttled
        # once allocated).
        usage_dict = {"cpu": cpu_usage - whlist_cpu_usage, "mem": mem_usage}
        cpu_quota, mem_quota = self.status.quotas()
        quotas_dict = {"cpu": cpu_quota, "mem": mem_quota}
        self.badness_obj.update_with_usage(usage_dict, quotas_dict)

    def set_badness(self, badness_obj):
        """
        Overrides a user's badness object with the one.

        badness_obj: badness.Badness()
            A badness object.
        """
        self.badness_obj = badness_obj

    def whitelisted_processes(self, processes):
        """
        Filters the given processes and into list of whitelisted processes.

        processes: iter
            A iterable of processes.
        """
        return [
            proc for proc in processes
            if proc.name.rstrip("*") in whitelist[self.status.current] or
               proc.owner in proc_owner_whitelist
        ]

    def mark_whitelisted_processes(self, processes):
        """
        Marks the given StaticProcess()s with a asterisk at the end of their
        name if the process is whitelisted either by the global whitelist,
        status whitelist or pid owner whitelist.
        """
        whitelisted_procs = self.whitelisted_processes(processes)
        for proc in whitelisted_procs:
            proc.name += "*"

    def last_cgroup_usage(self):
        """
        Returns the current average cgroup usage between the arbiter
        intervals.
        """
        updates = cfg.general.history_per_refresh
        cpu_usages = [
            event["cpu"]
            for event in itertools.islice(self.history, 0, updates)
        ]
        mem_usages = [
            event["mem"]
            for event in itertools.islice(self.history, 0, updates)
        ]
        return (
            sum(cpu_usages) / len(cpu_usages) if cpu_usages else 0.0,
            sum(mem_usages) / len(mem_usages) if mem_usages else 0.0
        )

    def last_proc_usage(self, whitelisted=False):
        """
        Returns the current average total process usage between the arbiter
        intervals.

        whitelisted: bool
            Whether to only count whitelisted processes.
        """
        updates = cfg.general.history_per_refresh
        total_procs = []
        for event in itertools.islice(self.history, 0, updates):
            if whitelisted:
                procs = self.whitelisted_processes(event["pids"].values())
            else:
                procs = list(event["pids"].values())
            total_procs.append(sum(procs))

        if not total_procs:
            return 0.0, 0.0

        avg_proc = usage.average(*total_procs)
        return avg_proc.usage["cpu"], avg_proc.usage["mem"]

    @property
    def cpu_usage(self):
        """
        Returns the cgroup CPU usage over the last arbiter refresh interval.

        Kept solely for backwards compatablity for integrations.py; deprecated.
        """
        return self.last_cgroup_usage()[0]

    @property
    def mem_usage(self):
        """
        Returns the cgroup memory usage over the last arbiter refresh interval.

        Kept solely for backwards compatablity for integrations.py; deprecated.
        """
        return self.last_cgroup_usage()[1]

    @property
    def cpu_quota(self):
        """
        Returns the user's CPU quota as a percent of a core or CPU, depending
        on the div_cpu_quotas_by_threads_per_core setting.

        Kept solely for backwards compatablity for integrations.py; deprecated.
        """
        return self.status.quotas()[0]

    @property
    def mem_quota(self):
        """
        Returns the user's memory quota as a percent of the machine.

        Kept solely for backwards compatablity for integrations.py; deprecated.
        """
        return self.status.quotas()[1]

    def new(self):
        """
        Returns whether the user is new (as in the obj created).
        """
        return len(self.history) <= cfg.general.history_per_refresh

    def needs_tracking(self):
        """
        Returns whether the user should continued to be tracked.
        """
        has_occurrences = self.status.occurrences > 0
        return (
            self.cgroup.active()
            or self.badness_obj.is_bad()
            or self.status.in_penalty()
            or has_occurrences
        )
