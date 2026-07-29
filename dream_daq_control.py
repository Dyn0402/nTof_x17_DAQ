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
import logging
import subprocess
from subprocess import Popen, PIPE, STDOUT
import pty
import termios
from time import sleep
from datetime import datetime
import traceback
import shutil
import threading
from Server import Server
import socket
from common_functions import *

# Durable event log for the dream_daq process itself. Deliberately NOT named
# dream_daq.log: that name belongs to the per-run logging.FileHandler that
# setup_logging() attaches inside each run's own data directory, which covers
# per-run debugging but says nothing about whether this process is alive.
# `log_event` comes from the common_functions star-import above.
_LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         'logs', 'dream_daq_control.log')


def _log(event, **details):
    log_event(_LOG_FILE, event, 'dream_daq', **details)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s: %(message)s',
        handlers=[logging.StreamHandler()]
    )
    port = 1101
    _log('START', port=port)
    while True:
        run_log_handler = None
        subrun_log_handler = None
        try:
            clear_terminal()  # Dream DAQ output can get messy, try to clear after
            with Server(port=port) as server:
                server.receive()
                server.send('Dream DAQ control connected')
                dream_info = server.receive_json()
                original_working_directory = os.getcwd()

                create_dir_if_not_exist(dream_info['data_out_dir'])
                run_log_handler = setup_logging(
                    os.path.join(dream_info['data_out_dir'], 'dream_daq.log'))
                logging.info('Run started')

                create_dir_if_not_exist(dream_info['run_directory'])
                # create_dir_if_not_exist(out_directory)  # Think this is causing race condition with daq_control.py

                res = server.receive()
                while 'Finished' not in res:
                    if 'Start' in res:
                        subrun = server.receive_json()
                        effective_info = {**dream_info, **subrun}

                        sub_run_name = subrun['sub_run_name']
                        run_time = float(subrun['run_time'])
                        print(f'Sub-run name: {sub_run_name}, Run time: {run_time} minutes')

                        effective_cfg_template_path = effective_info['daq_config_template_path']
                        effective_out_directory = effective_info['data_out_dir']
                        effective_raw_daq_inner_dir = effective_info['raw_daq_inner_dir']
                        effective_run_directory = effective_info['run_directory']
                        effective_copy_on_fly = effective_info['copy_on_fly']
                        effective_zero_suppress = effective_info.get('zero_suppress', False)
                        effective_samples_per_waveform = effective_info.get('n_samples_per_waveform', None)
                        effective_pedestals_dir = effective_info.get('pedestals_dir', None)
                        effective_pedestals = effective_info.get('pedestals', None)
                        effective_pedestal_subtraction = effective_info.get('pedestal_subtraction', None)
                        effective_common_noise_subtraction = effective_info.get('common_noise_subtraction', None)
                        effective_zs_type = effective_info.get('zs_type', None)
                        effective_zs_check_sample = effective_info.get('zs_check_sample', None)
                        effective_latency = effective_info.get('latency', None)
                        effective_sample_period = effective_info.get('sample_period', None)
                        effective_daq_run_events = effective_info.get('daq_run_events', None)
                        effective_inter_packet_delay = effective_info.get('inter_packet_delay', None)
                        effective_multipack_thr = effective_info.get('multipack_thr', None)
                        effective_multipack_enb = effective_info.get('multipack_enb', None)
                        effective_sparse_rd = effective_info.get('sparse_rd', None)
                        effective_rdclk_div = effective_info.get('rdclk_div', None)
                        effective_wrclk_div = effective_info.get('wrclk_div', None)
                        effective_rd_del = effective_info.get('rd_del', None)
                        effective_adc_dat_rdy_del = effective_info.get('adc_dat_rdy_del', None)
                        effective_trig_veto_len = effective_info.get('trig_veto_len', None)
                        effective_ovr_wrn_hwm = effective_info.get('ovr_wrn_hwm', None)
                        effective_ovr_wrn_lwm = effective_info.get('ovr_wrn_lwm', None)
                        effective_loc_throt = effective_info.get('loc_throt', None)
                        effective_included_feus = effective_info.get('included_feus', None)
                        effective_feu_connectors = effective_info.get('feu_connectors', None)
                        effective_trigger_feu = effective_info.get('trigger_feu', None)
                        effective_do_pedestal_threshold_run = effective_info.get('do_pedestal_threshold_run', None)
                        effective_do_data_run = effective_info.get('do_data_run', None)

                        sub_run_out_raw_inner_dir = f'{effective_out_directory}/{sub_run_name}/{effective_raw_daq_inner_dir}/'
                        create_dir_if_not_exist(sub_run_out_raw_inner_dir)
                        subrun_log_handler = setup_logging(
                            os.path.join(sub_run_out_raw_inner_dir, 'dream_daq.log'))
                        logging.info(f'Subrun started: {sub_run_name}  run_time={run_time}min')

                        if effective_run_directory is not None:
                            sub_run_dir = f'{effective_run_directory}{sub_run_name}/'
                            create_dir_if_not_exist(sub_run_dir)
                            os.chdir(sub_run_dir)
                        else:
                            sub_run_dir = os.getcwd()

                        cfg_run_path = make_config_from_template(
                            sub_run_dir, effective_cfg_template_path, run_time,
                            effective_zero_suppress, effective_samples_per_waveform,
                            effective_pedestal_subtraction, effective_common_noise_subtraction,
                            effective_zs_type, effective_zs_check_sample, effective_latency,
                            effective_included_feus, effective_feu_connectors, effective_trigger_feu,
                            do_pedestal_threshold_run=effective_do_pedestal_threshold_run,
                            do_data_run=effective_do_data_run, sample_period=effective_sample_period,
                            daq_run_events=effective_daq_run_events,
                            inter_packet_delay=effective_inter_packet_delay,
                            multipack_thr=effective_multipack_thr,
                            multipack_enb=effective_multipack_enb,
                            sparse_rd=effective_sparse_rd,
                            rdclk_div=effective_rdclk_div,
                            wrclk_div=effective_wrclk_div,
                            rd_del=effective_rd_del,
                            adc_dat_rdy_del=effective_adc_dat_rdy_del,
                            trig_veto_len=effective_trig_veto_len,
                            ovr_wrn_hwm=effective_ovr_wrn_hwm,
                            ovr_wrn_lwm=effective_ovr_wrn_lwm,
                            loc_throt=effective_loc_throt)
                        shutil.copy(cfg_run_path, sub_run_out_raw_inner_dir)

                        if effective_pedestals_dir is not None:
                            print(f'Getting pedestal files from {effective_pedestals_dir}...')
                            get_pedestals(effective_pedestals_dir, effective_pedestals, sub_run_dir, sub_run_out_raw_inner_dir)

                        # run_command = f'RunCtrl -c {cfg_run_path} -f {sub_run_name}'
                        # if batch_mode:
                        #     run_command += ' -b'
                        #
                        # process = Popen(run_command, shell=True, stdin=PIPE, stdout=PIPE, stderr=STDOUT, text=True)
                        # start, taking_pedestals, run_successful = time.time(), False, True
                        # server.send('Dream DAQ starting')
                        # max_run_time = run_time + max_run_time_addition

                        run_command = ['RunCtrl', '-c', cfg_run_path, '-f', sub_run_name, '-b']

                        # if copy_on_fly:
                        #     daq_finished = threading.Event()
                        #     copy_files_args = (sub_run_dir, sub_run_out_raw_inner_dir, daq_finished)
                        #     copy_files_on_the_fly_thread = threading.Thread(target=copy_files_on_the_fly,
                        #                                                        args=copy_files_args)
                        #     copy_files_on_the_fly_thread.start()
                        # A pedestal run enables BOTH PedThrRun and DataRun: the data
                        # phase exists only so batch RunCtrl self-exits, and its _datrun_
                        # FDFs are empty junk to be discarded (kept out of raw_daq_data
                        # here, deleted from the run dir below). Beam runs (DataRun only)
                        # are untouched.
                        discard_data_run = bool(effective_do_pedestal_threshold_run) and bool(effective_do_data_run)

                        if effective_copy_on_fly:
                            daq_finished = threading.Event()
                            copy_files_kwargs = {
                                'skip_substrings': ['_datrun_'] if discard_data_run else None,
                            }
                            copy_files_args = (sub_run_dir, sub_run_out_raw_inner_dir, daq_finished)
                            copy_files_on_the_fly_thread = threading.Thread(
                                target=copy_files_on_the_fly,
                                args=copy_files_args,
                                kwargs=copy_files_kwargs,
                                daemon=True,
                            )
                            copy_files_on_the_fly_thread.start()
                        server.send('Dream DAQ starting')
                        print(f'Starting Dream DAQ with command: {run_command}')
                        prepare_terminal()
                        ret = subprocess.call(run_command)

                        # while True:
                        #     output = process.stdout.readline()
                        #
                        #     if output == '' and process.poll() is not None:
                        #         server.send("Dream DAQ has finished")  # If only taking pedestals or a failure
                        #         break
                        #
                        #     if batch_mode:
                        #         if not taking_pedestals and '_TakePedThr' in output.strip():
                        #             taking_pedestals = True
                        #             server.send('Dream DAQ taking pedestals')
                        #         elif '_TakeData' in output.strip():
                        #             server.send('Dream DAQ started')
                        #             break
                        #     else:
                        #         if not taking_pedestals and output.strip() == '***':  # Start of run, begin taking pedestals
                        #             process.stdin.write('G')
                        #             process.stdin.flush()  # Ensure the command is sent immediately
                        #             taking_pedestals = True
                        #             print('Taking pedestals.')
                        #             server.send('Dream DAQ taking pedestals')
                        #         elif 'Press C to Continue' in output.strip():  # End of pedestals, begin taking data
                        #             process.stdin.write('C')  # Signal to start data taking
                        #             process.stdin.flush()
                        #             print('DAQ started.')
                        #             server.send('Dream DAQ started')
                        #             break
                        #
                        #     if output.strip() != '':
                        #         print(output.strip())
                        #
                        #     pedestals_time_out = time.time() - start > go_timeout and taking_pedestals
                        #     run_time_out = time.time() - start > max_run_time * 60
                        #     if pedestals_time_out or run_time_out:
                        #         print('DAQ process timed out.')
                        #         process.kill()
                        #         sleep(5)
                        #         run_successful = False
                        #         print('DAQ process timed out.')
                        #         server.send('Dream DAQ timed out')
                        #         break
                        #
                        # if process.poll() is None:  # Process still running, start main run
                        #     stop_event = threading.Event()
                        #     server.set_timeout(1.0)  # Set timeout for socket operations
                        #
                        #     stop_thread = threading.Thread(target=listen_for_stop, args=(server, stop_event))
                        #     stop_thread.start()
                        #     # screen_clear_period = 30
                        #     # screen_clear_timer = time.time()
                        #     while True:  # DAQ running
                        #         if stop_event.is_set():
                        #             process.stdin.write('g')
                        #             process.stdin.flush()
                        #             print('Stop command received. Stopping DAQ.')
                        #             break
                        #
                        #         # if time.time() - screen_clear_timer > screen_clear_period:  # Clear terminal every 5 minutes
                        #         #     clear_terminal()
                        #         #     screen_clear_timer = time.time()
                        #
                        #         output = process.stdout.readline()
                        #         if output == '' and process.poll() is not None:
                        #             print('DAQ process finished.')
                        #             break
                        #         if output.strip() != '':
                        #             print(output.strip())
                        #     print('Waiting for DAQ process to terminate.')
                        #     stop_event.set()  # Tell the listener thread to stop
                        #     stop_thread.join()
                        #     print('Listener thread joined.')
                        #     server.set_timeout(None)

                        # DAQ finished
                        # if copy_on_fly:
                        #     print('Waiting for on-the-fly copy thread to finish.')
                        #     daq_finished.set()
                        #     copy_files_on_the_fly_thread.join()
                        if effective_copy_on_fly:
                            print('Signaling on-the-fly copier to finish soon (but not waiting).')
                            daq_finished.set()

                        for log_file in os.listdir(sub_run_dir):
                            if log_file.endswith('.log'):
                                shutil.copy(os.path.join(sub_run_dir, log_file), sub_run_out_raw_inner_dir)
                                print(f'Copied log file {log_file} to {sub_run_out_raw_inner_dir}')

                        if discard_data_run:
                            # RunCtrl has exited, so every _datrun_ file is final. Delete
                            # them: they are the empty data-taking output written only so
                            # batch RunCtrl self-exits after a pedestal run. Pedestal
                            # outputs are named _pedthr_, so this never touches them. Clean
                            # both the run dir and raw_daq_data (the copier is told to skip
                            # _datrun_, but clear it defensively in case any slipped in).
                            n_removed = 0
                            for clean_dir in (sub_run_dir, sub_run_out_raw_inner_dir):
                                if not clean_dir or not os.path.isdir(clean_dir):
                                    continue
                                for fname in os.listdir(clean_dir):
                                    if '_datrun_' in fname:
                                        try:
                                            os.remove(os.path.join(clean_dir, fname))
                                            n_removed += 1
                                        except OSError as e:
                                            print(f'Could not remove data-run file {fname}: {e}')
                            print(f'Pedestal run: discarded {n_removed} _datrun_ file(s).')

                        os.chdir(original_working_directory)

                        # if run_successful:
                        #     print('Moving data files.')
                        #     move_data_files(sub_run_dir, sub_run_out_raw_inner_dir)

                        server.send('Dream DAQ stopped')
                        logging.info(f'Subrun finished: {sub_run_name}')
                        teardown_logging(subrun_log_handler)
                        subrun_log_handler = None
                    else:
                        server.send('Unknown Command')
                    res = server.receive()
                logging.info('Run finished normally')
                if run_log_handler is not None:
                    teardown_logging(run_log_handler)
                    run_log_handler = None
        except Exception as e:
            logging.exception(f'Unhandled error: {e}')
            # This handler is the process's real crash path — it recovers in place
            # rather than exiting, so the durable copy has to go here. The sleep(30)
            # below bounds the rate, so no throttling is needed.
            _log('CRASH', error=repr(e), traceback=traceback.format_exc())
            if subrun_log_handler is not None:
                teardown_logging(subrun_log_handler)
                subrun_log_handler = None
            if run_log_handler is not None:
                teardown_logging(run_log_handler)
                run_log_handler = None
            try:
                os.chdir(original_working_directory)
            except Exception:
                pass
            try:
                server.send(f'Dream DAQ error: {e}')
            except Exception:
                pass
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

