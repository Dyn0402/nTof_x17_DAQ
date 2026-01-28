#!/bin/bash
SESSION="daq_control"

# Send Ctrl-C to the session
tmux send-keys -t "$SESSION" C-c
sleep 1
tmux send-keys -t "$SESSION" C-c
