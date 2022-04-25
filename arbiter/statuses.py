# SPDX-FileCopyrightText: Copyright (c) 2019-2020 Center for High Performance Computing <helpdesk@chpc.utah.edu>
#
# SPDX-License-Identifier: GPL-2.0-only

"""
Methods related to getting status information.
"""
import datetime
import logging
import time
import types

import sysinfo
from cfgparser import cfg, Configuration

logger = logging.getLogger("arbiter." + __name__)


class Status(types.SimpleNamespace):
    """
    A class for storing a status. A status is a state that the user is in and
    the specific state and its properties (e.g. quotas) are called a status
    group. A user can only have a single status group at any moment, called
    their "current status", as well as a "default status," which is used to
    restore a user from their current status when a user's time in a penalty
    status group has expired. To track which penalty state (since there can
    be multiple) a user is in, there is a internal occurrences count which
    maps directly to the configured penalty states (e.g. an occurrences of 3
    means that the user's current status group should be the third penalty
    status group). The internal timestamps stored here are used to determine
    when a user should be put out of a penalty status group or have their
    occurrences lowered (they've been forgiven for their badness). Finally,
    the authority string tracks which host was the one who put someone in
    penalty, so that when the user goes out of penalty, only that host can
    send the nice all clear email to the user.

    Properties:
    current: str
        The user's current status group.
    default: str
        The user's current status group.
    occurrences: int
        The mapping between penalty status groups.
    timestamp: int
        A epoch timestamp for when the user's current status group was last
        updated (i.e. when a penalty was applied).
    occur_timestamp: int
        A epoch timestamp for when the user's occurrences was last updated.
    authority: str
        The host which changed the current status group last.
    """

    __slots__ = ["current", "default", "occurrences", "timestamp",
                 "occur_timestamp", "authority"]

    def __init__(self, current, default, occurrences, timestamp,
                 occur_timestamp, authority=sysinfo.hostname):
        self.current = current
        self.default = default
        self.occurrences = occurrences
        self.timestamp = timestamp
        self.occur_timestamp = occur_timestamp
        self.authority = authority

    def is_empty(self, uid):
        """
        Returns whether the status is empty for the user.
        """
        return self.equal(lookup_empty_status(uid))

    def in_penalty(self):
        """
        Returns whether the current status group is a penalty status group.
        """
        return lookup_is_penalty(self.current)

    def authoritative(self):
        """
        Returns whether this host is the authority of the current status. i.e.
        whether this host can send a warning email or nice email.
        """
        return self.authority == sysinfo.hostname

    def enforce_cfg_db_consistency(self, uid):
        """
        Ensures that the default and current status is consistent with the
        given user's configured status groups. If both the current and default
        statuses are the same and inconsistent, both statuses will be changed
        to the configured values. Otherwise, only the default status will be
        changed. This is meant to be called on statuses coming from the status
        database.

        The motivation for this is that occasionally the configuration is
        changed, but a user has a entry in statusdb with their old status
        (thus, the user's correct status couldn't be applied). This function
        enforces that the configuration is the ultimate source for determining
        a user's default status (note it is not the source for the current
        status).

        uid: int
            The user's uid.
        """
        cfg_default_status = lookup_default_status_group(uid)
        if self.default != cfg_default_status:
            if self.current == self.default:
                self.current = cfg_default_status
            self.default = cfg_default_status

    def quotas(self, default=False):
        """
        Returns the current status group quotas. The quotas are returned as a
        pct of the machine, rather than the configured values!

        default: bool
            Whether or not to return the quotas of the default status group,
            rather than the default.
        """
        current_status_group = self.default if default else self.current
        default_status_group = self.default
        status_prop = lookup_status_prop(current_status_group)
        quotas = [
            status_prop.cpu_quota,
            status_prop.mem_quota / sysinfo.bytes_to_gb(sysinfo.total_mem) * 100
        ]
        if cfg.status.div_cpu_quotas_by_threads_per_core:
            quotas[0] /= sysinfo.threads_per_core

        rel_quotas = cfg.status.penalty.relative_quotas
        if not default and self.in_penalty() and rel_quotas:
            default_prop = lookup_status_prop(default_status_group)
            quotas[0] = quotas[0] * default_prop.cpu_quota
            quotas[1] = quotas[1] * default_prop.mem_quota
        return quotas

    def has_occurrences(self):
        """
        Returns whether the status has any record of a user being in penalty.
        i.e. penalty occurrences > 0
        """
        return self.occurrences > 0

    def lower_occurrences(self):
        """
        Lowers the occurrences to at most 0.
        """
        self.occurrences = max(0, self.occurrences - 1)
        self.occur_timestamp = int(time.time())
        # We might have just been non-authoritative, but we independently
        # lower occurrences as a resilience mechanism and thus, we should be
        # allowed to send emails if a user gets in penalty again.
        self.authority = sysinfo.hostname
        return self.occurrences

    def reset_occurrences_timeout(self):
        """
        Resets the timestamp for when the timer should start on occurrences
        forgiveness.
        """
        self.occur_timestamp = int(time.time())

    def occurrences_expired(self):
        """
        Returns whether the timeout on occurrences has expired.
        """
        timeout = cfg.status.penalty.occur_timeout
        return self.occur_timestamp + timeout < time.time()

    def penalty_index(self):
        """
        Returns a index into the configuration's penalty order list, or -1 if
        the user is not in penalty.
        """
        penalties = cfg.status.penalty.order
        try:
            return self.current in penalties
        except ValueError:
            return -1

    def penalty_timeout(self):
        """
        Returns the configured timeout in seconds for the penalty. If the
        current status group is not a penalty, returns 0.
        """
        if self.in_penalty():
            return lookup_status_prop(self.current).timeout
        return 0

    def penalty_expired(self):
        """
        Returns whether the timeout on the penalty has expired. If the current
        status group is not penalty returns false.
        """
        return self.timestamp + self.penalty_timeout() < time.time()

    def downgrade_penalty(self):
        """
        Downgrades the penalty status.
        """
        self.current = self.default
        # Note: This line makes a significant, but necessary assumption: all
        #       hosts that sync statuses have clocks that are reasonably up
        #       to date with one another (within a couple seconds). We
        #       _cannot_ use a central time source (e.g. our database) because
        #       the syncronization algorithm is "eventually consistent" and
        #       proceeds even in the case of network failure.
        self.timestamp = int(time.time())
        # Reset the occurrences timeout so that the timer to lower occurrences
        # starts when the user's penalty was lowered, rather than when they got
        # into penalty
        self.occur_timestamp = self.timestamp
        # We might have just been non-authoritative, but we independently
        # lower penalties as a resilience mechanism and thus, we should be
        # allowed to send emails if a user gets in penalty again.
        self.authority = sysinfo.hostname
        return self.current

    def upgrade_penalty(self):
        """
        Upgrades the penalty status.
        """
        penalties = cfg.status.penalty.order
        self.occurrences = min(self.occurrences + 1, len(penalties))
        self.timestamp = int(time.time())
        self.occur_timestamp = self.timestamp
        self.current = penalties[self.occurrences - 1]
        self.authority = sysinfo.hostname
        return self.current

    def resolve_with_ourself(self, other_status):
        """
        Resolves the most up-to-date status into self.

        Returns whether or not we adopted the other_status

        See StatusDB.synchronize_status_from_ourself for how this is used.
        """
        max_timestamp = max(self.timestamp, self.occur_timestamp)
        other_max_timestamp = max(other_status.timestamp, other_status.occur_timestamp)
        if other_max_timestamp > max_timestamp:
            self.current = other_status.current
            self.default = other_status.default
            self.occurrences = other_status.occurrences
            self.timestamp = other_status.timestamp
            self.occur_timestamp = other_status.occur_timestamp
            self.authority = sysinfo.hostname
            return True
        return False

    def resolve_with_other_hosts(self, host_statuses):
        """
        Given a dictionary of statuses on other hosts, resolves the most
        severe valid penalty status into self and returns the hostname of the
        status that was choosen. If there was a change in the current status
        group, the particular host choosen is set to the authority.

        See __gt__ for details on how a status is resolved.

        See StatusDB.synchronize_status_from_other_hosts for how this is used.
        """
        was_in_penalty = self.in_penalty()
        resolved_hostname = sysinfo.hostname
        max_status = self.copy()

        for other_hostname, other_status in host_statuses.items():
            if max_status > other_status:
                continue

            max_status = other_status
            resolved_hostname = other_hostname
            self.current = other_status.current
            self.default = other_status.default
            self.occurrences = other_status.occurrences
            self.timestamp = other_status.timestamp
            self.occur_timestamp = other_status.occur_timestamp

        # If the choosen host was this host (not necessarily no change), then
        # we are obviously always authoritative
        this_host_choosen = resolved_hostname == sysinfo.hostname
        # If upgrade_penalty() on other host; we cannot email users since
        # that host is in charge of that thing
        if self.in_penalty() and not was_in_penalty and not this_host_choosen:
            self.authority = resolved_hostname
        # If downgrade_penalty() on other host; we can freely email users if
        # the user is bad
        if not self.in_penalty() and was_in_penalty:
            self.authority = sysinfo.hostname
        return resolved_hostname

    def equal(self, other):
        """
        Equality based on the current and default status groups, as well as
        occurrences.
        """
        return (
            self.current == other.current and
            self.default == other.default and
            self.occurrences == other.occurrences
        )

    def strictly_equal(self, other):
        """
        Equality based on all the properties of the status (including
        timestamps).
        """
        return (
            self.current == other.current and
            self.default == other.default and
            self.occurrences == other.occurrences and
            self.timestamp == other.timestamp and
            self.occur_timestamp == other.occur_timestamp
        )

    def copy(self):
        """
        Returns a shallow copy of itself. Note that since all the properties
        are immutable this is the same as a deep copy.
        """
        return Status(
            self.current,
            self.default,
            self.occurrences,
            self.timestamp,
            self.occur_timestamp,
            authority=self.authority
        )

    def __eq__(self, other):
        """
        Returns equality of the given status and this status.
        """
        return self.strictly_equal(other)

    def __ne__(self, other):
        """
        Returns the inverted equality of the given status and this status.
        """
        return not self.__eq__(other)

    def __gt__(self, other):
        """
        Returns whether this status is intuitively greater than the given
        status based on four short-circuting conditions:

            1. If other is in penalty, is the other's penalty invalid?
            2. If other is in penalty, is the other's penalty (if any) less
               than ours?
            3. Is other's occurrences less than ours, or if they are the same,
               is the other's invalid?
            4. Have we updated our status more recently or the same as the
               other?
        """
        if other.in_penalty():
            if other.penalty_expired():
                return True
            if other.penalty_index() < self.penalty_index():
                return True

        if other.occurrences < self.occurrences:
            return True
        if other.occurrences == self.occurrences and other.occurrences_expired():
            return True

        self_max_ts = max(self.timestamp, self.occur_timestamp)
        other_max_ts = max(other.timestamp, other.occur_timestamp)
        if self_max_ts >= other_max_ts:
            return True

        return False

    def __repr__(self):
        return (
            "Status(current='{}', default='{}', occurrences={}, "
            "timestamp={}, occur_timestamp={}, authority={})"
        ).format(
            self.current,
            self.default,
            self.occurrences,
            self.timestamp,
            self.occur_timestamp,
            self.authority
        )

    def __str__(self):
        iso_ts = ""
        if self.timestamp != 0:
            epoch_ts_dt = datetime.datetime.fromtimestamp(self.timestamp)
            iso_ts = datetime.datetime.isoformat(epoch_ts_dt)

        iso_occur_ts = ""
        if self.occur_timestamp != 0:
            epoch_occur_ts_dt = datetime.datetime.fromtimestamp(self.occur_timestamp)
            iso_occur_ts = datetime.datetime.isoformat(epoch_occur_ts_dt)

        return "Status({}/{}, occur={}, ts={}, occur_ts={}, authority={})".format(
            self.current,
            self.default,
            self.occurrences,
            iso_ts,
            iso_occur_ts,
            self.authority
        )

    def override_status_group(self, new_status_group):
        """
        Sets the current status group, removing penalty occurrences if needed.
        The timestamps are set slightly into the future so that this status
        will take priority over another status in resolve_with_self.
        """
        self.current = new_status_group
        self.occurrences = 0
        self.timestamp = int(time.time() + (2 * cfg.general.arbiter_refresh))
        self.occur_timestamp = self.timestamp


