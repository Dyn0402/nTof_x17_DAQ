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
from caen_hv_py.exceptions import CAENHVError

# from run_config import Config

# Bound the ramp wait so a dead crate costs one sub-run rather than hanging the
# whole (possibly overnight) run. Overridable via hv_info.
DEFAULT_RAMP_TIMEOUT_S = 180


class HVRampError(RuntimeError):
    """HV could not be set/ramped within the timeout (hard, non-transient)."""


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

                ip_address = hv_info['ip']
                username = hv_info['username']
                password = hv_info['password']
                caen_lock = threading.Lock()

                # keepalive_s pings the CAEN every 10 s so the session never hits
                # the ~15 s idle drop that used to wedge HV at scan-boundary pauses
                # (monitor stopped + boundary pause > 15 s). auto_reconnect is a
                # backstop: if the handle is lost anyway, calls transparently
                # re-login and retry instead of hanging on a -1/"CFE server down".
                with CAENHVController(ip_address, username, password,
                                      keepalive_s=10, auto_reconnect=True) as caen_hv:
                    print('HV Connected')
                    res = server.receive()
                    while 'Finished' not in res:
                        if 'Start' in res:
                            monitor_print_event.clear()
                            server.send('HV ready to start')
                            sub_run = server.receive_json()
                            try:
                                set_hvs(hv_info, sub_run['hvs'], caen_hv, caen_lock)
                            except HVRampError as e:
                                # Hard HV failure (e.g. CFE server crash, or a channel that
                                # plateaus outside tolerance under load) — bail out of just
                                # this sub-run instead of hanging or killing the whole run.
                                # Reply WITHOUT 'HV Set' so daq_control skips this sub-run
                                # (leaving it un-marked, so a resume run re-tries it) and
                                # moves on to the next one.
                                print(f'HV RAMP FAILED for {sub_run["sub_run_name"]}: {e}')
                                if monitor_thread is not None:
                                    monitor_stop_event.set()
                                    monitor_thread.join()
                                    monitor_thread = None
                                server.send(f'HV Ramp Error {sub_run["sub_run_name"]}: {e}')
                            else:
                                server.send(f'HV Set {sub_run["sub_run_name"]}')
                            monitor_print_event.set()
                        elif 'Power Off' in res:
                            server.send('HV ready to power off')
                            power_off_hvs(hv_info, caen_hv, caen_lock)
                            server.send('HV Powered Off')
                        elif 'Begin Monitoring' in res:
                            server.send('Starting HV monitor')
                            sub_run = server.receive_json()
                            monitor_stop_event.clear()
                            monitor_print_event.set()
                            monitor_args = (hv_info, sub_run['hvs'], sub_run['sub_run_name'],
                                            monitor_stop_event, monitor_print_event, caen_hv, caen_lock)
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
        except Exception as e:
            print(f'Error: {e}\nRestarting hv control server...')
    print('donzo')


