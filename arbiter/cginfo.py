#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2019-2020 Center for High Performance Computing <helpdesk@chpc.utah.edu>
# SPDX-FileCopyrightText: Copyright (c) 2020 Idaho National Laboratory
#
# SPDX-License-Identifier: GPL-2.0-only

"""
A module that gets utility information relating to systemd cgroups (Linux
cgroups v1). The classes follow the usage module philosophy of Static,
non-Static and Instance. See usage.py for details.

This module uses diamond inheritance (it was either that or duplicating
properties/methods), so beware of the fact that Python resolves those things
in the following fashion:
A(), B(A), C(A), D(B, C)

super() resolves to:
A: object
B: A
C: A
D: B

In D, you can explicitly call C via C.method(self, ...).
"""

import glob
import logging
import os
import re
import time

import sysinfo
import usage

logger = logging.getLogger("arbiter." + __name__)

# The base path for the cgroup hierarchy. I _think_ systemd has standardized
# this, but not 100% sure.
base_path = "/sys/fs/cgroup"

# The controller used to check whether a cgroup exists and to get pids from.
default_controller = "systemd"


class SystemdCGroup():
    """
    An object that contains methods/properties related to a specific systemd
    cgroup.
    """

    def __init__(self, name, parent):
        """
        Initializes an object that contains methods/properties related to a
        systemd cgroup. If a parent is specified, the parent's path must be at
        the top level of the cgroup hierarchy (/sys/fs/cgroup). e.g. user.slice
        or system.slice.

        name: str
            The full qualified name of the cgroup. e.g. user-1000.slice,
            user.slice & systemd-journald.service.
        parent: str
            The parent of the cgroup. e.g. /, user.slice, system.slice.
        memsw: bool
            Whether or not to use memsw for cgroup memory information.

        >>> c.SystemdCGroup("systemd-journald.service", "system.slice")
        >>> c.SystemdCGroup("user.slice")
        """
        self.name = name
        self.parent = parent

    def controller_exists(self, controller):
        """
        Returns whether a specific cgroup controller exists.
        """
        path = self.controller_path(controller=controller, cgfile="")
        return os.path.exists(path) and os.path.isdir(path)

    def controller_path(self, controller=default_controller, cgfile=""):
        """
        Returns the path to a cgroup property or file without checking that
        the path exists.

        >>> SystemdCGroup("user-0.slice", "user.slice").controller_path()
        "/sys/fs/cgroup/systemd/user.slice/user-0.slice"
        >>> SystemdCGroup("user-562.slice", "user.slice").controller_path("cpuacct")
        "/sys/fs/cgroup/cpuacct/user.slice/user-562.slice"
        """
        path_parts = (base_path, controller, self.parent, self.name, cgfile)
        return "/".join(filter(lambda p: p != "", path_parts))

    def assert_controller_path(self, controller=default_controller, cgfile=""):
        """
        Returns the path to a cgroup property or file. If a property is not
        given, the fully qualified systemd controller path is returned. If the
        path does not exist, a FileNotFoundError is raised.

        controller: str
            A cgroup controller. e.g. cpuacct, memory, blkio
        cgfile: str
            The cgroup file below the property in the path.

        >>> notaslice = SystemdCGroup("nota.slice", "system.slice")
        >>> notaslice.assert_controller_path("memory")
        FileNotFoundError("Cgroup property...")
        >>> rootslice = SystemdCGroup("user-0.slice", "user.slice")
        >>> rootslice.assert_controller_path("cpuacct", "cpu.stat")
        "/sys/fs/cgroup/cpu/user.slice/user-0.slice/cpu.stat"
        """
        path = self.controller_path(controller=controller, cgfile=cgfile)
        if not os.path.exists(path):
            raise FileNotFoundError("cgroup property path doesn't exist. "
                                    "This might be due to cgroup accounting "
                                    "not on, the cgroup not existing, or an "
                                    "invalid property. Path: " + path)
        return path

    def active(self):
        """
        Returns whether the current cgroup exists in the cgroup hierarchy.

        >>> SystemdCGroup("notacgroup.slice", "user.slice").active()
        False
        """
        try:
            self.assert_controller_path()
            return True
        except (FileNotFoundError, PermissionError):
            return False

    def cpu_usage_per_core(self):
        """
        Returns a list of current CPU usages (in CPU time (nanosecond)) of the
        cgroup, indexed by core - 1.

        >>> self.get_cpu_usage_per_core()
        [549135244092, 1150535824026, 412981314604, 1081336776345]
        """
        prop = "cpuacct.usage_percpu"
        with open(self.assert_controller_path("cpuacct", prop)) as cpuacct:
            return list(map(int, cpuacct.readline().split()))

    def mem_usage(self, memsw=True, kmem=False, page_cache=False):
        """
        Gets the memory utilization as a proportion of the system's total
        memory or in bytes. If memsw is True, the swap usage is added into the
        reported memory usage. If kmem is True, then kernel memory usage is
        also added. Similarly, if page_cache is True, then page cached memory
        is added as well.

        Note: With memory.stat shared memory is not proportionally divided
              between cgroups, meaning if a bunch of processes share a file
              mapping, then this will be counted against the owner of that
              mapping, instead of proportionally for every user who is using
              it. This is what the PSS (proportional shared size) value in
              /proc/<pid>/smaps is and it's optionally used by
              pidinfo.Process(). There's no nice way to find this out
              (expensive for the kernel), but this shouldn't be too big a
              problem since most shared memory is probably not shared between
              different users (except for glibc and common libraries, which
              hopefully shouldn't be big enough to cause problems).

        See kernel.org/doc/html/latest/admin-guide/cgroup-v1/memory.html for
        more details on this code.

        memsw: bool
            Whether to include swap.
        kmem: bool
            Whether to include kernel memory.
        page_cache: bool
            Whether to include page cache memory.

        >>> self.mem_usage()
        40
        """
        usage_bytes = 0
        # memory.usage_in_bytes includes page_cache info, also is a quick fuzz
        # value to avoid multi-core/numa cacheline false sharing, so
        # unfortauntely cannot use (historically this was incorrectly used)
        memory_stat_file = "memory.stat"
        kmem_usage_file = "memory.kmem.usage_in_bytes"

        mem_params = [
            # Anon and swap cache memory (cannot subtract out if memsw=False)
            # See "Swap Cache" part of
            # https://www.kernel.org/doc/gorman/html/understand/understand014.html
            # and https://www.halolinux.us/kernel-architecture/the-swap-cache.html
            # for details, note this is not the same as "swap". Also, if swap
            # is turned off this I think is zero -Dylan
            "total_rss",
            # Plus need file backed memory
            "total_mapped_file",
        ]
        if memsw:
            mem_params.append("total_swap")
        if page_cache:
            mem_params.append("total_cache")
        if kmem:
            with open(self.assert_controller_path("memory", kmem_usage_file)) as memfile:
                usage_bytes += int(memfile.read().strip())

        mem_re = r"({}) (\d+)".format("|".join(mem_params))
        mem_pattern = re.compile(mem_re)
        with open(self.assert_controller_path("memory", memory_stat_file)) as memfile:
            usage_bytes += sum(
                int(match.group(2)) if match else 0
                for match in mem_pattern.finditer(memfile.read())
            )
        return usage_bytes

    def pids(self):
        """
        Returns a list of current pids in the cgroup.
        """
        pids = []
        with open(self.assert_controller_path(cgfile="cgroup.procs")) as procfile:
            for pid in procfile.readlines():
                pids.append(pid.strip())
        return pids

    def cpu_quota(self):
        """
        Returns the current cgroup's CPU quota as a percentage. A -1 indicates
        that the quota has not been set.
        """
        quota_path = self.assert_controller_path("cpuacct", "cpu.cfs_quota_us")
        period_path = self.assert_controller_path("cpuacct", "cpu.cfs_period_us")
        with open(quota_path) as quota:
            with open(period_path) as period:
                return (float(quota.readline().strip()) /
                        float(period.readline().strip())) * 100

    def mem_quota(self, memsw=True):
        """
        Returns the current cgroup's memory quota in bytes. A -1 indicates that
        the quota has not been set.

        memsw: bool
            Whether or not to use memsw for getting the memory quota.
        """
        filename = "memory{}.limit_in_bytes".format(".memsw" if memsw else "")
        with open(self.assert_controller_path("memory", filename)) as quota:
            return int(quota.readline().strip())

    def _set_quota(self, quota, controller, cgfile):
        """
        Writes out a cgroup quota to a file.

        quota: int
            The corresponding quota.
        controller: str
            The controller to set the quotas on. e.g. "memory", "cpuacct".
        cgfile: str
            The path to the file relative to the property. e.g. cpu.shares.
        """
        with open(self.assert_controller_path(controller, cgfile), "w+") as prop:
            prop.write(str(quota))

    def set_mem_quota(self, quota, memsw=False):
        """
        Sets the memory quota of the user as a percentage of the machine.

        quota: float
            The memory quota (e.g. 50 for 50% of the total memory).
        memsw: bool
            Whether or not to write out to memory.memsw.limit_in_bytes.
        """
        raw_quota = int(sysinfo.total_mem * (quota / 100))
        files = ["memory.limit_in_bytes"]
        if memsw:
            memsw = "memory.memsw.limit_in_bytes"
            # memory.limit_in_bytes must be written before memsw, depending on
            # if higher
            if raw_quota >= self.mem_quota(memsw=True):
                files.insert(0, memsw)
            else:
                files.append(memsw)
        for memfile in files:
            self._set_quota(raw_quota, "memory", memfile)

    def set_cpu_quota(self, quota):
        """
        Sets the cpu quota of the user as a percentage of a core using the
        cpu period to get the quota relative to the shares.

        quota: float
            The cpu quota (e.g. 100 for 100% of a single core).
        """
        period_path = self.assert_controller_path("cpuacct", "cpu.cfs_period_us")
        with open(period_path, "r") as cpu_shares_file:
            shares = int(cpu_shares_file.readline())
        self._set_quota(int(quota / 100 * shares), "cpuacct", "cpu.cfs_quota_us")


