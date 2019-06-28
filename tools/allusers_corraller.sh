#!/bin/bash
# See tools/user_corraller.sh for details. Simply a wrapper around that script
# that corrals all users on the machine.
#
# Written by Brian Haymore.
# Usage: ./allusers_corraller.sh

for i in `w -h |awk '{print $1}' |sort |uniq |grep -v root`; do
 ./user_corraller.sh $i
done
