#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2020 Idaho National Laboratory
#
# SPDX-License-Identifier: GPL-2.0-only
#
# The purpose of this script is to stop the arbiter service and remove any
# limitations that were set on users by the arbiter service.
#
# Written by Brandon Biggs at Idaho National Laboratory

# Verify that we are being run as 'root'.
if [[ $EUID -ne 0 ]]; then
  echo "$0 must be run as 'root'." > /dev/null
  exit 1
fi

echo "Stopping the arbiter2 service"
systemctl stop arbiter2

echo "Turning off CPU and Memory accounting for all users logged in."
for uid in $(users | tr ' ' $'\n' | uniq | xargs -L1 id -u); do
    if [ $uid -gt 999 ]; then
      systemctl set-property user-$uid.slice CPUAccounting=false MemoryAccounting=false
    fi
done
