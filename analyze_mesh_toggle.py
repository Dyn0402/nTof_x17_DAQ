#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analyze_mesh_toggle.py — correlate the mesh ON/OFF square wave from
run_config_mesh_toggle_test.py against BOTH observables:

  DREAM side  : raw FDF bytes written per sub-run (continuous, no sampling limit)
  n_TOF side  : SiPM wall gamma-flash amplitude per stream file
                (stream1_monitor/wall_probe.py), plus the raw file size

TIME BASE — the thing that most easily goes wrong here:
  * dream_daq.log sub-run timestamps are LOCAL (CEST).
  * EOS mtimes from `xrdfs ls -l` are UTC. Silent 2 h offset; see memory
    eos-mtimes-are-utc. We convert EOS -> local and work entirely in local time.
  * A stream file's mtime is when it was CLOSED, so it covers [mtime - dt_prev, mtime].
    The FLASH is a point sample near that window's START, so it is assigned by the mesh
    state at `start` and reported with seconds-since-transition — which is what exposes
    the collapse/recovery asymmetry. File SIZE is an integral over the whole window, so
    it is flagged '(size straddles)' when the window crosses a transition.

Usage:
    ~/PycharmProjects/nTof_x17/.venv/bin/python analyze_mesh_toggle.py
    ... --run mesh_toggle_test --ntof-runs 224540 224541 --budget-mb 24
"""
import argparse
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'stream1_monitor'))

RUNS_ROOT = Path('/mnt/data/x17/beam_july/runs')
STAGE_ROOT = Path('/home/mx17/july_dream/dream_run')
XROOTD = 'root://eospublic.cern.ch'
EOS_BASE = '/eos/experiment/ntof/DAQ/2026/EAR2/X17_measurement'
EOS_UTC_OFFSET_H = 2            # EOS mtimes are UTC; we are CEST


def subrun_windows(run):
    """[(name, start, end)] in LOCAL time, from the run's dream_daq.log."""
    log = RUNS_ROOT / run / 'dream_daq.log'
    if not log.exists():
        sys.exit(f'no dream_daq.log for {run} at {log}')
    starts, out = {}, []
    pat = re.compile(r'^([\d-]+ [\d:,]+) INFO: Subrun (started|finished): (\S+)')
    for line in log.read_text().splitlines():
        m = pat.match(line)
        if not m:
            continue
        ts = datetime.strptime(m.group(1).split(',')[0], '%Y-%m-%d %H:%M:%S')
        if m.group(2) == 'started':
            starts[m.group(3)] = ts
        elif m.group(3) in starts:
            out.append((m.group(3), starts.pop(m.group(3)), ts))
    return sorted(out, key=lambda r: r[1])


def dream_bytes(run, sub):
    d = STAGE_ROOT / run / sub
    return sum(f.stat().st_size for f in d.glob('*datrun*.fdf')) if d.is_dir() else 0


