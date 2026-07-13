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

# Stop-request flags dropped by bash_scripts/stop_run.sh and stop_sub_run.sh.
# Using flag files (instead of racing Ctrl-C into the tmux pane) makes stopping
# deterministic: daq_control checks them between/after sub-runs and stops the DAQ
# via stop_dream.sh. Paths must match those scripts (repo root = this file's dir).
STOP_RUN_FLAG = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.stop_run')
STOP_SUBRUN_FLAG = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.stop_subrun')
# Post-sub-run pause flag (set/cleared by the flask "Pause after subrun" button).
# When present, daq_control waits at the next sub-run boundary until it's cleared
# (Resume). One-shot: clearing it lets the run continue without re-pausing.
PAUSE_FLAG = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.pause_run')


def _remove_flag(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def _snapshot_n1081b(out_path, label):
    """Fire a read-only background snapshot of the N1081B trigger modules for this
    sub-run (see n1081b/poll_modules.py). Fully guarded: any import/runtime problem
    is logged and swallowed so it can never disturb the run. Fired AFTER the inline
    N1081B config apply (see _apply_n1081b_with_retry) so it records the as-built
    trigger state; the .pause_run wait remains as a guard against a manual pause."""
    try:
        _n1081b_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'n1081b')
        if _n1081b_dir not in sys.path:
            sys.path.insert(0, _n1081b_dir)
        from poll_modules import poll_in_background
        poll_in_background(out_path, label=label, wait_flag=PAUSE_FLAG)
    except Exception as e:  # noqa: BLE001 - snapshotting must never break the run
        print(f'[n1081b] snapshot skipped ({e!r})')


def _make_scan_control(config):
    """Build the in-process N1081B scan controller (replaces the standalone
    n1081b_scan_watcher.py process). Construction is board-free — it only reads the
    schedule and the run's sub-run tags — so it is safe for every run type; a run
    that needs no trigger modulation yields a controller whose .needed is False.
    Returns None only if the controller module itself cannot be imported (a code
    problem), which is surfaced loudly rather than silently proceeding."""
    try:
        _n1081b_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'n1081b')
        if _n1081b_dir not in sys.path:
            sys.path.insert(0, _n1081b_dir)
        from scan_control import N1081BScanControl
        mode = getattr(config, 'n1081b_scan', 'auto')  # 'auto' | 'on' | 'off'
        ctl = N1081BScanControl(getattr(config, 'sub_runs', []), mode=mode)
        print(f'[n1081b] scan control: {ctl.summary()}')
        unknown = ctl.unknown_tags()
        if unknown:
            print(f'[n1081b] WARNING: sub-run tag(s) {unknown} have no schedule entry — '
                  f'those sub-runs run with the trigger AS-IS.')
        return ctl
    except Exception as e:  # noqa: BLE001
        print(f'[n1081b] !! scan control unavailable ({e!r}). If this run needs '
              f'trigger modulation, STOP and fix before taking data.')
        return None


def _apply_n1081b_with_retry(scan_ctl, sub_run):
    """Apply the sub-run's N1081B trigger/mesh config, verified by read-back. On
    failure, HOLD the run with the PAUSE flag and retry until it verifies or a Stop
    Run is requested — so we never take data with an unverified trigger (the safety
    property the standalone watcher provided by holding .pause_run). Returns True
    once applied (or not needed); False only if a stop ended the wait."""
    if scan_ctl is None or not getattr(scan_ctl, 'needed', False):
        return True
    announced = False
    i_held = False   # did WE arm the pause? (only then may we clear it)
    while not scan_ctl.apply_for(sub_run):
        if os.path.exists(STOP_RUN_FLAG):
            return False
        if not announced:
            print('[n1081b] !! trigger/mesh config did NOT verify — HOLDING the run '
                  '(paused). Fix the board, then Resume to retry. Refusing to take '
                  'data with an unverified trigger.')
            announced = True
        with open(PAUSE_FLAG, 'w') as f:
            f.write('n1081b config apply failed — fix board and Resume to retry\n')
        i_held = True
        while os.path.exists(PAUSE_FLAG) and not os.path.exists(STOP_RUN_FLAG):
            sleep(1)
        if os.path.exists(STOP_RUN_FLAG):
            return False
    # Clear ONLY a hold we armed — never an operator's GUI pause that was set after
    # the top-of-loop pause check (that pause must survive to the next boundary).
    if i_held:
        _remove_flag(PAUSE_FLAG)
    return True


