#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Build flat homogeneous-threshold pedestal dirs for the self-trigger threshold scan.

For each threshold value, copies the *_ped.prg / *_thr.prg files from the latest real
pedestal run into  <OUT_BASE>/pedestals_flat<thr>/pedestals/  and overwrites ONLY the
*_thr.prg files with a flat constant threshold (every Dream, every channel). The real
*_ped.prg pedestals are kept unchanged. The run pipeline's get_pedestals() then loads
these as dream_thresholds_NN_thr.prg / dream_pedestals_NN_ped.prg.

Layout matches what get_pedestals expects:  pedestals_dir/<pedestals>/pedestals/*.prg
"""

import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(__file__))
from dream_threshold_manager import DreamThresholdManager

SRC_PED_DIR = '/mnt/data/x17/beam_july/pedestals/'          # holds pedestals_MM-DD-YY_HH-MM-SS/
OUT_BASE = '/mnt/data/x17/beam_july/pedestals_selftrig/'    # per-run_config pedestals_dir
INNER = 'pedestals'                                          # .prg live in this subdir
THRESHOLDS = [1000, 700, 500]

_DT_DIR = re.compile(r'^pedestals_\d{2}-\d{2}-\d{2,4}_\d{2}-\d{2}-\d{2}$')


def latest_source_run(src_dir):
    """Most recently modified real pedestal run dir (pedestals_<datetime>/)."""
    cands = [os.path.join(src_dir, d) for d in os.listdir(src_dir)
             if _DT_DIR.match(d) and os.path.isdir(os.path.join(src_dir, d, INNER))]
    if not cands:
        raise FileNotFoundError(f'No pedestal run with a {INNER}/ subdir found in {src_dir}')
    return max(cands, key=os.path.getmtime)


def flatten_thr(prg_path, value):
    mgr = DreamThresholdManager()
    mgr.read_prg(prg_path)
    for dream_id in range(8):
        for channel in range(64):
            mgr.set_threshold(dream_id, channel, value)
    mgr.write_prg(prg_path)


def main():
    src_run = latest_source_run(SRC_PED_DIR)
    src_prg_dir = os.path.join(src_run, INNER)
    prg_files = [f for f in os.listdir(src_prg_dir) if f.endswith('.prg')]
    thr_files = [f for f in prg_files if f.endswith('_thr.prg')]
    ped_files = [f for f in prg_files if f.endswith('_ped.prg')]
    print(f'Source pedestal run: {src_run}')
    print(f'  {len(ped_files)} _ped.prg + {len(thr_files)} _thr.prg files')

    for thr in THRESHOLDS:
        dest_prg_dir = os.path.join(OUT_BASE, f'pedestals_flat{thr}', INNER)
        if os.path.exists(dest_prg_dir):
            shutil.rmtree(dest_prg_dir)
        os.makedirs(dest_prg_dir)
        for f in prg_files:
            shutil.copy(os.path.join(src_prg_dir, f), os.path.join(dest_prg_dir, f))
        for f in thr_files:
            flatten_thr(os.path.join(dest_prg_dir, f), thr)
        print(f'  pedestals_flat{thr}: {len(thr_files)} _thr.prg flattened to {thr}, '
              f'{len(ped_files)} _ped.prg kept')

    print('donzo')


if __name__ == '__main__':
    main()
