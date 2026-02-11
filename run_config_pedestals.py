#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on April 29 9:37 PM 2024
Created in PyCharm
Created as Cosmic_Bench_DAQ_Control/run_config_template.py

@author: Dylan Neff, Dylan
"""

from datetime import datetime

from run_config_base import RunConfigBase


class Config(RunConfigBase):
    def __init__(self, config_path=None):
        if not config_path:
            self._set_defaults()

        super().__init__(config_path)

    def _set_defaults(self, config_path=None):
        date_time_str = datetime.now().strftime('%m-%d-%y_%H-%M-%S')
        self.run_name = f'pedestals_{date_time_str}'
        # self.base_out_dir = '/media/dylan/data/x17/'
        self.base_out_dir = '/mnt/data/x17/beam_feb/'
        self.data_out_dir = f'{self.base_out_dir}pedestals/'
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
        self.beam_type = 'neutrons'
        self.target_type = 'Timepix'

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
            # 'daq_config_template_path': f'{self.base_out_dir}dream_config/CosmicTb_MX17_ped.cfg',
            'daq_config_template_path': f'{self.base_out_dir}dream_config/Self_Tcm_MM_Mx17_Feb_test_ped.cfg',
            # 'run_directory': f'/mnt/data/beam_sps_25/dream_run/{self.run_name}/',
            # 'run_directory': f'{self.base_out_dir}/dream_run/{self.run_name}/',
            'run_directory': f'{self.run_out_dir}',
            'data_out_dir': f'{self.run_out_dir}',
            'raw_daq_inner_dir': self.raw_daq_inner_dir,
            'n_samples_per_waveform': 24,  # Number of samples per waveform to configure in DAQ
            'go_timeout': 5 * 60,  # Seconds to wait for 'Go' response from RunCtrl before assuming failure
            'max_run_time_addition': 60 * 5,  # Seconds to add to requested run time before killing run
            'copy_on_fly': True,  # True to copy raw data to out dir during run, False to copy after run
            'batch_mode': True,  # Run Dream RunCtrl in batch mode. Not implemented for cosmic bench CPU.
            'zero_suppress': False,  # True to run in zero suppression mode, False to run in full readout mode
            'pedestals_dir': f'{self.base_out_dir}pedestals/',  # None to ignore, else top directory for pedestal runs
            'pedestals': 'latest',  # 'latest' for most recent, otherwise specify directory name, eg "pedestals_10-22-25_13-43-34"
            'latency': 33,  # Latency setting for DAQ in clock cycles
            'sample_period': 40,  # ns, sampling period
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
            # 'ip': '192.168.10.81',
            'username': 'admin',
            'password': 'admin',
            'n_cards': 6,
            'n_channels_per_card': 12,
            'run_out_dir': self.run_out_dir,
            'hv_monitoring': True,  # True to monitor HV during run, False to not monitor
            'monitor_interval': 1,  # Seconds between HV monitoring
        }

        self.sub_runs = [
            {
                'sub_run_name': f'pedestals',
                'run_time': 10.0 / 60,  # Minutes
                'hvs': {
                    '2': {
                        '0': 300,
                    },
                    '5': {
                        '0': 300,
                    },
                }
            },
        ]

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
                'dream_feu_inversion': {  # If True, connector is inverted --> 1, 0, 3, 2 ...
                    'x_1': True,
                    'x_2': True,
                    'x_3': True,
                    'x_4': True,
                    'x_5': True,
                    'x_6': True,
                    'x_7': True,
                    'x_8': True,
                    'y_1': True,
                    'y_2': True,
                    'y_3': True,
                    'y_4': True,
                    'y_5': True,
                    'y_6': True,
                    'y_7': True,
                    'y_8': True,
                }
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
                'dream_feu_inversion': {  # If True, connector is inverted --> 1, 0, 3, 2 ...
                    'x_1': True,
                    'x_2': True,
                    'x_3': True,
                    'x_4': True,
                    'x_5': True,
                    'x_6': True,
                    'x_7': True,
                    'x_8': True,
                    'y_1': True,
                    'y_2': True,
                    'y_3': True,
                    'y_4': True,
                    'y_5': True,
                    'y_6': True,
                    'y_7': True,
                    'y_8': True,
                }
            },

        ]

        if not self.write_all_dectors_to_json:
            self.detectors = [det for det in self.detectors if det['name'] in self.included_detectors]

if __name__ == '__main__':
    out_run_dir = 'config/json_run_configs/'

    config_name = 'run_config_pedestals.json'

    config = Config()

    config.write_to_file(f'{out_run_dir}{config_name}')

    print('donzo')
