#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Passive scintillator HV + beam logger.

Opens its own (read-only) CAEN session and logs vmon/imon/power for the
configured scint channels plus the current beam intensity to a CSV under
~/beam_july/scint_hv_scan/<label>/. NEVER sets a voltage — safe to run while
you drive HV by hand (CAENGECO) or just want a record. Coexists with the DAQ.

Usage:
    cd <repo root>
    python scintillator_hv/scint_hv_logger.py [label]

Ctrl-C to stop.
"""

import os
import sys
import threading
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scint_hv_config as cfg
import scint_hv_lib as lib


def main():
    label = sys.argv[1] if len(sys.argv) > 1 else 'log'
    stamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    out_dir = os.path.join(cfg.OUTPUT_ROOT, f'{stamp}_{label}')
    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, 'hv_beam_monitor.csv')

    lib.validate_channels()
    print(f'Logging HV + beam every {cfg.MONITOR_INTERVAL_S}s to:\n  {csv_path}')
    print('Read-only: no voltages will be changed. Ctrl-C to stop.\n')

    context = {'step_label': 'passive', 'target_v': ''}
    accumulator = lib.BeamAccumulator()
    stop_event = threading.Event()

    with lib.open_session() as caen_hv:
        try:
            lib.monitor_loop(caen_hv, csv_path, stop_event, context, accumulator)
        except KeyboardInterrupt:
            stop_event.set()
            print('\nStopped by user.')

    print(f'Data saved to {csv_path}')


if __name__ == '__main__':
    main()
