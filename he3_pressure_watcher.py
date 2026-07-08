#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on July 07 2026
Created in PyCharm
Created as nTof_x17_DAQ/pressure_watcher.py

@author: Dylan Neff, dylan

Standalone pressure-gauge watcher — the SOLE owner of the Keithley 2000 GPIB link.

Runs in its own tmux session (started by start_servers.sh at boot, or via the GUI
"Start Pressure Watcher" button). Continuously:
  * polls the Keithley DC voltage, converts to pressure = (V-1)*400 bar,
  * appends the pressure to the per-day CSV,
  * publishes the latest reading to config/pressure_state.json (served by
    /pressure/status).

Because a GPIB instrument has one owner, the Flask app must NOT open the bus itself
— it reads the published state file. See pressure_reader/KEITHLEY2000_GPIB_SETUP.md.

Read-only: there is nothing to command, so (unlike the gas watcher) there is no
command file.
"""

import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from pressure_reader.pressure_controller import PressureController


def main():
    PressureController().run_blocking()


if __name__ == "__main__":
    main()