def set_hvs(hv_info, hvs, caen_hv, caen_lock):
    print('Setting HV...')
    ramp_timeout_s = hv_info.get('ramp_timeout_s', DEFAULT_RAMP_TIMEOUT_S)
    try:
        with caen_lock:
            for slot, channel_v0s in hvs.items():
                for channel, v0 in channel_v0s.items():
                    if v0 is None:  # Monitor only, skip setting
                        continue
                    power = caen_hv.get_ch_power(int(slot), int(channel))
                    if v0 == 0:  # If 0 V, turn off channel without setting voltage
                        if power == 1:
                            caen_hv.set_ch_pw(int(slot), int(channel), 0)
                    else:
                        caen_hv.set_ch_v0(int(slot), int(channel), v0)
                        if power != 1:
                            caen_hv.set_ch_pw(int(slot), int(channel), 1)
    except CAENHVError as e:
        # Session is dead even after the controller's auto-reconnect tried —
        # setting failed, so don't fall into the ramp-wait loop; fail fast.
        raise HVRampError(f'setting HV failed: {e}')

    deadline = time.time() + ramp_timeout_s
    all_ramped = False
    while not all_ramped:
        all_ramped = True
        print('\nChecking HV ramp...')
        try:
            with caen_lock:
                for slot, channel_v0s in hvs.items():
                    for channel, v0 in channel_v0s.items():
                        if v0 is None:
                            continue
                        vmon = caen_hv.get_ch_vmon(int(slot), int(channel))
                        if abs(vmon - v0) > 1.5:  # Make sure within 1.5 V of set value
                            all_ramped = False
                            print(f' Slot {slot}, Channel {channel} not ramped: {vmon:.2f} V --> {v0} V')
        except CAENHVError as e:
            # Transient drops auto-heal inside the controller; a CAENHVError here
            # means reconnect was exhausted (crate/CFE really down). Treat as
            # not-yet-ramped and let the wall-clock timeout below decide to abort.
            all_ramped = False
            print(f'HV read failed during ramp check: {e}')

        if not all_ramped:
            if time.time() > deadline:
                # Bound the damage: a dead crate costs this sub-run + a clean
                # stop, not an indefinite (overnight) hang.
                raise HVRampError(
                    f'HV did not ramp within {ramp_timeout_s}s '
                    f'(crate/CFE server likely down) — aborting sub-run')
            print('Waiting for HV to ramp...')
            time.sleep(10)  # lock released during sleep — monitor runs freely
    print('HV Ramped')


def power_off_hvs(hv_info, caen_hv, caen_lock):
    print('Powering off HV...')
    with caen_lock:
        for slot in range(hv_info['n_cards']):
            for channel in range(hv_info['n_channels_per_card']):
                try:
                    power = caen_hv.get_ch_power(int(slot), int(channel))
                    if power == 1:
                        caen_hv.set_ch_pw(int(slot), int(channel), 0)
                except CAENHVError as e:
                    # Power-off runs at end of run, possibly after a crate failure;
                    # don't let one dead channel abort the whole shutdown.
                    print(f'Power off failed for {slot}:{channel}: {e}')
    print('HV Powered Off')


def monitor_hvs(hv_info, hvs, sub_run_name, stop_event, print_event, caen_hv, caen_lock):
    """
    Monitor the voltage and current of each HV channel and log the readings.
    Logs to CSV and prints human-readable output to the screen.
    """
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

        while not stop_event.is_set():
            row = [time.strftime("%Y-%m-%d %H:%M:%S")]

            with caen_lock:
                if print_event.is_set():
                    print(f"Monitoring HV \n{time.strftime('%H:%M:%S', time.strptime(row[0], '%Y-%m-%d %H:%M:%S'))}")
                for slot, channel_v0s in hvs.items():
                    for channel, v0 in channel_v0s.items():
                        try:
                            power = caen_hv.get_ch_power(int(slot), int(channel))
                            vmon = caen_hv.get_ch_vmon(int(slot), int(channel))
                            imon = caen_hv.get_ch_imon(int(slot), int(channel))
                        except CAENHVError as e:
                            # Don't let a hard HV read failure kill the monitor
                            # thread — log a blank row and keep going. Transient
                            # session drops are auto-healed inside the controller.
                            print(f"HV monitor read failed for {slot}:{channel}: {e}")
                            power, vmon, imon = '', float('nan'), float('nan')

                        row.extend([power, v0, vmon, imon])  # Append to row

                        if print_event.is_set():
                            v0_str = 'manual' if v0 is None else f'{v0:.2f}'
                            print(  # Human-readable output
                                f"Slot {slot} Channel {channel}: "
                                f"power={'on' if power else 'off'}, "
                                f"v set={v0_str}, v mon={vmon:.2f}, i mon={imon:.3f}"
                            )

            writer.writerow(row)
            csvfile.flush()  # ensure data is written to disk
            time.sleep(monitor_interval)  # lock released during sleep — set_hvs can run


if __name__ == '__main__':
    main()
