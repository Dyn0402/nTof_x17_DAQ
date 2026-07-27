#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
switch_mode.py — one command to flip the DREAM trigger between BEAM and COSMICS.

Written 2026-07-27 after the midday beam stop, where each changeover cost ~8 minutes of
hand-driven steps (run the setup script, read back the status, eyeball it, remember which
run config to launch, remember to check the beam) and the beam-off/beam-on windows were
partly wasted. Everything below is the same sequence, in one call, with the checks that
were previously done by hand done by the script instead.

    ./switch_mode.py cosmics --start      # beam just went away: retrigger + launch cosmics
    ./switch_mode.py beam    --start      # beam is back:        retrigger + launch the stats run
    ./switch_mode.py status               # read-only: what is the trigger doing right now?

WHAT IT ACTUALLY DOES
  1. Refuses to touch anything while a run is live (`daq_control.py` in the process table)
     or while another process holds an N1081B lock. This is the guard that matters: a
     mid-run trigger change silently corrupts the run you are already taking.
  2. Sanity-checks the beam against the mode — starting a beam run with the beam off gives
     you empty sub-runs that are STILL marked complete (daq_control has no beam gating), and
     a cosmic run with beam on is not a cosmic run. Override with --force if you mean it.
  3. Applies the routing by calling the SAME proven scripts we already use by hand, in
     sequence, one board process at a time (never a raw n1081b_sdk connection — see
     n1081b/CLAUDE.md):
         beam    -> n1081b/trigger_mode.py scint --singles --ps-pickup
         cosmics -> n1081b/setup_cosmics_singles_ungated.py
  4. Reads the routing back through `trigger_mode.py status` and CHECKS IT against what the
     mode requires, rather than printing it for a human to squint at. Non-zero exit if the
     hardware did not land where it should.
  5. Reports the M4.D in0 PS gate&delay. Neither setup script touches it, so the flash
     framing (1440 ns at latency 27) survives a cosmic detour untouched — this just proves
     it each time instead of assuming.
  6. With --start, launches the mode's run config through bash_scripts/start_run.sh.

⚠ It does NOT stop a running run. Stopping is the operator's action, deliberately: the
  script's first guard is that nothing is running. Stop the run yourself, then call this.

CONFIGS — the defaults below are the current production pair. Point elsewhere with
  --config, and bump the beam run number with RUN_NUM when starting a fresh run:
      RUN_NUM=82 .venv/bin/python run_configs/run_config_stats_optimized.py
      ./switch_mode.py beam --start --config run_config_stats_optimized_82.json
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.abspath(__file__))
PY = os.path.join(REPO, '.venv', 'bin', 'python')
ACCESS_DIR = os.path.join(REPO, 'config', 'n1081b_access')
BEAM_STATE = os.path.join(REPO, 'config', 'beam_state.json')
START_RUN = os.path.join(REPO, 'bash_scripts', 'start_run.sh')

# A pulse older than this means "not really running beam right now".
BEAM_FRESH_S = 180

MODES = {
    'beam': {
        'blurb': 'veto-gated scint Singles + PS/flash leg (production statistics)',
        'setup': [PY, 'n1081b/trigger_mode.py', 'scint', '--singles', '--ps-pickup'],
        'expect': {'SEC_C': ('or_veto', [0]), 'SEC_D': ('or', [0, 1])},
        'config': 'run_config_stats_optimized_81.json',
        'want_beam': True,
    },
    'cosmics': {
        'blurb': 'UNGATED scint Singles, veto open, PS leg dropped (beam-off cosmics)',
        'setup': [PY, 'n1081b/setup_cosmics_singles_ungated.py'],
        'expect': {'SEC_C': ('or', [0]), 'SEC_D': ('or', [1])},
        'config': 'run_config_cosmics_optimal_80.json',
        'want_beam': False,
    },
}


def _run(cmd, **kw):
    return subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, **kw)


def live_run_pids():
    """PIDs of a running daq_control.py, matched on argv rather than a loose pgrep."""
    out = []
    for pid in os.listdir('/proc'):
        if not pid.isdigit():
            continue
        try:
            with open(f'/proc/{pid}/cmdline', 'rb') as f:
                argv = f.read().split(b'\0')
        except OSError:
            continue
        argv = [a.decode('utf-8', 'replace') for a in argv if a]
        if len(argv) >= 2 and os.path.basename(argv[0]).startswith('python') \
                and os.path.basename(argv[1]) == 'daq_control.py':
            out.append((int(pid), ' '.join(argv[1:])))
    return out


def board_holders():
    if not os.path.isdir(ACCESS_DIR):
        return []
    held = []
    for fn in sorted(os.listdir(ACCESS_DIR)):
        if fn.endswith('.holder.json'):
            try:
                with open(os.path.join(ACCESS_DIR, fn)) as f:
                    held.append((fn, json.load(f)))
            except Exception:  # noqa: BLE001
                held.append((fn, None))
    return held


def beam_now():
    """(beam_on, seconds_since_pulse, note). beam_on None = UNKNOWN, not off."""
    try:
        with open(BEAM_STATE) as f:
            st = json.load(f)
    except Exception as e:  # noqa: BLE001
        return None, None, f'could not read beam_state.json ({e})'
    return st.get('beam_on'), st.get('seconds_since_pulse'), None


def read_trigger():
    """Parse `trigger_mode.py status` -> {'SEC_C': (fn, [lemos]), ...} plus raw text."""
    r = _run([PY, 'n1081b/trigger_mode.py', 'status'])
    txt = (r.stdout or '') + (r.stderr or '')
    secs = {}
    for m in re.finditer(r'(SEC_[CD]):\s*fn=(\S+)\s*lemos=\[([0-9,\s]*)\]', txt):
        lemos = [int(x) for x in m.group(3).split(',') if x.strip()]
        secs[m.group(1)] = (m.group(2), lemos)
    return secs, txt, r.returncode


