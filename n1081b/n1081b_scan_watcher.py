#!/usr/bin/env python3
"""
N1081B scan watcher — synchronises N1081B module config with the DREAM HV scans
WITHOUT integrating into the DAQ.

  ⚠ SUPERSEDED for data runs (2026-07-13): daq_control now applies the per-sub-run
  trigger/mesh config IN-PROCESS via n1081b/scan_control.py (which reuses the board
  primitives below), so the modulation is part of the run and can't be forgotten —
  the failure that corrupted run_30/run_33. Do NOT run this process during a
  daq_control run; both would drive the same board. Keep using it standalone for
  manual board setup, `--restore-baseline`, and `--dry-run`.

Model (decided with the user): one daq_control run contains several HV scans; each
scan is a group of sub-runs whose name shares a leading scan tag (the first
'_'-delimited token, produced by run_config_beam.py). Each scan wants a different
N1081B config, mapped by scan tag in config/n1081b_scan_schedule.json.

MULTI-SECTION (2026-07-10): the schedule may now drive SEVERAL board sections at
once via a `targets` map (e.g. mesh trigger on SEC_B input + pulser on SEC_D
outputs). Each scan tag carries a per-target override of only the fields it wants
to change (`input_status`, `delay`, `output_status`); every other field (gate,
monostable width, inversion, enables) is read live from the board and preserved.
The legacy single-section schema (`section` + `channels` + flat scan dicts) is still
accepted and mapped onto a single implicit target named "_default".

Restore-on-exit: by default the watcher SNAPSHOTS the live config of every target
channel at startup and re-applies that exact snapshot when it exits, so the board is
returned to precisely the state it was found in. Set `"restore": "baseline"` in the
schedule (plus a `baseline` block) to reset to a fixed known-good config instead.

How it stays in sync, using only mechanisms the DAQ already has:
  * SIGNAL (boundary): daq_control writes `{run_out_dir}{sub_run}/.subrun_complete`
    on each successful sub-run. The last sub-run of a scan completing = that scan is
    done. These markers are per-run-dir and only written on success, so they are
    unambiguous and stale-proof.
  * LEVER (hold): the `.pause_run` flag at the repo root. daq_control checks it at
    the top of each sub-run loop (before ramping the next sub-run) and waits while
    present. We set it the instant a scan's boundary sub-run completes, swap the
    module config, verify it latched, then clear it to release the DAQ.
  * END: the final sub-run's marker (or Stop Run) -> restore + exit.

Because the watcher both SETS and CLEARS `.pause_run`, the DAQ physically cannot
start the next scan until the config change is verified. Run on mx17-daq (board net).

Usage:
    .venv/bin/python n1081b/n1081b_scan_watcher.py            # live
    .venv/bin/python n1081b/n1081b_scan_watcher.py --dry-run  # logic only, no board/flag
    .venv/bin/python n1081b/n1081b_scan_watcher.py --restore-baseline   # reset board & exit
"""
import argparse
import json
import os
import signal
import sys
import time
from datetime import datetime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SCHEDULE = os.path.join(REPO_ROOT, 'config', 'n1081b_scan_schedule.json')
DEFAULT_RUNCONFIG = os.path.join(REPO_ROOT, 'config', 'json_run_configs', 'run_config_beam.json')
PAUSE_FLAG = os.path.join(REPO_ROOT, '.pause_run')          # must match daq_control.PAUSE_FLAG
STOP_RUN_FLAG = os.path.join(REPO_ROOT, '.stop_run')        # set by Stop Run; persists until next run start
POLL_S = 3


def log(msg):
    print(f'[{datetime.now().strftime("%H:%M:%S")}] {msg}', flush=True)


# --------------------------------------------------------------------------- board
def _board_module():
    from n1081b_sdk import N1081B  # imported lazily so --dry-run works off the board net
    return N1081B


def resolve_targets(sched):
    """Ordered {name: {'board': ip, 'section': str, 'channels': [int,...]}} from the
    schedule.

    Supports the multi-target schema (`targets`, each target optionally carrying its
    own `board` ip — MULTI-BOARD, 2026-07-11) and the legacy single-section schema
    (`section` + `channels`), which maps to one implicit target `_default`. Targets
    without a `board` use the schedule's top-level `board`.
    """
    if 'targets' in sched:
        return {name: {'board': t.get('board', sched['board']),
                       'section': t['section'], 'channels': list(t['channels'])}
                for name, t in sched['targets'].items()}
    return {'_default': {'board': sched['board'], 'section': sched['section'],
                         'channels': list(sched['channels'])}}


