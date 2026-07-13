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
# run_34 — FINE random-trigger HV scan, TWO consecutive ~3 h passes offset in
# HV to interleave new points. Ar/Iso 90/10 gas (Marex, parasitic beam), all
# four detectors A/B/C/D. Re-does run_33's random phase, which was CORRUPTED
# because the N1081B scan watcher was NOT running: the per-block trigger
# modulation never happened, so C.in0/in1 (detector Singles/Doubles) stayed
# enabled and their mains-combed self-triggers contaminated the "random"
# trigger (30 us trains at the 10 ms mains half-period, frac<0.1ms ~= 0.67,
# identical to run_30). run_32's random blocks were CLEAN because its watcher
# WAS live and cut C.in0/in1 -> pulser-only Poisson (median ~1 ms, frac<0.1ms
# ~= 0.06). Root cause: run_config_beam prints "watcher REQUIRED" but nothing
# enforces it; run_33 was launched without it.
#
#   THE FIX IS OPERATIONAL: start the watcher before this run (see PRE-RUN).
#   With it live, the randOn/randOff tags cut Singles/Doubles at C and the
#   time distribution is the intended veto-gated Poisson.
#
#   TWO PASSES (s1, s2) — each a full random-trigger fine HV scan under the
#     30 ms veto-gated Poisson RANDOM trigger (PS + pulser through the real
#     or_veto), 32 smp x 60 ns, latency 5. At EACH HV point a mesh-injection ON
#     sub-run then a mesh OFF sub-run (INTERLEAVED, so the on/off pair sees the
#     same beam within minutes). Resists step -10 V: 560 -> 460 V (A/B/C),
#     540 -> 440 V (D), 11 points x 2 mesh x 7 min at drift 800 V (~3.0 h/pass).
#     PASS 2 is shifted DOWN 2.5 V (557.5 -> 457.5 A/B/C) so its points
#     INTERLEAVE pass 1's -> new HV points, combined 2.5 V local spacing, plus a
#     drift/reproducibility cross-check between the two passes.
#     WATCH the first sub-runs' interval distributions live to confirm the fix
#     (analyze_intervals.py <subrun_dir>, or the §6 snippet in
#     HANDOFF_2026-07-13_randomizer_veto_test.md): expect frac<0.1ms ~= 0.06,
#     median ~1 ms like run_32 — NOT run_30/33's 0.67 / 30 us trains.
#
#   REUSES config/n1081b_scan_schedule.json (randOn / randOff tags). daq_control
#   now applies the per-sub-run trigger/mesh config ITSELF (in-process, verified
#   by read-back — see n1081b/scan_control.py); the standalone scan-watcher
#   process is no longer needed and must NOT be run alongside a data run.
#
#   PRE-RUN (one-time): the STATIC trigger setup — or_veto function on M4.C and
#   the M6.D Poisson pulser — is NOT part of the per-block schedule, so it still
#   must be applied once before the run:
#     .venv/bin/python n1081b/setup_run30_trigger.py
#   Then just launch daq_control: it snapshots the boards, cuts Singles/Doubles
#   and toggles mesh per sub-run automatically, and restores the boards on exit.
#   (Set self.n1081b_scan = 'off' to force it off, 'on' to force on; default
#   'auto' enables it whenever a sub-run tag matches a schedule scan entry.)
# ===========================================================================
OVERHEAD_MIN = 1      # per-subrun ramp poll + DAQ prep + inter-subrun wait

# Per-channel maximum resist voltages, card 5 channels 1-4 = detectors A/B/C/D.
# Kept at run_32/33's cautious Ar/Iso 90/10 ceiling (the 80/20 maxes 700/700/660
# do NOT transfer); detector D capped 20 V lower for its trip history. Both
# passes scan DOWN from these maxes (pass 2 from a 2.5 V-lower start).
RESIST_MAX = {'1': 560, '2': 560, '3': 560, '4': 540}

# ----- random-trigger fine HV scan geometry (two offset passes) ----------------
SCAN_DRIFT      = 800   # V, drift held throughout (A/B/C/D = card 9 ch 0-3)
RESIST_STEP     = 10    # V per step within a pass (mesh ON+OFF at each -> fits 3 h)
RESIST_MAX_OFF  = 100   # V, deepest offset below max (inclusive) -> 11 points/pass
RAND_SUBRUN_MIN = 7     # minutes per random sub-run (11 pts x 2 mesh x 7 min +
                        # settle ~= 3.0 h per pass; two passes ~= 6.1 h)
