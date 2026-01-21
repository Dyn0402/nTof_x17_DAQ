#!/bin/bash

# Restart servers in detached screen
screen -dmS restart_tmux bash -c '
  sleep 2
  /local/home/banco/dylan/Cosmic_Bench_DAQ_Control/start_servers.sh
'
# Kill tmux server
sessions=(
  daq_control
  dream_daq
  banco_tracker
  desync_monitor
  hv_control
  trigger_veto_control
  trigger_gen_control
  flask_server
)

for s in "${sessions[@]}"; do
  if tmux has-session -t "$s" 2>/dev/null; then
    tmux kill-session -t "$s"
  fi
done
