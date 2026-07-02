#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Autonomous pedestal QA watcher for nTof DREAM DAQ.

Watches the top-level pedestals directory for finished pedestal runs
(pedestals_<datetime>/ dirs written by run_config_pedestals.py) and runs
scripts/pedestal_strip_check.py on each new one, producing PNGs + summary.json
+ PDF in <run_dir>/<output_inner_dir>/ for the flask GUI's Pedestals tab.
Runs independently of daq_control.py; start/stop from the flask UI.

Usage:
    python pedestal_watcher.py <pedestal_qa_config_json_path>

Config keys (see pedestal_qa_config.py to generate the JSON):
  pedestals_dir    : top-level directory containing pedestals_<datetime>/ dirs
  analysis_python  : python interpreter with uproot/numpy/matplotlib
  decode_exe       : C++ decode executable from mm_strip_reconstruction
  output_inner_dir : subdir of each run dir for QA output (default: 'ped_qa')
  poll_interval    : seconds between scans (default: 10)
  quiet_sec        : run considered finished when no file in its subrun dir
                     changed for this many seconds (default: 60)
  memory_kill_pct  : kill the analysis if system RAM exceeds this % (default: 80)

A run is (re)analyzed whenever its set of _pedthr_ FDFs differs from what was
last analyzed (tracked in config/pedestal_qa_state.json).
"""

import os
import sys
import json
import time
import signal
import datetime
import subprocess
from pathlib import Path

_LOG_FILE   = Path(__file__).parent / 'logs' / 'pedestal_watcher.log'
_PED_SCRIPT = Path(__file__).parent / 'scripts' / 'pedestal_strip_check.py'


def _log(event: str, **details):
    try:
        _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        ts         = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        detail_str = ' | '.join(f'{k}={v}' for k, v in details.items())
        line       = f"{ts} | {event:<16} | ped_watcher  | {detail_str}\n"
        with open(_LOG_FILE, 'a') as f:
            f.write(line)
    except Exception as e:
        print(f"[ped_watcher] Warning: could not write to log: {e}")


def main():
    if len(sys.argv) != 2:
        print("Usage: python pedestal_watcher.py <pedestal_qa_config_json_path>")
        sys.exit(1)

    config_path = Path(sys.argv[1])
    with open(config_path) as f:
        config = json.load(f)

    state_path = config_path.parent / 'pedestal_qa_state.json'
    run_watcher(config, state_path)


# ---------------------------------------------------------------------------
# Main watcher loop
# ---------------------------------------------------------------------------

def run_watcher(config: dict, state_path: Path = None):
    pedestals_dir   = Path(config['pedestals_dir'])
    analysis_python = config['analysis_python']
    decode_exe      = config['decode_exe']
    inner_dir       = config.get('output_inner_dir', 'ped_qa')
    poll_interval   = config.get('poll_interval',  10)
    quiet_sec       = config.get('quiet_sec',      60)
    memory_kill_pct = config.get('memory_kill_pct', 80)

    print(f"[ped_watcher] pedestals_dir   : {pedestals_dir}")
    print(f"[ped_watcher] analysis_python : {analysis_python}")
    print(f"[ped_watcher] decode_exe      : {decode_exe}")
    print(f"[ped_watcher] output_inner    : {inner_dir}")
    print(f"[ped_watcher] poll={poll_interval}s  quiet={quiet_sec}s  mem_kill={memory_kill_pct}%")
    _log('START', pedestals_dir=pedestals_dir, memory_kill_pct=f'{memory_kill_pct}%')

    done: dict = _load_state(state_path)  # run_name -> sorted list of pedthr fdf names

    # Retry failed runs with exponential backoff (60 s, 2 min, ... capped at
    # 1 h) instead of hammering a permanently broken run every poll.
    failures:   dict = {}  # run_name -> consecutive failure count
    next_retry: dict = {}  # run_name -> epoch time before which we skip it

    idle_ticks = 0
    idle_line = False
    _SPINNER = ['-', '\\', '|', '/']

    def _end_idle():
        nonlocal idle_line
        if idle_line:
            sys.stdout.write('\n')
            sys.stdout.flush()
            idle_line = False

    while True:
        found_new = False

        if pedestals_dir.exists():
            for run_dir in sorted(pedestals_dir.iterdir()):
                if not run_dir.is_dir() or run_dir.name == inner_dir:
                    continue

                data_dir = _find_pedthr_dir(run_dir, inner_dir)
                if data_dir is None:
                    continue

                pedthr = sorted(f.name for f in data_dir.iterdir()
                                if f.is_file() and f.suffix == '.fdf' and '_pedthr_' in f.name)
                if pedthr == done.get(run_dir.name):
                    continue
                if time.time() < next_retry.get(run_dir.name, 0):
                    continue  # backing off after failures
                if not _dir_is_quiet(data_dir, quiet_sec):
                    continue  # run still being written / copied

                _end_idle()
                mem_pct, free_mb = _mem_usage_pct()
                print(f"[ped_watcher] {run_dir.name}  n_pedthr={len(pedthr)}"
                      f"  mem={mem_pct:.1f}%  free={free_mb:.0f}MB")
                _log('PED_QA_LAUNCH', run=run_dir.name, n_pedthr=len(pedthr),
                     mem_pct=f'{mem_pct:.1f}%')

                out_dir = run_dir / inner_dir
                completed_ok = _run_analysis_monitored(
                    analysis_python, data_dir, out_dir, decode_exe,
                    memory_kill_pct=memory_kill_pct)

                if completed_ok:
                    done[run_dir.name] = pedthr
                    failures.pop(run_dir.name, None)
                    next_retry.pop(run_dir.name, None)
                    _save_state(state_path, done)
                    print(f"[ped_watcher] {run_dir.name} done")
                    _log('PED_QA_DONE', run=run_dir.name)
                else:
                    # Remove decoded ROOTs so a retry doesn't reuse a file
                    # truncated by the kill; FDFs are untouched.
                    _clean_roots(data_dir)
                    n = failures.get(run_dir.name, 0) + 1
                    failures[run_dir.name] = n
                    delay = min(3600, 60 * 2 ** (n - 1))
                    next_retry[run_dir.name] = time.time() + delay
                    print(f"[ped_watcher] {run_dir.name} failed "
                          f"(attempt {n}), retry in {delay}s")
                    _log('PED_QA_FAILED', run=run_dir.name, attempt=n,
                         retry_in=f'{delay}s')
                found_new = True

        if found_new:
            idle_ticks = 0
        else:
            idle_ticks += 1
            elapsed = idle_ticks * poll_interval
            ts = datetime.datetime.now().strftime('%H:%M:%S')
            sp = _SPINNER[idle_ticks % 4]
            if not pedestals_dir.exists():
                msg = f'[ped_watcher] {sp} waiting for pedestals_dir  #{idle_ticks}  {ts}'
            else:
                msg = f'[ped_watcher] {sp} idle  #{idle_ticks}  {elapsed}s  {ts}'
            sys.stdout.write(f'\r{msg}          ')
            sys.stdout.flush()
            idle_line = True
        time.sleep(poll_interval)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_pedthr_dir(run_dir: Path, inner_dir: str):
    """
    Return the directory holding the run's _pedthr_ FDFs: the run dir itself or
    one of its direct subdirs (the 'pedestals' subrun). Skips the QA output dir
    and raw_daq_data (on-the-fly copies). Newest pedthr wins if several match.
    """
    candidates = [run_dir] + [d for d in run_dir.iterdir()
                              if d.is_dir() and d.name not in (inner_dir, 'raw_daq_data')]
    best, best_mtime = None, -1.0
    for d in candidates:
        try:
            fdfs = [f for f in d.iterdir()
                    if f.is_file() and f.suffix == '.fdf' and '_pedthr_' in f.name]
        except OSError:
            continue
        if not fdfs:
            continue
        newest = max(f.stat().st_mtime for f in fdfs)
        if newest > best_mtime:
            best, best_mtime = d, newest
    return best


def _dir_is_quiet(data_dir: Path, quiet_sec: float) -> bool:
    """True if no direct-child file of data_dir was modified in the last quiet_sec."""
    cutoff = time.time() - quiet_sec
    try:
        for f in data_dir.iterdir():
            if f.is_file() and f.stat().st_mtime > cutoff:
                return False
    except OSError:
        return False
    return True


def _clean_roots(data_dir: Path):
    for f in data_dir.iterdir():
        if f.is_file() and f.suffix == '.root' and '_pedthr_' in f.name:
            try:
                f.unlink()
            except OSError:
                pass


def _load_state(state_path: Path) -> dict:
    if state_path is None or not state_path.exists():
        return {}
    try:
        with open(state_path) as f:
            return json.load(f)
    except Exception as e:
        print(f"[ped_watcher] Could not load state from {state_path}: {e}")
        return {}


def _save_state(state_path: Path, done: dict):
    if state_path is None:
        return
    try:
        with open(state_path, 'w') as f:
            json.dump(done, f, indent=2)
    except Exception as e:
        print(f"[ped_watcher] Could not save state to {state_path}: {e}")


def _read_meminfo() -> tuple:
    total, avail = 0, 0
    try:
        with open('/proc/meminfo') as f:
            for line in f:
                if line.startswith('MemTotal:'):
                    total = int(line.split()[1])
                elif line.startswith('MemAvailable:'):
                    avail = int(line.split()[1])
                if total and avail:
                    break
    except Exception:
        pass
    return total, avail


def _mem_usage_pct() -> tuple:
    total, avail = _read_meminfo()
    if total == 0:
        return 0.0, float('inf')
    used_pct = (total - avail) / total * 100
    free_mb  = avail / 1024
    return used_pct, free_mb


def _run_analysis_monitored(analysis_python: str, data_dir: Path, out_dir: Path,
                            decode_exe: str, memory_kill_pct: float = 80,
                            monitor_interval: float = 1.0) -> bool:
    """
    Launch pedestal_strip_check.py as a subprocess and monitor system RAM.
    Same kill behavior as qa_watcher (SIGTERM at the threshold, SIGKILL after
    5 s) but signalling the whole process group: the analysis spawns C++
    decode children that must not survive the kill.
    Returns True if the process completed with exit code 0.
    """
    cmd = [analysis_python, str(_PED_SCRIPT), str(data_dir), str(out_dir),
           '--decode-exe', decode_exe]
    try:
        proc = subprocess.Popen(cmd, start_new_session=True)
    except OSError as e:
        print(f"\n[ped_watcher] Cannot launch analysis: {e}")
        _log('PED_QA_LAUNCH_ERR', run=data_dir.parent.name, error=e)
        return False

    def _kill_group(sig):
        try:
            os.killpg(proc.pid, sig)  # start_new_session -> pgid == pid
        except ProcessLookupError:
            pass

    while proc.poll() is None:
        time.sleep(monitor_interval)
        mem_pct, free_mb = _mem_usage_pct()
        if mem_pct >= memory_kill_pct:
            print(f"\n[ped_watcher] Memory {mem_pct:.1f}% >= {memory_kill_pct}%"
                  f" — killing analysis ({data_dir.parent.name})")
            _log('PED_QA_KILLED', run=data_dir.parent.name,
                 mem_pct=f'{mem_pct:.1f}%', threshold=f'{memory_kill_pct}%')
            _kill_group(signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _kill_group(signal.SIGKILL)
                proc.wait()
            return False

    return proc.returncode == 0


if __name__ == '__main__':
    main()