SETTLE_MIN      = 5     # settle at each pass's max before scanning
N_PASSES        = 2     # two consecutive rescans (s1, s2)
PASS_OFFSET_V   = 2.5   # pass 2 shifted DOWN this much to interleave new HV points

# Offsets (V below each pass's own max) -> 0..100 in 10 V steps = 11 points.
RESIST_OFFSETS = list(range(0, RESIST_MAX_OFF + RESIST_STEP, RESIST_STEP))

# Tags + DREAM readout overrides (samples / period ns / latency). Tags must
# match config/n1081b_scan_schedule.json; overrides ride on each sub-run. Each
# HV point interleaves mesh ON (randOn) then mesh OFF (randOff).
RAND_TAGS = ('randOn', 'randOff')  # mesh injection ON then OFF at each HV point
RAND_DREAM  = {'n_samples_per_waveform': 32, 'sample_period': 60, 'latency': 5}

POST_PAUSE_S = 5  # extra post-sub-run wait; with daq_control's built-in 10 s it
                  # guarantees the watcher (3 s poll) latches .pause_run and swaps
                  # the trigger/mesh config before the next sub-run starts.


def fmt_v(v):
    """Format a setpoint for sub-run names: 540, 537.5, 400 (no trailing .0)."""
    return f'{v:g}'


