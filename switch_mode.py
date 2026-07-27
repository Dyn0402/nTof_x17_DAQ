#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
switch_mode.py — one command to flip the DREAM trigger between BEAM and COSMICS.

    ./switch_mode.py beam    --go     # <- THE COMMAND. Nothing else to do, nothing to decide.
    ./switch_mode.py cosmics --go
    ./switch_mode.py status           # read-only: what is the trigger doing right now?

**`--go` IS THE WHOLE PROCEDURE.** Do not pick a run number, do not regenerate a config, do
not grep the applied cfg afterwards, do not start beam_gate by hand. `--go` does all of it and
fails loudly if any step does not verify. If you are reading this to work out what steps to
run, the answer is: one, the line above.

WHAT --go DOES, in order, with nothing left to the operator
  1. **Stops whatever run is live** and WAITS for daq_control to actually exit (up to 7 min).
     Without --go this is a hard refusal instead — stopping a run should not be a side effect
     of a command you thought was read-mostly.
  2. **Allocates the next run number** = max existing `run_N` across both run trees, + 1.
  3. **Regenerates the run config** from that mode's generator at the settled operating point
     (`OVR_WRN_HWM=1 OVR_WRN_LWM=0`, plus 15 min x 24 sub-runs for cosmics). The operating
     point lives in MODES below, in ONE place, so it cannot drift between the two directions.
  4. Refuses if another process holds an N1081B board lock.
  5. Sanity-checks the beam against the mode: beam-with-no-beam gives empty sub-runs that are
     STILL marked complete (daq_control has no beam gating), and cosmics-on-beam is not a
     cosmic run. `--force` overrides, and never overrides guards 1 or 4.
  6. Applies the routing with the same proven scripts we used by hand, one board process at a
     time (never a raw n1081b_sdk connection — see n1081b/CLAUDE.md):
         beam    -> n1081b/trigger_mode.py scint --singles --ps-pickup
         cosmics -> n1081b/setup_cosmics_singles_ungated.py
  7. **Reads the routing back and CHECKS it** against what the mode requires. Non-zero exit if
     the hardware did not land there. Also reports the M4.D in0 PS gate&delay — neither setup
     script touches it, so the 1440 ns flash framing survives a cosmic detour; this proves it
     each time instead of assuming.
  8. Launches the run.
  9. Beam only: starts `beam_gate.py`, so a flaky beam cannot fill sub-runs with nothing.
 10. **Verifies the cfg RunCtrl actually received** — Hwm 1 / Lwm 0, IPD 5, NbOfSamples 20,
     latency 0x001B — against the UNCOMMENTED `Dream * 12` line (the template carries commented
     decoys that read 0x005F and look exactly like "the override never applied"). Per-sub-run
     overrides have been silently dropped before by a stale dream_daq server, so this is not
     optional. Non-zero exit if it does not match.

WHY IT EXISTS
  Written 2026-07-27. First version replaced ~8 minutes of hand-driven steps per changeover.
  It still left the operator to stop the run, choose a run number, regenerate a config and
  verify afterwards — which in practice meant 10-15 minutes of deliberation before anyone hit
  start, which was then the slowest part of the whole changeover. Now that the operating point
  is settled (run_82: Hwm 1 / Lwm 0, IPD 5) there is nothing left to decide, so `--go` decides
  nothing and just does it. Typical warm changeover: ~60 s wall-clock, no human in the loop.

  ⚠ If the operating point ever moves, change it in MODES below and NOWHERE ELSE — both
  directions and the post-start verification all read from there.

THE ONE THING --go DOES NOT DO
  It does not wait for beam. If beam is off and you want the beam run to start the moment beam
  returns, use `beam_return_watcher.py`, which watches beam_state.json and then performs this
  same changeover unattended.
