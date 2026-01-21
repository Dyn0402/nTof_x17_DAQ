#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on October 22 11:25 PM 2025
Created in PyCharm
Created as Cosmic_Bench_DAQ_Control/iterate_run_num.py

@author: Dylan Neff, Dylan
"""

import os
import re
from pathlib import Path

from run_config_beam import Config
BASE_DIR = '/local/home/banco/dylan/Cosmic_Bench_DAQ_Control'
RUNCONFIG_PY_PATH = 'run_config_beam.py'


def main():
    config = Config()

    # Check run_out_dir. If it exists, check if run_name has a _<number> suffix.
    # If so, increment the number until a non-existing directory is found. Else, add _1 suffix.
    # Then replace all occurrences of the old run_name string with the new one in the config.
    run_out_dir = config.run_out_dir
    run_name = config.run_name

    # Split the last directory off of the run_out_dir to get the parent directory
    run_base_dir = remove_last_dir(run_out_dir)

    if os.path.exists(run_out_dir):
        print(f"Run output directory {run_out_dir} already exists. Incrementing run_name...")
        base_run_name = run_name
        suffix_num = 1
        if '_' in run_name and run_name.split('_')[-1].isdigit():
            base_run_name = '_'.join(run_name.split('_')[:-1])
            suffix_num = int(run_name.split('_')[-1]) + 1

        new_run_name = f"{base_run_name}_{suffix_num}"
        new_full_run_path = os.path.join(run_base_dir, new_run_name)
        print(f"Trying new run_name: {new_run_name}")
        print(f"New run output directory: {new_full_run_path}")

        while os.path.exists(new_full_run_path):
            print(f'Run output directory {new_full_run_path} also exists. Incrementing suffix...')
            suffix_num += 1
            new_run_name = f"{base_run_name}_{suffix_num}"
            new_full_run_path = os.path.join(run_base_dir, new_run_name)

        # Open the original python config file and update the run_name
        py_path = os.path.join(BASE_DIR, RUNCONFIG_PY_PATH)
        update_run_number(py_path, suffix_num)

    print('donzo')


def update_run_number(file_path, new_run_number):
    """
    Updates the 'run_name' assignment in a Python config file.

    Args:
        file_path (str): Path to the Python config file.
        new_run_number (int): New run number to set, e.g. 64 for 'run_64'.
    """
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Regex to match: self.run_name = 'run_63' (single or double quotes)
    new_run_name = f"run_{new_run_number}"
    new_line = f"self.run_name = '{new_run_name}'"
    updated_content, n = re.subn(
        r"self\.run_name\s*=\s*['\"]run_\d+['\"]",
        new_line,
        content
    )

    if n == 0:
        raise ValueError("No run_name line found to update in file.")

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(updated_content)

    print(f"Updated run_name to '{new_run_name}' in {file_path}")


def remove_last_dir(path_str: str) -> str:
    p = Path(path_str)
    # Path('/') has parent '/', but Path('') becomes '.'
    parent = p.parent
    return str(parent)


if __name__ == '__main__':
    main()
