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


class Config(RunConfigBase):
    def __init__(self, config_path=None):
        if not config_path:
            self._set_defaults()

        super().__init__(config_path)

    def _set_defaults(self, config_path=None):
        self.run_name = 'run_1'
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
        self.gas = 'Ar/CF4/Iso 88/10/2'  # Gas type for run
        # self.gas = 'He/Eth 96.5/3.5'  # Gas type for run
        # self.gas = 'Ne/Iso 95/5'  # Gas type for run
        # self.beam_type = 'cosmics'
        # self.beam_type = 'neutrons'
        # self.beam_type = 'cosmics+beam'
        # self.beam_type = 'bi-207'
        # self.beam_type = 'cs-137'
        self.beam_type = 'sr-90'
        # self.target_type = 'carbon'
        # self.target_type = 'B4C - 2.5mm (thinner)'
        # self.target_type = 'B4C - 5mm (thicker)'
        # self.target_type = 'Lead'
        # self.target_type = 'empty target holder'
        self.target_type = 'none'
        self.trigger = "Det 3 SiPM Wall + Det 3 Scint"

        self.dream_daq_info = {
            'ip': '192.168.10.8',
            'port': 1101,
            # 'daq_config_template_path': f'{self.base_out_dir}dream_config/Tcm_Mx17_May.cfg',
            'daq_config_template_path': f'{self.base_out_dir}dream_config/Tcm_Mx17_July.cfg',
            # 'daq_config_template_path': f'{self.base_out_dir}dream_config/Cosmics_Mx17_May.cfg',
            # 'daq_config_template_path': f'{self.base_out_dir}dream_config/Self_Trig_QA.cfg',
            # 'daq_config_template_path': f'{self.base_out_dir}dream_config/Self_Trig_det3_QA.cfg',

            'run_directory': f'{self.base_out_dir}/dream_run/{self.run_name}/',
            'data_out_dir': f'{self.run_out_dir}',
            'raw_daq_inner_dir': self.raw_daq_inner_dir,
            'n_samples_per_waveform': 400,  # Number of samples per waveform to configure in DAQ
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

        scint_A_HV, scint_B_HV = 1300, 1300
        # r0_init, r1_init, d0_init, d1_init = 635, 635, 800, 800
        r0_init, r1_init, d0_init, d1_init = 535, 535, 600, 600
        self.sub_runs = [
            # {
            #     'sub_run_name': f'long_run',
            #     'run_time': 60 * 24,  # Minutes
            #     'hvs': {
            #         '5': {  # Positive Resists
            #             '0': 530,  # Det
            #             '1': 530,
            #         },
            #         '9': {  # Negative Drifts
            #             '0': 600,
            #             '1': 600,
            #         },
            #         # '8': {  # PMTs
            #         #     '0': scint_A_HV,  # Top
            #         #     '1': scint_B_HV,  # Bottom
            #         # },
            #     }
            # },
            # {
            #     'sub_run_name': f'quick_run_10min',
            #     'run_time': 10,  # Minutes
            #     'hvs': {
            #         '5': {  # Positive Resists
            #             # '0': r0_init,  # mx17_3 30mm drift
            #             '1': r1_init,  # mx17_4 3.6mm drift
            #         },
            #         '9': {  # Negative Drifts
            #             # '0': d0_init,  # mx17_3 30mm drift
            #             '1': d1_init,  # mx17_4 3.6mm drift
            #         },
            #         '8': {  # PMTs
            #             '0': scint_A_HV,  # Top
            #             '1': scint_B_HV,  # Bottom
            #         },
            #     }
            # },
            # {
            #     # 'sub_run_name': f'gas_change',
            #     'sub_run_name': f'no_beam',
            #     'run_time': 6 * 60,  # Minutes
            #     'hvs': {
            #         '5': {  # Positive Resists
            #             '0': r0_init,  # mx17_3 30mm drift
            #             '1': r1_init,  # mx17_4 3.6mm drift
            #         },
            #         '9': {  # Negative Drifts
            #             '0': d0_init,  # mx17_3 30mm drift
            #             '1': d1_init,  # mx17_4 3.6mm drift
            #         },
            #         # '8': {  # PMTs
            #         #     '0': scint_A_HV,  # Top
            #         #     '1': scint_B_HV,  # Bottom
            #         # },
            #     }
            # },
        ]

        # drifts_0 = [600, 300, 100, 0]
        # drifts_1 = [600, 300, 100, 0]
        #
        # v_step, n_steps = 5, 20
        # above_r_init = 50
        # resists_0 = [r0_init + above_r_init - i * v_step for i in range(n_steps)]
        # resists_1 = [r1_init + above_r_init - i * v_step for i in range(n_steps)]
        #
        # hv_scan_i = 0
        # for drift_0, drift_1 in zip(drifts_0, drifts_1):
        #     scan_step_time = 20 if drift_0 == 600 else 5
        #     for resist_0, resist_1 in zip(resists_0, resists_1):
        #         new_subrun = {
        #             'sub_run_name': f'hv_scan_drift_{drift_0}_resist_{resist_0}',
        #             'run_time': scan_step_time,  # Minutes
        #             'hvs': {
        #                 '5': {  # Positive Resists
        #                     '0': resist_0,  # mx17_3 30mm drift
        #                     '1': resist_1,  # mx17_4 3.6mm drift
        #                 },
        #                 '9': {  # Negative Drifts
        #                     '0': drift_0,  # mx17_3 30mm drift
        #                     '1': drift_1,  # mx17_4 3.6mm drift
        #                 },
        #                 # '8': {  # PMTs
        #                 #     '0': scint_A_HV,  # Top
        #                 #     '1': scint_B_HV,  # Bottom
        #                 # },
        #             }
        #         }
        #
        #         self.sub_runs.append(new_subrun)
        #         hv_scan_i += 1

        # drifts_0 = [600]
        # drifts_1 = [600]
        #
        # v_step, n_steps = 5, 40
        # resists_0 = [r0_init - i * v_step for i in range(n_steps)]
        # resists_1 = [r1_init - i * v_step for i in range(n_steps)]
        #
        # # resists_0 = [525, 505, 495, 515]
        # # resists_1 = [525, 505, 495, 515]
        #
        # hv_scan_i = 0
        # for drift_0, drift_1 in zip(drifts_0, drifts_1):
        #     for resist_0, resist_1 in zip(resists_0, resists_1):
        #         # scan_step_time = 60 * 24 if resist_0 == 600 and drift_0 == 600 else 30
        #         scan_step_time = 1
        #         new_subrun = {
        #             'sub_run_name': f'hv_scan_drift_{drift_0}_resist_{resist_0}',
        #             'run_time': scan_step_time,  # Minutes
        #             'hvs': {
        #                 '5': {  # Positive Resists
        #                     '0': resist_0,  # mx17_3 30mm drift
        #                     '1': resist_1,  # mx17_4 3.6mm drift
        #                 },
        #                 '9': {  # Negative Drifts
        #                     '0': drift_0,  # mx17_3 30mm drift
        #                     '1': drift_1,  # mx17_4 3.6mm drift
        #                 },
        #                 # '8': {  # PMTs
        #                 #     '0': scint_A_HV,  # Top
        #                 #     '1': scint_B_HV,  # Bottom
        #                 # },
        #             }
        #         }
        #
        #         self.sub_runs.append(new_subrun)
        #         hv_scan_i += 1
        #
        # new_subrun = {
        #     'sub_run_name': f'long_run',
        #     'run_time': 60 * 3,  # Minutes
        #     'hvs': {
        #         '5': {  # Positive Resists
        #             '0': 790,  # mx17_3 30mm drift
        #             '1': 790,  # mx17_4 3.6mm drift
        #         },
        #         '9': {  # Negative Drifts
        #             '0': 600,  # mx17_3 30mm drift
        #             '1': 600,  # mx17_4 3.6mm drift
        #         },
        #         # '8': {  # PMTs
        #         #     '0': scint_A_HV,  # Top
        #         #     '1': scint_B_HV,  # Bottom
        #         # },
        #     }
        # }

        for i in range(20):
            new_subrun = {
                'sub_run_name': f'run{i}',
                'run_time': 60 * 3,  # Minutes
                'hvs': {
                    '5': {  # Positive Resists
                        '1': 800,  # mx17_4 3.6mm drift
                    },
                    '9': {  # Negative Drifts
                        '1': 600,  # mx17_4 3.6mm drift
                    },
                }
            }
            self.sub_runs.append(new_subrun)

        # v_start, v_step, n_steps = 800, 10, 10
        # resist_scan_voltages = [v_start + i * v_step for i in range(n_steps)]
        # for resist_v in resist_scan_voltages:
        #     self.sub_runs.append({
        #         'sub_run_name': f'hv_scan_resist_{resist_v}V',
        #         'run_time': 5,  # Minutes
        #         'hvs': {
        #             '5': {  # Positive Resists
        #                 '1': resist_v,
        #             },
        #             '9': {  # Negative Drifts
        #                 '1': 600,
        #             },
        #         }
        #     })
        #
        # v_step, n_steps = 10, 10
        # resists_0 = [r0_init - i * v_step for i in range(n_steps)]
        # resists_1 = [r1_init - i * v_step for i in range(n_steps)]
        #
        # scan_step_time = 10
        # hv_scan_i = 0
        # for drift_0, drift_1 in zip(drifts_0, drifts_1):
        #     for resist_0, resist_1 in zip(resists_0, resists_1):
        #         new_subrun = {
        #             'sub_run_name': f'hv_scan_b_{hv_scan_i}',
        #             'run_time': scan_step_time,  # Minutes
        #             'hvs': {
        #                 '5': {  # Positive Resists
        #                     '0': resist_0,  # mx17_3 30mm drift
        #                     '1': resist_1,  # mx17_4 3.6mm drift
        #                 },
        #                 '9': {  # Negative Drifts
        #                     '0': drift_0,  # mx17_3 30mm drift
        #                     '1': drift_1,  # mx17_4 3.6mm drift
        #                 },
        #                 '8': {  # PMTs
        #                     '0': scint_A_HV,  # Top
        #                     '1': scint_B_HV,  # Bottom
        #                 },
        #             }
        #         }
        #
        #         self.sub_runs.append(new_subrun)
        #         hv_scan_i += 1

        # Resist HV scan: 800V (1hr) → step down 10V/30min (790→720V, 8 steps, 4hr) → 790V long run (4hr) = 9hr total
        # resist_scan_steps = (
        #     [(800, 60 * 2, 'resist_initial_800V')] +
        #     [(800 - 10 * i, 30, f'resist_scan_{800 - 10 * i}V') for i in range(1, 9)] +
        #     [(790, 60 * 10, 'resist_final_790V')]
        # )
        # for resist_v, run_time, sub_run_name in resist_scan_steps:
        #     self.sub_runs.append({
        #         'sub_run_name': sub_run_name,
        #         'run_time': run_time,
        #         'hvs': {
        #             '5': {  # Positive Resists
        #                 '0': resist_v,
        #                 '1': resist_v,
        #             },
        #             '9': {  # Negative Drifts (monitor only, set manually)
        #                 '0': 600,
        #                 '1': 600,
        #             },
        #         }
        #     })


        self.bench_geometry = {
            'board_thickness': 5,  # mm  Thickness of PCB for test boards  Guess!
        }

        self.included_detectors = ['mx17_A', 'mx17_B', 'mx17_C', 'mx17_D']
        # self.included_detectors = ['mx17_A', 'mx17_B', 'mx17_C', 'mx17_D']

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

    print('donzo')
