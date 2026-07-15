#!/bin/bash

# Source the venv
source .venv/bin/activate

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