"""
import argparse
import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.abspath(__file__))
PY = os.path.join(REPO, '.venv', 'bin', 'python')
ACCESS_DIR = os.path.join(REPO, 'config', 'n1081b_access')
BEAM_STATE = os.path.join(REPO, 'config', 'beam_state.json')
START_RUN = os.path.join(REPO, 'bash_scripts', 'start_run.sh')
STOP_RUN_SH = os.path.join(REPO, 'bash_scripts', 'stop_run.sh')

# The settled operating point (run_82). --go asserts these on the cfg RunCtrl actually
# received, so a silently-dropped override cannot pass unnoticed.
EXPECT_CFG = {
    'Feu \\* Main_Trig_OvrWrnHwm': '1',
    'Feu \\* Main_Trig_OvrWrnLwm': '0',
    'Feu \\* Feu_InterPacket_Delay': '5',
    'Sys NbOfSamples': '20',
    'Feu \\* Dream \\* 12': '0x001B',      # latency 27
}
RUNS_DIR = '/mnt/data/x17/beam_july/runs'
DREAM_RUN_DIR = '/home/mx17/july_dream/dream_run'
STOP_TIMEOUT_S = 420

# A pulse older than this means "not really running beam right now".
BEAM_FRESH_S = 180

MODES = {
    'beam': {
        'blurb': 'veto-gated scint Singles + PS/flash leg (production statistics)',
        'setup': [PY, 'n1081b/trigger_mode.py', 'scint', '--singles', '--ps-pickup'],
        'expect': {'SEC_C': ('or_veto', [0]), 'SEC_D': ('or', [0, 1])},
        'config': 'run_config_stats_optimized_84.json',   # Hwm 1/Lwm 0 — the run_82 result
        'want_beam': True,
        # --go regenerates from this, so a fresh run needs no hand-editing and no
        # remembering which env vars carry the settled operating point.
        'gen': ('run_configs/run_config_stats_optimized.py',
                {'OVR_WRN_HWM': '1', 'OVR_WRN_LWM': '0'},
                'run_config_stats_optimized_{n}.json'),
        'gate': True,          # start beam_gate.py alongside a beam run
    },
    'cosmics': {
        'blurb': 'UNGATED scint Singles, veto open, PS leg dropped (beam-off cosmics)',
        'setup': [PY, 'n1081b/setup_cosmics_singles_ungated.py'],
        'expect': {'SEC_C': ('or', [0]), 'SEC_D': ('or', [1])},
        'config': 'run_config_cosmics_optimal_83.json',   # Hwm 1/Lwm 0, 0.50 MIP plastics
        'want_beam': False,
        'gen': ('run_configs/run_config_cosmics_optimal_80.py',
                {'OVR_WRN_HWM': '1', 'OVR_WRN_LWM': '0',
                 'SUBRUN_MIN': '15', 'N_SUBRUNS': '24'},
                'run_config_cosmics_optimal_{n}.json'),
        'gate': False,         # cosmics do not care about beam gaps
    },
}


CHANGEOVER_LOCK = os.path.join(REPO, 'config', '.switch_mode.lock')


def acquire_changeover_lock():
    """Single-flight: only ONE changeover may be in flight at a time, process-wide.

    Added 2026-07-27 alongside mode_watcher.py. There are now several things that can
    legitimately fire a changeover — the operator on the command line, the Flask Run Mode
    card, beam_return_watcher, mode_watcher — and two of them overlapping would be genuinely
    destructive: both would stop runs, both would allocate "the next" run number (the same
    one, from the same max+1 scan), and both would drive the same N1081B boards. The board
    flock protects individual calls but nothing previously serialised the whole sequence.

    Returns the held file object (keep it alive — closing it releases the lock), or None if
    someone else holds it. The lock dies with the process, so a crashed changeover cannot
    wedge future ones.
    """
    os.makedirs(os.path.dirname(CHANGEOVER_LOCK), exist_ok=True)
    f = open(CHANGEOVER_LOCK, 'w')
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        f.close()
        return None
    f.write(f'pid {os.getpid()} since {time.strftime("%Y-%m-%d %H:%M:%S")}\n')
    f.flush()
    return f


def read_changeover_lock():
    """Best-effort description of whoever holds the changeover lock ('' if free)."""
    try:
        with open(CHANGEOVER_LOCK) as f:
            who = f.read().strip()
    except Exception:  # noqa: BLE001
        return ''
    probe = None
    try:
        probe = open(CHANGEOVER_LOCK, 'a')
        fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(probe.fileno(), fcntl.LOCK_UN)
        return ''            # we got it -> nobody holds it
    except (BlockingIOError, OSError):
        return who or 'unknown holder'
    finally:
        if probe is not None:
            probe.close()


def existing_subruns(cfg_path):
    """How many sub-run dirs already exist in this config's run dir (0 if resume=True).

    A config with resume=True is meant to reuse its directory, so it is never flagged.
    Anything unreadable returns 0 — this is a safety hint, not a gate we want throwing.
    """
    try:
        with open(cfg_path) as f:
            cfg = json.load(f)
        if cfg.get('resume'):
            return 0
        out = cfg.get('run_out_dir')
        names = {s.get('sub_run_name') for s in cfg.get('sub_runs', [])}
        if not out or not os.path.isdir(out):
            return 0
        return sum(1 for d in os.listdir(out)
                   if d in names and os.path.isdir(os.path.join(out, d)))
    except Exception:  # noqa: BLE001
        return 0


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


def next_run_num():
    """Highest existing run_N across both run trees, + 1. No decision to make."""
    hi = 0
    for root in (RUNS_DIR, DREAM_RUN_DIR):
        try:
            names = os.listdir(root)
        except OSError:
            continue
        for n in names:
            m = re.fullmatch(r'run_(\d+)', n)
            if m:
                hi = max(hi, int(m.group(1)))
    return hi + 1


def generate_config(spec, run_num):
    """Run the mode's generator at run_num with the settled env. -> (cfg_name, ok)."""
    script, env_extra, tmpl = spec['gen']
    env = dict(os.environ, RUN_NUM=str(run_num), **env_extra)
    r = subprocess.run([PY, script], cwd=REPO, capture_output=True, text=True, env=env)
    if r.returncode != 0:
        sys.stdout.write(r.stdout or '')
        sys.stderr.write(r.stderr or '')
        return None, False
    cfg = tmpl.format(n=run_num)
    if not os.path.exists(os.path.join(REPO, 'config', 'json_run_configs', cfg)):
        print(f'!! generator ran but {cfg} is not there')
        return None, False
    return cfg, True


