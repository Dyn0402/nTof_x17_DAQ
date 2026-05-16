#!/bin/bash
SESSION="dream_daq"

BASE_DIR="$(dirname "$(dirname "$(realpath "$0")")")"
LOG_FILE="$BASE_DIR/logs/daq_events.log"
mkdir -p "$BASE_DIR/logs"
echo "$(date '+%Y-%m-%d %H:%M:%S') | STOP_DREAM     | bash_script  |" >> "$LOG_FILE"

# Send 'g' to Dream to stop it
tmux send-keys -t "$SESSION" 'g'
