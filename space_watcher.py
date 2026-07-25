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


# Last verdict a freeing pass published, kept so a poll that runs no pass can
# re-assert it with a fresh timestamp. See republish().
_last_verdict = None      # (state, detail, extra)


def publish(state: str, detail: str, free: int, cfg: dict, extra=None, remember: bool = True):
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

    `remember` records this as the standing verdict for republish() to re-assert
    during the retry backoff; republish() itself passes False so its own note does
    not accumulate onto the detail string poll after poll.
    """
    global _last_verdict
    if remember:
        _last_verdict = (state, detail, dict(extra) if extra else None)
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


def republish(free: int, cfg: dict, note: str, emergency: bool):
    """Refresh the state file on a poll that ran no freeing pass.

    The freshness window the GUI card and the Telegram monitor apply (3 polls, so
    16 min at the default 300 s) is far shorter than retry_interval (30 min), so a
    watcher correctly sitting out its backoff after a futile pass went silent long
    enough to be read as wedged. That fired "space_watcher is up but not publishing
    fresh state" every backoff, and — worse — the staleness check runs before the
    state check, so the false alarm REPLACED the real verdict (held / cannot_free)
    it was sitting on.

    So re-assert that verdict against the current free space. This deliberately
    does none of the work the backoff exists to avoid: no candidate scan, no EOS
    listing, just the free-space number the poll already had.
    """
    if _last_verdict is None:
        # No pass has run yet this process (only reachable if the first poll is
        # already backed off, which it cannot be). Say the true thing plainly.
        publish('held', f'{human(free)} free, below the {cfg["low_water_gb"]} GB floor; {note}',
                free, cfg, {'emergency': emergency}, remember=False)
        return
    state, detail, extra = _last_verdict
    extra = dict(extra or {})
    # Recompute rather than replaying: free space can have moved (a GUI-side
    # delete, or acquisition eating into it) since the pass that set this verdict.
    extra['emergency'] = emergency
    extra['stale_verdict'] = True      # these numbers are from the last pass, not now
    publish(state, f'{detail}; {note}', free, cfg, extra, remember=False)


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


def subrun_plan(cfg, emergency, held, projected, target):
    """Descend into runs that cannot be reclaimed as a whole and gather deletable
    SUBRUNS, oldest-first, until `projected` free reaches `target`.

    This is what rescues a single long run that fills the disk while still being
    acquired: the run is held whole (active, or not yet fully on EOS), but its
    completed subruns are individually verified and safe. Only visited when whole
    runs did not reach the target, and only for runs that have no whole-run path to
    deletion:

      active / unverified runs   always — their space is unreclaimable otherwise
      newest-N reserved runs     emergency only — the reserve is a deliberate keep

    Per run, the newest `keep_recent_subruns` are held back (dropped in emergency),
    mirroring keep_recent_runs one level down, and min_age applies to each subrun.

    scan_subruns() does one EOS listing per subrun, so this is the expensive path;
    it runs at most once per pass, only under the floor, and stops the instant the
    target is reachable — for the common single-long-run case that is one run's scan.
    Returns (units, projected, notes) where units are {run, subrun, size, size_h}.
    """
    keep_subs = 0 if emergency else max(0, int(cfg.get('keep_recent_subruns', 3)))
    min_age_s = 0.0 if emergency else float(cfg['min_age_hours']) * 3600
    visit = {'active', 'unverified', 'policy'} if emergency else {'active', 'unverified'}
    now = time.time()
    units, notes = [], []
    for run, _why, kind in held:                 # held is oldest-run-first
        if projected >= target:
            break
        if kind not in visit:
            continue
        try:
            ss = space_manager.scan_subruns('ssd', run)
        except Exception as e:
            notes.append(f'subrun scan failed for {run}: {e}')
            continue
        subs = ss['subruns']                     # acquisition order, oldest-first
        protected = {s['subrun'] for s in subs[-keep_subs:]} if keep_subs else set()
        added = 0
        for s in subs:
            if projected >= target:
                break
            if not s['safe'] or s['subrun'] in protected:
                continue
            age = now - newest_mtime(space_manager.SSD_DREAM_DIR / run / s['subrun'])
            if age < min_age_s:
                continue
            units.append({'run': run, 'subrun': s['subrun'],
                          'size': s['size'], 'size_h': s['size_h']})
            projected += s['size']
            added += 1
        notes.append(f'{run}: {added} of {ss["n_subruns"]} subruns deletable'
                     f'{" (reserving newest %d)" % keep_subs if keep_subs else ""}')
    return units, projected, notes


def freeing_pass(cfg: dict) -> dict:
    """One attempt to get back over target_free_gb. Deletes oldest-first — whole runs
    first, then, if those don't suffice, completed subruns of runs that can't be
    reclaimed whole — and stops the moment the target is reached, so no more data is
    destroyed than the policy requires."""
    target = int(cfg['target_free_gb'] * GB)
    low    = int(cfg['low_water_gb'] * GB)
    emerg  = emergency_bytes(cfg)
    free   = ssd_free()
    emergency = free < emerg

    if emergency:
        log(f"SSD at {human(free)} free — BELOW THE {human(emerg)} EMERGENCY LEVEL: "
            f"newest reserve and minimum age are OFF, any EOS-verified run/subrun may go")
    else:
        log(f"SSD at {human(free)} free (< floor {human(low)}) — looking for safe runs to delete")
    eligible, held, scan = candidates(cfg, emergency=emergency)

    held_pub = [{'run': r, 'reason': w, 'kind': k} for r, w, k in held]
    for run, why, _kind in held:
        log(f"  hold  {run:12} {why}")

    # Whole-run plan, oldest-first.
    plan = []
    projected = free
    for r in eligible:
        if projected >= target:
            break
        plan.append(r)
        projected += r['size']
    if plan:
        plan_str = ', '.join('{} ({})'.format(r['run'], r['size_h']) for r in plan)
        log(f"  plan  delete {len(plan)} whole run(s) oldest-first: {plan_str} "
            f"-> ~{human(projected)} free")

    # If whole runs don't get there, descend into subruns of the held runs.
    sub_plan, sub_notes = [], []
    if projected < target:
        sub_plan, projected, sub_notes = subrun_plan(cfg, emergency, held, projected, target)
        for n in sub_notes:
            log(f"  subruns {n}")
        if sub_plan:
            log(f"  plan  + {len(sub_plan)} subrun(s) -> ~{human(projected)} free")

    if not plan and not sub_plan:
        # Nothing to delete. Distinguish the two failures that need different humans:
        # everything held by soft guards (releases itself at the emergency level, and
        # backups are fine) vs nothing on EOS at all (a backup outage).
        if any(k == 'policy' for _r, _w, k in held):
            detail = (f'SSD below the {cfg["low_water_gb"]} GB floor, but every deletable run/subrun '
                      f'is held by the newest-reserve/min-age guards. Backups are fine — released '
                      f'automatically below the {cfg["emergency_gb"]} GB emergency level.')
            log(f"HELD BY POLICY: {detail}")
            publish('held', detail, free, cfg, {'held': held_pub, 'deleted': []})
            return {'freed': 0, 'deleted': [], 'state': 'held'}
        detail = (f'SSD below the {cfg["low_water_gb"]} GB floor with NO run or subrun safe to '
                  f'delete ({scan["n_runs"]} runs on disk). Raw staging cannot be pruned until '
                  f'runs reach EOS — check backup_watcher and the Kerberos ticket.')
        log(f"CANNOT FREE: {detail}")
        publish('cannot_free', detail, free, cfg, {'held': held_pub, 'deleted': []})
        return {'freed': 0, 'deleted': [], 'state': 'cannot_free'}

    if cfg['dry_run']:
        parts = [r['run'] for r in plan] + [f"{u['run']}/{u['subrun']}" for u in sub_plan]
        detail = (f'DRY RUN{" [EMERGENCY]" if emergency else ""} — would delete '
                  f'{len(plan)} run(s) + {len(sub_plan)} subrun(s) ({", ".join(parts)}) '
                  f'to reach {human(projected)} free.')
        log(detail)
        publish('dry_run', detail, free, cfg,
                {'would_delete': [{'run': r['run'], 'size_h': r['size_h']} for r in plan],
                 'would_delete_subruns': [{'run': u['run'], 'subrun': u['subrun'],
                                           'size_h': u['size_h']} for u in sub_plan],
                 'held': held_pub, 'deleted': [], 'emergency': emergency})
        return {'freed': 0, 'deleted': [], 'state': 'dry_run', 'emergency': emergency}

    deleted, deleted_subs, freed = [], [], 0
    # One fresh whole-tree EOS listing for the whole pass, instead of one per run:
    # an xrdfs listing costs ~20-25 s here, so a 36-run pass otherwise spent a
    # quarter of an hour re-listing the same tree. Verification itself still runs
    # per run, against this listing plus a fresh read of the local tree — see
    # space_manager._batch_listing() for why reusing it cannot make an unsafe
    # delete look safe.
    space_manager._batch_listing()
    for r in plan:
        # delete_run re-verifies against EOS from scratch and refuses the active
        # run. A refusal here is expected and safe (a run can finish backing up,
        # or start being written, between the scan and now).
        res = space_manager.delete_run('ssd', r['run'], force=False)
        if res.get('success'):
            freed += res['freed_bytes']
            deleted.append({'run': r['run'], 'freed_h': res['freed_h']})
            log(f"  DELETED {r['run']} — freed {res['freed_h']}")
        else:
            log(f"  refused {r['run']} — {res.get('message')}")
        if ssd_free() >= target:
            break

    if ssd_free() < target:
        for u in sub_plan:
            # delete_subrun re-verifies and, for the active run, refuses any subrun
            # not marked .subrun_complete. Refusals are expected and safe.
            res = space_manager.delete_subrun('ssd', u['run'], u['subrun'], force=False)
            if res.get('success'):
                freed += res['freed_bytes']
                deleted_subs.append({'run': u['run'], 'subrun': u['subrun'],
                                     'freed_h': res['freed_h']})
                log(f"  DELETED {u['run']}/{u['subrun']} — freed {res['freed_h']}")
            else:
                log(f"  refused {u['run']}/{u['subrun']} — {res.get('message')}")
            if ssd_free() >= target:
                break

    free = ssd_free()
    tag = 'EMERGENCY: ' if emergency else ''
    n_units = len(deleted) + len(deleted_subs)
    what = f'{len(deleted)} run(s) + {len(deleted_subs)} subrun(s)'
    if freed == 0:
        state, detail = 'cannot_free', 'freeing pass deleted nothing (all candidates refused on re-verify)'
    elif free < low:
        state, detail = 'partial', (f'{tag}freed {human(freed)} ({what}) but SSD is still below the '
                                    f'{cfg["low_water_gb"]} GB floor at {human(free)}')
    else:
        state, detail = 'freed', f'{tag}freed {human(freed)} ({what}); SSD now at {human(free)} free'
    log(f"pass complete: {detail}")
    publish(state, detail, free, cfg,
            {'deleted': deleted, 'deleted_subruns': deleted_subs,
             'freed_bytes': freed, 'freed_h': human(freed),
             'held': held_pub, 'emergency': emergency})
    return {'freed': freed, 'deleted': deleted, 'deleted_subruns': deleted_subs,
            'state': state, 'emergency': emergency}


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
        # Newest subruns kept back when pruning a run's subruns (see subrun_plan).
        c.setdefault('keep_recent_subruns', 3)
        if args.dry_run:
            c['dry_run'] = True
        return c

    cfg = load_cfg()
    log(f"space_watcher up — floor {cfg['low_water_gb']} GB, free to {cfg['target_free_gb']} GB, "
        f"keep newest {cfg['keep_recent_runs']} runs / {cfg['keep_recent_subruns']} subruns, "
        f"min age {cfg['min_age_hours']} h, guards off below {cfg['emergency_gb']} GB"
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
                left = int(cfg['retry_interval'] - (time.time() - last_futile))
                log(f"SSD at {human(free)} free — holding off, last pass freed nothing "
                    f"({cfg['retry_interval']}s backoff)")
                # Still publish: silence through a backoff longer than the readers'
                # freshness window is what made a healthy watcher look wedged.
                republish(free, cfg, f'retrying in {max(0, left)}s', emergency)
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
