#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2019-2020 Center for High Performance Computing <helpdesk@chpc.utah.edu>
#
# SPDX-License-Identifier: GPL-2.0-only
#
# Prints out the amount of shared memory within a cgroup that is not
# proportionally counted against the cgroup due to other processes within
# other cgroups using parts of that memory without that being accounted in
# their cgroup (and subracted from the owner cgroup).
#
# Or more specifically,
# Prints out the difference between the total cgroup PSS (Proportional Set Size),
# which is mostly (Anon + File Backed) [RSS - Shared] + (Shared /
# Num-Shared-Consumer-Procs [sorta]), and the most accurate cgroup memory
# number calculated with memory.stat, which is total_rss + total_file_mapped,
# but actually that's Anon + File Backed + (Shared-But-Mappings-Owner-Of)
#
# In the context of Arbiter2, this is basically at least how inaccurate it's
# memory measurements are in the perfect scenario without collection race
# conditions.
#
# I may be missing something here... This stuff is non trivial to understand
# as a non-kernel developer. Also, this code is racey since pids and cgroups
# can disappear and mess things up.
#
# Must be run as root.
#
# Written by Dylan Gardner.
# Usage: ./cgroup_shmem_diff.sh
total_mem_kb=`cat /proc/meminfo | grep MemTotal | awk '{print $2}'`
cgmemdir=/sys/fs/cgroup/memory
users_only=true

find "$cgmemdir" -type d -print0 | while IFS= read -r -d '' cgpath; do
    if $users_only && [[ $cgpath != "$cgmemdir/user.slice/user-"* ]]; then
        continue
    fi
    cgroup="$(basename "$cgpath")"
    # Total PSS of cgroup from /proc/<pid>/smaps
    cgroup_pss_kb="$(cat "$cgpath/cgroup.procs" 2>/dev/null | xargs -L1 -I{} grep ^Pss /proc/{}/smaps | awk '{sum+=$2} END {print sum}')"
    # Total RSS + File Mapped (including shared memory) from memory.stat
    # (not memory.usage_in_bytes since that includes page cache memory)
    cgroup_stat_kb="$(grep 'total_rss \|total_file_mapped ' "$cgpath/memory.stat" 2>/dev/null | awk '{sum+=$2} END {print sum / 1024}')"
    # Difference between the two (shared memory mappings are only accounted for one cgroup, not proportionally)
    # Positive is undercounting, negative overcounting.
    cgroup_pss_stat_diff="$(echo "$cgroup_pss_kb" - "$cgroup_stat_kb" | bc)"
    cgroup_pss_stat_pct_diff="$(awk 'BEGIN {print '"$cgroup_pss_stat_diff"' / '"$total_mem_kb"' * 100}')"
    echo "$cgroup pss=${cgroup_pss_kb} KiB, mem=${cgroup_stat_kb} KiB, diff=${cgroup_pss_stat_diff} KiB (diff_total_mem_pct=${cgroup_pss_stat_pct_diff}%)"
done
