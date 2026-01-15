#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on April 29 8:39 PM 2024
Created in PyCharm
Created as Cosmic_Bench_DAQ_Control/hv_control.py

@author: Dylan Neff, Dylan
"""

import os
import threading
import time
import csv

from Server import Server
from caen_hv_py.CAENHVController import CAENHVController

# from run_config import Config


def main():
    # config = Config()
    port = 1100
    monitor_stop_event, monitor_print_event, monitor_thread = threading.Event(), threading.Event(), None
    while True:
        try:
            with Server(port=port) as server:
                server.receive()
                server.send('HV control connected')
                hv_info = server.receive_json()

                res = server.receive()
                while 'Finished' not in res:
                    if 'Start' in res:
                        monitor_print_event.clear()
                        server.send('HV ready to start')
                        sub_run = server.receive_json()
                        set_hvs(hv_info, sub_run['hvs'])
                        server.send(f'HV Set {sub_run["sub_run_name"]}')
                        monitor_print_event.set()
                    elif 'Power Off' in res:
                        server.send('HV ready to power off')
                        power_off_hvs(hv_info)
                        server.send('HV Powered Off')
                    elif 'Begin Monitoring' in res:
                        server.send('Starting HV monitor')
                        sub_run = server.receive_json()
                        monitor_stop_event.clear()
                        monitor_print_event.set()
                        monitor_args = (hv_info, sub_run['hvs'], sub_run['sub_run_name'], monitor_stop_event, monitor_print_event)
                        monitor_thread = threading.Thread(target=monitor_hvs, args=monitor_args)
                        monitor_thread.start()
                        server.send(f'HV monitoring started for {sub_run["sub_run_name"]}')
                    elif 'End Monitoring' in res:
                        server.send('Stopping HV monitor')
                        if monitor_thread is not None:
                            monitor_stop_event.set()
                            monitor_thread.join()
                            monitor_thread = None
                        server.send('HV Monitor Stopped')
                    else:
                        server.send('Unknown Command')
                    res = server.receive()
            # power_off_hvs(hv_info)
        except Exception as e:
            print(f'Error: {e}\nRestarting hv control server...')
    print('donzo')


def set_hvs(hv_info, hvs):
    ip_address, username, password = hv_info['ip'], hv_info['username'], hv_info['password']
    with CAENHVController(ip_address, username, password) as caen_hv:
        for slot, channel_v0s in hvs.items():
            for channel, v0 in channel_v0s.items():
                power = caen_hv.get_ch_power(int(slot), int(channel))
                if v0 == 0:  # If 0 V, turn off channel without setting voltage
                    if power:
                        caen_hv.set_ch_pw(int(slot), int(channel), 0)
                else:
                    caen_hv.set_ch_v0(int(slot), int(channel), v0)
                    if not power:
                        caen_hv.set_ch_pw(int(slot), int(channel), 1)

        all_ramped = False
        while not all_ramped:
            all_ramped = True
            print('\nChecking HV ramp...')
            for slot, channel_v0s in hvs.items():
                for channel, v0 in channel_v0s.items():
                    vmon = caen_hv.get_ch_vmon(int(slot), int(channel))
                    if abs(vmon - v0) > 1.5:  # Make sure within 1.5 V of set value
                        all_ramped = False
                        print(f' Slot {slot}, Channel {channel} not ramped: {vmon:.2f} V --> {v0} V')
            if not all_ramped:
                print('Waiting for HV to ramp...')
                time.sleep(10)  # Make sure not to wait too long, after 15s crate times out
        print('HV Ramped')


def power_off_hvs(hv_info):
    ip_address, username, password = hv_info['ip'], hv_info['username'], hv_info['password']
    print('Powering off HV...')
    with CAENHVController(ip_address, username, password) as caen_hv:
        for slot in range(hv_info['n_cards']):
            for channel in range(hv_info['n_channels_per_card']):
                power = caen_hv.get_ch_power(int(slot), int(channel))
                if power:
                    caen_hv.set_ch_pw(int(slot), int(channel), 0)
    print('HV Powered Off')


# def monitor_hvs(hv_info, hvs, sub_run_name, stop_event):
#     """
#     Monitor the voltage and current of each HV channel and log the readings.
#     :param hv_info:
#     :param hvs:
#     :return:
#     """
#     ip_address, username, password = hv_info['ip'], hv_info['username'], hv_info['password']
#     run_out_dir = hv_info['run_out_dir']
#     sub_run_out_dir = f'{run_out_dir}/{sub_run_name}'
#     os.makedirs(sub_run_out_dir, exist_ok=True)
#     log_file_path = f'{sub_run_out_dir}/hv_monitor.csv'
#
#     with open(log_file_path, 'w') as log_file:
#         with CAENHVController(ip_address, username, password) as caen_hv:
#             while not stop_event.is_set():
#                 for slot, channel_v0s in hvs.items():
#                     for channel, v0 in channel_v0s.items():
#                         power = caen_hv.get_ch_power(int(slot), int(channel))
#                         vmon = caen_hv.get_ch_vmon(int(slot), int(channel))
#                         imon = caen_hv.get_ch_imon(int(slot), int(channel))


def monitor_hvs(hv_info, hvs, sub_run_name, stop_event, print_event):
    """
    Monitor the voltage and current of each HV channel and log the readings.
    Logs to CSV and prints human-readable output to the screen.
    """
    ip_address, username, password = hv_info['ip'], hv_info['username'], hv_info['password']
    run_out_dir = hv_info['run_out_dir']
    monitor_interval = hv_info.get('monitor_interval', 10)  # seconds
    sub_run_out_dir = f'{run_out_dir}/{sub_run_name}'
    os.makedirs(sub_run_out_dir, exist_ok=True)
    log_file_path = f'{sub_run_out_dir}/hv_monitor.csv'

    # Build headers dynamically based on hvs dict
    headers = ["timestamp"]
    for slot, channel_v0s in hvs.items():
        for channel in channel_v0s.keys():
            prefix = f"{slot}:{channel}"
            headers.extend([f"{prefix} power", f"{prefix} v0", f"{prefix} vmon", f"{prefix} imon"])

    with open(log_file_path, 'w', newline='') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(headers)  # write headers once

        with CAENHVController(ip_address, username, password) as caen_hv:
            while not stop_event.is_set():
                row = [time.strftime("%Y-%m-%d %H:%M:%S")]

                if print_event.is_set():
                    print(f"Monitoring HV \n{time.strftime('%H:%M:%S', time.strptime(row[0], '%Y-%m-%d %H:%M:%S'))}")
                for slot, channel_v0s in hvs.items():
                    for channel, v0 in channel_v0s.items():
                        power = caen_hv.get_ch_power(int(slot), int(channel))
                        vmon = caen_hv.get_ch_vmon(int(slot), int(channel))
                        imon = caen_hv.get_ch_imon(int(slot), int(channel))

                        row.extend([power, v0, vmon, imon])  # Append to row

                        if print_event.is_set():
                            print(  # Human-readable output
                                f"Slot {slot} Channel {channel}: "
                                f"power={'on' if power else 'off'}, "
                                f"v set={v0:.2f}, v mon={vmon:.2f}, i mon={imon:.3f}"
                            )

                writer.writerow(row)
                csvfile.flush()  # ensure data is written to disk
                time.sleep(monitor_interval)


if __name__ == '__main__':
    main()