def stop_live_run():
    """Stop whatever is running and WAIT for daq_control to exit. -> ok."""
    live = live_run_pids()
    if not live:
        print('[stop] nothing running')
        return True
    print(f'[stop] stopping the live run ({len(live)} daq_control pid(s))')
    subprocess.run(['bash', STOP_RUN_SH], cwd=REPO, capture_output=True, text=True)
    t0 = time.time()
    while live_run_pids() and time.time() - t0 < STOP_TIMEOUT_S:
        time.sleep(3)
    if live_run_pids():
        print(f'!! daq_control still alive {STOP_TIMEOUT_S}s after stop — NOT touching the '
              f'trigger under a live run. Sort it out by hand.')
        return False
    print(f'[stop] daq_control exited after {time.time() - t0:.0f}s')
    return True


def verify_applied_cfg(run_num, timeout_s=180):
    """Assert the settled readout on the cfg RunCtrl actually got. -> ok."""
    import glob as _glob
    pat = os.path.join(DREAM_RUN_DIR, f'run_{run_num}', '*', 'Tcm_Mx17_July.cfg')
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        hits = sorted(_glob.glob(pat))
        if hits:
            break
        time.sleep(3)
    else:
        print(f'!! no applied cfg under run_{run_num} after {timeout_s}s — VERIFY BY HAND')
        return False
    cfg = hits[0]
    try:
        lines = open(cfg).read().splitlines()
    except OSError as e:
        print(f'!! could not read {cfg}: {e}')
        return False
    ok = True
    for key, want in EXPECT_CFG.items():
        # the UNCOMMENTED line only -- the template carries commented decoys
        rx = re.compile(r'^' + key + r'\s')
        got = next((ln for ln in lines if rx.match(ln)), None)
        if got is None or want not in got:
            print(f'[verify-cfg] {key.replace(chr(92)+chr(92), "")}: '
                  f'{got or "MISSING"}   want {want}  !! FAIL')
            ok = False
        else:
            print(f'[verify-cfg] {got.strip()}   OK')
    if not ok:
        print(f'!! the applied cfg does NOT match the settled operating point ({cfg}).')
        print('   Per-sub-run overrides have been silently dropped before by a stale '
              'dream_daq server. STOP THE RUN and investigate.')
    return ok


def gate_pids():
    """PIDs of any running beam_gate.py, matched on argv."""
    out = []
    for pid in os.listdir('/proc'):
        if not pid.isdigit():
            continue
        try:
            with open(f'/proc/{pid}/cmdline', 'rb') as f:
                argv = [a.decode('utf-8', 'replace') for a in f.read().split(b'\0') if a]
        except OSError:
            continue
        if any(os.path.basename(a) == 'beam_gate.py' for a in argv):
            out.append(int(pid))
    return out


def stop_beam_gate():
    """Stop any running beam_gate and WAIT for it, so it releases a hold it owns.

    ⚠ This matters most when switching TO COSMICS. beam_gate holds .pause_run whenever beam
    is off — which is exactly when a cosmic run is taking data — so a gate surviving into a
    cosmic run would stall it at its first sub-run boundary and it would never resume.
    beam_gate releases its own hold on SIGTERM, so a clean stop is enough.
    """
    pids = gate_pids()
    if not pids:
        return
    print(f'[gate]  stopping beam_gate.py {pids} — it must not outlive a beam run')
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    t0 = time.time()
    while gate_pids() and time.time() - t0 < 30:
        time.sleep(1)
    left = gate_pids()
    if left:
        print(f'[gate]  ⚠ beam_gate {left} did not exit — check .pause_run by hand')
    else:
        print('[gate]  beam_gate stopped')
    try:
        os.remove(os.path.join(REPO, '.beam_gate.pid'))
    except FileNotFoundError:
        pass


