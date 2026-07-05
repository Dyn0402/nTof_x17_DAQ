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

# ---------------------------------------------------------------------------
# N1081B-synchronised HV scan schedule (Ar/CF4/Iso 88/10/2)
#   A sequence of SCANS. Each scan is a full resist HV scan (identical set of
#   resist steps) at that scan's drift voltage. The N1081B .243 Section B module
#   config for each scan is applied by the standalone watcher
#   (n1081b/n1081b_scan_watcher.py) using the scan tag in each sub-run name;
#   the watcher holds the DAQ at each scan boundary (.pause_run) while it swaps
#   the module config. This file only sets the DAQ/HV side (drift + resist steps).
# ---------------------------------------------------------------------------
SUBRUN_MIN   = 5      # run time per sub-run (minutes)
OVERHEAD_MIN = 1      # per-subrun ramp poll + DAQ prep + 10 s inter-subrun wait
BOUNDARY_PAUSE_MIN = 1  # safety-floor pause AFTER the last sub-run of each scan
                        # (minutes). The watcher extends this via .pause_run while it
                        # swaps the module config, so even if the watcher is down the
                        # DAQ pauses rather than running a scan with the wrong config.

# Per-channel maximum resist voltages, card 5 channels 1-4 = detectors A/B/C/D.
# Every scan starts each detector at its own max and steps down from there.
RESIST_MAX = {'1': 540, '2': 535, '3': 535, '4': 515}

# FULL-precision resist-step pattern below each detector's own max (tuned on Det A;
# B/C/D follow the same shape so all four stay in lock-step, same # of steps).
# List of (step_V, until_offset_below_max): -10 V down to max-30, then -5 V down
# to max-50, then -10 V down to max-190.  -> 22 steps/scan, Det A 540 -> 350 V.
# Used for the delay=1000 reference scans (baseline, current, drift-change).
RESIST_STEP_PLAN = [(-10, 30), (-5, 50), (-10, 190)]

# REDUCED pattern for the delay-shifted scans: uniform -10 V, offset 5..145 below
# each detector's max (Det A 535 -> 395), 15 steps. Coarser/shorter to fit more scans.
REDUCED_OFFSETS = list(range(5, 146, 10))  # [5, 15, 25, ..., 145]

# Ordered scans: (scan_tag, drift_V, reduced?). The scan_tag is the first
# '_'-delimited token of every sub-run name in that scan and is the key the watcher
# schedule (config/n1081b_scan_schedule.json) uses to look up the N1081B module
# config (input delay ch1&2 + output enable ch1&2). scan01..scan11 run in this
# order; tags are position-based, so this list and the schedule JSON must agree.
# Front (scan01-07) by increasing |delay shift|; tail (per user): +300, drift
# change, -900, +500:
#   scan01 baseline OFF | scan02 current | scan03-07 -150/+150/-300/-450/-600 ns
#   scan08 +300 ns | scan09 current @ drift 400 V | scan10 -900 ns | scan11 +500 ns
SCANS = [
    ('scan01', 800, False),  # baseline, outputs OFF (delay 1000)
    ('scan02', 800, False),  # current           (delay 1000)
    ('scan03', 800, True),   # delay 850   (-150 ns)
    ('scan04', 800, True),   # delay 1150  (+150 ns)
    ('scan05', 800, True),   # delay 700   (-300 ns)
    ('scan06', 800, True),   # delay 550   (-450 ns)
    ('scan07', 800, True),   # delay 400   (-600 ns)
    ('scan08', 800, True),   # delay 1300  (+300 ns)
    ('scan09', 400, False),  # current, drift 400 V (delay 1000) — drift change
    ('scan10', 800, True),   # delay 100   (-900 ns)
    ('scan11', 800, True),   # delay 1500  (+500 ns)
]


def build_resist_offsets(reduced=False):
    """Offsets (V below each detector's max) for the resist steps of one scan.
    reduced=True -> the coarse uniform -10 V pattern; else the full-precision pattern."""
    if reduced:
        return list(REDUCED_OFFSETS)
    offsets = [0]
    cur = 0
    for step_v, until in RESIST_STEP_PLAN:
        inc = -step_v  # step_v is negative; inc is the positive offset increment
        while cur < until:
            cur += inc
            offsets.append(cur)
    return offsets


