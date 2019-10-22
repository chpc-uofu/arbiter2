# SPDX-License-Identifier: GPL-2.0-only
"""
A module with utilities for storing information related to a specific user.
"""

import time
import collections
import logging
import itertools
import os
import pwd
import usage
import cinfo
import statuses
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
    cgroup: cinfo.UserSlice()
        A cgroup obj representing the user.
    history: collections.deque(dict, )
        A list of history events ordered chronologically (i.e. most recent is
        first). History events are formatted as:
            {"time": float,
             "mem": float,
             "cpu": float,
             "pids": {int (pid): pidinfo.StaticProcess()}}
    badness_history: collections.deque(dict, )
        A list of badness history events ordered chronologically (i.e. most
        recent is first). Badness events are formatted as:
            {"timestamp": int,
             "delta_badness": {"mem": float, "cpu": float},
             "badness": {"mem": float, "cpu": float}}
    badness_timestamp: int
        A epoch timestamp for when the user's badness score goes above zero.
        This is reset after the user's badness score goes to 0.
    status: statuses.Status()
        The status of the user. See statuses.get_status() for format.
    cpu_usage: float
        The average cpu usage of the user over the arbiter interval (as a
        percent of a single core), averaged from collector events during the
        arbiter interval.
    mem_usage: float
        The average mem usage of the user over the arbiter interval (as a
        percentage of the entire machine), averaged from collector events
        during the arbiter interval.
    cpu_quota: float
        The user's cpu quota (as a percent of a single core) based on their
        current status.
    mem_quota: float
        The user's memory quota (as a percentage of the entire machine) based
        on their current status.
    """
    __slots__ = ["uid", "gids", "cgroup", "username", "uid_name", "history",
                 "badness_history", "badness_timestamp", "status",
                 "cpu_usage", "mem_usage", "cpu_quota", "mem_quota"]

    def __init__(self, uid):
        """
        Initializes a user.

        uid: int
            The user's uid
        """
        self.uid = uid
        self.gids = statuses.query_gids(self.uid)
        self.cgroup = cinfo.UserSlice(self.uid)
        self.username = "?"
        try:
            self.username = pwd.getpwuid(uid).pw_name
        except KeyError:
            pass
        self.uid_name = "{} ({})".format(self.uid, self.username)
        self.status = statuses.get_status(uid)
        self.history = collections.deque(maxlen=cfg.badness.max_history_kept)
        self.badness_history = collections.deque(maxlen=cfg.badness.max_history_kept)
        self.badness_timestamp = 0  # epoch when badness started increasing
        self.set_badness({"cpu": 0.0, "mem": 0.0}, int(time.time()))

    def set_badness(self, badness, record_time):
        """
        Sets and clears the badness history with the given badness.

        badness: dict
            The new first badness.
        record_time: float, int
            Time in epoch that the badness was calculated.
        """
        self.badness_history.clear()
        self.badness_history.appendleft({
            "timestamp": record_time,
            "delta_badness": {"cpu": 0.0, "mem": 0.0},
            "badness": badness
        })
        if sum(badness.values()) == 0:
            self.badness_timestamp = record_time

    def add_badness(self, badness, delta_badness, record_time):
        """
        Imports new badness and prepends the new badness into the
        self.badness_history accordingly.

        badness: dict
            A new badness dictionary.
        delta_badness: dict
            The change in badness dictionary.
        record_time: float, int
            Time in epoch that the badness was calculated.
        """
        self.badness_history.appendleft({
            "timestamp": record_time,
            "delta_badness": delta_badness,
            "badness": badness
        })

        # Set badness_timestamp when user starts gaining badness or loses it
        if not self.badness_timestamp and sum(badness.values()) != 0:
            self.badness_timestamp = record_time
        elif self.badness_timestamp and sum(badness.values()) == 0:
            self.badness_timestamp = 0

    def update_properties(self):
        """
        Sets properties of the user.
        """
        self.status = statuses.get_status(self.uid)
        avg_cpu, avg_mem = self.avg_gen_usage()
        self.cpu_usage = avg_cpu
        self.mem_usage = avg_mem
        cpu_quota, mem_quota = statuses.lookup_quotas(self.uid, self.status.current)
        self.cpu_quota = cpu_quota
        self.mem_quota = mem_quota
        self.gids = statuses.query_gids(self.uid)

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

    def avg_gen_usage(self):
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

    def avg_proc_usage(self, whitelisted=False):
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

    def new(self):
        """
        Returns whether the user is new (as in the obj created).
        """
        return len(self.badness_history) <= 1
