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
        self.run_name = 'run_1'
        self.base_out_dir = '/mnt/data/x17/beam_feb/'
        self.data_out_dir = f'{self.base_out_dir}runs/'
        self.run_out_dir = f'{self.data_out_dir}{self.run_name}/'
        self.raw_daq_inner_dir = 'raw_daq_data'
        self.decoded_root_inner_dir = 'decoded_root'
        self.detector_info_dir = f'{self.base_out_dir}config/detectors/'
        self.save_fdfs = True  # True to save FDF files, False to delete after decoding
        self.start_time = None
        self.process_on_fly = False  # True to process fdfs on the  fly.
        self.power_off_hv_at_end = False  # True to power off all CAEN HV at the end of the run.
        self.write_all_dectors_to_json = True  # Only when making run config json template. Maybe do always?
        self.gas = 'Ar/CF4/Iso 88/10/2'  # Gas type for run
        # self.gas = 'He/Eth 96.5/3.5'  # Gas type for run
        self.beam_type = 'neutrons'
        # self.beam_type = 'cosmics'
        self.target_type = 'carbon'
        # self.target_type = 'none'

        self.weiner_ps_info = {  # If this exists, check for Weiner LV before applying any HV
            'ip': '192.168.10.222',
            'channels': {  # Check only the channels which exist here
                'U0': {
                    'expected_voltage': 4.5,  # V
                    'expected_current': 30,  # A
                    'voltage_tolerance': 0.4,  # V
                    'current_tolerance': 5,  # A
                },
            }
        }

        self.dream_daq_info = {
            'ip': '192.168.10.8',
            'port': 1101,
            # 'daq_config_template_path': f'{self.base_out_dir}dream_config/Tcm_Mx17_SiPM.cfg',
            # 'daq_config_template_path': f'{self.base_out_dir}dream_config/CosmicTb_MX17.cfg',
            # 'daq_config_template_path': f'{self.base_out_dir}dream_config/Tcm_Mx17_Feb_test.cfg',
            # 'daq_config_template_path': f'{self.base_out_dir}dream_config/Tcm_Mx17_Feb_test_200fc.cfg',
            'daq_config_template_path': f'{self.base_out_dir}dream_config/Self_Tcm_MM_Mx17_Feb_test.cfg',
            # 'run_directory': f'/mnt/data/beam_sps_25/dream_run/{self.run_name}/',
            'run_directory': f'{self.base_out_dir}/dream_run/{self.run_name}/',
            'data_out_dir': f'{self.run_out_dir}',
            'raw_daq_inner_dir': self.raw_daq_inner_dir,
            'n_samples_per_waveform': 500,  # Number of samples per waveform to configure in DAQ
            'go_timeout': 5 * 60,  # Seconds to wait for 'Go' response from RunCtrl before assuming failure
            'max_run_time_addition': 60 * 5,  # Seconds to add to requested run time before killing run
            'copy_on_fly': True,  # True to copy raw data to out dir during run, False to copy after run
            'batch_mode': True,  # Run Dream RunCtrl in batch mode. Not implemented for cosmic bench CPU.
            'zero_suppress': False,  # True to run in zero suppression mode, False to run in full readout mode
            'pedestals_dir': f'{self.base_out_dir}pedestals/',  # None to ignore, else top directory for pedestal runs
            'pedestals': 'latest',  # 'latest' for most recent, otherwise specify directory name, eg "pedestals_10-22-25_13-43-34"
            # 'latency': 110,  # Latency setting for DAQ in clock cycles
            'latency': 1,  # Latency setting for DAQ in clock cycles
            'sample_period': 20,  # ns, sampling period
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
            'ip': '192.168.10.199',
            # # 'ip': '192.168.10.81',
            'username': 'admin',
            'password': 'admin',
            # 'ip': '128.141.177.244',
            'n_cards': 6,
            'n_channels_per_card': 12,
            'run_out_dir': self.run_out_dir,
            'hv_monitoring': True,  # True to monitor HV during run, False to not monitor
            'monitor_interval': 1,  # Seconds between HV monitoring
        }

        # with open('hv_creds.txt') as f:
        #     lines = f.readlines()
        #     self.hv_info['username'] = lines[0].strip()
        #     self.hv_info['password'] = lines[1].strip()

        self.sub_runs = [
            # {
            #     'sub_run_name': f'initial_resist_440V_drift_600V',
            #     'run_time': 60 * 1.5,  # Minutes
            #     'hvs': {
            #         '2': {
            #             '0': 440,
            #         },
            #         '5': {
            #             '0': 600,
            #         },
            #     }
            # },
            # {
            #     'sub_run_name': f'resist_0V_drift_0V',
            #     'run_time': 5,  # Minutes
            #     'hvs': {
            #         '2': {
            #             '0': 0,
            #         },
            #         '5': {
            #             '0': 0,
            #         },
            #     }
            # },
            # {
            #     'sub_run_name': f'resist_0V_drift_300V',
            #     'run_time': 5,  # Minutes
            #     'hvs': {
            #         '2': {
            #             '0': 0,
            #         },
            #         '5': {
            #             '0': 300,
            #         },
            #     }
            # },
            # {
            #     'sub_run_name': f'resist_hv_420V_drift_600V',
            #     'run_time': 5,  # Minutes
            #     'hvs': {
            #         '2': {
            #             '0': 420,
            #         },
            #         '5': {
            #             '0': 600,
            #         },
            #     }
            # },
        ]

        # Add more hv_subruns
        # hvs = list(range(0, 300, 20))
        # hvs.extend(list(range(300, 520, 10)))
        # hvs = [400, 425, 450, 475, 485, 500, 510]
        hvs = [475, 485, 500, 510]
        for hv in hvs:
            new_subrun = {
                'sub_run_name': f'resist_{hv}V_drift_600V',
                'run_time': 10,  # Minutes
                'hvs': {
                    '2': {
                        '0': hv,
                    },
                    '5': {
                        '0': 600,
                    },
                }
            }
            self.sub_runs.append(new_subrun)

        self.sub_runs.append({
                'sub_run_name': f'final_resist_490V_drift_600V',
                'run_time': 60 * 24,  # Minutes
                'hvs': {
                    '2': {
                        '0': 490,
                    },
                    '5': {
                        '0': 600,
                    },
                }
            })

        self.bench_geometry = {
            'board_thickness': 5,  # mm  Thickness of PCB for test boards  Guess!
        }

        self.included_detectors = ['mx17_1']

        self.detectors = [
            {
                'name': 'mx17_1',
                'det_type': 'mx17',
                'resist_type': 'strip_with_silver_paste',
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
                    'drift': (5, 0),
                    'resist': (2, 0),
                },
                'dream_feus': {
                    'x_1': (4, 1),  # Runs along x direction, indicates y hit location
                    'x_2': (4, 2),
                    'x_3': (4, 3),
                    'x_4': (4, 4),
                    'x_5': (4, 5),
                    'x_6': (4, 6),
                    'x_7': (4, 7),
                    'x_8': (4, 8),
                    'y_1': (5, 1),  # Runs along y direction, indicates x hit location
                    'y_2': (5, 2),
                    'y_3': (5, 3),
                    'y_4': (5, 4),
                    'y_5': (5, 5),
                    'y_6': (5, 6),
                    'y_7': (5, 7),
                    'y_8': (5, 8),
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
                'name': 'mx17_2',
                'det_type': 'mx17',
                'resist_type': 'strip_resit_with_plein_on_top',
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
                    'drift': (0, 7),
                    'resist': (3, 0),
                },
                'dream_feus': {
                    'x_1': (4, 1),  # Runs along x direction, indicates y hit location
                    'x_2': (4, 2),
                    'x_3': (4, 3),
                    'x_4': (4, 4),
                    'x_5': (4, 5),
                    'x_6': (4, 6),
                    'x_7': (4, 7),
                    'x_8': (4, 8),
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
            },

        ]

        if not self.write_all_dectors_to_json:
            self.detectors = [det for det in self.detectors if det['name'] in self.included_detectors]

if __name__ == '__main__':
    out_run_dir = 'config/json_run_configs/'

    config_name = 'run_config_beam.json'

    config = Config()

    config.write_to_file(f'{out_run_dir}{config_name}')

    print('donzo')
