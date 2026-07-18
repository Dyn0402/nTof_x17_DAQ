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
# run_52 — GAS-CHANGE monitoring run: RANDOM trigger at FIXED resist HV while
# the gas transitions Ar/Iso 95/5 -> 90/10 (2026-07-17). Resist voltages are
# PINNED at the 95/5 operational maxima and held fixed across the whole run so
# the detector response (pedestal/noise + gain-on-tracks) can be tracked as the
# gas composition changes. NO HV scan this run.
#   Physics/safety: Garfield says 90/10 needs ~+72 V for equal gain vs 95/5
#   (see run-iso-equivalence-comparison / detD-hv-95-5 memories), so holding
#   resist at the 95/5 max while switching to 90/10 makes gain -- and spark
#   risk -- DROP as the gas transitions. Safe direction.
# GAS: switched to Ar/Iso 90/10 (from 95/5).
# RESIST (FIXED): A/B/C = 480 V (95/5 operational max = top of the run_49/51
#   scan); det D = 460 V (DET_D_OFFSET below A/B/C; 460 V is D's spark-safe max).
# DRIFT (FIXED): B/C/D = 800 V; det A = 550 V (sparks at 800 V in 95/5,
#   2026-07-17 midday -- kept at 550 V until investigated).
#
#   Mode 2 trigger, RANDOM pulser + gamma flash (RUN_MODES_2026-07.md §1.2,
#   `flash_random`): M4.C = or_veto(lemo4 = M6.D ~667 Hz Poisson pulser),
#   gated by the 30 ms N93B veto window; M4.D = OR(lemo0 = flash, lemo1 = C-out).
#   The 30 ms gate (~1 % duty) turns the 667 Hz pulser into a comfortable
#   ~5-6 Hz average of DREAM triggers. Random events read out flat pedestal at
#   a random time (uncorrelated -> latency irrelevant for them); the flash
#   events (~0.29 Hz) mark beam pulses and are framed by latency 5.
#   32 samples x 60 ns (1.92 us window), LATENCY 5 (Mode 2 canonical: flash
#   peak ~= latency + 13).
#   STATIC board settings, applied ONCE before the run:
#     .venv/bin/python n1081b/trigger_mode.py flash_random
#   Verify with `.venv/bin/python n1081b/trigger_mode.py status` before
#   starting DAQ (expect "flash_random"). Confirm the M6.D pulser is running
#   at the design 1.5 ms Poisson period (do NOT set period 150 ms -- silently
#   kills the output; see RUN_MODES §1.2).
#
#   Mesh grounded/disconnected -- n1081b_scan stays 'off' (no inline
#   trigger/mesh modulation, no cycling; do NOT start n1081b_scan_watcher.py).
#
#   STRUCTURE: 24 x 60 min sub-runs = 24 h, all at the SAME fixed HV. Chunked
#   so resume re-takes only an interrupted hour and files stay manageable; the
#   run auto-caps at 24 h but is expected to be STOPPED MANUALLY.
# ===========================================================================
OVERHEAD_MIN = 1      # per-subrun ramp poll + DAQ prep + inter-subrun wait

RESIST_ABC    = 480   # V, resist dets A/B/C (card 5 ch 1-3) -- 95/5 operational max, held FIXED
DET_D_OFFSET  = 20    # V, det D resist runs this many volts BELOW A/B/C (-> 460 V, D spark-safe max)
DRIFT_BCD     = 800   # V, drift dets B/C/D (card 9 ch 1-3), held FIXED
DRIFT_A       = 550   # V, drift det A ONLY (card 9 ch 0) -- sparks at 800 V in 95/5
                      # (2026-07-17 midday, run_50 sub-run 00); held at 550 V until investigated
RUN_HOURS     = 24    # total run length; expected to be stopped MANUALLY
SUBRUN_MIN    = 60    # minutes per sub-run (fixed HV; chunked for resume + file size)
N_SUBRUNS     = RUN_HOURS * 60 // SUBRUN_MIN  # = 24

