#!/bin/bash

# Source the venv
source .venv/bin/activate

# Start sessions. 3rd arg = tmux scrollback cap in LINES (memory-saving).
# hv_control is very chatty (HV monitor every monitor_interval seconds), so
# keep it short. The others keep a longer buffer for debugging.
bash_scripts/start_tmux.sh hv_control "python hv_control.py" 500
bash_scripts/start_tmux.sh dream_daq "python dream_daq_control.py" 20000
#bash_scripts/start_tmux.sh decoder "python processing_control.py" 20000
#bash_scripts/start_tmux.sh processor "python processor_server.py" 20000
bash_scripts/start_tmux.sh daq_control "echo 'Daq control session started'" 20000
bash_scripts/start_tmux.sh flask_server "flask_app/start_flask.sh" 5000