def copy_files_on_the_fly(sub_run_dir, sub_out_dir, daq_finished_event, check_interval=5, extra_minutes_after_finish=3,
                          skip_substrings=None):
    """
    Copies new .fdf files during the run, and continues for extra_minutes_after_finish
    after DAQ finishes, then exits cleanly without blocking the main thread.

    :param skip_substrings: Optional iterable of substrings; any .fdf whose name contains
        one is never copied. Used by pedestal runs to keep the empty _datrun_ FDFs (written
        only so batch RunCtrl self-exits) out of raw_daq_data, where 0-byte FDFs can deadlock
        the processor.
    """
    skip_substrings = tuple(skip_substrings or ())

    def _skip(name):
        return any(s in name for s in skip_substrings)

    create_dir_if_not_exist(sub_out_dir)
    sleep(60)  # Give DAQ time to start writing before first scan

    file_num = 0

    # Phase 1: DAQ running
    while not daq_finished_event.is_set():
        files_for_num = [
            f for f in os.listdir(sub_run_dir)
            if f.endswith('.fdf') and not _skip(f) and get_file_num_from_fdf_file_name(f, -2) == file_num
        ]
        if files_for_num and not file_num_still_running(sub_run_dir, file_num, silent=True):
            for file_name in files_for_num:
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
            if file_name.endswith('.fdf') and not _skip(file_name) and file_name not in already_seen:
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


