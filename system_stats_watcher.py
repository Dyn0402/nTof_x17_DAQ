#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on July 15 2026
Created in PyCharm
Created as nTof_x17_DAQ/system_stats_watcher.py

@author: Dylan Neff, dylan

Standalone DAQ-box system-resource watcher — samples psutil (CPU / memory /
disk-space / network + disk I/O) at a fixed rate and appends each sample to a
per-day CSV under ~/beam_july/slow_control/system_stats/.

Runs in its own tmux session (started by start_servers.sh at boot). Pure logger:
it owns no hardware and no state file. The Flask /system_stats endpoint reads
psutil directly for the live Overview plots, so this watcher exists only to give
those metrics a durable on-disk history. Default rate 2 Hz; retune by writing
{"poll_s": <seconds>} to config/system_stats_config.json (picked up within one
cycle). See system_monitor/system_stats_controller.py.
"""

import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from system_monitor.system_stats_controller import SystemStatsController


def main():
    SystemStatsController().run_blocking()


if __name__ == "__main__":
    main()
