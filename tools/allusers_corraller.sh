#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2019-2020 Center for High Performance Computing <helpdesk@chpc.utah.edu>
#
# SPDX-License-Identifier: GPL-2.0-only
# See tools/user_corraller.sh for details. Simply a wrapper around that script
# that corrals all users on the machine.
#
# Written by Brian Haymore
# Usage: ./allusers_corraller.sh

# Get all existing users on the machine (except root) and move their processes
# to the appropriate user cgroup.
for i in `w -h |awk '{print $1}' |sort |uniq |grep -v root`; do
 ./user_corraller.sh $i
done
