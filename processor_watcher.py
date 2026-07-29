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
import signal
import tempfile
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common_functions import create_dir_if_not_exist

# How long to wait for every FEU of a file-group to arrive before giving up and
# processing it incomplete (with a warning). copy_on_fly streams the FDFs across
# progressively, so a group is routinely partial for tens of seconds.
INCOMPLETE_GRACE_S = 900


# Decode watchdog defaults (see _decode_file). The decoder can infinite-loop on
# certain FDFs (100% CPU, input position and output ROOT both frozen — seen on
# the banco P2 setup 2026-07-23/24, same DreamDecoder source); one such file
# would block the pipeline forever. These bound a single decode. Overridable via
# the processor config. Ported from the banco fork's watcher, 2026-07-24.
DECODE_STALL_TIMEOUT_S = 180   # kill a decode whose output ROOT has not grown this long
DECODE_HARD_TIMEOUT_S  = 1800  # absolute cap on a single decode


class DecodeTimeout(Exception):
    """A decode was killed for hanging (output frozen, or hard cap exceeded).

    The raw FDF is preserved — renamed to <name>.hang — so the file_num can
    still finish from the surviving FEUs and the file stays a reproducer for
    the decoder bug.
    """
    def __init__(self, fdf_path, hang_path, reason):
        super().__init__(f'decode of {os.path.basename(fdf_path)} killed: {reason}')
        self.fdf_path = fdf_path
        self.hang_path = hang_path
        self.reason = reason


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
    decode_stall_timeout_s = config.get('decode_stall_timeout_s', DECODE_STALL_TIMEOUT_S)
    decode_hard_timeout_s  = config.get('decode_hard_timeout_s',  DECODE_HARD_TIMEOUT_S)

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
    incomplete_since: dict = {}    # key -> first time seen with a missing FEU
    idle_ticks = 0
    idle_line = False
    _SPINNER = ['-', '\\', '|', '/']

    def _end_idle():
        nonlocal idle_line
        if idle_line:
            sys.stdout.write('\n')
            sys.stdout.flush()
            idle_line = False

    def _scan_once() -> bool:
        """Run one newest-first scan and process at most ONE file_num.

        Runs, subruns and file_nums are all walked newest-first, and the scan
        returns as soon as it processes a single file_num so the caller can
        immediately rescan.  This keeps the watcher always working the newest
        available data, only draining older backlog when nothing newer needs
        processing (i.e. it works backwards in general).  Returns True if it
        processed a file_num, False if a full pass found nothing to do.
        """
        if not runs_dir.exists():
            return False

        for run_dir in _newest_first(d for d in runs_dir.iterdir() if d.is_dir()):
            if include_runs is not None and run_dir.name not in include_runs:
                continue
            if run_dir.name in exclude_runs:
                continue
            if run_dir.name in checked_stale_runs:
                continue

            is_stale = _run_is_stale(run_dir, raw_inner, stale_run_days)

            sample_period = _read_sample_period(run_dir)
            feu_det_map   = _read_feu_detector_map(run_dir)
            expected_feus = _read_expected_feus(run_dir)
            zs_baseline   = _read_zs_baseline(run_dir)

            for subrun_dir in _newest_first(d for d in run_dir.iterdir() if d.is_dir()):
                raw_dir = subrun_dir / raw_inner
                if not raw_dir.exists():
                    continue

                ped_dir = _resolve_pedestal_dir(raw_dir, pedestal_loc, pedestal_base_dir)

                if do_decode and ped_dir:
                    _decode_pedestals(ped_dir, decode_exe,
                                      decode_stall_timeout_s, decode_hard_timeout_s)

                all_fnums  = _get_data_file_nums(raw_dir)
                done_fnums = _get_processed_file_nums(
                    subrun_dir, combined_inner, hits_inner, decoded_inner,
                    do_combine, do_analyze, expected_feus, raw_dir
                )

                for fnum in sorted(all_fnums - done_fnums, reverse=True):
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

                    # ---- FEU-completeness guard ----
                    # Size-stability alone is NOT enough: copy_on_fly streams the
                    # 8 FDFs across progressively, so a single FEU's file can sit
                    # complete and stable on disk while the rest are still coming.
                    # Acting on that subset produced permanently partial data —
                    # run_63/dblPS_dr600_r540_004 was decoded from FEU 06 alone at
                    # 09:52 and combined at 09:53, while FEUs 01-05,07,08 only
                    # finished arriving at 10:01; the combined file then marked the
                    # file_num "done" forever. run_61 48/113 groups, run_62 12/25,
                    # run_63 10/18 were lost this way (raw FDFs all intact).
                    # Wait for the full set; after INCOMPLETE_GRACE_S process what
                    # is there but say so loudly, so a genuinely missing FEU cannot
                    # stall the pipeline indefinitely.
                    n_feus = len({_extract_feu_num(p.name)
                                  for p in all_fdf_group} - {None})
                    if expected_feus and n_feus < expected_feus:
                        first_seen = incomplete_since.setdefault(key, time.time())
                        waited = time.time() - first_seen
                        if waited < INCOMPLETE_GRACE_S:
                            prev_sizes[key] = current
                            continue
                        _end_idle()
                        print(f"[watcher] WARNING {run_dir.name}/{subrun_dir.name} "
                              f"file_num={fnum:03d}: only {n_feus}/{expected_feus} "
                              f"FEUs after {waited / 60:.0f} min — processing "
                              f"INCOMPLETE")
                    else:
                        incomplete_since.pop(key, None)

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
                            sample_period, common_noise_subtraction,
                            feu_det_map, zs_baseline,
                            decode_stall_timeout_s, decode_hard_timeout_s
                        )
                        del prev_sizes[key]
                        incomplete_since.pop(key, None)
                        return True
                    else:
                        prev_sizes[key] = current

            if is_stale:
                checked_stale_runs.add(run_dir.name)
                _end_idle()
                print(f"[watcher] Marked stale (will skip): {run_dir.name}")

        return False

    while True:
        if _scan_once():
            idle_ticks = 0
            continue

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
                       sample_period=None, common_noise_subtraction=True,
                       feu_det_map=None, zs_baseline=False,
                       decode_stall_timeout_s=DECODE_STALL_TIMEOUT_S,
                       decode_hard_timeout_s=DECODE_HARD_TIMEOUT_S):

    decoded_dir  = subrun_dir / decoded_inner
    hits_dir     = subrun_dir / hits_inner
    combined_dir = subrun_dir / combined_inner

    # Step 1: Decode FDFs
    if do_decode:
        create_dir_if_not_exist(str(decoded_dir))
        counts = {}
        with ThreadPoolExecutor(max_workers=n_threads) as pool:
            futs = {}
            for fdf in all_fdf_paths:
                root_path = decoded_dir / fdf.name.replace('.fdf', '.root')
                if root_path.exists():
                    continue
                futs[pool.submit(_decode_file, str(fdf), str(root_path), decode_exe,
                                 decode_stall_timeout_s,
                                 decode_hard_timeout_s)] = (fdf, root_path)
            for t in as_completed(futs):
                try:
                    counts[futs[t]] = t.result()
                except DecodeTimeout as e:
                    # Continue with the surviving FEUs; the quarantined FDF
                    # stays behind as <name>.hang for offline debugging. Recorded
                    # as None so the truncation guard below leaves it alone —
                    # re-decoding a file that just hung would only hang again.
                    print(f"[decode]  quarantined {os.path.basename(e.hang_path)} "
                          f"({e.reason}); continuing with surviving FEUs")
                    counts[futs[t]] = None

        # Truncation guard: all FEUs of a group see the same triggers, so their
        # decoded event counts must agree. Re-decode any short output once (a
        # dead decode process is the usual cause); warn if it stays short.
        good = [c for c in counts.values() if c and c > 0]
        if good:
            gmax = max(good)
            for (fdf, root_path), c in list(counts.items()):
                if c is None or c <= 0 or c >= gmax:
                    continue
                print(f"[decode]  TRUNCATED {os.path.basename(str(fdf))}: "
                      f"{c} events vs {gmax} in this group — re-decoding")
                if root_path.exists():
                    root_path.unlink()
                try:
                    c2 = _decode_file(str(fdf), str(root_path), decode_exe,
                                      decode_stall_timeout_s, decode_hard_timeout_s)
                except DecodeTimeout as e:
                    print(f"[decode]  quarantined {os.path.basename(e.hang_path)} "
                          f"({e.reason}) on the truncation retry")
                    continue
                if c2 is None or (c2 > 0 and c2 < gmax):
                    print(f"[decode]  WARNING {os.path.basename(str(fdf))} still "
                          f"short after retry ({c2} vs {gmax})")

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
                    _analyze_file, str(root_path), ped_dir, str(hits_path), analyze_exe,
                    sample_period, common_noise_subtraction, feu_det_map, zs_baseline
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
            # Rebuild when any input is newer than the combined file. Previously
            # this was "write only if absent", which froze a partial combine (built
            # from a subset of FEUs) in place even after the missing FEUs were
            # later decoded. With the freshness check, recovering a damaged group
            # needs no manual deletion: decoding the missing FEUs produces newer
            # hits files, which re-triggers the combine.
            newest_input = max(os.path.getmtime(p) for p in feu_hits_map.values())
            stale = (combined_path.exists()
                     and os.path.getmtime(combined_path) < newest_input)
            if not combined_path.exists() or stale:
                if stale:
                    print(f"[combine] rebuilding {combined_name} "
                          f"({len(feu_hits_map)} FEUs; inputs newer than output)")
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


