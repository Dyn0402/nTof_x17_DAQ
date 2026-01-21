#!/bin/bash

# Restart servers in detached screen
screen -dmS restart_tmux bash -c '
  sleep 2
  /local/home/banco/dylan/Cosmic_Bench_DAQ_Control/start_servers.sh
'
# Kill tmux server
tmux kill-server

#tmux list-sessions -F "#{session_name}" 2>/dev/null | grep -vE '^(decoder_|processor_|analyzer_)' | xargs -r -n1 tmux kill-session -t

## Patterns of tmux session names to KEEP
#KEEP_PATTERNS=("decoder_" "processor_" "analyzer_")
#
## Get all tmux sessions
#sessions=$(tmux list-sessions -F "#{session_name}" 2>/dev/null)
#
## Restart servers in detached screen
#screen -dmS restart_tmux bash -c '
#  sleep 20
#  /local/home/banco/dylan/Cosmic_Bench_DAQ_Control/start_servers.sh
#'
#
## Determine which to kill
#for s in $sessions; do
#  keep=false
#  for pat in "${KEEP_PATTERNS[@]}"; do
#    if [[ $s == $pat* ]]; then
#      keep=true
#      break
#    fi
#  done
#  if ! $keep; then
#    echo "Killing tmux session: $s"
#    tmux kill-session -t "$s"
#  fi
#done


## Start a detached screen that will handle the restart
#screen -dmS restart_tmux bash -c '
#  sleep 2
#  /local/home/banco/dylan/Cosmic_Bench_DAQ_Control/start_servers.sh
#'
#
## Kill tmux server
#tmux kill-server
