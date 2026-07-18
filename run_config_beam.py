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
# Gamma-flash-triggered DAQ-recovery scan (Ar/Iso 95/5).  ALL FOUR detectors read out.
#
#   Successor to the run_13/16/17 self-trigger attempts, which had trouble.  Instead
#   of self-triggering, the DAQ is now EXTERNALLY triggered (TCM / N1081B): on every
#   1.2 s machine cycle it fires on the gamma flash itself, and then again at a RANDOM
#   time within a 30 ms window after the flash.  Sampling the post-flash window at
#   random times maps out WHEN the baseline stops being flat (DAQ recovered) and, if
#   we are lucky, catches real detector signals returning.
#
#   Window: 32 samples x 60 ns = 1920 ns per event.  The 60 ns period is deliberate
#   (wider window than 20 ns) at the cost of time resolution; 32 samples kept short.
#
#   Trigger + full 4-detector readout come from the external-trigger TCM template
#   (Tcm_Mx17_July.cfg) with set_feus_from_detectors=True (all four detectors = Dat,
#   no self-trigger FEU role).  Same DAQ path the run_15 beam scan used.
#
#   Sequence (see SUB-RUN BUILD below):
#     1. "gas_change" settling sub-run: 2 h at drift 800 V, resists at max (all channels).
#     2. A resist scan per drift, run in order DRIFT_SCAN_ORDER.  Each scan steps all
#        four detectors together from their own max down in -5 V steps for 10 points;
#        the first 6 points run 30 min, the last 4 run 10 min (spend the most time near
#        max gain).  800 V is the "good" scan; 600 V precedes it, 400 V follows.
# ===========================================================================
OVERHEAD_MIN = 1      # per-subrun ramp poll + DAQ prep + 10 s inter-subrun wait

# Per-channel maximum resist voltages, card 5 channels 1-4 = detectors A/B/C/D.
# Every scan starts each detector at its own max and steps down from there in lock-step.
RESIST_MAX = {'1': 480, '2': 480, '3': 460, '4': 445}

# ----- Resist scan profile (all drift scans share this per-point timing) -----
# Each scan steps all four detectors together from their own max down in -RESIST_STEP_V
# steps for N_POINTS points.  First N_LONG points run long, the rest short.
RESIST_STEP_V    = 5     # step size (V) below each detector's max
N_POINTS         = 10    # resist points per scan (max, max-5, ... max-45)
N_LONG           = 6     # first N_LONG points run SUBRUN_MIN_LONG, the rest SUBRUN_MIN_SHORT
SUBRUN_MIN_LONG  = 30    # minutes per point, first N_LONG points
SUBRUN_MIN_SHORT = 10    # minutes per point, remaining points

# Drift scans, run in this order.  800 V is the "good" scan; 600 V before, 400 V after.
DRIFT_SCAN_ORDER = [600, 800, 400]

# ----- Leading gas-exchange / settling sub-run -----
GAS_CHANGE_MIN    = 120   # 2 h settle
GAS_CHANGE_OFFSET = 0     # resists held at max (all channels) during settle
GAS_CHANGE_DRIFT  = 800   # drift held at the good operating point


def build_resist_offsets(step_v, n_points):
    """Offsets (V below each detector's max) for a scan: [0, step, 2*step, ...] (n_points)."""
    return [step_v * k for k in range(n_points)]


def scan_point_minutes(k):
    """Run time (minutes) for the k-th resist point: long for the first N_LONG, then short."""
    return SUBRUN_MIN_LONG if k < N_LONG else SUBRUN_MIN_SHORT


def fmt_v(v):
    """Format a setpoint for sub-run names: 540, 537.5, 400 (no trailing .0)."""
    return f'{v:g}'


