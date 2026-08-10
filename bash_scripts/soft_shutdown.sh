#!/bin/bash
# Soft shutdown — bring the DAQ down cleanly so the host can be rebooted.
#
# This does NOT reboot. It stops everything in a safe order and reports when it is
# safe for a human to reboot. Driven from the GUI (Flask /soft_shutdown) or by hand.
#
# WHY THIS EXISTS
# ---------------
# A plain `reboot` is already *mostly* clean: systemd SIGTERMs everything, and the TT
# supervisor's handler does close its stream and restore .244 to counters (verified on
# 2026-07-30: stats.json showed "finished": "signal", "restored": true). Two things make
# it a dice roll rather than a guarantee, and this script removes both:
#
#   1. DefaultTimeoutStopSec is 90 s, and the TT graceful stop needs 30-150 s (a segment
#      notices the stop within ~10 s, then stop_tt_data + the verified restore take
#      ~30-70 s). Overrun that and systemd SIGKILLs mid-session — which is precisely the
#      DIRTY DISCONNECT that wedges these boards for hours (n1081b/CLAUDE.md rule 4).
#      On 07-30 it finished in ~28 s; at a segment boundary or on a loaded host it may
#      not. Here it gets as long as it needs.
#   2. mode_watcher does automatic beam<->cosmics changeover and will happily START A
#      NEW RUN when it sees the current one end. It must be stopped FIRST, or stopping
#      the run races a fresh one into existence seconds before the reboot.
#
# WHAT IT DELIBERATELY DOES *NOT* DO
# ----------------------------------
# * It does NOT touch HV, and it does NOT zero the gas. Both should stay exactly as they
#   are across a reboot — ramping HV down and back up costs hours of conditioning, and a
#   reboot is not a detector-safety event. If you want those off, that is the Shift
#   Overview EMERGENCY STOP (/shift/emergency_stop), which is a different intent.
# * It does NOT stop flask_server: it is what is serving the request and reporting
#   progress, it owns no hardware, and the reboot takes it down anyway.
# * It does NOT reboot the host. Reboot yourself once this reports SAFE.
#
# ON THE WAY BACK UP: a reboot used to fake a .244 wedge (autostart raced the DREAM
# link -> OSError(113) -> "wedge" -> 6 h quarantine -> permanent stop). That is fixed by
# the reachability gate in tt_stream_supervisor.py. See
# n1081b/HANDOFF_2026-07-30_tt_reboot_race.md.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_DIR" || exit 1

STATE_FILE="$REPO_DIR/config/soft_shutdown_state.json"
LOG_FILE="$REPO_DIR/logs/soft_shutdown.log"
EVENT_LOG="$REPO_DIR/logs/daq_events.log"
TT_STOP_FILE="$REPO_DIR/config/tt_stream_supervisor.stop"

# Grace budgets (seconds).
RUN_GRACE=150       # RunCtrl to exit after stop_run.sh
TT_GRACE=240        # TT supervisor to close the stream AND verify the .244 restore
BACKUP_GRACE=90     # backup_watcher to finish an in-flight xrdcp
GENERIC_GRACE=15    # stateless poll-loop watchers

mkdir -p "$REPO_DIR/logs" "$REPO_DIR/config"

STEP_JSON=""
PHASE="starting"
SAFE="false"
# Published with the pid: this state file outlives the reboot it exists to enable,
# and after a reboot some unrelated process will eventually hold that pid. The GUI
# only trusts the pid on the boot that recorded it (flask_app/app.py).
BOOT_ID="$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || echo unknown)"

_now () { date '+%Y-%m-%d %H:%M:%S'; }

log () {
    echo "$(_now) | $*" | tee -a "$LOG_FILE"
}

# Publish progress so the GUI can show it while we work (the button returns at once).
publish () {
    PHASE="$1"
    cat > "$STATE_FILE.tmp" <<EOF
{
  "phase": "$PHASE",
  "safe_to_reboot": $SAFE,
  "updated": "$(_now)",
  "updated_epoch": $(date +%s),
  "pid": $$,
  "boot_id": "$BOOT_ID",
  "steps": [$STEP_JSON]
}
EOF
    mv -f "$STATE_FILE.tmp" "$STATE_FILE"
}

