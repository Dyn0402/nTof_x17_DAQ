#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Standalone processor configuration for nTof.
Edit the constants below, then run this script to regenerate config/processor_config.json.
The flask UI's Start Processor button reads that JSON to launch processor_watcher.py.
"""

import json
import os

# --- Paths ---
BASE_SOFT = '/home/mx17/CLionProjects/mm_strip_reconstruction/build/'
BASE_DATA = '/mnt/data/x17/beam_may/'

CONFIG = {
    # Top-level directory containing all run_N/ subdirectories
    'runs_dir': f'{BASE_DATA}runs/',

    # Subdirectory names (must match what daq_control.py creates)
    'raw_daq_inner_dir':       'raw_daq_data',
    'decoded_root_inner_dir':  'decoded_root',
    'hits_inner_dir':          'hits_root',
    'combined_hits_inner_dir': 'combined_hits_root',

    # C++ executables from mm_strip_reconstruction
    'decode_executable':  f'{BASE_SOFT}decoder/decode',
    'analyze_executable': f'{BASE_SOFT}waveform_analysis/analyze_waveforms',
    'combine_executable': f'{BASE_SOFT}feu_hit_combiner/combine_feus_hits',

    # Pipeline stages to run
    'do_decode':  True,
    'do_analyze': True,
    'do_combine': True,

    # Cleanup options
    'save_fdfs':    True,  # Keep raw FDF files after processing
    'save_decoded': True,  # Keep decoded ROOT files after analysis

    # Pedestal location:
    #   'same'  - pedestal FDFs are in raw_daq_data/ alongside data FDFs
    #   'abs'   - fixed absolute path given by pedestal_dir
    #   'find'  - read pedestal_run.txt from raw_daq_data/ and look up
    #             pedestal_dir/<name>/pedestals/
    # 'pedestal_loc': 'find',
    'pedestal_loc': 'same',
    'pedestal_dir': f'{BASE_DATA}pedestals/',

    # Run filtering: process only specific runs or exclude certain runs by directory name.
    # If include_runs is a non-empty list, only those run directories are processed.
    # If exclude_runs is a non-empty list, those run directories are skipped.
    # Both null/empty means process all runs as normal.
    'include_runs': ['run_3'],  # e.g. ['run_1', 'run_2'] — only process these runs, None for all
    'exclude_runs': None,  # e.g. ['run_3']          — skip these runs

    # Watcher behavior
    'poll_interval':  30,  # seconds between full directory scans
    'stale_run_days':  4,  # runs with no new FDFs for this many days are checked once then skipped
    'free_threads':    2,  # CPU threads to leave free during parallel processing
}

if __name__ == '__main__':
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config', 'processor_config.json')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as f:
        json.dump(CONFIG, f, indent=4)
    print(f'Written: {out_path}')
