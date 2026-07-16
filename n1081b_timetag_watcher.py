#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on July 14 2026
Created as nTof_x17_DAQ/n1081b_timetag_watcher.py

@author: Dylan Neff, dylan

Standalone N1081B time-tag watcher -- the SOLE owner of Module 5 (.244), streaming
its four scintillator-wall sections (A-D) as per-edge timestamps.

Runs in its own tmux session (started by start_servers.sh at boot, or via the GUI
"Start N1081B Time-Tag Watcher" button). Continuously:
  * arms every section to Time-Tag once, then reads their buffers round-robin
    (the persistent-FIFO scheme -- see n1081b/timetag_watcher_controller.py),
  * dedups and appends per-edge rows (host_unix, section, channel, t_board_ns) to
    the per-day CSV n1081b/logs/n1081b_timetag_%Y-%m-%d.csv,
  * publishes health + per-section rates to config/n1081b_timetag_state.json
    (served by /n1081b/status),
  * on exit RESTORES .244 to its counter steady state.

Because the board broadcasts its stream to every websocket client, the Flask app
and poll_modules.py must NOT open .244 while this runs -- this watcher owns it,
exactly as the gas watcher owns the FLOW-BUS. poll_modules.py drops .244 from
POLL_IPS whenever this watcher's tmux session is alive.
"""

import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from n1081b.timetag_watcher_controller import N1081BTimeTagController


def main():
    # `--restore` is the recovery path: if the watcher was SIGKILLed (or crashed)
    # without running its clean-exit restore, .244 is left in 'wire' passthrough.
    # This puts all four sections back to their counter steady state and exits.
    if "--restore" in sys.argv[1:]:
        ok = N1081BTimeTagController().restore_counters()
        sys.exit(0 if ok else 1)
    N1081BTimeTagController().run_blocking()


if __name__ == "__main__":
    main()
