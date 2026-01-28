#!/bin/bash

# Restart servers in detached screen
screen -dmS restart_tmux bash -c '
  sleep 2
  /home/mx17/PycharmProjects/nTof_x17_DAQ/start_servers.sh
'
# Kill tmux server
tmux kill-server