def _open_board(sched, ip=None):
    """Connect (+ login) to `ip` (default: the schedule's top-level board).
    Old-firmware boards serve get/set WITHOUT a login and return False from login();
    set `"require_login": false` in the schedule for those. Writes are still guarded
    by read-back verify, so proceeding login-less is safe."""
    N1081B = _board_module()
    ip = ip or sched['board']
    d = N1081B(ip)
    if not d.connect():
        raise RuntimeError(f"connect to {ip} failed")
    d.ws.settimeout(8)
    logged_in = d.login(sched.get('password', 'password'))
    if not logged_in:
        if sched.get('require_login', True):
            raise RuntimeError(f"login to {ip} failed")
        log(f"  login returned False on {ip} — proceeding "
            f"(old-fw board; get/set work without login, writes are verified by read-back)")
    return d, N1081B


def _apply_channel(d, section, ch, override):
    """Apply `override` (any subset of input_status/delay/enable_gd/gate/output_status)
    to one channel, reading + preserving every other field. Only the requested
    direction(s) are touched: an input field present -> re-write the input channel;
    output_status present -> re-write the output channel. Returns (ok, detail) where
    ok is the read-back verify result.

    NOTE: a `delay` only takes effect when the channel's Gate&Delay is enabled —
    pass `enable_gd` (+ a sane `gate` ≥ the incoming pulse width) alongside it."""
    in_keys = ('input_status', 'delay', 'enable_gd', 'gate')
    touch_in = any(k in override for k in in_keys)
    touch_out = ('output_status' in override)
    ok, detail = True, {}
    if touch_in:
        cin = d.get_input_channel_configuration(section, ch)['data']
        new_status = override.get('input_status', cin['status'])
        new_gd = override.get('enable_gd', cin['enable_gd'])
        new_gate = override.get('gate', cin['gate'])
        new_delay = override.get('delay', cin['delay'])
        d.set_input_channel_configuration(
            section, ch, new_status, new_gd, new_gate, new_delay, cin['invert'])
        rin = d.get_input_channel_configuration(section, ch)['data']
        ok = ok and (rin['status'] == new_status and rin['delay'] == new_delay
                     and rin['enable_gd'] == new_gd and rin['gate'] == new_gate)
        detail['in_status'] = rin['status']
        detail['delay'] = rin['delay']
        if 'enable_gd' in override or 'gate' in override:
            detail['gd'] = rin['enable_gd']
            detail['gate'] = rin['gate']
    if touch_out:
        cout = d.get_output_channel_configuration(section, ch)['data']
        new_status = override['output_status']
        d.set_output_channel_configuration(
            section, ch, new_status, cout['enable_mono'], cout['mono_value'], cout['invert'])
        rout = d.get_output_channel_configuration(section, ch)['data']
        ok = ok and rout['status'] == new_status
        detail['out_status'] = rout['status']
    return ok, detail


def _strip_notes(scan_cfg):
    """Drop '_'-prefixed annotation keys (e.g. _note) from a scan config."""
    return {k: v for k, v in scan_cfg.items() if not k.startswith('_')}


