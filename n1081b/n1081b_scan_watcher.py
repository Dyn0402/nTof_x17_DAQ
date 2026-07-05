#!/usr/bin/env python3
"""
N1081B scan watcher — synchronises .243 Section B module config with the DREAM HV
scans WITHOUT integrating into the DAQ.

Model (decided with the user): one daq_control run contains several HV scans; each
scan is a group of sub-runs whose name shares a leading scan tag (scan01, scan02, …,
produced by run_config_beam.py). Each scan wants a different N1081B .243 Section B
config (input delay on ch1&2, output enable on ch1&2) — mapped by scan tag in
config/n1081b_scan_schedule.json.

How it stays in sync, using only mechanisms the DAQ already has:
  * SIGNAL (boundary): daq_control writes `{run_out_dir}{sub_run}/.subrun_complete`
    on each successful sub-run (daq_control.py:185). The last sub-run of a scan
    completing = that scan is done. These markers are per-run-dir and only written
    on success, so they are unambiguous and stale-proof.
  * LEVER (hold): the `.pause_run` flag at the repo root. daq_control checks it at
    the top of each sub-run loop (before ramping the next sub-run) and waits while
    present. We set it the instant a scan's boundary sub-run completes (≥10 s + the
    configured boundary pause of headroom before the check), swap the module config,
    verify it latched, then clear it to release the DAQ into the next scan.
  * END: tmux pane "donzo"/"Run complete" (or the final sub-run's marker) → restore
    the baseline config and exit.

Because the watcher both SETS and CLEARS `.pause_run`, the DAQ physically cannot
start the next scan until the config change is verified. A configured boundary pause
(run_config_beam.BOUNDARY_PAUSE_MIN) is the safety floor if the watcher is down.

Only `delay` (input) and output `status` are changed; gate, monostable width and
inversion are read live from the board and preserved. Run on mx17-daq (board net).

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


def apply_module_config(sched, delay, output_status, input_status=True, dry_run=False):
    """Apply `delay` + `input_status` to the input channels and `output_status` to the
    output channels of the configured board/section, preserving every other field.
    Verifies via read-back. Returns the verified read-back (or a synthetic dict in
    dry-run). Raises on connect/login/verify failure so the caller can hold the DAQ
    paused.

    input_status is FORCED (not preserved) so it defaults back to True (input enabled,
    triggers flowing) for every ordinary scan; only a scan that explicitly sets it False
    (the no-trigger 'ctrl' baseline: output ON, input OFF) disables the input. Preserving
    it would leave the input stuck OFF for all scans after the control."""
    ip, sec_name, channels = sched['board'], sched['section'], sched['channels']
    if dry_run:
        log(f'  [dry-run] would set {sec_name} ch{channels}: input delay={delay}, '
            f'input status={input_status}, output status={output_status}')
        return {ch: {'delay': delay, 'in_status': input_status, 'status': output_status} for ch in channels}

    N1081B = _board_module()
    section = getattr(N1081B.Section, sec_name)
    d = N1081B(ip)
    if not d.connect():
        raise RuntimeError(f'connect to {ip} failed')
    try:
        d.ws.settimeout(8)
        if not d.login(sched.get('password', 'password')):
            raise RuntimeError(f'login to {ip} failed')
        verified = {}
        for ch in channels:
            cin = d.get_input_channel_configuration(section, ch)['data']
            d.set_input_channel_configuration(
                section, ch, input_status, cin['enable_gd'], cin['gate'], delay, cin['invert'])
            cout = d.get_output_channel_configuration(section, ch)['data']
            d.set_output_channel_configuration(
                section, ch, output_status, cout['enable_mono'], cout['mono_value'], cout['invert'])
            # read-back verify
            rin = d.get_input_channel_configuration(section, ch)['data']
            rout = d.get_output_channel_configuration(section, ch)['data']
            if rin['delay'] != delay or rin['status'] != input_status or rout['status'] != output_status:
                raise RuntimeError(
                    f'verify FAILED ch{ch}: wanted delay={delay}/in_status={input_status}/'
                    f'out_status={output_status}, got delay={rin["delay"]}/in_status={rin["status"]}/'
                    f'out_status={rout["status"]}')
            verified[ch] = {'delay': rin['delay'], 'in_status': rin['status'], 'status': rout['status']}
        return verified
    finally:
        try:
            d.disconnect()
        except Exception:
            pass


def apply_with_retry(sched, delay, output_status, input_status, dry_run, retries=3):
    last = None
    for attempt in range(1, retries + 1):
        try:
            v = apply_module_config(sched, delay, output_status, input_status, dry_run)
            log(f'  applied & verified: {v}')
            return True
        except Exception as e:  # noqa: BLE001
            last = e
            log(f'  !! apply attempt {attempt}/{retries} failed: {e!r}')
            time.sleep(2)
    log(f'  !! ALL apply attempts failed: {last!r}')
    return False


# ----------------------------------------------------------------------- run plan
def load_plan(schedule_path, runconfig_path):
    """Build the ordered scan plan from the active run config + schedule JSON.

    Returns (sched, plan) where plan is a list of scan dicts in order:
      {tag, cfg, boundary_marker, next_tag, is_last}
    plus 'final_marker' (last sub-run of the whole run) attached to the plan list.
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
    log(f'[scan] active={tag} delay={cfg["delay"]} out={cfg["output_status"]} '
        f'in={cfg.get("input_status", True)}')


