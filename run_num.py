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


def read_highwater(scan_hi, path=HIGHWATER, quiet=False):
    """Highest number ever handed out, or 0. Never trusted blindly."""
    try:
        with open(path) as f:
            hw = int(json.load(f).get('last_allocated', 0))
    except Exception:  # noqa: BLE001
        return 0
    if hw < 0 or hw > scan_hi + IMPLAUSIBLE_AHEAD:
        if not quiet:
            print(f'[runnum] ⚠ ignoring implausible high-water mark {hw} '
                  f'(directories top out at {scan_hi})', file=sys.stderr)
        return 0
    return hw


def peek(roots=(RUNS_DIR, DREAM_RUN_DIR), path=HIGHWATER):
    """What the next run number WOULD be. No side effects — safe to call for display."""
    hi = scan_max(roots)
    return max(hi, read_highwater(hi, path, quiet=True)) + 1


def allocate(roots=(RUNS_DIR, DREAM_RUN_DIR), path=HIGHWATER):
    """Claim the next run number and record it. Call this ONCE per run start.

    ⚠ Claiming is deliberately eager: if the operator then cancels the confirmation
    dialog, the number is burned. That is the safe direction — a gap costs nothing, while
    handing the same number out twice can put two runs in one EOS directory. It is also
    what lets the GUI show the REAL run name before starting, instead of showing one
    number and starting another.
    """
    hi = scan_max(roots)
    nxt = max(hi, read_highwater(hi, path)) + 1
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            json.dump({'last_allocated': nxt,
                       'at': time.strftime('%Y-%m-%d %H:%M:%S'),
                       'scan_max': hi}, f, indent=1)
    except Exception as e:  # noqa: BLE001
        print(f'[runnum] ⚠ could not persist high-water mark ({e}) — '
              f'scan-only allocation this time', file=sys.stderr)
    return nxt


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
