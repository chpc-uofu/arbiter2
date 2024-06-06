#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# A program that reads Arbiter2's status databases and sends the associated
# information, such as statuses and badness scores, to Prometheus when pulled.
# Also exports per-user cgroup metrics. See ArbiterCollector description for
# specifics.
#
# Written by Dylan Gardner
# Usage: ./arbiter_exporter.py
import argparse
import collections
import logging
import os
import pwd
import shlex
import socket
import sys
import time
import toml
import prometheus_client.core

logger = logging.getLogger('arbiter_exporter')


class ArbiterCollector:
    """
    Prometheus-style Collector object that returns user status, badness,
    and configured status group values from Arbiter2 as well as cgroup
    metrics.

    For querying simplicity, this class goes to great lengths to ensure that
    regardless of user race conditions, if a user appears in one particular
    metric, it will also occur in the other produced metrics. However, it is
    not the case that all metrics for a user can be associated with a
    particular precise moment in time, despite the appearance of so. This is
    inherit in the timing of collecting all these metrics.

    The atomicity property enables the easy use of division operators without
    needing to coalesce null values which is not easy/possible to do in
    Prometheus.
    """

    def __init__(self, statusdb_obj, interval=5, min_uid=0, swap=False):
        """
        Initializes the collector instance.
        """
        self.namespace = "arbiter"
        self.statusdb_obj = statusdb_obj
        # Up until Python 3.6, dictionaries were not ordered
        self.default_labels = collections.OrderedDict({"hostname": socket.gethostname()})

        # FIXME: Maybe make interval and poll configurable?
        self.interval = interval
        self.poll = 2
        self.min_uid = min_uid
        self.swap = swap

    def collect(self):
        """
        Returns metrics to Prometheus when called.
        """
        logger.info("Collecting all the metrics for a Prometheus client")
        user_metrics = self.define_user_metrics()
        sys_metrics = self.define_sys_metrics()
        config_metrics = self.define_config_metrics()

        logger.debug("Collecting system metrics for a Prometheus client")
        self.add_sys_metrics(sys_metrics)

        active_uids = cginfo.current_cgroup_uids()
        uid_username_mapping = self.uids_to_usernames(active_uids)
        username_uids = set(uid_username_mapping.keys())

        logger.debug("Collecting cgroup metrics for a Prometheus client over a "
                     "%ss interval, poll %s", self.interval, self.poll)
        # Collect cgroup metrics first; for atomicity constraint in class desc
        # this is where users are going to disappear on us, collect that first
        # so we don't have to track which user data to throw away
        still_active_uids = self.add_user_slice_metrics(
            username_uids,
            uid_username_mapping,
            user_metrics
        )

        logger.debug("Collecting Arbiter2 status and badness metrics for a Prometheus client")
        self.add_user_status_metrics(still_active_uids, uid_username_mapping, user_metrics)

        logger.debug("Collecting Arbiter2 configuration metrics for a Prometheus client")
        self.add_config_metrics(config_metrics)

        yield from sys_metrics.values()
        yield from user_metrics.values()
        yield from config_metrics.values()

    def uids_to_usernames(self, uids):
        """
        Given a set of usernames, returns a dictionary of uid to username
        mappings.

        uid: {int,}
            The set of uids to convert.
        """
        # Sometimes there are users on the machine with no passwd entry
        # (they've been removed in LDAP or the equivalent) and thus no
        # username. We have usernames as label, so we can't return metrics for
        # them. Furthermore, Arbiter2 is supposed to ignore those users as
        # well so Arbiter2 probably won't have notable data on them if any.
        mapping = {}
        for uid in uids:
            if uid < self.min_uid:
                continue

            try:
                passwd = pwd.getpwuid(uid)
            except KeyError:
                # No passwd entry
                continue

            mapping[uid] = passwd.pw_name
        return mapping

    def define_user_metrics(self):
        """
        Returns a dictionary of Prometheus metric objects collected on a
        per-user basis.

        Note: metrics are returned, rather than set internally to facilliate
              multiple Prometheus clients pulling at the same time.
        """
        metrics = {}
        metrics["user_status_group"] = prometheus_client.core.GaugeMetricFamily(
            self.namespace + "_user_status_group",
            "The current status group index of a user.",
            labels=tuple(self.default_labels.keys()) + ("username", "uid", "current", "default")
        )
        metrics["user_status_group_updated"] = prometheus_client.core.GaugeMetricFamily(
            self.namespace + "_user_status_group_updated",
            "When the status group of the user has last changed as an epoch timestamp.",
            labels=tuple(self.default_labels.keys()) + ("username", "uid", "current", "default")
        )
        metrics["user_penalty_occurrences"] = prometheus_client.core.GaugeMetricFamily(
            self.namespace + "_user_penalty_occurrences",
            "Penalty index of a user if they were or are to be in penalty. "
            "i.e. penalty remembrance",
            labels=tuple(self.default_labels.keys()) + ("username", "uid", "penalty")
        )
        metrics["user_penalty_occurrences_updated"] = prometheus_client.core.GaugeMetricFamily(
            self.namespace + "_user_penalty_occurrences_updated",
            "When the penalty occurrences of the user has last changed as an epoch timestamp.",
            labels=tuple(self.default_labels.keys()) + ("username", "uid", "penalty")
        )
        metrics["user_penalty"] = prometheus_client.core.GaugeMetricFamily(
            self.namespace + "_user_penalty",
            "Penalty index of a user in penalty.",
            labels=tuple(self.default_labels.keys()) + ("username", "uid", "penalty", "default")
        )
        metrics["user_authoritative"] = prometheus_client.core.GaugeMetricFamily(
            self.namespace + "_user_authoritative",
            "1 if this host has the authority to communicate with the user (e.g. emails), else 0.",
            labels=tuple(self.default_labels.keys()) + ("username", "uid", "authority")
        )

        metrics["user_cpu_badness"] = prometheus_client.core.GaugeMetricFamily(
            self.namespace + "_user_cpu_badness",
            "The CPU badness score of a user. The total badness score is the "
            "sum of both CPU and memory badness.",
            labels=tuple(self.default_labels.keys()) + ("username", "uid", "current", "default")
        )
        metrics["user_mem_badness"] = prometheus_client.core.GaugeMetricFamily(
            self.namespace + "_user_mem_badness",
            "The memory badness score of a user. The total badness score is "
            "the sum of both CPU and memory badness.",
            labels=tuple(self.default_labels.keys()) + ("username", "uid", "current", "default")
        )

        metrics["user_cpu_usage"] = prometheus_client.core.GaugeMetricFamily(
            self.namespace + "_user_cpu_usage",
            "The per-core usage of the user collected from "
            "their user-$UID.slice cgroup over a {}s interval.".format(self.interval),
            labels=tuple(self.default_labels.keys()) + ("username", "uid", "current", "default")
        )
        metrics["user_mem_usage"] = prometheus_client.core.GaugeMetricFamily(
            self.namespace + "_user_mem_usage",
            "The average memory usage in bytes of the user collected from "
            "their user-$UID.slice cgroup over a {}s interval.".format(self.interval),
            labels=tuple(self.default_labels.keys()) + ("username", "uid", "current", "default")
        )

        metrics["user_cpu_limit"] = prometheus_client.core.GaugeMetricFamily(
            self.namespace + "_user_cpu_limit",
            "The per-core limit of the user collected from their "
            "user-$UID.slice cgroup.",
            labels=tuple(self.default_labels.keys()) + ("username", "uid", "current", "default")
        )
        metrics["user_mem_limit"] = prometheus_client.core.GaugeMetricFamily(
            self.namespace + "_user_mem_limit",
            "The memory limit in bytes of the user collected from "
            "their user-$UID.slice cgroup.",
            labels=tuple(self.default_labels.keys()) + ("username", "uid", "current", "default")
        )
        return metrics

    def define_config_metrics(self):
        """
        Defines the Prometheus metrics objects collected about the given
        Arbiter2 configuration. Returns a dictionary of metrics.
        """
        metrics = {}
        metrics["config_arbiter_refresh"] = prometheus_client.core.GaugeMetricFamily(
            self.namespace + "_config_arbiter_refresh",
            "How often Arbiter2 evaluates users for violations in seconds and "
            "applies the quotas of new users.",
            labels=tuple(self.default_labels.keys())
        )
        metrics["config_cpu_badness_threshold"] = prometheus_client.core.GaugeMetricFamily(
            self.namespace + "_config_cpu_badness_threshold",
            "The percentage (expressed as a fraction of 1) of a user's "
            "current status group's CPU quota that a user's usage must stay "
            "below in order to not be \"bad\".",
            labels=tuple(self.default_labels.keys())
        )
        metrics["config_mem_badness_threshold"] = prometheus_client.core.GaugeMetricFamily(
            self.namespace + "_config_mem_badness_threshold",
            "The percentage (expressed as a fraction of 1) of a user's "
            "current status group's memory quota that a user's usage must "
            "stay below in order to not be \"bad\".",
            labels=tuple(self.default_labels.keys())
        )
        metrics["config_time_to_max_bad"] = prometheus_client.core.GaugeMetricFamily(
            self.namespace + "_config_time_to_max_bad",
            "If the user's usage is at their status group's quota times the "
            "corresponding badness threshold fraction, how long will it take "
            "in seconds for their badness scores to reach 100 (the maximum "
            "badness score).",
            labels=tuple(self.default_labels.keys())
        )
        metrics["config_time_to_min_bad"] = prometheus_client.core.GaugeMetricFamily(
            self.namespace + "_config_time_to_min_bad",
            "If the user is at 100 badness, how long will it take in seconds "
            "to get to 0 badness given that their usage is at 0.",
            labels=tuple(self.default_labels.keys())
        )
        metrics["config_status_group_cpu_quota"] = prometheus_client.core.GaugeMetricFamily(
            self.namespace + "_config_status_group_cpu_quota",
            "The CPU quota of the status group as a aggregate of a single CPU core.",
            labels=tuple(self.default_labels.keys()) + ("current", "default")
        )
        metrics["config_status_group_mem_quota"] = prometheus_client.core.GaugeMetricFamily(
            self.namespace + "_config_status_group_mem_quota",
            "The memory quota in bytes of the status group.",
            labels=tuple(self.default_labels.keys()) + ("current", "default")
        )
        metrics["config_penalty_cpu_quota"] = prometheus_client.core.GaugeMetricFamily(
            self.namespace + "_config_penalty_cpu_quota",
            "The CPU quota of the penalty as a aggregate of a single CPU core.",
            labels=tuple(self.default_labels.keys()) + ("current",)
        )
        metrics["config_penalty_mem_quota"] = prometheus_client.core.GaugeMetricFamily(
            self.namespace + "_config_penalty_mem_quota",
            "The memory quota in bytes of the penalty if "
            "penalty_relative_quotas is 0. Otherwise it is a fraction of 1 "
            "that can be multiplied by a user's default status group's mem "
            "quota to calculate the real memory quota.",
            labels=tuple(self.default_labels.keys()) + ("current",)
        )
        metrics["config_penalty_timeout"] = prometheus_client.core.GaugeMetricFamily(
            self.namespace + "_config_penalty_timeout",
            "Time in seconds before the user is released into their default "
            "status group.",
            labels=tuple(self.default_labels.keys()) + ("current",)
        )
        metrics["config_penalty_relative_quotas"] = prometheus_client.core.GaugeMetricFamily(
            self.namespace + "_config_penalty_relative_quotas",
            "Whether or not a user's penalty quota is ratio of their default "
            "status. If 1, then the quotas will be expressed as a fraction "
            "of 1.",
            labels=tuple(self.default_labels.keys()) + ("current",)
        )
        metrics["config_penalty_occur_timeout"] = prometheus_client.core.GaugeMetricFamily(
            self.namespace + "_config_penalty_occur_timeout",
            "The amount of time in seconds for which a user keeps their "
            "current \"occurrence\" count (after that period it is lowered "
            "by 1)",
            labels=tuple(self.default_labels.keys())
        )
        metrics["config_status_tablename"] = prometheus_client.core.GaugeMetricFamily(
            self.namespace + "_config_status_tablename",
            "Dummy metric exporting the status tablename such that hosts "
            "synchronizing together can automatically be detected.",
            labels=tuple(self.default_labels.keys()) + ("status_tablename",)
        )
        return metrics

    def define_sys_metrics(self):
        """
        Defines the Prometheus metrics objects collected about the system and
        returns a dictionary of required metrics.
        """
        metrics = {}
        metrics["cpu_cores"] = prometheus_client.core.GaugeMetricFamily(
            self.namespace + "_cpu_cores",
            "The total number of physical CPUs cores.",
            labels=tuple(self.default_labels.keys())
        )
        metrics["cpu_threads_per_core"] = prometheus_client.core.GaugeMetricFamily(
            self.namespace + "_cpu_threads_per_core",
            "The number of threads per physical CPU core.",
            labels=tuple(self.default_labels.keys())
        )
        metrics["mem_total"] = prometheus_client.core.GaugeMetricFamily(
            self.namespace + "_mem_total",
            "The total memory available on the machine in bytes."
            "user-$UID.slice cgroup.",
            labels=tuple(self.default_labels.keys())
        )
        metrics["swap_total"] = prometheus_client.core.GaugeMetricFamily(
            self.namespace + "_swap_total",
            "The swap memory available on the machine in bytes.",
            labels=tuple(self.default_labels.keys())
        )
        return metrics

    def add_user_status_metrics(self, uids, uid_username_mapping, metrics):
        """
        Adds Arbiter2's status and badness metrics.

        uids: set
            A set of uids to add metrics for.
        uid_username_mapping: dict
            A mapping between uids and usernames
        metrics: dict
            A dictionary of Prometheus metric objects.
        """
        now = time.time()
        # If Arbiter2 hasn't updated the badness data within two intervals
        # then let's not trust the score, it's out of date!
        badness_timeout = cfg.general.arbiter_refresh * 2

        try:
            # read_badness returns nothing on error... sigh
            per_user_badness = self.statusdb_obj.read_badness()
            per_user_status = self.statusdb_obj.read_status()
        except statusdb.common_db_errors as err:
            logger.error("Failed to read statuses in statusdb %s: %s",
                         cfgparser.redacted_url(self.statusdb_obj.url), err)
            return

        for uid in uids:
            username = uid_username_mapping[uid]

            # For no good reason uids in read_status are strings rather than
            # ints... sigh
            if str(uid) in per_user_status:
                status = per_user_status[str(uid)]
            else:
                # Retrive their 'empty' status, which is basically just their
                # status that if they logged on with no record would have
                status = statuses.lookup_empty_status(uid)

            # Skip old badness scores, not accurate
            if uid in per_user_badness:
                badness_obj = per_user_badness[uid]
                if badness_obj.expired(timeout=badness_timeout):
                    badness_obj = badness.Badness()  # zero badness
            else:
                badness_obj = badness.Badness()  # Replace with zero badness

            self.add_per_user_status_metrics(uid, username, status, badness_obj, metrics)

    def add_per_user_status_metrics(self, uid, username, status, badness_obj, metrics):
        """
        Given a Status object and badness object, adds the values to the
        internal metrics.

        uid: int
            The user's uid associated with the metrics.
        username: str
            The user's username.
        status: statusdb.Status()
            A Status object representing a user.
        badness_obj: badness.Badness()
            A badness object corresponding to the user.
        metrics: dict
            A dictionary of Prometheus metric objects.
        """
        uid_str = str(uid)  # Prometheus wants a string
        metrics["user_status_group"].add_metric(
            labels=tuple(self.default_labels.values()) + (
                username, uid_str, status.current, status.default
            ),
            value=0 if status.current == status.default else 1
        )
        metrics["user_status_group_updated"].add_metric(
            labels=tuple(self.default_labels.values()) + (
                username, uid_str, status.current, status.default
            ),
            value=status.timestamp
        )
        metrics["user_penalty_occurrences"].add_metric(
            labels=tuple(self.default_labels.values()) + (username, uid_str, status.current),
            value=status.occurrences
        )
        metrics["user_penalty_occurrences_updated"].add_metric(
            labels=tuple(self.default_labels.values()) + (username, uid_str, status.current),
            value=status.occur_timestamp
        )
        if status.in_penalty():
            metrics["user_penalty"].add_metric(
                labels=tuple(self.default_labels.values()) + (username, uid_str, status.current),
                value=cfg.status.penalty.order.index(status.current)
            )
        metrics["user_authoritative"].add_metric(
            labels=tuple(self.default_labels.values()) + (username, uid_str, status.authority),
            value=int(status.authoritative())  # authoritative() -> bool
        )

        metrics["user_cpu_badness"].add_metric(
            labels=tuple(self.default_labels.values()) + (username, uid_str),
            value=badness_obj.cpu
        )
        metrics["user_mem_badness"].add_metric(
            labels=tuple(self.default_labels.values()) + (username, uid_str),
            value=badness_obj.mem
        )

    def add_config_metrics(self, metrics):
        """
        Adds configuration metrics to the given metrics.

        metrics: dict
            A dictionary of Prometheus metric objects.
        """
        metrics["config_arbiter_refresh"].add_metric(
            labels=tuple(self.default_labels.values()),
            value=cfg.general.arbiter_refresh
        )
        metrics["config_cpu_badness_threshold"].add_metric(
            labels=tuple(self.default_labels.values()),
            value=cfg.badness.cpu_badness_threshold
        )
        metrics["config_mem_badness_threshold"].add_metric(
            labels=tuple(self.default_labels.values()),
            value=cfg.badness.mem_badness_threshold
        )
        metrics["config_time_to_max_bad"].add_metric(
            labels=tuple(self.default_labels.values()),
            value=cfg.badness.time_to_max_bad
        )
        metrics["config_time_to_min_bad"].add_metric(
            labels=tuple(self.default_labels.values()),
            value=cfg.badness.time_to_min_bad
        )
        for status_group in set(cfg.status.order) | {cfg.status.fallback_status}:
            # The only way to get quotas beyond looking at the configuration
            # is by using the .quotas() function on a Status() object, so
            # we'll construct a bogus one here and use that. Avoid
            # configuration because there is weirdness with
            # cfg.status.div_cpu_quotas_by_threads_per_core
            status = statuses.Status(status_group, status_group, 0, 0, 0)
            cpu_quota, mem_quota = status.quotas()
            metrics["config_status_group_cpu_quota"].add_metric(
                labels=tuple(self.default_labels.values()) + (status_group, status_group),
                value=cpu_quota
            )
            metrics["config_status_group_mem_quota"].add_metric(
                labels=tuple(self.default_labels.values()) + (status_group, status_group),
                value=sysinfo.pct_to_gb(mem_quota) * 1024**3  # GiB -> bytes
            )

        for penalty_status_group in cfg.status.penalty.order:
            # If relative_quotas=true, let's try and just export the relative
            # ratio since the quotas are dependent on the default status group
            # each user belongs to and it feels wrong to export
            # num_status_groups * num_penalty_status_groups quotas.
            metrics["config_penalty_relative_quotas"].add_metric(
                labels=tuple(self.default_labels.values()) + (penalty_status_group,),
                value=int(cfg.status.penalty.relative_quotas)
            )
            if not cfg.status.penalty.relative_quotas:
                status = statuses.Status(status_group, status_group, 0, 0, 0)
                cpu_quota, mem_quota_pct = status.quotas()
                mem_quota = sysinfo.pct_to_gb(mem_quota_pct) * 1024**3  # GiB -> bytes
            else:
                penalty_prop = statuses.lookup_status_prop(penalty_status_group)
                cpu_quota = penalty_prop.cpu_quota  # actually a ratio
                mem_quota = penalty_prop.mem_quota  # same here

            metrics["config_penalty_cpu_quota"].add_metric(
                labels=tuple(self.default_labels.values()) + (penalty_status_group,),
                value=cpu_quota
            )
            metrics["config_penalty_mem_quota"].add_metric(
                labels=tuple(self.default_labels.values()) + (penalty_status_group,),
                value=mem_quota
            )
            metrics["config_penalty_timeout"].add_metric(
                labels=tuple(self.default_labels.values()) + (penalty_status_group,),
                value=statuses.lookup_status_prop(penalty_status_group).timeout
            )

        metrics["config_penalty_occur_timeout"].add_metric(
            labels=tuple(self.default_labels.values()),
            value=cfg.status.penalty.occur_timeout
        )
        sync_group = cfg.database.statusdb_sync_group
        metrics["config_status_tablename"].add_metric(
            labels=tuple(self.default_labels.values()) + (sync_group,),
            value=0
        )

    def add_sys_metrics(self, metrics):
        """
        Adds system stats such as memory total and CPU core counts to the
        given metrics.

        metrics: dict
            A dictionary of Prometheus metric objects.
        """
        metrics["cpu_cores"].add_metric(
            value=os.cpu_count() / sysinfo.threads_per_core,
            labels=tuple(self.default_labels.values())
        )
        metrics["cpu_threads_per_core"].add_metric(
            value=sysinfo.threads_per_core,
            labels=tuple(self.default_labels.values())
        )
        metrics["mem_total"].add_metric(
            value=sysinfo.total_mem,
            labels=tuple(self.default_labels.values())
        )
        metrics["swap_total"].add_metric(
            value=sysinfo.total_swap,
            labels=tuple(self.default_labels.values())
        )

    def add_user_slice_metrics(self, uids, uid_username_mapping, metrics):
        """
        Adds user.slice/user-$UID.slice cgroup stats to the given metrics and
        returns the set of users that could be successfully collected for.

        uids: set
            A set of uids to add metrics for.
        uid_username_mapping: dict
            A mapping between uids and usernames
        metrics: dict
            A dictionary of Prometheus metric objects.
        """
        # There is a good chance that a uid we see in the uids will disappear
        # at some point (e.g. they will log out) and we won't have a complete
        # record of their usage. For the atomicity constraint described in
        # this class' description, we'll keep track of who we don't want to
        # record future metrics for in this method and return it. We'll also
        # carefully follow the atomicity constraint here too
        active_uids = uids

        # We can't add user slice limits to the metrics dictionary unless the
        # later user slice usage metrics work out, so track them here and
        # apply at the end
        user_slice_limits = {}
        for uid in active_uids.copy():  # copy() -> popping uids
            success, limits = self.try_collect_per_user_slice_limits(uid)
            if not success:
                active_uids.discard(uid)
            else:
                user_slice_limits[uid] = limits

        user_slice_instances = collections.defaultdict(list)
        for poll in range(self.poll):
            for uid in active_uids.copy():
                success, instance = self.try_collect_per_user_slice_instance(uid)
                if not success:
                    active_uids.discard(uid)
                    # None -> uid instance list might not exist
                    user_slice_instances.pop(uid, None)
                else:
                    user_slice_instances[uid].append(instance)

            if poll != self.poll - 1:
                time.sleep(self.interval)

        for uid in active_uids:
            self.add_per_user_slice_limits_metrics(
                uid,
                uid_username_mapping[uid],
                user_slice_limits[uid],
                metrics
            )
            self.add_per_user_slice_instance_metrics(
                uid,
                uid_username_mapping[uid],
                user_slice_instances[uid],
                metrics
            )

        return active_uids

    def try_collect_per_user_slice_instance(self, uid):
        """
        Records an instance of a particular user.slice/user-$UID.slice cgroup
        if possible. Returns a tuple of whether the collection was successful
        and the instance.

        uid: int
            The user's uid associated with the metrics.
        """
        try:
            return True, cginfo.UserSliceInstance(uid, memsw=self.swap)
        except FileNotFoundError:
            logger.debug("Failed to collect instance metrics on "
                         "user.slice/user-%s.slice. Skipping...", str(uid))
            return False, None
        except OSError as err:
            logger.warning("Failed to collect instance metrics on "
                           "user.slice/user-%s.slice because of a unknown OS "
                           "error. Skipping... %s", str(uid), str(err))
            return False, None

    def add_per_user_slice_instance_metrics(self, uid, username, instances, metrics):
        """
        Adds user.slice/user-$UID.slice cgroup stats from the collected
        instances to the internal metrics.

        uid: int
            The user's uid associated with the metrics.
        username: str
            The user's username.
        instances: list
            A list of instances: cginfo.UserSliceInstance().
        metrics: dict
            A dictionary of Prometheus metric objects.
        """
        uid_str = str(uid)  # Prometheus wants a string

        # Finally combine the instances into actual metrics
        static_user_slice = usage.average(
            *usage.combine(*instances),
            by=self.poll
        )
        metrics["user_mem_usage"].add_metric(
            labels=tuple(self.default_labels.values()) + (username, uid_str),
            # Convert to bytes from percentages
            value=static_user_slice.usage["mem"] / 100 * sysinfo.total_mem
        )
        metrics["user_cpu_usage"].add_metric(
            labels=tuple(self.default_labels.values()) + (username, uid_str),
            # We're pulling from cgroup cpuacct's usage_percpu which reports
            # per virtual CPU so the effect is that when reporting relative
            # to physical cores, your usage is half of what is should be.
            # Reverse this here. If users want to know per virtual CPU they
            # can simply divide by the cpu_threads_per_core we also give
            value=static_user_slice.usage["cpu"] * sysinfo.threads_per_core
        )

    def try_collect_per_user_slice_limits(self, uid):
        """
        Records the CPU and memory limits of a particular
        user.slice/user-$UID.slice cgroup if possible. Returns a tuple of
        whether the collection was successful and a tuple of the limits.

        uid: int
            The user's uid associated with the metrics.
        """
        user_slice = cginfo.UserSlice(uid)
        try:
            mem_limit = user_slice.mem_quota(memsw=self.swap)
            cpu_limit = user_slice.cpu_quota()
        except FileNotFoundError:
            logger.debug("Failed to collect instance metrics on "
                         "user.slice/user-%s.slice. Skipping...", str(uid))
            return False, tuple()
        except OSError as err:
            logger.warning("Failed to collect instance metrics on "
                           "user.slice/user-%s.slice because of a unknown OS "
                           "error. Skipping... %s", str(uid), str(err))
            return False, tuple()

        # If a limit isn't set it's -1, but cpu_quota returns a percentage
        # which makes it 0.001 (also because of the cpu_period) so just make
        # it -1 so it's easier to tell if the cgroup limit is unset
        if cpu_limit == -1 * 100:  # No limit set
            cpu_limit = -1

        return True, (cpu_limit, mem_limit)

    def add_per_user_slice_limits_metrics(self, uid, username, limits, metrics):
        """
        Adds user.slice/user-$UID.slice cgroup limits to the internal metrics.

        uid: int
            The user's uid associated with the metrics.
        username: str
            The user's username.
        limits: (float, float)
            A tuple of CPU and memory limits
        metrics: dict
            A dictionary of Prometheus metric objects.
        """
        uid_str = str(uid)  # Prometheus wants a string

        cpu_limit, mem_limit = limits
        metrics["user_cpu_limit"].add_metric(
            labels=tuple(self.default_labels.values()) + (username, uid_str),
            value=cpu_limit
        )
        metrics["user_mem_limit"].add_metric(
            labels=tuple(self.default_labels.values()) + (username, uid_str),
            value=mem_limit
        )


