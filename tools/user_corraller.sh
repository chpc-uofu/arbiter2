#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-only
# Corrals users' PIDs into their user-$UID.slice. This is useful when Arbiter2
# is first deployed, since users' PIDs are not moved to their slices when
# accounting is turned on. New sessions after turning on accounting, however,
# will be accounted for.
#
# Written by Brian Haymore.
# Usage: ./user_corraller.sh USERNMAE

#'die' function to report error condition of exit.
die() {
  echo >&2 "$@"
  exit 1
}

# Checks:

# Verify we have 1 and only 1 arguement.
[ "$#" -eq 1 ] || die "1 argument required, $# provided."

# Verify that $1 (provided argument) is a valid user.
getent passwd $1 > /dev/null 2>&1 || die "$1 is not a valid user."

# Verify that we are being run as 'root'.
if [[ $EUID -ne 0 ]]; then die "$0 must be run as 'root'."; fi

# Verify that the UID of $1 is greater than or equal to 1000.
ID=`id -ru $1`
if [[ ! $ID -ge 1000 ]]; then die "UID of $1 is not >= 1000."; fi

# Verify that there are processes owned by user $1 that we can act on.
ps -u $1 >/dev/null 2>&1 || die "$1 has no running processes for us to act on."

# This should be the default location for systemd based systems
CGDIR=/sys/fs/cgroup

# Now find all PIDs for user $1 and move them into 'cpuacct,cpu' and 'memory' cgroups for user's UID.
for pid in `ps axo user:32,pid |grep ^$1 |awk '{print $2}'`; do
  echo "Moving pid:$pid into 'cpu,cpuacct', 'memory' and 'systemd' cgroups for user $1:$ID"
  echo $pid >$CGDIR/cpu,cpuacct/user.slice/user-${ID}.slice/cgroup.procs
  echo $pid >$CGDIR/memory/user.slice/user-${ID}.slice/cgroup.procs
  echo $pid >$CGDIR/systemd/user.slice/user-${ID}.slice/cgroup.procs
  grep -e "memory" -e "cpuacct" -e "systemd" /proc/$pid/cgroup
  echo
done
