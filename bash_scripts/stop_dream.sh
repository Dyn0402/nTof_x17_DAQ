#!/bin/bash
SESSION="dream_daq"

# Send 'g' to Dream to stop it
tmux send-keys -t "$SESSION" 'g'