# ---------------------------------------------------------------------------
# SiPM trigger wall (structure-centered): 16 read-out bars per arm.
# ---------------------------------------------------------------------------
# Geometry from the Geant sim (~/CLionProjects/MX17_Full_Geant, SimConfig.hh /
# DetectorConstruction.cc).  Bars run along the beam (v); the array runs along
# the in-plane tangent (u), centered on the mechanical STRUCTURE (u=0, NOT the
# pinwheel-shifted MM).  Of the 20 bars across the 50 cm wall, 16 are read out,
# the window shifted 1 bar toward the MM -> instrumented bars i=1..16 with
# u_i = SIPM_BAR_W_MM*(i - (SIPM_N_BARS-1)/2).  Scint-plane depth from the mylar
# front face = sipm_front(110) + container(35)/2 = 127.5 mm.
SIPM_N_BARS     = 20        # total bars across the wall
SIPM_N_READOUT  = 16        # instrumented bars
SIPM_SHIFT_BARS = 1         # read-out window shift toward the MM [bars]
SIPM_BAR_W_MM   = 25.0      # bar pitch / width (u) [mm]
SIPM_DEPTH_MM   = 127.5     # scint-plane depth from mylar front face [mm]

# Per-arm mapping from a tangential offset u (mm, along +uHat) and the fixed
# scint-plane depth to world (x, z), plus the arm outward-normal orientation.
# Arms: A(+Z), B(-X), C(-Z), D(+X); same frame/rotations as the mx17 detectors.
_SIPM_ARM_PLACE = {
    'A': (lambda u: {'x': u,      'y': 0, 'z': 332.0}, 0),     # +Z normal
    'B': (lambda u: {'x': -331.5, 'y': 0, 'z': u},    -90),    # -X normal
    'C': (lambda u: {'x': -u,     'y': 0, 'z': -332.0}, 180),  # -Z normal
    'D': (lambda u: {'x': 331.5,  'y': 0, 'z': -u},    90),    # +X normal
}


def build_sipm_wall_detectors():
    """Return the SiPM trigger-wall bar detectors (16 per arm, structure-centered).

    Bar index i runs 1..SIPM_N_READOUT (the read-out window, shifted toward the
    MM); its tangential offset from the structure centre is
    u_i = SIPM_BAR_W_MM*(i - (SIPM_N_BARS-1)/2), matching the sim's bar loop.
    HV / ntof-DAQ channel mapping are TODO (added with the rest in the next pass).
    """
    dets = []
    for arm, (place, orient_y) in _SIPM_ARM_PLACE.items():
        for i in range(1, SIPM_N_READOUT + 1):
            u = SIPM_BAR_W_MM * (i - (SIPM_N_BARS - 1) / 2.0)  # mm along +uHat
            dets.append({
                'name': f'sipm_{arm}_{i:02d}',
                'det_type': 'scintillator_SiPM',
                'scint_medium': 'plastic (PVT)',
                'description': f'SiPM trigger-wall bar {i}/16 (25x500 mm), structure-centered',
                'det_center_coords': place(u),  # mm  bar center (scint plane)
                'det_orientation': {'x': 0, 'y': orient_y, 'z': 0},  # deg (arm outward normal)
                # HV / ntof_daq mapping: TODO (next pass)
            })
    return dets