def _decode_pedestals(ped_dir: str, decode_exe: str,
                      decode_stall_timeout_s=DECODE_STALL_TIMEOUT_S,
                      decode_hard_timeout_s=DECODE_HARD_TIMEOUT_S):
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
        try:
            _decode_file(str(fdf), str(root_out), decode_exe,
                         decode_stall_timeout_s, decode_hard_timeout_s)
        except DecodeTimeout as e:
            print(f"[decode]  quarantined pedestal "
                  f"{os.path.basename(e.hang_path)} ({e.reason}); skipping it")


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------

def _newest_first(dirs):
    """Sort directories newest-first by the last integer in their name.

    Runs are ``run_<N>`` (higher N == newer) and subruns end in a ``_<k>``
    creation index (higher k == newer), so the trailing integer orders both by
    recency.  Names without a number sort last.
    """
    def key(p):
        nums = re.findall(r'\d+', p.name)
        return int(nums[-1]) if nums else -1
    return sorted(dirs, key=key, reverse=True)


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
                              do_combine, do_analyze, expected_feus=None,
                              raw_dir=None) -> set:
    """Return file_nums whose final pipeline output already exists.

    ``expected_feus`` makes "done" mean *fully* done: a file_num counts as
    processed only if every FEU present in the raw directory also has a decoded
    ROOT. Without this the mere existence of a combined file marked a group
    finished even when it had been built from a single FEU, which is what made
    the copy_on_fly race permanent instead of self-healing.
    """
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

    # Demote groups whose per-FEU decode is incomplete, so the watcher revisits
    # them and fills in the missing FEUs from the (intact) raw FDFs.
    if expected_feus and raw_dir is not None:
        decoded_dir = subrun_dir / decoded_inner
        for n in sorted(done):
            raw_feus = {_extract_feu_num(f.name) for f in raw_dir.iterdir()
                        if _is_data_fdf(f.name) and _extract_file_num(f.name) == n}
            raw_feus -= {None}
            dec_feus = set()
            if decoded_dir.exists():
                dec_feus = {_extract_feu_num(f.name) for f in decoded_dir.iterdir()
                            if f.name.endswith('.root') and '_datrun_' in f.name
                            and '_pedestals_' not in f.name
                            and _extract_file_num(f.name) == n} - {None}
            if raw_feus and not raw_feus.issubset(dec_feus):
                done.discard(n)
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


