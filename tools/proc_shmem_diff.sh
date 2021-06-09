#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2019-2020 Center for High Performance Computing <helpdesk@chpc.utah.edu>
#
# SPDX-License-Identifier: GPL-2.0-only
#
# Prints out the amount of shared memory reported in /proc/<pid>/status,
# /proc/<pid>/statm and /proc/<pid>/smaps.
#
# Must be run as root.
#
# Written by Dylan Gardner.
# Usage: ./proc_shmem_diff.sh
for pid in $(ps axo pid); do
    # Total shared memory of pid from /proc/<pid>/status, differentiate
    # between pure shared memory (via mmap(NULL, ..., MAP_SHARED, ...)) and
    # file-backed memory which is also shared between those using the file
    # (via mmap(filepath, ...).
    proc_shmem_status_bytes="$(awk '/RssShmem/ { total+= $2 } END { print total * 1024 }' /proc/$pid/status 2>/dev/null)"
    proc_file_status_bytes="$(awk '/RssFile/ { total+= $2 } END { print total * 1024 }' /proc/$pid/status 2>/dev/null)"

    # Total RSS shared memory of pid from /proc/<pid>/statm, includes
    # file-backed memory. Should equal RssShmem + RssFile in status
    #
    # Note that to make "accounting scalable, RSS related information are
    # handled in an asynchronous manner and the value may not be very precise"
    # https://www.kernel.org/doc/Documentation/filesystems/proc.txt
    #
    # This often makes the shared memory reported appear less than what is
    # reported in smaps.
    proc_shmem_statm_bytes="$(awk '{print $3 * 4096}' /proc/$pid/statm 2>/dev/null)"

    # Total shared memory of pid from /proc/<pid>/smaps. This may be less than
    # the number reported in statm if there is file-backed memory that is not
    # shared (in the sense of more than one consumer) between processes.
    # Instead the kernel marks it in the Private field of smaps.
    #
    # Also includes hugetable memory, but unclear whether statm or status
    # also counts this.
    #
    # See https://www.kernel.org/doc/html/latest/filesystems/proc.html
    proc_shmem_smaps_bytes="$(awk '/Shared/ {sum+=$2} END {print sum * 1024}' /proc/$pid/smaps 2>/dev/null)"

    echo "$pid shared_status=${proc_shmem_status_bytes}, file_status=${proc_file_status_bytes} shared_statm=${proc_shmem_statm_bytes} bytes, shared_smaps=${proc_shmem_smaps_bytes} bytes"
done
