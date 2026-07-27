#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mode_watcher.py — keep the DAQ on whichever trigger the beam justifies, in BOTH directions.

    nohup .venv/bin/python mode_watcher.py > logs/mode_watcher.log 2>&1 &
    .venv/bin/python mode_watcher.py --status      # what would it do right now?
    .venv/bin/python mode_watcher.py --dry-run     # full loop, decides, touches nothing

WHAT IT DOES
  * beam run + no pulse for BEAM_DOWN_MIN (default 5 min)  ->  switch to COSMICS
  * cosmics run + beam confirmed back                      ->  switch to BEAM

  Both directions go through `switch_mode.py --go`, which remains the ONE changeover
  implementation. This watcher only decides *when*; it never re-implements *how*.

WHY THE TWO DIRECTIONS ARE NOT SYMMETRIC — this is the whole design, not an oversight.
  Beam time is scarce and cosmic time is free, so the two errors do not cost the same:

    * being on cosmics while beam runs   = lost beam data, unrecoverable
    * being on beam while beam is off    = lost cosmic data, recoverable any time

  So going BACK to beam is eager (one confirmed pulse, ~8 s typical detection, no cooldown),
  and giving UP on beam is conservative (a full BEAM_DOWN_MIN with no pulse at all, plus a
  cooldown). Never delay resuming beam; do be slow to abandon it.

  ⚠ Do NOT "simplify" this by making both directions use the same threshold.

RELATIONSHIP TO beam_gate.py — they solve different halves and must both run.
  `beam_gate` holds the run at a sub-run boundary the moment beam drops, so a short gap does
  not fill a sub-run with nothing. It is the right answer for a 2-minute gap: the run stays
  parked and resumes with no changeover at all. But a parked run collects NOTHING, so for a
  long stop that parked time is pure waste — which is what this watcher recovers by trading
  the beam run for a cosmic one. Short gap -> beam_gate parks it. Long gap -> mode_watcher
  swaps it. `switch_mode.py --go` stops beam_gate as part of the changeover, so they cannot
  end up fighting over `.pause_run`.

RELATIONSHIP TO beam_return_watcher.py
  That one is a ONE-SHOT "I am leaving, start the beam run when beam returns". This is the
  standing, both-ways version. Running both at once is now SAFE but pointless — they would
  both try the same cosmics->beam changeover and the loser hits the changeover lock added to
  switch_mode.py and gives up cleanly. Prefer one or the other.

EVERYTHING AMBIGUOUS MEANS "DO NOTHING"
  A stale `beam_state.json` (monitor down), an unreadable one, `beam_on: null`, a missing
  `seconds_since_pulse` — all of these leave the DAQ exactly as it is. A changeover on bad
  information is worse than a late changeover in either direction, and a monitoring glitch
  must never be able to tear down a good beam run.

SAFETY
  * refuses to act while a changeover lock is held (someone else is mid-changeover)
  * does nothing at all when no run is live — an idle DAQ is the operator's business
  * disarm without killing it: `touch config/.mode_watcher_disarmed` (the Flask Run Mode
    card does exactly this). It keeps polling and logging, but will not act.
  * `--dry-run` walks the entire decision path and touches nothing
  * kill it any time (SIGTERM/SIGINT); it holds no flags and owns no board session