def start_beam_gate():
    gate = os.path.join(REPO, 'beam_gate.py')
    log = os.path.join(REPO, 'logs', 'beam_gate.log')
    if not os.path.exists(gate):
        return
    os.makedirs(os.path.dirname(log), exist_ok=True)
    with open(log, 'a') as gl:
        p = subprocess.Popen([PY, gate, '--poll', '10'], cwd=REPO,
                             stdout=gl, stderr=subprocess.STDOUT, start_new_session=True)
    with open(os.path.join(REPO, '.beam_gate.pid'), 'w') as f:
        f.write(str(p.pid) + '\n')
    print(f'[gate]  beam_gate.py pid {p.pid} — holds .pause_run across beam-off '
          f'(log: logs/beam_gate.log)')


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
    ap.add_argument('--go', action='store_true',
                    help='THE ONE-COMMAND CHANGEOVER: stop whatever is running, allocate the '
                         'next run number, regenerate the config at the settled operating '
                         'point, switch the trigger, start the run, start beam_gate (beam '
                         'only), and verify the applied cfg. Nothing to decide.')
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

    # ---------- guard 0: only one changeover at a time ----------
    # Held for the whole of main(); released when this process exits.
    _lock = acquire_changeover_lock()  # noqa: F841 — the handle IS the lock
    if _lock is None:
        print(f'!! REFUSING: another changeover is already in progress '
              f'({read_changeover_lock()}). Two overlapping changeovers would both stop '
              f'runs, both claim the same "next" run number, and both drive the same boards.')
        return 7
    print('[guard] changeover lock acquired — OK')

    # ---------- guard 1: nothing may be running ----------
    # --go stops it for you; without --go this stays a hard refusal, because stopping a
    # run is not something to do as a side effect of a command you thought was read-mostly.
    live = live_run_pids()
    if live and args.go:
        if not stop_live_run():
            return 2
    elif live:
        print('!! REFUSING: a run is still live — stop it first, then re-run this '
              '(or use --go, which stops it for you).')
        for pid, cmd in live:
            print(f'   pid {pid}: {cmd}')
        return 2
    else:
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

    # ---------- --go: allocate the run number and regenerate the config ----------
    go_cfg, go_run = None, None
    if args.go:
        if args.config:
            go_cfg = args.config
            print(f'[gen]   using the config you passed: {go_cfg}')
        elif 'gen' not in spec:
            print(f'!! mode "{args.mode}" has no generator spec — pass --config')
            return 6
        else:
            go_run = next_run_num()
            script = spec['gen'][0]
            env_txt = ' '.join(f'{k}={v}' for k, v in spec['gen'][1].items())
            print(f'[gen]   run_{go_run} <- {script}  ({env_txt})')
            go_cfg, ok = generate_config(spec, go_run)
            if not ok:
                print('!! config generation failed — NOTHING has been touched yet.')
                return 6
            print(f'[gen]   wrote {go_cfg}')

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
    if args.start or args.go:
        cfg = go_cfg or args.config or spec['config']
        cfg_path = os.path.join(REPO, 'config', 'json_run_configs', cfg)
        if not os.path.exists(cfg_path):
            print(f'!! run config not found: {cfg_path}')
            return 6
        # ⚠ Guard the --start path against launching INTO AN EXISTING RUN.
        # --go allocates a fresh run number so it is immune, but --start falls back to the
        # hardcoded default in MODES, which goes stale the moment --go bumps the run number.
        # On 2026-07-27 those defaults still said run_84/run_83 while the live run was 86,
        # so a bare `--start` would have written new sub-runs into a run that already held
        # 9.4 GB. A config with resume=True is explicitly ALLOWED to reuse its directory.
        if not args.go:
            occupied = existing_subruns(cfg_path)
            if occupied:
                print(f'!! REFUSING: {cfg} targets a run directory that already holds '
                      f'{occupied} sub-run(s), and the config does not set resume. '
                      f'Its default is stale — use --go (it allocates the next run number), '
                      f'or pass --config explicitly.')
                return 8
        print(f'[start] {os.path.basename(START_RUN)} {cfg}')
        subprocess.run(['bash', START_RUN, cfg], cwd=REPO, check=False)
        print(f'[start] sent to the daq_control tmux pane.')
        if args.go:
            # unconditional: for cosmics this is the fix (a surviving gate would stall the
            # run at its first boundary); for beam it stops a duplicate gate piling up.
            stop_beam_gate()
            if spec.get('gate'):
                start_beam_gate()
            if go_run is not None:
                print(f'[verify-cfg] waiting for run_{go_run} to write its applied cfg...')
                if not verify_applied_cfg(go_run):
                    return 7
            print(f'\n=== {args.mode.upper()} RUNNING as run_{go_run} — '
                  f'changeover took {time.time() - t0:.0f}s ===')
    else:
        print(f'[next]  ./bash_scripts/start_run.sh {spec["config"]}   (or re-run with --start)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