def _sleep_unless_stop(seconds):
    """Sleep in 1 s steps, returning early if a stop-run is requested so Stop Run
    stays responsive through a configured post-sub-run pause."""
    waited = 0
    while waited < seconds and not os.path.exists(STOP_RUN_FLAG):
        sleep(1)
        waited += 1


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

    # In-process N1081B scan control (replaces the standalone scan-watcher process):
    # daq_control applies each sub-run's trigger/mesh config itself, so it can never
    # be forgotten. No-op for runs that need no modulation.
    scan_ctl = _make_scan_control(config)

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
        _remove_flag(STOP_RUN_FLAG)  # clear any stale stop requests from a previous run
        _remove_flag(STOP_SUBRUN_FLAG)
        _remove_flag(PAUSE_FLAG)     # never start a run already paused

        try:
            # N1081B trigger-control pre-flight — FAIL CLOSED. A run that needs
            # trigger modulation must never silently take data without it (the
            # run_30/run_33 corruption). Inside the try so restore-on-exit always
            # covers a snapshot that partially applied.
            ok_to_run = True
            if scan_ctl is None:
                print('[n1081b] !! scan control could not be built — REFUSING to start. '
                      'Fix the error above, or set n1081b_scan="off" in the run config '
                      'to deliberately run WITHOUT trigger modulation.')
                ok_to_run = False
            elif scan_ctl.needed:
                unknown = scan_ctl.unknown_tags()
                if unknown:
                    print(f'[n1081b] !! sub-run tag(s) {unknown} have no schedule entry '
                          f'— REFUSING to start (those sub-runs would take data with an '
                          f'uncontrolled / leftover trigger). Fix the schedule or the '
                          f'sub-run names.')
                    ok_to_run = False
                else:
                    try:
                        scan_ctl.start()   # snapshot boards for restore-on-exit
                    except Exception as e:  # noqa: BLE001
                        print(f'[n1081b] !! could not snapshot the trigger boards '
                              f'({e!r}) — REFUSING to start a scan run without trigger '
                              f'control. Fix the board network and relaunch.')
                        ok_to_run = False

            for sub_run in (config.sub_runs if ok_to_run else []):
                if os.path.exists(STOP_RUN_FLAG):
                    print('[stop] Stop-run requested — ending run before next sub-run.')
                    break
                # Post-sub-run pause: if armed, wait here before ramping the next
                # sub-run. HV stays at its current setpoint. Interruptible by Stop Run;
                # clearing the flag (Resume) continues the run (one-shot).
                if os.path.exists(PAUSE_FLAG):
                    print('[pause] Paused after sub-run — waiting for Resume...')
                    while os.path.exists(PAUSE_FLAG) and not os.path.exists(STOP_RUN_FLAG):
                        sleep(1)
                    if os.path.exists(STOP_RUN_FLAG):
                        print('[stop] Stop-run requested during pause — ending run.')
                        break
                    print('[pause] Resumed.')
                sub_run_name = sub_run['sub_run_name']
                # sub_run_dir = f'{config.dream_daq_info["run_directory"]}{sub_run_name}/'
                # create_dir_if_not_exist(sub_run_dir)  # Means DAQ runs on Dream CPU! Can fix, need config template in dream_daq control!
                sub_top_out_dir = f'{config.run_out_dir}{sub_run_name}/'
                complete_marker = f'{sub_top_out_dir}.subrun_complete'
                if getattr(config, 'resume', False) and os.path.exists(complete_marker):
                    print(f'[resume] Skipping already-completed sub run {sub_run_name}')
                    continue
                create_dir_if_not_exist(sub_top_out_dir)
                sub_out_dir = f'{sub_top_out_dir}{config.raw_daq_inner_dir}/'
                create_dir_if_not_exist(sub_out_dir)

                if getattr(config, 'weiner_ps_info', None):  # Ensure ps is on before starting run
                    weiner_ok = check_weiner_lv_status(config.weiner_ps_info)
                    if not weiner_ok:
                        print(f'Weiner Power Supply check failed, skipping sub run {sub_run_name}')
                        continue

                # Emit the status line before ramping so the flask daq_control card shows the
                # current run/subrun immediately — otherwise it displays the previous run's name
                # (from the last [status] line still in the tmux buffer) throughout the HV ramp.
                print(f'[status] run={config.run_name}  subrun={sub_run_name}  run_time={sub_run.get("run_time", "?")}min')

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
                    settle_time = sub_run.get('settle_time', 0)  # Seconds; 0 for most runs
                    if settle_time and not os.path.exists(STOP_RUN_FLAG):
                        print(f'HV ramp complete, settling for {settle_time} seconds before starting DAQ')
                        sleep(settle_time)

                    # Apply this sub-run's N1081B trigger/mesh config INLINE (replaces
                    # the standalone scan watcher). Verified by read-back; the run is
                    # held paused on failure so we never take data with a wrong trigger.
                    if not _apply_n1081b_with_retry(scan_ctl, sub_run):
                        if config.hv_info['hv_monitoring']:
                            hv.send('End Monitoring')
                            hv.receive()
                            hv.receive()
                        print('[stop] Stop requested while applying N1081B config — ending run.')
                        break

                    print(f'Prepping DAQs for {sub_run_name}')

                    # Read-only background snapshot of the N1081B trigger modules for
                    # this sub-run. Fired here (after HV ramp / any scan-watcher config
                    # change has settled, before the blocking DAQ call) so it runs in
                    # parallel with data-taking and captures the as-built trigger state.
                    _snapshot_n1081b(f'{sub_top_out_dir}n1081b_config.json', sub_run_name)

                    print(f'Starting run for sub run {sub_run_name}')
                    run_daq_controller(sub_run, sub_out_dir, dream_daq)

                    if config.hv_info['hv_monitoring']:
                        hv.send('End Monitoring')
                        hv.receive()  # Stopping monitoring
                        hv.receive()  # Finished monitoring

                    # A manual stop (stop_run/stop_sub_run) cuts the sub-run short, so don't mark it
                    # complete — resume should re-run it. Otherwise mark it so a resume run skips it.
                    stop_run_req = os.path.exists(STOP_RUN_FLAG)
                    stop_subrun_req = os.path.exists(STOP_SUBRUN_FLAG)
                    if stop_subrun_req:
                        _remove_flag(STOP_SUBRUN_FLAG)
                    if stop_run_req or stop_subrun_req:
                        print(f'[stop] Sub run {sub_run_name} stopped manually — not marking complete.')
                    else:
                        with open(complete_marker, 'w') as f:
                            f.write(datetime.now().strftime('%Y-%m-%d %H:%M:%S') + '\n')

                    print(f'Finished with sub run {sub_run_name}, waiting 10 seconds before next run')
                    sleep(10)

                    # Optional configured post-sub-run pause (seconds, from the run
                    # config). HV stays at its current setpoint; Stop Run interrupts it.
                    post_pause_s = sub_run.get('post_pause_s', 0) or 0
                    if post_pause_s > 0 and not os.path.exists(STOP_RUN_FLAG):
                        print(f'[pause] Post-sub-run pause: waiting {post_pause_s}s after {sub_run_name}...')
                        _sleep_unless_stop(post_pause_s)
                        print('[pause] Post-sub-run pause: done')
                else:
                    # HV ramp failed (e.g. a channel plateaued outside tolerance, or the
                    # CFE server dropped). Close out this sub-run's monitor thread and
                    # move on to the next sub-run instead of aborting the whole run —
                    # leaving it unmarked so a resume run re-tries it.
                    print(f'[hv] Ramp failed for {sub_run_name}: {res} — skipping this sub-run.')
                    if config.hv_info['hv_monitoring']:
                        hv.send('End Monitoring')
                        hv.receive()  # Stopping monitoring
                        hv.receive()  # Finished monitoring
        except KeyboardInterrupt as e:
            print(f'Run stoppping.')

            if config.hv_info['hv_monitoring']:
                hv.send('End Monitoring')
                hv.receive()  # Stopping monitoring
                hv.receive()  # Finished monitoring

        finally:
            _remove_flag(STOP_RUN_FLAG)
            _remove_flag(STOP_SUBRUN_FLAG)
            _remove_flag(PAUSE_FLAG)   # clear any apply-failure hold so the run
                                       # doesn't end leaving a stale 'paused' state
            # Return the N1081B boards to the exact state found at run start.
            if scan_ctl is not None:
                scan_ctl.restore()
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


def run_daq_controller(sub_run, sub_out_dir, dream_daq_client):
    daq_controller = DAQController(subrun=sub_run, out_dir=sub_out_dir, dream_daq_client=dream_daq_client)

    daq_success = False
    while not daq_success:  # Rerun if failure
        if os.path.exists(STOP_RUN_FLAG) or os.path.exists(STOP_SUBRUN_FLAG):
            print('[stop] Stop requested — not (re)starting DAQ controller.')
            break
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
