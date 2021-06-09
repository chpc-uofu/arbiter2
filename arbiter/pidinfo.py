# SPDX-FileCopyrightText: Copyright (c) 2019-2020 Center for High Performance Computing <helpdesk@chpc.utah.edu>
# SPDX-FileCopyrightText: Copyright (c) 2020 Idaho National Laboratory
#
# SPDX-License-Identifier: GPL-2.0-only

import collections
import errno
import math
import os
import re

import sysinfo
import usage

"""
A simple module containing classes and methods for managing and storing
information related to a Linux Process. The classes follow the usage module
philosophy of Static, non-Static and Instance. See usage.py for details.
"""

# smaps_rollup is a aggregate file of smaps, so it's faster to read. It was
# added in the 4.14 kernel, so CentOS/RHEL 7 can't take advantage of this, but
# CentOS/RHEL 8 and newer Ubuntus can.
# See https://github.com/torvalds/linux/commit/493b0e9d945fa9dfe96be93ae41b4ca4b6fdb317 for more details
smaps_rollup_exists = os.path.exists("/proc/1/smaps_rollup")

# Constants for getting process information
pss_swap_re = rb"Pss:\s+(\d+)\skB"
pss_no_swap_re = rb"\nPss:\s+(\d+)\skB"
pss_pattern_no_swap = re.compile(pss_no_swap_re)
pss_pattern_swap = re.compile(pss_swap_re)

class Process():
    """
    An object that contains methods/properties related to a process.
    """

    def __init__(self, pid):
        """
        Initializes an object that contains methods/properties related to a
        process.

        pid: int
            A process id.
        """
        self.pid = pid

    def active(self):
        """
        Returns whether the process is active.
        """
        try:
            os.kill(self.pid, 0)  # doesn't actually kill, asks proc for info
        except OSError as error:
            # check the errors
            if error.errno == errno.ESRCH:
                return False
            # if EPERM, means access denied, meaning it exists
            elif error.errno == errno.EPERM:
                return True
            # it's a different error and doesn't exist, see "man 2 kill"
            else:
                raise
        else:
            return True

    def proc_status(self, key):
        """
        Returns the value in /proc/self.pid/status by key without a newline
        character. If the key doesn't exist, returns an empty string. May
        throw a FileNotFoundError if the process disappears.
        """
        with open("/proc/{}/status".format(self.pid)) as proc_status:
            lines = proc_status.read()
            match = re.search(key + r":\s+(.*)", lines)
            if match:
                try:
                    return match.group(1).strip()
                except AttributeError:
                    pass
        return ""

    def proc_stat(self, *indexes):
        """
        Returns the value(s) at the indexes (nonzoro-based!) in
        /proc/self.pid/stat. If the index is out of bounds, a IndexError is
        raised. May also throw a FileNotFoundError if the process disappears.
        See "man 5 proc" for details.
        """
        with open("/proc/{}/stat".format(self.pid)) as stat:
            values = stat.readline().split(" ")
            return [values[i - 1] for i in indexes]

    def curr_name(self):
        """
        Returns the name of the pid. If the pid doesn't exist, returns None.

        >>> self.get_name()
        "(bash)"
        """
        return self.proc_status("Name")

    def curr_owner(self, effective_uid=True):
        """
        Returns the uid of the owner of the pid. If effective_uid is not True,
        the noneffective_uid owner is returned.

        effective_uid: bool
            Whether or not to return the effective uid.
        """
        index = 2 if effective_uid else 1
        try:
            return int(self.proc_status("Uid").split("\t")[index])
        except (OSError, IndexError):
            return -1

    def curr_uptime(self):
        """
        Returns the uptime of the process in seconds.
        """
        # Get uptime of machine in jiffies
        with open("/proc/uptime") as proc_uptime:
            uptime = float(proc_uptime.readline().split(" ")[0])

        start_time = float(self.proc_stat(22)[0])  # Since boot in clock ticks
        start_time /= sysinfo.clockticks_per_sec  # Divide to get jiffies
        return uptime - start_time

    def curr_memory_bytes(self, pss=False, swap=True):
        """
        Returns the current memory usage in bytes.

        pss: bool
            Sets whether to collect pss from /proc/<pid>/smaps. This requires
            special capabilities to do so (e.g. through CAP_SYS_PTRACE or root
            privileges) and a error will be raised if there is not sufficient
            permissions.
        swap: bool
            Whether to include swapped memory in the usage reported.
        """
        if pss:
            return self._pss_mem_usage(swap=swap)
        return self._rss_mem_usage(swap=swap)

    def _rss_mem_usage(self, swap=True):
        """
        Returns the current memory usage (rss) in bytes.

        swap: bool
            Whether to include swapped memory in the usage reported.
        """
        # Get vmRSS (virtual mem resident set size)
        raw_rss = self.proc_status("VmRSS").rstrip(" kB")
        rss = int(raw_rss) * 1024 if raw_rss else 0.0
        raw_rss_swap = self.proc_status("VmSwap").rstrip(" kB")
        rss_swap = int(raw_rss_swap) * 1024 if swap and raw_rss_swap else 0.0
        return rss + rss_swap

    def _pss_mem_usage(self, swap=True):
        """
        Returns the current pss (proportional shared size) memory usage in
        bytes. Using pss reads /proc/<pid>/smaps or /proc/<pid>/smaps_rollup,
        which requires CAP_SYS_PTRACE capabilities. A error will be raised if
        there is not sufficient permissions.

        swap: bool
            Whether to include swapped memory in the usage reported.
        """
        smaps_file = "smaps_rollup" if smaps_rollup_exists else "smaps"
        pss_pattern = pss_pattern_swap if swap else pss_pattern_no_swap

        # We read as bytes to avoid the overhead of converting to a string
        with open("/proc/{}/{}".format(self.pid, smaps_file), 'rb') as smaps:
            return sum(
                int(match.group(1)) if match else 0
                for match in pss_pattern.finditer(smaps.read())
            ) * 1024  # smaps returns kB

    def curr_shared_memory_bytes(self):
        """
        Returns the current shared memory usage in bytes. File-backed memory
        (which is implicitly shared) is not included here.

        Note: File-backed is created via mmap(filename, ...), whereas pure
              shared memory reported here is mmap(NULL, ..., MAP_SHARED, ...).
              Both can be obtained in one go via the shared field in
              /proc/<pid>/statm or by adding RssShmem and RssFile in
              /proc/<pid>/status (+ curr_file_memory_bytes()).
        """
        # Use /proc/<pid>/status here since the ProcessInstance subclass
        # caches this file and thus we don't have to open another file.
        raw_rss_shmem = self.proc_status("RssShmem").rstrip(" kB")
        return int(raw_rss_shmem) * 1024 if raw_rss_shmem else 0.0

    def curr_file_memory_bytes(self):
        """
        Returns the current file-backed memory usage in bytes.
        """
        # Use /proc/<pid>/status here since the ProcessInstance subclass
        # caches this file and thus we don't have to open another file.
        raw_rss_file = self.proc_status("RssFile").rstrip(" kB")
        return int(raw_rss_file) * 1024 if raw_rss_file else 0.0

    def curr_cputime(self):
        """
        Returns the time the process has been scheduled in kernel and user
        mode measured in clock ticks.
        """
        # 14 - utime (user time)
        # 15 - stime (kernel time)
        return sum(map(int, self.proc_stat(14, 15)))


