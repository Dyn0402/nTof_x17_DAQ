#!/bin/bash

# Start sessions
bash_scripts/start_tmux.sh hv_control "python hv_control.py"
bash_scripts/start_tmux.sh dream_daq "python dream_daq_control.py"
#bash_scripts/start_tmux.sh decoder "python processing_control.py"
#bash_scripts/start_tmux.sh processor "python processor_server.py"
bash_scripts/start_tmux.sh daq_control "echo 'Daq control session started'"
bash_scripts/start_tmux.sh flask_server "flask_app/start_flask.sh"
