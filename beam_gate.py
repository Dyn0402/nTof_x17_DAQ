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

IT MUST NOT OUTLIVE ITS RUN  (2026-08-06)
  A gate started for run_147 survived a manual Stop Run. daq_control clears `.pause_run`
  when a run STARTS ("never start a run already paused"), so the gate's RE-ASSERT put the
  hold straight back — onto an unrelated pedestal run that had just started. That pedestal
  sat parked at its first sub-run boundary for 11 minutes, never reached `Subrun started`,
  and left an empty directory that then crashed the next cosmics run when `pedestals:
  'latest'` resolved to it.

  So the gate now has a RUN-SCOPED LIFETIME. It latches onto the `daq_control.py` PID it
  first sees and stands down — releasing its hold — once that PID is gone. Scoping is by
  IDENTITY, not liveness: run_147 ended and the pedestal run started in the same second,
  so "is a run live?" never answered no, and only "is it still MY run?" catches it. It
  latches only after actually observing a run, so a gate started a moment before its own
  daq_control cannot exit on that startup race. `--stop` is the explicit version, used by
  the operator stop (`stop_run.sh --full`).

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
  .venv/bin/python beam_gate.py --stop       # stop every gate, release any orphaned hold
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
RUN_GONE_READS = 3   # consecutive reads with no daq_control before we stand down

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


def _pids_of(script, exclude_self=True):
    """PIDs whose argv mentions `script`, matched on basename — the same /proc discipline
    switch_mode.gate_pids() uses, so both agree on what 'running' means."""
    me = os.getpid()
    out = []
    for pid in os.listdir('/proc'):
        if not pid.isdigit():
            continue
        p = int(pid)
        if exclude_self and p == me:
            continue
        try:
            with open(f'/proc/{pid}/cmdline', 'rb') as f:
                argv = [a.decode('utf-8', 'replace') for a in f.read().split(b'\0') if a]
        except OSError:      # process exited between listdir and open
            continue
        if any(os.path.basename(a) == script for a in argv):
            out.append(p)
    return out


def run_pids():
    """PIDs of the live daq_control.py run, as a set.

    ⚠ IDENTITY, NOT LIVENESS. On 2026-08-06 run_147 ended and a pedestal run started in
    the SAME SECOND (RUN_END 14:28:51 / START 14:28:51), so "is any daq_control running?"
    never once answered no. A gate that only checked liveness would have sailed straight
    through the handover and pinned the pedestal exactly as the real one did. daq_control
    is launched fresh per run and exits with it, so its PID *is* the run's identity.

    Exact basename match, so dream_daq_control.py (a long-lived server) is not mistaken
    for a run — `pgrep -f daq_control.py` does make that mistake.
    """
    return set(_pids_of('daq_control.py'))


def stop_all(timeout=30.0):
    """Stop every other beam_gate and make sure no hold is left behind.

    Each gate releases its own hold on SIGTERM, so a clean stop is normally enough. But a
    gate that was SIGKILLed leaves `.beam_gate_hold` (and therefore `.pause_run`) with
    nobody left to release it, which pins the next run just as surely. Since this is the
    explicit operator 'get out of the way' call, adopt and release that orphan too.

    Returns the process exit status (0 = everything is clear).
    """
    pids = _pids_of('beam_gate.py')
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    if pids:
        log(f'--stop: SIGTERM to beam_gate {pids}, waiting for them to release')
        t0 = time.time()
        while _pids_of('beam_gate.py') and time.time() - t0 < timeout:
            time.sleep(0.5)
        left = _pids_of('beam_gate.py')
        if left:
            log(f'!! beam_gate {left} did not exit within {timeout:g}s — '
                f'check .pause_run and .beam_gate_hold by hand')
            return 1
        log('--stop: all beam_gates stopped')
    else:
        log('--stop: no beam_gate running')

    # Orphaned hold from a killed gate (or one that could not clean up).
    if os.path.exists(HOLD_MARKER):
        for p in (PAUSE_FLAG, HOLD_MARKER):
            try:
                os.remove(p)
            except FileNotFoundError:
                pass
        log('--stop: released an orphaned .beam_gate_hold (a gate died without cleaning up)')
    elif os.path.exists(PAUSE_FLAG):
        # Somebody else's hold — the operator's own pause, or daq_control retrying an
        # n1081b apply. Never ours to clear; say so rather than stamping on it.
        log('--stop: .pause_run exists but is NOT a beam_gate hold '
            '(operator pause, or daq_control n1081b retry) — leaving it alone')
    return 0


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
    ap.add_argument('--stop', action='store_true',
                    help='stop every running beam_gate and release any orphaned hold, then exit')
    args = ap.parse_args()

    if args.stop:
        return stop_all()

    state, detail = read_beam_state()
    if args.status:
        held = 'ours' if i_hold() else ('someone else' if os.path.exists(PAUSE_FLAG) else 'none')
        print(f'beam      : {state}  ({detail})')
        _r = sorted(run_pids())
        print(f'run       : {"daq_control pid(s) " + str(_r) if _r else "NONE — a gate here would stand down"}')
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
    my_run, gone = set(), 0
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

            # RUN-SCOPED LIFETIME — never outlive the run we were started for. We latch
            # onto the daq_control PID(s) we first see and stand down when they are gone.
            # Latching only after we have actually seen a run means a gate started a
            # moment before its own daq_control cannot exit on that startup race.
            # `finally` releases our hold on the way out.
            live = run_pids()
            if not my_run:
                if live:
                    my_run = live
                    log(f'scoped to daq_control pid(s) {sorted(my_run)} — this gate dies with it')
            elif my_run & live:
                gone = 0                      # our run is still there
            else:
                other = live - my_run
                if other:
                    # A DIFFERENT run is already up. No debounce: this is unambiguous, and
                    # every extra second is a second we could pin a run that is not ours.
                    log(f'run {sorted(my_run)} is gone and a DIFFERENT run {sorted(other)} '
                        f'is live — standing down rather than pinning it')
                    break
                gone += 1
                if gone >= RUN_GONE_READS:
                    log(f'no daq_control for {gone * args.poll:.0f}s — the run this gate was '
                        f'started for is over; standing down so it cannot pin the next one')
                    break

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
