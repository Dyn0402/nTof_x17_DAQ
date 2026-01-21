#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on September 08 13:57 2025
Created in PyCharm
Created as Cosmic_Bench_DAQ_Control/dream_daq_control

@author: Dylan Neff, dn277127
"""

import os
import sys
import re
from subprocess import Popen, PIPE, STDOUT
import pty
from time import sleep
from datetime import datetime
import traceback
import shutil
import threading
from Server import Server
import socket
from common_functions import *


def main():
    port = 1101
    while True:
        try:
            clear_terminal()  # Dream DAQ output can get messy, try to clear after
            with Server(port=port) as server:
                server.receive()
                server.send('Dream DAQ control connected')
                dream_info = server.receive_json()
                cfg_template_path = dream_info['daq_config_template_path']
                run_directory = dream_info['run_directory']
                out_directory = dream_info['data_out_dir']
                raw_daq_inner_dir = dream_info['raw_daq_inner_dir']
                go_timeout = dream_info['go_timeout']
                max_run_time_addition = dream_info['max_run_time_addition']
                copy_on_fly = dream_info['copy_on_fly']
                batch_mode = dream_info['batch_mode']
                zero_supress = dream_info.get('zero_suppress', False)
                samples_per_waveform = dream_info.get('n_samples_per_waveform', None)
                pedestals_dir = dream_info.get('pedestals_dir', None)
                pedestals = dream_info.get('pedestals', None)
                original_working_directory = os.getcwd()

                create_dir_if_not_exist(run_directory)
                # create_dir_if_not_exist(out_directory)  # Think this is causing race condition with daq_control.py

                res = server.receive()
                while 'Finished' not in res:
                    if 'Start' in res:
                        print(res)
                        res_parts = res.strip().split()
                        sub_run_name = res_parts[-3]
                        run_time = float(res_parts[-2])
                        cfg_file_run_time = float(res_parts[-1])
                        print(f'Sub-run name: {sub_run_name}, Run time: {run_time} minutes')
                        sub_run_out_raw_inner_dir = f'{out_directory}/{sub_run_name}/{raw_daq_inner_dir}/'
                        create_dir_if_not_exist(sub_run_out_raw_inner_dir)

                        if run_directory is not None:
                            sub_run_dir = f'{run_directory}{sub_run_name}/'
                            create_dir_if_not_exist(sub_run_dir)
                            os.chdir(sub_run_dir)
                        else:
                            sub_run_dir = os.getcwd()

                        # Make cfg from template
                        cfg_run_path = make_config_from_template(sub_run_dir, cfg_template_path, cfg_file_run_time,
                                                                 zero_supress, samples_per_waveform)

                        # Copy dream config file to out directory for future reference
                        shutil.copy(cfg_run_path, sub_run_out_raw_inner_dir)

                        if pedestals_dir is not None:  # If pedestals_dir is not None, copy pedestal files to run dir.
                            get_pedestals(pedestals_dir, pedestals, sub_run_dir, sub_run_out_raw_inner_dir)

                        run_command = f'RunCtrl -c {cfg_run_path} -f {sub_run_name}'
                        if batch_mode:
                            run_command += ' -b'

                        process = Popen(run_command, shell=True, stdin=PIPE, stdout=PIPE, stderr=STDOUT, text=True)
                        start, taking_pedestals, run_successful = time.time(), False, True
                        server.send('Dream DAQ starting')
                        max_run_time = run_time + max_run_time_addition

                        # if copy_on_fly:
                        #     daq_finished = threading.Event()
                        #     copy_files_args = (sub_run_dir, sub_run_out_raw_inner_dir, daq_finished)
                        #     copy_files_on_the_fly_thread = threading.Thread(target=copy_files_on_the_fly,
                        #                                                        args=copy_files_args)
                        #     copy_files_on_the_fly_thread.start()
                        if copy_on_fly:
                            daq_finished = threading.Event()
                            copy_files_args = (sub_run_dir, sub_run_out_raw_inner_dir, daq_finished)
                            copy_files_on_the_fly_thread = threading.Thread(
                                target=copy_files_on_the_fly,
                                args=copy_files_args,
                                daemon=True,  # <- important: thread lives independently
                            )
                            copy_files_on_the_fly_thread.start()

                        while True:
                            output = process.stdout.readline()

                            if output == '' and process.poll() is not None:
                                server.send("Dream DAQ has finished")  # If only taking pedestals or a failure
                                break

                            if batch_mode:
                                if not taking_pedestals and '_TakePedThr' in output.strip():
                                    taking_pedestals = True
                                    server.send('Dream DAQ taking pedestals')
                                elif '_TakeData' in output.strip():
                                    server.send('Dream DAQ started')
                                    break
                            else:
                                if not taking_pedestals and output.strip() == '***':  # Start of run, begin taking pedestals
                                    process.stdin.write('G')
                                    process.stdin.flush()  # Ensure the command is sent immediately
                                    taking_pedestals = True
                                    print('Taking pedestals.')
                                    server.send('Dream DAQ taking pedestals')
                                elif 'Press C to Continue' in output.strip():  # End of pedestals, begin taking data
                                    process.stdin.write('C')  # Signal to start data taking
                                    process.stdin.flush()
                                    print('DAQ started.')
                                    server.send('Dream DAQ started')
                                    break

                            if output.strip() != '':
                                print(output.strip())

                            pedestals_time_out = time.time() - start > go_timeout and taking_pedestals
                            run_time_out = time.time() - start > max_run_time * 60
                            if pedestals_time_out or run_time_out:
                                print('DAQ process timed out.')
                                process.kill()
                                sleep(5)
                                run_successful = False
                                print('DAQ process timed out.')
                                server.send('Dream DAQ timed out')
                                break

                        if process.poll() is None:  # Process still running, start main run
                            stop_event = threading.Event()
                            server.set_timeout(1.0)  # Set timeout for socket operations

                            stop_thread = threading.Thread(target=listen_for_stop, args=(server, stop_event))
                            stop_thread.start()
                            # screen_clear_period = 30
                            # screen_clear_timer = time.time()
                            while True:  # DAQ running
                                if stop_event.is_set():
                                    process.stdin.write('g')
                                    process.stdin.flush()
                                    print('Stop command received. Stopping DAQ.')
                                    break

                                # if time.time() - screen_clear_timer > screen_clear_period:  # Clear terminal every 5 minutes
                                #     clear_terminal()
                                #     screen_clear_timer = time.time()

                                output = process.stdout.readline()
                                if output == '' and process.poll() is not None:
                                    print('DAQ process finished.')
                                    break
                                if output.strip() != '':
                                    print(output.strip())
                            print('Waiting for DAQ process to terminate.')
                            stop_event.set()  # Tell the listener thread to stop
                            stop_thread.join()
                            print('Listener thread joined.')
                            server.set_timeout(None)

                        # DAQ finished
                        # if copy_on_fly:
                        #     print('Waiting for on-the-fly copy thread to finish.')
                        #     daq_finished.set()
                        #     copy_files_on_the_fly_thread.join()
                        if copy_on_fly:
                            print('Signaling on-the-fly copier to finish soon (but not waiting).')
                            daq_finished.set()  # thread continues running in background

                        os.chdir(original_working_directory)

                        # if run_successful:
                        #     print('Moving data files.')
                        #     move_data_files(sub_run_dir, sub_run_out_raw_inner_dir)

                        server.send('Dream DAQ stopped')
                    else:
                        server.send('Unknown Command')
                    res = server.receive()
        except Exception as e:
            traceback.print_exc()
            print(f'Error: {e}')
            sleep(30)
    print('donzo')


def move_data_files(src_dir, dest_dir):
    for file in os.listdir(src_dir):
        if file.endswith('.fdf'):
            # shutil.move(f'{src_dir}/{file}', f'{dest_dir}/{file}')
            # Copy for now, maybe move and clean up later when more confident
            shutil.copy(f'{src_dir}/{file}', f'{dest_dir}/{file}')


def listen_for_stop(server, stop_event):
    while not stop_event.is_set():
        try:
            res = server.receive()
            if 'Stop' in res:
                stop_event.set()
                break
        except socket.timeout:
            continue  # just loop again and check stop_event



# def copy_files_on_the_fly(sub_run_dir, sub_out_dir, daq_finished_event, check_interval=5):
#     """
#     Continuously copy .fdf files from sub_run_dir to sud_out_dir while DAQ is running.
#     :param sub_run_dir: Sub-run directory to monitor for new files.
#     :param sub_out_dir: Sub-run output directory to copy files to.
#     :param daq_finished_event: threading.Event() that is set when DAQ is finished.
#     :param check_interval: Time in seconds between checks for new files.
#     :return:
#     """
#
#     create_dir_if_not_exist(sub_out_dir)
#     sleep(60 * 1)  # Wait on start for daq to start running
#     file_num = 0
#     while not daq_finished_event.is_set():  # Running
#         if not file_num_still_running(sub_run_dir, file_num, silent=True):
#             for file_name in os.listdir(sub_run_dir):
#                 if file_name.endswith('.fdf') and get_file_num_from_fdf_file_name(file_name, -2) == file_num:
#                     # shutil.move(f'{sub_run_dir}{file_name}', f'{sub_out_dir}{file_name}')
#                     # Copy instead of move to keep a redundant copy of the fdfs.
#                     shutil.copy(f'{sub_run_dir}{file_name}', f'{sub_out_dir}{file_name}')
#             file_num += 1
#         sleep(check_interval)  # Check every 5 seconds

