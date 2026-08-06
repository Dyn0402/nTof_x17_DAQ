#!/bin/bash
# Stop the WHOLE run cleanly.
#
# Drop a .stop_run flag, then stop the DAQ. RunCtrl exits and the current
# sub-run ends; daq_control sees the flag, skips the rest of the sub-runs, and
# powers off HV via its normal shutdown — no orphaned RunCtrl and no Ctrl-C
# races. The cut-short sub-run is left unmarked so resume re-runs it.
#
# --full  ALSO shuts down the auto-switch machinery: stops beam_gate (so it releases
#         any .pause_run hold) and disarms mode_watcher. This is the OPERATOR stop —
#         "I am taking over, stay out of my way until I re-arm you."
#
# ⚠ WHY --full IS OPT-IN AND NOT THE DEFAULT
#   switch_mode.py:307 calls this script as part of every changeover, and mode_watcher
#   drives changeovers through `switch_mode.py --go`. If disarming lived in the default
#   path, the first automatic changeover would disarm the very watcher that ordered it
#   and automation would never run again. switch_mode stops/restarts beam_gate itself
#   (switch_mode.stop_beam_gate), so the changeover path needs none of this.
#
#   Without --full, a gate outliving its run re-asserts .pause_run over the clear that
#   daq_control does at run start, and parks the NEXT run at its first sub-run boundary
#   with no data and no obvious symptom — that cost an 11-minute pedestal run and the
#   cosmics run after it on 2026-08-06.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

FULL=0
[ "$1" = "--full" ] && FULL=1

LOG_FILE="$REPO_DIR/logs/daq_events.log"
mkdir -p "$REPO_DIR/logs"
echo "$(date '+%Y-%m-%d %H:%M:%S') | STOP_RUN       | bash_script  | full=$FULL" >> "$LOG_FILE"

touch "$REPO_DIR/.stop_run"

if [ "$FULL" -eq 1 ]; then
    PY="$REPO_DIR/.venv/bin/python"
    [ -x "$PY" ] || PY=python3

    # beam_gate releases its own hold on SIGTERM; --stop also clears a hold orphaned by a
    # gate that died without cleaning up. Foreground on purpose: the hold must be gone
    # before the operator can start anything else.
    "$PY" "$REPO_DIR/beam_gate.py" --stop >> "$REPO_DIR/logs/beam_gate.log" 2>&1
    echo "$(date '+%Y-%m-%d %H:%M:%S') | BEAM_GATE      | bash_script  | action=stop | via=stop_run --full" >> "$LOG_FILE"

    # Disarm rather than kill: mode_watcher keeps polling and logging (so the GUI card and
    # the log stay continuous) but takes no action until someone re-arms it. Nothing
    # re-arms implicitly — that is the point.
    touch "$REPO_DIR/config/.mode_watcher_disarmed"
    echo "$(date '+%Y-%m-%d %H:%M:%S') | MODE_WATCHER   | bash_script  | action=disarm | via=stop_run --full" >> "$LOG_FILE"
fi

"$SCRIPT_DIR/stop_dream.sh"