def _extract_feu_num(filename: str):
    """Extract the 2-digit FEU number from a DREAM filename, or None."""
    m = re.match(r'.*_(\d{3})_(\d{2})[._]', filename)
    return int(m.group(2)) if m else None


def _read_expected_feus(run_dir: Path):
    """How many FEUs a complete file-group must contain, from run_config.json.

    Returns None when it cannot be determined, in which case the completeness
    guard below is skipped (fail-open: never stall the pipeline on a config we
    cannot read).
    """
    cfg_path = run_dir / 'run_config.json'
    if not cfg_path.exists():
        return None
    try:
        with open(cfg_path) as f:
            cfg = json.load(f)
        feus = cfg.get('dream_daq_info', {}).get('included_feus')
        return len(feus) if feus else None
    except Exception as e:
        print(f"[watcher] Could not read included_feus from {cfg_path}: {e}")
        return None


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


def _read_zs_baseline(run_dir: Path) -> bool:
    """True when the run took zero-suppressed data with ON-FEU pedestal
    subtraction (dream_daq_info in run_config.json): the decoded waveforms are
    then re-centred at 256 and analyze_waveforms needs --zs-baseline 1 (the
    pedestal file's per-channel means would be the wrong baseline; its RMS is
    still used for thresholds). ZS without on-FEU subtraction sits on the raw
    per-channel baselines, where the pedestal means ARE correct — hence the
    two-condition test. Ported from the banco P2 fork, 2026-07-24."""
    cfg_path = run_dir / 'run_config.json'
    if not cfg_path.exists():
        return False
    try:
        with open(cfg_path) as f:
            cfg = json.load(f)
        daq = cfg.get('dream_daq_info', {})
        return bool(daq.get('zero_suppress')) and bool(daq.get('pedestal_subtraction'))
    except Exception as e:
        print(f"[watcher] Could not read zs_baseline from {cfg_path}: {e}")
        return False


