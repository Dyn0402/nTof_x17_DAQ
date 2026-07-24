#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Autonomous SSD space watcher for nTof DREAM raw staging.

The SSD (/home/mx17/july_dream/dream_run) is the acquisition staging area for raw
.fdf files. Nothing has ever pruned it, so it fills at the raw acquisition rate
(~22 GB/hr measured during run_67) until acquisition dies on a full disk.

This watcher enforces one policy:

    keep at least `low_water_gb` free on the SSD, by deleting the OLDEST runs
    that are provably safe, and never more of them than needed.

"Provably safe" is not this file's judgement — it is space_manager.verify_ssd_run:
every raw .fdf on the SSD must also exist on the HDD at matching size, AND that
HDD run must be byte-for-byte verified against EOS. SSD -> HDD -> EOS. The actual
removal goes through space_manager.delete_run, which re-runs that entire
verification itself immediately before unlinking anything and refuses the active
run. This watcher therefore cannot delete an unbacked-up run even if its own
bookkeeping is wrong; the worst a bug here can do is ASK for the wrong run and be
refused.

On top of that, two guards live here, both about keeping recent data at hand
rather than about correctness:

  keep_recent_runs  the N newest runs are never candidates, however verified
  min_age_hours     a run must have been quiet this long to be a candidate

Deletion order is strictly oldest-run-first, so the newest data survives longest.

Those two guards are a convenience, and convenience loses to a dead DAQ. Below
`emergency_gb` free the watcher drops BOTH of them and will delete any verified
run, newest included. What it never drops is the safety proper: the active run
and the SSD -> HDD -> EOS verification are enforced by space_manager.delete_run,
which this file cannot talk its way past. So emergency mode can cost you the
convenience of a local copy of a recent run; it cannot cost you data.

What it deliberately does NOT do:
  * touch the HDD. That is a 34-hour problem, not a 5-hour one, and the HDD copy
    is what processing and QA read from. Prune it from the GUI's Disk Space tab.
  * delete anything not named run_<N> (space_manager.list_runs ignores those), so
    e.g. run_67_recon and pedestal staging are invisible to it.
  * free space by any means other than whole verified runs — no partial deletes,
    no "oldest files", no truncation.

Usage:
    python space_watcher.py <space_config_json_path>
    python space_watcher.py <config> --once       # single pass, then exit
    python space_watcher.py <config> --dry-run    # report only, delete nothing

Config keys (see space_config.py to generate the JSON):
  low_water_gb      : free-space floor that triggers a freeing pass
  target_free_gb    : free space to reach once freeing (hysteresis above the floor)
  keep_recent_runs  : number of newest runs never eligible for deletion
  min_age_hours     : minimum quiet time before a run is eligible
  emergency_gb      : free space below which keep_recent_runs and min_age_hours are
                      both ignored (EOS verification and the active run are not)
  poll_interval     : seconds between free-space checks
  retry_interval    : seconds before retrying a pass that freed nothing
  dry_run           : report what would be deleted, delete nothing
