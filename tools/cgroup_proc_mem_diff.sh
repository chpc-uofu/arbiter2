#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2019-2020 Center for High Performance Computing <helpdesk@chpc.utah.edu>
#
# SPDX-License-Identifier: GPL-2.0-only
#
# Prints out the amount of memory within a cgroup calculated from memory.stat,
# memory.usage_in_bytes - memory.kmem.usage_in_bytes and total PSS memory.
#
# Arbiter2 can or has used one of these three techniques to account for a
# user's memory usage. If the set of pids a in cgroup is fixed and the
# amount of memory allocated doesn't change in between collection of all
# three methods, the following should be true:
# - PSS memory is technically the most accurate
# - PSS memory will be the most expensive to collect by a long shot
# - memory.stat is the most accurate cgroup-based accounting of memory
# - memory.stat is slightly more expensive than memory.usage_in_bytes - kmem
# - memory.usage_in_bytes minus kmem will undercount user memory if the user
#   is using a lot of non-file-backed cache kernel memory
# - If kmem is turned off, memory.kmem.usage_in_bytes will be zero and
#   kernel memory (specifically, file-backed page cache) will count against
#   the user
#
# This script aims to measure and show the difference in all of these
# measurements.
#
# Must be run as root.
#
# Written by Dylan Gardner.
# Usage: ./cgroup_proc_mem_diff.sh
total_mem_kb=`cat /proc/meminfo | grep MemTotal | awk '{print $2}'`
cgmemdir=/sys/fs/cgroup/memory
users_only=true

find "$cgmemdir" -type d -print0 | while IFS= read -r -d '' cgpath; do
    if $users_only && [[ $cgpath != "$cgmemdir/user.slice/user-"* ]]; then
        continue
    fi
    cgroup="$(basename "$cgpath")"
    # Total PSS of cgroup from /proc/<pid>/smaps
    cgroup_pss_kb="$(cat "$cgpath/cgroup.procs" 2>/dev/null | xargs -L1 -I{} grep "^Pss" /proc/{}/smaps 2>/dev/null | awk '{sum+=$2} END {print sum}')"
    # Total RSS + File Mapped (including shared memory) from memory.stat
    cgroup_stat_kb="$(grep 'total_rss \|total_file_mapped ' "$cgpath/memory.stat" 2>/dev/null | awk '{sum+=$2} END {print sum / 1024}')"
    # Total memory usage + file cached memory
    cgroup_usage_incl_cached_kb="$(awk '{print $1/1024}' "$cgpath/memory.usage_in_bytes")"
    # Total kernel memory (may be zero)
    cgroup_kmem_usage_kb="$(awk '{print $1/1024}' "$cgpath/memory.kmem.usage_in_bytes")"
    cgroup_usage_kb="$(echo "$cgroup_usage_incl_cached_kb" - "$cgroup_kmem_usage_kb" | bc)"
    echo "$cgroup pss=${cgroup_pss_kb} KiB, mem=${cgroup_stat_kb} KiB, usage=${cgroup_usage_kb} KiB, kmem=${cgroup_kmem_usage_kb} KiB, usage_incl_cache=${cgroup_usage_incl_cached_kb} KiB"
done