class StaticSystemdCGroup(usage.Usage, SystemdCGroup):
    """
    A single state of a systemd cgroup that contains human readable values.
    """

    def __init__(self, name, parent, **kwargs):
        """
        Initializes a static SystemdCGroup.
        """
        self._pids = []
        SystemdCGroup.__init__(self, name, parent)
        super().__init__(**kwargs)

    def __repr__(self):
        return "<{}: {}>".format(
            type(self).__name__,
            "/".join([base_path, self.parent, self.name])
        )

    def __add__(self, other):
        """
        Adds two StaticSystemdCGroup objects together by taking the values of
        the first and adding the usage and pids.
        """
        if isinstance(other, type(self)):
            new = super().__add__(other)
            new.name = self.name
            new.parent = self.parent
            new._pids = list(set(self._pids + other._pids))
            return new
        return super().__add__(other)

    def __sub__(self, other):
        """
        Subtracts two StaticSystemdCGroup objects together by taking the values
        of the first, subtracting the usage and adding the pids.
        """
        if isinstance(other, type(self)):
            new = super().__sub__(other)
            new.name = self.name
            new.parent = self.parent
            new._pids = list(set(self._pids + other._pids))
            return new
        return super().__sub__(other)

    def pids(self):
        """
        Returns the recorded pids of the systemd cgroup.
        """
        return self._pids


