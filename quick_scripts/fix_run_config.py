#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on February 04 11:11 PM 2026
Created in PyCharm
Created as nTof_x17_DAQ/fix_run_config.py

@author: Dylan Neff, dylan
"""

import os
import numpy as np
import json


def main():
    # base_path = '/media/dylan/data/x17/feb_beam/runs/'
    base_path = '/mnt/data/x17/beam_feb/runs/'
    run_nums = np.arange(1, 30)

    for run_num in run_nums:
        run = f'run_{run_num}'

        run_config_path = f'{base_path}{run}/run_config.json'
        if not os.path.exists(run_config_path):
            print(f'Run config file does not exist: {run_config_path}')
            continue

        with open(run_config_path) as f:
            run_cfg = json.load(f)
        print('\n', run_cfg)

        run_cfg = fix_dream_feus(run_cfg)

        with open(run_config_path, 'w') as f:
            json.dump(run_cfg, f)

    print('donzo')


def fix_dream_feus(run_cfg):
    """
    Fix DREAM feus
    Args:
        run_cfg:

    Returns:

    """
    det_name = 'mx17_1'

    for det in run_cfg['detectors']:
        if det['name'] != det_name:
            continue
        print(det['dream_feus'])
        for connector in det['dream_feus']:
            if connector.startswith('y_'):
                det['dream_feus'][connector][0] = 5

    print(run_cfg['detectors'][0]['dream_feus'])

    return run_cfg


if __name__ == '__main__':
    main()
