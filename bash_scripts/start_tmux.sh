#!/bin/bash

name=$1
cmd=$2
# Scrollback cap for this session, in LINES. tmux's only history knob is a
# per-pane line count (no byte-size or time-based limit), so "cap by size"
# means capping lines. Each retained line costs memory, so keep chatty
# sessions short to avoid the OOM killer on this RAM-limited machine.
hist=${3:-5000}
# Optional fixed pane size: columns in $4, rows in $5 (both required together).
# Some DAQ programs draw a full-screen status display with fixed column
# positions (RunTime/IntRate etc.) that get clipped and overlap into garbage
# once the pane is narrower than they expect. tmux's default window-size
# ("latest") resizes the window to whatever client last attached, so an
# operator (or an automated ssh) attaching with an 80-col terminal silently
# corrupts that display for anyone reading it later, including Flask's status
# poller. When given, we pin the window to this size via `resize-window -x
# -y`, which also flips this window's window-size option to "manual" so it
# can no longer be shrunk by a future client attach.
cols=$4
rows=$5

# Check if tmux session already exists
if tmux has-session -t "$name" 2>/dev/null; then
    echo "❌ Tmux session '$name' already exists!"
    return 1
fi

# A pane captures history-limit at the moment it is created, so set the
# server-wide default right before creating this session's first pane. We also
# set it on the session itself so any later windows inherit the same cap.
tmux start-server 2>/dev/null
tmux set-option -g history-limit "$hist" 2>/dev/null

tmux new-session -d -s "$name"
tmux set-option -t "$name" history-limit "$hist" 2>/dev/null
if [ -n "$cols" ] && [ -n "$rows" ]; then
    tmux resize-window -t "$name" -x "$cols" -y "$rows"
    size_note=", pinned ${cols}x${rows}"
fi

if [ -z "$cmd" ]; then
    # Start an empty interactive tmux session
    echo "✅ Started empty tmux session: $name (scrollback ${hist} lines${size_note})"
else
    # Start tmux session and run command
    tmux send-keys -t "$name" "$cmd" Enter
    echo "✅ Started $name running: $cmd (scrollback ${hist} lines${size_note})"
fi