#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Autonomous on-the-fly processor watcher for nTof DREAM DAQ data.

Watches all runs under a top-level runs directory and runs the
decode → analyze_waveforms → combine_feus_hits pipeline
on each new FDF file group as it becomes available.  Runs completely
independently of daq_control.py; start/stop from the flask UI or command line.

Usage:
    python processor_watcher.py <processor_config_json_path>

Config keys (see processor_config.py to generate the JSON):
  runs_dir                : top-level directory containing run_N/ subdirs
  raw_daq_inner_dir       : subdir name for raw FDF files          (default: 'raw_daq_data')
  decoded_root_inner_dir  : subdir name for decoded ROOT files      (default: 'decoded_root')
  hits_inner_dir          : subdir name for per-FEU hit files       (default: 'hits_root')
  combined_hits_inner_dir : subdir name for combined hits           (default: 'combined_hits_root')

  decode_executable       : path to the 'decode' binary
  analyze_executable      : path to the 'analyze_waveforms' binary  (optional)
  combine_executable      : path to the 'combine_feus_hits' binary  (optional)

  do_decode               : run decode step              (default: true)
  do_analyze              : run waveform analysis         (default: false)
  do_combine              : combine per-FEU hits          (default: false)

  save_fdfs               : keep FDF files after processing         (default: true)
  save_decoded            : keep decoded ROOT files after analysis  (default: true)

  pedestal_loc            : 'same' | 'abs' | 'find'
  pedestal_dir            : base pedestal dir (for 'abs' or 'find' modes)
                            'find' mode: expects <pedestal_dir>/<run_name>/pedestals/
                                         with pedestal_run.txt in raw_daq_data/

  include_runs            : list of run directory names to process exclusively (null = all)
  exclude_runs            : list of run directory names to skip (null = none skipped)
  poll_interval           : seconds between full directory scans    (default: 10)
  stale_run_days          : runs with no new FDFs for this many days are skipped  (default: 4)
  free_threads            : CPU threads to leave free during processing (default: 2)