def matches(secs, spec):
    """True if the read-back sections are exactly what this mode requires."""
    return all(secs.get(sec) == (fn, lemos) for sec, (fn, lemos) in spec['expect'].items())


def describe(secs):
    for name, spec in MODES.items():
        if matches(secs, spec):
            return f'matches mode: {name}  ({spec["blurb"]})'
    return 'does not match either configured mode'


def read_ps_delay():
    r = _run([PY, 'n1081b/set_ps_trigger_delay.py', '--show'])
    txt = (r.stdout or '') + (r.stderr or '')
    m = re.search(r'delay=(\d+)', txt)
    return (int(m.group(1)) if m else None), txt.strip()


def main():
    ap = argparse.ArgumentParser(
        description='Flip the DREAM trigger between beam and cosmics in one command.')
    ap.add_argument('mode', choices=['beam', 'cosmics', 'status'])
    ap.add_argument('--start', action='store_true',
                    help="launch the mode's run config once the trigger verifies")
    ap.add_argument('--config', help='override the run config json for --start')
    ap.add_argument('--force', action='store_true',
                    help='proceed despite a beam-state mismatch (never overrides the '
                         'live-run or board-lock guards)')
    args = ap.parse_args()

    t0 = time.time()

    # ---------- status: read-only, always safe ----------
    if args.mode == 'status':
        secs, txt, _ = read_trigger()
        print(txt.rstrip())
        delay, dtxt = read_ps_delay()
        print(dtxt)
        print('\n=> ' + describe(secs))
        return 0

    spec = MODES[args.mode]
    print(f'=== switch to {args.mode.upper()}: {spec["blurb"]} ===')

    # ---------- guard 1: nothing may be running ----------
    live = live_run_pids()
    if live:
        print('!! REFUSING: a run is still live — stop it first, then re-run this.')
        for pid, cmd in live:
            print(f'   pid {pid}: {cmd}')
        return 2
    print('[guard] no daq_control.py running — OK')

    # ---------- guard 2: boards must be free ----------
    held = board_holders()
    if held:
        print('!! REFUSING: another process holds an N1081B board lock:')
        for fn, info in held:
            print(f'   {fn}: {info}')
        return 2
    print('[guard] no N1081B holder — OK')

    # ---------- guard 3: beam must match the mode ----------
    on, since, note = beam_now()
    if note:
        print(f'[beam] ⚠ {note}')
    fresh = (since is not None and since <= BEAM_FRESH_S)
    desc = f'beam_on={on} last pulse {since:.0f}s ago' if since is not None else f'beam_on={on}'
    if spec['want_beam'] and not (on is True and fresh):
        print(f'[beam] ⚠ mode "beam" wants live beam but {desc}.')
        print('       daq_control has NO beam gating: empty sub-runs would still be marked '
              'complete, and a later resume would skip them forever.')
        if not args.force:
            print('!! REFUSING — wait for a real pulse, or pass --force.')
            return 3
        print('       --force given, proceeding anyway.')
    elif not spec['want_beam'] and on is True and fresh:
        print(f'[beam] ⚠ mode "cosmics" but the beam looks live ({desc}).')
        if not args.force:
            print('!! REFUSING — pass --force if you really want ungated running on beam.')
            return 3
        print('       --force given, proceeding anyway.')
    else:
        print(f'[beam] {desc} — consistent with mode "{args.mode}"')

    # ---------- apply ----------
    print(f'[apply] {" ".join(os.path.basename(c) for c in spec["setup"][1:])}')
    r = _run(spec['setup'])
    sys.stdout.write(r.stdout or '')
    if r.stderr:
        sys.stderr.write(r.stderr)
    if r.returncode != 0:
        print(f'!! setup script exited {r.returncode} — NOT starting anything.')
        return 4

    # ---------- verify against what the mode requires ----------
    secs, txt, _ = read_trigger()
    ok = True
    for sec, (want_fn, want_lemos) in spec['expect'].items():
        got = secs.get(sec)
        if got is None:
            print(f'[verify] {sec}: NO READBACK  !! FAIL')
            ok = False
            continue
        good = (got[0] == want_fn and got[1] == want_lemos)
        ok &= good
        print(f'[verify] {sec}: fn={got[0]} lemos={got[1]}  '
              f'(want fn={want_fn} lemos={want_lemos})  {"OK" if good else "!! FAIL"}')
    if not ok:
        print('!! trigger did NOT land in the requested state — NOT starting anything.')
        print(txt.rstrip())
        return 5

    delay, dtxt = read_ps_delay()
    print(f'[verify] M4.D in0 PS delay = {delay} ns '
          f'(untouched by either setup script; beam mode needs 1440 at latency 27)')

    print(f'[done] trigger is in {args.mode.upper()} state in {time.time() - t0:.0f}s')

    # ---------- optionally launch ----------
    if args.start:
        cfg = args.config or spec['config']
        cfg_path = os.path.join(REPO, 'config', 'json_run_configs', cfg)
        if not os.path.exists(cfg_path):
            print(f'!! run config not found: {cfg_path}')
            return 6
        print(f'[start] {os.path.basename(START_RUN)} {cfg}')
        subprocess.run(['bash', START_RUN, cfg], cwd=REPO, check=False)
        print(f'[start] sent to the daq_control tmux pane — watch it there.')
    else:
        print(f'[next]  ./bash_scripts/start_run.sh {spec["config"]}   (or re-run with --start)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