def lookup_is_penalty(status_group):
    """
    Returns whether the status group is a penalty status group.
    """
    return status_group in cfg.status.penalty.order


def lookup_status_prop(status_group):
    """
    Looks up the status group properties from the config, and returns the
    properties as a Configuration() object. If the group doesn't exist, returns
    a empty Configuration object. The quotas follow the configuration values.

    status_group: str
        The user's current status group.
    """
    context = cfg.status
    if lookup_is_penalty(status_group):
        context = cfg.status.penalty
    return getattr(context, status_group, Configuration({}))


def lookup_default_status_group(uid):
    """
    Looks up the default status group of the user, matching in the order a
    status group appears in config. The fallback status group specified
    in config will be returned if the user doesn't match any groups.

    uid: int
        The user's uid.
    """
    # Cast types to make sure arguments are integers
    uid = int(uid)
    gids = sysinfo.query_gids(uid)

    for status_group in cfg.status.order:
        status_prop = lookup_status_prop(status_group)
        if uid in status_prop.uids or any(gid in status_prop.gids for gid in gids):
            return status_group
    return cfg.status.fallback_status


def lookup_empty_status(uid):
    """
    Looks up a empty status of the user based on the configuration. The
    status will have the same current and default statuses, as well as zero
    occurrences and empty timestamps.

    uid: int
        The user's uid.
    """
    default_status_group = lookup_default_status_group(uid)
    return Status(default_status_group, default_status_group, 0, 0, 0)