POST_PAUSE_S = 0  # no inline scan control in play this run; nothing to re-apply between sub-runs.


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
        self.run_name = 'run_52'  # Gas-change RANDOM-trigger monitoring run (Mode 2 flash_random): resist FIXED at 95/5 maxima (A/B/C 480 V, D 460 V), drift 800 V B/C/D + 550 V det A (spark), gas switched to 90/10, 24 x 60 min = 24 h, manual stop
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
        self.gas = 'Ar/Iso 90/10'  # Gas type for run (gas-change run: switched from 95/5 on 2026-07-17; resist held at 95/5 maxima)
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
        # self.beam_filter = 'Pb 20 mm (beamline, upstream of target)'
        self.beam_filter = 'none'
        # self.trigger = "Det 3 SiPM Wall + Det 3 Scint"
        # self.trigger = "PS Pickup"
        self.trigger = ("External (N1081B M4.D out0): RANDOM pulser + gamma flash "
                        "(M4.C = or_veto(lemo4 = M6.D ~667 Hz Poisson pulser), gated by the "
                        "30 ms N93B window; M4.D = OR(lemo0 = flash, lemo1 = C-out)) — Mode 2 "
                        "'flash_random' of RUN_MODES_2026-07.md. The 30 ms gate turns the "
                        "667 Hz pulser into ~5-6 Hz average DREAM triggers; random events read "
                        "out flat pedestal at random times, flash events (~0.29 Hz) mark beam "
                        "pulses. Static setup via n1081b/trigger_mode.py flash_random. Mesh "
                        "grounded/disconnected. NO beamline filter (20 mm Pb removed "
                        "2026-07-17). GAS-CHANGE MONITORING: gas switched to Ar/Iso 90/10, "
                        "resist HELD FIXED at the 95/5 operational maxima (A/B/C 480 V, det D "
                        "460 V = 20 V below, D spark-safe max); drift 800 V B/C/D, det A drift "
                        "550 V (sparks at 800 V, lowered 2026-07-17). 24 x 60 min = 24 h at "
                        "fixed HV, manual stop. 32 smp x 60 ns (1.92 us window), latency 5 "
                        "(Mode 2 canonical; flash peak ~= latency + 13, random events "
                        "latency-independent).")

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
            'n_samples_per_waveform': 32,  # flash_random config (Mode 2): 32 x 60 ns = 1920 ns window
            'go_timeout': 5 * 60,  # Seconds to wait for 'Go' response from RunCtrl before assuming failure
            'max_run_time_addition': 60 * 5,  # Seconds to add to requested run time before killing run
            'copy_on_fly': True,  # True to copy raw data to out dir during run, False to copy after run
            'batch_mode': True,  # Run Dream RunCtrl in batch mode. Not implemented for cosmic bench CPU.
            'zero_suppress': False,  # True to run in zero suppression mode, False to run in full readout mode
            'pedestals_dir': f'{self.base_out_dir}pedestals/',  # None to ignore, else top directory for pedestal runs
            'pedestals': 'latest',  # 'latest' for most recent, otherwise specify directory name, eg "pedestals_10-22-25_13-43-34"
            'latency': 5,   # flash_random value (Mode 2, RUN_MODES_2026-07.md): frames the
                            # flash events (flash peak ~= latency + 13, i.e. samples ~16-20;
                            # det A rails from ~11). Random pulser events are uncorrelated so
                            # latency is irrelevant for them (flat pedestal).
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
        # Single phase, static flash_random trigger (no scan tags — n1081b_scan='off',
        # so sub_run_name has no special leading token). FIXED HV: every sub-run applies
        # the SAME resist (A/B/C = RESIST_ABC, det D = RESIST_ABC - DET_D_OFFSET) and drift
        # (B/C/D = DRIFT_BCD, det A = DRIFT_A). The run is chunked into N_SUBRUNS hourly
        # sub-runs only for resume + file-size management — the HV never changes. Names
        # carry the fixed HV point and a global index k so resume re-takes only an
        # interrupted hour.
        def _drift():
            # card 9 ch 0-3 = drifts A/B/C/D; A pinned to DRIFT_A (sparks at 800 V)
            return {'0': DRIFT_A, '1': DRIFT_BCD, '2': DRIFT_BCD, '3': DRIFT_BCD}

        def _resist():
            # card 5 ch 1-4 = det A/B/C/D; D runs DET_D_OFFSET volts below A/B/C
            return {'1': RESIST_ABC, '2': RESIST_ABC, '3': RESIST_ABC, '4': RESIST_ABC - DET_D_OFFSET}

        self.sub_runs = []
        for k in range(N_SUBRUNS):
            self.sub_runs.append({
                'sub_run_name': (f'gaschg_A{fmt_v(RESIST_ABC)}_D{fmt_v(RESIST_ABC - DET_D_OFFSET)}'
                                 f'_dr{DRIFT_BCD}_dA{fmt_v(DRIFT_A)}_{k:02d}'),
                'run_time': SUBRUN_MIN, 'post_pause_s': POST_PAUSE_S,
                'hvs': {'5': _resist(), '9': _drift()},
            })


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
    START = datetime.now()  # projection only; run starts when daq_control is launched

    import os as _os
    ns = config.dream_daq_info['n_samples_per_waveform']
    sp = config.dream_daq_info['sample_period']
    lat = config.dream_daq_info['latency']
    n_pts = len(config.sub_runs)
    print(f'Gas: {config.gas}   Beam: {config.beam_type}   Target: {config.target_type}')
    print(f'Beam filter: {getattr(config, "beam_filter", "none")}')
    print(f'Run: {config.run_name}  — GAS-CHANGE random-trigger monitoring, all 4 detectors, FIXED HV')
    print(f'Trigger: {config.trigger}')
    print(f'DAQ: {ns} smp x {sp} ns = {ns*sp} ns window, latency {lat}; RESIST FIXED '
          f'{RESIST_ABC} V A/B/C (det D {RESIST_ABC-DET_D_OFFSET} V), drift '
          f'{DRIFT_BCD} V B/C/D + {DRIFT_A} V det A (spark-limited); held constant across '
          f'{N_SUBRUNS} x {SUBRUN_MIN} min = {RUN_HOURS} h (manual stop).')
    print('*** PRE-RUN: apply the STATIC flash_random trigger once — '
          '.venv/bin/python n1081b/trigger_mode.py flash_random (M4.C = or_veto(lemo4 = M6.D '
          '~667 Hz Poisson pulser), 30 ms N93B veto gate; M4.D = OR(lemo0 = flash, lemo1 = '
          'C-out)). Verify with n1081b/trigger_mode.py status before starting DAQ (expect '
          '"flash_random"); confirm the M6.D pulser is at the 1.5 ms Poisson period (do NOT '
          'set period 150 ms — it silently kills the output). Mesh grounded — nothing else to '
          'set. n1081b_scan is "off": no inline trigger/mesh modulation. Do NOT run '
          'n1081b_scan_watcher.py during the run. ***')
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