def copy_files_on_the_fly(sub_run_dir, sub_out_dir, daq_finished_event, check_interval=5, extra_minutes_after_finish=3):
    """
    Copies new .fdf files during the run, and continues for extra_minutes_after_finish
    after DAQ finishes, then exits cleanly without blocking the main thread.
    """

    create_dir_if_not_exist(sub_out_dir)
    sleep(60)  # Give DAQ time to start writing before first scan

    file_num = 0

    # Phase 1: DAQ running
    while not daq_finished_event.is_set():
        if not file_num_still_running(sub_run_dir, file_num, silent=True):
            for file_name in os.listdir(sub_run_dir):
                if (
                    file_name.endswith('.fdf') and
                    get_file_num_from_fdf_file_name(file_name, -2) == file_num
                ):
                    shutil.copy(
                        os.path.join(sub_run_dir, file_name),
                        os.path.join(sub_out_dir, file_name),
                    )
            file_num += 1
        sleep(check_interval)

    # Phase 2: DAQ has ended — keep copying “stragglers”
    print("DAQ finished — continuing file copy for cleanup window.")

    end_time = time.time() + extra_minutes_after_finish * 60
    already_seen = set()

    while time.time() < end_time:
        for file_name in os.listdir(sub_run_dir):
            if file_name.endswith('.fdf') and file_name not in already_seen:
                src = os.path.join(sub_run_dir, file_name)
                dst = os.path.join(sub_out_dir, file_name)
                try:
                    shutil.copy(src, dst)
                    already_seen.add(file_name)
                except Exception:
                    pass  # ignore transient errors (file still being written)
        sleep(check_interval)

    print("On-the-fly copy thread exiting.")



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


