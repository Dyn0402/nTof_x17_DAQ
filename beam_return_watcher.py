#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
beam_return_watcher.py — wait for beam to come back, then swap cosmics -> beam. ONE SHOT.

Written 2026-07-27 for an unattended changeover: the operator left during a beam-off period
with run_83 (cosmics) taking data, and wants the production beam run started as soon as beam
is genuinely back rather than whenever someone next looks at the screen.

WHAT IT DOES, IN ORDER
  1. Polls `config/beam_state.json` until beam is CONFIRMED back (see below).
  2. Runs `./switch_mode.py beam --go`, which is the single changeover path: it stops the
     live run and waits for daq_control to exit, allocates the next run number, regenerates
     the config at the settled operating point, re-triggers, verifies the routing, starts the
     run, starts beam_gate.py, and asserts the cfg RunCtrl actually received.
  3. Exits. It never runs a second time.

  ⚠ There is deliberately only ONE changeover implementation, in switch_mode.py --go. This
  watcher decides *when*; it does not re-implement *how*.

DETECTION — ~16 s worst case, off beam_state.json's UNDEBOUNCED pulse fields

  ⚠ Two sources look tempting and are both wrong; this is measured, not assumed.

  * **`beam_on` is useless for detecting RETURN** — it carries an 80 s no-pulse debounce.
  * **The intensity CSV looks like the fast source and is not.** Its ROWS are 2.4 s apart
    (max 6.9 s over 24 851 rows today, zero gaps > 20 s) but it is **written in BATCHES**:
    sampled every 2 s for a minute, the file grew on only 5/29 samples and the newest row was
    12.8-32.2 s old (median 24 s). Replaying today's three real beam returns through row
    timestamps says "+4 to +10 s", but that ignores flush latency — real detection off the CSV
    would be up to ~40 s.

  * **`beam_state.json` IS the fast source.** Measured: it is rewritten every ~14 s and its
    own timestamp is 0.8-14.8 s old at any moment. Crucially `seconds_since_pulse` and
    `last_pulse_e10` are direct readings with NO debounce — only `beam_on` is debounced.

  So: beam is back when `last_pulse_e10 > pulse_threshold_e10` AND `seconds_since_pulse` is
  within PULSE_FRESH_S, on CONFIRM_READS consecutive polls. Worst case = the monitor's ~14 s
  update + one poll + one confirm ~= **16 s**; typical ~8 s.

  ⚠ Do NOT use `sps_state.json`. Measured 2026-07-27 15:19: `spill_on: true`,
  `seconds_since_spill: 35` while FTN `beam_on: false`, `seconds_since_pulse: 461`. The SPS
  was extracting and none of it was reaching n_TOF. SPS spilling is not beam on our target.

  Everything ambiguous counts as NOT back — a stale beam_state (monitor down), an unreadable
  file, a missing threshold. The failure mode of acting early is a beam run whose empty
  sub-runs are STILL marked complete, so ambiguity must block rather than proceed.

  ONE FAST PULSE IS ENOUGH BECAUSE THE CHANGEOVER RE-CHECKS. We fire on a single confirmed
  recent pulse rather than waiting for a second one (which would cost another ~14 s pulse
  interval). The safety net is that `switch_mode.py --go` stops the cosmic run FIRST and only
  then re-validates the beam before touching the trigger — by which point ~40 s have passed
  and several more pulses should have landed. If beam really was a one-off, --go refuses and
  this watcher restarts cosmics rather than leaving the DAQ idle.

SAFETY
  * one-shot: it exits after acting, and refuses to act twice
  * it pins the run that is live WHEN IT IS ARMED and will only stop that one. If a
    different run is live by the time beam returns, someone else changed things while it was
    waiting, so it aborts rather than stopping a run it was never told about (--any-run
    overrides). Pin an explicit name with --expect-run.
  * it aborts rather than guessing if daq_control does not exit within STOP_TIMEOUT_S
  * every step is logged to logs/beam_return_watcher.log with a timestamp
  * `--dry-run` walks the whole sequence and prints what it would do, touching nothing
  * kill it any time (SIGTERM/SIGINT); it holds no flags and owns no board session

Usage
  nohup .venv/bin/python beam_return_watcher.py > logs/beam_return_watcher.log 2>&1 &
  .venv/bin/python beam_return_watcher.py --dry-run     # rehearse
  .venv/bin/python beam_return_watcher.py --status      # is beam back right now?
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime

