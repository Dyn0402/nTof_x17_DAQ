#!/bin/bash
SESSION="daq_control"
CONFIG_PATH="$1"

if [ -z "$CONFIG_PATH" ]; then
  echo "Usage: $0 <config_path>"
  exit 1
fi


# Run numbers are NOT iterated here. This script is a dumb launcher: it runs exactly the
# config JSON it is given. Allocation lives in run_num.py — the GUI calls it via
# /run/prepare, switch_mode --go calls run_num.allocate(). (iterate_run_num.py, which used
# to be invoked here, is retired: it rewrote run_config_beam.py source and only worked for
# the base config.)

COMMAND="python daq_control.py \"$CONFIG_PATH\""

# Send command to the tmux session
tmux send-keys -t "$SESSION" "$COMMAND" C-m
