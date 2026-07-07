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
# Self-trigger per-detector threshold + HV scan (Ar/CF4/Iso 88/10/2) — overnight.
#   The DAQ runs in self-trigger mode. The run cycles the *triggering* detector
#   A -> B -> C -> D; for each, the trigger source is that detector's two FEUs (X & Y),
#   selected by a per-detector self-trigger .cfg (SelfTrig_Mx17_July_<det>.cfg, with Trg
#   roles on those FEUs). All four detectors are always read out.
#   For each triggering detector we scan three discriminator thresholds set by the global
#   Dream*1 ThDAC (magnitudes 127/105/89 = high/med/low; each is its own per-detector .cfg)
#   and, at each threshold, a short resist HV scan: every detector starts at its own max resist
#   and steps down together, drift held fixed. A final higher-statistics tail repeats a coarse
#   3-point resist scan per detector at a single DAC threshold.
#   NB: the self-trigger threshold is the analog Dream*1 DAC, NOT the _thr.prg (that is the ZS
#   readout threshold and does not gate the trigger). Channels are enabled via Dream*8/9=0 in the
#   cfgs; pedestals import the latest real run as usual. Trigger requires X-Y coincidence (both of
#   the detector's FEUs fire), HIT_multi>=4 channels per connector, only its 2 FEUs are read out,
#   and each sub-run is capped at a per-FEU event count (MAIN_EVENTS / TAIL_EVENTS) OR its run_time.
#   The trigger config changes per sub-run via the daq_config_template_path override, so
#   set_feus_from_detectors is OFF (Trg roles + DAC come from the .cfg, not the pipeline).
#   Sample period / NbOfSamples / latency are driven from dream_daq_info.
# ---------------------------------------------------------------------------
OVERHEAD_MIN = 1      # per-subrun ramp poll + DAQ prep + 10 s inter-subrun wait

# Per-channel maximum resist voltages, card 5 channels 1-4 = detectors A/B/C/D.
# Every scan starts each detector at its own max and steps down from there.
RESIST_MAX = {'1': 540, '2': 535, '3': 530, '4': 515}

# Triggering-detector cycle and its card-5 resist channel.
DETECTORS = ['A', 'B', 'C', 'D']
DET_RESIST_CH = {'A': '1', 'B': '2', 'C': '3', 'D': '4'}
DRIFT_V = 800                       # all four drift channels, held fixed across the scan

# Self-trigger discriminator threshold = global Dream*1 ThDAC magnitude (0-127), NOT the _thr.prg
# (which is the ZS/readout threshold and does not gate the trigger -- verified 2026-07-06). Each DAC
# level is a separate per-detector .cfg (SelfTrig_Mx17_July_<det>_dac<mag>.cfg, built by
# dream_scripts/make_selftrig_cfgs.py). 127/105/89 (high/med/low) translate the old 1000/700/500 scan
# via ~5.6 ADC counts/DAC-LSB (17.5% discriminator window). Larger magnitude = higher (tighter) threshold.
THRESHOLDS_DAC = [127, 105, 89]

# Per-FEU event cap: each sub-run stops at MAIN_EVENTS events/FEU OR its run_time, whichever comes
# first (RunCtrl OR logic). Bounds data volume when the rate saturates at high HV.
MAIN_EVENTS = 6000

# Main scan: per (detector, DAC threshold), a resist scan from each detector's own max.
MAIN_N_POINTS = 8                   # resist points
MAIN_RESIST_STEP = 10               # V decrement per point
MAIN_SUBRUN_MIN = 10

# High-statistics tail: per detector, a long resist scan at one DAC, starting at max-20 V and
# stepping down -10 V, 30 min / 12000 events each. Runs as far down as time allows (resume-safe);
# the sequence intentionally extends well past a night so it never runs dry before we stop it.
TAIL_DAC = 105
TAIL_SUBRUN_MIN = 30
TAIL_EVENTS = 12000
TAIL_START_OFFSET = 20              # first tail point at each detector's max - 20 V
TAIL_RESIST_STEP = 10
TAIL_RESIST_OFFSETS = list(range(TAIL_START_OFFSET, 151, TAIL_RESIST_STEP))  # 20,30,...,150

