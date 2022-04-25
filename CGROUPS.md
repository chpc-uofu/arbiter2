## Linux Control Groups (cgroups)

### What are control groups?

Control groups, or "cgroups" for short, are a feature of the Linux kernel that allows for limiting, prioritization, control and accounting of a collection of processes in a hierarchical manner. The resources of these cgroups can then be manipulated by different controllers. This document will focus on just the CPU and memory controllers. See the Wikipedia page on [cgroups](https://en.wikipedia.org/wiki/Cgroups) for an overview. A deep dive is available by looking at the latest [man page](http://man7.org/linux/man-pages/man7/cgroups.7.html).

## cgroups v1 vs v2

There are two versions of cgroups. The first, called v1, was introduced in kernel version 2.6.24 (January 2008). Later in 2016, a newer version of cgroups was introduced called cgroups v2. On nearly all distributions as of writing (Summer of 2021), cgroups v1 is the default version mounted, though v2 can be mounted at the same time.

Arbiter2 uses cgroups v1 and this document mostly only pertains to cgroups v1.

### Differences

The primary difference between v1 and v2 is that whereas v1 had a multiple hierarchies for different controllers (allowing different groups of processes per controller), v2 has a unified hierarchy meant to be managed by a single authority:

```bash
$ ls /sys/fs/cgroup  # v1 mount on CentOS 8 Stream
blkio
cpu
cpuacct
cpu,cpuacct
cpuset
devices
freezer
hugetlb
memory
net_cls
net_cls,net_prio
net_prio
perf_event
pids
rdma
systemd

$ ls /sys/fs/cgroup/unified  # systemd’s v2 mount
cpu
cpuset
freezer
hugetlb
io
memory
perf_event
pids
rdma
systemd
```

It should be noted that some controllers have been redesigned (`cpu` now combines `cpuacct` and `cpu` in v2) and some controllers have been removed entirely (`net_cls` and `net_prio`).

## systemd

Although cgroups is a kernel feature independent of userspace software such as systemd, systemd has co-opted and integrated cgroups into it's own notion of units, services and slices, making it easy for administrators to control the resources of units supervised by systemd. Furthermore, with cgroups v2, the kernel also expects that a single process is responsible for managing and delegating parts of the cgroup hierarchy to other processes to prevent multiple processes from erroneously impacting the resources of each other due to the design of cgroups v1 and the global nature of resources. systemd is this daemon on systemd-based systems.

Of particular interest is systemd's `user-$UID.slice` unit, which represents a logged-in user. cgroups can be automatically created for `user-$UID.slice` units for each particular controller when accounting for that controller is enabled. With accounting enabled, a user slice (`user-$UID.slice`) is initialized when a user logs into the server. More specifically, systemd creates a cgroup when a user does not already have an existing session. A `user-$UID.slice` is removed when there are no active sessions by the user.

It is important to note that these user slices do not necessarily contain all processes owned by that user. This is because systemd only creates a slice and a session when a user logs in via a PAM authentication mechanism (e.g. SSH), and relies on the fact that spawned processes inherit their parent's cgroup to further maintain to cgroup hierachy. _Note: this means that `su`ing to the user does not result in your session process being in the `user-$OTHER_UID.slice` but rather your `user-$UID.slice`. In order to do so you must login in some manner that triggers PAM authentication._

By default, the controller accounting that is available through cgroups is disabled for user slices. As a consequence, Arbiter2 will not be able to see or limit resource usage. Prior to deploying or testing Arbiter2, then, administrators must enable cgroup accounting.

When accounting is enabled for one cgroup, it is also enabled for other cgroups on the same level and their parents. This is a kernel-based rule. e.g. enabling CPU accounting for `user-1000.slice` results in accounting turning on for all other users, as well as the parent `user.slice` and it's parent `/`. Accounting for systemd cgroups can be enabled with `systemctl set-property user-$UID.slice CPUAccounting=true MemoryAccounting=true` for a given `$UID` from a user who is currently logged into the server. In Arbiter2, this is generally the `arbiter` user: it is guaranteed to be present when the Arbiter2 service is running and will remain logged in.

After accounting is enabled, new user sessions should be contained in the new `user-$UID.slice`. Prior to this, however, the processes and user sessions will not be associated with user-level cgroups. Consequently, even when cgroup accounting has been enabled, some usage will not be counted by Arbiter2 and throttling may not affect all users' processes. During testing, for example, users who were logged on before accounting was enabled may have usage above the thresholds set on their user slices. Further, a process inherits its position in the cgroup hierarchy from its parents; new processes originating from processes or sessions outside of a `user-$UID.slice` are not visible to Arbiter2 and will not be counted against users. A script called `scripts/allusers_coraller.sh`can be used to move existing processes.

_Note: [systemd has a detailed document on cgroups at systemd.io](https://systemd.io/CGROUP_DELEGATION/). In particular, the document describes how container and cgroup managers should behave in systemd's world in [a quite opinionated manner](https://systemd.io/CGROUP_DELEGATION/#three-scenarios). Arbiter2 does not necessarily follow all the advice found in that document for three reasons. One, the document was written after Arbiter2 was written. Two, Arbiter2 cannot exist in the world described by systemd since Arbiter2 hijacks systemd's user-$UID.slices (which you're not supposed to do). Three, systemd insists on being the only process that can write to the cgroup hierarchy; this would require changing Arbiter2 to use D-Bus to communicate with systemd, adding a lot of unneeded complexity. Regardless, for now Arbiter2 appears to be able to co-exist with systemd quite well, despite not following it's advice._

### Limits

Limits on the utilization of a resource can be set in several ways. In Arbiter2, they are set by writing directly to files in the cgroup hierarchy. (This is generally not recommended, but it works and has a lower impact on the host than alternative methods.)

To remove the limits that Arbiter2 has set, one can either disable accounting for a slice entirely or write -1 to the corresponding control file. To disable accounting for a `user-$UID.slice`, one could do `systemctl set-property user-$UID.slice CPUAccounting=false MemoryAccounting=false` for a given `$UID`. Behind the scenes, this will remove the limits by essentially asking systemd via D-Bus to remove the cgroup. Alternatively, `-1` can be written to `memory.limit_in_bytes` and `cpu.cfs_quota_us`. The latter option naturally will not disable accounting.

There are two other ways that limits and quotas and limits can be applied to a systemd unit, whether it be a service such as `rsyslog.service` or a slice such as `user.slice` (representing all the logged in users on a machine). Both methods result in cgroup controller accounting being enabled if not already done.

1. Via the unit file. The resource controls defined are placed in the `[Slice]`, `[Scope]`, `[Service]`, etc sections of their respective unit files. For example if you have a service called `usertask.service` and wanted to limit the CPU time to 25% of a single core, your unit file would contain:

    ```ini
    [Service]
    CPUQuota=25%
    ```

    Unit files are located in the `/etc/systemd/` directory. e.g. `/etc/systemd/user/user-1000.slice.conf`. For settings that apply to generic units, such as all users (all `user-$UID.slice` units), a special drop-in unit file with the "id" removed in the name can be created. e.g. `user-.slice/CPUQuota.conf` would apply blindly to all users. It should be noted that these drop-in unit files are only supported in newer systemd versions not present on CentOS 7 machines.

2. Via runtime with `systemctl set-property <unit> [resource-control ...]`. e.g. `systemctl set-property usertask.service CPUQuota=25%`. On CentOS 7 machines, there is a significant bug when using this technique with `user-$UID.slice` cgroups that makes these settings only apply for only the period when there are more than 1 user logged in. i.e. once you end up in a state with all users logged out (including reboots), systemd will not remember any of your settings.

More details and the specific resource controls can be found in systemd's man page: `man 5 systemd.resource-control`. Although a nicer copies of these manuals can be found on the internet, be warned that the rapid development of systemd often results in serious discrepancies between the systemd version packed with your distro and the latest and greatest from systemd found online.

## Controller Details

All cgroup controllers contain at least two pertinent files: `cgroup.procs` and `tasks`. These files contain PIDs and TIDs of the tasks on that level of the cgroup hierarchy (i.e. sub-cgroup children are not included in the parent `cgroup.procs` file). In v1, tasks/threads can (confusingly) of a single process can belong to different cgroups, though in practice this is not recommended and not used. Thus the key file in cgroups is `cgroup.procs`, which contains a list of PIDs that presently exist in the cgroup. This list is not necessarily ordered and may even contain duplicates in the case of PID reuse:

```bash
$ systemctl set-property user-$UID.slice CPUAccounting=true
$ cat /sys/fs/cgroup/cpuacct/user.slice/cgroup.procs
1498
1515
1605
1645
2428
2448
2460
2914
2953
2173666
$ # The procs above belong to no user-$UID.slice sessions
```

### Memory

The memory controller has several important files within each cgroup directory:

- `memory.stat`
- `memory.{usage,limit}_in_bytes`
- `memory.{kmem,memsw}.{usage,limit}_in_bytes`

The `memory.stat` is the most important for accounting purposes. It contains statistics about the memory usage in bytes of the cgroup for different categories (e.g. shared memory, RSS, etc). It looks like the following. It has been annotated.

```bash
$ systemctl set-property user.slice MemoryAccounting=true
$ cat /sys/fs/cgroup/memory/user.slice/memory.stat
cache 0         # Page-cached memory, lies in the kernel, not alloc by user
rss 0           # Anonymous + Shared memory, not the same as
                # "RSS" found in PID stats since does not include
                # file-backed memory; includes huge tables
rss_huge 0      # Same as above, but only transparent huge page tables
shmem 0         # Shared memory
mapped_file 0   # File-backed memory
dirty 0         # In-memory memory that could be written to swap
writeback 0     # In-memory memory that is queued to be written to swap
swap 0          # On-disk memory in swap
pgpgin 0        # ~mostly irrelevant for userspace; kernel stat~
pgpgout 0       # ~mostly irrelevant for userspace; kernel stat~
pgfault 0       # ~not documented, no clue~
pgmajfault 0    # ~not documented, no clue~
inactive_anon 0 # Anonymous memory in in-active LRU list (likely to be swapped)
active_anon 0   # Anonymous memory not in in-active LRU list (unlikely
                # to be swapped)
inactive_file 0 # Same as above, but for file-backed memory
active_file 0   # Same as above, but for file-backed memory
unevictable 0   # Amount of memory locked by processes (e.g. RDMA pinned, mlock'd)
hierarchical_memory_limit 9223372036854771712
hierarchical_memsw_limit 9223372036854771712
total_cache 1749671936
total_rss 341549056
total_rss_huge 155189248
total_shmem 1351680
total_mapped_file 153145344
total_dirty 946176
total_writeback 135168
total_swap 540672
total_pgpgin 32555853
total_pgpgout 32088753
total_pgfault 62790420
total_pgmajfault 2409
total_inactive_anon 29487104
total_active_anon 309170176
total_inactive_file 1157238784
total_active_file 591577088
total_unevictable 0
```

`memory.{usage,limit}_in_bytes` are two files that report on the amount of memory considered by the kernel for limiting purposes and the actual limit, both in bytes. It is important to note that the usage is not usually what you want to look at. This is because the usage contains both kernel and page-cached memory, which processes have little control over. Interestingly enough, on systemd v239 `systemctl status` incorrectly (or on purpose?!) uses this file to report on the memory usage of a unit:

```bash
$ systemctl set-property user-$UID.slice MemoryAccounting=true
$ systemctl status user-$UID.slice
● user-1000.slice - User Slice of UID 1000
   Loaded: loaded
  Drop-In: /usr/lib/systemd/system/user-.slice.d
           └─10-defaults.conf
           /etc/systemd/system.control/user-1000.slice.d
           └─50-CPUAccounting.conf, 50-MemoryAccounting.conf
   Active: active since Tue 2021-01-05 10:09:48 MST; 5 months 30 days ago
    Tasks: 39 (limit: 48784)
   Memory: 1.5G
      CPU: 16h 7min 30.422s
$ head -c 536870912 /dev/random > random.bin
$ cat random.bin >/dev/null
$ systemctl status user-$UID.slice
● user-1000.slice - User Slice of UID 1000
   Loaded: loaded
  Drop-In: /usr/lib/systemd/system/user-.slice.d
           └─10-defaults.conf
           /etc/systemd/system.control/user-1000.slice.d
           └─50-CPUAccounting.conf, 50-MemoryAccounting.conf
   Active: active since Tue 2021-01-05 10:09:48 MST; 5 months 30 days ago
    Tasks: 39 (limit: 48784)
   Memory: 2G  <-- Somehow we allocated 0.5GiB?! Nope, it is cached.
      CPU: 16h 7min 30.422s
```

The `memory.{kmem,memsw}.{usage,limit}_in_bytes` files are similar. `kmem` reports on and limits kernel memory and `memsw` reports on memory plus swap usage. Note that on CentOS 7 `kmem` accounting and limiting is disabled, resulting in zeros for those files.

See https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v1/memory.html for more details.

### CPU

There are two main knobs in the `cpu` controller that allow you to prioritize and throttle the CPU usage of a cgroup: a "shares" mechanism that lets administrators guarantee an amount of CPU time for a cgroup when the system is busy, and a quota-based mechanism that limits the CPU time of a cgroup within a period.

See the [kernel docs for the shares-based approach](https://www.kernel.org/doc/Documentation/scheduler/sched-design-CFS.rst). I do not know enough about this mechanism to adequately explain it here.

For a quota-based approach there are two settings that control the limiting of CPU time: the period and the quota. The quota is a proportion of the period. These are controlled via the `cpu.cfs_quota_us` and `cpu.cfs_period_us` files, respectively. The units of these files is Hz. On most modern machines, the Hz period is 1000, so if you wanted to limit the CPU usage of a cgroup to 2 cores, you would write 2000 to `cpu.cfs_quota_us`. Similarly, if you wanted to limit the CPU usage of a cgroup to 10% of a CPU, you would write 100 to `cpu.cfs_quota_us`. One could in theory extend or decrease the `cpu.cfs_period_us` if you had a time-sensitive process.

### CPU Accounting

The `cpuacct` controller reports on the CPU time consumed by a cgroup in both kernel and user CPU time. The CPU time reported in the `cpuacct` controller uses nanoseconds as it's unit of time. Obtaining a CPU percentage from two CPU times is then simply `(new_ctime_ns - old_ctime_ns) / (new_clock_ns - old_clock_ns) * 100`.

See [this StackOverflow question for details](https://unix.stackexchange.com/questions/450748/calculating-cpu-usage-of-a-cgroup-over-a-period-of-time).

## Further readings

- http://man7.org/linux/man-pages/man7/cgroups.7.html
- https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v1/memory.html
- https://www.kernel.org/doc/Documentation/scheduler/sched-design-CFS.rst
- https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v1/cpuacct.html
- https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v1/cgroups.html
- https://access.redhat.com/documentation/en-us/red_hat_enterprise_linux/7/html/resource_management_guide/chap-introduction_to_control_groups
- https://utcc.utoronto.ca/~cks/space/blog/linux/SystemdCgroupsNotes
- https://utcc.utoronto.ca/~cks/space/blog/linux/SystemdFairshareScheduling
- https://engineering.squarespace.com/blog/2017/understanding-linux-container-scheduling?format=amp
- https://systemd.io/CGROUP_DELEGATION/
