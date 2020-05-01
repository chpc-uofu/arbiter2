#!/bin/bash
set -e

# Make sure a directory exists and set its permissions
ARBBASEDIR=/tmp/arbiter2  # Update with your own location!
mkdir -p $ARBBASEDIR/tools/tmp
touch $ARBBASEDIR/tools/tmp/arbreport_process_history
chown -R arbiter $ARBBASEDIR/tools/tmp

# Change to arbiter and start running the reporting utility
sudo -u arbiter /usr/bin/python3 $ARBBASEDIR/tools/arbreport.py \
  -a $ARBBASEDIR/arbiter \
  -g $ARBBASEDIR/etc/config.toml \
  -e $ARBBASEDIR/etc \
  --sendemail \
  --loglocation $ARBBASEDIR/logs \
  --processhistory $ARBBASEDIR/tools/tmp/arbreport_process_history \
  2>&1 \
  > $ARBBASEDIR/tools/tmp/arbreport_output