def restore_baseline(sched, dry_run):
    b = sched['baseline']
    in_status = b.get('input_status', True)
    log('[scan] restoring baseline')
    log(f'Restoring baseline: delay={b["delay"]}, output_status={b["output_status"]}, '
        f'input_status={in_status}')
    apply_with_retry(sched, b['delay'], b['output_status'], in_status, dry_run)


def main():
    ap = argparse.ArgumentParser(description='N1081B scan watcher')
    ap.add_argument('--schedule', default=DEFAULT_SCHEDULE)
    ap.add_argument('--run-config', default=DEFAULT_RUNCONFIG)
    ap.add_argument('--poll', type=float, default=POLL_S)
    ap.add_argument('--dry-run', action='store_true', help='log actions only; no board writes / no .pause_run')
    ap.add_argument('--restore-baseline', action='store_true', help='apply baseline config and exit')
    ap.add_argument('--no-restore', action='store_true', help='do NOT restore baseline on exit')
    args = ap.parse_args()

    sched, plan, run_out_dir, final_marker = load_plan(args.schedule, args.run_config)

    if args.restore_baseline:
        restore_baseline(sched, args.dry_run)
        return 0

    # ---- print the plan for eyeballing ----
    log(f'Board {sched["board"]} {sched["section"]} ch{sched["channels"]}  '
        f'run_out_dir={run_out_dir}')
    log(f'{len(plan)} scans:')
    for p in plan:
        c = p['cfg']
        log(f'   {p["tag"]}: delay={c["delay"]} out={c["output_status"]} in={c.get("input_status", True)}  '
            f'-> boundary sub-run {p["boundary_name"]}'
            + ('  (final)' if p['is_last'] else f'  -> {p["next_tag"]}'))

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
    apply_with_retry(sched, cur['cfg']['delay'], cur['cfg']['output_status'],
                     cur['cfg'].get('input_status', True), args.dry_run)
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
                    f'Holding DAQ and switching to {nxt["tag"]} '
                    f'(delay={nxt["cfg"]["delay"]}, out={nxt["cfg"]["output_status"]}, '
                    f'in={nxt["cfg"].get("input_status", True)}).')
                log(f'[scan] switching {p["tag"]}->{nxt["tag"]}')
                set_flag(args.dry_run)
                ok = apply_with_retry(sched, nxt['cfg']['delay'], nxt['cfg']['output_status'],
                                      nxt['cfg'].get('input_status', True), args.dry_run)
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
        if not args.no_restore:
            restore_baseline(sched, args.dry_run)
        else:
            log('--no-restore: leaving board as-is.')
    log('watcher done.')
    return exit_code


if __name__ == '__main__':
    sys.exit(main())
