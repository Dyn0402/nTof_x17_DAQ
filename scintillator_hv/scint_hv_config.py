#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Configuration for the scintillator HV scan.

This scan runs a SECOND, independent CAEN session alongside the DREAM DAQ's
hv_control.py. That is supported by the mainframe (see repo CLAUDE.md notes and
emergency_hv_off.py), on ONE hard condition:

    >>> Every channel listed here MUST be different from the channels the
    >>> running DAQ controls (run_config_beam.py detectors' hv_channels). <<<

Two sessions writing the same slot:channel would race. Different channels are
fine. scint_hv_scan.py cross-checks this against run_config_beam.py at startup
and refuses to run on any overlap.
"""

import os

# --- CAEN mainframe (same crate the DREAM DAQ uses) -------------------------
# IP matches run_config_beam.py's hv_info['ip']; creds come from hv_creds.txt
# at the repo root (line 1 = username, line 2 = password), same as the DAQ.
CAEN_IP = '128.141.177.244'
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HV_CREDS_PATH = os.path.join(REPO_ROOT, 'hv_creds.txt')

# --- Scintillator channels to scan ------------------------------------------
# FILL THIS IN. Each entry: a human label + the (slot, channel) on the crate.
# These MUST be disjoint from the DREAM DAQ's channels. Leave empty and the
# scan/logger will refuse to start rather than guess.
#
# Example:
#   SCINT_CHANNELS = [
#       {'name': 'scint_top',    'slot': 2, 'channel': 0},
#       {'name': 'scint_bottom', 'slot': 2, 'channel': 1},
#   ]
#
# Plastic scintillator PMTs, CAEN card 07 (from run_config_beam.py "HV TODO"
# comments, decoded as CAEN card.channel). All 8 plastics scanned together.
# Liquids (card 08, ch 0-3, ~2000 V, PMT NOT connected) are deliberately left
# out -> "liquid fixed". DREAM DAQ uses cards 5 & 9 only, so card 07 is disjoint.
#
# 'nominal_v' = the operating voltage to restore at the end of the scan.
# 2026-07-18: standing values are the run224466 GAIN-EQUALIZED set
# (calibrations/pss/hv_equalization_run224466.json), applied 02:21 and the basis
# of the post-FIFO M2 threshold calibration (walls rate-matched within ~2%;
# D at -38 mV == A/B/C at -30 mV). The previous flat operational set
# (1325/1275/1325/1300/1300/1300/1300/1300, run_config_beam GUI comments
# 2026-07-16) is what the 07-17 night scans up to 02:21 ran on — do not restore
# it without redoing the M2 threshold ladder.
SCINT_CHANNELS = [
    {'name': 'plastic_A_L', 'slot': 7, 'channel': 0, 'nominal_v': 1237},
    {'name': 'plastic_A_R', 'slot': 7, 'channel': 1, 'nominal_v': 1177},
    {'name': 'plastic_B_L', 'slot': 7, 'channel': 2, 'nominal_v': 1440},
    {'name': 'plastic_B_R', 'slot': 7, 'channel': 3, 'nominal_v': 1248},
    {'name': 'plastic_C_L', 'slot': 7, 'channel': 4, 'nominal_v': 1214},
    {'name': 'plastic_C_R', 'slot': 7, 'channel': 5, 'nominal_v': 1312},
    {'name': 'plastic_D_L', 'slot': 7, 'channel': 6, 'nominal_v': 1331},
    {'name': 'plastic_D_R', 'slot': 7, 'channel': 7, 'nominal_v': 1448},
]

# What to leave the channels at when the scan finishes or is Ctrl-C'd:
#   'nominal'    -> ramp each channel back to its 'nominal_v' (operational)
#   'off'        -> power all scanned channels off
#   'hold'       -> leave them at the last scan voltage
END_ACTION = 'nominal'

# --- Output -----------------------------------------------------------------
# Data lands under here as <OUTPUT_ROOT>/<run label>/ with the HV+beam CSV.
OUTPUT_ROOT = os.path.expanduser('~/beam_july/scint_hv_scan')

# Beam state published by beam_watcher.py (read-only; no crate involvement).
BEAM_STATE_PATH = os.path.join(REPO_ROOT, 'config', 'beam_state.json')

# --- Logging / monitoring ---------------------------------------------------
MONITOR_INTERVAL_S = 5.0        # how often to log HV + beam to the CSV
KEEPALIVE_S = 10.0              # ping the crate this often (< ~15 s idle drop)

# --- Scan ladder (only used by scint_hv_scan.py in --scan mode) -------------
# Voltages (V) to step through, low -> high. The scan sets every channel in
# SCINT_CHANNELS to each step in turn.
# Two descending passes, 50 V final grid (9 points, ~90 min at current beam):
#   Pass 1 (coarse, 100 V):  1600 1500 1400 1300 1200
#   Pass 2 (50 V midpoints):      1550 1450 1350 1250
# Merged plateau curve = 1200..1600 in 50 V steps. Pass 1 alone already covers
# the full range, so an early stop still yields a usable (coarse) curve.
SCAN_VOLTAGES = [
    1600, 1500, 1400, 1300, 1200,   # Pass 1: coarse full-range
    1550, 1450, 1350, 1250,         # Pass 2: fill the midpoints
]

# Advance to the next voltage only once this many protons (in units of 1e10,
# matching beam_state.json's e10 fields) have been delivered at the current
# step. ~100,000 e10 ~= 10 min at current beam (beam_state protons_10min_e10
# ~= 105,000). Auto-stretches if beam weakens. Set to 0 for ramp-only advance.
PROTONS_PER_STEP_E10 = 100000.0

# Per-step safety / ramp behaviour.
RAMP_TOLERANCE_V = 1.5          # vmon within this of target = "ramped"
RAMP_TIMEOUT_S = 180            # abort a step if it will not ramp in this long
I0SET_UA = None                 # if set (µA), apply as the channel current limit
TRIP_S = None                   # if set (s), apply as the channel trip time