"""
import argparse
import importlib.util
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
SWITCH = os.path.join(REPO, 'switch_mode.py')
DISARM_FLAG = os.path.join(REPO, 'config', '.mode_watcher_disarmed')
STATE_FILE = os.path.join(REPO, 'config', 'mode_watcher_state.json')

POLL_S = 5.0

# BEAM_DOWN_MIN — MEASURED, not guessed. beam_monitor/analyze_stop_durations.py over
# 2026-06-30..07-27 (642 h, 130 real beam stops, 4.9/day, 18% of wall-clock):
#   * the short-hiccup population dies out sharply — 23 stops in 1-2 min, 18 in 2-3,
#     13 in 3-4, then only 4 in 4-5 and 2 in 5-6. Past ~4 min the distribution is flat
#     and long (median stop overall 8.6 min, p75 47 min, mean 53 min).
#   * conditional survival has its knee in the same place. Given the beam has been away
#     T min, P(away another 15) = 51% at T=2, 70% at T=4, 72% at T=5 — then it PLATEAUS
#     (72% at 7, 72% at 8, 79% at 10).
# So 5 min sits just past the end of the self-resolving hiccups and at the knee: below
# ~4 min we would be swapping the DAQ for stops that fix themselves (at T=2 it is a coin
# flip, and half the switches would not last one cosmic sub-run); above ~10 min we buy
# almost no extra certainty and just sit idle through real downtime.
# ⚠ Re-run the analysis after a machine schedule change — this is a property of the
# accelerator, not of our DAQ. See docs/METHOD_beam_stop_threshold.md.
BEAM_DOWN_MIN = 5.0        # no pulse for this long -> give up on beam, go cosmics
PULSE_FRESH_S = 25.0       # a pulse this recent = beam actively running (they come ~11-16 s apart)
CONFIRM_READS = 2          # consecutive agreeing polls before acting, either direction
STATE_STALE_S = 45.0       # beam_state older than this -> monitor down -> UNKNOWN
COOLDOWN_MIN = 5.0         # min time on beam before we may abandon it (beam->cosmics only)

# Reuse switch_mode's MODES / process discovery so there is exactly one definition of
# "what is a beam run" and one changeover implementation.
_spec = importlib.util.spec_from_file_location('switch_mode', SWITCH)
sm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sm)


def log(msg):
    print(f'[mode_watcher {datetime.now():%Y-%m-%d %H:%M:%S}] {msg}', flush=True)


def config_prefixes():
    """{mode: config-filename prefix} derived from switch_mode.MODES, not hardcoded."""
    out = {}
    for name, spec in sm.MODES.items():
        tmpl = spec.get('gen', (None, None, spec.get('config', '')))[2]
        out[name] = tmpl.split('{n}')[0]
    return out


def current_mode():
    """('beam'|'cosmics'|None, detail) from the LIVE run's config filename.

    Deliberately does not touch a board: a watcher polling N1081B every few seconds is
    exactly the traffic pattern n1081b/CLAUDE.md warns about.
    """
    live = sm.live_run_pids()
    if not live:
        return None, 'no run live'
    argv = live[0][1]
    for mode, pref in config_prefixes().items():
        if pref and pref in argv:
            return mode, argv
    return None, f'run live but its config matches no mode: {argv}'


def beam_view():
    """-> (state, seconds_since_pulse, detail) with state in ON/OFF/UNKNOWN.

    ON  = a pulse above threshold within PULSE_FRESH_S (undebounced -> fast to see a return)
    OFF = a valid, fresh reading whose last pulse is simply old
    UNKNOWN = anything we cannot trust
    """
    try:
        with open(BEAM_STATE) as f:
            st = json.load(f)
    except FileNotFoundError:
        return 'UNKNOWN', None, 'beam_state.json missing'
    except Exception as e:  # noqa: BLE001
        return 'UNKNOWN', None, f'beam_state.json unreadable ({e})'

    ts = st.get('timestamp')
    if ts:
        try:
            age = (datetime.now() - datetime.fromisoformat(ts)).total_seconds()
            if age > STATE_STALE_S:
                return 'UNKNOWN', None, f'beam_state stale by {age:.0f}s (monitor down?)'
        except Exception:  # noqa: BLE001
            pass

    thr = st.get('pulse_threshold_e10')
    sp = st.get('seconds_since_pulse')
    e10 = st.get('last_pulse_e10')
    if thr is None or not isinstance(sp, (int, float)):
        return 'UNKNOWN', None, 'beam_state missing pulse_threshold_e10/seconds_since_pulse'

    if isinstance(e10, (int, float)) and e10 > thr and sp <= PULSE_FRESH_S:
        return 'ON', sp, f'pulse {e10:.0f}e10 {sp:.0f}s ago'
    return 'OFF', sp, f'last pulse {sp / 60.0:.1f} min ago'


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def save_state(**kw):
    st = load_state()
    st.update(kw)
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, 'w') as f:
            json.dump(st, f, indent=1)
    except Exception as e:  # noqa: BLE001
        log(f'  (could not write state file: {e})')


def disarmed():
    return os.path.exists(DISARM_FLAG)


def decide(mode, beam, since_pulse, beam_down_min, cooldown_min, now=None):
    """Pure decision function -> (target_mode|None, reason). Unit-tested; no side effects."""
    now = now if now is not None else time.time()
    if mode is None:
        return None, 'no run live — nothing to switch'
    if beam == 'UNKNOWN':
        return None, 'beam state UNKNOWN — holding (a glitch must not trigger a changeover)'

    if mode == 'cosmics':
        if beam == 'ON':
            return 'beam', f'beam is back and we are on cosmics'
        return None, 'on cosmics, beam still off — correct place to be'

    # mode == 'beam'
    if beam == 'ON':
        return None, 'on beam, beam is running — correct place to be'
    if since_pulse is None:
        return None, 'on beam but no pulse age available — holding'
    if since_pulse < beam_down_min * 60.0:
        return None, (f'on beam, {since_pulse / 60.0:.1f} min since last pulse '
                      f'(need {beam_down_min:g} min to give up)')
    last = load_state().get('last_changeover_ts')
    if last is not None:
        age_min = (now - last) / 60.0
        if age_min < cooldown_min:
            return None, (f'beam down {since_pulse / 60.0:.1f} min BUT only {age_min:.1f} min '
                          f'since the last changeover (cooldown {cooldown_min:g} min) — '
                          f'not abandoning a run we just started')
    return 'cosmics', f'beam down {since_pulse / 60.0:.1f} min with a run on the beam trigger'


def do_changeover(target, dry):
    held = sm.read_changeover_lock()
    if held:
        log(f'  another changeover is in progress ({held}) — skipping this cycle')
        return False
    log(f'*** switching to {target.upper()} — running switch_mode.py {target} --go ***')
    if dry:
        log('  [dry-run] not executing')
        return False
    r = subprocess.run([PY, SWITCH, target, '--go'], cwd=REPO,
                       capture_output=True, text=True)
    for line in (r.stdout or '').splitlines():
        log(f'    | {line}')
    for line in (r.stderr or '').splitlines():
        log(f'    ! {line}')
    if r.returncode == 0:
        log(f'  CHANGEOVER TO {target.upper()} COMPLETE')
        save_state(last_changeover_ts=time.time(), last_target=target,
                   last_result='ok',
                   last_changeover_at=datetime.now().isoformat(timespec='seconds'))
        return True
    if r.returncode == 7:
        log('  lost the changeover lock race — someone else is doing it; fine, standing down')
        return False
    log(f'!! switch_mode.py exited {r.returncode} — the DAQ may be in NEITHER state. '
        f'Not retrying automatically; check by hand.')
    save_state(last_changeover_ts=time.time(), last_target=target,
               last_result=f'FAILED rc={r.returncode}',
               last_changeover_at=datetime.now().isoformat(timespec='seconds'))
    return False


def main():
    ap = argparse.ArgumentParser(
        description='Keep the DAQ on the trigger the beam justifies, both directions.')
    ap.add_argument('--poll', type=float, default=POLL_S)
    ap.add_argument('--beam-down-min', type=float, default=BEAM_DOWN_MIN,
                    help=f'minutes with no pulse before giving up on beam (default {BEAM_DOWN_MIN:g})')
    ap.add_argument('--cooldown-min', type=float, default=COOLDOWN_MIN,
                    help='minutes after a changeover before we may abandon beam again '
                         f'(default {COOLDOWN_MIN:g}; never delays going back TO beam)')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--status', action='store_true', help='one-shot report, then exit')
    ap.add_argument('--once', action='store_true', help='evaluate once, act, exit')
    args = ap.parse_args()

    mode, mdetail = current_mode()
    beam, since, bdetail = beam_view()
    target, reason = decide(mode, beam, since, args.beam_down_min, args.cooldown_min)

    if args.status:
        st = load_state()
        print(f'run mode  : {mode or "—"}  ({mdetail})')
        print(f'beam      : {beam}  ({bdetail})')
        print(f'armed     : {"NO — config/.mode_watcher_disarmed exists" if disarmed() else "yes"}')
        print(f'changeover: {sm.read_changeover_lock() or "lock free"}')
        print(f'last      : {st.get("last_changeover_at", "—")} -> {st.get("last_target", "—")} '
              f'({st.get("last_result", "—")})')
        print(f'action    : {("SWITCH to " + target) if target else "none"} — {reason}')
        return 0

    stopping = {'now': False}
    signal.signal(signal.SIGINT, lambda *_: stopping.update(now=True))
    signal.signal(signal.SIGTERM, lambda *_: stopping.update(now=True))

    log(f'watching{" [DRY RUN]" if args.dry_run else ""} — give up on beam after '
        f'{args.beam_down_min:g} min with no pulse (cooldown {args.cooldown_min:g} min); '
        f'return to beam on one confirmed pulse, no cooldown. Poll {args.poll:g}s, '
        f'{CONFIRM_READS} agreeing reads to act.')
    log(f'now: run mode {mode or "—"} ({mdetail}); beam {beam} ({bdetail})')
    log(f'first decision: {("SWITCH to " + target) if target else "none"} — {reason}')
    if disarmed():
        log('DISARMED (config/.mode_watcher_disarmed present) — will poll and log but not act')

    last_reason, streak, last_target = None, 0, None
    while not stopping['now']:
        mode, mdetail = current_mode()
        beam, since, bdetail = beam_view()
        target, reason = decide(mode, beam, since, args.beam_down_min, args.cooldown_min)

        streak = streak + 1 if (target and target == last_target) else (1 if target else 0)
        last_target = target

        if reason != last_reason:
            log(f'{beam} | mode={mode or "—"} | {reason}'
                + (f'  [{streak}/{CONFIRM_READS}]' if target else ''))
            last_reason = reason

        if target and streak >= CONFIRM_READS:
            if disarmed():
                if streak == CONFIRM_READS:
                    log(f'  would switch to {target.upper()} but DISARMED — not acting')
            else:
                do_changeover(target, args.dry_run)
                last_reason, streak, last_target = None, 0, None
                if args.once:
                    break

        if args.once and streak == 0:
            break

        slept = 0.0
        while slept < args.poll and not stopping['now']:
            time.sleep(0.25)
            slept += 0.25

    log('stopped')
    return 0


if __name__ == '__main__':
    sys.exit(main())
