#!/bin/bash
SESSION="daq_control"

BASE_DIR="$(dirname "$(dirname "$(realpath "$0")")")"
LOG_FILE="$BASE_DIR/logs/daq_events.log"
mkdir -p "$BASE_DIR/logs"
echo "$(date '+%Y-%m-%d %H:%M:%S') | STOP_SUB_RUN   | bash_script  |" >> "$LOG_FILE"

# Send Ctrl-C to the session
tmux send-keys -t "$SESSION" C-c
