#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_num.py — the ONE answer to "what is the next run number?".

    .venv/bin/python run_num.py            # peek: what the next run would be
    .venv/bin/python run_num.py --allocate # claim it (prints the number)

WHY THIS FILE EXISTS
  Before 2026-07-27 there were three different notions of "the next run number", and they
  disagreed:

  1. `iterate_run_num.py` (the GUI Start Run path). Parsed the `_N` suffix off
     `run_config_beam.py`'s hardcoded `self.run_name` and probed upward for a free
     directory — then **rewrote the `self.run_name = '...'` line in the tracked source
     file**. It only ever worked for the base config (every generator in `run_configs/`
     overrides `run_name`, so editing the base changed nothing), it only looked at the HDD
     runs tree, and Flask fired it with `Popen(...)` + `sleep(0.2)` and then read the name
     back in a separate request — so the confirmation popup could name a different run than
     the one that actually started. It is why `start_run.sh` carries the line
     `#python iterate_run_num.py  # Not working, skip for now!`.
  2. `switch_mode.next_run_num()` (the --go changeover path). max(both run trees) + 1,
     later + a high-water mark. Correct, but private to switch_mode.
  3. `/start_run` (launch a JSON directly). No iteration at all — whatever the JSON says.

  Now everything calls this module, and the number is chosen exactly once per run.

WHAT IT GUARANTEES
  * **Monotonic.** Never returns a number it has already handed out, even if the
    directories that number came from are deleted afterwards.
  * **Both trees.** The HDD run tree and the DREAM staging tree, since a run can exist in
    one and not the other.
  * ⚠ **Deletion-proof**, which the directory scan alone is not: `space_watcher` drops its
    `keep_recent_runs` reserve below `emergency_gb` (50 GB) and will then delete "any
    EOS-verified run ... newest included". With the newest gone from both local trees a
    pure scan hands back a number that still exists ON EOS, and the new run's backup would
    land in a directory holding another run's data. The high-water file closes that.

FAILURE DIRECTIONS ARE ALL "SKIP", NEVER "REUSE"
  * `allocate()` persists the mark when the number is CLAIMED, not when the run succeeds,
    so an abandoned start burns a number rather than letting the next one reuse it.
  * A missing / corrupt / implausible high-water file falls back to the directory scan.
  * An unwritable state file degrades to scan-only with a warning instead of raising.
  Gaps in the run numbering are harmless; a reused number silently corrupts a backup.