def get_tmux_pane():
    """Return the current tmux pane address (session:window.pane) for use with send-keys."""
    try:
        result = subprocess.run(
            ['tmux', 'display-message', '-p', '#{session_name}:#{window_index}.#{pane_index}'],
            capture_output=True, text=True, timeout=5,
        )
        addr = result.stdout.strip()
        return addr if addr else None
    except Exception:
        return None


def runctrl_batch_watchdog(process, go_timeout, tmux_pane):
    """Recover from a batch-mode failure where RunCtrl is stuck waiting for 'G'.

    Sleeps for go_timeout seconds.  If RunCtrl is still running at that point it
    has not started on its own (pedestals + startup normally complete well within
    go_timeout).  Sends 'G' to the tmux pane so the run can proceed.  Only fires
    once; if RunCtrl already exited the thread does nothing.
    """
    sleep(go_timeout)
    if process.poll() is not None:
        return  # Already finished normally — batch mode worked, nothing to do
    print(f'\nWARNING: RunCtrl still running after {go_timeout:.0f}s — batch mode may be '
          f'stuck waiting for "G".  Sending G to recover.')
    if tmux_pane:
        subprocess.run(['tmux', 'send-keys', '-t', tmux_pane, 'G'],
                       stderr=subprocess.DEVNULL)
    else:
        print('WARNING: Could not determine tmux pane address — G not sent automatically.')


