#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2019-2020 Center for High Performance Computing <helpdesk@chpc.utah.edu>
# SPDX-FileCopyrightText: Copyright (c) 2020 Idaho National Laboratory
#
# SPDX-License-Identifier: GPL-2.0-only

"""
A bunch of helper functions and values that retrieve/have "system"
information, taken broadly.
"""

import os
import pwd
import re
import socket
import time


def clockticks():
    """
    Returns the total cpu clock ticks of the system in jiffies.
    """
    with open("/proc/stat") as stat:
        stat_values = list(map(int, stat.readline()[5:].split(" ")))
        # Sum up the user kernel and guest time
        return sum(stat_values)


def threads_per_core():
    """
    Returns the number of threads per core. Includes hyperthreading as a
    separate thread from the CPU core.
    """
    get_values = [r"siblings.+:\s(\d+)", r"cpu\scores.+:\s(\d+)"]
    cpu_values = []
    for line in open("/proc/cpuinfo"):
        if len(cpu_values) >= len(get_values):
            break
        for match in re.finditer(get_values[len(cpu_values)], str(line)):
            cpu_values.append(int(match.group(1)))
    return int(cpu_values[0] / cpu_values[1])


def proc_meminfo(mproperty=None):
    """
    Returns a string containing /proc/meminfo. If mproperty is not None,
    /proc/meminfo returns the int value after the mproperty in /proc/meminfo
    (Typically in kB). Raises ValueError if /proc/meminfo doesn't contain the
    property.

    >>> proc_meminfo("MemTotal")
    8043084
    """
    with open("/proc/meminfo") as mem_file:
        memfile = mem_file.read()
    if not mproperty:
        return memfile
    matched = re.search(r"{}:\s+(\d+)".format(mproperty), memfile)
    if matched:
        return int(matched.groups()[0])

    raise ValueError("/proc/meminfo does not contain {}".format(mproperty))


def free_swap():
    """
    Returns the free swap size in bytes.
    """
    return proc_meminfo("SwapFree") * 1024


# Num of clock ticks per second
clockticks_per_sec = os.sysconf(2)

# Total Memory in bytes
total_mem = proc_meminfo("MemTotal") * 1024

# Total Swap size in bytes
total_swap = proc_meminfo("SwapTotal") * 1024

# Threads per core (Includes hyperthreading as a thread per core)
threads_per_core = threads_per_core()


def gb_to_bytes(memory_gb):
    """
    Returns the memory in bytes from GB.

    memory_bytes: float, int
        The memory to convert in gigabytes.
    """
    return memory_gb * 1024**3


def bytes_to_gb(memory_bytes):
    """
    Returns the memory in GB from bytes.

    memory_bytes: float, int
        The memory to convert in bytes.
    """
    return memory_bytes / 1024**3


def bytes_to_pct(memory_bytes):
    """
    Returns the memory in bytes to a percent of the machine.

    memory_bytes: float, int
        The memory to convert in bytes.
    """
    return memory_bytes / total_mem * 100


def pct_to_gb(memory_pct):
    """
    Given a percent of the machine, return how much memory in GB that is.

    memory_pct: float, int
        The memory (as a percentage of the machine e.g. 50) to convert.
    """
    return memory_pct / 100 * bytes_to_gb(total_mem)


def passwd_entry(uid):
    """
    Returns whether the user can be looked up via passwd.
    """
    try:
        getpwuid_cached(uid)
        return True
    except KeyError:
        return False


# Cache passwd records for quick lookup
passwd_cache = {}

def getpwuid_cached(uid):
    """
    A wrapper around pwd.getpwuid that caches values.

    Note: It is not safe to assume every uid has a passwd entry.
          pwd.getpwuid() will raise a KeyError in that case and thus,
          this function will too.
    """
    cache_timeout = 60 * 30  # 30m
    if uid in passwd_cache:
        ts, passwd = passwd_cache[uid]
        if time.time() - ts < cache_timeout:
            return passwd
        passwd_cache.pop(uid)

    passwd = pwd.getpwuid(uid)
    passwd_cache[uid] = time.time(), passwd
    return passwd


# Cache this lookup, we use it heavily and plus this ensures the hostname is
# safe against it changing from under us.
hostname = socket.gethostname()


def query_gids(uid):
    """
    Queries the gids of the groups that the user belongs to. If a user belongs
    to no groups, an empty list is returned.

    uid: int
        The user's uid.
    """
    try:
        passwd = getpwuid_cached(uid)
        username = str(passwd.pw_name)
        return os.getgrouplist(username, passwd.pw_gid)
    except KeyError:
        return []
