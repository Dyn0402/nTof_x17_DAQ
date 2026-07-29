#!/bin/bash

# Source the venv
source .venv/bin/activate

# Seed a Kerberos ticket from the keytab BEFORE the NXCALS/EOS watchers start.
# A reboot wipes /tmp/krb5cc_1000, and beam_watcher (Spark) + backup_watcher (EOS
# xrdcp) both idle in an auth-error state without one. The keytab is reboot-safe
# (no password / gpg / tty). Regenerate it after a CERN password change with
# bash_scripts/regen_cern_keytab.sh. A cron entry (crontab -l) renews it hourly.
KEYTAB="$HOME/.keytab/mx17_cern.keytab"
if [ -f "$KEYTAB" ]; then
    kinit -kt "$KEYTAB" dneff@CERN.CH \
        && echo "[start_servers] kinit from keytab OK" \
        || echo "[start_servers] WARNING: kinit from keytab FAILED (keytab stale? run regen_cern_keytab.sh)"
else
    echo "[start_servers] WARNING: no keytab at $KEYTAB — beam/backup watchers need a manual kinit"
fi

# Absolute venv interpreter for anything launched INSIDE a tmux session: the tmux
# login shell re-reads the profile and drops the venv from PATH, so a bare "python"
# there is not reliably the venv's. (The Flask /start_* endpoints use sys.executable
# for the same reason.)
VENV_PY="$PWD/.venv/bin/python"

# Launch a watcher that needs its JSON config regenerated from a .py generator first,
# mirroring exactly what the Flask /start_processor|/start_qa|/start_backup buttons do.
# If the generator fails we skip the watcher rather than starting it on a stale config.
start_watcher_with_config () {
    local session=$1 script=$2 generator=$3 config=$4 hist=${5:-5000}
    # Check the session BEFORE regenerating anything, so re-running this script to fill
    # a gap is a true no-op for whatever is already up (it must not rewrite a live
    # watcher's config out from under it).
    if tmux has-session -t "$session" 2>/dev/null; then
        echo "❌ Tmux session '$session' already exists — leaving it alone."
        return 1
    fi
    if ! "$VENV_PY" "$PWD/$generator" >/dev/null; then
        echo "[start_servers] WARNING: $generator failed — $session NOT started"
        return 1
    fi
    bash_scripts/start_tmux.sh "$session" "$VENV_PY $PWD/$script $PWD/$config" "$hist"
}

