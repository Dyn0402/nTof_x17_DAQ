#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on April 29 11:13 AM 2024
Created in PyCharm
Created as Cosmic_Bench_DAQ_Control/DAQController.py

@author: Dylan Neff, Dylan
"""

import os
import re
import shutil
from time import time, sleep


class DAQController:
    def __init__(self, cfg_template_file_path=None, run_time=10, out_name=None, out_dir=None,
                 trigger_switch_client=None, dream_daq_client=None):
        self.cfg_template_file_path = cfg_template_file_path
        self.out_directory = out_dir
        self.out_name = out_name
        self.trigger_switch_client = trigger_switch_client
        self.dream_daq_client = dream_daq_client
        self.original_working_directory = os.getcwd()

        self.run_time = run_time  # minutes
        self.max_run_time = self.run_time + 5  # minutes After this time assume stuck and kill
        self.go_timeout = 8  # minutes
        self.run_start_time = None
        self.measured_run_time = None

        self.wait_for_banco_time = 10
        self.dream_overrun_time = 12 / 60  # minutes, needs to be at least wait_for_banco_time in case no triggers

        # If trigger switch is used, need to run past run time to bracket the trigger switch on/off. Else just run time.
        # DAQ resets timer when first trigger received, so only need short pause to be sure.
        self.cfg_file_run_time = self.run_time if self.trigger_switch_client is None \
            else self.run_time + self.dream_overrun_time  # minutes

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        os.chdir(self.original_working_directory)

    def run(self):
        run_successful = True
        if self.trigger_switch_client is not None:
            self.trigger_switch_client.silent = True

        try:
            self.dream_daq_client.send(f'Start {self.out_name} {self.run_time} {self.cfg_file_run_time}')
            res = self.dream_daq_client.receive()
            if res != 'Dream DAQ starting':
                print('Error starting Dream DAQ')
                return False

            res = self.dream_daq_client.receive()
            if res != 'Dream DAQ taking pedestals' and res != 'Dream DAQ started':
                print('Error taking pedestals')
                return False

            if res == 'Dream DAQ taking pedestals':  # Wait for pedestals to finish if taking
                res = self.dream_daq_client.receive()
            # if res != 'Dream DAQ started':
            #     print('Error starting DAQ')
            #     return False
            if res == 'Dream DAQ started':
                self.run_start_time = time()

                if self.trigger_switch_client is not None:
                    sleep(self.wait_for_banco_time)  # Wait a bit to ensure DAQs are running before starting trigger
                    self.trigger_switch_client.send('on')
                    self.run_start_time = time()
                    self.trigger_switch_client.receive()

                if self.trigger_switch_client:  # Wait for run to finish
                    sleep_time = self.run_time * 60 - (time() - self.run_start_time)
                    print(f'Waiting for {sleep_time:.1f} seconds of run time...')
                    sleep(sleep_time)
                    # if time() - self.run_start_time >= self.run_time * 60:
                    self.trigger_switch_client.send('off')
                    print('Holding triggers')
                    self.measured_run_time = time() - self.run_start_time
                    self.trigger_switch_client.receive()
            elif res == 'Dream DAQ has finished':   # Only taking pedestals
                print('Dream only took pedestals')
                self.measured_run_time = 0
            else:
                print('Error during DAQ run')
                return False

            res = self.dream_daq_client.receive()  # Wait for dream daq to finish
            if res != 'Dream DAQ stopped':
                print('Error stopping DAQ')
                return False

            if self.trigger_switch_client is None:  # Run time dictated by DREAM DAQ if no trigger switch
                self.measured_run_time = time() - self.run_start_time


        except KeyboardInterrupt:
            print('Keyboard interrupt. Stopping DAQ process.')
            if self.trigger_switch_client is not None:
                self.trigger_switch_client.send('off')
            if self.run_start_time is not None:
                self.measured_run_time = time() - self.run_start_time
            else:
                self.measured_run_time = 0
            if self.trigger_switch_client is not None:
                self.trigger_switch_client.receive()
            sleep(1)
            self.dream_daq_client.send('Stop')
            res = self.dream_daq_client.receive()
            if res != 'Dream DAQ stopped':
                print('Error stopping Dream DAQ')
        finally:
            print('Dream Subrun complete.')
            if self.trigger_switch_client is not None:
                self.trigger_switch_client.silent = False

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
