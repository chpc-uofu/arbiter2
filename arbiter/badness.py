# SPDX-FileCopyrightText: Copyright (c) 2019-2020 Center for High Performance Computing <helpdesk@chpc.utah.edu>
#
# SPDX-License-Identifier: GPL-2.0-only

"""
Methods and classes related to the notion of a badness score.

A badness is simply a rolling score of each resources tracked. A badness score
is the sum of all the badness.
"""

import collections
import datetime
import time
import types

from cfgparser import cfg


class Badness(types.SimpleNamespace):
    """
    A class for storing badness at a particular time. Badness is a numeric
    number between 0 and 100 that represents how close resource usage is to a
    violation based on a threshold-time calculation. 0 means the usage hasn't
    crossed the threshold ever (or since the previous badness score was
    reduced to 0) and 100 means that the usage represents a violation of the
    threshold-time policy.

    There are multiple badness numbers corresponding to particular resources
    tracked. A badness score (via the score() function) is simply a sum of the
    badness over all the resources.

    Note that because this is a threshold-time value, the current badness
    recorded is a summation of all the previous badness scores. To derive new
    badness numbers, the update_with_usage() function should be used.
    """

    def __init__(self, cpu=0.0, mem=0.0, timestamp=None):
        """
        Initializes a badness score.
        """
        self.reset(cpu, mem, timestamp)

    def reset(self, cpu=0.0, mem=0.0, timestamp=None):
        """
        Reinitializes the badness score.
        """
        self.cpu = cpu
        self.mem = mem
        self.update_ts = int(timestamp) if timestamp else int(time.time())
        self.start_of_bad_ts = 0 if self.is_good() else self.update_ts

    def last_updated(self):
        """
        Returns when the badness was last updated.
        """
        return self.update_ts

    def start_of_badness(self):
        """
        Returns a timestamp for when the user started being bad. Returns 0 if
        the is not bad.
        """
        return self.start_of_bad_ts

    def expired(self, timeout=None):
        """
        Returns whether the badness score is irrelevent now.

        timeout: int
            An optional timeout to use. Defaults to the imported badness
            timeout from the configuration.
        """
        if not timeout:
            timeout = cfg.badness.imported_badness_timeout
        return self.last_updated() + timeout < time.time()

    def is_good(self):
        """
        Returns whether the badness is empty (all zeros).
        """
        return self.cpu == 0 and self.mem == 0

    def is_bad(self):
        """
        Returns whether the badness score is nonzero.
        """
        return not self.is_good()

    def score(self):
        """
        Returns the total badness score.
        """
        return self.cpu + self.mem

    def is_violation(self):
        """
        Returns whether the badness represents the max badness.
        """
        return self.score() >= 100.0

    def update_with_usage(self, usage, quotas):
        """
        Updates a new badness score based on a delta badnes score derived from
        the given usage and quota.

        usage: dict
            A dictionary of usage with "cpu" and "mem" keys.
        quotas: dict
            A dictionary of quotas with "cpu" and "mem" keys.
        """
        self.update_ts = int(time.time())
        was_bad = self.is_bad()

        delta = calc_delta_badness(usage, quotas)
        self.cpu = min(100.0, max(0.0, self.cpu + delta["cpu"]))
        self.mem = min(100.0, max(0.0, self.mem + delta["mem"]))

        if was_bad and self.is_good():
            self.start_of_bad_ts = 0
        elif not was_bad and self.is_bad():
            self.start_of_bad_ts = self.update_ts

    def __repr__(self):
        return "Badness(cpu={}, mem={}, updated={}, started_bad={})".format(
            self.cpu,
            self.mem,
            self.update_ts,
            self.start_of_bad_ts,
        )

    def __str__(self):
        if self.is_good():
            return "cpu=0, mem=0"

        epoch_ts_dt = datetime.datetime.fromtimestamp(self.start_of_bad_ts)
        iso_ts = datetime.datetime.isoformat(epoch_ts_dt)
        return "cpu={}, mem={} since {}".format(
            self.cpu,
            self.mem,
            iso_ts
        )


def calc_delta_badness(usage, quotas):
    """
    Computes a delta badness score. Returns a cpu and mem delta badness.

    usage: dict
        A dictionary of usage with "cpu" and "mem" keys.
    quotas: dict
        A dictionary of quotas with "cpu" and "mem" keys.
    """
    refresh = cfg.general.arbiter_refresh
    time_to_max_bad = cfg.badness.time_to_max_bad
    time_to_min_bad = cfg.badness.time_to_min_bad

    Metric = collections.namedtuple("Metric", "quota usage threshold")
    metrics = {
        "cpu": Metric(quotas["cpu"], usage["cpu"], cfg.badness.cpu_badness_threshold),
        "mem": Metric(quotas["mem"], usage["mem"], cfg.badness.mem_badness_threshold),
    }

    delta = {}
    for name, metric in metrics.items():
        # Calculate the increase/decrease in badness (to translate the time
        # and extreme scores to a change per interval)
        max_incr_per_sec = 100.0 / (time_to_max_bad * metric.threshold)
        max_incr_per_interval = max_incr_per_sec * refresh
        max_decr_per_sec = 100.0 / time_to_min_bad
        max_decr_per_interval = max_decr_per_sec * refresh

        usage = metric.usage
        # Make badness scores consistent between debug and non-debug mode
        # (where usage cannot exceed the quota) or optionally cap the
        # badness increase by capping the usage to shield against
        # erroneous data
        if cfg.general.debug_mode or cfg.badness.cap_badness_incr:
            usage = min(metric.usage, metric.quota)

        rel_usage = usage / metric.quota
        if rel_usage >= metric.threshold:
            change = rel_usage * max_incr_per_interval
        else:
            change = (1 - rel_usage) * -max_decr_per_interval
        delta[name] = change

    return delta
