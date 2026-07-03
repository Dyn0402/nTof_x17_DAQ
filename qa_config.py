#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Standalone QA watcher configuration for nTof.
Edit the constants below, then run this script to regenerate config/qa_config.json.
The flask UI's Start QA Watcher button reads that JSON to launch qa_watcher.py.
"""

import json
import os

from run_config_beam import BASE_DATA_DIR

BASE_DATA    = BASE_DATA_DIR
NTOF_X17_DIR = '/home/mx17/PycharmProjects/nTof_x17'

CONFIG = {
    # Top-level directory containing all run_N/ subdirectories
    'runs_dir': f'{BASE_DATA}runs/',

    # Path to the nTof_x17 analysis repository (contains ntof_daq_analysis/detector_qa.py)
    'ntof_x17_dir': NTOF_X17_DIR,

    # Subdirectory name for combined hits files (must match processor_config)
    'combined_hits_inner_dir': 'combined_hits_root',

    # QA file mode:
    #   'all'      — rerun QA with all accumulated files whenever a new one appears (default)
    #   'first'    — run QA once per subrun using only file_num=0 (fast for long runs)
    #   'per_file' — independent QA plot set for each file_num
    'qa_file_mode': 'first',

    # Run filtering
    'include_runs': None,  # None,  # e.g. ['run_1', 'run_2'] — only process these; None = all
    'exclude_runs': None,  # e.g. ['run_0']          — skip these

    # Watcher behavior
    'poll_interval':   10,  # seconds between scans
    'stale_run_days':   1,  # runs with no new combined_hits for this many days are skipped
    'memory_kill_pct': 80,  # kill the QA process if system RAM usage exceeds this % (retried next poll)

    # CPU throttling — keep QA from starving the DAQ.  6-core box, DAQ uses ~1 core:
    #   pin QA to cores 2-5 (4 cores), reserve 0-1 for the DAQ, run at lowest priority.
    'cpu_nice':         19,          # nice level (also ionice idle class); null = no niceing
    'cpu_affinity': [2, 3, 4, 5],    # CPU cores QA may use (taskset); null = all cores
    'qa_threads':     None,          # numpy/BLAS thread cap; null = len(cpu_affinity) = 4
}

if __name__ == '__main__':
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config', 'qa_config.json')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as f:
        json.dump(CONFIG, f, indent=4)
    print(f'Written: {out_path}')