"""
import argparse
import errno
import fcntl
import json
import os
import re
import sys
import time

REPO = os.path.dirname(os.path.abspath(__file__))
RUNS_DIR = '/mnt/data/x17/beam_july/runs'
DREAM_RUN_DIR = '/home/mx17/july_dream/dream_run'
HIGHWATER = os.path.join(REPO, 'config', 'run_num_highwater.json')

# A high-water mark this far beyond the directories is treated as corruption, not history.
IMPLAUSIBLE_AHEAD = 1000


def scan_max(roots=(RUNS_DIR, DREAM_RUN_DIR)):
    """Highest run_N present in any of the run trees (0 if none)."""
    hi = 0
    for root in roots:
        try:
            names = os.listdir(root)
        except OSError:
            continue
        for n in names:
            m = re.fullmatch(r'run_(\d+)', n)
            if m:
                hi = max(hi, int(m.group(1)))
    return hi


def _parse_highwater(raw, scan_hi, quiet=False):
    """Pure: high-water value from file text, 0 if absent/corrupt/implausible."""
    try:
        hw = int(json.loads(raw).get('last_allocated', 0))
    except Exception:  # noqa: BLE001
        return 0
    if hw < 0 or hw > scan_hi + IMPLAUSIBLE_AHEAD:
        if not quiet:
            print(f'[runnum] ⚠ ignoring implausible high-water mark {hw} '
                  f'(directories top out at {scan_hi})', file=sys.stderr)
        return 0
    return hw


def read_highwater(scan_hi, path=HIGHWATER, quiet=False):
    """Highest number ever handed out, or 0. Never trusted blindly."""
    try:
        with open(path) as f:
            raw = f.read()
    except Exception:  # noqa: BLE001
        return 0
    return _parse_highwater(raw, scan_hi, quiet)


def peek(roots=(RUNS_DIR, DREAM_RUN_DIR), path=HIGHWATER):
    """What the next run number WOULD be. No side effects — safe to call for display."""
    hi = scan_max(roots)
    return max(hi, read_highwater(hi, path, quiet=True)) + 1


LOCK_TIMEOUT_S = 20.0


class AllocationBusy(RuntimeError):
    """Could not take the allocation lock. Deliberately fatal — see allocate()."""


def allocate(roots=(RUNS_DIR, DREAM_RUN_DIR), path=HIGHWATER, timeout_s=LOCK_TIMEOUT_S):
    """Claim the next run number and record it. Call this ONCE per run start.

    ⚠ ATOMIC. The whole read-modify-write happens while holding an exclusive `flock` on the
    high-water file, because the callers genuinely can overlap: `/run/prepare` (the GUI
    Start Run button) does NOT hold switch_mode's changeover lock, so a Start Run racing a
    `mode_watcher` changeover — or simply a double-clicked button, or two browser tabs —
    hits this concurrently. Measured before the lock existed: 12 simultaneous calls returned
    only 4 distinct numbers. Two runs sharing a number means two runs sharing an EOS
    directory, which is the one outcome worth failing hard to avoid.

    If the lock cannot be taken within `timeout_s` this RAISES rather than guessing.
    Refusing to start a run is recoverable; silently duplicating a run number is not.

    ⚠ Claiming is deliberately eager: if the operator then cancels the confirmation
    dialog, the number is burned. That is the safe direction — a gap costs nothing, while
    handing the same number out twice can put two runs in one EOS directory. It is also
    what lets the GUI show the REAL run name before starting, instead of showing one
    number and starting another.
    """
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    except OSError as e:
        # Degraded: no state file possible (read-only mount, bad path). Fall back to a
        # scan-only allocation so a run can still be started, but say so loudly — this
        # path has NO cross-process protection.
        print(f'[runnum] ⚠ cannot open the high-water file ({e}) — scan-only allocation, '
              f'NOT race-safe', file=sys.stderr)
        return scan_max(roots) + 1

    with os.fdopen(fd, 'r+') as f:
        deadline = time.time() + timeout_s
        while True:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as e:
                if e.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
                if time.time() >= deadline:
                    raise AllocationBusy(
                        f'another process has held the run-number lock for '
                        f'{timeout_s:g}s ({path}). Refusing to allocate rather than risk '
                        f'handing out a duplicate run number.')
                time.sleep(0.05)

        f.seek(0)
        hi = scan_max(roots)
        nxt = max(hi, _parse_highwater(f.read(), hi)) + 1
        f.seek(0)
        f.truncate()
        json.dump({'last_allocated': nxt,
                   'at': time.strftime('%Y-%m-%d %H:%M:%S'),
                   'scan_max': hi}, f, indent=1)
        f.flush()
        os.fsync(f.fileno())          # survive a crash between claim and run start
        return nxt                    # flock released by the close on the way out


def run_name(n):
    return f'run_{n}'


def main():
    ap = argparse.ArgumentParser(description="The next run number, in one place.")
    ap.add_argument('--allocate', action='store_true',
                    help='claim the number (persists the high-water mark), not just peek')
    ap.add_argument('--json', action='store_true', help='machine-readable output')
    args = ap.parse_args()
    n = allocate() if args.allocate else peek()
    if args.json:
        print(json.dumps({'run_num': n, 'run_name': run_name(n),
                          'allocated': args.allocate, 'scan_max': scan_max()}))
    else:
        print(n)
    return 0


if __name__ == '__main__':
    sys.exit(main())