def parse_statusdb_url(args):
    """
    Given args, return the correct statusdb url.
    """
    # Local sqlite database
    if args.database_loc:
        return "sqlite:///{}".format(args.database_loc)
    # Provided URL
    elif args.statusdb_url:
        return args.statusdb_url
    # Resolve to what statusdb_url is when empty in the config
    elif cfg.database.statusdb_url == "":
        return "sqlite:///{}".format(cfg.database.log_location + "/statuses.db")
    # Use statusdb_url from the config
    else:
        return cfg.database.statusdb_url


def bootstrap(args):
    """
    Configures the program so that it can function correctly. This is done by
    changing into the arbiter directory and then importing arbiter functions.
    """
    logger.setLevel(logging.INFO)
    if args.verbose:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.CRITICAL)

    # Make the path to files absolute. This makes behavior consistent when
    # changing directories. Otherwise, configuration files would be relative to
    # the arbiter/ directory
    args.configs = [os.path.abspath(path) for path in args.configs]
    os.chdir(args.arbdir)
    insert(args.arbdir)
    import cfgparser
    try:
        if not cfgparser.load_config(*args.configs, pedantic=False):
            print("There was an issue with the specified configuration (see "
                  "above). You can investigate this with the cfgparser.py "
                  "tool.")
            sys.exit(2)
    except (TypeError, toml.decoder.TomlDecodeError) as err:
        print("Configuration error:", str(err), file=sys.stderr)
        sys.exit(2)


