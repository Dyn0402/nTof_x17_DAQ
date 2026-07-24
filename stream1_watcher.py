#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on July 22 2026
Created in PyCharm
Created as nTof_x17_DAQ/stream1_watcher.py

@author: Dylan Neff, dylan

Standalone n_TOF stream1 raw-file-size watcher — the online version of the
SiPM-wall dropout study in ~/beam_july/analysis/sipm_wall_filesize/.

Runs in its own tmux session (started by start_servers.sh at boot, or via the GUI
"Start Stream1 Watcher" button). Every ~2 min it:
  * lists the newest run dirs on EOS with `xrdfs ls -l` (sizes + mtimes only —
    no file contents are read),
  * appends every newly-seen stream1 file to the per-day CSV in
    ~/beam_july/slow_control/stream1_filesize/,
  * flags files whose size falls below a trailing-window baseline (a wall dropping
    out roughly halves the stream volume) and publishes a summary to
    config/stream1_filesize_state.json (served by /stream1/status).

Read-only: owns no hardware and commands nothing, so it is safe to start/stop at
any time. Needs a valid Kerberos ticket for EOS (the keytab-seeded one the backup
watcher uses). See stream1_monitor/stream1_size_controller.py for the classifier.
"""

import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from stream1_monitor.stream1_size_controller import Stream1SizeMonitor


def main():
    Stream1SizeMonitor().run_blocking()


if __name__ == "__main__":
    main()