# Start sessions. 3rd arg = tmux scrollback cap in LINES (memory-saving).
# hv_control is very chatty (HV monitor every monitor_interval seconds), so
# keep it short. The others keep a longer buffer for debugging.
bash_scripts/start_tmux.sh hv_control "python hv_control.py" 500
# Prepend bash_scripts/daq_shims to PATH so RunCtrl's post-pedestal
# `xterm -e xmgrace ...` plot calls hit a no-op shim instead of failing on the
# missing X DISPLAY (which otherwise hangs the DAQ at a "Press C to Continue"
# prompt). Data is unaffected; only the interactive plot is skipped. See the
# comment in bash_scripts/daq_shims/xterm.
# Pinned to 220x50: RunCtrl's live status screen ("TestFun_TakeData: RunTime
# ... IntRate=...") writes to fixed column positions and garbles/clips once
# the pane is narrower than that, which breaks Flask's status parsing
# (daq_status.get_dream_daq_status). Pinning also sets window-size manual for
# this window, so an operator attaching with a smaller terminal can't shrink
# it back down. See bash_scripts/start_tmux.sh.
bash_scripts/start_tmux.sh dream_daq "PATH=/home/mx17/PycharmProjects/nTof_x17_DAQ/bash_scripts/daq_shims:\$PATH python dream_daq_control.py" 20000 220 50
#bash_scripts/start_tmux.sh decoder "python processing_control.py" 20000
#bash_scripts/start_tmux.sh processor "python processor_server.py" 20000
bash_scripts/start_tmux.sh daq_control "echo 'Daq control session started'" 20000
bash_scripts/start_tmux.sh flask_server "flask_app/start_flask.sh" 5000
# Gas-mixer watcher: sole owner of the FLOW-BUS (reads + logs + applies setpoints).
# Must run for gas logging/control; Flask talks to it via config/gas_command.json.
bash_scripts/start_tmux.sh gas_watcher "python gas_watcher.py" 5000
# 3He pressure watcher: sole owner of the Keithley 2000 GPIB link (reads + logs pressure).
# Flask reads its state from config/he3_pressure_state.json. Read-only (no control).
bash_scripts/start_tmux.sh he3_pressure_watcher "python he3_pressure_watcher.py" 5000
# System-stats watcher: samples psutil (CPU / memory / disk-space / net + disk I/O) at
# 2 Hz and appends a per-day CSV to ~/beam_july/slow_control/system_stats/. Pure logger:
# owns no hardware, no state file (Flask /system_stats reads psutil directly for the live
# Overview plots). Retune via config/system_stats_config.json. See system_monitor/.
bash_scripts/start_tmux.sh system_stats_watcher "python system_stats_watcher.py" 5000
# Beam-intensity watcher: sole owner of the NXCALS/Spark session (n_TOF protons on
# target from Timber's database). Runs under its OWN venv (pytimber + PySpark) and
# needs a valid Kerberos ticket (kinit dneff@CERN.CH) — without one it idles in an
# error state and recovers on its own once the ticket is reseeded.
# See beam_monitor/README.md.
bash_scripts/start_tmux.sh beam_watcher "$HOME/venvs/nxcals/bin/python beam_watcher.py" 5000
# stream1 file-size watcher: polls EOS listings (xrdfs ls -l — sizes/mtimes only, no
# file contents) for the n_TOF raw-stream files and flags reduced-size episodes, the
# online proxy for a SiPM wall dropping out (~50% size drop; see
# ~/beam_july/analysis/sipm_wall_filesize/). Read-only, owns no hardware; needs the
# same Kerberos ticket as the EOS backup. Flask reads config/stream1_filesize_state.json.
bash_scripts/start_tmux.sh stream1_watcher "python stream1_watcher.py" 5000
# --- watchers that used to be GUI-start-only ---------------------------------------
# Added 2026-07-22. These three are started from the Flask GUI, so they were never in
# this script and EVERY reboot silently left them down — the 07-22 reboot included.
# Nothing alerted on their absence (see the new rule_*_watcher_dead rules in
# flask_app/monitor.py), and a dead backup_watcher means runs stop reaching EOS, which
# is the one failure here with real data-loss exposure. backup_watcher must come after
# the kinit above (it needs the same Kerberos ticket as the EOS mirror).
# To go back to manual start, comment out the relevant line — the GUI buttons are
# unchanged and still kill/restart these sessions.
start_watcher_with_config backup_watcher    backup_watcher.py    backup_config.py    config/backup_config.json
start_watcher_with_config processor_watcher processor_watcher.py processor_config.py config/processor_config.json
start_watcher_with_config qa_watcher        qa_watcher.py        qa_config.py        config/qa_config.json
# SSD space watcher: keeps the raw staging disk (/home/mx17/july_dream/dream_run) above a
# free-space floor by deleting the OLDEST runs that are verified SSD -> HDD -> EOS, newest
# runs first to survive. Added 2026-07-22: nothing had ever pruned that disk and at the
# measured ~22 GB/hr raw rate it was ~5 h from full, which stops acquisition outright.
# Deletion goes through space_manager.delete_run, which re-verifies against EOS itself and
# refuses the active run. Retune the floor in space_config.py. Depends on backup_watcher
# above actually reaching EOS — if backups stall, this correctly frees NOTHING and the
# rule_space_watcher_stuck alert fires.
#
# NB: this one deliberately does NOT use start_watcher_with_config. That helper
# regenerates the JSON from the .py generator on every boot, which is right for the
# watchers whose config only ever changes in git — but the space buffer is
# operator-settable from the GUI (Disk Space tab), which writes this JSON directly.
# Regenerating it here would silently reset the operator's buffer at every reboot.
# So: seed the defaults only if the file is absent, then start.
if [ ! -f "$PWD/config/space_config.json" ]; then
    "$VENV_PY" "$PWD/space_config.py" >/dev/null \
        || echo "[start_servers] WARNING: space_config.py failed — space_watcher NOT started"
fi
if [ -f "$PWD/config/space_config.json" ]; then
    bash_scripts/start_tmux.sh space_watcher "$VENV_PY $PWD/space_watcher.py $PWD/config/space_config.json" 5000
fi
# Public statistics page publisher: xrdcp's a small JSON summary + the projection
# plot into the EOS directory behind https://dylan-neff.web.cern.ch/x17/ every 60 s.
# Push-only and read-only — opens no port, owns no hardware, commands nothing, so it
# is safe to start at boot and to kill at any time. Needs the same Kerberos ticket as
# the EOS backup, so it must come after the kinit above. If config/stats_page_config.json
# is missing it falls back to the built-in defaults, which are already correct for
# this machine. See stats_page/README.md.
bash_scripts/start_tmux.sh stats_page_watcher "python stats_page_watcher.py" 5000
# NOT auto-started: pedestal_watcher (config/pedestal_qa_config.json). It is a
# per-pedestal-run tool driven from the GUI, not a standing service, and an empty
# 0-byte pedestal FDF deadlocks it — add a start_watcher_with_config line here if you
# decide you want it standing.
# N1081B trigger-timestamp stream: sole owner of Module 5 (.244). Streams section C
# (the four sector coincidences from M3) continuously as per-edge timestamps, for
# offline DREAM event <-> trigger matching. Holds .244 in Time-Tag for as long as it
# runs, and poll_modules auto-skips .244 while it is up; on stop it restores counters.
# Flask reads config/n1081b_timetag_state.json. See n1081b/TIMETAG_WATCHER.md.
#
# The tmux session name MUST stay `n1081b_timetag_watcher` — that is the name
# poll_modules looks for to decide to skip .244.
#
# History: the v1/v2 round-robin watcher (`n1081b_timetag_watcher.py`) WEDGED .244 on
# 2026-07-15 and again on 07-17 — the damage was rapid TT start/stop cycling and
# reconnect churn, not TT streaming itself. The supervisor below is the mode that was
# actually proven: ONE section, THREE stream commands per 6 h segment, zero reconnects.
# Re-enabled 2026-07-29. Do NOT swap this back to n1081b_timetag_watcher.py.
bash_scripts/start_tmux.sh n1081b_timetag_watcher \
    "$VENV_PY $PWD/n1081b/tt_stream_supervisor.py --section C" 5000