class Config(RunConfigBase):
    def __init__(self, config_path=None):
        if not config_path:
            self._set_defaults()

        super().__init__(config_path)

    def _set_defaults(self, config_path=None):
        self.run_name = 'run_34'  # Two ~3 h random-trigger fine HV passes, HV-offset to interleave; all 4 dets, Ar/Iso 90/10
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
        self.resume = False  # Fresh run_34 (new run dir, no markers). Set True only
                             # to continue a partially-completed run_34.
        self.n1081b_scan = 'on'  # 'on' (this IS a veto-gated random-trigger scan) |
                             # 'auto' (enable iff a sub-run tag matches the schedule) |
                             # 'off' (deliberately no trigger modulation). 'on' makes
                             # daq_control REFUSE to start if it can't control the
                             # trigger (missing schedule / unreachable boards) rather
                             # than silently take data with Singles leaking in.
        self.write_all_detectors_to_json = True  # Only when making run config json template. Maybe do always?
        # self.gas = 'Ar/CF4/CO2 45/40/15'  # Gas type for run
        # self.gas = 'Ar/CF4 90/10'  # Gas type for run
        # self.gas = 'Ar/CO2 70/30'  # Gas type for run
        self.gas = 'Ar/Iso 90/10'  # Gas type for run (changed from 80/20 on 2026-07-12)
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
        self.target_type = 'Marex'
        # self.trigger = "Det 3 SiPM Wall + Det 3 Scint"
        # self.trigger = "PS Pickup"
        self.trigger = ("External (N1081B M4.D out0): PS + 30 ms-veto-gated Poisson "
                        "pulser (~5-6 Hz avg) through the real or_veto with C.in0/in1 "
                        "(Singles/Doubles) CUT by the scan watcher — the intended "
                        "'random' trigger. REQUIRES n1081b_scan_watcher.py running "
                        "(run_33's random phase was corrupted by Singles leakage "
                        "because the watcher was not started). Two consecutive fine "
                        "HV passes (s1, s2), pass 2 offset -2.5 V to interleave new "
                        "points. Mesh injection (M6.B) toggled ON (randOn) then OFF "
                        "(randOff) per HV point. All four detectors A/B/C/D.")

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
            'n_samples_per_waveform': 400,  # base value; PER-SUB-RUN OVERRIDES in SCAN_BLOCKS drive the real settings
            'go_timeout': 5 * 60,  # Seconds to wait for 'Go' response from RunCtrl before assuming failure
            'max_run_time_addition': 60 * 5,  # Seconds to add to requested run time before killing run
            'copy_on_fly': True,  # True to copy raw data to out dir during run, False to copy after run
            'batch_mode': True,  # Run Dream RunCtrl in batch mode. Not implemented for cosmic bench CPU.
            'zero_suppress': False,  # True to run in zero suppression mode, False to run in full readout mode
            'pedestals_dir': f'{self.base_out_dir}pedestals/',  # None to ignore, else top directory for pedestal runs
            'pedestals': 'latest',  # 'latest' for most recent, otherwise specify directory name, eg "pedestals_10-22-25_13-43-34"
            'latency': 60,  # base value; per-sub-run overrides apply (tuned 2026-07-11:
                            # flash 20 ns -> 60; flash 60 ns -> 5; scint 60 ns -> 35)
            'sample_period': 20,  # ns (20 or 60); base value, per-sub-run overrides apply
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
        # PHASE 1: fine HV scan under the veto-gated Poisson random trigger, with
        # a mesh-ON (randOn) then mesh-OFF (randOff) sub-run at each HV point,
        # max -> min in 5 V steps. PHASE 2: one open-ended scint sub-run at max
        # HV. The leading name token = the N1081B watcher scan tag; the watcher
        # swaps mesh routing at every on<->off change (and trigger routing at the
        # phase-1 -> phase-2 border). DREAM readout overrides (samples/period/
        # latency) ride on each sub-run dict ({**dream_info, **subrun}).
        def _drift(v):
            return {'0': v, '1': v, '2': v, '3': v}  # card 9 ch 0-3 = drifts A/B/C/D

        self.sub_runs = []

        # TWO consecutive random-trigger fine HV passes. Pass p starts 2.5*(p-1) V
        # below the maxes so pass 2's grid interleaves pass 1's (new HV points).
        # Within a pass: settle at that pass's max, then step DOWN by RESIST_OFFSETS
        # at drift 800 V, taking a mesh-ON (randOn) then mesh-OFF (randOff) sub-run
        # at each point (HV identical within the pair; ramps only between points).
        for p in range(1, N_PASSES + 1):
            shift = PASS_OFFSET_V * (p - 1)
            pass_max = {ch: v - shift for ch, v in RESIST_MAX.items()}

            # Settle at this pass's max. randOn tag (mesh ON) so the watcher
            # re-applies the random trigger config at the pass start.
            self.sub_runs.append({
                'sub_run_name': f'{RAND_TAGS[0]}_s{p}_settle_Amax',
                'run_time': SETTLE_MIN, 'post_pause_s': POST_PAUSE_S,
                'hvs': {'5': dict(pass_max), '9': _drift(SCAN_DRIFT)},
                **RAND_DREAM,
            })

            for k, off_v in enumerate(RESIST_OFFSETS):
                resists = {ch: v - off_v for ch, v in pass_max.items()}
                for tag in RAND_TAGS:
                    self.sub_runs.append({
                        'sub_run_name': f'{tag}_s{p}_A{fmt_v(resists["1"])}_{k:02d}',
                        'run_time': RAND_SUBRUN_MIN, 'post_pause_s': POST_PAUSE_S,
                        'hvs': {'5': resists, '9': _drift(SCAN_DRIFT)},
                        **RAND_DREAM,
                    })


        self.bench_geometry = {
            'board_thickness': 5,  # mm  Thickness of PCB for test boards  Guess!
        }

        # Detector D back in service 2026-07-12 (held 520 V through the gas change).
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

    # ----- Wall-clock schedule ------------------------------------------------
    # Set the intended start time here to project when each phase begins/ends.
    from datetime import datetime, timedelta
    START = datetime.now()  # projection only; run starts when daq_control is launched

    import os as _os
    print(f'Gas: {config.gas}   Beam: {config.beam_type}   Target: {config.target_type}')
    print(f'Run: {config.run_name}  — two consecutive random-trigger fine HV passes '
          f'(pass 2 offset -{PASS_OFFSET_V:g} V)')
    print(f'Trigger: {config.trigger}')
    print(f'Each pass {"/".join(RAND_TAGS)}: {RAND_DREAM["n_samples_per_waveform"]} smp x '
          f'{RAND_DREAM["sample_period"]} ns = {RAND_DREAM["n_samples_per_waveform"]*RAND_DREAM["sample_period"]} ns '
          f'window, latency {RAND_DREAM["latency"]}; HV {RESIST_MAX["1"]} -> '
          f'{RESIST_MAX["1"]-RESIST_MAX_OFF} V (D {RESIST_MAX["4"]} -> {RESIST_MAX["4"]-RESIST_MAX_OFF}) '
          f'in -{RESIST_STEP} V ({len(RESIST_OFFSETS)} pts x {len(RAND_TAGS)} mesh x '
          f'{RAND_SUBRUN_MIN} min), drift {SCAN_DRIFT} V. {N_PASSES} passes, '
          f'pass 2 shifted -{PASS_OFFSET_V:g} V (interleaves new HV points).')
    print('*** PRE-RUN: apply the STATIC trigger setup once — '
          '.venv/bin/python n1081b/setup_run30_trigger.py (or_veto + pulser). '
          'daq_control now applies the per-sub-run trigger/mesh config INLINE '
          '(n1081b/scan_control.py) — no separate watcher process. Do NOT run '
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
