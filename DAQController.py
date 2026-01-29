#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on April 29 11:13 AM 2024
Created in PyCharm
Created as Cosmic_Bench_DAQ_Control/DAQController.py

@author: Dylan Neff, Dylan
"""

import os
import subprocess
from time import time


class DAQController:
    def __init__(self, cfg_template_file_path=None, run_time=10, out_name=None, out_dir=None, dream_daq_client=None):
        self.cfg_template_file_path = cfg_template_file_path
        self.out_directory = out_dir
        self.out_name = out_name
        self.dream_daq_client = dream_daq_client
        self.original_working_directory = os.getcwd()

        self.run_time = run_time  # minutes
        self.run_start_time = None
        self.measured_run_time = None

        # self.stop_dream_sh_path = '/home/mx17/PycharmProjects/nTof_x17_DAQ/bash_scripts/stop_dream.sh'
        self.stop_dream_sh_path = './bash_scripts/stop_dream.sh'

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        os.chdir(self.original_working_directory)

    def run(self):
        run_successful = True

        try:
            self.dream_daq_client.send(f'Start {self.out_name} {self.run_time} {self.run_time}')
            res = self.dream_daq_client.receive()
            if res != 'Dream DAQ starting':
                print('Error starting Dream DAQ')
                return False
            self.run_start_time = time()

            res = self.dream_daq_client.receive()  # Wait for dream daq to finish
            if res != 'Dream DAQ stopped':
                print('Error stopping DAQ')
                return False

            self.measured_run_time = time() - self.run_start_time


        except KeyboardInterrupt:
            print('Keyboard interrupt. Stopping DAQ process.')
            # Run stop dream sh
            ret = subprocess.call([self.stop_dream_sh_path])
            if ret != 0:
                print('Error stopping Dream DAQ via stop_dream_daq.sh script.')
            run_successful = False

            if self.run_start_time is not None:
                self.measured_run_time = time() - self.run_start_time
                run_successful = True  # Low bar for a successful run, but maybe ok?
            else:
                self.measured_run_time = 0
            # self.dream_daq_client.send('Stop')
            res = self.dream_daq_client.receive()
            if res != 'Dream DAQ stopped':
                print('Error stopping Dream DAQ')
        finally:
            print('Dream Subrun complete.')
            if self.measured_run_time is None:
                if self.run_start_time is None:
                    self.measured_run_time = 0
                else:
                    self.measured_run_time = time() - self.run_start_time

            if run_successful:
                self.write_run_time()

        return run_successful

    def write_run_time(self):
        with open(f'{self.out_directory}/run_time.txt', 'w') as file:
            out_str = ''
            if self.measured_run_time is not None:
                out_str += f'Run Time: {self.measured_run_time:.2f} seconds'
            if self.run_start_time is not None:
                out_str += f'\nRun Start Time: {self.run_start_time}'
            if out_str != '':
                file.write(out_str)
            else:
                file.write('None')
