#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
beam_gate.py — hold the DAQ at sub-run boundaries while the beam is off.

WHY THIS EXISTS
  `daq_control.py` has NO beam-gating: it runs each sub-run for its wall-clock `run_time`
  whether or not any triggers arrive, and then writes `.subrun_complete` regardless. On a
  PS/singles beam trigger that means a beam-off sub-run records ~nothing, is marked done,
  and is then SKIPPED by a later `resume: true` — a permanent hole in the grid that looks
  completely normal in the GUI and the logs.

  daq_control DOES honour the `.pause_run` flag at each sub-run boundary
  (daq_control.py:264). This gate drives that flag from `config/beam_state.json`, so the
  run simply will not START a point into a beam gap: it waits there and picks up when the
  beam comes back. It cannot rescue a beam gap that opens MID sub-run — that point just
  ends up with fewer flashes, which is why every metric downstream is per-flash-anchored
  and why you judge a point by its flash count rather than its wall-clock.

CO-OPERATIVE BY CONSTRUCTION — it will not stamp on anyone else's hold
  Three parties can hold `.pause_run`: the operator (Flask "Pause after subrun"),
  daq_control itself (when an N1081B config apply fails to verify — see
  `_apply_n1081b_with_retry`, which uses this same ownership discipline), and this gate.

    * to PAUSE  — only if `.pause_run` does not already exist. If someone else is already
                  holding, we log it and DO NOT claim it, so we can never release it.
    * to RELEASE — only if our own `.beam_gate_hold` sidecar exists, i.e. only a hold we set.
    * on EXIT   — release our own hold, so a Ctrl-C on this gate can never pin the run.

  A sidecar left behind by a crashed instance is adopted on startup rather than orphaned.

UNKNOWN IS NOT OFF
  `beam_on: null` means the beam monitor could not determine the state — a Kerberos/NXCALS
  hiccup, not a beam stop. A stale `beam_state.json` (monitor down) means the same thing.
  In both cases the gate HOLDS ITS CURRENT STATE: it will not pause on a monitoring glitch,
  and it will not release a hold it already has while it is blind. Only a positive
  `beam_on: false` from a fresh file pauses; only a positive `beam_on: true` releases.
  (`beam_on` already carries the monitor's own 180 s no-pulse debounce, so a single missed
  pulse does not flip it; CONFIRM_READS adds one more read of margin on top.)

Usage
  .venv/bin/python beam_gate.py &            # alongside a beam run
  .venv/bin/python beam_gate.py --dry-run    # log decisions, touch nothing
  .venv/bin/python beam_gate.py --status     # one-shot: what would it do right now?
