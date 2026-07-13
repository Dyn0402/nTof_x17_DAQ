#!/bin/bash
# Restart ONLY the Flask GUI server (tmux session `flask_server`). The DAQ, HV,
# gas/pressure/beam watchers, and every other tmux session keep running — this is
# the "GUI Reset" button, not a full DAQ restart (that's restart_daq_tmux_processes.sh).
#
# Runs detached via `screen` because Flask is serving the very request that
# triggered this: once we kill the flask_server session the serving process dies,
# so the kill+relaunch must live in a process that outlives it. The GUI is
# unreachable for ~3 s while it cycles; nothing else is touched.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

screen -dmS restart_flask bash -c '
  sleep 2
  tmux kill-session -t flask_server 2>/dev/null
  tmux new-session -d -s flask_server
  tmux set-option -t flask_server history-limit 5000 2>/dev/null
  tmux send-keys -t flask_server "cd '"$REPO_DIR"' && source .venv/bin/activate && flask_app/start_flask.sh" Enter
'