step () {  # step <name> <ok|fail|skip> <detail>
    local sep=""
    [ -n "$STEP_JSON" ] && sep=","
    local detail=${3//\"/\'}
    STEP_JSON="$STEP_JSON$sep{\"name\":\"$1\",\"status\":\"$2\",\"detail\":\"$detail\"}"
    log "[$2] $1 — $3"
    publish "$PHASE"
}

# Wait for a pgrep pattern to have NO matches. Returns 0 if it went away in time.
wait_gone () {  # wait_gone <pgrep-pattern> <seconds> [-x]
    local pat=$1 budget=$2 exact=${3:-}
    local deadline=$(( $(date +%s) + budget ))
    while [ "$(date +%s)" -lt "$deadline" ]; do
        if [ -n "$exact" ]; then
            pgrep -x "$pat" >/dev/null 2>&1 || return 0
        else
            pgrep -f "$pat" >/dev/null 2>&1 || return 0
        fi
        sleep 2
    done
    return 1
}

# Stop a tmux session's process politely (SIGTERM), wait, then reap the session.
# Never SIGKILL: several of these hold serial/GPIB links, and for anything touching an
# N1081B a SIGKILL is the dirty disconnect that wedges boards.
stop_session () {  # stop_session <session> <pattern> <grace> <label>
    local session=$1 pattern=$2 grace=$3 label=$4
    if ! tmux has-session -t "$session" 2>/dev/null; then
        step "$label" skip "no tmux session '$session'"
        return 0
    fi
    pkill -TERM -f "$pattern" 2>/dev/null
    if wait_gone "$pattern" "$grace"; then
        tmux kill-session -t "$session" 2>/dev/null
        step "$label" ok "SIGTERM honoured, session reaped"
    else
        tmux kill-session -t "$session" 2>/dev/null
        step "$label" fail "still alive after ${grace}s SIGTERM; session reaped anyway"
    fi
}

echo "$(_now) | SOFT_SHUTDOWN  | bash_script  |" >> "$EVENT_LOG"
: > "$LOG_FILE"
log "=== soft shutdown starting (pid $$) ==="
publish "starting"

# ---------------------------------------------------------------------------
# 1. mode_watcher FIRST — it would otherwise start a new run when this one ends.
# ---------------------------------------------------------------------------
publish "stopping mode_watcher"
stop_session mode_watcher "mode_watcher.py" "$GENERIC_GRACE" "mode_watcher (auto-changeover)"

# Also disarm the scan watcher if it happens to be up: it talks to boards on its own
# schedule and we are about to stop caring about board state.
stop_session n1081b_scan_watcher "n1081b_scan_watcher.py" "$GENERIC_GRACE" "n1081b_scan_watcher"

# ---------------------------------------------------------------------------
# 2. The run. stop_run.sh drops .stop_run then walks RunCtrl out phase by phase, so
#    daq_control ends the sub-run and shuts down through its normal path.
# ---------------------------------------------------------------------------
publish "stopping run"
if pgrep -x RunCtrl >/dev/null 2>&1; then
    "$SCRIPT_DIR/stop_run.sh" >>"$LOG_FILE" 2>&1
    if wait_gone RunCtrl "$RUN_GRACE" -x; then
        step "run" ok "RunCtrl exited; sub-run closed through daq_control"
    else
        step "run" fail "RunCtrl still alive after ${RUN_GRACE}s — check the dream_daq pane BEFORE rebooting"
    fi
else
    step "run" skip "no RunCtrl running"
fi
# daq_control itself may still be finishing its shutdown (HV/bookkeeping).
if pgrep -f "daq_control.py" >/dev/null 2>&1; then
    if wait_gone "daq_control.py" 60; then
        step "daq_control" ok "finished its shutdown"
    else
        step "daq_control" fail "daq_control.py still running after 60s"
    fi
else
    step "daq_control" skip "not running"
fi

# ---------------------------------------------------------------------------
# 3. The N1081B TT stream — the one step with real hardware consequences.
#    Stop-file, NOT a signal or kill-session: the supervisor has to close the stream
#    and put section C of .244 back to counters, and be seen to verify it.
# ---------------------------------------------------------------------------
publish "stopping N1081B TT stream (.244 restore)"
if pgrep -f "tt_stream_supervisor.py" >/dev/null 2>&1; then
    touch "$TT_STOP_FILE"
    if wait_gone "tt_stream_supervisor.py" "$TT_GRACE"; then
        tmux kill-session -t n1081b_timetag_watcher 2>/dev/null
        rm -f "$TT_STOP_FILE"
        # The supervisor's own log is the authority on whether the restore verified.
        if tail -40 "$REPO_DIR/logs/n1081b_tt_stream.log" 2>/dev/null \
             | grep -q "restore C -> counter: OK"; then
            step "n1081b_tt_stream" ok ".244 section C restored to counter (verified in log)"
        else
            step "n1081b_tt_stream" ok "supervisor exited cleanly; restore not confirmed in the last log lines — check logs/n1081b_tt_stream.log"
        fi
    else
        # Do NOT escalate to SIGKILL. Leave it running and say so: a reboot's own
        # SIGTERM is still a clean path, whereas SIGKILL here is what wedges .244.
        rm -f "$TT_STOP_FILE"
        step "n1081b_tt_stream" fail "supervisor did not finish in ${TT_GRACE}s. NOT killed (SIGKILL would wedge .244). Wait for it, then re-run; do not reboot yet."
    fi
else
    tmux kill-session -t n1081b_timetag_watcher 2>/dev/null
    step "n1081b_tt_stream" skip "supervisor not running"
fi

# ---------------------------------------------------------------------------
# 4. backup_watcher — give an in-flight xrdcp a chance to finish. Note it runs under
#    supervise_watcher.sh, which would restart it, so stop the supervisor wrapper too.
# ---------------------------------------------------------------------------
publish "stopping backup_watcher"
pkill -TERM -f "supervise_watcher.sh" 2>/dev/null
stop_session backup_watcher "backup_watcher.py" "$BACKUP_GRACE" "backup_watcher (EOS)"

# ---------------------------------------------------------------------------
# 5. Everything else: poll loops and loggers. space_watcher goes early-ish so it is not
#    mid-delete. dream_daq last of the DAQ set.
# ---------------------------------------------------------------------------
publish "stopping remaining watchers"
stop_session space_watcher       "space_watcher.py"        30 "space_watcher (SSD prune)"
stop_session processor_watcher   "processor_watcher.py"    30 "processor_watcher"
stop_session pedestal_watcher    "pedestal_watcher.py"     30 "pedestal_watcher"
stop_session qa_watcher          "qa_watcher.py"           30 "qa_watcher"
stop_session gas_watcher         "gas_watcher.py"          "$GENERIC_GRACE" "gas_watcher (FLOW-BUS; setpoints kept)"
stop_session he3_pressure_watcher "he3_pressure_watcher.py" "$GENERIC_GRACE" "he3_pressure_watcher (GPIB)"
stop_session beam_watcher        "beam_watcher.py"         "$GENERIC_GRACE" "beam_watcher (NXCALS)"
stop_session stream1_watcher     "stream1_watcher.py"      "$GENERIC_GRACE" "stream1_watcher"
stop_session stats_page_watcher  "stats_page_watcher.py"   "$GENERIC_GRACE" "stats_page_watcher"
stop_session system_stats_watcher "system_stats_watcher.py" "$GENERIC_GRACE" "system_stats_watcher"
# hv_control: monitoring/alerting only. Stopping it does NOT change HV state — the
# channels stay exactly where they are, which is what we want across a reboot.
stop_session hv_control          "hv_control.py"           "$GENERIC_GRACE" "hv_control (HV left ON, by design)"

for s in dream_daq daq_control; do
    if tmux has-session -t "$s" 2>/dev/null; then
        tmux kill-session -t "$s" 2>/dev/null
        step "$s" ok "session reaped"
    fi
done

# ---------------------------------------------------------------------------
# 6. Verdict.
# ---------------------------------------------------------------------------
# log() prefixes each line with "<timestamp> | ", so anchor on the separator, not ^.
# Assigned without `|| echo 0`: grep -c already prints 0 when it finds nothing, and the
# fallback would have appended a SECOND 0 and made the -eq test below fail to parse.
FAILED=$(grep -c ' | \[fail\] ' "$LOG_FILE" 2>/dev/null)
[ -z "$FAILED" ] && FAILED=0
if [ "$FAILED" -eq 0 ]; then
    SAFE="true"
    publish "done — SAFE TO REBOOT"
    log "=== soft shutdown complete: SAFE TO REBOOT ==="
    log "HV is still on and gas is still flowing, by design."
    echo "$(_now) | SOFT_SHUT_DONE | bash_script  | safe_to_reboot=yes" >> "$EVENT_LOG"
    exit 0
fi
SAFE="false"
publish "done with $FAILED problem(s) — review before rebooting"
log "=== soft shutdown finished with $FAILED problem(s) — review above before rebooting ==="
echo "$(_now) | SOFT_SHUT_DONE | bash_script  | safe_to_reboot=no problems=$FAILED" >> "$EVENT_LOG"
exit 1