class SystemdCGroupInstance(SystemdCGroup):
    """
    An object that contains instaneous usage information related to a specific
    systemd cgroup.
    """

    def __init__(self, name, parent, memsw=False):
        """
        Initializes the instaneous usage information of a systemd cgroup
        """
        SystemdCGroup.__init__(self, name, parent)
        # Yeah let's not muck around with leap seconds and the like...
        self.monotonic_time = time.monotonic()
        # Raise errors if these fail since it likely means user disappeared
        self.cputime = sum(self.cpu_usage_per_core())
        self.memory_bytes = self.mem_usage(memsw=memsw)
        self._pids = self.pids()

    def __add__(self, other):
        raise TypeError(
            "Cannot add to a instaneous {} object.".format(
                type(self).__name__
            )
        )

    def __sub__(self, other):
        raise TypeError(
            "Cannot subtract to a instaneous {} object.".format(
                type(self).__name__
            )
        )

    def _calc_usage(self, other):
        """
        Returns the usage between the instances.
        """
        # If a cgroup disappears between collection of instances, the
        # instances will have drastically different CPU and memory data,
        # leading to erroneous results. To get around this, we zero out usage
        # if the cputime (which is cumulative) of the first is greater than
        # the second.
        if self.cputime > other.cputime:
            return usage.metrics.copy()  # Zero out the usage
        return {
            "cpu": (
                max(other.cputime - self.cputime, 0) /
                (other.monotonic_time - self.monotonic_time) / 1e9 * 100
            ),
            "mem": (
                (other.memory_bytes + self.memory_bytes) / 2
                / sysinfo.total_mem * 100
            )
        }

    def __truediv__(self, other):
        """
        Averages two UserSliceInstances together into a human readable
        StaticUserSlice. The divisor instance should come later in time than
        the dividend (older / newer).
        """
        if isinstance(other, type(self)):
            return StaticSystemdCGroup(
                self.name,
                self.parent,
                _pids=list(set(self._pids + other._pids)),
                usage=self._calc_usage(other)
            )
        return super().__truediv__(other)