def _read_feu_detector_map(run_dir: Path) -> dict:
    """Return {feu_num: detector_label} from run_config.json, or {} if absent.

    Each detector's ``dream_feus`` maps a connector name to a ``(feu, channel)``
    pair, so a detector can span several FEUs (e.g. beam ``mx17_A`` uses FEU 3+4)
    and, conversely, one FEU can serve several detectors (e.g. the cosmic-bench
    ``m3`` quadrants all sit on FEU 1).  When a FEU serves more than one included
    detector the labels are joined with ``/`` so the analyze line stays a single
    whitespace-free token.  Restricted to ``included_detectors`` when present.
    """
    cfg_path = run_dir / 'run_config.json'
    if not cfg_path.exists():
        return {}
    try:
        with open(cfg_path) as f:
            cfg = json.load(f)
    except Exception as e:
        print(f"[watcher] Could not read detectors from {cfg_path}: {e}")
        return {}

    detectors = cfg.get('detectors', []) or []
    included  = cfg.get('included_detectors')
    if included:
        detectors = [d for d in detectors if d.get('name') in set(included)]

    feu_map: dict = {}
    for det in detectors:
        name = det.get('name')
        if not name:
            continue
        for feu_ch in (det.get('dream_feus') or {}).values():
            if isinstance(feu_ch, (list, tuple)) and feu_ch:
                feu = int(feu_ch[0])
                if name not in feu_map.setdefault(feu, []):
                    feu_map[feu].append(name)
    return {feu: '/'.join(names) for feu, names in feu_map.items()}


# ---------------------------------------------------------------------------
# Worker functions (invoke C++ executables)
# ---------------------------------------------------------------------------

