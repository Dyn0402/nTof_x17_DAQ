#!/usr/bin/env python3
"""
Generate flat-threshold pedestal directories for ZS scan tests.

For each threshold value, copies the source pedestals_* directory in zs_test to
pedestals_{threshold}/, then overwrites every .prg file inside with a flat
constant-threshold version of that file.

Thresholds: 290–350 in steps of 10, 400–600 in steps of 50, 700–1000 in steps of 100.
"""

import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(__file__))
from dream_threshold_manager import DreamThresholdManager

ZS_TEST_DIR = os.path.expanduser("~/Desktop/zs_test")


def threshold_sequence():
    thresholds = list(range(290, 351, 10))
    thresholds += list(range(400, 601, 50))
    thresholds += list(range(700, 1001, 100))
    return thresholds


def find_source_dir(zs_test_dir):
    """Return the original pedestals_* directory, ignoring any pedestals_{int} dirs we created."""
    candidates = [
        d for d in os.listdir(zs_test_dir)
        if os.path.isdir(os.path.join(zs_test_dir, d))
        and d.startswith("pedestals_")
        and not re.fullmatch(r"pedestals_\d+", d)
    ]
    if not candidates:
        raise FileNotFoundError(f"No source pedestals_* directory found in {zs_test_dir}")
    if len(candidates) > 1:
        raise ValueError(f"Multiple source directories found: {candidates}")
    return os.path.join(zs_test_dir, candidates[0])


def overwrite_prg_files(directory, threshold):
    """Set every .prg file in directory to a flat constant threshold."""
    for fname in os.listdir(directory):
        if not fname.endswith(".prg"):
            continue
        fpath = os.path.join(directory, fname)
        mgr = DreamThresholdManager()
        mgr.read_prg(fpath)
        for dream_id in range(8):
            for channel in range(64):
                mgr.set_threshold(dream_id, channel, threshold)
        mgr.write_prg(fpath)


def main():
    source_dir = find_source_dir(ZS_TEST_DIR)
    print(f"Source directory: {source_dir}")

    thresholds = threshold_sequence()
    print(f"Generating {len(thresholds)} threshold directories")

    for thr in thresholds:
        dest_dir = os.path.join(ZS_TEST_DIR, f"pedestals_{thr}")
        if os.path.exists(dest_dir):
            shutil.rmtree(dest_dir)
        shutil.copytree(source_dir, dest_dir)

        # The prg files sit inside a 'pedestals' subdirectory
        prg_dir = os.path.join(dest_dir, "pedestals")
        if not os.path.isdir(prg_dir):
            prg_dir = dest_dir

        overwrite_prg_files(prg_dir, thr)
        print(f"  pedestals_{thr} done")

    print("Done.")


if __name__ == '__main__':
    main()
