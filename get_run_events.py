#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on November 20 15:16 2025
Created in PyCharm
Created as Cosmic_Bench_DAQ_Control/get_run_events

@author: Dylan Neff, dn277127
"""

import os
import sys
import pandas as pd

RUN_DIR = '/mnt/data/beam_sps_25/Run/'

def main():
    if len(sys.argv) != 2:
        print('Usage: python get_run_events.py <run_number>')
        sys.exit(1)
    run_number = int(sys.argv[1])
    total_events, subrun_details = get_total_events_for_run(RUN_DIR, run_number)
    print(f'Total Dream events for run {run_number}: {total_events}')

    print('donzo')


def get_total_events_for_run(run_dir, run_number, csv_name="daq_status_log.csv", event_col="dream_events"):
    """
    Given the base run directory (e.g. '/mnt/data/beam_sps_25/Run/')
    and the run number (e.g. 160),
    return the total number of Dream events across all subruns.
    """

    # Format like "run_160"
    run_name = f"run_{run_number}"
    run_path = os.path.join(run_dir, run_name)

    if not os.path.exists(run_path):
        raise FileNotFoundError(f"Run directory does not exist: {run_path}")

    total_events = 0
    subrun_event_counts = {}  # optional: return per-subrun details if needed

    # Loop over subruns
    for subrun in os.listdir(run_path):
        subrun_path = os.path.join(run_path, subrun)

        if not os.path.isdir(subrun_path):
            continue

        csv_path = os.path.join(subrun_path, csv_name)
        if not os.path.exists(csv_path):
            continue

        try:
            df = pd.read_csv(csv_path)

            if event_col not in df.columns:
                continue

            max_events = df[event_col].max()
            if pd.notna(max_events):
                total_events += int(max_events)
                subrun_event_counts[subrun] = int(max_events)

        except Exception as e:
            print(f"Warning: failed reading {csv_path}: {e}")

    return total_events, subrun_event_counts


if __name__ == '__main__':
    main()