class Config(RunConfigBase):
    def __init__(self, config_path=None):
        if not config_path:
            self._set_defaults()

        super().__init__(config_path)

    def _set_defaults(self, config_path=None):
        self.run_name = 'run_18'  # Gamma-flash externally-triggered DAQ-recovery scan (flash + random 30 ms window)
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
        self.resume = False  # True to resume an existing run: skip sub-runs already marked .subrun_complete.
        self.write_all_detectors_to_json = True  # Only when making run config json template. Maybe do always?
        # self.gas = 'Ar/CF4/CO2 45/40/15'  # Gas type for run
        # self.gas = 'Ar/CF4 90/10'  # Gas type for run
        # self.gas = 'Ar/CO2 70/30'  # Gas type for run
        self.gas = 'Ar/Iso 95/5'  # Gas type for run
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
        self.target_type = 'none'
        # self.trigger = "Det 3 SiPM Wall + Det 3 Scint"
        # self.trigger = "PS Pickup"
        self.trigger = ("External (TCM/N1081B): gamma flash on each 1.2 s cycle + random trigger "
                        "within 30 ms window after flash; all 4 detectors read out")

        self.dream_daq_info = {
            'ip': '192.168.10.8',
            'port': 1101,
            # External-trigger (TCM) template: gamma flash + random post-flash trigger fed in
            # externally; every detector is Dat (full 4-detector readout), no self-trigger FEU.
            'daq_config_template_path': f'{self.base_out_dir}dream_config/Tcm_Mx17_July.cfg',

            # 'run_directory': f'{self.base_out_dir}/dream_run/{self.run_name}/',
            'run_directory': f'/home/mx17/july_dream/dream_run/{self.run_name}/',
            'data_out_dir': f'{self.run_out_dir}',
            'raw_daq_inner_dir': self.raw_daq_inner_dir,
            'n_samples_per_waveform': 32,  # short window (32 x 60 ns = 1920 ns)
            'go_timeout': 5 * 60,  # Seconds to wait for 'Go' response from RunCtrl before assuming failure
            'max_run_time_addition': 60 * 5,  # Seconds to add to requested run time before killing run
            'copy_on_fly': True,  # True to copy raw data to out dir during run, False to copy after run
            'batch_mode': True,  # Run Dream RunCtrl in batch mode. Not implemented for cosmic bench CPU.
            'zero_suppress': False,  # True to run in zero suppression mode, False to run in full readout mode
            'pedestals_dir': f'{self.base_out_dir}pedestals/',  # None to ignore, else top directory for pedestal runs
            'pedestals': 'latest',  # 'latest' for most recent, otherwise specify directory name, eg "pedestals_10-22-25_13-43-34"
            'latency': 3,  # Latency (clock cycles); external-trigger value from run_15 Tcm config — check flash sits in window
            'sample_period': 60,  # ns, sampling period (only 20 or 60 supported) -> wider window than run_15's 20 ns
            # No daq_run_events cap: runs are purely time-based (30 min scan / 3 h settle), like run_15.
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
        # 1) Leading gas-exchange / settling sub-run: GAS_CHANGE_MIN, all resists at
        #    max - GAS_CHANGE_OFFSET, drift at the good operating point.  2) One resist scan per drift
        #    in DRIFT_SCAN_ORDER; each steps all four detectors together from their own max down in
        #    -RESIST_STEP_V steps for N_POINTS points, scan_point_minutes(k) per point (long then short).
        #    All four detectors read out every sub-run.
        self.sub_runs = []

        gas_resists = {ch: v - GAS_CHANGE_OFFSET for ch, v in RESIST_MAX.items()}
        self.sub_runs.append({
            'sub_run_name': f'gas_change_dr{GAS_CHANGE_DRIFT}_Amax'
                            + (f'-{GAS_CHANGE_OFFSET}' if GAS_CHANGE_OFFSET else ''),
            'run_time': GAS_CHANGE_MIN,  # Minutes
            'post_pause_s': 0,  # seconds; 0 = none
            'hvs': {
                '5': gas_resists,  # Positive Resists (mx17_A/B/C/D on channels 1-4)
                '9': {ch: GAS_CHANGE_DRIFT for ch in ('0', '1', '2', '3')},  # Negative Drifts
            },
        })

        offsets = build_resist_offsets(RESIST_STEP_V, N_POINTS)
        for drift in DRIFT_SCAN_ORDER:
            for k, off in enumerate(offsets):
                resists = {ch: v - off for ch, v in RESIST_MAX.items()}
                self.sub_runs.append({
                    'sub_run_name': f'dr{drift}_A{fmt_v(resists["1"])}_{k:02d}',
                    'run_time': scan_point_minutes(k),  # Minutes (30 for first N_LONG, then 10)
                    'post_pause_s': 0,  # seconds; 0 = none
                    'hvs': {
                        '5': resists,  # Positive Resists (mx17_A/B/C/D on channels 1-4)
                        '9': {'0': drift, '1': drift, '2': drift, '3': drift},  # Negative Drifts
                    },
                })


        self.bench_geometry = {
            'board_thickness': 5,  # mm  Thickness of PCB for test boards  Guess!
        }

        self.included_detectors = [
            'mx17_A', 'mx17_B', 'mx17_C', 'mx17_D',
            'plastic_A_L', 'plastic_A_R', 'plastic_B_L', 'plastic_B_R',
            'plastic_C_L', 'plastic_C_R', 'plastic_D_L', 'plastic_D_R',
            'liquid_A', 'liquid_B', 'liquid_C', 'liquid_D',
        ]
        # SiPM trigger-wall bars (16/arm), appended programmatically below.
        self.included_detectors += [
            f'sipm_{arm}_{i:02d}' for arm in ('A', 'B', 'C', 'D')
            for i in range(1, SIPM_N_READOUT + 1)
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
            # §5 stack + SimConfig.hh dims).  Updated 2026-07-18 to the 2026-07-17/18
            # placement survey: SiPM container 3.5 cm (back at 145.0 mm from mylar
            # front), per-arm plastics gaps, and the STEP-derived LS vessel.
            #
            #   Coords: same frame as the mx17 detectors.  Depths are measured from
            #   the MM drift-mylar FRONT face (w=0): SiPM scint plane 127.5 mm;
            #   plastics center per arm (D 222.72 / B 218.72 / A 220.72 / C 218.72 mm);
            #   LS slab center per arm (D/A 278.6, B/C 282.6 mm from mylar front).
            #
            #   Tangential (u) reference differs by layer:
            #     - SiPM wall + LS vessel are centered on the mechanical STRUCTURE
            #       (u=0, NOT the pinwheel-shifted MM).  The LS slab centre carries
            #       the surveyed u offset (ls_center_u: D +8.3 / B -13.4 / A +6.3 /
            #       C -17.4 mm) and a sub-mm v (beam) offset.
            #     - Plastics still inherit the per-arm pinwheel MM shift, so their
            #       tangential offset equals the mx17 value.
            #   Plastics are two 20x30x2.5 cm bars side-by-side, split +/-101.72 mm
            #   along the arm's in-plane tangent (uHat); L = detn 1, R = detn 2
            #   ("left/right seen from the back", per mx_july_beam_qa).
            #   SiPM wall: 16 read-out bars/arm (of 20), 2.5 cm pitch, read-out window
            #   shifted 1 bar toward the MM (bars i=1..16, u_i = 25 mm*(i-9.5));
            #   appended programmatically below (build_sipm_wall_detectors).
            #   LS vessel: STEP-derived (VERTICAL PMT-up on B/C, HORIZONTAL PMT-+u on
            #   A/D); det_center_coords is the LAB slab centre.
            #
            #   ntof DAQ mapping (mx_july_beam_qa trees): plastics -> PSS{A,B,C,D}
            #   with detn 1=L / 2=R; liquids -> LIQ{A,B,C,D} (single PMT per arm).
            #   SiPM-wall tree/channel mapping: TODO (next pass).
            #
            #   HV: plastics on CAEN card 07 (ch 0-7) and liquids on card 08 (ch 0-3)
            #   are wired in per detector ('hv_channels' bias + 'hv_setpoint', from the
            #   2026-07-18 GUI; current limits crate-managed).  The setpoints are merged
            #   into every sub-run's hvs below so the PMT bias is held on for the whole
            #   run and appears in HV monitoring.  SiPM-wall PMT bias: TODO (not in the
            #   2026-07-18 GUI screenshot).
            # =================================================================

            # ----- Plastic scintillators (card 07): 2 bars/arm, L = detn 1, R = detn 2 -----
            {
                'name': 'plastic_A_L',
                'det_type': 'scintillator_PMT',
                'scint_medium': 'plastic (PVT)',
                'description': '20x30x2.5 cm plastic bar, left (seen from back)',
                'det_center_coords': {'x': -118.07, 'y': 0, 'z': 425.22},  # mm
                'det_orientation': {'x': 0, 'y': 0, 'z': 0},  # deg (+Z normal, arm A)
                'hv_channels': {'bias': (7, 0)},  # CAEN 07.000 PLASTIC_A_L
                'hv_setpoint': 1303,  # V (set 2026-07-18 GUI; 250 uA limit, crate-managed)
                'ntof_daq': {'tree': 'PSSA', 'detn': 1},
            },
            {
                'name': 'plastic_A_R',
                'det_type': 'scintillator_PMT',
                'scint_medium': 'plastic (PVT)',
                'description': '20x30x2.5 cm plastic bar, right (seen from back)',
                'det_center_coords': {'x': 85.37, 'y': 0, 'z': 425.22},  # mm
                'det_orientation': {'x': 0, 'y': 0, 'z': 0},  # deg (+Z normal, arm A)
                'hv_channels': {'bias': (7, 1)},  # CAEN 07.001 PLASTIC_A_R
                'hv_setpoint': 1242,  # V (set 2026-07-18 GUI; 250 uA limit, crate-managed)
                'ntof_daq': {'tree': 'PSSA', 'detn': 2},
            },
            {
                'name': 'plastic_B_L',
                'det_type': 'scintillator_PMT',
                'scint_medium': 'plastic (PVT)',
                'description': '20x30x2.5 cm plastic bar, left (seen from back)',
                'det_center_coords': {'x': -422.72, 'y': 0, 'z': -117.47},  # mm
                'det_orientation': {'x': 0, 'y': -90, 'z': 0},  # deg (-X normal, arm B)
                'hv_channels': {'bias': (7, 2)},  # CAEN 07.002 PLASTIC_B_L
                'hv_setpoint': 1376,  # V (set 2026-07-18 GUI; 250 uA limit, crate-managed)
                'ntof_daq': {'tree': 'PSSB', 'detn': 1},
            },
            {
                'name': 'plastic_B_R',
                'det_type': 'scintillator_PMT',
                'scint_medium': 'plastic (PVT)',
                'description': '20x30x2.5 cm plastic bar, right (seen from back)',
                'det_center_coords': {'x': -422.72, 'y': 0, 'z': 85.97},  # mm
                'det_orientation': {'x': 0, 'y': -90, 'z': 0},  # deg (-X normal, arm B)
                'hv_channels': {'bias': (7, 3)},  # CAEN 07.003 PLASTIC_B_R
                'hv_setpoint': 1279,  # V (set 2026-07-18 GUI; 250 uA limit, crate-managed)
                'ntof_daq': {'tree': 'PSSB', 'detn': 2},
            },
            {
                'name': 'plastic_C_L',
                'det_type': 'scintillator_PMT',
                'scint_medium': 'plastic (PVT)',
                'description': '20x30x2.5 cm plastic bar, left (seen from back)',
                'det_center_coords': {'x': 119.02, 'y': 0, 'z': -423.22},  # mm
                'det_orientation': {'x': 0, 'y': 180, 'z': 0},  # deg (-Z normal, arm C)
                'hv_channels': {'bias': (7, 4)},  # CAEN 07.004 PLASTIC_C_L
                'hv_setpoint': 1180,  # V (set 2026-07-18 GUI; 250 uA limit, crate-managed)
                'ntof_daq': {'tree': 'PSSC', 'detn': 1},
            },
            {
                'name': 'plastic_C_R',
                'det_type': 'scintillator_PMT',
                'scint_medium': 'plastic (PVT)',
                'description': '20x30x2.5 cm plastic bar, right (seen from back)',
                'det_center_coords': {'x': -84.42, 'y': 0, 'z': -423.22},  # mm
                'det_orientation': {'x': 0, 'y': 180, 'z': 0},  # deg (-Z normal, arm C)
                'hv_channels': {'bias': (7, 5)},  # CAEN 07.005 PLASTIC_C_R
                'hv_setpoint': 1307,  # V (set 2026-07-18 GUI; 250 uA limit, crate-managed)
                'ntof_daq': {'tree': 'PSSC', 'detn': 2},
            },
            {
                'name': 'plastic_D_L',
                'det_type': 'scintillator_PMT',
                'scint_medium': 'plastic (PVT)',
                'description': '20x30x2.5 cm plastic bar, left (seen from back)',
                'det_center_coords': {'x': 426.72, 'y': 0, 'z': 117.22},  # mm
                'det_orientation': {'x': 0, 'y': 90, 'z': 0},  # deg (+X normal, arm D)
                'hv_channels': {'bias': (7, 6)},  # CAEN 07.006 PLASTIC_D_L
                'hv_setpoint': 1303,  # V (set 2026-07-18 GUI; 250 uA limit, crate-managed)
                'ntof_daq': {'tree': 'PSSD', 'detn': 1},
            },
            {
                'name': 'plastic_D_R',
                'det_type': 'scintillator_PMT',
                'scint_medium': 'plastic (PVT)',
                'description': '20x30x2.5 cm plastic bar, right (seen from back)',
                'det_center_coords': {'x': 426.72, 'y': 0, 'z': -86.22},  # mm
                'det_orientation': {'x': 0, 'y': 90, 'z': 0},  # deg (+X normal, arm D)
                'hv_channels': {'bias': (7, 7)},  # CAEN 07.007 PLASTIC_D_R
                'hv_setpoint': 1417,  # V (set 2026-07-18 GUI; 250 uA limit, crate-managed)
                'ntof_daq': {'tree': 'PSSD', 'detn': 2},
            },

            # ----- Liquid scintillators (card 08): single LS layer per arm, one PMT -----
            {
                'name': 'liquid_A',
                'det_type': 'scintillator_PMT',
                'scint_medium': 'liquid (LAB)',
                'description': 'STEP LS vessel (45x45 cm LAB slab in CFRP box), HORIZONTAL, PMT toward +X (North)',
                'det_center_coords': {'x': 6.3, 'y': 0.6, 'z': 483.1},  # mm  slab center (structure-referenced u)
                'det_orientation': {'x': 0, 'y': 0, 'z': 0},  # deg (+Z normal, arm A); vessel roll -90 (horizontal)
                'hv_channels': {'bias': (8, 0)},  # CAEN 08.000 LIQUID_A
                'hv_setpoint': 2000,  # V (set 2026-07-18 GUI; 2000 uA limit, crate-managed)
                'ntof_daq': {'tree': 'LIQA', 'detn': 1},
            },
            {
                'name': 'liquid_B',
                'det_type': 'scintillator_PMT',
                'scint_medium': 'liquid (LAB)',
                'description': 'STEP LS vessel (45x45 cm LAB slab in CFRP box), VERTICAL, PMT up (+beam)',
                'det_center_coords': {'x': -486.6, 'y': 0.3, 'z': -13.4},  # mm  slab center (structure-referenced u)
                'det_orientation': {'x': 0, 'y': -90, 'z': 0},  # deg (-X normal, arm B); vessel roll 0 (vertical)
                'hv_channels': {'bias': (8, 1)},  # CAEN 08.001 LIQUID_B
                'hv_setpoint': 2000,  # V (set 2026-07-18 GUI; 2000 uA limit, crate-managed)
                'ntof_daq': {'tree': 'LIQB', 'detn': 1},
            },
            {
                'name': 'liquid_C',
                'det_type': 'scintillator_PMT',
                'scint_medium': 'liquid (LAB)',
                'description': 'STEP LS vessel (45x45 cm LAB slab in CFRP box), VERTICAL, PMT up (+beam)',
                'det_center_coords': {'x': 17.4, 'y': -0.7, 'z': -487.1},  # mm  slab center (structure-referenced u)
                'det_orientation': {'x': 0, 'y': 180, 'z': 0},  # deg (-Z normal, arm C); vessel roll 0 (vertical)
                'hv_channels': {'bias': (8, 2)},  # CAEN 08.002 LIQUID_C
                'hv_setpoint': 2000,  # V (set 2026-07-18 GUI; 2000 uA limit, crate-managed)
                'ntof_daq': {'tree': 'LIQC', 'detn': 1},
            },
            {
                'name': 'liquid_D',
                'det_type': 'scintillator_PMT',
                'scint_medium': 'liquid (LAB)',
                'description': 'STEP LS vessel (45x45 cm LAB slab in CFRP box), HORIZONTAL, PMT toward -Z (West)',
                'det_center_coords': {'x': 482.6, 'y': -0.4, 'z': -8.3},  # mm  slab center (structure-referenced u)
                'det_orientation': {'x': 0, 'y': 90, 'z': 0},  # deg (+X normal, arm D); vessel roll -90 (horizontal)
                'hv_channels': {'bias': (8, 3)},  # CAEN 08.003 LIQUID_D
                'hv_setpoint': 2000,  # V (set 2026-07-18 GUI; 2000 uA limit, crate-managed)
                'ntof_daq': {'tree': 'LIQD', 'detn': 1},
            },

        ]

        # SiPM trigger-wall bars (16 per arm), generated to match the sim's bar
        # layout (see build_sipm_wall_detectors / the SCINTILLATOR STACK note).
        self.detectors += build_sipm_wall_detectors()

        # Hold the scintillator PMT bias on for the whole run: merge each included
        # scintillator's setpoint into every sub-run's hvs (plastics card 07,
        # liquids card 08).  set_hvs only asserts v0 + powers on, so re-listing the
        # channels each sub-run simply holds them at voltage; it also adds them to
        # HV monitoring.  Detectors without an hv_setpoint (e.g. the SiPM bars) are
        # skipped.  Done here so it also covers the leading gas_change sub-run.
        scint_hvs = {}
        for det in self.detectors:
            if det['name'] not in self.included_detectors:
                continue
            if not str(det.get('det_type', '')).startswith('scintillator'):
                continue
            hv_channels = det.get('hv_channels')
            setpoint = det.get('hv_setpoint')
            if not isinstance(hv_channels, dict) or setpoint is None:
                continue
            for slot, channel in hv_channels.values():
                scint_hvs.setdefault(str(slot), {})[str(channel)] = setpoint
        for sub_run in self.sub_runs:
            hvs = sub_run.setdefault('hvs', {})
            for slot, chans in scint_hvs.items():
                hvs.setdefault(slot, {}).update(chans)

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
    START = datetime.now().replace(hour=23, minute=0, second=0, microsecond=0)  # 11 PM tonight

    ns = config.dream_daq_info['n_samples_per_waveform']
    sp = config.dream_daq_info['sample_period']
    print(f'Gas: {config.gas}   Beam: {config.beam_type}')
    print(f'Run: {config.run_name}  — external gamma-flash + random-window trigger, all 4 read out')
    print(f'DAQ: {ns} samples x {sp} ns = {ns * sp} ns window; full readout; '
          f'template {config.dream_daq_info["daq_config_template_path"].split("/")[-1]}')
    print(f'Resist scan: max -> max-{RESIST_STEP_V * (N_POINTS - 1)} V in -{RESIST_STEP_V} V steps, '
          f'{N_POINTS} pts ({N_LONG}x{SUBRUN_MIN_LONG} min + {N_POINTS - N_LONG}x{SUBRUN_MIN_SHORT} min); '
          f'drifts (in order) {DRIFT_SCAN_ORDER} V')
    print(f'Lead-in: gas_change {GAS_CHANGE_MIN} min at drift {GAS_CHANGE_DRIFT} V, '
          f'resists {"max" if not GAS_CHANGE_OFFSET else f"max-{GAS_CHANGE_OFFSET}"} V\n')

    # Walk the sub-runs, assigning wall-clock start/end (run_time + per-subrun overhead).
    t = START
    print(f'{"sub-run":34s} {"len":>6s}  {"start":>7s} -> {"end":>7s}')
    print('-' * 62)
    phase_start = {}  # drift -> (start_dt, end_dt) spanning that scan
    for sr in config.sub_runs:
        dur = timedelta(minutes=sr['run_time'] + OVERHEAD_MIN)
        end = t + dur
        # Track drift-scan phase boundaries for the summary below.
        name = sr['sub_run_name']
        if name.startswith('dr'):
            drift = int(name[2:name.index('_')])
            s, _ = phase_start.get(drift, (t, end))
            phase_start[drift] = (s, end)
        hh = sr['run_time']
        lbl = f'{hh//60}h{hh%60:02d}' if hh >= 60 else f'{hh}m'
        print(f'{name:34s} {lbl:>6s}  {t:%H:%M} -> {end:%H:%M}')
        t = end

    total_min = sum(sr['run_time'] + OVERHEAD_MIN for sr in config.sub_runs)
    print('-' * 62)
    print(f'\nPhase boundaries (drift scans, in run order):')
    for drift in DRIFT_SCAN_ORDER:
        s, e = phase_start[drift]
        star = '   <-- "good" scan' if drift == 800 else ''
        print(f'  drift {drift} V:  {s:%a %H:%M} -> {e:%a %H:%M}{star}')
    print(f'\nSub-runs: {len(config.sub_runs)}   Total: {total_min} min = {total_min/60:.2f} h  '
          f'(start {START:%a %H:%M} -> end {t:%a %H:%M})')

    print('donzo')