def _decode_file(fdf_path: str, root_path: str, decode_exe: str,
                 stall_timeout_s: float = DECODE_STALL_TIMEOUT_S,
                 hard_timeout_s: float = DECODE_HARD_TIMEOUT_S):
    """Decode one FDF, with a watchdog. Returns the decoder's event count, or
    None if it failed; raises DecodeTimeout if the watchdog had to kill it.

    Two independent decoder failure modes are handled here.

    HANG — DreamDecoder can infinite-loop on certain files: 100% CPU with the
    input read position and the output ROOT both frozen (seen on the banco P2
    setup 2026-07-23/24; same decoder source). The watcher is sequential per
    subrun, so one such file pegs a core AND blocks the pipeline behind it.
    Guard it: poll the output ROOT while the decoder runs; if it stops growing
    for stall_timeout_s (the hang signature) or the decode exceeds
    hard_timeout_s, kill the decoder, drop the partial ROOT, and quarantine the
    FDF to <name>.hang. The raw FDF is renamed, never deleted — it remains a
    reproducer for the decoder bug.

    TRUNCATION — a decode that died part-way used to leave a short ROOT which
    Step 1 then skipped forever (run_61 lost 68609 events across 84 files this
    way, run_62 10232). The returned count comes from the decoder's own
    ``Events analysed : N`` line, and the caller compares counts across a
    file-group (every FEU sees the same triggers, so they must agree). A silent
    failure is reported and its output removed, so the next pass retries this
    FEU instead of treating it as done.
    """
    print(f"[decode]  {os.path.basename(fdf_path)}")
    # stdout goes to a temp file rather than a pipe: the watchdog loop below
    # never drains a pipe, so a chatty decoder would deadlock on a full one.
    with tempfile.TemporaryFile(mode='w+', errors='replace') as out:
        # own session/process group so the watchdog can reap the decoder and any
        # children it might spawn in one shot.
        proc = subprocess.Popen([decode_exe, fdf_path, root_path],
                                stdout=out, stderr=subprocess.STDOUT,
                                start_new_session=True)
        start = time.time()
        last_size, last_progress = -1, start
        while True:
            try:
                proc.wait(timeout=5)
                break  # decode finished on its own (success or its own error)
            except subprocess.TimeoutExpired:
                pass
            now = time.time()
            try:
                size = os.path.getsize(root_path)
            except OSError:
                size = 0
            if size > last_size:
                last_size, last_progress = size, now
            stalled  = now - last_progress > stall_timeout_s
            over_cap = now - start > hard_timeout_s
            if not (stalled or over_cap):
                continue
            reason = (f'output frozen {int(now - last_progress)}s at {size} B'
                      if stalled else f'exceeded {int(hard_timeout_s)}s hard cap')
            print(f"[decode]  HANG: {os.path.basename(fdf_path)} — {reason}; killing decoder")
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()  # fallback if the group is already gone
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
            try:
                if os.path.exists(root_path):
                    os.remove(root_path)
            except OSError:
                pass
            hang_path = fdf_path + '.hang'
            try:
                os.replace(fdf_path, hang_path)
            except OSError:
                hang_path = fdf_path
            raise DecodeTimeout(fdf_path, hang_path, reason)

        out.seek(0)
        stdout_text = out.read()

    if stdout_text:
        sys.stdout.write(stdout_text)
    # A silent decode failure used to be invisible: the pipeline carried on and
    # combined whatever happened to exist, so a crashed/killed decoder became
    # permanently missing data. Report it, and remove any truncated output so the
    # next pass retries this FEU instead of treating it as done.
    if proc.returncode != 0:
        print(f"[decode]  FAILED rc={proc.returncode}: {os.path.basename(fdf_path)}")
        if os.path.exists(root_path):
            os.remove(root_path)
        return None
    if not os.path.exists(root_path) or os.path.getsize(root_path) == 0:
        print(f"[decode]  FAILED (no/empty output): {os.path.basename(fdf_path)}")
        if os.path.exists(root_path):
            os.remove(root_path)
        return None
    m = re.search(r'Events analysed\s*:\s*(\d+)', stdout_text)
    return int(m.group(1)) if m else -1     # -1 = decoded but count unknown


def _analyze_file(root_path: str, ped_dir: str, hits_out_path: str, analyze_exe: str,
                  sample_period=None, common_noise_subtraction: bool = True, feu_det_map=None,
                  zs_baseline: bool = False):
    m = re.search(r'_(\d{3})_(\d{2})', os.path.basename(root_path))
    if not m:
        print(f"[analyze] Cannot extract FEU number from {root_path}, skipping")
        return
    feu_num = int(m.group(2))
    detector = (feu_det_map or {}).get(feu_num)

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

    det_tag = f"det={detector} " if detector else ""
    print(f"[analyze] {det_tag}feu={feu_num:02d}  {os.path.basename(root_path)}")
    cmd = [analyze_exe, root_path, hits_out_path, ped_path]
    if sample_period is not None:
        cmd += ['--tps', str(sample_period)]
    cmd += ['--cns', '1' if common_noise_subtraction else '0']
    if zs_baseline:
        cmd += ['--zs-baseline', '1']
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