# Per-detector, per-DAC self-trig cfg (relative to base_out_dir). Pedestals import the latest real
# pedestal run as usual (dream_daq_info defaults) -- the trigger threshold is the DAC, not the pedestal.
DREAM_CFG_INNER_DIR = 'dream_config/'
SELFTRIG_CFG_FMT = 'SelfTrig_Mx17_July_{det}_dac{dac}.cfg'


def fmt_v(v):
    """Format a setpoint for sub-run names: 540, 537.5, 400 (no trailing .0)."""
    return f'{v:g}'


def make_self_trig_sub_run(det, dac, offset, minutes, base_out_dir, k, tag='', events=None):
    """One self-trigger sub-run: triggers on `det` at global-DAC threshold `dac`, all four detectors
    ramped to (own max - offset) resist at DRIFT_V. Per-sub-run override selects the detector's
    per-DAC self-trig .cfg; pedestals come from the dream_daq_info defaults (latest real run). When
    `events` is given it overrides the dream_daq_info per-FEU event cap for this sub-run."""
    resists = {ch: v - offset for ch, v in RESIST_MAX.items()}
    det_resist = resists[DET_RESIST_CH[det]]
    sub = {
        'sub_run_name': f'trig{det}_dac{dac}_dr{DRIFT_V}_r{fmt_v(det_resist)}_{tag}{k:02d}',
        'run_time': minutes,           # Minutes
        'post_pause_s': 0,             # seconds; 0 = none
        'daq_config_template_path': f'{base_out_dir}{DREAM_CFG_INNER_DIR}{SELFTRIG_CFG_FMT.format(det=det, dac=dac)}',
        'hvs': {
            '5': resists,                                                   # Positive Resists (A/B/C/D on ch 1-4)
            '9': {'0': DRIFT_V, '1': DRIFT_V, '2': DRIFT_V, '3': DRIFT_V},  # Negative Drifts
        },
    }
    if events is not None:
        sub['daq_run_events'] = events
    return sub


