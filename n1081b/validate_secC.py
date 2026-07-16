#!/usr/bin/env python3
"""
Validation test for the extended N1081B scan watcher against Module 6 (.245),
Section C — the one section the user confirmed is NOT currently in use.

Purpose: prove the watcher's get/set input+output channel-config path works on .245's
OLD firmware (2022.3.0.0) BEFORE we trust it to drive SEC_B (mesh) + SEC_D (pulser) in
a long unattended run. Exercises the EXACT functions the watcher uses:

  1. snapshot_targets()  -- read-only capture of SEC_C ch 0-3 (in + out config)
  2. apply_scan()        -- flip output_status on ch0, then nudge input delay on ch0,
                            each verified by read-back (this is the watcher's write path)
  3. restore_snapshot()  -- put SEC_C back to the captured state, verified
  4. final re-read       -- confirm every field matches the original snapshot

The snapshot is taken FIRST and is read-only, so we always hold the original values;
restore runs in a finally block and is retried, so a mid-test failure still resets SEC_C.

Run on mx17-daq (board net):  .venv/bin/python n1081b/validate_secC.py
"""
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from n1081b_scan_watcher import (  # noqa: E402
    snapshot_targets, apply_scan, restore_snapshot, log,
)

BOARD = '192.168.10.245'
SECTION = 'SEC_C'
CHANNELS = [0, 1, 2, 3]
TEST_CH = 0

# .245 runs old firmware that serves get/set without a login -> require_login False.
SCHED = {'board': BOARD, 'password': 'password', 'require_login': False,
         'targets': {'secC': {'section': SECTION, 'channels': CHANNELS}}}
# One-channel target for the write tests, so we perturb only ch0.
SCHED_CH0 = {'board': BOARD, 'password': 'password', 'require_login': False,
             'targets': {'secC': {'section': SECTION, 'channels': [TEST_CH]}}}


def _cmp_snapshots(a, b):
    """List of human-readable mismatches between two snapshots (empty = identical)."""
    diffs = []
    for ch in a['secC']:
        for direction in ('in', 'out'):
            for k, v in a['secC'][ch][direction].items():
                bv = b['secC'].get(ch, {}).get(direction, {}).get(k)
                if bv != v:
                    diffs.append(f'ch{ch}.{direction}.{k}: {v!r} -> {bv!r}')
    return diffs


def main():
    results = {}
    snap = None
    log(f'=== SEC_C validation on {BOARD} {SECTION} ch{CHANNELS} ===')

    # 1) snapshot (read-only) -------------------------------------------------
    try:
        snap = snapshot_targets(SCHED)
        orig = snap['secC'][TEST_CH]
        log(f'Snapshot OK. ch{TEST_CH} original: '
            f"in.status={orig['in']['status']} in.delay={orig['in']['delay']} "
            f"out.status={orig['out']['status']}")
        for ch in CHANNELS:
            s = snap['secC'][ch]
            log(f"   ch{ch}: in={s['in']}  out={s['out']}")
        results['snapshot'] = True
    except Exception as e:  # noqa: BLE001
        log(f'!! snapshot FAILED: {e!r}')
        log('   (read-only step; board untouched) — ABORTING, nothing to restore.')
        return 2

    orig_out = snap['secC'][TEST_CH]['out']['status']
    orig_delay = snap['secC'][TEST_CH]['in']['delay']

    try:
        # 2a) output write+verify: flip output_status on ch0 -------------------
        log(f'TEST output write: flip ch{TEST_CH} output_status {orig_out} -> {not orig_out}')
        results['output_write'] = apply_scan(SCHED_CH0, {'secC': {'output_status': not orig_out}})

        # 2b) input write+verify: nudge input delay on ch0 --------------------
        new_delay = orig_delay + 10
        log(f'TEST input write: set ch{TEST_CH} input delay {orig_delay} -> {new_delay}')
        results['input_write'] = apply_scan(SCHED_CH0, {'secC': {'delay': new_delay}})
    except Exception as e:  # noqa: BLE001
        log(f'!! write test raised: {e!r}')
        results.setdefault('output_write', False)
        results.setdefault('input_write', False)
    finally:
        # 3) restore snapshot (always) ---------------------------------------
        log('Restoring SEC_C to captured snapshot...')
        results['restore'] = restore_snapshot(SCHED, snap)

    # 4) final independent re-read verify ------------------------------------
    try:
        after = snapshot_targets(SCHED)
        diffs = _cmp_snapshots(snap, after)
        if diffs:
            log('!! FINAL VERIFY: SEC_C does NOT match original:')
            for d in diffs:
                log(f'     {d}')
            results['final_match'] = False
        else:
            log('FINAL VERIFY: SEC_C matches original snapshot exactly.')
            results['final_match'] = True
    except Exception as e:  # noqa: BLE001
        log(f'!! final re-read FAILED: {e!r}')
        results['final_match'] = False

    # summary -----------------------------------------------------------------
    log('--- summary ---')
    order = ['snapshot', 'output_write', 'input_write', 'restore', 'final_match']
    for k in order:
        log(f'   {k:14s}: {"PASS" if results.get(k) else "FAIL"}')
    all_pass = all(results.get(k) for k in order)
    log(f'=== {"ALL PASS — watcher path works on .245; safe for SEC_B/SEC_D run" if all_pass else "FAILURES — do NOT trust the watcher on .245 yet"} ===')
    return 0 if all_pass else 1


if __name__ == '__main__':
    sys.exit(main())
