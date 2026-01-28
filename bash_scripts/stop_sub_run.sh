#!/bin/bash
SESSION="daq_control"

# Send Ctrl-C to the session
tmux send-keys -t "$SESSION" C-c