class StaticProcess(usage.Usage, Process):
    """
    A single state of a process that contains human readable values.
    """

    def __init__(self, pid, **kwargs):
        """
        Initializes a static Process.
        """
        Process.__init__(self, pid)
        super().__init__()
        self.name = "unknown"
        self.uptime = -1
        self.owner = -1
        self.count = 1
        for key, value in kwargs.items():
            setattr(self, key, value)

    def __repr__(self):
        return "<{} {}: {}>".format(type(self).__name__, self.pid, self.name)

    def __str__(self):
        return "{} ({})".format(self.name, self.pid)

    def debug_str(self):
        return "{} ({}) [ruid={},uptime={:.1f}s]: cpu {:.3f}%, mem {:.3f}%".format(
            self.name,
            self.pid,
            self.owner,
            self.uptime,
            self.usage["cpu"],
            self.usage["mem"]
        )

    def __hash__(self):
        return hash(self.name)

    def __eq__(self, other):
        """
        Returns whether the processes are the equal by looking at the names.
        """
        if isinstance(self, type(self)):
            return self.name == other.name
        return super().__eq__(other)

    def __add__(self, other):
        """
        Adds two StaticProcess objects together by taking the values of the
        first, taking the max of the uptime, adding the count and adding the
        usage.
        """
        if isinstance(other, type(self)):
            new = super().__add__(other)
            new.uptime = max(self.uptime, other.uptime)
            new.count = self.count + other.count
            return new
        return super().__add__(other)

    def __sub__(self, other):
        """
        Subtracts two StaticProcess objects together by taking the values of
        the first, taking the max of the uptime, subtracting the count and
        subtracting the usage.
        """
        if isinstance(other, type(self)):
            new = super().__sub__(other)
            new.uptime = max(self.uptime, other.uptime)
            new.count = self.count - other.count
            return new
        return super().__sub__(other)

    def __truediv__(self, other):
        """
        Divides two StaticProcesses usage and count by the given number.
        """
        if isinstance(other, (int, float, complex)):
            new = super().__truediv__(other)
            new.count = math.ceil(self.count / other)
            return new
        return super().__truediv__(other)

    def __floordiv__(self, other):
        """
        Floor divides two StaticProcesses usage and count by the given number.
        """
        if isinstance(other, (int, float, complex)):
            new = super().__floordiv__(other)
            new.count = self.count // other
            return new
        return super().__floordiv__(other)