def insert(context):
    """
    Appends a path to into the Python path.
    """
    context_path = os.path.dirname(__file__)
    sys.path.insert(0, os.path.abspath(os.path.join(context_path, context)))


def arbiter_environ():
    """
    Returns a dictionary with the ARB environment variables. If a variable is
    not found, it is not in the dictionary.
    """
    env = {}
    env_vars = {
        "ARBETC": ("-e", "--etc"),
        "ARBDIR": ("-a", "--arbdir"),
        "ARBCONFIG": ("-g", "--config")
    }
    for env_name, ignored_prefixes in env_vars.items():
        env_value = os.environ.get(env_name)
        if not env_value:
            continue
        warn = lambda i, s: print("{} in {} {}".format(i, env_name, s))
        expanded_path = lambda p: os.path.expandvars(os.path.expanduser(p))

        for prefix in ignored_prefixes:
            if env_value.startswith(prefix):
                env_value = env_value.lstrip(prefix).lstrip()
                break

        if env_name == "ARBCONFIG":
            config_paths = shlex.split(env_value, comments=False, posix=True)
            valid_paths = []
            for path in config_paths:
                if not os.path.isfile(expanded_path(path)):
                    warn(path, "does not exist")
                    continue
                valid_paths.append(path)

            if valid_paths:
                env[env_name] = valid_paths
            continue

        expanded_value = expanded_path(env_value)
        if not os.path.exists(expanded_value):
            warn(env_value, "does not exist")
            continue
        if not os.path.isdir(expanded_value):
            warn(env_value, "is not a directory")
            continue
        if env_name == "ARBDIR" and not os.path.exists(expanded_value + "/arbiter.py"):
            warn(env_value, "does not contain arbiter modules! (not arbiter/ ?)")
            continue
        if env_name == "ARBETC" and not os.path.exists(expanded_value + "/integrations.py"):
            warn(env_value, "does not contain etc modules! (no integrations.py)")
            continue
        env[env_name] = expanded_value
    return env


