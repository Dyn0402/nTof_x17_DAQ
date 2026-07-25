#!/bin/bash
# Restart ONLY the Flask GUI server (tmux session `flask_server`). The DAQ, HV,
# gas/pressure/beam watchers, and every other tmux session keep running — this is
# the "GUI Reset" button, not a full DAQ restart (that's restart_daq_tmux_processes.sh).
#
# Runs detached via `screen` because Flask is serving the very request that
# triggered this: once we kill the flask_server session the serving process dies,
# so the kill+relaunch must live in a process that outlives it. The GUI is
# unreachable for ~3 s while it cycles; nothing else is touched.
#
# The session is recreated through start_tmux.sh with the SAME command and
# scrollback cap start_servers.sh uses, so a button-restarted GUI is
# indistinguishable from one started at boot. It used to hand-roll
# `tmux new-session` + `source .venv/bin/activate`, which set the history limit
# only AFTER the pane had already captured the server-wide default, and diverged
# from the boot path for no reason — start_flask.sh runs the venv interpreter
# itself.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

screen -dmS restart_flask bash -c '
  sleep 2
  tmux kill-session -t flask_server 2>/dev/null
  cd "'"$REPO_DIR"'" || exit 1
  bash_scripts/start_tmux.sh flask_server "flask_app/start_flask.sh" 5000
'
