"""
A module with utilities for storing information related to a specific user.
"""

import time
import collections
import logging
import itertools
from cfgparser import cfg
import cinfo
import statuses

logger = logging.getLogger("arbiter." + __name__)


class User(object):
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
    __slots__ = ["uid", "gids", "cgroup", "history", "badness_history",
                 "badness_timestamp", "status", "cpu_usage", "mem_usage",
                 "cpu_quota", "mem_quota"]

    def __init__(self, uid):
        """
        Initializes a user.

        uid: int
            The user's uid
        """
        self.uid = uid
        self.gids = statuses.query_gids(self.uid)
        self.cgroup = cinfo.UserSlice(
            self.uid,
            memsw=cfg.processes.memsw
        )
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
        # Average cpu and mem usage over the arbiter interval
        avg_cpu, avg_mem = self.avg_usage()
        self.cpu_usage = avg_cpu
        self.mem_usage = avg_mem
        cpu_quota, mem_quota = statuses.lookup_quotas(self.uid, self.status.current)
        self.cpu_quota = cpu_quota
        self.mem_quota = mem_quota
        self.gids = statuses.query_gids(self.uid)

    def whitelisted_processes(self, processes):
        """
        Returns a list of StaticProcess()s that are whitelisted either by the
        global whitelist, status whitelist or pid owner whitelist.
        """
        return [
            proc for proc in processes
            if proc.is_whitelisted(self.status.current)
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

    def avg_usage(self):
        """
        Returns the current usage bewteen the arbiter intervals using usage
        history.
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

    def new(self):
        """
        Returns whether the user is new (as in the obj created).
        """
        return len(self.badness_history) <= 1