def apply_scan(sched, scan_cfg, dry_run=False, retries=3):
    """Apply one scan's per-target overrides to all target channels, verifying by
    read-back. `scan_cfg` = {target_name: {input_status?, delay?, enable_gd?, gate?,
    output_status?}}; '_'-prefixed keys are annotations and ignored.
    Opens one connection per board per attempt. Returns True only if every channel
    verified. Raising is avoided; the caller decides what to do with False."""
    scan_cfg = _strip_notes(scan_cfg)
    targets = resolve_targets(sched)
    if dry_run:
        for tname, ov in scan_cfg.items():
            t = targets[tname]
            log(f"  [dry-run] {tname} {t['section']} ch{t['channels']}: {ov}")
        return True
    last = None
    boards = sorted({targets[tname]['board'] for tname in scan_cfg})
    for attempt in range(1, retries + 1):
        try:
            all_ok = True
            for board in boards:
                d, N1081B = _open_board(sched, board)
                try:
                    for tname, ov in scan_cfg.items():
                        t = targets[tname]
                        if t['board'] != board:
                            continue
                        section = getattr(N1081B.Section, t['section'])
                        for ch in t['channels']:
                            ok, detail = _apply_channel(d, section, ch, ov)
                            log(f"  {tname} {board} {t['section']} ch{ch}: {detail}"
                                f"{'' if ok else '  <-- VERIFY FAILED'}")
                            all_ok = all_ok and ok
                finally:
                    try:
                        d.disconnect()
                    except Exception:
                        pass
            if not all_ok:
                raise RuntimeError('read-back verify failed for one or more channels')
            return True
        except Exception as e:  # noqa: BLE001
            last = e
            log(f'  !! apply attempt {attempt}/{retries} failed: {e!r}')
            time.sleep(2)
    log(f'  !! ALL apply attempts failed: {last!r}')
    return False


def snapshot_targets(sched):
    """Read the FULL live input+output config of every target channel. Returns
    {target: {ch: {'in': {...}, 'out': {...}}}} for exact restoration on exit."""
    targets = resolve_targets(sched)
    snap = {}
    for board in sorted({t['board'] for t in targets.values()}):
        d, N1081B = _open_board(sched, board)
        try:
            for tname, t in targets.items():
                if t['board'] != board:
                    continue
                section = getattr(N1081B.Section, t['section'])
                snap[tname] = {}
                for ch in t['channels']:
                    cin = d.get_input_channel_configuration(section, ch)['data']
                    cout = d.get_output_channel_configuration(section, ch)['data']
                    snap[tname][ch] = {'in': cin, 'out': cout}
        finally:
            try:
                d.disconnect()
            except Exception:
                pass
    return snap


def restore_snapshot(sched, snap, dry_run=False, retries=3):
    """Re-apply a full snapshot (every field, both directions) to every target channel
    and verify. Used on exit to return the board to the exact found state."""
    if dry_run:
        log('  [dry-run] would restore captured snapshot')
        return True
    targets = resolve_targets(sched)
    last = None
    boards = sorted({targets[tname]['board'] for tname in snap})
    for attempt in range(1, retries + 1):
        try:
            all_ok = True
            for board in boards:
                d, N1081B = _open_board(sched, board)
                try:
                    for tname, chans in snap.items():
                        if targets[tname]['board'] != board:
                            continue
                        section = getattr(N1081B.Section, targets[tname]['section'])
                        for ch, cfg in chans.items():
                            ci, co = cfg['in'], cfg['out']
                            d.set_input_channel_configuration(
                                section, int(ch), ci['status'], ci['enable_gd'], ci['gate'],
                                ci['delay'], ci['invert'])
                            d.set_output_channel_configuration(
                                section, int(ch), co['status'], co['enable_mono'],
                                co['mono_value'], co['invert'])
                            ri = d.get_input_channel_configuration(section, int(ch))['data']
                            ro = d.get_output_channel_configuration(section, int(ch))['data']
                            ok = (ri['status'] == ci['status'] and ri['delay'] == ci['delay']
                                  and ri['enable_gd'] == ci['enable_gd']
                                  and ri['gate'] == ci['gate']
                                  and ro['status'] == co['status'])
                            all_ok = all_ok and ok
                finally:
                    try:
                        d.disconnect()
                    except Exception:
                        pass
            if not all_ok:
                raise RuntimeError('restore read-back verify failed')
            log('  snapshot restored & verified')
            return True
        except Exception as e:  # noqa: BLE001
            last = e
            log(f'  !! restore attempt {attempt}/{retries} failed: {e!r}')
            time.sleep(2)
    log(f'  !! ALL restore attempts failed: {last!r}')
    return False


# ----------------------------------------------------------------------- summaries
def _summ(scan_cfg):
    """(delay, out, in) strings for the flask card's `[scan] active=` line, summarised
    across targets: first target that sets each field wins; 'keep' if none does."""
    delay = out = inp = 'keep'
    for ov in _strip_notes(scan_cfg).values():
        if 'delay' in ov and delay == 'keep':
            delay = str(ov['delay'])
        if 'output_status' in ov and out == 'keep':
            out = str(ov['output_status'])
        if 'input_status' in ov and inp == 'keep':
            inp = str(ov['input_status'])
    return delay, out, inp


