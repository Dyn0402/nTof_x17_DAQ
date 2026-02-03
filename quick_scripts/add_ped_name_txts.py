#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on February 03 3:35 PM 2026
Created in PyCharm
Created as nTof_x17_DAQ/add_ped_name_txts.py

@author: Dylan Neff, dylan
"""

import os


def main():
    runs_path = '/mnt/data/x17/beam_feb/runs/'
    ped_run_name = 'pedestals_02-02-26_10-49-02'
    raw_daq_data_name = 'raw_daq_data'
    for run in os.listdir(runs_path):
        run_dir = os.path.join(runs_path, run)
        if not os.path.isdir(run_dir):
            continue
        for sub_run in os.listdir(run_dir):
            subrun_dir = os.path.join(run_dir, sub_run)
            if not os.path.isdir(subrun_dir):
                continue
            print(f'Adding pedestal_run.txt to {run}/{sub_run}')
            ped_name_txt_path_bad = os.path.join(subrun_dir, 'pedestal_run.txt')
            os.remove(ped_name_txt_path_bad) if os.path.exists(ped_name_txt_path_bad) else None
            ped_name_txt_path = os.path.join(f'{subrun_dir}{raw_daq_data_name}/', 'pedestal_run.txt')
            with open(ped_name_txt_path, 'w') as f:
                f.write(ped_run_name)
        print()

    print('donzo')


if __name__ == '__main__':
    main()