def main(args):
    """
    Runs the Arbiter2 exporter on the given port.
    """
    logger.info("Staring HTTP server on port %s", args.port)
    statusdb_url = parse_statusdb_url(args)
    statusdb_obj = statusdb.lookup_statusdb(statusdb_url)

    collector = ArbiterCollector(
        statusdb_obj,
        min_uid=cfg.general.min_uid,
        swap=cfg.processes.memsw,
    )
    prometheus_client.core.REGISTRY.register(collector)
    prometheus_client.start_http_server(args.port)
    while True:
        # The prometheus client runs in background, but we need to stay
        # alive... ah ah ah
        time.sleep(5)


if __name__ == "__main__":
    desc = "Exports arbiter measurements to Prometheus when pulled."
    parser = argparse.ArgumentParser(description=desc)
    arb_environ = arbiter_environ()
    parser.add_argument("-a", "--arbdir",
                        type=str,
                        help="Sets the directory in which Arbiter modules "
                             "are loaded from. Defaults to $ARBDIR if "
                             "present or ../arbiter otherwise.",
                        default=arb_environ.get("ARBDIR", "../arbiter"),
                        dest="arbdir")
    parser.add_argument("-g", "--config",
                        type=str,
                        nargs="+",
                        help="The configuration files to use. Configs will be "
                             "cascaded together starting at the leftmost (the "
                             "primary config) going right (the overwriting "
                             "configs). Defaults to $ARBCONFIG if present or "
                             "../etc/config.toml otherwise.",
                        default=arb_environ.get("ARBCONFIG", ["../etc/config.toml"]),
                        dest="configs")

    parser.add_argument("-p", "--port",
                        type=int,
                        default=9765,
                        help="Which port to run under. Defaults to 9765.",
                        dest="port")

    url = parser.add_mutually_exclusive_group()
    url.add_argument("-u", "--statusdb-url",
                     type=str,
                     help="Pulls from the specified statusdb url. Defaults "
                          "to database.statusdb_url specified in the "
                           "configuration.",
                    dest="statusdb_url")
    url.add_argument("-d", "--database",
                     type=str,
                     help="Pulls from the specified sqlite statusdb, rather "
                          "than database.statusdb_url specified in the "
                          "configuration.",
                     dest="database_loc")

    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument("-q", "--quiet",
                           action="store_true",
                           help="Only outputs critical information to stdout.",
                           dest="quiet")
    verbosity.add_argument("-v", "--verbose",
                           action="store_true",
                           help="Turns on debugging output to stdout.",
                           dest="verbose")
    args = parser.parse_args()
    bootstrap(args)
    import badness
    import statusdb
    import statuses
    import database  # Needed for errors
    import cginfo
    import sysinfo
    import usage
    import cfgparser
    from cfgparser import cfg, shared
    main(args)
