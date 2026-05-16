#!/usr/bin/env python3
"""
Test script for verifying RunCtrl batch-mode (-b) reliability.

Starts RunCtrl in a temp test directory multiple times.  For each iteration:
  - Prints iteration info to the terminal.
  - Runs RunCtrl exactly as dream_daq_control.py does (subprocess.call, no stdin redirect).
  - Optionally also tests with stdin=DEVNULL to compare behaviour.
  - After RunCtrl exits, records how long it took and whether you had to type anything.

Usage:
  python test_runctrl_batch.py [--n N] [--devnull] [--test-dir DIR]

  --n N          Number of iterations (default 5)
  --devnull      Also run each iteration with stdin=DEVNULL and compare
  --test-dir DIR Temp directory to cd into for each run (default /tmp/runctrl_test)

Workflow per iteration:
  1. cd to test dir
  2. subprocess.call(['RunCtrl', '-c', <cfg>, '-f', <name>, '-b'])
     RunCtrl takes over the terminal.  If batch mode works it should start without
     waiting for 'G'.  Press 'g' in the terminal once you see it running to stop it.
  3. Script prints how long it ran and loops to the next iteration.

After all iterations the script prints a summary so you can spot intermittent failures.
"""

import os
import sys
import subprocess
import tempfile
import time
import argparse
import shutil

TEST_CFG_NAME = 'test_dream.cfg'


def make_test_dir(base_dir):
    os.makedirs(base_dir, exist_ok=True)
    return base_dir


def make_dummy_cfg(test_dir):
    """Create a minimal stub config so RunCtrl has something to open."""
    cfg_path = os.path.join(test_dir, TEST_CFG_NAME)
    if not os.path.exists(cfg_path):
        with open(cfg_path, 'w') as f:
            f.write('# Dummy dream config for batch-mode test\n')
            f.write('Sys DaqRun Time 60\n')
            f.write('Sys DaqRun Mode Raw\n')
    return cfg_path


def run_once(cfg_path, run_name, use_devnull=False, label=''):
    cmd = ['RunCtrl', '-c', cfg_path, '-f', run_name, '-b']
    stdin_arg = subprocess.DEVNULL if use_devnull else None  # None = inherit terminal

    stdin_desc = 'stdin=DEVNULL' if use_devnull else 'stdin=inherited (as in dream_daq_control.py)'
    print(f'\n{"="*60}')
    print(f'{label}')
    print(f'Command : {" ".join(cmd)}')
    print(f'Stdin   : {stdin_desc}')
    print(f'CWD     : {os.getcwd()}')
    print(f'{"="*60}')
    if use_devnull:
        print('NOTE: stdin is /dev/null — RunCtrl cannot read from terminal.')
        print('      Batch mode MUST handle startup on its own.')
    else:
        print('NOTE: RunCtrl shares this terminal.')
        print('      In batch mode it should start automatically.')
        print('      If it waits for "G" — that is the bug.')
        print('      Press "g" once you see it running to stop it.')
    print()

    t0 = time.time()
    ret = subprocess.call(cmd, stdin=stdin_arg)
    elapsed = time.time() - t0

    print(f'\nRunCtrl exited: return code={ret}, elapsed={elapsed:.1f}s')
    return ret, elapsed


def main():
    parser = argparse.ArgumentParser(description='Test RunCtrl batch-mode reliability')
    parser.add_argument('--n', type=int, default=5, help='Number of iterations')
    parser.add_argument('--devnull', action='store_true',
                        help='Also run with stdin=DEVNULL after each normal run')
    parser.add_argument('--test-dir', default='/tmp/runctrl_test',
                        help='Working directory for RunCtrl (default /tmp/runctrl_test)')
    args = parser.parse_args()

    test_dir = args.test_dir
    make_test_dir(test_dir)
    cfg_path = make_dummy_cfg(test_dir)

    original_dir = os.getcwd()
    os.chdir(test_dir)

    print(f'Test directory : {test_dir}')
    print(f'Config file    : {cfg_path}')
    print(f'Iterations     : {args.n}')
    print(f'RunCtrl binary : {shutil.which("RunCtrl") or "not found on PATH"}')
    print()

    results = []

    for i in range(1, args.n + 1):
        run_name = f'test_batch_{i:03d}'

        # --- Normal run (inherited stdin, same as dream_daq_control.py) ---
        ret, elapsed = run_once(cfg_path, run_name,
                                use_devnull=False,
                                label=f'Iteration {i}/{args.n} — inherited stdin')
        results.append({'iter': i, 'devnull': False, 'ret': ret, 'elapsed': elapsed})

        if args.devnull:
            # --- Repeat with DEVNULL stdin ---
            run_name_dn = f'test_devnull_{i:03d}'
            ret_dn, elapsed_dn = run_once(cfg_path, run_name_dn,
                                          use_devnull=True,
                                          label=f'Iteration {i}/{args.n} — stdin=DEVNULL')
            results.append({'iter': i, 'devnull': True, 'ret': ret_dn, 'elapsed': elapsed_dn})

        if i < args.n:
            print(f'\nReady for iteration {i+1}. Press Enter to continue...')
            input()

    os.chdir(original_dir)

    print('\n' + '='*60)
    print('SUMMARY')
    print('='*60)
    print(f'{"Iter":>5}  {"Stdin":>16}  {"RC":>4}  {"Elapsed":>9}')
    print('-'*60)
    for r in results:
        stdin_label = 'DEVNULL' if r['devnull'] else 'inherited'
        print(f'{r["iter"]:>5}  {stdin_label:>16}  {r["ret"]:>4}  {r["elapsed"]:>8.1f}s')

    short_runs = [r for r in results if not r['devnull'] and r['elapsed'] < 3]
    long_runs  = [r for r in results if not r['devnull'] and r['elapsed'] > 60]
    print()
    if short_runs:
        print(f'WARNING: {len(short_runs)} run(s) finished in <3s — RunCtrl may have crashed or '
              f'got EOF immediately.')
    if long_runs:
        print(f'NOTE: {len(long_runs)} run(s) took >60s — user likely had to type "G" (the bug).')
    print('Done.')


if __name__ == '__main__':
    main()