class Config(RunConfigBase):
    def __init__(self, config_path=None):
        if not config_path:
            self._set_defaults()

        super().__init__(config_path)

    def _set_defaults(self, config_path=None):
        self.run_name = 'run_13'
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
            # 'n_samples_per_waveform': 400,  # Number of samples per waveform to configure in DAQ
            'n_samples_per_waveform': 32,  # Number of samples per waveform to configure in DAQ
            'go_timeout': 5 * 60,  # Seconds to wait for 'Go' response from RunCtrl before assuming failure
            'max_run_time_addition': 60 * 5,  # Seconds to add to requested run time before killing run
            'copy_on_fly': True,  # True to copy raw data to out dir during run, False to copy after run
            'batch_mode': True,  # Run Dream RunCtrl in batch mode. Not implemented for cosmic bench CPU.
            'zero_suppress': False,  # True to run in zero suppression mode, False to run in full readout mode
            'pedestals_dir': f'{self.base_out_dir}pedestals/',  # None to ignore, else top directory for pedestal runs
            'pedestals': 'latest',  # 'latest' for most recent, otherwise specify directory name, eg "pedestals_10-22-25_13-43-34"
            'latency': 2,  # Latency setting for DAQ in clock cycles (self-trig: matches May Self_Trig cfgs)
            'daq_run_events': MAIN_EVENTS,  # Per-FEU event cap per sub-run (0 = infinite); tail overrides it
            # 'sample_period': 20,  # ns, sampling period
            'sample_period': 60,  # ns, sampling period (-> DrmClk RdClk_Div/WrClk_Div 6.0 in cfg)
            'zs_check_sample': 4,  # Number of samples to read out beyond threshold crossing
            # True to auto-select the active FEUs in the .cfg from the included detectors' dream_feus maps.
            # Only the Sys Topo / Feu_RunCtrl_Id / NetChan_Ip lines for FEUs actually used by the included
            # detectors are left active; the rest are commented out. On each active Sys Topo line the per-
            # Dream roles are set to Dat for used connectors and Msk otherwise. nTof has no dedicated
            # trigger FEU (multiplicity coincidence), so trigger_feu stays None.
            # OFF for the self-trigger run: the per-detector self-trig .cfg carries the Trg roles,
            # and set_active_feus would overwrite every Dream role to Dat/Msk. All 8 FEUs are used
            # (all detectors read out) so nothing needs commenting out anyway.
            'set_feus_from_detectors': False,
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

        # ----- Self-trigger per-detector threshold + HV scan (built from constants above) -----
        # Main scan: triggering detector A->B->C->D, DAC threshold 127->105->89, and at each a
        # resist scan (all detectors ramped together, drift fixed). Then a high-statistics tail:
        # a coarse 3-point resist scan per detector at a single DAC. Each sub-run selects its own
        # per-detector per-DAC self-trig .cfg via override; pedestals are the latest real run.
        self.sub_runs = []
        for det in DETECTORS:
            for dac in THRESHOLDS_DAC:
                for k in range(MAIN_N_POINTS):
                    off = k * MAIN_RESIST_STEP
                    self.sub_runs.append(
                        make_self_trig_sub_run(det, dac, off, MAIN_SUBRUN_MIN, self.base_out_dir, k))
        for det in DETECTORS:
            for k, off in enumerate(TAIL_RESIST_OFFSETS):
                self.sub_runs.append(
                    make_self_trig_sub_run(det, TAIL_DAC, off, TAIL_SUBRUN_MIN,
                                           self.base_out_dir, k, tag='hi', events=TAIL_EVENTS))


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

    # Schedule summary — sanity-check timing and the scan structure.
    run_min = sum(sr['run_time'] for sr in config.sub_runs)
    n_sub = len(config.sub_runs)
    overhead_min = n_sub * OVERHEAD_MIN
    total_h = (run_min + overhead_min) / 60
    n_main = len(DETECTORS) * len(THRESHOLDS_DAC) * MAIN_N_POINTS
    n_tail = len(DETECTORS) * len(TAIL_RESIST_OFFSETS)
    print(f'Gas: {config.gas}')
    print(f'Run: {config.run_name}  (self-trigger, per-detector+DAC cfg — no n1081b scan watcher)')
    print(f'  Trigger cycle: {" -> ".join(DETECTORS)}  (each triggers on its own X&Y FEUs)')
    print(f'  Thresholds: Dream*1 DAC {THRESHOLDS_DAC}  (global discriminator DAC, high->low)')
    print(f'  Trigger: X-Y coincidence (both FEUs), HIT_multi>=4 ch/connector, <=1 Dream/FEU')
    print(f'  Main scan: {len(DETECTORS)} det x {len(THRESHOLDS_DAC)} DAC x {MAIN_N_POINTS} resist pts '
          f'(-{MAIN_RESIST_STEP} V/pt, drift {DRIFT_V} V), {MAIN_SUBRUN_MIN} min OR {MAIN_EVENTS} ev/FEU -> {n_main} sub-runs')
    print(f'  Hi-stat tail: {len(DETECTORS)} det x {len(TAIL_RESIST_OFFSETS)} resist pts (max-{TAIL_START_OFFSET} '
          f'down -{TAIL_RESIST_STEP} V) @ DAC {TAIL_DAC}, {TAIL_SUBRUN_MIN} min OR {TAIL_EVENTS} ev/FEU -> {n_tail} sub-runs')
    print(f'Sub-runs: {n_sub} total  (Det A max resist {RESIST_MAX["1"]} V; B/C/D offset-matched from own max)')
    print(f'Run time: {run_min} min run + ~{overhead_min} min overhead = ~{total_h:.2f} h '
          f'(user typically kills early)')

    print('donzo')
