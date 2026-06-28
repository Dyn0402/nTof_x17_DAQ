#!/bin/bash

# NOTE: make_dream_ped_cfg.py is currently an empty no-op. The pedestal cfg is
# the pre-made dream_config/Tcm_Mx17_May_ped.cfg referenced by
# run_config_pedestals.py, so this call is disabled. Re-enable it if/when
# make_dream_ped_cfg.py is implemented to generate that pedestal cfg.
# python make_dream_ped_cfg.py

python run_config_pedestals.py

CONFIG_PATH="run_config_pedestals.json"

bash_scripts/start_run.sh "$CONFIG_PATH"