def eos_files(ntof_runs):
    """[(local_mtime, size, path)] sorted by time, across the given n_TOF runs."""
    rows = []
    for r in ntof_runs:
        cmd = f'xrdfs {XROOTD} ls -l {EOS_BASE}/{r}/stream1/'
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=300)
        for line in p.stdout.splitlines():
            f = line.split()
            if len(f) < 7 or not f[-1].endswith('.finished'):
                continue
            try:
                utc = datetime.strptime(f'{f[4]} {f[5]}', '%Y-%m-%d %H:%M:%S')
            except ValueError:
                continue
            rows.append((utc + timedelta(hours=EOS_UTC_OFFSET_H), int(f[3]), f[-1]))
    return sorted(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run', default='mesh_toggle_test')
    ap.add_argument('--ntof-runs', nargs='+', default=None,
                    help='n_TOF run numbers to scan (default: auto from EOS, newest 3)')
    ap.add_argument('--budget-mb', type=int, default=24,
                    help='wall_probe read budget; 2 MB is TOO SMALL in the current '
                         'run config (returns 0 wall channels) — 24 MB reaches WALA/B/C')
    args = ap.parse_args()

    wins = subrun_windows(args.run)
    if not wins:
        sys.exit('no completed sub-runs found')
    t0, t1 = wins[0][1], wins[-1][2]
    print(f'=== {args.run}: {len(wins)} sub-runs, {t0} -> {t1} (local) ===\n')

    # ---------- DREAM side: continuous, one number per sub-run ----------
    print('DREAM raw bytes per sub-run (mesh state is the leading name token):')
    print(f'{"sub-run":22s} {"mesh":5s} {"start":8s} {"s":>5s} {"MB":>9s} {"MB/min":>9s}')
    for name, s, e in wins:
        dur = max((e - s).total_seconds(), 1)
        mb = dream_bytes(args.run, name) / 1e6
        mesh = 'ON' if name.startswith('mtestOn') else ('OFF' if name.startswith('mtestOff') else '?')
        print(f'{name:22s} {mesh:5s} {s.strftime("%H:%M:%S")} {dur:5.0f} {mb:9.1f} '
              f'{mb/dur*60:9.1f}')

    on = [dream_bytes(args.run, n) / max((e-s).total_seconds(), 1) * 60 / 1e6
          for n, s, e in wins if n.startswith('mtestOn')]
    off = [dream_bytes(args.run, n) / max((e-s).total_seconds(), 1) * 60 / 1e6
           for n, s, e in wins if n.startswith('mtestOff')]
    if on and off:
        mon, moff = sum(on)/len(on), sum(off)/len(off)
        print(f'\n  mean mesh-ON  {mon:8.1f} MB/min   (n={len(on)})')
        print(f'  mean mesh-OFF {moff:8.1f} MB/min   (n={len(off)})')
        print(f'  ratio ON/OFF  {mon/moff:8.1f}x' if moff else '  ratio: OFF is zero')

    # ---------- n_TOF side: one flash measurement per stream file ----------
    runs = args.ntof_runs
    if runs is None:
        p = subprocess.run(f'xrdfs {XROOTD} ls {EOS_BASE}/', shell=True,
                           capture_output=True, text=True, timeout=300)
        runs = [l.rsplit('/', 1)[-1] for l in p.stdout.split()][-3:]
    print(f'\nn_TOF stream files (runs {" ".join(map(str, runs))}), '
          f'EOS mtimes UTC -> local +{EOS_UTC_OFFSET_H} h:')

    files = [f for f in eos_files(runs)
             if t0 - timedelta(minutes=3) <= f[0] <= t1 + timedelta(minutes=3)]
    if not files:
        print('  none overlapping the run window')
        return 0

    from wall_probe import probe_file      # noqa: E402

    # Mesh-state intervals: state of sub-run i holds from its start until the NEXT
    # sub-run's start (the flip is the first thing scan_control does), so the last
    # one runs to its own end.
    mesh_iv = []
    for i, (name, s_, e_) in enumerate(wins):
        nxt = wins[i + 1][1] if i + 1 < len(wins) else e_
        mesh_iv.append((name, s_, nxt))

    print(f'{"closed":8s} {"covers":17s} {"GB":>5s} {"flash":>8s} {"verdict":8s} assignment')
    prev = None
    for mtime, size, path in files:
        dt = (mtime - prev).total_seconds() if prev else 70.0
        prev = mtime
        start = mtime - timedelta(seconds=min(dt, 120))
        # Mesh state flips AT each sub-run start (verified: n1081b_config.json
        # polled_at lands within ~2 s of the logged start) and HOLDS through the
        # inter-sub-run gap. So the mesh-state interval is start_i -> start_{i+1},
        # which is wider than the sub-run window and makes more files assignable.
        # *** The flash is a POINT sample near the file START, not an integral over
        # the file, so it is assigned by the mesh state AT `start` — NOT by whether
        # the whole file is contained in one state. (Requiring containment was the
        # original rule and it labelled almost everything AMBIGUOUS while the data
        # was in fact perfectly clean; 2026-07-22.) File SIZE, by contrast, IS an
        # integral and genuinely is ambiguous across a transition — hence `contained`.
        at = [(n, a) for n, a, b in mesh_iv if a <= start < b]
        if at:
            tag, t_on = at[-1]
            state = 'ON' if tag.startswith('mtestOn') else 'OFF'
            assign = f'mesh {state:3s} {(start - t_on).total_seconds():5.0f}s in   [{tag}]'
        else:
            assign = 'outside run window (standing mesh state)'
        contained = any(a <= start and mtime <= b for n, a, b in mesh_iv)
        if not contained:
            assign += '  (size straddles)'
        try:
            r = probe_file(path, nbytes=args.budget_mb << 20)
            flash = r['flash_median']
            fs = f'{flash:8.0f}' if flash is not None else '    None'
            vd = r['verdict']
        except Exception as exc:
            fs, vd = '   ERROR', str(exc)[:20]
        print(f'{mtime.strftime("%H:%M:%S")} '
              f'{start.strftime("%H:%M:%S")}-{mtime.strftime("%H:%M:%S")} '
              f'{size/1e9:5.2f} {fs} {vd:8s} {assign}')

    print('\nRead: mesh-ON files should show flash ~34 000 and ~2.7 GB; a collapsed wall\n'
          'gives flash 350-1200 and roughly half the file size. AMBIGUOUS rows straddle a\n'
          'mesh transition and prove nothing either way — use SUBRUN_MIN=2 to get more\n'
          'cleanly-contained files.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