"""

import os
import sys
import json
import time
import argparse
from datetime import datetime
from pathlib import Path

import space_manager
from space_manager import human

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, 'config', 'space_watcher_state.json')

GB = 1024 ** 3


def log(msg):
    """Watcher chatter goes to stdout (the tmux pane); anything that actually
    changed the disk ALSO goes to the space_manager delete log, which is the
    durable record of every removal."""
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {msg}", flush=True)


def ssd_free() -> int:
    return space_manager.disk_usage()['ssd'].get('free', 0)


def newest_mtime(root: Path) -> float:
    """Most recent mtime under root, or 0.0 if unreadable/empty."""
    newest = 0.0
    for f in root.rglob('*'):
        try:
            if f.is_file() and not f.is_symlink():
                newest = max(newest, f.stat().st_mtime)
        except OSError:
            pass
    return newest


def emergency_bytes(cfg: dict) -> int:
    """The free-space level below which the soft guards are dropped. Clamped to the
    floor: an emergency level above low_water_gb would mean the guards are never in
    force at all, which is a config typo, not a policy."""
    return min(int(float(cfg['emergency_gb']) * GB), int(cfg['low_water_gb'] * GB))


def publish(state: str, detail: str, free: int, cfg: dict, extra=None):
    """Write the state file Flask + the Telegram monitor read.

    `state` is one of:
      ok            free space is above the floor, nothing to do
      freed         a pass just ran and got back over the target
      partial       a pass freed something but is still under the floor
      held          under the floor, but everything deletable is held by the soft
                    guards only — this releases itself at the emergency level, so
                    it is a heads-up, not the backup failure `cannot_free` is
      cannot_free   under the floor with nothing safe to delete  <-- needs a human
      dry_run       under the floor, would have deleted, but is not armed
    """
    st = {
        'timestamp':      datetime.now().isoformat(timespec='seconds'),
        'poll_s':         cfg['poll_interval'],
        'state':          state,
        'detail':         detail,
        'free_bytes':     free,
        'free_h':         human(free),
        'low_water_bytes':   int(cfg['low_water_gb'] * GB),
        'target_free_bytes': int(cfg['target_free_gb'] * GB),
        'emergency_bytes':   emergency_bytes(cfg),
        'dry_run':        bool(cfg['dry_run']),
    }
    if extra:
        st.update(extra)
    try:
        tmp = STATE_FILE + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(st, f, indent=2)
        os.replace(tmp, STATE_FILE)   # atomic: a reader never sees a half-written file
    except Exception as e:
        log(f"WARNING: could not publish state: {e}")


def candidates(cfg: dict, emergency: bool = False):
    """Runs eligible for deletion, oldest first, plus a human-readable account of
    why every other run was held back.

    In emergency mode the two soft guards (keep_recent_runs, min_age_hours) are
    skipped entirely, so the only runs held back are the active one and any run
    not verified through to EOS. Note the ordering: the safety check runs BEFORE
    the soft guards, so an unverified run reports as unverified in both modes
    rather than hiding behind "newest-N reserve".

    Each held entry is (run, reason, kind) with kind in:
      active      currently acquiring — never deletable
      unverified  not (yet) verified SSD -> HDD -> EOS — never deletable
      policy      held by a soft guard — deletable in emergency mode

    The EOS verification here is the expensive part (one xrdfs -R listing per
    run), which is why the caller only reaches this once free space is genuinely
    below the floor.
    """
    scan = space_manager.scan('ssd')
    runs = scan['runs']                      # already sorted oldest -> newest
    protect_n = 0 if emergency else max(0, int(cfg['keep_recent_runs']))
    protected = {r['run'] for r in runs[-protect_n:]} if protect_n else set()
    min_age_s = 0.0 if emergency else float(cfg['min_age_hours']) * 3600
    now = time.time()

    eligible, held = [], []
    for r in runs:
        run = r['run']
        if r.get('active'):
            held.append((run, 'active run', 'active'))
            continue
        if not r['safe']:
            held.append((run, r['reason'], 'unverified'))
            continue
        if run in protected:
            held.append((run, f'newest-{protect_n} reserve', 'policy'))
            continue
        age = now - newest_mtime(space_manager.SSD_DREAM_DIR / run)
        if age < min_age_s:
            held.append((run, f'written {age / 3600:.1f} h ago (< {cfg["min_age_hours"]} h)',
                         'policy'))
            continue
        eligible.append(r)
    return eligible, held, scan


def freeing_pass(cfg: dict) -> dict:
    """One attempt to get back over target_free_gb. Deletes oldest-first and stops
    the moment the target is reached, so no more data is destroyed than the policy
    requires."""
    target = int(cfg['target_free_gb'] * GB)
    low    = int(cfg['low_water_gb'] * GB)
    emerg  = emergency_bytes(cfg)
    free   = ssd_free()
    emergency = free < emerg

    if emergency:
        log(f"SSD at {human(free)} free — BELOW THE {human(emerg)} EMERGENCY LEVEL: "
            f"newest-run reserve and minimum age are OFF, any EOS-verified run may go")
    else:
        log(f"SSD at {human(free)} free (< floor {human(low)}) — looking for safe runs to delete")
    eligible, held, scan = candidates(cfg, emergency=emergency)

    held_pub = [{'run': r, 'reason': w, 'kind': k} for r, w, k in held]
    for run, why, _kind in held:
        log(f"  hold  {run:12} {why}")
    if not eligible:
        # Two very different failures share "nothing to delete", and they need
        # different humans: soft guards holding everything back releases itself at
        # the emergency level, whereas nothing being on EOS is a backup outage.
        if any(k == 'policy' for _r, _w, k in held):
            detail = (f'SSD below the {cfg["low_water_gb"]} GB floor, but every deletable run is '
                      f'held by the newest-{cfg["keep_recent_runs"]}/min-age guards. Backups are '
                      f'fine — these are released automatically below the '
                      f'{cfg["emergency_gb"]} GB emergency level.')
            log(f"HELD BY POLICY: {detail}")
            publish('held', detail, free, cfg, {'held': held_pub, 'deleted': []})
            return {'freed': 0, 'deleted': [], 'state': 'held'}
        detail = (f'SSD below the {cfg["low_water_gb"]} GB floor with NO run safe to delete '
                  f'({scan["n_runs"]} runs on disk). Raw staging cannot be pruned until runs '
                  f'reach EOS — check backup_watcher and the Kerberos ticket.')
        log(f"CANNOT FREE: {detail}")
        publish('cannot_free', detail, free, cfg, {'held': held_pub, 'deleted': []})
        return {'freed': 0, 'deleted': [], 'state': 'cannot_free'}

    plan = []
    projected = free
    for r in eligible:
        if projected >= target:
            break
        plan.append(r)
        projected += r['size']
    plan_str = ', '.join('{} ({})'.format(r['run'], r['size_h']) for r in plan)
    log(f"  plan  delete {len(plan)} run(s) oldest-first: {plan_str} "
        f"-> ~{human(projected)} free")

    if cfg['dry_run']:
        detail = (f'DRY RUN{" [EMERGENCY]" if emergency else ""} — would delete {len(plan)} run(s) '
                  f'({", ".join(r["run"] for r in plan)}) to reach {human(projected)} free.')
        log(detail)
        publish('dry_run', detail, free, cfg,
                {'would_delete': [{'run': r['run'], 'size_h': r['size_h']} for r in plan],
                 'held': held_pub, 'deleted': [], 'emergency': emergency})
        return {'freed': 0, 'deleted': [], 'state': 'dry_run', 'emergency': emergency}

    deleted, freed = [], 0
    for r in plan:
        # delete_run re-verifies SSD -> HDD -> EOS from scratch and refuses the
        # active run. A refusal here is expected and safe (a run can finish
        # backing up, or start being written, between the scan and now).
        res = space_manager.delete_run('ssd', r['run'])
        if res.get('success'):
            freed += res['freed_bytes']
            deleted.append({'run': r['run'], 'freed_h': res['freed_h']})
            log(f"  DELETED {r['run']} — freed {res['freed_h']}")
        else:
            log(f"  refused {r['run']} — {res.get('message')}")
        if ssd_free() >= target:
            break

    free = ssd_free()
    tag = 'EMERGENCY: ' if emergency else ''
    if freed == 0:
        state, detail = 'cannot_free', 'freeing pass deleted nothing (all candidates refused on re-verify)'
    elif free < low:
        state, detail = 'partial', (f'{tag}freed {human(freed)} but SSD is still below the '
                                    f'{cfg["low_water_gb"]} GB floor at {human(free)}')
    else:
        state, detail = 'freed', f'{tag}freed {human(freed)}; SSD now at {human(free)} free'
    log(f"pass complete: {detail}")
    publish(state, detail, free, cfg,
            {'deleted': deleted, 'freed_bytes': freed, 'freed_h': human(freed),
             'held': held_pub, 'emergency': emergency})
    return {'freed': freed, 'deleted': deleted, 'state': state, 'emergency': emergency}


def main():
    ap = argparse.ArgumentParser(description='Keep the SSD raw-staging disk above a free-space floor')
    ap.add_argument('config', help='path to config/space_config.json')
    ap.add_argument('--once', action='store_true', help='run a single pass and exit')
    ap.add_argument('--dry-run', action='store_true', help='report only; delete nothing')
    args = ap.parse_args()

    def load_cfg():
        """Re-read the config file. Done every poll, not once at startup, so the
        operator can retune the buffer from the GUI's Disk Space tab and have it
        take effect on the next poll instead of needing a restart. A malformed or
        missing file leaves the last-good config in place rather than killing the
        watcher."""
        with open(args.config) as f:
            c = json.load(f)
        c.setdefault('poll_interval', 300)
        c.setdefault('retry_interval', 1800)
        c.setdefault('dry_run', False)
        # An old config file predating the emergency level would otherwise keep the
        # soft guards in force down to a full disk. Default it to half the floor.
        c.setdefault('emergency_gb', round(float(c['low_water_gb']) / 2, 1))
        if args.dry_run:
            c['dry_run'] = True
        return c

    cfg = load_cfg()
    log(f"space_watcher up — floor {cfg['low_water_gb']} GB, free to {cfg['target_free_gb']} GB, "
        f"keep newest {cfg['keep_recent_runs']} runs, min age {cfg['min_age_hours']} h, "
        f"guards off below {cfg['emergency_gb']} GB"
        f"{'  [DRY RUN]' if cfg['dry_run'] else ''}")

    last_futile = 0.0
    futile_emergency = False   # were the guards already off when that futile pass ran?
    while True:
        try:
            try:
                new_cfg = load_cfg()
                if new_cfg != cfg:
                    log(f"config changed — floor {new_cfg['low_water_gb']} GB, "
                        f"free to {new_cfg['target_free_gb']} GB"
                        f"{'  [DRY RUN]' if new_cfg['dry_run'] else ''}")
                    # A retune is a fresh start: the old backoff was about the old floor.
                    last_futile = 0.0
                cfg = new_cfg
            except Exception as e:
                log(f"WARNING: could not reload config ({e}) — keeping previous settings")
            low = int(cfg['low_water_gb'] * GB)
            free = ssd_free()
            emergency = free < emergency_bytes(cfg)
            # A futile pass under the soft guards says nothing about a pass without
            # them, so crossing into emergency cancels the backoff rather than
            # sitting out up to retry_interval on a nearly full disk.
            backed_off = (time.time() - last_futile < cfg['retry_interval']
                          and not (emergency and not futile_emergency))
            if free >= low:
                publish('ok', f'{human(free)} free, above the {cfg["low_water_gb"]} GB floor', free, cfg)
            elif backed_off:
                # Under the floor but the last pass could free nothing. Re-listing
                # every run on EOS every poll would hammer EOS for no benefit.
                log(f"SSD at {human(free)} free — holding off, last pass freed nothing "
                    f"({cfg['retry_interval']}s backoff)")
            else:
                res = freeing_pass(cfg)
                if res['freed'] == 0:
                    last_futile = time.time()
                    futile_emergency = bool(res.get('emergency'))
                else:
                    last_futile = 0.0
                    futile_emergency = False
        except Exception as e:
            log(f"ERROR in poll: {e}")
        if args.once:
            break
        time.sleep(cfg['poll_interval'])


if __name__ == '__main__':
    main()
