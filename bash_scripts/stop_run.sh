#!/bin/bash
SESSION="daq_control"

# File to append to
LOGFILE="/local/home/banco/dylan/Cosmic_Bench_DAQ_Control/bash_scripts/stop_run_log.txt"

# Get date/time in ISO 8601 format
timestamp="$(date '+%Y-%m-%d %H:%M:%S')"

# Append to file
echo "$timestamp" >> "$LOGFILE"

# Send Ctrl-C to the session
tmux send-keys -t "$SESSION" C-c
sleep 1
tmux send-keys -t "$SESSION" C-c
