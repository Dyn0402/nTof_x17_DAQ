#!/bin/bash

# Restart servers in detached screen
screen -dmS restart_tmux bash -c '
  sleep 2
  /home/mx17/PycharmProjects/nTof_x17_DAQ/start_servers.sh
'
# Kill tmux server
sessions=(
  daq_control
  dream_daq
  hv_control
  flask_server
)

for s in "${sessions[@]}"; do
  if tmux has-session -t "$s" 2>/dev/null; then
    tmux kill-session -t "$s"
  fi
done