def _fmt_cfg(scan_cfg):
    return '  '.join(f'{name}{{{", ".join(f"{k}={v}" for k, v in ov.items())}}}'
                     for name, ov in _strip_notes(scan_cfg).items())


# ----------------------------------------------------------------------- run plan
def load_plan(schedule_path, runconfig_path):
    """Build the ordered scan plan from the active run config + schedule JSON.

    Returns (sched, plan, run_out_dir, final_marker) where plan is a list of scan dicts:
      {tag, cfg, boundary_name, boundary_marker, next_tag, is_last}.
    """
    with open(schedule_path) as f:
        sched = json.load(f)
    with open(runconfig_path) as f:
        rc = json.load(f)
    run_out_dir = rc['run_out_dir']
    if not run_out_dir.endswith(os.sep):
        run_out_dir += os.sep
    sub_runs = rc['sub_runs']
    if not sub_runs:
        raise RuntimeError('run config has no sub_runs')

    # Group sub-runs by leading scan tag, preserving order.
    groups = []  # (tag, [names...])
    for sr in sub_runs:
        tag = sr['sub_run_name'].split('_')[0]
        if not groups or groups[-1][0] != tag:
            groups.append((tag, []))
        groups[-1][1].append(sr['sub_run_name'])

    # Validate every tag has a schedule entry.
    missing = [tag for tag, _ in groups if tag not in sched['scans']]
    if missing:
        raise RuntimeError(f'scan tags with no schedule entry: {missing}')

    plan = []
    for i, (tag, names) in enumerate(groups):
        is_last = (i == len(groups) - 1)
        plan.append({
            'tag': tag,
            'cfg': sched['scans'][tag],
            'boundary_name': names[-1],
            'boundary_marker': os.path.join(run_out_dir, names[-1], '.subrun_complete'),
            'next_tag': None if is_last else groups[i + 1][0],
            'is_last': is_last,
        })
    final_marker = os.path.join(run_out_dir, sub_runs[-1]['sub_run_name'], '.subrun_complete')
    return sched, plan, run_out_dir, final_marker


# ---------------------------------------------------------------------------- main
def set_flag(dry_run):
    if dry_run:
        log('  [dry-run] would create .pause_run'); return
    open(PAUSE_FLAG, 'w').close()


def clear_flag(dry_run):
    if dry_run:
        log('  [dry-run] would remove .pause_run'); return
    try:
        os.remove(PAUSE_FLAG)
    except FileNotFoundError:
        pass


def announce_active(tag, cfg):
    """Machine-parseable line the flask card keys off for the current scan."""
    delay, out, inp = _summ(cfg)
    log(f'[scan] active={tag} delay={delay} out={out} in={inp}')


def restore_baseline(sched, dry_run):
    """Reset to the schedule's fixed `baseline` config (per-target overrides)."""
    b = sched.get('baseline')
    if not b:
        log('[scan] no baseline block in schedule; nothing to reset')
        return False
    log('[scan] restoring baseline')
    log(f'Restoring baseline: {_fmt_cfg(b)}')
    return apply_scan(sched, b, dry_run)