def clear_terminal():
    try:
        os.system('cls' if os.name == 'nt' else 'clear')
    except:
        print('Failed to clear terminal')  # Ignore any errors


def make_config_from_template(run_dir, cfg_template_file_path, cfg_file_run_time, zero_suppress_mode=False,
                              samples_per_waveform=None):
    print('Making config file from template...')
    dest = run_dir
    cfg_file_name = os.path.basename(cfg_template_file_path)
    cfg_file_path = f'{dest}{cfg_file_name}'
    shutil.copy(cfg_template_file_path, cfg_file_path)

    # Copy all Grace* files from template directory to run directory
    template_dir = os.path.dirname(cfg_template_file_path)
    for file in os.listdir(template_dir):
        if file.startswith('Grace_'):
            shutil.copy(f'{template_dir}/{file}', f'{dest}{file}')

    updates = {  # Update config file with desired parameters
        "Sys DaqRun Time": cfg_file_run_time * 60,  # Seconds
        "Sys DaqRun Mode": 'ZS' if zero_suppress_mode else 'Raw',
    }
    if samples_per_waveform is not None:
        updates["Sys NbOfSamples"] = samples_per_waveform  # Use specified number of samples
    update_config_value(cfg_file_path, updates)

    return cfg_file_path


def update_config_value(filepath, updates, output_path=None):
    """
    Updates parameters in a free-form config file without changing spacing/comments.

    Parameters
    ----------
    filepath : str or Path
        Path to the input config file.
    updates : dict
        Keys are full parameter flags (e.g., "Sys DaqRun Trig"),
        values are the new values to insert.
    output_path : str or Path, optional
        Where to save the updated file. Defaults to overwriting the original.
    """
    output_path = output_path or filepath
    with open(filepath, 'r') as f:
        lines = f.readlines()

    updates = {re.escape(k.strip()): str(v) for k, v in updates.items()}
    new_lines = []

    for line in lines:
        if re.match(r'^\s*#', line) or not line.strip():
            new_lines.append(line)
            continue

        for flag_pattern, new_value in updates.items():
            pattern = rf"^(\s*{flag_pattern}\s+)([^\s#]+)(?=(\s*#|$))"
            if re.search(pattern, line):
                # Use a lambda to avoid backreference confusion
                line = re.sub(pattern, lambda m: f"{m.group(1)}{new_value}", line)
                break

        new_lines.append(line)

    with open(output_path, 'w') as f:
        f.writelines(new_lines)


