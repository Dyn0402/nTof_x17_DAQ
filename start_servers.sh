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
# N1081B time-tag watcher: sole owner of Module 5 (.244). Arms all four scintillator-wall
# sections to Time-Tag and streams per-edge timestamps to a daily CSV (n1081b/logs/).
# NOTE: this holds .244 in Time-Tag mode (not counter) for as long as it runs, and
# poll_modules auto-skips .244 while it is up. On stop it restores .244 to counters.
# Flask reads its state from config/n1081b_timetag_state.json. See n1081b/TIMETAG_WATCHER.md.
# DISABLED 2026-07-15: a ~1 h run WEDGED .244's firmware (command interface hung, needed a
# power-cycle) — the rapid TT start/stop cycling is too hard on the board. Do NOT re-enable
# until the cadence is made gentle + a duration soak-test passes. See n1081b/TIMETAG_WATCHER.md.
# bash_scripts/start_tmux.sh n1081b_timetag_watcher "python n1081b_timetag_watcher.py" 5000
