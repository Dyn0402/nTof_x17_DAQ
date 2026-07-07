#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on July 08 2026
Created in PyCharm
Created as nTof_x17_DAQ/gas_watcher.py

@author: Dylan Neff, dylan

Standalone gas-mixer watcher — the SOLE owner of the Bronkhorst FLOW-BUS.

Runs in its own tmux session (started by start_servers.sh at boot, or via the GUI
"Start Gas Watcher" button). Continuously:
  * polls both mass-flow controllers and appends to the per-day CSV,
  * publishes the latest readback to config/gas_state.json (served by /gas/status),
  * applies setpoint commands the Flask app drops in config/gas_command.json
    (from /gas/apply and /gas/zero).

Because a serial bus can only have one owner, the Flask app must NOT open the bus
itself — it talks to this watcher purely through those two files. See
gas_mixer_control/README.md.

Does not zero setpoints on exit: the controllers hold their setpoints in hardware,
so gas keeps flowing across a watcher restart.
"""

import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from gas_mixer_control.flow_controller import FlowController


def main():
    FlowController().run_blocking()


if __name__ == "__main__":
    main()