class Config(RunConfigBase):
    def __init__(self, config_path=None):
        if not config_path:
            self._set_defaults()

        super().__init__(config_path)

    def _set_defaults(self, config_path=None):
        self.run_name = 'run_9'
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
        self.resume = True  # True to resume an existing run: skip sub-runs already marked .subrun_complete.
        self.write_all_detectors_to_json = True  # Only when making run config json template. Maybe do always?
        # self.gas = 'Ar/CF4/CO2 45/40/15'  # Gas type for run
        # self.gas = 'Ar/CF4 90/10'  # Gas type for run
        # self.gas = 'Ar/CO2 70/30'  # Gas type for run
        self.gas = 'Ar/CF4/Iso 88/10/2'  # Gas type for run
        # self.gas = 'He/Eth 96.5/3.5'  # Gas type for run
        # self.gas = 'Ne/Iso 95/5'  # Gas type for run
        # self.beam_type = 'cosmics'
        self.beam_type = 'neutrons'
        # self.beam_type = 'cosmics+beam'
        # self.beam_type = 'bi-207'
        # self.beam_type = 'cs-137'
        # self.beam_type = 'sr-90'
        # self.target_type = 'carbon'
        # self.target_type = 'B4C - 2.5mm (thinner)'
        # self.target_type = 'B4C - 5mm (thicker)'
        # self.target_type = 'Lead'
        # self.target_type = 'empty target holder'
        self.target_type = 'none'
        # self.trigger = "Det 3 SiPM Wall + Det 3 Scint"
        self.trigger = "PS Pickup"

        self.dream_daq_info = {
            'ip': '192.168.10.8',
            'port': 1101,
            # 'daq_config_template_path': f'{self.base_out_dir}dream_config/Tcm_Mx17_May.cfg',
            'daq_config_template_path': f'{self.base_out_dir}dream_config/Tcm_Mx17_July.cfg',
            # 'daq_config_template_path': f'{self.base_out_dir}dream_config/Cosmics_Mx17_May.cfg',
            # 'daq_config_template_path': f'{self.base_out_dir}dream_config/Self_Trig_QA.cfg',
            # 'daq_config_template_path': f'{self.base_out_dir}dream_config/Self_Trig_det3_QA.cfg',

            # 'run_directory': f'{self.base_out_dir}/dream_run/{self.run_name}/',
            'run_directory': f'/home/mx17/july_dream/dream_run/{self.run_name}/',
            'data_out_dir': f'{self.run_out_dir}',
            'raw_daq_inner_dir': self.raw_daq_inner_dir,
            'n_samples_per_waveform': 400,  # Number of samples per waveform to configure in DAQ
            # 'n_samples_per_waveform': 32,  # Number of samples per waveform to configure in DAQ
            'go_timeout': 5 * 60,  # Seconds to wait for 'Go' response from RunCtrl before assuming failure
            'max_run_time_addition': 60 * 5,  # Seconds to add to requested run time before killing run
            'copy_on_fly': True,  # True to copy raw data to out dir during run, False to copy after run
            'batch_mode': True,  # Run Dream RunCtrl in batch mode. Not implemented for cosmic bench CPU.
            'zero_suppress': False,  # True to run in zero suppression mode, False to run in full readout mode
            'pedestals_dir': f'{self.base_out_dir}pedestals/',  # None to ignore, else top directory for pedestal runs
            'pedestals': 'latest',  # 'latest' for most recent, otherwise specify directory name, eg "pedestals_10-22-25_13-43-34"
            'latency': 3,  # Latency setting for DAQ in clock cycles
            'sample_period': 20,  # ns, sampling period
            # 'sample_period': 60,  # ns, sampling period
            'zs_check_sample': 4,  # Number of samples to read out beyond threshold crossing
            # True to auto-select the active FEUs in the .cfg from the included detectors' dream_feus maps.
            # Only the Sys Topo / Feu_RunCtrl_Id / NetChan_Ip lines for FEUs actually used by the included
            # detectors are left active; the rest are commented out. On each active Sys Topo line the per-
            # Dream roles are set to Dat for used connectors and Msk otherwise. nTof has no dedicated
            # trigger FEU (multiplicity coincidence), so trigger_feu stays None.
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

        # ----- N1081B-synchronised scan schedule (built from constants above) -----
        self.sub_runs = []

        # Each scan = the same set of resist steps (from each detector's max down
        # the RESIST_STEP_PLAN) at that scan's drift. The last sub-run of every scan
        # (except the final scan) carries a BOUNDARY_PAUSE_MIN safety-floor pause;
        # the watcher swaps the N1081B module config during that boundary.
        boundary_pause_s = int(round(BOUNDARY_PAUSE_MIN * 60))
        for s_idx, (tag, drift, reduced) in enumerate(SCANS):
            is_last_scan = (s_idx == len(SCANS) - 1)
            offsets = build_resist_offsets(reduced=reduced)
            last_step = len(offsets) - 1
            for k, off in enumerate(offsets):
                resists = {ch: v - off for ch, v in RESIST_MAX.items()}
                is_boundary = (k == last_step) and not is_last_scan
                self.sub_runs.append({
                    # tag MUST stay the first '_'-delimited token (watcher parses it).
                    'sub_run_name': f'{tag}_dr{drift}_A{resists["1"]}_{k:02d}',
                    'run_time': SUBRUN_MIN,  # Minutes
                    'post_pause_s': boundary_pause_s if is_boundary else 0,  # seconds; 0 = none
                    'hvs': {
                        '5': resists,  # Positive Resists (mx17_A/B/C/D on channels 1-4)
                        '9': {'0': drift, '1': drift, '2': drift, '3': drift},  # Negative Drifts
                    },
                })

        # ----- One-off no-trigger baseline control (inserted 2026-07-05) -----
        # A full resist scan at drift 800, max resists walking down in uniform -10 V steps
        # to max-190 (20 steps, 5 min each), run with the N1081B .243 SecB output ENABLED
        # but its input DISABLED ('ctrl' tag -> config/n1081b_scan_schedule.json). That
        # holds the DREAM trigger line at its normal ~3 V idle with no pulses — the clean
        # replacement for scan01's output-OFF control (which dropped the line to 0 V and
        # glitched an edge at each boundary). Inserted directly before the sub-run that was
        # running when we paused to add it (scan10 last step), so on a resume run it runs
        # first, then the interrupted scan10 step, then scan11. Remove this block (and the
        # 'ctrl' schedule entry) once the control run is done.
        CTRL_DRIFT = 800
        CTRL_OFFSETS = list(range(0, 191, 10))  # 0,10,...,190 -> Det A 540->350, 20 steps
        CTRL_BEFORE = 'scan10_dr800_A395_14'    # currently-running sub-run to precede
        ctrl_subs = []
        ctrl_last = len(CTRL_OFFSETS) - 1
        for k, off in enumerate(CTRL_OFFSETS):
            resists = {ch: v - off for ch, v in RESIST_MAX.items()}
            ctrl_subs.append({
                'sub_run_name': f'ctrl_dr{CTRL_DRIFT}_A{resists["1"]}_{k:02d}',
                'run_time': SUBRUN_MIN,  # 5 min
                # Watcher swaps SecB back (input ON) at the ctrl->scan10 boundary; pause is
                # the safety floor. Only the last ctrl sub-run is a boundary.
                'post_pause_s': boundary_pause_s if k == ctrl_last else 0,
                'hvs': {
                    '5': resists,
                    '9': {'0': CTRL_DRIFT, '1': CTRL_DRIFT, '2': CTRL_DRIFT, '3': CTRL_DRIFT},
                },
            })
        _ctrl_idx = next((i for i, sr in enumerate(self.sub_runs)
                          if sr['sub_run_name'] == CTRL_BEFORE), None)
        if _ctrl_idx is None:
            raise RuntimeError(f'CTRL_BEFORE sub-run {CTRL_BEFORE!r} not found; '
                               f'update it to the sub-run the ctrl scan should precede.')
        self.sub_runs[_ctrl_idx:_ctrl_idx] = ctrl_subs


        self.bench_geometry = {
            'board_thickness': 5,  # mm  Thickness of PCB for test boards  Guess!
        }

        self.included_detectors = ['mx17_A', 'mx17_B', 'mx17_C', 'mx17_D']

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
                    'x': -32.7,  # mm  tangential pinwheel shift (-X)
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
                    'z': -31.5,  # mm  tangential pinwheel shift (-Z)
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
                    'x': 34.6,  # mm  tangential pinwheel shift (+X)
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
                    'z': 31.0,  # mm  tangential pinwheel shift (+Z)
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
            {
                'name': 'scint_A',
                'det_type': 'scintillator_PMT',
                'det_center_coords': {  # Center of detector
                    'x': 0,  # mm
                    'y': 0,  # mm
                    'z': 10,  # mm
                },
                'det_orientation': {
                    'x': 0,  # deg  Rotation about x axis
                    'y': 0,  # deg  Rotation about y axis
                    'z': 0,  # deg  Rotation about z axis
                },
                'hv_channels': {
                    'bias': (8, 0),
                },
            },
            {
                'name': 'scint_B',
                'det_type': 'scintillator_PMT',
                'det_center_coords': {  # Center of detector
                    'x': 0,  # mm
                    'y': 0,  # mm
                    'z': 7,  # mm
                },
                'det_orientation': {
                    'x': 0,  # deg  Rotation about x axis
                    'y': 0,  # deg  Rotation about y axis
                    'z': 0,  # deg  Rotation about z axis
                },
                'hv_channels': {
                    'bias': (8, 1),
                },
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

    # Schedule summary — sanity-check timing and the per-scan HV setpoints.
    run_min = sum(sr['run_time'] for sr in config.sub_runs)
    n_sub = len(config.sub_runs)
    n_full = len(build_resist_offsets(reduced=False))
    n_red = len(build_resist_offsets(reduced=True))
    overhead_min = n_sub * OVERHEAD_MIN
    pause_min = sum(sr['post_pause_s'] for sr in config.sub_runs) / 60
    total_h = (run_min + overhead_min + pause_min) / 60
    a = RESIST_MAX['1']
    print(f'Gas: {config.gas}')
    print(f'Scans: {len(SCANS)}  (N1081B cfg per scan via watcher / config/n1081b_scan_schedule.json)')
    for tag, drift, reduced in SCANS:
        kind = f'reduced {n_red}st ({a - build_resist_offsets(True)[0]}->{a - build_resist_offsets(True)[-1]}V)' \
               if reduced else f'full {n_full}st ({a}->{a - build_resist_offsets(False)[-1]}V)'
        print(f'  {tag}: drift {drift} V, {kind}')
    print(f'Sub-runs: {n_sub}, {SUBRUN_MIN} min each  (Det A shown; B/C/D offset-matched from own max)')
    print(f'Run time: {run_min} min run + ~{overhead_min} min overhead + {pause_min:.0f} min boundary pauses '
          f'= ~{total_h:.2f} h')

    print('donzo')
