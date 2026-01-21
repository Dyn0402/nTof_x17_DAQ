#!/bin/bash

python make_dream_ped_cfg.py

python run_config_pedestals.py

CONFIG_PATH="run_config_pedestals.json"

bash_scripts/start_run.sh "$CONFIG_PATH"
