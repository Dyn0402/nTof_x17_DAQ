#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Autonomous on-the-fly QA watcher for nTof DREAM DAQ data.

Watches all runs under a top-level runs directory and runs the QA analysis
script whenever new combined_hits files appear.  Runs independently of
daq_control.py and processor_watcher.py; start/stop from the flask UI.

Usage:
    python qa_watcher.py <qa_config_json_path>

Config keys (see qa_config.py to generate the JSON):
  runs_dir                : top-level directory containing run_N/ subdirs
  ntof_x17_dir            : path to the nTof_x17 repository
  combined_hits_inner_dir : subdir for combined hits files  (default: 'combined_hits_root')
  qa_file_mode            : 'all' | 'first' | 'per_file'   (default: 'all')
                              all      — rerun QA on all accumulated files whenever a new one appears
                              first    — run QA once per subrun using only file_num=0
                              per_file — independent QA output per file_num
  include_runs            : list of run directory names to process exclusively (null = all)
  exclude_runs            : list of run directory names to skip (null = none)
  poll_interval           : seconds between scans   (default: 10)
  stale_run_days          : runs with no new combined_hits for this many days are skipped (default: 4)
"""

import re
import sys  # used for argv in main()
import json
import time
import subprocess
from pathlib import Path


def main():
    if len(sys.argv) != 2:
        print("Usage: python qa_watcher.py <qa_config_json_path>")
        sys.exit(1)

    config_path = Path(sys.argv[1])
    with open(config_path) as f:
        config = json.load(f)

    reset_signal_path = config_path.parent / 'qa_reset.json'
    run_watcher(config, reset_signal_path)


# ---------------------------------------------------------------------------
# Main watcher loop
# ---------------------------------------------------------------------------

def run_watcher(config: dict, reset_signal_path: Path = None):
    runs_dir       = Path(config['runs_dir'])
    ntof_x17_dir   = Path(config['ntof_x17_dir'])
    combined_inner = config.get('combined_hits_inner_dir', 'combined_hits_root')
    mode           = config.get('qa_file_mode', 'all')

    include_runs = set(config['include_runs']) if config.get('include_runs') else None
    exclude_runs = set(config['exclude_runs']) if config.get('exclude_runs') else set()

    poll_interval  = config.get('poll_interval',  10)
    stale_run_days = config.get('stale_run_days',  4)

    qa_script  = ntof_x17_dir / 'ntof_daq_analysis' / 'detector_qa.py'
    qa_python  = ntof_x17_dir / '.venv' / 'bin' / 'python'

    print(f"[qa_watcher] runs_dir     : {runs_dir}")
    print(f"[qa_watcher] qa_script    : {qa_script}")
    print(f"[qa_watcher] python       : {qa_python}")
    print(f"[qa_watcher] mode         : {mode}")
    if include_runs:
        print(f"[qa_watcher] include_runs : {sorted(include_runs)}")
    if exclude_runs:
        print(f"[qa_watcher] exclude_runs : {sorted(exclude_runs)}")
    print(f"[qa_watcher] poll         : {poll_interval}s  stale_after={stale_run_days}d")

    state_path = reset_signal_path.parent / 'qa_state.json' if reset_signal_path else None

    checked_stale_runs: set = set()

    # Per-mode tracking state, keyed by (run_name, subrun_name)
    seen_files:  dict = _load_state(state_path)  # 'all' mode: frozenset of filenames at last QA run
    done_first:  set  = set()  # 'first' mode: subruns already processed
    done_fnums:  dict = {}  # 'per_file' mode: set of completed file_nums

    while True:
        found_new = False

        if reset_signal_path:
            reset = _pop_reset_signal(reset_signal_path)
            if reset is not False:
                if reset is None:
                    seen_files.clear()
                    done_first.clear()
                    done_fnums.clear()
                    checked_stale_runs.clear()
                    _save_state(state_path, seen_files)
                    print("[qa_watcher] Reset: all runs will be reprocessed")
                else:
                    for key in list(seen_files):
                        if key[0] in reset: del seen_files[key]
                    done_first -= {k for k in done_first if k[0] in reset}
                    for key in list(done_fnums):
                        if key[0] in reset: del done_fnums[key]
                    checked_stale_runs -= reset
                    _save_state(state_path, seen_files)
                    print(f"[qa_watcher] Reset: {sorted(reset)} will be reprocessed")

        if not runs_dir.exists():
            print(f"[qa_watcher] Waiting for runs_dir: {runs_dir}")
        else:
            for run_dir in sorted(runs_dir.iterdir()):
                if not run_dir.is_dir():
                    continue
                if include_runs is not None and run_dir.name not in include_runs:
                    continue
                if run_dir.name in exclude_runs:
                    continue
                if run_dir.name in checked_stale_runs:
                    continue

                run_config_path = run_dir / 'run_config.json'
                if not run_config_path.exists():
                    continue

                is_stale = _run_is_stale(run_dir, combined_inner, stale_run_days)

                for subrun_dir in sorted(run_dir.iterdir()):
                    if not subrun_dir.is_dir():
                        continue

                    combined_dir = subrun_dir / combined_inner
                    if not combined_dir.exists():
                        continue

                    stable = _stable_combined_files(combined_dir)
                    if not stable:
                        continue

                    key = (run_dir.name, subrun_dir.name)

                    if mode == 'all':
                        current = frozenset(stable)
                        if current != seen_files.get(key):
                            print(f"\n[qa_watcher] {run_dir.name}/{subrun_dir.name}"
                                  f"  n_files={len(stable)}")
                            _run_qa(qa_python, qa_script, subrun_dir, run_config_path, 'all')
                            seen_files[key] = current
                            _save_state(state_path, seen_files)
                            found_new = True

                    elif mode == 'first':
                        if key not in done_first:
                            if any(_file_num(f) == 0 for f in stable):
                                print(f"\n[qa_watcher] {run_dir.name}/{subrun_dir.name}"
                                      f"  file_num=0")
                                _run_qa(qa_python, qa_script, subrun_dir, run_config_path, 'first')
                                done_first.add(key)
                                found_new = True

                    elif mode == 'per_file':
                        completed = done_fnums.get(key, set())
                        new_fnums = {_file_num(f) for f in stable} - {None} - completed
                        for fnum in sorted(new_fnums):
                            print(f"\n[qa_watcher] {run_dir.name}/{subrun_dir.name}"
                                  f"  file_num={fnum:03d}")
                            _run_qa(qa_python, qa_script, subrun_dir, run_config_path, 'per_file', fnum)
                            completed.add(fnum)
                            found_new = True
                        done_fnums[key] = completed

                if is_stale:
                    checked_stale_runs.add(run_dir.name)
                    print(f"[qa_watcher] Marked stale (will skip): {run_dir.name}")

        if not found_new:
            print(f"[qa_watcher] Sleeping {poll_interval}s...")
        time.sleep(poll_interval)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_state(state_path: Path) -> dict:
    if state_path is None or not state_path.exists():
        return {}
    try:
        with open(state_path) as f:
            raw = json.load(f)
        return {tuple(k.split('/', 1)): frozenset(v) for k, v in raw.items()}
    except Exception as e:
        print(f"[qa_watcher] Could not load state from {state_path}: {e}")
        return {}


def _save_state(state_path: Path, seen_files: dict):
    if state_path is None:
        return
    try:
        raw = {f"{k[0]}/{k[1]}": sorted(v) for k, v in seen_files.items()}
        with open(state_path, 'w') as f:
            json.dump(raw, f, indent=2)
    except Exception as e:
        print(f"[qa_watcher] Could not save state to {state_path}: {e}")


def _pop_reset_signal(signal_path: Path):
    """
    Check for a reset signal file.
    Returns False  — no file present (no reset needed).
    Returns None   — reset all runs.
    Returns set    — reset only the named runs.
    """
    if not signal_path.exists():
        return False
    try:
        with open(signal_path) as f:
            data = json.load(f)
        signal_path.unlink()
        runs = data.get('runs')
        return set(runs) if runs else None
    except Exception as e:
        print(f"[qa_watcher] Error reading reset signal: {e}")
        try:
            signal_path.unlink()
        except OSError:
            pass
        return False


def _run_qa(qa_python: Path, qa_script: Path, subrun_dir: Path, run_config_path: Path,
             mode: str, file_num: int = None):
    cmd = [str(qa_python), str(qa_script),
           '--subrun_dir', str(subrun_dir),
           '--run_config', str(run_config_path),
           '--mode', mode]
    if file_num is not None:
        cmd += ['--file_num', str(file_num)]
    subprocess.run(cmd)


def _stable_combined_files(combined_dir: Path) -> list:
    """Return sorted filenames of feu-combined ROOT files with size > 0."""
    result = []
    for f in combined_dir.iterdir():
        if f.suffix != '.root' or '_datrun_' not in f.name or 'feu-combined' not in f.name:
            continue
        try:
            if f.stat().st_size > 0:
                result.append(f.name)
        except OSError:
            continue
    return sorted(result)


def _file_num(filename: str):
    m = re.match(r'.*_(\d{3})_feu-combined', filename)
    return int(m.group(1)) if m else None


def _run_is_stale(run_dir: Path, combined_inner: str, stale_days: float) -> bool:
    cutoff = time.time() - stale_days * 86400
    newest = 0.0
    found_any = False
    for subrun in run_dir.iterdir():
        if not subrun.is_dir():
            continue
        d = subrun / combined_inner
        if d.exists():
            found_any = True
            mtime = d.stat().st_mtime
            if mtime > newest:
                newest = mtime
    if not found_any:
        return False  # No combined_hits yet — run is new, not stale
    return newest < cutoff


if __name__ == '__main__':
    main()
