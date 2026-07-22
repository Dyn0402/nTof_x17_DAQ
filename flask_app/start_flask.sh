#!/bin/bash

# Activate the venv ourselves. This script runs inside a tmux session whose login shell
# re-reads the profile and drops whatever venv the caller had active, so a bare `flask`
# is "command not found" — it only worked at boot because start_servers.sh sources the
# venv before the tmux server is created, and every session inherits that. Restarting
# just this session by hand from an ordinary shell therefore left Flask DOWN while
# printing nothing but "flask: command not found" inside the pane (hit 2026-07-22).
# Also cd to the repo root: FLASK_APP below is a repo-relative path.
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
source .venv/bin/activate || exit 1

export FLASK_APP=flask_app/app.py
flask run --host=0.0.0.0 --port=5001