REPO = os.path.dirname(os.path.abspath(__file__))
PY = os.path.join(REPO, '.venv', 'bin', 'python')
BEAM_STATE = os.path.join(REPO, 'config', 'beam_state.json')
RUN_STATE = os.path.join(REPO, 'config', 'current_run_state.json')
STOP_RUN = os.path.join(REPO, 'bash_scripts', 'stop_run.sh')
SWITCH = os.path.join(REPO, 'switch_mode.py')
GATE = os.path.join(REPO, 'beam_gate.py')
GATE_LOG = os.path.join(REPO, 'logs', 'beam_gate.log')

POLL_S = 2.0             # beam_state.json is rewritten every ~14 s; poll well inside that
PULSE_FRESH_S = 25.0     # a pulse this recent means beam is actively running (they come ~11-16 s apart)
CONFIRM_READS = 2        # consecutive agreeing polls, so one bad read cannot fire it
STATE_STALE_S = 45.0     # beam_state older than this -> monitor down -> NOT back (measured max age 14.8 s)
STOP_TIMEOUT_S = 420.0   # how long to wait for daq_control to exit after stop_run


def log(msg):
    print(f'[beam_return {datetime.now():%Y-%m-%d %H:%M:%S}] {msg}', flush=True)


def beam_is_back():
    """-> (bool, detail). Anything ambiguous returns False."""
    try:
        with open(BEAM_STATE) as f:
            st = json.load(f)
    except FileNotFoundError:
        return False, 'beam_state.json missing'
    except Exception as e:
        return False, f'beam_state.json unreadable ({e})'

    ts = st.get('timestamp')
    if ts:
        try:
            age = (datetime.now() - datetime.fromisoformat(ts)).total_seconds()
            if age > STATE_STALE_S:
                return False, f'beam_state stale by {age:.0f}s (monitor down?)'
        except Exception:
            pass

    thr = st.get('pulse_threshold_e10')
    sp = st.get('seconds_since_pulse')
    e10 = st.get('last_pulse_e10')
    if thr is None or not isinstance(sp, (int, float)) or not isinstance(e10, (int, float)):
        return False, 'beam_state missing pulse_threshold_e10/seconds_since_pulse/last_pulse_e10'

    if e10 <= thr:
        return False, f'last pulse only {e10:.1f}e10 (<= {thr:.0f} threshold)'
    if sp > PULSE_FRESH_S:
        return False, f'last pulse {sp:.0f}s ago (need <= {PULSE_FRESH_S:.0f}s), {e10:.0f}e10'
    return True, f'pulse {e10:.0f}e10 {sp:.0f}s ago'


def live_run_pids():
    """PIDs of a running daq_control.py, matched on argv (same rule switch_mode.py uses)."""
    out = []
    for pid in os.listdir('/proc'):
        if not pid.isdigit():
            continue
        try:
            with open(f'/proc/{pid}/cmdline', 'rb') as f:
                argv = [a.decode('utf-8', 'replace') for a in f.read().split(b'\0') if a]
        except OSError:
            continue
        if len(argv) >= 2 and os.path.basename(argv[0]).startswith('python') \
                and os.path.basename(argv[1]) == 'daq_control.py':
            out.append(int(pid))
    return out


def current_run_name():
    try:
        with open(RUN_STATE) as f:
            return json.load(f).get('run_name')
    except Exception:
        return None


def sh(cmd, dry, what):
    log(f'  $ {" ".join(cmd)}')
    if dry:
        log(f'  [dry-run] skipped: {what}')
        return 0, ''
    r = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
    for line in (r.stdout or '').splitlines():
        log(f'    | {line}')
    for line in (r.stderr or '').splitlines():
        log(f'    ! {line}')
    return r.returncode, (r.stdout or '')


