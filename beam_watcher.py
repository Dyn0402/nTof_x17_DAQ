#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on July 10 2026
Created in PyCharm
Created as nTof_x17_DAQ/beam_watcher.py

@author: Dylan Neff, dylan

Standalone n_TOF beam-intensity watcher — the SOLE owner of the NXCALS/Spark
session that pulls F16.BCT372.TOF:INTENSITY (protons on the n_TOF target, the
same data Timber shows).

Runs in its own tmux session (started via the GUI "Start Beam Watcher" button).
Continuously:
  * queries NXCALS every ~30 s for the latest TOF-cycle intensities,
  * appends every point to the per-day CSV in beam_monitor/logs/,
  * publishes a beam on/off summary to config/beam_state.json (served by
    /beam/status and the Shift Overview card).

MUST run under the NXCALS venv (~/venvs/nxcals/bin/python — pytimber + PySpark
live there, not in the DAQ venv) with a valid Kerberos ticket in the default
cache (same `kinit dneff@CERN.CH` the EOS backup uses). See
beam_monitor/README.md.
"""

import os
import sys
import traceback

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from beam_monitor.beam_intensity_controller import BeamIntensityMonitor, BEAM_EVENT_LOG
from common_functions import log_event


def main():
    try:
        BeamIntensityMonitor().run_blocking()
    except Exception as e:  # noqa: BLE001
        # Durable second copy only — re-raised so the tmux pane still shows the live
        # traceback and the process still exits non-zero.
        log_event(BEAM_EVENT_LOG, 'CRASH', 'beam_watcher',
                  error=repr(e), traceback=traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
