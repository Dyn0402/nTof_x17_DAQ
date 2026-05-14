#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on April 29 9:37 PM 2024
Created in PyCharm
Created as Cosmic_Bench_DAQ_Control/run_config_template.py

@author: Dylan Neff, Dylan
"""

from run_config_base import RunConfigBase


class Config(RunConfigBase):
    def __init__(self, config_path=None):
        if not config_path:
            self._set_defaults()

        super().__init__(config_path)

    def _set_defaults(self, config_path=None):
        self.run_name = 'run_38'
        self.base_out_dir = '/mnt/data/x17/beam_may/'
        self.data_out_dir = f'{self.base_out_dir}runs/'
        self.run_out_dir = f'{self.data_out_dir}{self.run_name}/'
        self.raw_daq_inner_dir = 'raw_daq_data'
        self.decoded_root_inner_dir = 'decoded_root'
        self.detector_info_dir = f'{self.base_out_dir}config/detectors/'
        self.save_fdfs = True  # True to save FDF files, False to delete after decoding
        self.start_time = None
        self.process_on_fly = False  # True to process fdfs on the fly.
        self.power_off_hv_at_end = False  # True to power off all CAEN HV at the end of the run.
        self.write_all_detectors_to_json = True  # Only when making run config json template. Maybe do always?
        # self.gas = 'Ar/CF4/CO2 45/40/15'  # Gas type for run
        self.gas = 'Ar/CF4 90/10'  # Gas type for run
        # self.gas = 'Ar/CO2 70/30'  # Gas type for run
        # self.gas = 'Ar/CF4/Iso 88/10/2'  # Gas type for run
        # self.gas = 'He/Eth 96.5/3.5'  # Gas type for run
        # self.beam_type = 'cosmics'
        self.beam_type = 'neutrons'
        # self.beam_type = 'cosmics+beam'
        # self.beam_type = 'bi-207'
        # self.beam_type = 'cs-137'
        # self.target_type = 'carbon'
        self.target_type = 'B4C - 2.5mm (thinner)'
        # self.target_type = 'B4C - 5mm (thicker)'
        # self.target_type = 'Lead'
        # self.target_type = 'empty target holder'
        # self.target_type = 'none'

        self.dream_daq_info = {
            'ip': '192.168.10.8',
            'port': 1101,
            # 'daq_config_template_path': f'{self.base_out_dir}dream_config/Tcm_Mx17_May.cfg',
            'daq_config_template_path': f'{self.base_out_dir}dream_config/Tcm_Mx17_May_Coinc.cfg',
            # 'daq_config_template_path': f'{self.base_out_dir}dream_config/Cosmics_Mx17_May.cfg',
            # 'daq_config_template_path': f'{self.base_out_dir}dream_config/Self_Trig_QA.cfg',
            # 'daq_config_template_path': f'{self.base_out_dir}dream_config/Self_Trig_det3_QA.cfg',

            'run_directory': f'{self.base_out_dir}/dream_run/{self.run_name}/',
            'data_out_dir': f'{self.run_out_dir}',
            'raw_daq_inner_dir': self.raw_daq_inner_dir,
            # 'n_samples_per_waveform': 100,  # Number of samples per waveform to configure in DAQ
            # 'n_samples_per_waveform': 390,  # Number of samples per waveform to configure in DAQ
            # 'n_samples_per_waveform': 510,  # Number of samples per waveform to configure in DAQ
            # 'n_samples_per_waveform': 450,  # Number of samples per waveform to configure in DAQ
            # 'n_samples_per_waveform': 400,  # Number of samples per waveform to configure in DAQ
            # 'n_samples_per_waveform': 32,  # Number of samples per waveform to configure in DAQ
            # 'n_samples_per_waveform': 100,  # Number of samples per waveform to configure in DAQ
            'n_samples_per_waveform': 300,  # Number of samples per waveform to configure in DAQ
            'go_timeout': 5 * 60,  # Seconds to wait for 'Go' response from RunCtrl before assuming failure
            'max_run_time_addition': 60 * 5,  # Seconds to add to requested run time before killing run
            'copy_on_fly': True,  # True to copy raw data to out dir during run, False to copy after run
            'batch_mode': True,  # Run Dream RunCtrl in batch mode. Not implemented for cosmic bench CPU.
            'zero_suppress': True,  # True to run in zero suppression mode, False to run in full readout mode
            'pedestals_dir': f'{self.base_out_dir}pedestals/',  # None to ignore, else top directory for pedestal runs
            'pedestals': 'latest',  # 'latest' for most recent, otherwise specify directory name, eg "pedestals_10-22-25_13-43-34"
            # 'latency': 90,  # Latency setting for DAQ in clock cycles
            # 'latency': 100,  # Latency setting for DAQ in clock cycles
            # 'latency': 2,  # Latency setting for DAQ in clock cycles
            'latency': 190,  # Latency setting for DAQ in clock cycles
            # 'latency': 24,  # Latency setting for DAQ in clock cycles
            'sample_period': 20,  # ns, sampling period
            # 'sample_period': 60,  # ns, sampling period
            'samples_beyond_threshold': 4,  # Number of samples to read out beyond threshold crossing
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
        r0_init, r1_init, d0_init, d1_init = 635, 635, 800, 800
        self.sub_runs = [
            # {
            #     'sub_run_name': f'long_run',
            #     'run_time': 60 * 24,  # Minutes
            #     'hvs': {
            #         '5': {  # Positive Resists
            #             '0': 635,  # Det
            #             '1': 635,
            #         },
            #         '9': {  # Negative Drifts
            #             '0': 800,
            #             '1': 800,
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
            #     'sub_run_name': f'gas_change',
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
            #         '8': {  # PMTs
            #             '0': scint_A_HV,  # Top
            #             '1': scint_B_HV,  # Bottom
            #         },
            #     }
            # },
        ]

        # drifts_0 = [800, 400]
        # drifts_1 = [800, 400]
        #
        # v_step, n_steps = 15, 20
        # resists_0 = [r0_init - i * v_step for i in range(n_steps)]
        # resists_1 = [r1_init - i * v_step for i in range(n_steps)]
        #
        # scan_step_time = 5
        # hv_scan_i = 0
        # for drift_0, drift_1 in zip(drifts_0, drifts_1):
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
        #
        # new_subrun = {
        #     'sub_run_name': f'long_run',
        #     'run_time': 60 * 3,  # Minutes
        #     'hvs': {
        #         '5': {  # Positive Resists
        #             '0': 640,  # mx17_3 30mm drift
        #             '1': 640,  # mx17_4 3.6mm drift
        #         },
        #         '9': {  # Negative Drifts
        #             '0': drift_0,  # mx17_3 30mm drift
        #             '1': drift_1,  # mx17_4 3.6mm drift
        #         },
        #         '8': {  # PMTs
        #             '0': scint_A_HV,  # Top
        #             '1': scint_B_HV,  # Bottom
        #         },
        #     }
        # }
        #
        # self.sub_runs.append(new_subrun)
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

        for i in range(20):
            new_subrun = {
                'sub_run_name': f'run_{i}',
                'run_time': 60 * 3,  # Minutes
                'hvs': {
                    '5': {  # Positive Resists
                        '0': 645,  # mx17_3 30mm drift
                        '1': 645,  # mx17_4 3.6mm drift
                    },
                    '9': {  # Negative Drifts
                        '0': 800,  # mx17_3 30mm drift
                        '1': 800,  # mx17_4 3.6mm drift
                    },
                }
            }
            self.sub_runs.append(new_subrun)


        self.bench_geometry = {
            'board_thickness': 5,  # mm  Thickness of PCB for test boards  Guess!
        }

        # self.included_detectors = ['mx17_3', 'mx17_4', 'scint_A', 'scint_B']
        self.included_detectors = ['mx17_3', 'mx17_4']

        self.detectors = [
            {
                'name': 'mx17_3',
                'det_type': 'mx17',
                'resist_type': 'strip',
                'drift_gap': '16 mm',
                'frame_type': 'aluminum',  # carbon or aluminum
                # 'distance_from_target': 20, # cm from target
                'aluminum_shielding': False,
                'det_center_coords': {  # Center of detector
                    'x': 0,  # mm
                    'y': 0,  # mm
                    'z': 0,  # mm
                },
                'det_orientation': {
                    'x': 0,  # deg  Rotation about x axis
                    'y': 0,  # deg  Rotation about y axis
                    'z': 0,  # deg  Rotation about z axis
                },
                'hv_channels': {
                    'drift': (9, 0),
                    'resist': (5, 0),
                },
                'mx_cards': '4 M1',
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
            },
            {
                'name': 'mx17_4',
                'det_type': 'mx17',
                'resist_type': 'strip',
                'drift_gap': '30 mm',
                'frame_type': 'aluminum',  # carbon or aluminum
                # 'distance_from_target': 20,  # cm from target
                'aluminum_shielding': True,
                'det_center_coords': {  # Center of detector
                    'x': 0,  # mm
                    'y': 0,  # mm
                    'z': 0,  # mm
                },
                'det_orientation': {
                    'x': 0,  # deg  Rotation about x axis
                    'y': 0,  # deg  Rotation about y axis
                    'z': 0,  # deg  Rotation about z axis
                },
                'hv_channels': {
                    'drift': (9, 1),
                    'resist': (5, 1),
                },
                'mx_cards': '4 M2',
                'dream_feus': {
                    'x_1': (3, 1),  # Runs along x direction, indicates y hit location
                    'x_2': (3, 2),
                    'x_3': (3, 3),
                    'x_4': (3, 4),
                    'y_1': (3, 5),  # Runs along y direction, indicates x hit location
                    'y_2': (3, 6),
                    'y_3': (3, 7),
                    'y_4': (3, 8),
                },
                'dream_feu_orientation': {  # If connector is normal, inverted, rotated, or rotated_inverted
                    'x_1': 'inverted',
                    'x_2': 'inverted',
                    'x_3': 'inverted',
                    'x_4': 'inverted',
                    'y_1': 'inverted',
                    'y_2': 'inverted',
                    'y_3': 'inverted',
                    'y_4': 'inverted',
                },
                # 'dream_feus': {
                #     'x_1': (3, 1),  # Runs along x direction, indicates y hit location
                #     'x_2': (3, 2),
                #     'x_3': (3, 3),
                #     'x_4': (3, 4),
                #     'x_5': (3, 5),
                #     'x_6': (3, 6),
                #     'x_7': (3, 7),
                #     'x_8': (3, 8),
                #     'y_1': (4, 1),  # Runs along y direction, indicates x hit location
                #     'y_2': (4, 2),
                #     'y_3': (4, 3),
                #     'y_4': (4, 4),
                #     'y_5': (4, 5),
                #     'y_6': (4, 6),
                #     'y_7': (4, 7),
                #     'y_8': (4, 8),
                # },
                # 'dream_feu_orientation': {  # If connector is normal, inverted, rotated, or rotated_inverted
                #     'x_1': 'inverted',
                #     'x_2': 'inverted',
                #     'x_3': 'inverted',
                #     'x_4': 'inverted',
                #     'x_5': 'inverted',
                #     'x_6': 'inverted',
                #     'x_7': 'inverted',
                #     'x_8': 'inverted',
                #     'y_1': 'inverted',
                #     'y_2': 'inverted',
                #     'y_3': 'inverted',
                #     'y_4': 'inverted',
                #     'y_5': 'inverted',
                #     'y_6': 'inverted',
                #     'y_7': 'inverted',
                #     'y_8': 'inverted',
                # },
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

if __name__ == '__main__':
    out_run_dir = 'config/json_run_configs/'

    config_name = 'run_config_beam.json'

    config = Config()

    config.write_to_file(f'{out_run_dir}{config_name}')

    print('donzo')
