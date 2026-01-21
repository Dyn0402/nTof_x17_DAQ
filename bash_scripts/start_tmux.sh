#!/bin/bash

name=$1
cmd=$2

# Check if tmux session already exists
if tmux has-session -t "$name" 2>/dev/null; then
    echo "❌ Tmux session '$name' already exists!"
    return 1
fi

if [ -z "$cmd" ]; then
    # Start an empty interactive tmux session
    tmux new-session -d -s "$name"
    echo "✅ Started empty tmux session: $name"
else
    # Start tmux session and run command
    tmux new-session -d -s "$name"
    tmux send-keys -t "$name" "$cmd" Enter
    echo "✅ Started $name running: $cmd"
fi