class ProcessInstance(Process):
    """
    An object that contains instantaneous usage information related to a
    process.
    """

    def __init__(self, pid, pss=False, swap=True, clockticks=None,
                 selective_pss_threshold=0.0):
        """
        Initializes the instantaneous usage information of a process.
        """
        super().__init__(pid)
        # Cache these file so that subsequent calls to different parts of the
        # same file are returned without reading again
        self.proc_status_cache = None
        self.proc_stat_cache = None

        self.name = self.curr_name()
        self.uptime = self.curr_uptime()
        self.owner = self.curr_owner()

        if selective_pss_threshold > 0:
            # Shared mem collection cost is mostly zero cost due to caching
            # of /proc/pid/status
            pure_shared_memory_bytes = self.curr_shared_memory_bytes()
            file_backed_memory_bytes = self.curr_file_memory_bytes()
            total_shared_memory_bytes = pure_shared_memory_bytes + file_backed_memory_bytes
            if total_shared_memory_bytes < selective_pss_threshold:
                pss = False

        self.memory_bytes = self.curr_memory_bytes(pss=pss, swap=swap)
        self.cputime = self.curr_cputime()

        # We may get provided clockticks as an optimization
        if not clockticks:
            self.clockticks = sysinfo.clockticks()
        else:
            self.clockticks = clockticks

    def proc_status(self, key):
        """
        Returns the value in /proc/self.pid/status by key without a newline
        character. If the key doesn't exist, returns an empty string. May
        throw a FileNotFoundError if the process disappears.
        """
        if not self.proc_status_cache:
            with open("/proc/{}/status".format(self.pid)) as proc_status:
                self.proc_status_cache = proc_status.read()

        lines = self.proc_status_cache
        match = re.search(key + r":\s+(.*)", lines)
        if match:
            try:
                return match.group(1).strip()
            except AttributeError:
                pass
        return ""

    def proc_stat(self, *indexes):
        """
        Returns the value(s) at the indexes (nonzoro-based!) in
        /proc/self.pid/stat. If the index is out of bounds, a IndexError is
        raised. May also throw a FileNotFoundError if the process disappears.
        See "man 5 proc" for details.
        """
        if not self.proc_stat_cache:
            with open("/proc/{}/stat".format(self.pid)) as stat:
                self.proc_stat_cache = stat.readline().split(" ")

        return [self.proc_stat_cache[i - 1] for i in indexes]

    def __add__(self, other):
        raise TypeError(
            "Cannot add to a instantaneous {} object.".format(type(self).__name__)
        )

    def __sub__(self, other):
        raise TypeError(
            "Cannot subtract to a instantaneous {} object.".format(
                type(self).__name__
            )
        )

    def __truediv__(self, other):
        """
        Averages two ProcessInstances together into a human readable
        StaticProcess. The divisor instance should come later in time than
        the dividend (older / newer).
        """
        if isinstance(other, type(self)):
            # If a pid is reassigned between collection of instances, the
            # instances will have drastically different cpu and memory data,
            # leading to erroneous results. To get around this, we zero out
            # usage if the cputime (which is cumulative) of the first is
            # greater than the second, as well as if the process names are not
            # the same.
            if self.cputime > other.cputime or self.name != other.name:
                calc_usage = usage.metrics.copy()  # Zero out the usage
            else:
                calc_usage = {
                    "cpu": max(
                        max(other.cputime - self.cputime, 0) /
                        max(abs(other.clockticks - self.clockticks), 1) *
                        os.cpu_count(), 0) * 100,
                    "mem": (
                        (other.memory_bytes + self.memory_bytes) / 2
                        / sysinfo.total_mem
                    ) * 100
                }
            return StaticProcess(
                pid=self.pid,
                name=self.name,
                uptime=max(self.uptime, other.uptime),
                owner=self.owner,
                usage=calc_usage
            )
        return super().__truediv__(other)


def combo_procs_by_name(procs):
    """
    Combines the given processes together into new StaticProcess()s if they
    have the same name. Returns a iterable of processes.

    procs: iter
        A iterable of static processes.
    """
    new_procs = collections.defaultdict(lambda: 0)
    for proc in procs:
        new_procs[proc.name] += proc
    return list(new_procs.values())