class AllUsersSlice(SystemdCGroup):
    """
    An object that contains methods/propreties related to user.slice.
    """

    def __init__(self):
        """
        Initializes an object that contains methods/properties related to
        user.slice.
        """
        super().__init__("user.slice", "")


class StaticAllUsersSlice(StaticSystemdCGroup, AllUsersSlice):
    """
    A single state of user.slice that contains human readable values.
    """

    def __init__(self, **kwargs):
        """
        Initializes a static AllUsersSlice.
        """
        AllUsersSlice.__init__(self)
        super().__init__(self.name, self.parent, **kwargs)


class AllUsersSliceInstance(SystemdCGroupInstance, AllUsersSlice):
    """
    An object that contains instaneous usage information related to a specific
    systemd user-$UID.slice.
    """

    def __init__(self, memsw=False):
        """
        Initializes the instaneous usage information of a systemd
        user-$UID.slice.

        uid: int
            A uid of the user-$UID.slice.
        """
        AllUsersSlice.__init__(self)
        super().__init__(self.name, self.parent, memsw=memsw)


class UserSlice(SystemdCGroup):
    """
    An object that contains methods/propreties related to a specific systemd
    user-$UID.slice.
    """

    def __init__(self, uid):
        """
        Initializes an object that contains methods/properties related to a
        systemd user-$UID.slice.

        uid: int
            A uid of the user-$UID.slice.
        """
        super().__init__("user-{}.slice".format(uid), "user.slice")
        self.uid = int(uid)

    def pids(self):
        """
        Returns a list of pids in the cgroup. If session@scope is turned on
        for user-$UID.slices, returns the pids in all sessions.
        """
        pids = super().pids()  # top level pids
        # if session@scope is turned on
        path = "{}/*.scope/cgroup.procs".format(self.assert_controller_path())
        for session in glob.iglob(path):
            with open(session) as proc_file:
                for pid in proc_file.readlines():
                    pids.append(pid.strip())
        return pids


