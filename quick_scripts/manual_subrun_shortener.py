#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on February 13 11:18 AM 2026
Created in PyCharm
Created as nTof_x17_DAQ/manual_subrun_shortener.py

@author: Dylan Neff, dylan
"""

import os
from time import sleep

def main():
    n_stops = 50
    sub_run_time = 5 * 60
    for i in range(n_stops):
        # Run bash script to stop DAQ then wait to do again.
        print(f'Stopping sub-run {i + 1} / {n_stops}')
        os.system(f'/bash_scripts/stop_sub_run.sh')
        print(f'Waiting {sub_run_time} seconds before stopping again.\n')
        sleep(sub_run_time)
    print('donzo')


if __name__ == '__main__':
    main()
