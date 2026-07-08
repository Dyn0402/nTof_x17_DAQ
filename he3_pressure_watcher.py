#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on July 07 2026
Created in PyCharm
Created as nTof_x17_DAQ/he3_pressure_watcher.py

@author: Dylan Neff, dylan

Standalone 3He target pressure-gauge watcher — the SOLE owner of the Keithley 2000
GPIB link.

Runs in its own tmux session (started by start_servers.sh at boot, or via the GUI
"Start 3He Pressure Watcher" button). Continuously:
  * polls the Keithley DC voltage, converts to pressure = (V-1)*400 bar,
  * appends the pressure to the per-day CSV,
  * publishes the latest reading to config/he3_pressure_state.json (served by
    /he3_pressure/status).

Because a GPIB instrument has one owner, the Flask app must NOT open the bus itself
— it reads the published state file. See he3_pressure_reader/KEITHLEY2000_GPIB_SETUP.md.

Read-only: there is nothing to command, so (unlike the gas watcher) there is no
command file.
"""

import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from he3_pressure_reader.he3_pressure_controller import He3PressureController


def main():
    He3PressureController().run_blocking()


if __name__ == "__main__":
    main()
