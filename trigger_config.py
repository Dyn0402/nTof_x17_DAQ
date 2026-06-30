#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Standalone trigger-logger configuration for the CAEN N1081B units.
Edit the constants below, then run this script to regenerate config/trigger_config.json.
trigger_logger.py reads that JSON to know which units to poll and where to write CSVs.
"""

import json
import os

BASE_DATA = '/mnt/data/x17/beam_may/'

# One entry per N1081B unit.  `channels` are the LEMO input indices (0-5) to time-tag
# on the chosen `section`.  Add as many units as you have.
UNITS = [
    {
        'name':           'n1081b_0',
        'ip':             '192.168.50.153',
        'password':       'password',
        'section':        'A',        # A | B | C | D
        'channels':       [0],        # LEMO inputs 0-5 to time-tag
        'input_standard': 'NIM',      # NIM | TTL | DISCRIMINATOR
        'threshold':      0,          # only used for DISCRIMINATOR standard
        'impedance':      50,         # 50 | 'high'
    },
    # {
    #     'name':           'n1081b_1',
    #     'ip':             '192.168.50.154',
    #     'password':       'password',
    #     'section':        'A',
    #     'channels':       [0, 1],
    #     'input_standard': 'NIM',
    #     'threshold':      0,
    #     'impedance':      50,
    # },
]

CONFIG = {
    # Directory where <unit_name>_triggers.csv (and .meta.json) files are written.
    # Point this at a per-run directory when launching from run control.
    'output_dir': f'{BASE_DATA}triggers/',

    'units': UNITS,

    # Seconds to wait before reconnecting a unit after a connection/login error.
    'reconnect_interval': 5,

    # WebSocket recv timeout (s).  Bounds how quickly a unit notices a stop request
    # when no triggers are arriving; lower = snappier shutdown, more idle wakeups.
    'recv_timeout': 2,
}

if __name__ == '__main__':
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config', 'trigger_config.json')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as f:
        json.dump(CONFIG, f, indent=4)
    print(f'Written: {out_path}')
