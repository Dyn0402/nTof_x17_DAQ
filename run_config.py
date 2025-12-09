#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on April 29 9:37 PM 2024
Created in PyCharm
Created as Cosmic_Bench_DAQ_Control/run_config_template.py

@author: Dylan Neff, Dylan
"""

import sys
import json
import copy


class Config:
    def __init__(self, config_path=None):
        self.run_name = 'run_199'
        self.base_out_dir = '/mnt/data/beam_sps_25/'
        self.data_out_dir = f'{self.base_out_dir}Run/'
        self.run_out_dir = f'{self.data_out_dir}{self.run_name}/'
        self.raw_daq_inner_dir = 'raw_daq_data'
        self.decoded_root_inner_dir = 'decoded_root'
        self.detector_info_dir = f'{self.base_out_dir}config/detectors/'
        self.start_time = None
        self.write_all_dectors_to_json = True  # Only when making run config json template. Maybe do always?
        self.gas = 'Ar/CF4/Iso 88/10/2'  # Gas type for run
        self.beam_type = 'neutrons'
        self.target_type = 'Boron?'

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
            'daq_config_template_path': f'{self.base_out_dir}dream_run/config/TbSPS25.cfg',
            # 'run_directory': f'/mnt/data/beam_sps_25/dream_run/{self.run_name}/',
            'run_directory': f'/local/home/banco/beam_test_2025/Run/{self.run_name}/',
            'data_out_dir': f'{self.base_out_dir}Run/{self.run_name}',
            'raw_daq_inner_dir': self.raw_daq_inner_dir,
            'n_samples_per_waveform': 24,  # Number of samples per waveform to configure in DAQ
            'go_timeout': 5 * 60,  # Seconds to wait for 'Go' response from RunCtrl before assuming failure
            'max_run_time_addition': 60 * 5,  # Seconds to add to requested run time before killing run
            'copy_on_fly': True,  # True to copy raw data to out dir during run, False to copy after run
            'batch_mode': True,  # Run Dream RunCtrl in batch mode. Not implemented for cosmic bench CPU.
            'zero_suppress': True,  # True to run in zero suppression mode, False to run in full readout mode
            'pedestals_dir': f'{self.base_out_dir}pedestals_noise/',  # None to ignore, else top directory for pedestal runs
            'pedestals': 'latest',  # 'latest' for most recent, otherwise specify directory name, eg "pedestals_10-22-25_13-43-34"
            'latency': 33,  # Latency setting for DAQ in clock cycles
            'sample_period': 40,  # ns, sampling period
            'samples_beyond_threshold': 4,  # Number of samples to read out beyond threshold crossing
        }

        self.dedip196_processor_info = {
            'ip': '192.168.10.8',
            'port': 1200,
            'run_dir': f'{self.base_out_dir}Run/{self.run_name}',
            'raw_daq_inner_dir': self.raw_daq_inner_dir,
            'decoded_root_inner_dir': self.decoded_root_inner_dir,
            'decode_path': '/local/home/banco/dylan/decode/decode',
            'convert_path': '/local/home/banco/dylan/decode/convert_vec_tree_to_array',
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
                'sub_run_name': f'run',
                'run_time': 10,  # Minutes
                'hvs': {
                    '1': {
                        '1': 600,
                        '2': 600,
                    },
                    '5': {
                        '0': 500,
                        '1': 500,
                        '2': 470,
                    },
                }
            },
        ]

        self.bench_geometry = {
            'board_thickness': 5,  # mm  Thickness of PCB for test boards  Guess!
        }

        self.included_detectors = ['rd5_plein_saral_2','rd5_strip_saral_1']

        self.detectors = [
            {
                'name': 'rd5_plein_saral_2',
                'det_type': 'rd5_plein_saral',
                'det_center_coords': {  # Center of detector
                    'x': -200,  # mm
                    'y': +400,  # mm
                    'z': +100,  # mm
                },
                'det_orientation': {
                    'x': 0,  # deg  Rotation about x axis
                    'y': +90,  # deg  Rotation about y axis
                    'z': 0,  # deg  Rotation about z axis
                },
                'hv_channels': {
                    'drift': (1, 1),
                    'resist_2': (5, 2)
                },
                'dream_feus': {
                    'x_1': (5, 8),  # Unplugged!
                    'x_2': (5, 5),  # Runs along x direction, indicates y hit location
                    'y_1': (5, 6),  # Runs along y direction, indicates x hit location
                    'y_2': (5, 7),
                },
                'dream_feu_inversion': {  # If True, connector is inverted --> 1, 0, 3, 2 ...
                    'x_1': True,
                    'x_2': True,
                    'y_1': False,
                    'y_2': False,
                },
                'kel_connectors': '100 cm kel cable on y_2 (Feu 1 channel 8). With 2 1.5m bluejean cables.'
            },

            {
                'name': 'rd5_strip_saral_1',
                'det_type': 'rd5_strip_saral',
                'det_center_coords': {  # Center of detector
                    'x': -200,  # mm
                    'y': +500,  # mm
                    'z': -100 ,  # mm
                },
                'det_orientation': {
                    'x': 0,  # deg  Rotation about x axis
                    'y': +90,  # deg  Rotation about y axis
                    'z': 0,  # deg  Rotation about z axis
                },
                'hv_channels': {
                    'drift': (1, 2),
                    'resist_1': (5, 0),
                    'resist_2': (5, 1)
                },
                'dream_feus': {
                    'x_1': (5, 1),  # Runs along x direction, indicates y hit location
                    'x_2': (5, 2),
                    'y_1': (5, 3),  # Runs along y direction, indicates x hit location
                    'y_2': (5, 4),
                },
                'dream_feu_inversion': {  # If True, connector is inverted --> 1, 0, 3, 2 ...
                    'x_1': True,
                    'x_2': True,
                    'y_1': False,
                    'y_2': False,
                }
            },

        ]

        if not self.write_all_dectors_to_json:
            self.detectors = [det for det in self.detectors if det['name'] in self.included_detectors]

        if config_path:  # Clear everything and load from file
            self.load_from_file(config_path)

    def write_to_file(self, file_path):
        with open(file_path, 'w') as file:
            json.dump(self.__dict__, file, indent=4)

    def load_from_file(self, file_path):
        with open(file_path, 'r') as file:
            data = json.load(file)
            self.__dict__.clear()
            self.__dict__.update(data)


if __name__ == '__main__':
    out_run_dir = 'config/json_run_configs/'

    config_name = 'run_config_beam.json'

    config = Config()

    config.write_to_file(f'{out_run_dir}{config_name}')

    print('donzo')