def get_pedestals(pedestals_dir, pedestals, run_dir, out_dir=None):
    """
    Get pedestal files from specified directory and copy to run directory with proper naming.
    :param pedestals_dir: Directory containing pedestal runs
    :param pedestals: 'latest' or specific pedestal run directory name
    :param run_dir: Directory of current run to copy pedestal files to
    :param out_dir: If a copy of pedestal files should also be placed in out_dir
    :return:
    """
    sub_run_name = 'pedestals_noise'  # Standard name for pedestal runs
    if not os.path.isdir(pedestals_dir):
        print(f'Pedestals directory `{pedestals_dir}` does not exist.')
        return None

    if pedestals == 'latest':
        # Find latest pedestal files in pedestals_dir. Directories named pedestals_MM-DD-YY_HH-MM-SS or pedestals_MM-DD-YYYY_HH-MM-SS
        valid_dirs = []
        for item in os.listdir(pedestals_dir):
            full_path = os.path.join(pedestals_dir, item)
            if not os.path.isdir(full_path) or not item.startswith('pedestals_'):
                continue

            # Validate the trailing datetime portion; accept either two- or four-digit year
            date_part = item[len('pedestals_'):]
            parsed_dt = None
            for fmt in ('%m-%d-%y_%H-%M-%S', '%m-%d-%Y_%H-%M-%S'):
                try:
                    parsed_dt = datetime.strptime(date_part, fmt)
                    break
                except ValueError:
                    continue

            if parsed_dt is None:
                # ignore directories that don't match the expected datetime format
                continue

            valid_dirs.append((parsed_dt, item))

        if not valid_dirs:
            print('No pedestal directories found matching the expected datetime format.')
            return None

        # sort by parsed datetime and pick latest
        valid_dirs.sort(key=lambda x: x[0])
        latest_pedestal_dir = valid_dirs[-1][1]
        pedestals_prg_dir = os.path.join(pedestals_dir, latest_pedestal_dir, sub_run_name) + os.sep
    else:
        pedestals_prg_dir = os.path.join(pedestals_dir, pedestals, sub_run_name) + os.sep

    # For each .prg file found in pedestals_prg_dir (eg TbSPS25_pedestals_noise_pedthr_251022_14H39_000_04_ped.prg)
    # get the type (_thr.prg or _ped.prg) and feu number (_03_) and copy to run_dir with name reconstructed with these
    # two parameters (eg dream_pedestals_thresholds_03_thr.prg)
    for file in os.listdir(pedestals_prg_dir):
        if file.endswith('.prg') and sub_run_name in file:
            feu_num_search = re.search(r'_(\d{2})_', file)
            if feu_num_search:
                feu_num = feu_num_search.group(1)
                if '_ped.prg' in file:
                    dest_file_name = f'dream_pedestals_{feu_num}_ped.prg'
                elif '_thr.prg' in file:
                    dest_file_name = f'dream_thresholds_{feu_num}_thr.prg'
                else:
                    print(f'Unknown pedestal file type for {file}, skipping.')
                    continue
                shutil.copy(f'{pedestals_prg_dir}{file}', f'{run_dir}{dest_file_name}')
                if out_dir:
                    shutil.copy(f'{pedestals_prg_dir}{file}', f'{out_dir}{file}')
                print(f'Copied pedestal file {file} to {dest_file_name}')
            else:
                print(f'Could not find FEU number in pedestal file name {file}, skipping.')


if __name__ == '__main__':
    main()
