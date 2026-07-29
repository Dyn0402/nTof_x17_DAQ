#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on May 13 4:44 PM 2024
Created in PyCharm
Created as Cosmic_Bench_DAQ_Control/common_functions.py

@author: Dylan Neff, Dylan
"""

import os
import logging
from datetime import datetime
import time


def _oneline(value):
    """Flatten a detail value to a single line.

    The event-log convention is one line per event (that is what makes it
    greppable), and the most useful CRASH detail — a traceback — is multi-line.
    Newlines become a literal '\\n' so nothing is lost and nothing wraps.
    """
    return str(value).replace('\r', ' ').replace('\n', '\\n')


def log_event(log_path, event, source, **details):
    """Append one line to a lightweight per-process event log.

    Format matches the watcher logs that already exist (logs/qa_watcher.log,
    logs/pedestal_watcher.log):

        2026-07-29 10:15:03 | START            | backup_watch | key=value | key=value

    This is deliberately NOT `setup_logging`/`teardown_logging` above: those attach a
    logging.FileHandler to the root logger for the duration of a run and write into
    that run's own data directory. This is the lighter, standing "what did this
    process do" trail under the repo's logs/ dir, and the two are unrelated.

    Never raises. A logging failure must not take down the process it instruments.
    """
    try:
        log_dir = os.path.dirname(log_path)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        detail_str = ' | '.join(f'{k}={_oneline(v)}' for k, v in details.items())
        line = f"{ts} | {event:<16} | {source:<12} | {detail_str}\n"
        with open(log_path, 'a') as f:
            f.write(line)
    except Exception as e:
        print(f"Warning: could not write to {log_path}: {e}")


def setup_logging(log_path):
    """Attach a FileHandler for log_path to the root logger. Returns the handler."""
    handler = logging.FileHandler(log_path)
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s: %(message)s'))
    logging.getLogger().addHandler(handler)
    return handler


def teardown_logging(handler):
    """Remove and close a logging FileHandler."""
    logging.getLogger().removeHandler(handler)
    handler.close()


def create_dir_if_not_exist(dir_path):
    if not os.path.isdir(dir_path):
        os.makedirs(dir_path)
        os.chmod(dir_path, 0o777)


def get_date_from_fdf_file_name(file_name):
    """
    Get date from file name with format ...xxx_xxx_240212_11H42_000_01.xxx
    :param file_name:
    :return:
    """
    date_str = file_name.split('_')[-4] + ' ' + file_name.split('_')[-3]
    date = datetime.strptime(date_str, '%y%m%d %HH%M')
    return date


def get_feu_num_from_fdf_file_name(file_name):
    """
    Get fdf style feu number from file name with format ...xxx_xxx_240212_11H42_000_01.xxx
    :param file_name:
    :return:
    """
    fdf_num = int(file_name.split('_')[-1].split('.')[0])
    return fdf_num


def get_file_num_from_fdf_file_name(file_name, num_index=-2):
    """
    Get fdf style file number from file name with format ...xxx_xxx_240212_11H42_000_01.xxx
    Updated to more robustly get first number from back.
    :param file_name:
    :param num_index:
    :return:
    """
    file_split = remove_after_last_dot(file_name).split('_')
    file_nums = []
    for x in file_split:
        try:
            file_nums.append(int(x))
        except ValueError:
            pass
    return file_nums[num_index]


def remove_after_last_dot(input_string):
    # Find the index of the last dot
    last_dot_index = input_string.rfind('.')

    # If there's no dot, return the original string
    if last_dot_index == -1:
        return input_string

    # Return the substring up to the last dot (not including the dot)
    return input_string[:last_dot_index]


def get_run_name_from_fdf_file_name(file_name):
    file_name_split = file_name.split('_')
    run_name_end_index = 0
    for i, part in enumerate(file_name_split):  # Find xxHxx in file name split
        if len(part) == 5 and part[2] == 'H' and is_convertible_to_int(part[:2]) and is_convertible_to_int(part[3:]):
            run_name_end_index = i
            break
    run_name = '_'.join(file_name_split[:run_name_end_index + 1])
    return run_name


def is_convertible_to_int(s):
    try:
        int(s)
        return True
    except ValueError:
        return False


def wait_for_copy_complete(filepath, check_interval=1.0, stable_time=3.0, wait_for_creation=False):
    """
    Wait until file size stops changing for 'stable_time' seconds.

    Args:
        filepath (str): Path to file to check.
        check_interval (float): Seconds between size checks.
        stable_time (float): Time file size must remain constant to be considered complete.
        wait_for_creation (bool):
            - If True, keep waiting until the file appears.
            - If False, return False immediately if file does not exist.

    Returns:
        bool: True if file appears and stabilizes, False otherwise.
    """
    last_size = -1
    stable_start = None

    while True:
        if not os.path.exists(filepath):
            if not wait_for_creation:
                return False
            stable_start = None
            time.sleep(check_interval)
            continue

        current_size = os.path.getsize(filepath)

        if current_size == last_size:
            if stable_start is None:
                stable_start = time.time()
            elif time.time() - stable_start >= stable_time:
                return True  # File size stable long enough
        else:
            stable_start = None
            last_size = current_size

        time.sleep(check_interval)