def main():
    ap = argparse.ArgumentParser(description='N1081B scan watcher')
    ap.add_argument('--schedule', default=DEFAULT_SCHEDULE)
    ap.add_argument('--run-config', default=DEFAULT_RUNCONFIG)
    ap.add_argument('--poll', type=float, default=POLL_S)
    ap.add_argument('--dry-run', action='store_true', help='log actions only; no board writes / no .pause_run')
    ap.add_argument('--restore-baseline', action='store_true', help='apply baseline config and exit')
    ap.add_argument('--no-restore', action='store_true', help='do NOT restore on exit')
    args = ap.parse_args()

    sched, plan, run_out_dir, final_marker = load_plan(args.schedule, args.run_config)

    if args.restore_baseline:
        restore_baseline(sched, args.dry_run)
        return 0

    targets = resolve_targets(sched)

    # ---- print the plan for eyeballing ----
    tgt_desc = '  '.join(f'{n}:{t["board"].split(".")[-1]}.{t["section"]}ch{t["channels"]}'
                         for n, t in targets.items())
    log(f'Default board {sched["board"]}  targets: {tgt_desc}  run_out_dir={run_out_dir}')
    log(f'{len(plan)} scans:')
    for p in plan:
        log(f'   {p["tag"]}: {_fmt_cfg(p["cfg"])}  -> boundary sub-run {p["boundary_name"]}'
            + ('  (final)' if p['is_last'] else f'  -> {p["next_tag"]}'))

    # ---- restore strategy: snapshot live state (default) or fixed baseline ----
    restore_mode = sched.get('restore', 'snapshot')
    snap = None
    if not args.dry_run and restore_mode == 'snapshot':
        try:
            snap = snapshot_targets(sched)
            log(f'Snapshotted live config of {sum(len(c) for c in snap.values())} target '
                f'channel(s) for exact restore on exit.')
        except Exception as e:  # noqa: BLE001
            log(f'!! startup snapshot FAILED ({e!r}) — aborting so we never touch the board '
                f'without a restore path.')
            return 2
    elif restore_mode == 'snapshot':
        log('[dry-run] would snapshot live config for restore-on-exit')

    # ---- startup sync: how many scan boundaries are already complete? ----
    handled = 0
    for p in plan[:-1]:  # last scan has no actionable boundary
        if os.path.exists(p['boundary_marker']):
            handled += 1
        else:
            break
    cur = plan[min(handled, len(plan) - 1)]
    log(f'Startup: {handled} scan boundary(ies) already complete -> current scan {cur["tag"]}')
    log(f'Applying current scan config ({cur["tag"]}) now.')
    apply_scan(sched, cur['cfg'], args.dry_run)
    announce_active(cur['tag'], cur['cfg'])

    run_seen_active = handled > 0 or os.path.isdir(os.path.join(run_out_dir, plan[0]['boundary_name']))

    stop = {'flag': False}

    def _handle_sig(signum, frame):
        log(f'signal {signum} received — stopping watcher.')
        stop['flag'] = True
    signal.signal(signal.SIGINT, _handle_sig)
    signal.signal(signal.SIGTERM, _handle_sig)

    exit_code = 0
    try:
        while not stop['flag']:
            # Boundaries, strictly in order.
            for i in range(handled, len(plan) - 1):
                p = plan[i]
                if not os.path.exists(p['boundary_marker']):
                    break  # not there yet; don't look past an unfinished boundary
                run_seen_active = True
                nxt = plan[i + 1]
                log(f'BOUNDARY: {p["tag"]} complete ({p["boundary_name"]}). '
                    f'Holding DAQ and switching to {nxt["tag"]} ({_fmt_cfg(nxt["cfg"])}).')
                log(f'[scan] switching {p["tag"]}->{nxt["tag"]}')
                set_flag(args.dry_run)
                ok = apply_scan(sched, nxt['cfg'], args.dry_run)
                if not ok:
                    log('  !! module config NOT applied — LEAVING DAQ PAUSED (.pause_run held). '
                        'Will retry next poll. Fix the board or resume manually.')
                    # Do NOT clear the flag and do NOT advance handled: retry next poll.
                    break
                clear_flag(args.dry_run)
                handled = i + 1
                log(f'  released DAQ into {nxt["tag"]}.')
                announce_active(nxt['tag'], nxt['cfg'])

            # Completion / manual stop (both stale-proof filesystem signals):
            if os.path.exists(final_marker):
                log('Final sub-run complete — run finished.')
                break
            if run_seen_active and os.path.exists(STOP_RUN_FLAG):
                log('Stop Run detected (.stop_run) after run was active — ending watcher.')
                break

            time.sleep(args.poll)
    finally:
        if args.no_restore:
            log('--no-restore: leaving board as-is.')
        elif snap is not None:
            log('[scan] restoring snapshot (returning board to found state)')
            restore_snapshot(sched, snap, args.dry_run)
        else:
            restore_baseline(sched, args.dry_run)
    log('watcher done.')
    return exit_code


if __name__ == '__main__':
    sys.exit(main())