def prepare_terminal():
    """Reset terminal state before each RunCtrl launch.

    Two things can cause batch mode to intermittently fail between sequential sub-runs:
    1. Stray bytes in the stdin buffer (e.g. a leftover 'g' from an early stop arriving
       after RunCtrl has already exited but before the next one starts).
    2. Terminal left in raw mode if a previous RunCtrl was killed before its cleanup
       trap could run 'stty sane'.
    Both are fixed here without removing stdin access (needed so 'g' can stop the run).
    """
    try:
        termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
    except Exception:
        pass
    try:
        subprocess.run(['stty', 'sane'], stdin=sys.stdin, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def clear_terminal():
    try:
        os.system('cls' if os.name == 'nt' else 'clear')
    except:
        print('Failed to clear terminal')  # Ignore any errors


def _to_bit(val):
    """Convert bool/int/str (0, 1, True, False, 'true', 'false') to integer 0 or 1."""
    if isinstance(val, bool):
        return int(val)
    if isinstance(val, int):
        return int(bool(val))
    s = str(val).strip().lower()
    if s in ('1', 'true', 'yes'):
        return 1
    if s in ('0', 'false', 'no'):
        return 0
    raise ValueError(f"Cannot convert {val!r} to 0/1")


def _to_zs_typ(val):
    """Convert ZsTyp value: 0/'tracker' → 0, 1/'tpc' → 1."""
    if isinstance(val, bool):
        return int(val)
    if isinstance(val, int):
        return val
    s = str(val).strip().lower()
    if s in ('tpc', '1'):
        return 1
    if s in ('tracker', '0'):
        return 0
    raise ValueError(f"Cannot convert {val!r} to ZsTyp (0=tracker, 1=tpc)")


# Hardware sample period (ns) -> DREAM SCA clock dividers (RdClk_Div, WrClk_Div). The 100 MHz
# trigger clock is divided by WrClk_Div to set the SCA write (sampling) period: 2.0 -> 20 ns,
# 6.0 -> 60 ns. Only these two sample periods are supported; anything else raises.
#
# READ CLOCK IS PINNED AT THE 4.0 MINIMUM (25 MHz) for BOTH presets -- deliberately decoupled from
# the sample clock. RdClk sets the SCA readout speed, hence per-event deadtime, hence sustained rate;
# 4.0 is the hardware minimum divisor (Drm_RdClk_Div_Min in FeuUdpControl/DrmClkConfig.c -> below it
# FeuCtrl_Open fails). Measured 2026-07-23: read clock 6.0->4.0 (16.7->25 MHz) buys a clean 1.5x rate
# (7231->10847 Hz, 0 drops, tracers 100%, baseline unchanged) with the 60 ns / 32-sample / 1.92 us
# window untouched. Full analysis + firmware limits: docs/CLOCK_RATE_SCAN_2026-07-23.md.
# CAVEAT: 25 MHz is a mild overclock of the ASIC's rated 20 MHz RCk (the firmware permits it). Clean
# in all short tests; a multi-hour soak to confirm long-term stability is still pending (do it with
# beam back). To revert to the conservative 16.7 MHz read clock, set 60 -> ('6.0', '6.0').
# WrClk_Phase 2 / AdcClk_Phase 5 (template) are valid at both -- 4.0/6.0 verified clean as `coarse_a`.
SAMPLE_PERIOD_CLOCK_DIVS = {
    20: ('4.0', '2.0'),   # 20 ns sampling, 25 MHz read (vendor pair)
    60: ('4.0', '6.0'),   # 60 ns sampling, 25 MHz read -- production default (was ('6.0','6.0'))
}


def make_config_from_template(run_dir, cfg_template_file_path, cfg_file_run_time, zero_suppress_mode=False,
                              samples_per_waveform=None, pedestal_subtraction=None,
                              common_noise_subtraction=None, zs_type=None, zs_check_sample=None,
                              latency=None, included_feus=None, feu_connectors=None, trigger_feu=None,
                              do_pedestal_threshold_run=None, do_data_run=None, sample_period=None,
                              daq_run_events=None, inter_packet_delay=None,
                              multipack_thr=None, multipack_enb=None, sparse_rd=None, rdclk_div=None,
                              wrclk_div=None, rd_del=None, adc_dat_rdy_del=None,
                              trig_veto_len=None, ovr_wrn_hwm=None, ovr_wrn_lwm=None, loc_throt=None):
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

    updates = {
        "Sys DaqRun Time": cfg_file_run_time * 60,  # Seconds
        "Sys DaqRun Mode": 'ZS' if zero_suppress_mode else 'Raw',
        "Feu * Feu_RunCtrl_ZS": _to_bit(zero_suppress_mode),
    }
    if samples_per_waveform is not None:
        updates["Sys NbOfSamples"] = samples_per_waveform
    if pedestal_subtraction is not None:
        updates["Feu * Feu_RunCtrl_Pd"] = _to_bit(pedestal_subtraction)
    if common_noise_subtraction is not None:
        updates["Feu * Feu_RunCtrl_CM"] = _to_bit(common_noise_subtraction)
    if zs_type is not None:
        updates["Feu * Feu_RunCtrl_ZsTyp"] = _to_zs_typ(zs_type)
    if zs_check_sample is not None:
        val = int(zs_check_sample)
        if not (0 <= val <= 4):
            raise ValueError(f"zs_check_sample must be between 0 and 4, got {val}")
        updates["Feu * Feu_RunCtrl_ZsChkSmp"] = val
    if latency is not None:
        updates["Feu * Dream * 12"] = f'0x{int(latency):04X}'
    if sample_period is not None:
        try:
            rd_div, wr_div = SAMPLE_PERIOD_CLOCK_DIVS[int(sample_period)]
        except (KeyError, ValueError, TypeError):
            raise ValueError(
                f"sample_period must be one of {sorted(SAMPLE_PERIOD_CLOCK_DIVS)} ns, got {sample_period!r}")
        updates["Feu * DrmClk RdClk_Div"] = rd_div
        updates["Feu * DrmClk WrClk_Div"] = wr_div
    if daq_run_events is not None:
        # Per-FEU event cap. RunCtrl stops the data run at whichever comes FIRST: this many
        # events/FEU or Sys DaqRun Time (RunCtrlMain.c TestFun_TakeData). 0 = infinite.
        updates["Sys DaqRun Events"] = int(daq_run_events)
    if inter_packet_delay is not None:
        # Per-FEU inter-packet delay (Feu_InterPacket_Delay), template default 100.
        # Sets the gap between UDP data packets a FEU emits -> the dominant knob for the
        # per-event deadtime / sustained-rate ceiling (dead ~= 1 us * IPD * NbOfSamples/32;
        # see docs/live_zs_run_sources_2026-07-19.md, zs_ipd_safety FINDINGS_2026-07-18).
        # SAFETY: low IPD is only safe with ZS (mostly-empty packets); Raw/full-readout
        # needs IPD >= ~75 or the 1 GbE wire/host buffers overflow. Leave None to keep the
        # template's 100. ZS beam runs use 2 (k8) on the SSD write path.
        updates["Feu * Feu_InterPacket_Delay"] = int(inter_packet_delay)
    if multipack_thr is not None:
        # UdpChan_MultiPackThr: bytes accumulated before a packed UDP datagram ships
        # (MultiPackEnb=1). Never splits a packet; raising it toward the FEU 8192 frame
        # cap packs fuller / fewer datagrams. Above MTU-MaxDataPacketSize the last add can
        # overrun the frame -> last packet dropped (comb study 2026-07-19). See
        # Documents/dream project note dream-jumbo-network-tuning.
        updates["Feu * UdpChan_MultiPackThr"] = int(multipack_thr)
    if multipack_enb is not None:
        updates["Feu * UdpChan_MultiPackEnb"] = _to_bit(multipack_enb)
    if sparse_rd is not None:
        # Main_Conf_SparseRd (0x100004 bits 19:17): 0 = read all samples, n=[1..7] skip n
        # samples in the SCA readout. Shortens the analog readout deadtime (the flash comb)
        # while keeping the window SPAN, at coarser time resolution. Comb study 2026-07-19.
        updates["Feu * Main_Conf_SparseRd"] = int(sparse_rd)
    if rdclk_div is not None:
        # DrmClk RdClk_Div = TrigClock/RdClk read-clock divisor (valid [2..6], 0.5 steps).
        # LOWER = faster SCA readout = shorter deadtime (the flash comb), but the DREAM RCk is
        # specced to 20 MHz and phases (WrClk_Phase/AdcClk_Phase) are tuned for the nominal
        # divisor — faster is the manual's "delicate" region: VERIFY decoded data sanity
        # (baseline ~256, no DreamRdErr). Overrides the sample_period-derived RdClk_Div; WrClk
        # (sampling period) is left untouched. Comb study 2026-07-19.
        updates["Feu * DrmClk RdClk_Div"] = f'{float(rdclk_div)}'
    if wrclk_div is not None:
        # DrmClk WrClk_Div = TrigClock/WrClk SCA *write* (sampling) divisor. 2.0 -> 20 ns/sample,
        # 6.0 -> 60 ns/sample. Normally set as a pair with RdClk_Div via `sample_period`
        # (SAMPLE_PERIOD_CLOCK_DIVS); this override exists to decouple the two so the readout
        # clock can be raised WITHOUT collapsing the sampling window, or vice versa. Overrides
        # the sample_period-derived WrClk_Div.
        # CAUTION: WrClk_Phase / AdcClk_Phase in the cfg are tuned for the vendor divisor PAIRS
        # (6.0/6.0 and 4.0/2.0). Any unpaired combination is un-phased territory -- VERIFY the
        # decoded data (baseline ~256, ZS tracer channels present in 100% of events) before
        # trusting a rate number taken there.
        updates["Feu * DrmClk WrClk_Div"] = f'{float(wrclk_div)}'
    if rd_del is not None:
        # Feu_RunCtrl_RdDel -> RunControl 0x200008 bit 22 (DreamRdDel). FEU manual 3.2.3: "intended
        # for tests", DEFAULT 0; when 1 the FIRST Dream Read of the train is delayed by a hardcoded
        # 1536 core-clock cycles (12.3 us @125 MHz / 15.4 us @100 MHz). NOT in our template, so the
        # hardware has been sitting at 1 (peeked on all 8 FEUs 2026-07-23) -- inherited, not chosen.
        # SAFE TO SET: our data runs use `Sys DaqRun Trig Ext`, and that RunCtrl branch (RunCtrl.c
        # ~1328) sets ONLY Feu_PreScale_EvtData -- it never touches RdDel, so unlike the trigger-FIFO
        # watermarks this value is NOT clamped. VERIFY anyway: peek 0x200008 bit 22.
        updates["Feu * Feu_RunCtrl_RdDel"] = int(rd_del)
    if adc_dat_rdy_del is not None:
        # Feu_RunCtrl_AdcDatRdyDel -> 0x200008 bits 20:16 (Rd2AdcDataDel): read-clock cycles the logic
        # waits between the Dream Read strobe and valid ADC data. Manual: "for the 20.8(3) MHz read
        # clock this value is usually set to 8"; hardware holds 8, but we now read at 25 MHz
        # (RdClk_Div 4.0), so 8 may no longer centre the ADC eye. This is a DATA-QUALITY knob, not a
        # rate knob -- judge it on baseline RMS / tracer integrity, not on IntRate.
        updates["Feu * Feu_RunCtrl_AdcDatRdyDel"] = int(adc_dat_rdy_del)
    if trig_veto_len is not None:
        # Trig_Conf_TrigVetoLen (TrigGen CSR 0xE00000): per-trigger dead window in trigger-clock
        # periods (10-bit, max 1023 ≈ 8-10 µs). Rejects a new trigger within this window of the
        # previous one — kills close-spaced re-triggers ("ringing"), e.g. the ~5 µs-spaced γ-flash
        # burst. Cost: also drops genuine physics triggers closer than the window. Comb study 2026-07-19.
        updates["Feu * Trig_Conf_TrigVetoLen"] = int(trig_veto_len)
    if ovr_wrn_hwm is not None:
        # Main_Trig_OvrWrnHwm (TrigConfig 0x100008, bits 23:18): trigger-FIFO occupancy above
        # which the FEU raises OverflowWarning -> FEU BUSY -> TCM stops sending triggers. FIFO is
        # 64 deep; cfg default 20 (manual default 24). Raising it was a NULL result (comb study
        # 2026-07-19: occupancy never exceeds ~11-12 so the mark is never reached). LOWERING it
        # below the observed occupancy is the untested direction — it should make the FEU assert
        # BUSY earlier and throttle the flash burst at the source. Pairs with loc_throt.
        updates["Feu * Main_Trig_OvrWrnHwm"] = int(ovr_wrn_hwm)
    if ovr_wrn_lwm is not None:
        # Main_Trig_OvrWrnLwm (0x100008, bits 17:12): occupancy below which OverflowWarning
        # clears. Hysteresis partner of the HWM — keep LWM < HWM. cfg default 16.
        updates["Feu * Main_Trig_OvrWrnLwm"] = int(ovr_wrn_lwm)
    if loc_throt is not None:
        # Main_Trig_LocThrot (0x100008, bit 30): if 1, the FEU refuses triggers while in the
        # OverflowWarning condition ("local trigger throttle", manual 3.1.3). cfg default 1.
        # UNVERIFIED whether this gates EXTERNAL (TCM) triggers or only self-triggers — the
        # sibling knob Trig_Conf_TrigVetoLen proved inert under external triggering (comb study).
        # That is exactly what the HWM-down scan is designed to decide.
        updates["Feu * Main_Trig_LocThrot"] = _to_bit(loc_throt)
    if do_pedestal_threshold_run is not None:
        updates["Sys Action PedThrRun"] = _to_bit(do_pedestal_threshold_run)
    if do_data_run is not None:
        # Sys Action DataRun controls the data-taking phase that follows the
        # pedestal/threshold run. Pedestal runs set this to 0 so RunCtrl produces
        # only the _pedthr_ files (and _ped/_thr .prg) and skips the pointless
        # data run, which otherwise writes empty _datrun_ FDFs (external trigger,
        # no beam -> 0 events).
        updates["Sys Action DataRun"] = _to_bit(do_data_run)
    update_config_value(cfg_file_path, updates)

    if included_feus is not None:
        set_active_feus(cfg_file_path, included_feus, feu_connectors=feu_connectors, trigger_feu=trigger_feu)

    return cfg_file_path


# Connectors in dream_feus are 1-based (1..8) and map to FEU Dream indices 0..7.
CONNECTOR_DREAM_OFFSET = 1  # connector = dream_index + CONNECTOR_DREAM_OFFSET


def set_active_feus(filepath, included_feus, feu_connectors=None, trigger_feu=None, output_path=None):
    """
    Enable only the given FEUs in a Dream .cfg, and set per-Dream roles, by editing lines in place.

    For each FEU-specific hardware line (``Feu N Feu_RunCtrl_Id ...``, ``Feu N NetChan_Ip ...``) and
    the ``Sys Topo Feu N ...`` topology line, the line is left active when its FEU number N is in
    ``included_feus`` and commented out otherwise.

    For an active ``Sys Topo`` line, the per-Dream roles are also rewritten when ``feu_connectors`` is
    given (a ``{feu_number: [used connectors]}`` map): each Dream becomes ``Dat`` when its connector is
    used by an included detector, else ``Msk``. The ``trigger_feu`` (e.g. a dedicated trigger FEU) is
    left untouched so it keeps its template roles. nTof triggers on multiplicity coincidence and has no
    dedicated trigger FEU, so trigger_feu is normally None here.

    Wildcard ``Feu * ...`` lines and per-FEU register overrides (e.g. ``Feu 1 Dream * ...``) are left
    untouched, so the template remains the source of truth for hardware Id/IP and Dream registers.
    """
    output_path = output_path or filepath
    included = {int(f) for f in included_feus}
    feu_connectors = {int(k): set(v) for k, v in (feu_connectors or {}).items()}
    trigger_feu = int(trigger_feu) if trigger_feu is not None else None

    topo_pat = re.compile(
        r'^(?P<indent>\s*)#*\s*(?P<head>Sys\s+Topo\s+Feu\s+(?P<num>\d+)\s+Dream\s+)(?P<dreams>.*)$')
    hw_pats = [
        re.compile(r'^(?P<indent>\s*)#*\s*(?P<body>Feu\s+(?P<num>\d+)\s+Feu_RunCtrl_Id\b.*)$'),
        re.compile(r'^(?P<indent>\s*)#*\s*(?P<body>Feu\s+(?P<num>\d+)\s+NetChan_Ip\b.*)$'),
    ]

    def set_dream_roles(dreams_str, connectors):
        # Replace each "<dream_index> <whitespace> <role>" triplet's role, preserving spacing.
        def repl(m):
            dream_idx = int(m.group(1))
            role = 'Dat' if (dream_idx + CONNECTOR_DREAM_OFFSET) in connectors else 'Msk'
            return f'{m.group(1)}{m.group(2)}{role}'
        return re.sub(r'(\d+)(\s+)(?:Trg|Dat|Msk)', repl, dreams_str)

    with open(filepath, 'r') as f:
        lines = f.readlines()

    new_lines = []
    activated, deactivated = set(), set()
    for line in lines:
        raw = line.rstrip('\n')

        m = topo_pat.match(raw)
        if m:
            feu_num = int(m.group('num'))
            indent, head, dreams = m.group('indent'), m.group('head'), m.group('dreams')
            if feu_num in included:
                # Rewrite roles for active data FEUs; leave the trigger FEU's roles as-is.
                if feu_num != trigger_feu and feu_num in feu_connectors:
                    dreams = set_dream_roles(dreams, feu_connectors[feu_num])
                new_lines.append(f'{indent}{head}{dreams}\n')
                activated.add(feu_num)
            else:
                new_lines.append(f'{indent}#{head}{dreams}\n')
                deactivated.add(feu_num)
            continue

        for pat in hw_pats:
            hm = pat.match(raw)
            if not hm:
                continue
            feu_num = int(hm.group('num'))
            indent, body = hm.group('indent'), hm.group('body')
            was_commented = raw.lstrip().startswith('#')
            if feu_num in included:
                # Preserve the template's comment state for included FEUs. nTof templates may carry a
                # second, commented-out alternate hardware block (spare FEU Ids/IPs); force-uncommenting
                # would resurrect it and create a duplicate Feu_RunCtrl_Id/NetChan_Ip line. The template's
                # already-active block stays the single source of truth for each FEU's Id/IP.
                new_lines.append(line)
            else:
                # Comment out hardware for FEUs not in this run (no-op if already commented).
                new_lines.append(line if was_commented else f'{indent}#{body}\n')
            break
        else:
            new_lines.append(line)

    with open(output_path, 'w') as f:
        f.writelines(new_lines)

    print(f'Set active FEUs from detectors: enabled {sorted(activated)}, disabled {sorted(deactivated)}')
    missing = included - activated
    if missing:
        print(f'WARNING: requested FEUs {sorted(missing)} have no Sys Topo lines in the template '
              f'and could not be enabled.')


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
            pattern = rf"^(\s*{flag_pattern}\s+)([^\s#]+)"
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
    # sub_run_name = 'pedestals_noise'  # Standard name for pedestal runs
    sub_run_name = 'pedestals'  # Standard name for pedestal runs
    if not os.path.isdir(pedestals_dir):
        print(f'Pedestals directory `{pedestals_dir}` does not exist.')
        return None

    if pedestals == 'latest':
        # Find latest pedestal files in pedestals_dir. Directories named pedestals_MM-DD-YY_HH-MM-SS or pedestals_MM-DD-YYYY_HH-MM-SS
        valid_dirs = []
        for item in os.listdir(pedestals_dir):
            print(f'Checking pedestal item: {item}')
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
        print(f'Checking pedestal file: {file}')
        if file.endswith('.prg'):
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
                print(f'Copying pedestal file {file} for FEU {feu_num}...')
                shutil.copy(f'{pedestals_prg_dir}{file}', f'{run_dir}{dest_file_name}')
                if out_dir:
                    shutil.copy(f'{pedestals_prg_dir}{file}', f'{out_dir}{file}')
                # Write the pedestal_prg_dir directory name to a text file in the run directory for reference
                ped_run = pedestals_prg_dir.strip('/').split('/')[-2]
                with open(f'{run_dir}pedestal_run.txt', 'w') as f:
                    f.write(ped_run)
                if out_dir:
                    with open(f'{out_dir}pedestal_run.txt', 'w') as f:
                        f.write(ped_run)
                print(f'Copied pedestal file {file} to {dest_file_name}')
            else:
                print(f'Could not find FEU number in pedestal file name {file}, skipping.')
        elif file.endswith('.fdf'):
            feu_num_search = re.search(r'_(\d{3})_(\d{2})\.', file)
            if feu_num_search:
                print(f'Copying pedestal fdf file {file}...')
                shutil.copy(f'{pedestals_prg_dir}{file}', f'{run_dir}{file}')
                if out_dir:
                    shutil.copy(f'{pedestals_prg_dir}{file}', f'{out_dir}{file}')
            else:
                print(f'Could not find FEU number in pedestal fdf file name {file}, skipping.')


if __name__ == '__main__':
    main()
