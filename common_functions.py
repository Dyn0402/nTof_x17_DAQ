#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on May 13 4:44 PM 2024
Created in PyCharm
Created as Cosmic_Bench_DAQ_Control/common_functions.py

@author: Dylan Neff, Dylan
"""

import os
from datetime import datetime
import time
import yaml


def load_yaml_config(file_path):
    with open(file_path, 'r') as f:
        # Loader=yaml.SafeLoader is recommended for security
        config = yaml.load(f, Loader=yaml.SafeLoader)
    return config


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
