#!/bin/bash
# Re-run a watcher script if it exits unexpectedly (an unhandled exception —
# e.g. backup_watcher racing space_watcher's HDD cleanup, 2026-07-29), so a
# bug that used to kill the process for hours until someone noticed now just
# costs one restart.
#
# An operator's own Ctrl-C in the tmux pane, or a `tmux kill-session`, must
# still stop it for good — NOT be fought by an auto-restart. The trap below
# marks a clean-stop request and forwards the signal to the child; the loop
# checks that flag before ever deciding to restart.
#
# Usage: supervise_watcher.sh <python> <script.py> <config.json>
set -u

PYTHON=$1
SCRIPT=$2
CONFIG=$3
name=$(basename "$SCRIPT")

stop_requested=0
child_pid=""

_on_stop() {
    stop_requested=1
    [ -n "$child_pid" ] && kill "$child_pid" 2>/dev/null
}
trap _on_stop SIGINT SIGTERM

backoff=5
while [ "$stop_requested" -eq 0 ]; do
    start_ts=$(date +%s)
    "$PYTHON" "$SCRIPT" "$CONFIG" &
    child_pid=$!
    wait "$child_pid"
    code=$?
    child_pid=""
    end_ts=$(date +%s)
    uptime=$((end_ts - start_ts))

    if [ "$stop_requested" -eq 1 ]; then
        echo "[supervisor] stop requested — not restarting $name (last exit $code, ran ${uptime}s)"
        break
    fi

    # A run that survived a while before dying is unrelated to whatever made
    # the last restart loop fast, so don't let backoff creep upward forever.
    if [ "$uptime" -ge 120 ]; then
        backoff=5
    fi
    echo "[supervisor] $name exited (code $code, ran ${uptime}s) — restarting in ${backoff}s"
    sleep "$backoff" &
    wait $!
    if [ "$backoff" -lt 60 ]; then
        backoff=$((backoff * 2))
    fi
done