"""
import argparse
import json
import os
import signal
import sys
import time
from datetime import datetime

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
BEAM_STATE = os.path.join(REPO_ROOT, 'config', 'beam_state.json')
PAUSE_FLAG = os.path.join(REPO_ROOT, '.pause_run')        # must match daq_control.PAUSE_FLAG
HOLD_MARKER = os.path.join(REPO_ROOT, '.beam_gate_hold')  # our ownership sidecar
STOP_RUN_FLAG = os.path.join(REPO_ROOT, '.stop_run')

POLL_S = 10.0
STALE_S = 180.0      # beam_state older than this -> the monitor is down -> UNKNOWN
CONFIRM_READS = 2    # consecutive agreeing reads before we act

ON, OFF, UNKNOWN = 'ON', 'OFF', 'UNKNOWN'


def log(msg):
    print(f'[beam_gate {datetime.now():%H:%M:%S}] {msg}', flush=True)


def read_beam_state(path=BEAM_STATE, now=None):
    """-> (ON|OFF|UNKNOWN, detail string). Never raises."""
    try:
        with open(path) as f:
            st = json.load(f)
    except FileNotFoundError:
        return UNKNOWN, 'beam_state.json missing'
    except Exception as e:
        return UNKNOWN, f'beam_state.json unreadable ({e})'

    ts = st.get('timestamp')
    age = None
    if ts:
        try:
            age = (now or datetime.now()) - datetime.fromisoformat(ts)
            age = age.total_seconds()
        except Exception:
            age = None
    if age is not None and age > STALE_S:
        return UNKNOWN, f'beam_state stale by {age:.0f} s (monitor down?)'

    on = st.get('beam_on')
    if on is None:
        return UNKNOWN, 'beam_on: null (monitor could not determine state)'

    sp = st.get('seconds_since_pulse')
    e10 = st.get('last_pulse_e10')
    detail = (f'last pulse {sp:.0f} s ago' if isinstance(sp, (int, float)) else 'no pulse info')
    if isinstance(e10, (int, float)):
        detail += f', {e10:.0f}e10'
    return (ON if on else OFF), detail


def i_hold():
    return os.path.exists(HOLD_MARKER)


def acquire(dry_run=False):
    """Pause the run. Returns True if WE now hold it."""
    if i_hold():
        # daq_control clears PAUSE_FLAG unconditionally when a run STARTS
        # (daq_control.py:197, "never start a run already paused"), which would
        # silently drop our hold while the sidecar survives. Re-assert it.
        if not os.path.exists(PAUSE_FLAG) and not dry_run:
            with open(PAUSE_FLAG, 'w') as f:
                f.write('beam_gate: beam is off — holding at the sub-run boundary\n')
            log('  RE-ASSERTED .pause_run (something cleared it while we still hold)')
        return True
    if os.path.exists(PAUSE_FLAG):
        log('  beam is OFF but .pause_run is already held by someone else '
            '(operator, or daq_control retrying an n1081b apply) — NOT claiming it, '
            'so we can never release their hold')
        return False
    if dry_run:
        log('  [dry-run] would create .pause_run + .beam_gate_hold')
        return False
    with open(PAUSE_FLAG, 'w') as f:
        f.write('beam_gate: beam is off — holding at the sub-run boundary\n')
    with open(HOLD_MARKER, 'w') as f:
        f.write(f'{datetime.now().isoformat()}\n')
    log('  HELD  .pause_run — the run will wait at the next sub-run boundary')
    return True


def release(dry_run=False, reason='beam is back'):
    """Drop only a hold we set."""
    if not i_hold():
        return False
    if dry_run:
        log('  [dry-run] would remove .pause_run + .beam_gate_hold')
        return False
    for p in (PAUSE_FLAG, HOLD_MARKER):
        try:
            os.remove(p)
        except FileNotFoundError:
            pass
    log(f'  RELEASED .pause_run ({reason}) — the run continues')
    return True


def main():
    ap = argparse.ArgumentParser(description='Hold the DAQ at sub-run boundaries while beam is off')
    ap.add_argument('--poll', type=float, default=POLL_S)
    ap.add_argument('--dry-run', action='store_true', help='log decisions, touch no flags')
    ap.add_argument('--status', action='store_true', help='one-shot report, then exit')
    ap.add_argument('--stop-with-run', action='store_true',
                    help='exit when .stop_run appears (releasing our hold first)')
    args = ap.parse_args()

    state, detail = read_beam_state()
    if args.status:
        held = 'ours' if i_hold() else ('someone else' if os.path.exists(PAUSE_FLAG) else 'none')
        print(f'beam      : {state}  ({detail})')
        print(f'.pause_run: {held}')
        print(f'action    : ' + {
            OFF: 'would HOLD' if held == 'none' else ('already holding' if held == 'ours'
                                                      else 'someone else holds it — hands off'),
            ON: 'would RELEASE' if held == 'ours' else 'nothing to do',
            UNKNOWN: 'hold current state (UNKNOWN is not OFF)'}[state])
        return 0

    if i_hold():
        log('adopting a .beam_gate_hold left by a previous instance')

    stopping = {'now': False}

    def _bye(signum, _frame):
        stopping['now'] = True
    signal.signal(signal.SIGINT, _bye)
    signal.signal(signal.SIGTERM, _bye)

    log(f'watching {os.path.relpath(BEAM_STATE, REPO_ROOT)} every {args.poll:g} s'
        f'{" [DRY RUN]" if args.dry_run else ""}')
    log(f'beam is {state} ({detail})')

    last, streak = None, 0
    try:
        while not stopping['now']:
            state, detail = read_beam_state()
            streak = streak + 1 if state == last else 1
            if state != last:
                log(f'beam -> {state} ({detail})')
            last = state

            if streak >= CONFIRM_READS:
                if state == OFF:
                    acquire(args.dry_run)
                elif state == ON:
                    release(args.dry_run)
                # UNKNOWN: deliberately do nothing -- hold whatever state we are in

            if args.stop_with_run and os.path.exists(STOP_RUN_FLAG):
                log('.stop_run seen — exiting')
                break

            slept = 0.0
            while slept < args.poll and not stopping['now']:
                time.sleep(0.25)
                slept += 0.25
    finally:
        # never leave the run pinned by us
        if release(args.dry_run, reason='beam_gate exiting'):
            pass
        elif i_hold():
            log('!! could not release our hold — remove .pause_run and .beam_gate_hold by hand')
        log('stopped')
    return 0


if __name__ == '__main__':
    sys.exit(main())
