#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on April 29 8:58 PM 2024
Created in PyCharm
Created as Cosmic_Bench_DAQ_Control/daq_control.py

@author: Dylan Neff, Dylan
"""

import sys
import shutil
from time import sleep
from contextlib import nullcontext

from Client import Client
from DAQController import DAQController

from run_config_base import RunConfigBase
from common_functions import *
from weiner_ps_monitor import get_pl512_status

RUNCONFIG_REL_PATH = "config/json_run_configs/"


def main():
    print("Starting DAQ Control")

    config = RunConfigBase()  # Initially just load run_config_beam.py
    if len(sys.argv) == 2:
        config_path = os.path.join(RUNCONFIG_REL_PATH, sys.argv[1]) if not os.path.isabs(sys.argv[1]) else sys.argv[1]
        print(f'Using run config file: {config_path}')
        if not os.path.isfile(config_path):
            print(f'File {config_path} does not exist, exiting')
            return
        if config_path.endswith('.json'):
            config.load_from_file(config_path)  # If a config file is given, load it
        elif config_path.endswith('.py'):
            pass
    config.start_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    hv_ip, hv_port = config.hv_control_info['ip'], config.hv_control_info['port']
    if config.process_on_fly:
        processor_ip, processor_port = config.processor_info['ip'], config.processor_info['port']
    else:
        processor_ip, processor_port = None, None

    dream_daq_ip, dream_daq_port = config.dream_daq_info['ip'], config.dream_daq_info['port']

    hv_client = Client(hv_ip, hv_port)
    processor_client = Client(processor_ip, processor_port) if config.process_on_fly else nullcontext()
    dream_daq_client = Client(dream_daq_ip, dream_daq_port)

    with hv_client as hv, \
            processor_client as processor, \
            dream_daq_client as dream_daq:

        hv.send('Connected to daq_control')
        hv.receive()
        hv.send_json(config.hv_info)

        create_dir_if_not_exist(config.run_out_dir)
        config.write_to_file(f'{config.run_out_dir}run_config.json')

        dream_daq.send('Connected to daq_control')
        dream_daq.receive()
        dream_daq.send_json(config.dream_daq_info)

        if config.process_on_fly:
            processor.send('Connected to daq_control')
            processor.receive()
            processor.send_json(config.processor_info)
            processor.receive()
            processor.send_json({'included_detectors': config.included_detectors})
            processor.receive()
            processor.send_json({'detectors': config.detectors})
            processor.receive()

        sleep(2)  # Wait for all clients to do what they need to do (specifically, create directories)
        try:
            for sub_run in config.sub_runs:
                sub_run_name = sub_run['sub_run_name']
                # sub_run_dir = f'{config.dream_daq_info["run_directory"]}{sub_run_name}/'
                # create_dir_if_not_exist(sub_run_dir)  # Means DAQ runs on Dream CPU! Can fix, need config template in dream_daq control!
                sub_top_out_dir = f'{config.run_out_dir}{sub_run_name}/'
                create_dir_if_not_exist(sub_top_out_dir)
                sub_out_dir = f'{sub_top_out_dir}{config.raw_daq_inner_dir}/'
                create_dir_if_not_exist(sub_out_dir)

                if getattr(config, 'weiner_ps_info', None):  # Ensure ps is on before starting run
                    weiner_ok = check_weiner_lv_status(config.weiner_ps_info)
                    if not weiner_ok:
                        print(f'Weiner Power Supply check failed, skipping sub run {sub_run_name}')
                        continue

                print(f'Ramping HVs for {sub_run_name}')
                if config.hv_info['hv_monitoring']:  # Monitor hv and write to file
                    hv.send('Begin Monitoring')
                    hv.receive()  # Starting monitoring
                    hv.send_json(sub_run)
                    hv.receive()  # Monitoring started

                hv.send('Start')
                hv.receive()
                hv.send_json(sub_run)
                res = hv.receive()
                if 'HV Set' in res:
                    print(f'Prepping DAQs for {sub_run_name}')

                    daq_control_args = (config.dream_daq_info['daq_config_template_path'], sub_run_name,
                                        sub_run['run_time'], sub_out_dir, dream_daq)

                    print(f'Starting run for sub run {sub_run_name}')
                    run_daq_controller(*daq_control_args)

                    if config.hv_info['hv_monitoring']:
                        hv.send('End Monitoring')
                        hv.receive()  # Stopping monitoring
                        hv.receive()  # Finished monitoring

                    print(f'Finished with sub run {sub_run_name}, waiting 10 seconds before next run')
                    sleep(10)
        except KeyboardInterrupt as e:
            print(f'Run stoppping.')

            if config.hv_info['hv_monitoring']:
                hv.send('End Monitoring')
                hv.receive()  # Stopping monitoring
                hv.receive()  # Finished monitoring

        finally:
            pass
        print('Run complete, closing down subsystems')
        if config.power_off_hv_at_end:
            hv.send('Power Off')
            hv.receive()  # Starting power off
            hv.receive()  # Finished power off
        hv.send('Finished')
        dream_daq.send('Finished')
        if config.process_on_fly:
            processor.send('Finished')
    print('donzo')


def run_daq_controller(config_template_path, sub_run_name, run_time, sub_out_dir,
                       dream_daq_client):
    daq_controller = DAQController(cfg_template_file_path=config_template_path, run_time=run_time, out_dir=sub_out_dir,
                                   out_name=sub_run_name, dream_daq_client=dream_daq_client)

    daq_success = False
    while not daq_success:  # Rerun if failure
        print('Starting DAQ Controller')
        daq_success = daq_controller.run()


def found_file_num(fdf_dir, file_num):
    """
    Look for file number in fdf dir. Return True if found, False if not
    :param fdf_dir: Directory containing fdf files
    :param file_num:
    :return:
    """
    for file_name in os.listdir(fdf_dir):
        if not file_name.endswith('.fdf') or '_datrun_' not in file_name:
            continue
        if file_num == get_file_num_from_fdf_file_name(file_name, -2):
            return True
    return False


def file_num_still_running(fdf_dir, file_num, wait_time=30, silent=False):
    """
    Check if dream DAQ is still running by finding all fdfs with file_num and checking to see if any file size
    increases within wait_time
    :param fdf_dir: Directory containing fdf files
    :param file_num: File number to check for
    :param wait_time: Time to wait for file size increase
    :param silent: Print debug info
    :return: True if size increased over wait time (still running), False if not.
    """
    file_paths = []
    for file in os.listdir(fdf_dir):
        if not file.endswith('.fdf') or '_datrun_' not in file:
            continue  # Skip non fdf data files
        if get_file_num_from_fdf_file_name(file) == file_num:
            file_paths.append(f'{fdf_dir}{file}')

    if len(file_paths) == 0:
        if not silent:
            print(f'No fdfs with file num {file_num} found in {fdf_dir}')
        return False

    old_sizes = []
    for fdf_path in file_paths:
        old_sizes.append(os.path.getsize(fdf_path))
        if not silent:
            print(f'File: {fdf_path} Original Size: {old_sizes[-1]}')

    sleep(wait_time)

    new_sizes = []
    for fdf_path in file_paths:
        new_sizes.append(os.path.getsize(fdf_path))
        if not silent:
            print(f'File: {fdf_path} New Size: {new_sizes[-1]}')

    for i in range(len(old_sizes)):
        if not silent:
            print(f'File: {file_paths[i]} Original Size: {old_sizes[i]} New Size: {new_sizes[i]}')
            print(f'Increased? {new_sizes[i] > old_sizes[i]}')
        if new_sizes[i] > old_sizes[i]:
            return True
    return False


def check_weiner_lv_status(weiner_ps_info):
    """
    Check the weiner power supply status and ensure it is on and at expected voltages/currents.
    :param weiner_ps_info: Weiner power supply info from run config.
    :return:
    """
    ps_status = get_pl512_status(f'http://{weiner_ps_info["ip"]}')
    if ps_status['power_supply_status'] != 'ON':
        print('Weiner Power Supply is not ON, exiting sub-run')
        return False
    for channel in weiner_ps_info['channels']:
        channel_status = ps_status['channels'].get(channel, None)
        if channel_status is None:
            print(f'Weiner Power Supply Channel {channel} not found, exiting sub-run')
            return False
        if channel_status['status'] != 'ON':
            print(f'Weiner Power Supply Channel {channel} is not ON, exiting sub-run')
            return False
        channel_info = weiner_ps_info['channels'][channel]

        v_meas = channel_status['measured_sense_voltage']
        v_expected = channel_info['expected_voltage']
        v_tol = channel_info['voltage_tolerance']
        if not (v_expected - v_tol <= float(v_meas) <= v_expected + v_tol):
            print(f'Weiner Power Supply Channel {channel} voltage out of tolerance '
                  f'({v_meas} V measured, {v_expected} +/- {v_tol} V expected), exiting sub-run')
            return False

        i_meas = channel_status['measured_current']
        i_expected = channel_info['expected_current']
        i_tol = channel_info['current_tolerance']
        if not (i_expected - i_tol <= float(i_meas) <= i_expected + i_tol):
            print(f'Weiner Power Supply Channel {channel} current out of tolerance '
                  f'({i_meas} A measured, {i_expected} +/- {i_tol} A expected), exiting sub-run')
            return False
    print('Weiner Power Supply status OK, continuing with sub-run')
    return True


# def double_interrupt_handler(sig, frame):
#     global stop_all
#     if stop_all:
#         print("\nSecond Ctrl-C detected, exiting immediately.")
#         sys.exit(1)
#     else:
#         print("\nCtrl-C detected. Finishing current sub-run gracefully. Press again to exit entirely.")
#         stop_all = True


if __name__ == '__main__':
    main()
