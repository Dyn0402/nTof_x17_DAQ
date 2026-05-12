#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Standalone QA watcher configuration for nTof.
Edit the constants below, then run this script to regenerate config/qa_config.json.
The flask UI's Start QA Watcher button reads that JSON to launch qa_watcher.py.
"""

import json
import os

BASE_DATA    = '/mnt/data/x17/beam_may/'
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
    'qa_file_mode': 'all',

    # Run filtering
    'include_runs': ['run_18'],  # None,  # e.g. ['run_1', 'run_2'] — only process these; None = all
    'exclude_runs': None,  # e.g. ['run_0']          — skip these

    # Watcher behavior
    'poll_interval':  10,  # seconds between scans
    'stale_run_days':  1,  # runs with no new combined_hits for this many days are skipped
}

if __name__ == '__main__':
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config', 'qa_config.json')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as f:
        json.dump(CONFIG, f, indent=4)
    print(f'Written: {out_path}')