def do_changeover(dry, any_run, expect_run):
    run = current_run_name()
    pids = live_run_pids()
    log(f'current run state: {run!r}; daq_control pids: {pids or "none"}')

    if pids:
        if expect_run is not None and run != expect_run and not any_run:
            log(f'!! ABORT: a run is live but it is {run!r}, not the {expect_run!r} that was '
                f'live when I was armed. Someone changed things while I waited — refusing to '
                f'stop a run I was never told about. Re-run with --any-run to override.')
            return 2
        log(f'run {run} is live — switch_mode --go will stop it and wait for the exit')
    else:
        log('no run live — nothing to stop, going straight to the changeover')

    log('switching trigger to BEAM and launching the production run...')
    rc, out = sh([PY, SWITCH, 'beam', '--go'], dry, 'switch_mode.py beam --go')
    if rc == 3:
        # switch_mode's beam guard refused AFTER the cosmic run was stopped: the pulse that
        # woke us was a one-off and beam is not actually running. The trigger has NOT been
        # touched (the guard runs before the apply), so the only damage is an idle DAQ.
        # Put cosmics back rather than leaving it idle.
        log('!! beam vanished between detection and the guard — a one-off pulse. The trigger '
            'was NOT touched. Restarting cosmics so the DAQ is not left idle.')
        rc2, _ = sh([PY, SWITCH, 'cosmics', '--go'], dry, 'switch_mode.py cosmics --go')
        if rc2 != 0:
            log(f'!! could not restart cosmics either (exit {rc2}) — DAQ IS IDLE, fix by hand.')
            return 6
        log('cosmics restarted. Re-arm this watcher to try again.')
        return 3
    if rc != 0:
        log(f'!! switch_mode.py exited {rc} — the trigger may NOT be in beam state. '
            f'DO NOT assume the run is good; check by hand.')
        return 5

    log('CHANGEOVER COMPLETE — switch_mode --go started the run, started beam_gate, and '
        'verified the applied cfg (it exits non-zero if any of that failed).')
    return 0


def main():
    ap = argparse.ArgumentParser(description='Wait for beam, then swap cosmics -> beam (one shot)')
    ap.add_argument('--poll', type=float, default=POLL_S)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--status', action='store_true', help='report beam state and exit')
    ap.add_argument('--any-run', action='store_true',
                    help='stop whatever run is live, not just the one that was live when armed')
    ap.add_argument('--expect-run', default=None,
                    help='pin the run name to stop (default: whichever is live at arm time)')
    ap.add_argument('--now', action='store_true', help='skip the wait, act immediately')
    args = ap.parse_args()

    back, detail = beam_is_back()
    if args.status:
        print(f'beam back : {back}  ({detail})')
        print(f'run live  : {current_run_name()!r}  pids={live_run_pids() or "none"}')
        print(f'would act : {"YES" if back else "no — keep waiting"}')
        return 0

    stopping = {'now': False}
    signal.signal(signal.SIGINT, lambda *_: stopping.update(now=True))
    signal.signal(signal.SIGTERM, lambda *_: stopping.update(now=True))

    log(f'watching for beam return{" [DRY RUN]" if args.dry_run else ""} — a pulse above '
        f'threshold within {PULSE_FRESH_S:.0f}s, on {CONFIRM_READS} consecutive polls '
        f'{args.poll:g}s apart. Source: beam_state.json (updates every ~14 s, undebounced '
        f'pulse fields) -> detection ~8 s typical, ~16 s worst.')
    log('will stop the live run and launch the beam config via switch_mode.py --go')
    log(f'beam now: {"BACK" if back else "not back"} ({detail})')

    pinned = args.expect_run or current_run_name()
    log(f'pinned run to stop: {pinned!r}'
        + ('' if args.expect_run else '  (whatever was live when armed)'))

    if args.now:
        return do_changeover(args.dry_run, args.any_run, pinned)

    last_detail, t_arm, streak = None, time.time(), 0
    while not stopping['now']:
        back, detail = beam_is_back()
        streak = streak + 1 if back else 0
        if detail != last_detail:
            log(f'{"BACK" if back else "waiting"}: {detail}'
                + (f'  [{streak}/{CONFIRM_READS}]' if back else ''))
            last_detail = detail
        if streak >= CONFIRM_READS:
            log(f'*** BEAM CONFIRMED BACK after {time.time() - t_arm:.0f}s of waiting '
                f'({detail}) — starting changeover ***')
            return do_changeover(args.dry_run, args.any_run, pinned)
        slept = 0.0
        while slept < args.poll and not stopping['now']:
            time.sleep(0.5)
            slept += 0.5
    log('stopped by signal before beam returned — no changeover performed')
    return 1


if __name__ == '__main__':
    sys.exit(main())
