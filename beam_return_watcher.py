#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
beam_return_watcher.py — wait for beam to come back, then swap cosmics -> beam. ONE SHOT.

Written 2026-07-27 for an unattended changeover: the operator left during a beam-off period
with run_83 (cosmics) taking data, and wants the production beam run started as soon as beam
is genuinely back rather than whenever someone next looks at the screen.

WHAT IT DOES, IN ORDER
  1. Polls `config/beam_state.json` until beam is CONFIRMED back (see below).
  2. Stops the running cosmic run via `bash_scripts/stop_run.sh`, and WAITS for daq_control
     to actually exit — `switch_mode.py` refuses to touch the trigger while a run is live,
     and that guard is the one that matters.
  3. Runs `./switch_mode.py beam --start`, which re-triggers (scint --singles --ps-pickup),
     reads the routing back and CHECKS it, reports the M4.D in0 PS delay, and launches the
     beam config.
  4. Starts `beam_gate.py` alongside it, so if the beam is flaky the run will not start a
     sub-run into a gap.
  5. Exits. It never runs a second time.

WHY "CONFIRMED" AND NOT JUST beam_on
  `beam_on` already carries the monitor's own no-pulse debounce, but a single flicker at the
  start of a fill would still trip it. So we require beam_on true AND a pulse fresher than
  PULSE_FRESH_S, on CONFIRM_READS consecutive polls. `beam_on: null`, a stale beam_state, or
  an unreadable file all count as NOT-back (the opposite of beam_gate.py's rule, deliberately:
  here the failure mode of acting too early is a run full of empty sub-runs that are still
  marked complete, so ambiguity must block, not proceed).

SAFETY
  * one-shot: it exits after acting, and refuses to act twice
  * it will only stop a run whose name matches EXPECT_RUN (default run_83) unless --any-run
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

POLL_S = 20.0
PULSE_FRESH_S = 60.0     # a pulse older than this is not "running beam"
STALE_S = 180.0          # beam_state older than this -> monitor down -> treat as not back
CONFIRM_READS = 3        # consecutive good reads before we act (~60 s)
STOP_TIMEOUT_S = 420.0   # how long to wait for daq_control to exit after stop_run
EXPECT_RUN = 'run_83'


def log(msg):
    print(f'[beam_return {datetime.now():%Y-%m-%d %H:%M:%S}] {msg}', flush=True)


def beam_is_back():
    """-> (bool, detail). Anything ambiguous returns False: acting early is the costly error."""
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
            if age > STALE_S:
                return False, f'beam_state stale by {age:.0f}s (monitor down?)'
        except Exception:
            pass

    on = st.get('beam_on')
    if on is None:
        return False, 'beam_on: null (UNKNOWN — not treated as back)'
    if not on:
        sp = st.get('seconds_since_pulse')
        return False, (f'beam off, last pulse {sp:.0f}s ago' if isinstance(sp, (int, float))
                       else 'beam off')

    sp = st.get('seconds_since_pulse')
    if not isinstance(sp, (int, float)):
        return False, 'beam_on true but no pulse age reported'
    if sp > PULSE_FRESH_S:
        return False, f'beam_on true but last pulse {sp:.0f}s ago (>{PULSE_FRESH_S:.0f})'

    e10 = st.get('last_pulse_e10')
    p10 = st.get('pulses_10min')
    return True, (f'last pulse {sp:.0f}s ago'
                  + (f', {e10:.0f}e10' if isinstance(e10, (int, float)) else '')
                  + (f', {p10} pulses/10min' if p10 is not None else ''))


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


def do_changeover(dry, any_run):
    run = current_run_name()
    pids = live_run_pids()
    log(f'current run state: {run!r}; daq_control pids: {pids or "none"}')

    if pids:
        if run != EXPECT_RUN and not any_run:
            log(f'!! ABORT: a run is live but it is {run!r}, not {EXPECT_RUN!r}. '
                f'Refusing to stop a run I was not told about. Re-run with --any-run to override.')
            return 2
        log(f'stopping the cosmic run ({run})...')
        rc, _ = sh(['bash', STOP_RUN], dry, 'stop the run')
        if rc != 0:
            log(f'!! ABORT: stop_run.sh exited {rc}')
            return 3
        if not dry:
            t0 = time.time()
            while live_run_pids() and time.time() - t0 < STOP_TIMEOUT_S:
                time.sleep(5)
            if live_run_pids():
                log(f'!! ABORT: daq_control still running {STOP_TIMEOUT_S:.0f}s after stop_run. '
                    f'Not touching the trigger while a run is live. Sort it out by hand.')
                return 4
            log(f'daq_control exited after {time.time()-t0:.0f}s')
    else:
        log('no run live — nothing to stop, going straight to the changeover')

    log('switching trigger to BEAM and launching the production run...')
    rc, out = sh([PY, SWITCH, 'beam', '--start'], dry, 'switch_mode.py beam --start')
    if rc != 0:
        log(f'!! switch_mode.py exited {rc} — the trigger may NOT be in beam state. '
            f'DO NOT assume the run is good; check by hand.')
        return 5

    log('starting beam_gate.py alongside the run (holds .pause_run across beam-off)')
    if not dry:
        with open(GATE_LOG, 'a') as gl:
            p = subprocess.Popen([PY, GATE, '--poll', '10'], cwd=REPO,
                                 stdout=gl, stderr=subprocess.STDOUT,
                                 start_new_session=True)
        with open(os.path.join(REPO, '.beam_gate.pid'), 'w') as f:
            f.write(str(p.pid) + '\n')
        log(f'  beam_gate pid {p.pid} (log: logs/beam_gate.log)')

    log('CHANGEOVER COMPLETE — beam run launched. Verify:')
    log('  tmux capture-pane -p -J -t daq_control | tail -20')
    log('  grep -H -E "Main_Trig_OvrWrn|InterPacket" ~/july_dream/dream_run/run_84/*/Tcm_Mx17_July.cfg')
    return 0


def main():
    ap = argparse.ArgumentParser(description='Wait for beam, then swap cosmics -> beam (one shot)')
    ap.add_argument('--poll', type=float, default=POLL_S)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--status', action='store_true', help='report beam state and exit')
    ap.add_argument('--any-run', action='store_true',
                    help=f'stop whatever run is live, not just {EXPECT_RUN}')
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

    log(f'watching for beam return{" [DRY RUN]" if args.dry_run else ""} — '
        f'need beam_on + pulse < {PULSE_FRESH_S:.0f}s on {CONFIRM_READS} consecutive polls '
        f'({args.poll:g}s apart)')
    log(f'will stop {EXPECT_RUN} and launch the beam config via switch_mode.py')
    log(f'beam now: {"BACK" if back else "not back"} ({detail})')

    if args.now:
        return do_changeover(args.dry_run, args.any_run)

    streak, last_detail = 0, None
    while not stopping['now']:
        back, detail = beam_is_back()
        streak = streak + 1 if back else 0
        if detail != last_detail:
            log(f'{"BACK" if back else "waiting"}: {detail}'
                + (f'  [{streak}/{CONFIRM_READS}]' if back else ''))
            last_detail = detail
        if streak >= CONFIRM_READS:
            log(f'*** BEAM CONFIRMED BACK ({detail}) — starting changeover ***')
            return do_changeover(args.dry_run, args.any_run)
        slept = 0.0
        while slept < args.poll and not stopping['now']:
            time.sleep(0.5)
            slept += 0.5
    log('stopped by signal before beam returned — no changeover performed')
    return 1


if __name__ == '__main__':
    sys.exit(main())
