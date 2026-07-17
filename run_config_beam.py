#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on April 29 9:37 PM 2024
Created in PyCharm
Created as Cosmic_Bench_DAQ_Control/run_config_template.py

@author: Dylan Neff, Dylan
"""

from run_config_base import RunConfigBase

# ---------------------------------------------------------------------------
# Site configuration — edit here to change the data location
# ---------------------------------------------------------------------------
BASE_DISK     = '/mnt/data/x17/'
PROJECT       = 'beam_july'
BASE_DATA_DIR = f'{BASE_DISK}{PROJECT}/'

# ===========================================================================
# run_49 — OVERNIGHT SCINT-DOUBLES repeated HV scan in 95/5 gas (Mode 3,
# doubles only). Follows the run_48 qualification (~90 verified MM tracks per
# 5 min at 460/440 V, ~5 Hz avg trigger, latency 35 confirmed): same trigger
# and readout, now a repeated RESIST scan to map track yield/gain vs HV
# overnight. Ar/Iso 95/5 gas, 3He target, 20 mm Pb beamline filter.
# RESIST: A/B/C 480 -> 440 V in -5 V steps (9 points); det D 20 V below
# throughout (460 -> 420 V; 460 V max per spark-safety). Drift 800 V.
# 15 min per point; the full sweep (~2.4 h) repeats N_REPEATS times
# (~9.6 h) so each HV point gets sampled across the night (beam-condition
# variation averages out; sweeps are comparable to each other).
#
#   Mode 3 trigger, DOUBLES ONLY (RUN_MODES_2026-07.md §1.3 + rate_scan_2d
#   study 2026-07-16): M4.C = or_veto(lemo1 = Doubles = M4.B >=2-of-4 sector
#   coincidence), gated by the 30 ms N93B veto window; M4.D = OR(lemo1 = C-out).
#   Singles + flash + pulser all cut. run_48 measured ~5 Hz average (~1.5 k
#   events / 5 min), flash-type ~10 % of triggers — well inside DREAM budget.
#   Thresholds: walls at calibration nominal (13/14/13/14 mV), plastics -15 mV
#   (C/D saturate at -10, keep off the floor).
#   32 samples x 60 ns (1.92 us window), LATENCY 35: doubles-path track
#   clusters land at samples ~15-18 (run_48 subruns 00-06), window well framed.
#   STATIC board settings, applied ONCE before the run:
#     .venv/bin/python n1081b/trigger_mode.py scint --doubles
#   Verify with `.venv/bin/python n1081b/trigger_mode.py status` before
#   starting DAQ (expect "looks like mode: scint(doubles)").
#
#   Mesh grounded/disconnected — n1081b_scan stays 'off' (no inline
#   trigger/mesh modulation, no cycling; do NOT start n1081b_scan_watcher.py).
#
#   STRUCTURE: N_REPEATS x 9 HV points x 15 min (+1 min overhead each)
#   ~= 9.6 h. Sub-run names carry the HV point AND a global index, so resume
#   re-takes only the interrupted point. Track QA per sub-run:
#   ~/beam_july/analysis/scint_doubles/analyze_subrun.py + event_display.py.
# ===========================================================================
OVERHEAD_MIN = 1      # per-subrun ramp poll + DAQ prep + inter-subrun wait

SCAN_DRIFT    = 800   # V, drift, all four detectors (A/B/C/D = card 9 ch 0-3)
RESIST_TOP    = 480   # V, resist starting point (A/B/C); D = 460 (its safe max)
RESIST_STEP   = 5     # V, step DOWN between points
RESIST_BOTTOM = 440   # V, resist final point (A/B/C), inclusive
DET_D_OFFSET  = 20    # V, det D resist runs this many volts BELOW A/B/C
N_REPEATS     = 4     # full-sweep repetitions across the night (~2.4 h each)
SUBRUN_MIN    = 15    # minutes per sub-run (one HV point per sub-run)

POST_PAUSE_S = 0  # no inline scan control in play this run; nothing to re-apply between sub-runs.


def fmt_v(v):
    """Format a setpoint for sub-run names: 540, 537.5, 400 (no trailing .0)."""
    return f'{v:g}'


class Config(RunConfigBase):
    def __init__(self, config_path=None):
        if not config_path:
            self._set_defaults()

        super().__init__(config_path)

    def _set_defaults(self, config_path=None):
        self.run_name = 'run_49'  # Overnight scint-DOUBLES repeated HV scan (95/5 gas), resist A/B/C 480->440 V step -5 (D 20 V below, max 460), drift 800 V, 4 sweeps x 9 pts x 15 min; 20 mm Pb beam filter
        self.base_out_dir = BASE_DATA_DIR
        self.data_out_dir = f'{self.base_out_dir}runs/'
        self.run_out_dir = f'{self.data_out_dir}{self.run_name}/'
        self.raw_daq_inner_dir = 'raw_daq_data'
        self.decoded_root_inner_dir = 'decoded_root'
        self.detector_info_dir = f'{self.base_out_dir}config/detectors/'
        self.save_fdfs = True  # True to save FDF files, False to delete after decoding
        self.start_time = None
        self.process_on_fly = False  # True to process fdfs on the fly.
        self.power_off_hv_at_end = False  # True to power off all CAEN HV at the end of the run.
        self.resume = False  # Fresh run: do not skip any sub-runs.
                             # (2026-07-16: run_47 subrun _14 was re-taken via resume=True after a
                             # beam-off outage, then reset to False; see start-run gotcha memory.)
        self.n1081b_scan = 'off'  # No inline trigger/mesh modulation this run: the
                             # flash_random trigger is a STATIC board setting
                             # (n1081b/trigger_mode.py flash_random + M6.D Poisson pulser,
                             # applied once pre-run) and the mesh is grounded/disconnected,
                             # so there is nothing to toggle or cycle per sub-run.
        self.write_all_detectors_to_json = True  # Only when making run config json template. Maybe do always?
        # self.gas = 'Ar/CF4/CO2 45/40/15'  # Gas type for run
        # self.gas = 'Ar/CF4 90/10'  # Gas type for run
        # self.gas = 'Ar/CO2 70/30'  # Gas type for run
        self.gas = 'Ar/Iso 95/5'  # Gas type for run (gas-change run: changed from 90/10 on 2026-07-16)
        # self.gas = 'He/Eth 96.5/3.5'  # Gas type for run
        # self.gas = 'Ne/Iso 95/5'  # Gas type for run
        # self.beam_type = 'cosmics'
        self.beam_type = 'neutrons'  # noqa: keep
        # self.beam_type = 'cosmics+beam'
        # self.beam_type = 'bi-207'
        # self.beam_type = 'cs-137'
        # self.beam_type = 'sr-90'
        # self.target_type = 'carbon'
        # self.target_type = 'B4C - 2.5mm (thinner)'
        # self.target_type = 'B4C - 5mm (thicker)'
        # self.target_type = 'Lead'
        # self.target_type = 'empty target holder'
        # self.target_type = 'none'
        # self.target_type = 'Marex'
        # self.target_type = 'B4C - 2.5mm (thinner)'
        self.target_type = '3He'  # new 3He target installed 2026-07-15
        # Beamline filter (upstream of the target). Recorded 2026-07-16: 20 mm of
        # lead inserted into the beamline. 'none' when no filter is present.
        self.beam_filter = 'Pb 20 mm (beamline, upstream of target)'
        # self.trigger = "Det 3 SiPM Wall + Det 3 Scint"
        # self.trigger = "PS Pickup"
        self.trigger = ("External (N1081B M4.D out0): SCINT DOUBLES trigger "
                        "(M4.C = or_veto(lemo1 = Doubles = M4.B >=2-of-4 sector wall*scint "
                        "coincidence), gated by the 30 ms N93B window; M4.D = OR(lemo1 = "
                        "C-out); Singles/flash/pulser all cut) — Mode 3 of "
                        "RUN_MODES_2026-07.md, doubles-only, qualified by run_48 (~90 MM "
                        "tracks / 5 min on all four detectors at 460/440 V, ~5 Hz avg). "
                        "Static setup via n1081b/trigger_mode.py scint --doubles. Walls at "
                        "calibration nominal (13/14/13/14 mV), plastics -15 mV. Mesh "
                        "grounded/disconnected. 20 mm Pb filter in beamline. OVERNIGHT "
                        "REPEATED RESIST HV SCAN in 95/5 gas: A/B/C 480->440 V in -5 V "
                        "steps (det D 20 V below, 460->420 V, 460 V spark-safe max), drift "
                        "800 V; 9 points x 15 min per sweep, 4 sweeps (~9.6 h). 32 smp x "
                        "60 ns (1.92 us window), latency 35 (doubles-path track clusters "
                        "at samples ~15-18, run_48-verified).")

        self.dream_daq_info = {
            'ip': '192.168.10.8',
            'port': 1101,
            # External-trigger (TCM) template: gamma flash trigger fed in externally;
            # every detector is Dat (full 4-detector readout), no self-trigger FEU.
            'daq_config_template_path': f'{self.base_out_dir}dream_config/Tcm_Mx17_July.cfg',

            # 'run_directory': f'{self.base_out_dir}/dream_run/{self.run_name}/',
            'run_directory': f'/home/mx17/july_dream/dream_run/{self.run_name}/',
            'data_out_dir': f'{self.run_out_dir}',
            'raw_daq_inner_dir': self.raw_daq_inner_dir,
            'n_samples_per_waveform': 32,  # scint config (Mode 3): 32 x 60 ns = 1920 ns window
            'go_timeout': 5 * 60,  # Seconds to wait for 'Go' response from RunCtrl before assuming failure
            'max_run_time_addition': 60 * 5,  # Seconds to add to requested run time before killing run
            'copy_on_fly': True,  # True to copy raw data to out dir during run, False to copy after run
            'batch_mode': True,  # Run Dream RunCtrl in batch mode. Not implemented for cosmic bench CPU.
            'zero_suppress': False,  # True to run in zero suppression mode, False to run in full readout mode
            'pedestals_dir': f'{self.base_out_dir}pedestals/',  # None to ignore, else top directory for pedestal runs
            'pedestals': 'latest',  # 'latest' for most recent, otherwise specify directory name, eg "pedestals_10-22-25_13-43-34"
            'latency': 35,  # scint value (Mode 3, RUN_MODES_2026-07.md; MM pulse arrives
                            # ~1.4 us BEFORE the trigger => peak ~= latency - 24, i.e.
                            # samples ~11-13; window-start artifact smp 0-5. Calibrated on
                            # the SINGLES path — verify on the first doubles sub-run and
                            # shift 1:1 in samples if needed)
            'sample_period': 60,  # ns (20 or 60)
            # No daq_run_events cap: runs are purely time-based (5 min sub-runs), like run_15.
            'zs_check_sample': 4,  # Number of samples to read out beyond threshold crossing
            # Full 4-detector readout auto-derived from included detectors (all four = Dat). External
            # trigger, so no trigger FEU (nTof multiplicity path is unused here).
            'set_feus_from_detectors': True,
        }

        self.processor_info = {
            'ip': '192.168.10.8',
            'port': 1200,
            'run_dir': f'{self.run_out_dir}',
            'raw_daq_inner_dir': self.raw_daq_inner_dir,
            'decoded_root_inner_dir': self.decoded_root_inner_dir,
            'decode_path': '/home/dylan/CLionProjects/mm_strip_reconstruction/cmake-build-debug/decoder/decode',
            # 'convert_path': '/local/home/banco/dylan/decode/convert_vec_tree_to_array',
            'detector_info_dir': self.detector_info_dir,
            'out_type': 'both',  # 'vec', 'array', or 'both'
            'on-the-fly_timeout': 2  # hours or None If running on-the-fly, time out and die after this time.
        }

        self.hv_control_info = {
            'ip': '192.168.10.8',
            'port': 1100,
        }

        self.hv_info = {
            # 'ip': '192.168.10.199',
            # # # 'ip': '192.168.10.81',
            # 'username': 'admin',
            # 'password': 'admin',
            'ip': '128.141.177.244',
            'n_cards': 10,
            'n_channels_per_card': 12,
            'run_out_dir': self.run_out_dir,
            'hv_monitoring': True,  # True to monitor HV during run, False to not monitor
            'monitor_interval': 1,  # Seconds between HV monitoring
        }

        with open('hv_creds.txt') as f:
            lines = f.readlines()
            self.hv_info['username'] = lines[0].strip()
            self.hv_info['password'] = lines[1].strip()

        # ----- SUB-RUN BUILD (from constants above) -----
        # Single phase, static scint-doubles trigger (no scan tags — n1081b_scan='off',
        # so sub_run_name has no special leading token). REPEATED RESIST SCAN: each
        # sweep steps A/B/C from RESIST_TOP down to RESIST_BOTTOM by -RESIST_STEP with
        # det D DET_D_OFFSET volts below at every point; the sweep repeats N_REPEATS
        # times. Names carry sweep + HV point + global index k so resume re-takes only
        # the interrupted point. Drift SCAN_DRIFT for all four throughout.
        def _drift(v):
            return {'0': v, '1': v, '2': v, '3': v}  # card 9 ch 0-3 = drifts A/B/C/D

        def _resist(v):
            # card 5 ch 1-4 = det A/B/C/D; D runs DET_D_OFFSET volts below A/B/C
            return {'1': v, '2': v, '3': v, '4': v - DET_D_OFFSET}

        self.sub_runs = []

        k = 0
        for sweep in range(N_REPEATS):
            v = RESIST_TOP
            while v >= RESIST_BOTTOM - 1e-9:
                self.sub_runs.append({
                    'sub_run_name': (f'scintd_s{sweep}_dr{SCAN_DRIFT}_A{fmt_v(v)}'
                                     f'_D{fmt_v(v - DET_D_OFFSET)}_{k:02d}'),
                    'run_time': SUBRUN_MIN, 'post_pause_s': POST_PAUSE_S,
                    'hvs': {'5': _resist(v), '9': _drift(SCAN_DRIFT)},
                })
                k += 1
                v -= RESIST_STEP


        self.bench_geometry = {
            'board_thickness': 5,  # mm  Thickness of PCB for test boards  Guess!
        }

        # Detector D back in service 2026-07-12 (held 520 V through the gas change).
        self.included_detectors = [
            'mx17_A', 'mx17_B', 'mx17_C', 'mx17_D',
            'plastic_A_L', 'plastic_A_R', 'plastic_B_L', 'plastic_B_R',
            'plastic_C_L', 'plastic_C_R', 'plastic_D_L', 'plastic_D_R',
            'liquid_A', 'liquid_B', 'liquid_C', 'liquid_D',
        ]

        self.detectors = [
            {
                'name': 'mx17_A',
                'alias': 'mx17_3',
                'description': 'Bulked by Stephan June 15',
                'det_type': 'mx17',
                'resist_type': 'strip',
                'drift_gap': '30 mm',
                'frame_type': 'aluminum',  # carbon or aluminum
                'det_center_coords': {  # Center of detector at mesh plane (sim X/Z; y free, set 0)
                    'x': -16.35,  # mm  tangential pinwheel shift (-X)
                    'y': 0,  # mm
                    'z': 234.6,  # mm  +Z normal: mylar 204.5 + 30.1 (drift gap to mesh)
                },
                'det_orientation': {
                    'x': 0,  # deg  Rotation about x axis
                    'y': 0,  # deg  Rotation about y axis (faces +Z, sim Arm 2 unrotated)
                    'z': 0,  # deg  Rotation about z axis
                },
                'hv_channels': {
                    'drift': (9, 0),
                    'resist': (5, 1),
                },
                'mx_cards': '4 Good M1',
                'dream_feus': {
                    'x_1': (3, 1),  # Runs along x direction, indicates y hit location
                    'x_2': (3, 2),
                    'x_3': (3, 3),
                    'x_4': (3, 4),
                    'x_5': (3, 5),
                    'x_6': (3, 6),
                    'x_7': (3, 7),
                    'x_8': (3, 8),
                    'y_1': (4, 1),  # Runs along y direction, indicates x hit location
                    'y_2': (4, 2),
                    'y_3': (4, 3),
                    'y_4': (4, 4),
                    'y_5': (4, 5),
                    'y_6': (4, 6),
                    'y_7': (4, 7),
                    'y_8': (4, 8),
                },
                'dream_feu_orientation': {  # If connector is normal, inverted, rotated, or rotated_inverted
                    'x_1': 'inverted',
                    'x_2': 'inverted',
                    'x_3': 'inverted',
                    'x_4': 'inverted',
                    'x_5': 'inverted',
                    'x_6': 'inverted',
                    'x_7': 'inverted',
                    'x_8': 'inverted',
                    'y_1': 'inverted',
                    'y_2': 'inverted',
                    'y_3': 'inverted',
                    'y_4': 'inverted',
                    'y_5': 'inverted',
                    'y_6': 'inverted',
                    'y_7': 'inverted',
                    'y_8': 'inverted',
                },
                'dream_feu_cable_length': {  # Cable length from detector connector to FEU
                    'x_1': '1.5 m',
                    'x_2': '1.5 m',
                    'x_3': '1.5 m',
                    'x_4': '1.5 m',
                    'x_5': '1.5 m',
                    'x_6': '1.5 m',
                    'x_7': '1.5 m',
                    'x_8': '1.5 m',
                    'y_1': '1.5 m',
                    'y_2': '1.5 m',
                    'y_3': '1.5 m',
                    'y_4': '1.5 m',
                    'y_5': '1.5 m',
                    'y_6': '1.5 m',
                    'y_7': '1.5 m',
                    'y_8': '1.5 m',
                },
            },
            {
                'name': 'mx17_B',
                'alias': 'mx17_2',
                'description': 'Bulked by Arnaud June 12. Giant pillars on parts of the detector.',
                'det_type': 'mx17',
                'resist_type': 'strip',
                'drift_gap': '30 mm',
                'frame_type': 'aluminum',  # carbon or aluminum
                'det_center_coords': {  # Center of detector at mesh plane (sim X/Z; y free, set 0)
                    'x': -234.1,  # mm  -X normal: mylar 204.0 + 30.1 (drift gap to mesh)
                    'y': 0,  # mm
                    'z': -15.75,  # mm  tangential pinwheel shift (-Z)
                },
                'det_orientation': {
                    'x': 0,  # deg  Rotation about x axis
                    'y': -90,  # deg  Rotation about y axis (faces -X, sim Arm 1)
                    'z': 0,  # deg  Rotation about z axis
                },
                'hv_channels': {
                    'drift': (9, 1),
                    'resist': (5, 2),
                },
                'mx_cards': '4 Bad M1',
                'dream_feus': {
                    'x_1': (5, 1),  # Runs along x direction, indicates y hit location
                    'x_2': (5, 2),
                    'x_3': (5, 3),
                    'x_4': (5, 4),
                    'x_5': (5, 5),
                    'x_6': (5, 6),
                    'x_7': (5, 7),
                    'x_8': (5, 8),
                    'y_1': (6, 1),  # Runs along y direction, indicates x hit location
                    'y_2': (6, 2),
                    'y_3': (6, 3),
                    'y_4': (6, 4),
                    'y_5': (6, 5),
                    'y_6': (6, 6),
                    'y_7': (6, 7),
                    'y_8': (6, 8),
                },
                'dream_feu_orientation': {  # If connector is normal, inverted, rotated, or rotated_inverted
                    'x_1': 'inverted',
                    'x_2': 'inverted',
                    'x_3': 'inverted',
                    'x_4': 'inverted',
                    'x_5': 'inverted',
                    'x_6': 'inverted',
                    'x_7': 'inverted',
                    'x_8': 'inverted',
                    'y_1': 'inverted',
                    'y_2': 'inverted',
                    'y_3': 'inverted',
                    'y_4': 'inverted',
                    'y_5': 'inverted',
                    'y_6': 'inverted',
                    'y_7': 'inverted',
                    'y_8': 'inverted',
                },
                'dream_feu_cable_length': {  # Cable length from detector connector to FEU
                    'x_1': '1.5 m',
                    'x_2': '1.5 m',
                    'x_3': '1.5 m',
                    'x_4': '1.5 m',
                    'x_5': '1.5 m',
                    'x_6': '1.5 m',
                    'x_7': '1.5 m',
                    'x_8': '1.5 m',
                    'y_1': '1.5 m',
                    'y_2': '1.5 m',
                    'y_3': '1.5 m',
                    'y_4': '1.5 m',
                    'y_5': '1.5 m',
                    'y_6': '1.5 m',
                    'y_7': '1.5 m',
                    'y_8': '1.5 m',
                },
            },
            {
                'name': 'mx17_C',
                'alias': 'mx17_6',
                'description': 'Bulked by Stephan June 24 (?). Was board D. Stephan redid the lamination after first '
                               'layer had wrinkles a few times until good. In the end, a column of waves in the mesh '
                               'and maybe a spot with no pillar caps.',
                'det_type': 'mx17',
                'resist_type': 'strip',
                'drift_gap': '30 mm',
                'frame_type': 'aluminum',  # carbon or aluminum
                'det_center_coords': {  # Center of detector at mesh plane (sim X/Z; y free, set 0)
                    'x': 17.3,  # mm  tangential pinwheel shift (+X)
                    'y': 0,  # mm
                    'z': -234.6,  # mm  -Z normal: mylar 204.5 + 30.1 (drift gap to mesh)
                },
                'det_orientation': {
                    'x': 0,  # deg  Rotation about x axis
                    'y': 180,  # deg  Rotation about y axis (faces -Z, sim Arm 3)
                    'z': 0,  # deg  Rotation about z axis
                },
                'hv_channels': {
                    'drift': (9, 2),
                    'resist': (5, 3),
                },
                'mx_cards': '4 Bad M1',
                'dream_feus': {
                    'x_1': (7, 1),  # Runs along x direction, indicates y hit location
                    'x_2': (7, 2),
                    'x_3': (7, 3),
                    'x_4': (7, 4),
                    'x_5': (7, 5),
                    'x_6': (7, 6),
                    'x_7': (7, 7),
                    'x_8': (7, 8),
                    'y_1': (8, 1),  # Runs along y direction, indicates x hit location
                    'y_2': (8, 2),
                    'y_3': (8, 3),
                    'y_4': (8, 4),
                    'y_5': (8, 5),
                    'y_6': (8, 6),
                    'y_7': (8, 7),
                    'y_8': (8, 8),
                },
                'dream_feu_orientation': {  # If connector is normal, inverted, rotated, or rotated_inverted
                    'x_1': 'inverted',
                    'x_2': 'inverted',
                    'x_3': 'inverted',
                    'x_4': 'inverted',
                    'x_5': 'inverted',
                    'x_6': 'inverted',
                    'x_7': 'inverted',
                    'x_8': 'inverted',
                    'y_1': 'inverted',
                    'y_2': 'inverted',
                    'y_3': 'inverted',
                    'y_4': 'inverted',
                    'y_5': 'inverted',
                    'y_6': 'inverted',
                    'y_7': 'inverted',
                    'y_8': 'inverted',
                },
                'dream_feu_cable_length': {  # Cable length from detector connector to FEU
                    'x_1': '1.5 m',
                    'x_2': '1.5 m',
                    'x_3': '1.5 m',
                    'x_4': '1.5 m',
                    'x_5': '1.5 m',
                    'x_6': '1.5 m',
                    'x_7': '1.5 m',
                    'x_8': '1.5 m',
                    'y_1': '1.5 m',
                    'y_2': '1.5 m',
                    'y_3': '1.5 m',
                    'y_4': '1.5 m',
                    'y_5': '1.5 m',
                    'y_6': '1.5 m',
                    'y_7': '1.5 m',
                    'y_8': '1.5 m',
                },
            },
            {
                'name': 'mx17_D',
                'alias': 'mx17_7',
                'description': 'Bulked by Stephan in batch of 3 on June 22. Was board B. Had one or two bubbles, but '
                               'appears that the pillars underneath were still there, so just no caps',
                'det_type': 'mx17',
                'resist_type': 'strip',
                'drift_gap': '30 mm',
                'frame_type': 'aluminum',  # carbon or aluminum
                'det_center_coords': {  # Center of detector at mesh plane (sim X/Z; y free, set 0)
                    'x': 234.1,  # mm  +X normal: mylar 204.0 + 30.1 (drift gap to mesh)
                    'y': 0,  # mm
                    'z': 15.5,  # mm  tangential pinwheel shift (+Z)
                },
                'det_orientation': {
                    'x': 0,  # deg  Rotation about x axis
                    'y': 90,  # deg  Rotation about y axis (faces +X, sim Arm 0)
                    'z': 0,  # deg  Rotation about z axis
                },
                'hv_channels': {
                    'drift': (9, 3),
                    'resist': (5, 4),
                },
                'mx_cards': '4 Bad M1',
                'dream_feus': {
                    'x_1': (1, 1),  # Runs along x direction, indicates y hit location
                    'x_2': (1, 2),
                    'x_3': (1, 3),
                    'x_4': (1, 4),
                    'x_5': (1, 5),
                    'x_6': (1, 6),
                    'x_7': (1, 7),
                    'x_8': (1, 8),
                    'y_1': (2, 1),  # Runs along y direction, indicates x hit location
                    'y_2': (2, 2),
                    'y_3': (2, 3),
                    'y_4': (2, 4),
                    'y_5': (2, 5),
                    'y_6': (2, 6),
                    'y_7': (2, 7),
                    'y_8': (2, 8),
                },
                'dream_feu_orientation': {  # If connector is normal, inverted, rotated, or rotated_inverted
                    'x_1': 'inverted',
                    'x_2': 'inverted',
                    'x_3': 'inverted',
                    'x_4': 'inverted',
                    'x_5': 'inverted',
                    'x_6': 'inverted',
                    'x_7': 'inverted',
                    'x_8': 'inverted',
                    'y_1': 'inverted',
                    'y_2': 'inverted',
                    'y_3': 'inverted',
                    'y_4': 'inverted',
                    'y_5': 'inverted',
                    'y_6': 'inverted',
                    'y_7': 'inverted',
                    'y_8': 'inverted',
                },
                'dream_feu_cable_length': {  # Cable length from detector connector to FEU
                    'x_1': '2 m',
                    'x_2': '2 m',
                    'x_3': '2 m',
                    'x_4': '2 m',
                    'x_5': '2 m',
                    'x_6': '2 m',
                    'x_7': '2 m',
                    'x_8': '2 m',
                    'y_1': '2 m',
                    'y_2': '2 m',
                    'y_3': '2 m',
                    'y_4': '2 m',
                    'y_5': '2 m',
                    'y_6': '2 m',
                    'y_7': '2 m',
                    'y_8': '2 m',
                },
            },
            # =================================================================
            # SCINTILLATOR STACK (per arm, behind each Micromegas: MM -> SiPM wall
            # -> plastics -> 1 liquid layer).  Geometry taken from the Geant sim
            # ~/CLionProjects/MX17_Full_Geant (GEOMETRY_COORDINATE_CONVENTION.md
            # §5 stack + SimConfig.hh dims; stack flip 2026-07-15, single LS layer).
            # Rough placement for now — refine positions later.
            #
            #   Coords: same frame as the mx17 detectors.  Each layer sits along the
            #   arm outward normal at a depth measured from the MM drift-mylar FRONT
            #   face (w=0): plastics center 225.72 mm, liquid center 300.76 mm.
            #   Plastics + LS inherit the per-arm pinwheel tangential shift (centered
            #   on the MM), so their tangential offset equals the mx17 value.
            #   Plastics are two 20x30x2.5 cm bars side-by-side, split +/-101.72 mm
            #   along the arm's in-plane tangent (uHat); L = detn 1, R = detn 2
            #   ("left/right seen from the back", per mx_july_beam_qa).
            #
            #   ntof DAQ mapping (mx_july_beam_qa trees): plastics -> PSS{A,B,C,D}
            #   with detn 1=L / 2=R; liquids -> LIQ{A,B,C,D} (single PMT per arm).
            #
            #   HV: NOT included yet — TODO add scintillator PMT bias later.  The CAEN
            #   card/channel and the 2026-07-16 GUI setpoint are noted per detector so
            #   they are easy to wire in (plastics card 07, liquids card 08).  The 4
            #   liquid PMTs are currently NOT connected.
            # =================================================================

            # ----- Plastic scintillators (card 07): 2 bars/arm, L = detn 1, R = detn 2 -----
            {
                'name': 'plastic_A_L',
                'det_type': 'scintillator_PMT',
                'scint_medium': 'plastic (PVT)',
                'description': '20x30x2.5 cm plastic bar, left (seen from back)',
                'det_center_coords': {'x': -118.07, 'y': 0, 'z': 430.22},  # mm
                'det_orientation': {'x': 0, 'y': 0, 'z': 0},  # deg (+Z normal, arm A)
                # HV TODO: add later — CAEN 07.000 PLASTIC_A_L, ~1325 V (2026-07-16 GUI)
                'ntof_daq': {'tree': 'PSSA', 'detn': 1},
            },
            {
                'name': 'plastic_A_R',
                'det_type': 'scintillator_PMT',
                'scint_medium': 'plastic (PVT)',
                'description': '20x30x2.5 cm plastic bar, right (seen from back)',
                'det_center_coords': {'x': 85.37, 'y': 0, 'z': 430.22},  # mm
                'det_orientation': {'x': 0, 'y': 0, 'z': 0},  # deg (+Z normal, arm A)
                # HV TODO: add later — CAEN 07.001 PLASTIC_A_R, ~1275 V (2026-07-16 GUI)
                'ntof_daq': {'tree': 'PSSA', 'detn': 2},
            },
            {
                'name': 'plastic_B_L',
                'det_type': 'scintillator_PMT',
                'scint_medium': 'plastic (PVT)',
                'description': '20x30x2.5 cm plastic bar, left (seen from back)',
                'det_center_coords': {'x': -429.72, 'y': 0, 'z': -117.47},  # mm
                'det_orientation': {'x': 0, 'y': -90, 'z': 0},  # deg (-X normal, arm B)
                # HV TODO: add later — CAEN 07.002 PLASTIC_B_L, ~1325 V (2026-07-16 GUI)
                'ntof_daq': {'tree': 'PSSB', 'detn': 1},
            },
            {
                'name': 'plastic_B_R',
                'det_type': 'scintillator_PMT',
                'scint_medium': 'plastic (PVT)',
                'description': '20x30x2.5 cm plastic bar, right (seen from back)',
                'det_center_coords': {'x': -429.72, 'y': 0, 'z': 85.97},  # mm
                'det_orientation': {'x': 0, 'y': -90, 'z': 0},  # deg (-X normal, arm B)
                # HV TODO: add later — CAEN 07.003 PLASTIC_B_R, ~1300 V (2026-07-16 GUI)
                'ntof_daq': {'tree': 'PSSB', 'detn': 2},
            },
            {
                'name': 'plastic_C_L',
                'det_type': 'scintillator_PMT',
                'scint_medium': 'plastic (PVT)',
                'description': '20x30x2.5 cm plastic bar, left (seen from back)',
                'det_center_coords': {'x': 119.02, 'y': 0, 'z': -430.22},  # mm
                'det_orientation': {'x': 0, 'y': 180, 'z': 0},  # deg (-Z normal, arm C)
                # HV TODO: add later — CAEN 07.004 PLASTIC_C_L, ~1300 V (2026-07-16 GUI)
                'ntof_daq': {'tree': 'PSSC', 'detn': 1},
            },
            {
                'name': 'plastic_C_R',
                'det_type': 'scintillator_PMT',
                'scint_medium': 'plastic (PVT)',
                'description': '20x30x2.5 cm plastic bar, right (seen from back)',
                'det_center_coords': {'x': -84.42, 'y': 0, 'z': -430.22},  # mm
                'det_orientation': {'x': 0, 'y': 180, 'z': 0},  # deg (-Z normal, arm C)
                # HV TODO: add later — CAEN 07.005 PLASTIC_C_R, ~1300 V (2026-07-16 GUI)
                'ntof_daq': {'tree': 'PSSC', 'detn': 2},
            },
            {
                'name': 'plastic_D_L',
                'det_type': 'scintillator_PMT',
                'scint_medium': 'plastic (PVT)',
                'description': '20x30x2.5 cm plastic bar, left (seen from back)',
                'det_center_coords': {'x': 429.72, 'y': 0, 'z': 117.22},  # mm
                'det_orientation': {'x': 0, 'y': 90, 'z': 0},  # deg (+X normal, arm D)
                # HV TODO: add later — CAEN 07.006 PLASTIC_D_L, ~1300 V (2026-07-16 GUI)
                'ntof_daq': {'tree': 'PSSD', 'detn': 1},
            },
            {
                'name': 'plastic_D_R',
                'det_type': 'scintillator_PMT',
                'scint_medium': 'plastic (PVT)',
                'description': '20x30x2.5 cm plastic bar, right (seen from back)',
                'det_center_coords': {'x': 429.72, 'y': 0, 'z': -86.22},  # mm
                'det_orientation': {'x': 0, 'y': 90, 'z': 0},  # deg (+X normal, arm D)
                # HV TODO: add later — CAEN 07.007 PLASTIC_D_R, ~1300 V (2026-07-16 GUI)
                'ntof_daq': {'tree': 'PSSD', 'detn': 2},
            },

            # ----- Liquid scintillators (card 08): single LS layer per arm, one PMT -----
            {
                'name': 'liquid_A',
                'det_type': 'scintillator_PMT',
                'scint_medium': 'liquid (LAB)',
                'description': 'single 45x45x2 cm LAB layer in CFRP box, behind the plastics',
                'det_center_coords': {'x': -16.35, 'y': 0, 'z': 505.26},  # mm
                'det_orientation': {'x': 0, 'y': 0, 'z': 0},  # deg (+Z normal, arm A)
                # HV TODO: add later — CAEN 08.000 LIQUID_A, ~2000 V (2026-07-16 GUI); PMT not connected
                'ntof_daq': {'tree': 'LIQA', 'detn': 1},
            },
            {
                'name': 'liquid_B',
                'det_type': 'scintillator_PMT',
                'scint_medium': 'liquid (LAB)',
                'description': 'single 45x45x2 cm LAB layer in CFRP box, behind the plastics',
                'det_center_coords': {'x': -504.76, 'y': 0, 'z': -15.75},  # mm
                'det_orientation': {'x': 0, 'y': -90, 'z': 0},  # deg (-X normal, arm B)
                # HV TODO: add later — CAEN 08.001 LIQUID_B, ~2000 V (2026-07-16 GUI); PMT not connected
                'ntof_daq': {'tree': 'LIQB', 'detn': 1},
            },
            {
                'name': 'liquid_C',
                'det_type': 'scintillator_PMT',
                'scint_medium': 'liquid (LAB)',
                'description': 'single 45x45x2 cm LAB layer in CFRP box, behind the plastics',
                'det_center_coords': {'x': 17.3, 'y': 0, 'z': -505.26},  # mm
                'det_orientation': {'x': 0, 'y': 180, 'z': 0},  # deg (-Z normal, arm C)
                # HV TODO: add later — CAEN 08.002 LIQUID_C, ~2000 V (2026-07-16 GUI); PMT not connected
                'ntof_daq': {'tree': 'LIQC', 'detn': 1},
            },
            {
                'name': 'liquid_D',
                'det_type': 'scintillator_PMT',
                'scint_medium': 'liquid (LAB)',
                'description': 'single 45x45x2 cm LAB layer in CFRP box, behind the plastics',
                'det_center_coords': {'x': 504.76, 'y': 0, 'z': 15.5},  # mm
                'det_orientation': {'x': 0, 'y': 90, 'z': 0},  # deg (+X normal, arm D)
                # HV TODO: add later — CAEN 08.003 LIQUID_D, ~2000 V (2026-07-16 GUI); PMT not connected
                'ntof_daq': {'tree': 'LIQD', 'detn': 1},
            },

        ]

        if not self.write_all_detectors_to_json:
            self.detectors = [det for det in self.detectors if det['name'] in self.included_detectors]

        # Derive the active FEUs (and their used connectors) from the included detectors so
        # dream_daq_control can enable only those FEUs in the .cfg and set per-Dream roles.
        # Derived from the included subset explicitly so it works whether or not self.detectors
        # was already filtered above (write_all_detectors_to_json defaults True for nTof).
        if self.dream_daq_info.get('set_feus_from_detectors', False):
            feu_connectors = self.get_active_feu_connectors()
            if feu_connectors:
                self.dream_daq_info['included_feus'] = sorted(feu_connectors)
                self.dream_daq_info['feu_connectors'] = feu_connectors
                self.dream_daq_info['trigger_feu'] = None  # nTof triggers on multiplicity, no trigger FEU
            else:
                # No included detector exposes dream_feus (e.g. scintillator-only run). Leave the
                # template's FEU selection untouched rather than commenting out every FEU.
                print('set_feus_from_detectors is on but no included detector has dream_feus; '
                      'leaving the template FEU selection unchanged.')

    def get_active_feu_connectors(self):
        """Map each FEU used by the included detectors to the sorted list of its used connectors.

        Each dream_feus value is a (feu_number, connector) tuple. Connectors are 1-based (1..8) and
        correspond to FEU Dream indices 0..7 (Dream index = connector - 1). Detectors without a
        dict-valued dream_feus map (e.g. PMT scintillators) carry no FEU/connector numbers and are
        skipped. Restricted to included_detectors so it is correct even when self.detectors still
        holds the full list.
        """
        included = [det for det in self.detectors if det['name'] in self.included_detectors]
        feu_connectors = {}
        for det in included:
            dream_feus = det.get('dream_feus')
            if not isinstance(dream_feus, dict):
                continue
            for mapping in dream_feus.values():
                if isinstance(mapping, (tuple, list)) and len(mapping) >= 2:
                    feu, connector = int(mapping[0]), int(mapping[1])
                    feu_connectors.setdefault(feu, set()).add(connector)
        return {feu: sorted(conns) for feu, conns in feu_connectors.items()}

    def get_active_feus(self):
        """Sorted FEU numbers used by the included detectors (keys of get_active_feu_connectors)."""
        return sorted(self.get_active_feu_connectors())

if __name__ == '__main__':
    out_run_dir = 'config/json_run_configs/'

    config_name = 'run_config_beam.json'

    config = Config()

    config.write_to_file(f'{out_run_dir}{config_name}')

    # ----- Wall-clock schedule ------------------------------------------------
    # Set the intended start time here to project when each phase begins/ends.
    from datetime import datetime, timedelta
    START = datetime.now()  # projection only; run starts when daq_control is launched

    import os as _os
    ns = config.dream_daq_info['n_samples_per_waveform']
    sp = config.dream_daq_info['sample_period']
    lat = config.dream_daq_info['latency']
    n_pts = len(config.sub_runs)
    print(f'Gas: {config.gas}   Beam: {config.beam_type}   Target: {config.target_type}')
    print(f'Beam filter: {getattr(config, "beam_filter", "none")}')
    print(f'Run: {config.run_name}  — OVERNIGHT scint-DOUBLES repeated HV scan, all 4 detectors')
    print(f'Trigger: {config.trigger}')
    print(f'DAQ: {ns} smp x {sp} ns = {ns*sp} ns window, latency {lat}; RESIST scan '
          f'{RESIST_TOP}->{RESIST_BOTTOM} V step -{RESIST_STEP} V for A/B/C '
          f'(det D {RESIST_TOP-DET_D_OFFSET}->{RESIST_BOTTOM-DET_D_OFFSET} V), drift '
          f'{SCAN_DRIFT} V; {N_REPEATS} sweeps x {n_pts//N_REPEATS} pts x {SUBRUN_MIN} min '
          f'~= {n_pts*(SUBRUN_MIN+OVERHEAD_MIN)/60:.1f} h.')
    print('*** PRE-RUN: apply the STATIC scint-doubles trigger once — '
          '.venv/bin/python n1081b/trigger_mode.py scint --doubles (M4.C = or_veto(lemo1 = '
          'Doubles), 30 ms N93B veto gate; M4.D = OR(lemo1 = C-out); Singles/flash/pulser '
          'cut). Verify with n1081b/trigger_mode.py status before starting DAQ (expect '
          '"scint(doubles)"). Mesh grounded — nothing else to set. n1081b_scan is "off": no '
          'inline trigger/mesh modulation. Do NOT run n1081b_scan_watcher.py during the run. '
          'Track QA per finished sub-run: ~/beam_july/analysis/scint_doubles/'
          'analyze_subrun.py + event_display.py. ***')
    print(f'resume={getattr(config, "resume", False)}: sub-runs with a .subrun_complete '
          'marker are shown [done] and skipped.\n')

    # Walk the sub-runs, assigning wall-clock start/end to the ones that will
    # actually RUN (resume skips completed markers). Times are for remaining work.
    t = START
    remaining_min = 0
    counts = {'done': 0, 'run': 0}
    print(f'{"sub-run":30s} {"len":>8s}  {"start":>7s} -> {"end":>7s}')
    print('-' * 60)
    for sr in config.sub_runs:
        name = sr['sub_run_name']
        marker = _os.path.join(config.run_out_dir, name, '.subrun_complete')
        done = getattr(config, 'resume', False) and _os.path.exists(marker)
        if done:
            counts['done'] += 1
            print(f'{name:30s} {"[done]":>8s}   (skipped by resume)')
            continue
        counts['run'] += 1
        dur = sr['run_time'] + OVERHEAD_MIN
        remaining_min += dur
        end = t + timedelta(minutes=dur)
        print(f'{name:30s} {str(sr["run_time"])+"m":>8s}  {t:%H:%M} -> {end:%H:%M}')
        t = end

    print('-' * 60)
    print(f'\nResume: {counts["done"]} sub-runs done/skipped, {counts["run"]} to run.')
    print(f'Remaining wall-clock: {remaining_min} min = {remaining_min/60:.2f} h  '
          f'(start {START:%a %H:%M} -> end {t:%a %H:%M})')
    print('donzo')