class StaticUserSlice(StaticSystemdCGroup, UserSlice):
    """
    A single state of a specific systemd user-$UID.slice that contains human
    readable values.
    """

    def __init__(self, uid, **kwargs):
        """
        Initializes a static UserSlice.

        uid: int
            A uid of the user-$UID.slice.
        """
        UserSlice.__init__(self, uid)  # Initialize the uid
        # Prevent multiple values for keyword args
        kwargs.pop("name", None)
        kwargs.pop("parent", None)
        super().__init__(self.name, self.parent, **kwargs)

    def __add__(self, other):
        """
        Adds two UserSlice objects together by taking the values of the first
        and adding the usage and pids.
        """
        new = super().__add__(other)
        new.uid = self.uid
        return new

    def __sub__(self, other):
        """
        Subtracts two UserSlice objects together by taking the values of the
        first and subtracting the usage and adding the pids.
        """
        new = super().__sub__(other)
        new.uid = self.uid
        return new


class UserSliceInstance(SystemdCGroupInstance, UserSlice):
    """
    An object that contains instaneous usage information related to a specific
    systemd user-$UID.slice.
    """

    def __init__(self, uid, memsw=False):
        """
        Initializes the instaneous usage information of a systemd
        user-$UID.slice.

        uid: int
            A uid of the user-$UID.slice.
        """
        UserSlice.__init__(self, uid)
        super().__init__(self.name, self.parent)

    def __truediv__(self, other):
        """
        Averages two UserSliceInstances together into a human readable
        StaticUserSlice. The divisor instance should come later in time than
        the dividend (older / newer).
        """
        if isinstance(other, type(self)):
            return StaticUserSlice(
                self.uid,
                _pids=list(set(self._pids + other._pids)),
                usage=self._calc_usage(other)
            )
        return super().__truediv__(other)


def wait_till_uids(min_uid=0, blacklist=tuple()):
    """
    Blocks until users are on the machine and returns a list of uids that are
    active.
    """
    uids = []
    while not uids:
        time.sleep(0.5)
        all_uids_above_min = current_cgroup_uids(min_uid=min_uid)
        uids = list(filter(lambda u: u not in blacklist, all_uids_above_min))
    return uids


def current_cgroup_uids(min_uid=0):
    """
    Returns a list of str uids that are active in the user.slice cgroup.

    controller: str
        Which cgroup controller to pull from. (Assumes that accounting is
        enabled for the controller)

    >>> current_cgroup_uids()
    [1000, 1001, 1003]
    """
    uids = []
    # Loop through names and remove the "user-" and ".slice" part
    for name in current_cgroups():
        uid = re.findall(r"-(\d+)", name)
        uids.extend(list(map(int, uid)))
    return [uid for uid in uids if uid >= min_uid]


def current_cgroups(parent="user.slice", controller=default_controller):
    """
    Returns a list of all the current cgroup slices' paths that are active on
    the system. If a parent is specified, returns all cgroups below that parent
    cgroup. By default, all user cgroups are returned. This call will not
    throw FileNotFoundError, unlike others.

    parent: str
        The parent cgroup that contains the path of its parents.
    controller: str
        Which cgroup controller to pull from. (Assumes that accounting is
        enabled for the controller)

    >>> current_cgroups()
    ["user-1000.slice", "user-1010.slice", "user-robot7.slice"]
    >>> current_cgroups("user.slice/user-1010.slice")
    []
    """
    glob_str = "/sys/fs/cgroup/{}/{}/**/*.slice".format(controller, parent)
    return [os.path.basename(p) for p in glob.iglob(glob_str, recursive=True)]


def safe_check_on_any_uid(func, min_uid=0, retry_interval=0.1):
    """
    Returns whether the function (which takes a uid) is true for a uid on the
    machine in a safe and atomic way. Normally with active uids, there is a
    race condition pertaining to a user logging out during execution. This
    works around that. Note that this function waits indefinitely for users.

    func: function
        A function that takes in a uid.
    min_uid: int
        The minimum uid to check on.
    retry_interval: int
        How long to wait to try again if all the uids on the machine disappear
        during check.
    """
    while True:
        for uid in wait_till_uids(min_uid):
            try:
                return func(uid)
            except FileNotFoundError:
                pass
        time.sleep(retry_interval)