"""

import os
import sys
import json
import re
import time
import datetime
import tempfile
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common_functions import create_dir_if_not_exist


def main():
    if len(sys.argv) != 2:
        print("Usage: python processor_watcher.py <processor_config_json_path>")
        sys.exit(1)

    with open(sys.argv[1]) as f:
        config = json.load(f)

    run_watcher(config)


# ---------------------------------------------------------------------------
# Main watcher loop
# ---------------------------------------------------------------------------

def run_watcher(config: dict):
    runs_dir       = Path(config['runs_dir'])
    raw_inner      = config.get('raw_daq_inner_dir',        'raw_daq_data')
    decoded_inner  = config.get('decoded_root_inner_dir',   'decoded_root')
    hits_inner     = config.get('hits_inner_dir',           'hits_root')
    combined_inner = config.get('combined_hits_inner_dir',  'combined_hits_root')

    decode_exe  = config.get('decode_executable',  '')
    analyze_exe = config.get('analyze_executable', '')
    combine_exe = config.get('combine_executable', '')

    do_decode  = config.get('do_decode',  True)  and bool(decode_exe)
    do_analyze = config.get('do_analyze', False) and bool(analyze_exe)
    do_combine = config.get('do_combine', False) and bool(combine_exe)
    common_noise_subtraction = config.get('common_noise_subtraction', True)

    save_fdfs    = config.get('save_fdfs',    True)
    save_decoded = config.get('save_decoded', True)

    pedestal_loc      = config.get('pedestal_loc', 'same')
    pedestal_base_dir = config.get('pedestal_dir', '') or ''

    include_runs = set(config['include_runs']) if config.get('include_runs') else None
    exclude_runs = set(config['exclude_runs']) if config.get('exclude_runs') else set()

    poll_interval  = config.get('poll_interval',  10)
    stale_run_days = config.get('stale_run_days',  4)
    free_threads   = config.get('free_threads',    2)
    n_threads      = max(1, (os.cpu_count() or 1) - free_threads)

    print(f"[watcher] runs_dir      : {runs_dir}")
    if include_runs:
        print(f"[watcher] include_runs  : {sorted(include_runs)}")
    if exclude_runs:
        print(f"[watcher] exclude_runs  : {sorted(exclude_runs)}")
    print(f"[watcher] pipeline      : decode={do_decode}  analyze={do_analyze}  combine={do_combine}")
    print(f"[watcher] threads       : {n_threads}  poll={poll_interval}s  stale_after={stale_run_days}d")
    print(f"[watcher] pedestal      : loc={pedestal_loc}  base={pedestal_base_dir or '(same as raw)'}")

    checked_stale_runs: set = set()
    prev_sizes: dict = {}
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

        if not runs_dir.exists():
            pass
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

                is_stale = _run_is_stale(run_dir, raw_inner, stale_run_days)

                sample_period = _read_sample_period(run_dir)

                for subrun_dir in sorted(run_dir.iterdir()):
                    if not subrun_dir.is_dir():
                        continue

                    raw_dir = subrun_dir / raw_inner
                    if not raw_dir.exists():
                        continue

                    ped_dir = _resolve_pedestal_dir(raw_dir, pedestal_loc, pedestal_base_dir)

                    if do_decode and ped_dir:
                        _decode_pedestals(ped_dir, decode_exe)

                    all_fnums  = _get_data_file_nums(raw_dir)
                    done_fnums = _get_processed_file_nums(
                        subrun_dir, combined_inner, hits_inner, decoded_inner,
                        do_combine, do_analyze
                    )

                    for fnum in sorted(all_fnums - done_fnums):
                        all_fdf_group = [
                            raw_dir / f for f in os.listdir(raw_dir)
                            if _is_data_fdf(f) and _extract_file_num(f) == fnum
                        ]
                        if not all_fdf_group:
                            continue

                        key = (run_dir.name, subrun_dir.name, fnum)
                        current = {p.name: p.stat().st_size for p in all_fdf_group if p.exists()}
                        if not current or any(s == 0 for s in current.values()):
                            prev_sizes[key] = current
                            continue

                        if prev_sizes.get(key) == current:
                            _end_idle()
                            print(f"[watcher] {run_dir.name}/{subrun_dir.name}  "
                                  f"file_num={fnum:03d}  ({len(all_fdf_group)} FEU(s))")
                            _process_file_num(
                                fnum, all_fdf_group, subrun_dir, ped_dir,
                                decoded_inner, hits_inner, combined_inner,
                                decode_exe, analyze_exe, combine_exe,
                                do_decode, do_analyze, do_combine,
                                save_fdfs, save_decoded, n_threads,
                                sample_period, common_noise_subtraction
                            )
                            del prev_sizes[key]
                            found_new = True
                        else:
                            prev_sizes[key] = current

                if is_stale:
                    checked_stale_runs.add(run_dir.name)
                    _end_idle()
                    print(f"[watcher] Marked stale (will skip): {run_dir.name}")

        if found_new:
            idle_ticks = 0
        else:
            idle_ticks += 1
            elapsed = idle_ticks * poll_interval
            ts = datetime.datetime.now().strftime('%H:%M:%S')
            sp = _SPINNER[idle_ticks % 4]
            if not runs_dir.exists():
                msg = f'[watcher] {sp} waiting for runs_dir  #{idle_ticks}  {ts}'
            else:
                msg = f'[watcher] {sp} idle  #{idle_ticks}  {elapsed}s  {ts}'
            sys.stdout.write(f'\r{msg}          ')
            sys.stdout.flush()
            idle_line = True
        time.sleep(poll_interval)


# ---------------------------------------------------------------------------
# Pipeline orchestration
# ---------------------------------------------------------------------------

def _process_file_num(fnum, all_fdf_paths, subrun_dir, ped_dir,
                       decoded_inner, hits_inner, combined_inner,
                       decode_exe, analyze_exe, combine_exe,
                       do_decode, do_analyze, do_combine,
                       save_fdfs, save_decoded, n_threads,
                       sample_period=None, common_noise_subtraction=True):

    decoded_dir  = subrun_dir / decoded_inner
    hits_dir     = subrun_dir / hits_inner
    combined_dir = subrun_dir / combined_inner

    # Step 1: Decode FDFs
    if do_decode:
        create_dir_if_not_exist(str(decoded_dir))
        tasks = []
        with ThreadPoolExecutor(max_workers=n_threads) as pool:
            for fdf in all_fdf_paths:
                root_path = decoded_dir / fdf.name.replace('.fdf', '.root')
                if root_path.exists():
                    continue
                tasks.append(pool.submit(_decode_file, str(fdf), str(root_path), decode_exe))
            for t in as_completed(tasks):
                t.result()

    # Step 2: Analyze waveforms
    if do_analyze:
        create_dir_if_not_exist(str(hits_dir))
        source_roots = [
            f for f in decoded_dir.glob('*.root')
            if '_datrun_' in f.name and '_pedestals_' not in f.name
            and _extract_file_num(f.name) == fnum
        ] if decoded_dir.exists() else []

        tasks = []
        with ThreadPoolExecutor(max_workers=n_threads) as pool:
            for root_path in source_roots:
                hits_path = hits_dir / root_path.name.replace('.root', '_hits.root')
                if hits_path.exists():
                    continue
                tasks.append(pool.submit(
                    _analyze_file, str(root_path), ped_dir, str(hits_path), analyze_exe, sample_period, common_noise_subtraction
                ))
            for t in as_completed(tasks):
                t.result()

    # Step 3: Combine hits
    if do_combine:
        create_dir_if_not_exist(str(combined_dir))
        feu_hits_map = _get_feu_hits_map(hits_dir, fnum)
        if feu_hits_map:
            combined_name = _make_combined_name(next(iter(feu_hits_map.values())))
            combined_path = combined_dir / combined_name
            if not combined_path.exists():
                _combine_hits(feu_hits_map, str(combined_path), combine_exe)

    # Step 4: Cleanup
    if not save_fdfs:
        for fdf in all_fdf_paths:
            if fdf.exists():
                fdf.unlink()
                print(f"[cleanup] Removed {fdf.name}")
    if not save_decoded and decoded_dir.exists():
        for f in decoded_dir.glob('*.root'):
            if '_datrun_' in f.name and _extract_file_num(f.name) == fnum:
                f.unlink()
                print(f"[cleanup] Removed {f.name}")


def _decode_pedestals(ped_dir: str, decode_exe: str):
    """Decode pedestal FDFs in ped_dir in-place, skipping already-decoded ones."""
    ped_path = Path(ped_dir)
    if not ped_path.exists():
        return
    for fdf in ped_path.iterdir():
        if '_pedthr_' not in fdf.name or fdf.suffix != '.fdf':
            continue
        root_out = fdf.with_suffix('.root')
        if root_out.exists():
            continue
        print(f"[watcher] Decoding pedestal: {fdf.name}")
        _decode_file(str(fdf), str(root_out), decode_exe)


# ---------------------------------------------------------------------------
# Stale-run detection
# ---------------------------------------------------------------------------

def _run_is_stale(run_dir: Path, raw_inner: str, stale_days: float) -> bool:
    cutoff = time.time() - stale_days * 86400
    newest_mtime = None
    for subrun_dir in run_dir.iterdir():
        if not subrun_dir.is_dir():
            continue
        raw_dir = subrun_dir / raw_inner
        if raw_dir.exists():
            mtime = raw_dir.stat().st_mtime
            if newest_mtime is None or mtime > newest_mtime:
                newest_mtime = mtime
    if newest_mtime is None:
        return False  # no raw_daq_data dirs yet — run just started, not stale
    return newest_mtime < cutoff


# ---------------------------------------------------------------------------
# Directory / filename helpers
# ---------------------------------------------------------------------------

def _resolve_pedestal_dir(raw_dir: Path, pedestal_loc: str, pedestal_base_dir: str) -> str:
    if pedestal_loc == 'same':
        return str(raw_dir)
    if pedestal_loc == 'abs':
        return pedestal_base_dir
    if pedestal_loc == 'find':
        txt = raw_dir / 'pedestal_run.txt'
        if txt.exists():
            ped_run = txt.read_text().strip()
            return str(Path(pedestal_base_dir) / ped_run / 'pedestals')
        print(f"[watcher] pedestal_run.txt not found in {raw_dir}, skipping pedestal decode")
    return ''


def _is_data_fdf(name: str) -> bool:
    """True for real data datrun FDFs.

    Excludes the 0-byte ``Mx17_pedestals_datrun_*.fdf`` placeholders that the DAQ
    drops into every subrun's raw_daq_data: they share file_num 000 with the real
    scan datruns, so if they are grouped in, the ``any(size == 0)`` guard treats
    file_num 000 as "still being written" forever and the subrun is never decoded.
    """
    return name.endswith('.fdf') and '_datrun_' in name and '_pedestals_' not in name


def _get_data_file_nums(raw_dir: Path) -> set:
    nums = set()
    for f in raw_dir.iterdir():
        if _is_data_fdf(f.name):
            n = _extract_file_num(f.name)
            if n is not None:
                nums.add(n)
    return nums


def _get_processed_file_nums(subrun_dir, combined_inner, hits_inner, decoded_inner,
                              do_combine, do_analyze) -> set:
    """Return file_nums whose final pipeline output already exists."""
    if do_combine:
        check_dir, flag = subrun_dir / combined_inner, 'feu-combined'
    elif do_analyze:
        check_dir, flag = subrun_dir / hits_inner, '_hits'
    else:
        check_dir, flag = subrun_dir / decoded_inner, '.root'

    done = set()
    if not check_dir.exists():
        return done
    for f in check_dir.iterdir():
        # Skip pedestal reference outputs copied into the subrun: they carry
        # file_num 000 and would otherwise mark the real scan's file_num 000 as
        # "done", blocking its analyze/combine forever (mirrors _is_data_fdf).
        if flag not in f.name or '_pedestals_' in f.name:
            continue
        n = _extract_file_num(f.name)
        if n is not None:
            done.add(n)
    return done


def _get_feu_hits_map(hits_dir: Path, fnum: int) -> dict:
    """Return {feu_num: path_str} for all hits files matching fnum."""
    result = {}
    if not hits_dir.exists():
        return result
    for f in hits_dir.iterdir():
        if '_pedestals_' in f.name:   # never combine copied-in pedestal hits
            continue
        m = re.match(r'.*_(\d{3})_(\d{2})_hits\.root$', f.name)
        if m and int(m.group(1)) == fnum:
            result[int(m.group(2))] = str(f)
    return result


def _extract_file_num(filename: str):
    """Extract the 3-digit file-number from a DREAM filename, or None."""
    m = re.match(r'.*_(\d{3})_feu-combined', filename)
    if m:
        return int(m.group(1))
    m = re.match(r'.*_(\d{3})_(\d{2})[._]', filename)
    if m:
        return int(m.group(1))
    return None


def _make_combined_name(a_hits_path: str) -> str:
    """Replace the 2-digit FEU field with 'feu-combined' in a hits filename."""
    name = os.path.basename(a_hits_path)
    return re.sub(r'(_\d{3}_)\d{2}(_hits\.root)$', r'\1feu-combined\2', name)


# ---------------------------------------------------------------------------
# Run-config helpers
# ---------------------------------------------------------------------------

def _read_sample_period(run_dir: Path):
    """Return dream_daq_info.sample_period from run_config.json, or None if absent."""
    cfg_path = run_dir / 'run_config.json'
    if not cfg_path.exists():
        return None
    try:
        with open(cfg_path) as f:
            cfg = json.load(f)
        return cfg.get('dream_daq_info', {}).get('sample_period')
    except Exception as e:
        print(f"[watcher] Could not read sample_period from {cfg_path}: {e}")
        return None


# ---------------------------------------------------------------------------
# Worker functions (invoke C++ executables)
# ---------------------------------------------------------------------------

def _decode_file(fdf_path: str, root_path: str, decode_exe: str):
    print(f"[decode]  {os.path.basename(fdf_path)}")
    subprocess.run([decode_exe, fdf_path, root_path])


def _analyze_file(root_path: str, ped_dir: str, hits_out_path: str, analyze_exe: str,
                  sample_period=None, common_noise_subtraction: bool = True):
    m = re.search(r'_(\d{3})_(\d{2})', os.path.basename(root_path))
    if not m:
        print(f"[analyze] Cannot extract FEU number from {root_path}, skipping")
        return
    feu_num = int(m.group(2))

    ped_path = ''
    if ped_dir and os.path.isdir(ped_dir):
        ped_files = [
            f for f in os.listdir(ped_dir)
            if '_pedthr_' in f and f.endswith('.root')
            and re.search(r'_(\d{3})_(\d{2})', f)
            and int(re.search(r'_(\d{3})_(\d{2})', f).group(2)) == feu_num
        ]
        if len(ped_files) == 1:
            ped_path = os.path.join(ped_dir, ped_files[0])
            print(f"[analyze] Using pedestal {ped_files[0]}")
        elif len(ped_files) > 1:
            print(f"[analyze] Multiple pedestals for FEU {feu_num}, skipping {os.path.basename(root_path)}")
            return
        else:
            print(f"[analyze] No pedestal for FEU {feu_num}, continuing without")

    print(f"[analyze] {os.path.basename(root_path)}")
    cmd = [analyze_exe, root_path, hits_out_path, ped_path]
    if sample_period is not None:
        cmd += ['--tps', str(sample_period)]
    cmd += ['--cns', '1' if common_noise_subtraction else '0']
    subprocess.run(cmd)


def _combine_hits(feu_hits_map: dict, combined_path: str, combine_exe: str):
    print(f"[combine] -> {os.path.basename(combined_path)}")
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=True) as tmp:
        for feu, path in sorted(feu_hits_map.items()):
            tmp.write(f"{path} {feu}\n")
        tmp.flush()
        subprocess.run([combine_exe, tmp.name, combined_path], check=True)


if __name__ == '__main__':
    main()
