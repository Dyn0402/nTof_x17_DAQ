#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Disk-space management for nTof DREAM data.

Provides a read-only *scan/check* and a heavily guarded *delete* for freeing
space in the two places DREAM data lives:

  HDD  /mnt/data/x17/beam_july/runs/<run>          processed runs, backed up to EOS
  SSD  /home/mx17/july_dream/dream_run/<run>       raw .fdf acquisition staging

Safety model — a run is only ever "safe to delete" when its data is provably
preserved somewhere else:

  HDD run  -> SAFE iff EVERY file is present on EOS at matching size (the
              authoritative offsite backup). This is exactly the check
              backup_watcher uses (relative path + byte size; data is write-once).

  SSD run  -> raw staging. SAFE iff every raw *.fdf* file on the SSD also exists
              on the HDD for the same run at matching size, AND that HDD run is
              itself fully verified on EOS. i.e. SSD -> HDD -> EOS. The non-.fdf
              SSD files (.prg copies, .par, .cfg_cpy, logs) are reproducible
              staging artifacts and are reported but do not block deletion.

Nothing here trusts a caller-supplied verdict: delete_run() re-runs the full
verification itself immediately before it removes anything, refuses the active
run, and refuses any path that is not a plain run directory directly under the
expected root.

The EOS endpoint + path are read from the same config/backup_config.json the
backup watcher uses, so the two always agree on where the backup lives.
"""

import os
import re
import json
import time
import shutil
import subprocess
from pathlib import Path
from datetime import datetime

# --- Locations -------------------------------------------------------------
BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
HDD_RUNS_DIR   = Path('/mnt/data/x17/beam_july/runs')
SSD_DREAM_DIR  = Path('/home/mx17/july_dream/dream_run')
# A path on each physical filesystem, for disk-usage reporting.
HDD_FS_PATH    = '/mnt/data'
SSD_FS_PATH    = '/home/mx17'

BACKUP_CONFIG_PATH   = os.path.join(BASE_DIR, 'config', 'backup_config.json')
CURRENT_RUN_STATE    = os.path.join(BASE_DIR, 'config', 'current_run_state.json')
DELETE_LOG           = os.path.join(BASE_DIR, 'logs', 'space_manager.log')

RUN_NAME_RE = re.compile(r'^run_\d+$')
# A subrun is a directory directly under a run dir, with the SAME name on SSD, HDD
# and EOS (e.g. m090On_dr500_r520_062, or pedestals). Names are DAQ-chosen and not
# as regular as run_<N>, so this is a whitelist of safe characters — it must start
# with an alphanumeric and contain only [A-Za-z0-9._-], which by construction
# excludes '.', '..', and anything with a path separator. The resolved-path guards
# in delete_subrun are the real protection; this just rejects obvious nonsense early.
SUBRUN_NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*$')

DISKS = {
    'hdd': {'label': 'HDD (processed runs)',   'root': HDD_RUNS_DIR,  'fs': HDD_FS_PATH},
    'ssd': {'label': 'SSD (raw dream_run)',    'root': SSD_DREAM_DIR, 'fs': SSD_FS_PATH},
}


# --- EOS config ------------------------------------------------------------

def _eos_config():
    """(xrootd_url, eos_runs_dir) from the backup watcher's config."""
    with open(BACKUP_CONFIG_PATH) as f:
        cfg = json.load(f)
    url = cfg.get('xrootd_url', 'root://eospublic.cern.ch').rstrip('/')
    eos_runs = str(Path(cfg['eos_dir']) / cfg.get('runs_subdir', 'runs'))
    return url, eos_runs


# --- Size maps -------------------------------------------------------------

def _local_size_map(root: Path) -> dict:
    """{relpath: size} for every regular file under root."""
    out = {}
    for f in root.rglob('*'):
        try:
            if f.is_file() and not f.is_symlink():
                out[f.relative_to(root).as_posix()] = f.stat().st_size
        except OSError:
            pass
    return out


def _remote_size_map(eos_dir: str):
    """{relpath: size} for every file under eos_dir on EOS, or None on a listing
    error (so the caller can treat 'could not verify' as NOT safe).

    An absent directory lists cleanly as empty ({}), which correctly reads as
    'nothing backed up'. A genuine xrdfs failure (auth, network) returns None.
    """
    url, _ = _eos_config()
    result = subprocess.run(
        ['xrdfs', url, 'ls', '-l', '-R', eos_dir],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        err = (result.stderr or '').lower()
        if 'not found' in err or 'no such file' in err or '3011' in err:
            return {}
        return None
    base = eos_dir.rstrip('/') + '/'
    sizes = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 7 or parts[0].startswith('d'):
            continue
        try:
            size = int(parts[3])
        except ValueError:
            continue
        path = parts[-1]
        if path.startswith(base):
            sizes[path[len(base):]] = size
    return sizes


# --- Helpers ---------------------------------------------------------------

def _run_num(name: str) -> int:
    m = re.search(r'(\d+)', name)
    return int(m.group(1)) if m else -1


def active_run() -> str:
    """Name of the run currently being acquired (never deletable), or ''."""
    try:
        with open(CURRENT_RUN_STATE) as f:
            return json.load(f).get('run_name', '') or ''
    except Exception:
        return ''


def _dir_size(root: Path) -> int:
    total = 0
    for f in root.rglob('*'):
        try:
            if f.is_file() and not f.is_symlink():
                total += f.stat().st_size
        except OSError:
            pass
    return total


def human(n: int) -> str:
    f = float(n)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if abs(f) < 1024 or unit == 'TB':
            return f"{f:.1f} {unit}" if unit != 'B' else f"{int(f)} B"
        f /= 1024
    return f"{f:.1f} TB"


def disk_usage() -> dict:
    """Free/used/total for both filesystems."""
    out = {}
    for key, d in DISKS.items():
        try:
            u = shutil.disk_usage(d['fs'])
            out[key] = {'label': d['label'], 'fs': d['fs'],
                        'total': u.total, 'used': u.used, 'free': u.free,
                        'pct': round(100.0 * u.used / u.total, 1) if u.total else 0.0}
        except OSError as e:
            out[key] = {'label': d['label'], 'fs': d['fs'], 'error': str(e)}
    return out


# --- Verification ----------------------------------------------------------

def verify_hdd_run(run: str) -> dict:
    """Compare an HDD run against EOS, file by file (relpath + size)."""
    root = HDD_RUNS_DIR / run
    res = {'run': run, 'disk': 'hdd', 'size': 0, 'files': 0,
           'ok': 0, 'missing': 0, 'mismatch': 0,
           'safe': False, 'reason': '', 'unverifiable': False}
    if not root.is_dir():
        res['reason'] = 'run directory not found on HDD'
        return res
    local = _local_size_map(root)
    res['files'] = len(local)
    res['size'] = sum(local.values())
    # Compare against the EOS copy of THIS run.
    _, eos_runs = _eos_config()
    remote = _remote_size_map(f"{eos_runs}/{run}")
    if remote is None:
        res['unverifiable'] = True
        res['reason'] = 'could not list run on EOS (Kerberos/network?) — NOT safe'
        return res
    missing = mismatch = ok = 0
    for rel, sz in local.items():
        rsz = remote.get(rel)
        if rsz == sz:
            ok += 1
        elif rsz is None:
            missing += 1
        else:
            mismatch += 1
    res.update(ok=ok, missing=missing, mismatch=mismatch)
    if missing == 0 and mismatch == 0 and ok == len(local) and len(local) > 0:
        res['safe'] = True
        res['reason'] = f'all {ok} files verified on EOS'
    elif len(local) == 0:
        res['reason'] = 'run directory is empty'
    else:
        res['reason'] = f'{missing} missing + {mismatch} size-mismatched on EOS'
    return res


def verify_ssd_run(run: str) -> dict:
    """An SSD raw run is safe iff every .fdf is on the HDD (same run) at matching
    size AND that HDD run is fully verified on EOS (SSD -> HDD -> EOS)."""
    root = SSD_DREAM_DIR / run
    res = {'run': run, 'disk': 'ssd', 'size': 0, 'files': 0,
           'fdf_total': 0, 'fdf_on_hdd': 0, 'staging_files': 0,
           'hdd_safe': False, 'safe': False, 'reason': '', 'unverifiable': False}
    if not root.is_dir():
        res['reason'] = 'run directory not found on SSD'
        return res
    local = _local_size_map(root)
    res['files'] = len(local)
    res['size'] = sum(local.values())

    hdd_root = HDD_RUNS_DIR / run
    if not hdd_root.is_dir():
        res['reason'] = 'no matching HDD run — raw data not yet migrated; NOT safe'
        return res

    # (basename, size) multiset of everything on the HDD for this run.
    hdd_pairs = set()
    for rel, sz in _local_size_map(hdd_root).items():
        hdd_pairs.add((os.path.basename(rel), sz))

    fdf = {rel: sz for rel, sz in local.items() if rel.lower().endswith('.fdf')}
    res['fdf_total'] = len(fdf)
    res['staging_files'] = len(local) - len(fdf)
    on_hdd = sum(1 for rel, sz in fdf.items()
                 if (os.path.basename(rel), sz) in hdd_pairs)
    res['fdf_on_hdd'] = on_hdd

    hdd_res = verify_hdd_run(run)
    res['hdd_safe'] = hdd_res['safe']
    res['unverifiable'] = hdd_res.get('unverifiable', False)

    if len(fdf) == 0:
        res['reason'] = 'no .fdf raw files present (nothing to protect)'
    if on_hdd < len(fdf):
        res['reason'] = (f'{len(fdf) - on_hdd}/{len(fdf)} raw .fdf files NOT '
                         f'found on HDD — NOT safe')
        return res
    if not hdd_res['safe']:
        res['reason'] = f"raw .fdf on HDD, but HDD run not on EOS: {hdd_res['reason']}"
        return res
    res['safe'] = True
    res['reason'] = (f'all {len(fdf)} raw .fdf verified on HDD->EOS; '
                     f'{res["staging_files"]} staging files reproducible')
    return res


def verify_run(disk: str, run: str) -> dict:
    return verify_hdd_run(run) if disk == 'hdd' else verify_ssd_run(run)


# --- Subrun verification ---------------------------------------------------
# A subrun is a self-contained directory mirrored across all three tiers
# (SSD raw -> HDD processed -> EOS), so the run-level safety model applies
# unchanged, one directory deeper. The only extra signal is `.subrun_complete`,
# the HDD-side marker the DAQ writes when a subrun finishes; it is mirrored to
# EOS like any other file, so it never trips the file-set comparison.

def verify_hdd_subrun(run: str, subrun: str) -> dict:
    """Compare one HDD subrun directory against its EOS mirror (relpath + size),
    exactly as verify_hdd_run does for a whole run, scoped one level deeper."""
    root = HDD_RUNS_DIR / run / subrun
    res = {'run': run, 'subrun': subrun, 'disk': 'hdd', 'size': 0, 'files': 0,
           'ok': 0, 'missing': 0, 'mismatch': 0,
           'safe': False, 'reason': '', 'unverifiable': False,
           'complete': (root / '.subrun_complete').is_file()}
    if not root.is_dir():
        res['reason'] = 'subrun directory not found on HDD'
        return res
    local = _local_size_map(root)
    res['files'] = len(local)
    res['size'] = sum(local.values())
    _, eos_runs = _eos_config()
    remote = _remote_size_map(f"{eos_runs}/{run}/{subrun}")
    if remote is None:
        res['unverifiable'] = True
        res['reason'] = 'could not list subrun on EOS (Kerberos/network?) — NOT safe'
        return res
    missing = mismatch = ok = 0
    for rel, sz in local.items():
        rsz = remote.get(rel)
        if rsz == sz:
            ok += 1
        elif rsz is None:
            missing += 1
        else:
            mismatch += 1
    res.update(ok=ok, missing=missing, mismatch=mismatch)
    if missing == 0 and mismatch == 0 and ok == len(local) and len(local) > 0:
        res['safe'] = True
        res['reason'] = f'all {ok} files verified on EOS'
    elif len(local) == 0:
        res['reason'] = 'subrun directory is empty'
    else:
        res['reason'] = f'{missing} missing + {mismatch} size-mismatched on EOS'
    return res


def verify_ssd_subrun(run: str, subrun: str) -> dict:
    """An SSD raw subrun is safe iff every .fdf is on the HDD (same run/subrun) at
    matching size AND that HDD subrun is fully verified on EOS. Mirrors
    verify_ssd_run exactly, scoped to one subrun."""
    root = SSD_DREAM_DIR / run / subrun
    res = {'run': run, 'subrun': subrun, 'disk': 'ssd', 'size': 0, 'files': 0,
           'fdf_total': 0, 'fdf_on_hdd': 0, 'staging_files': 0,
           'hdd_safe': False, 'safe': False, 'reason': '', 'unverifiable': False,
           'complete': False}
    if not root.is_dir():
        res['reason'] = 'subrun directory not found on SSD'
        return res
    local = _local_size_map(root)
    res['files'] = len(local)
    res['size'] = sum(local.values())

    hdd_root = HDD_RUNS_DIR / run / subrun
    if not hdd_root.is_dir():
        res['reason'] = 'no matching HDD subrun — raw data not yet migrated; NOT safe'
        return res

    # (basename, size) multiset of everything on the HDD for this subrun.
    hdd_pairs = set()
    for rel, sz in _local_size_map(hdd_root).items():
        hdd_pairs.add((os.path.basename(rel), sz))

    fdf = {rel: sz for rel, sz in local.items() if rel.lower().endswith('.fdf')}
    res['fdf_total'] = len(fdf)
    res['staging_files'] = len(local) - len(fdf)
    on_hdd = sum(1 for rel, sz in fdf.items()
                 if (os.path.basename(rel), sz) in hdd_pairs)
    res['fdf_on_hdd'] = on_hdd

    hdd_res = verify_hdd_subrun(run, subrun)
    res['hdd_safe'] = hdd_res['safe']
    res['unverifiable'] = hdd_res.get('unverifiable', False)
    res['complete'] = hdd_res['complete']

    if len(fdf) == 0:
        res['reason'] = 'no .fdf raw files present (nothing to protect)'
    if on_hdd < len(fdf):
        res['reason'] = (f'{len(fdf) - on_hdd}/{len(fdf)} raw .fdf files NOT '
                         f'found on HDD — NOT safe')
        return res
    if not hdd_res['safe']:
        res['reason'] = f"raw .fdf on HDD, but HDD subrun not on EOS: {hdd_res['reason']}"
        return res
    res['safe'] = True
    res['reason'] = (f'all {len(fdf)} raw .fdf verified on HDD->EOS; '
                     f'{res["staging_files"]} staging files reproducible')
    return res


def verify_subrun(disk: str, run: str, subrun: str) -> dict:
    return verify_hdd_subrun(run, subrun) if disk == 'hdd' else verify_ssd_subrun(run, subrun)


# --- Scan ------------------------------------------------------------------

def list_runs(disk: str) -> list:
    root = DISKS[disk]['root']
    if not root.is_dir():
        return []
    runs = [p.name for p in root.iterdir() if p.is_dir() and RUN_NAME_RE.match(p.name)]
    return sorted(runs, key=_run_num)


def _subrun_key(name: str):
    """Sort subruns by their trailing _<NNN> acquisition index when present (so
    m..._062 orders by 62, not lexically), falling back to the name otherwise."""
    m = re.search(r'_(\d+)$', name)
    return (0, int(m.group(1))) if m else (1, name)


def list_subruns(disk: str, run: str) -> list:
    """Subrun directory names directly under a run, acquisition-order-sorted.
    Only real directories whose name passes SUBRUN_NAME_RE — loose run-level files
    (run_config.json, dream_daq.log) and symlinks are ignored."""
    root = DISKS[disk]['root'] / run
    if not root.is_dir():
        return []
    subs = [p.name for p in root.iterdir()
            if p.is_dir() and not p.is_symlink() and SUBRUN_NAME_RE.match(p.name)]
    return sorted(subs, key=_subrun_key)


def scan(disk: str, runs=None) -> dict:
    """Verify every run (or a subset) on a disk; return per-run verdicts."""
    if disk not in DISKS:
        raise ValueError(f'unknown disk {disk!r}')
    names = runs if runs else list_runs(disk)
    act = active_run()
    results = []
    for run in names:
        v = verify_run(disk, run)
        v['active'] = (run == act)
        # The active run is never deletable regardless of backup status.
        if v['active']:
            v['safe'] = False
            v['reason'] = 'currently acquiring — never deletable while active'
        v['size_h'] = human(v.get('size', 0))
        results.append(v)
    safe_bytes = sum(r['size'] for r in results if r['safe'])
    return {
        'disk': disk, 'label': DISKS[disk]['label'],
        'runs': results,
        'n_runs': len(results),
        'n_safe': sum(1 for r in results if r['safe']),
        'safe_bytes': safe_bytes, 'safe_bytes_h': human(safe_bytes),
        'active_run': act,
        'usage': disk_usage().get(disk, {}),
        'scanned_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }


def scan_subruns(disk: str, run: str) -> dict:
    """Per-subrun verdicts for one run — the finer-grained sibling of scan().

    Every subrun is verified independently (SSD -> HDD -> EOS, or HDD -> EOS). For
    the run that is CURRENTLY ACQUIRING there is an extra gate: a subrun is only ever
    deletable once its HDD copy carries the `.subrun_complete` marker AND it verifies.
    The in-progress subrun has no marker yet, so it can never be a candidate even if a
    verification happens to pass mid-write. Completed subruns of the active run ARE
    eligible — that is the whole point: a long run can be pruned while it is still
    being taken.
    """
    if disk not in DISKS:
        raise ValueError(f'unknown disk {disk!r}')
    if not RUN_NAME_RE.match(run or ''):
        raise ValueError(f'invalid run name {run!r}')
    act = active_run()
    is_active_run = (run == act)
    results = []
    for sub in list_subruns(disk, run):
        v = verify_subrun(disk, run, sub)
        v['active_run'] = is_active_run
        v['held_active'] = False
        # Active-run gate: an incomplete subrun of the active run is never deletable.
        if is_active_run and not v.get('complete'):
            v['held_active'] = True
            if v['safe']:
                v['safe'] = False
                v['reason'] = 'active run: subrun not marked complete — never deletable yet'
        v['size_h'] = human(v.get('size', 0))
        results.append(v)
    safe_bytes = sum(r['size'] for r in results if r['safe'])
    total_bytes = sum(r['size'] for r in results)
    return {
        'disk': disk, 'run': run, 'active': is_active_run,
        'subruns': results,
        'n_subruns': len(results),
        'n_safe': sum(1 for r in results if r['safe']),
        'safe_bytes': safe_bytes, 'safe_bytes_h': human(safe_bytes),
        'total_bytes': total_bytes, 'total_bytes_h': human(total_bytes),
        'active_run': act,
        'scanned_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }


# --- Delete ----------------------------------------------------------------

def _log_delete(msg: str):
    try:
        os.makedirs(os.path.dirname(DELETE_LOG), exist_ok=True)
        with open(DELETE_LOG, 'a') as f:
            f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {msg}\n")
    except Exception:
        pass


def delete_run(disk: str, run: str) -> dict:
    """Delete one run directory from a disk, but ONLY after re-verifying, here,
    that it is safe. Never trusts a caller verdict.

    Guards, in order:
      1. disk is known; run matches ^run_\\d+$.
      2. target resolves to a real directory sitting DIRECTLY under the disk root
         (no symlinks, no traversal, no partial-name tricks).
      3. run is not the active (currently-acquiring) run.
      4. a fresh verify_run() says SAFE.
    """
    if disk not in DISKS:
        return {'success': False, 'message': f'unknown disk {disk!r}'}
    if not RUN_NAME_RE.match(run or ''):
        return {'success': False, 'message': f'invalid run name {run!r}'}

    root = DISKS[disk]['root'].resolve()
    target = (DISKS[disk]['root'] / run)
    try:
        rtarget = target.resolve()
    except OSError as e:
        return {'success': False, 'message': f'cannot resolve path: {e}'}
    if target.is_symlink():
        return {'success': False, 'message': 'refusing to delete a symlink'}
    if not rtarget.is_dir():
        return {'success': False, 'message': f'{run} is not a directory on {disk}'}
    if rtarget.parent != root or rtarget == root:
        return {'success': False, 'message': 'path is not a run directly under the disk root'}

    if run == active_run():
        return {'success': False, 'message': f'{run} is the active run — refusing'}

    verdict = verify_run(disk, run)
    verdict['active'] = (run == active_run())
    if not verdict['safe']:
        _log_delete(f"REFUSED {disk}/{run}: {verdict['reason']}")
        return {'success': False, 'message': f"not safe to delete: {verdict['reason']}",
                'verdict': verdict}

    size = _dir_size(rtarget)
    try:
        shutil.rmtree(rtarget)
    except Exception as e:
        _log_delete(f"ERROR deleting {disk}/{run}: {e}")
        return {'success': False, 'message': f'delete failed: {e}'}

    _log_delete(f"DELETED {disk}/{run}  freed={human(size)}  ({verdict['reason']})")
    return {'success': True, 'run': run, 'disk': disk,
            'freed_bytes': size, 'freed_h': human(size),
            'message': f'Deleted {disk}/{run}, freed {human(size)}'}


def delete_runs(disk: str, runs: list) -> dict:
    """Delete several runs; each is independently re-verified. Stops nothing on a
    single failure — reports per-run outcomes."""
    results = []
    freed = 0
    for run in runs:
        r = delete_run(disk, run)
        results.append(r)
        if r.get('success'):
            freed += r.get('freed_bytes', 0)
    return {'results': results, 'freed_bytes': freed, 'freed_h': human(freed),
            'n_deleted': sum(1 for r in results if r.get('success')),
            'n_failed': sum(1 for r in results if not r.get('success'))}


def delete_subrun(disk: str, run: str, subrun: str) -> dict:
    """Delete one subrun directory from a disk, only after re-verifying, here, that
    it is safe. Same never-trust-the-caller model as delete_run, one level deeper.

    Guards, in order:
      1. disk is known; run matches ^run_\\d+$; subrun matches SUBRUN_NAME_RE.
      2. target resolves to a real directory sitting DIRECTLY under a real run
         directory that itself sits DIRECTLY under the disk root (no symlinks, no
         traversal, no partial-name tricks). The run dir is never itself the target.
      3. if the run is the active one, the subrun must be marked `.subrun_complete`
         (an in-progress subrun is never deletable).
      4. a fresh verify_subrun() says SAFE.
    """
    if disk not in DISKS:
        return {'success': False, 'message': f'unknown disk {disk!r}'}
    if not RUN_NAME_RE.match(run or ''):
        return {'success': False, 'message': f'invalid run name {run!r}'}
    if not SUBRUN_NAME_RE.match(subrun or ''):
        return {'success': False, 'message': f'invalid subrun name {subrun!r}'}

    root = DISKS[disk]['root'].resolve()
    run_dir = DISKS[disk]['root'] / run
    target = run_dir / subrun
    try:
        rrun = run_dir.resolve()
        rtarget = target.resolve()
    except OSError as e:
        return {'success': False, 'message': f'cannot resolve path: {e}'}
    if run_dir.is_symlink() or target.is_symlink():
        return {'success': False, 'message': 'refusing to delete through a symlink'}
    if not rtarget.is_dir():
        return {'success': False, 'message': f'{run}/{subrun} is not a directory on {disk}'}
    if rrun.parent != root or rrun == root:
        return {'success': False, 'message': 'run is not directly under the disk root'}
    if rtarget.parent != rrun or rtarget == rrun:
        return {'success': False, 'message': 'subrun is not directly under its run directory'}

    # Active-run gate: only a completed subrun of the acquiring run may go.
    if run == active_run():
        v0 = verify_subrun(disk, run, subrun)
        if not v0.get('complete'):
            _log_delete(f"REFUSED {disk}/{run}/{subrun}: active run, subrun not complete")
            return {'success': False,
                    'message': f'{run}/{subrun} is in the active run and not marked complete — refusing',
                    'verdict': v0}

    verdict = verify_subrun(disk, run, subrun)
    if not verdict['safe']:
        _log_delete(f"REFUSED {disk}/{run}/{subrun}: {verdict['reason']}")
        return {'success': False, 'message': f"not safe to delete: {verdict['reason']}",
                'verdict': verdict}

    size = _dir_size(rtarget)
    try:
        shutil.rmtree(rtarget)
    except Exception as e:
        _log_delete(f"ERROR deleting {disk}/{run}/{subrun}: {e}")
        return {'success': False, 'message': f'delete failed: {e}'}

    _log_delete(f"DELETED {disk}/{run}/{subrun}  freed={human(size)}  ({verdict['reason']})")
    return {'success': True, 'run': run, 'subrun': subrun, 'disk': disk,
            'freed_bytes': size, 'freed_h': human(size),
            'message': f'Deleted {disk}/{run}/{subrun}, freed {human(size)}'}


def delete_subruns(disk: str, run: str, subruns: list) -> dict:
    """Delete several subruns of one run; each independently re-verified."""
    results = []
    freed = 0
    for sub in subruns:
        r = delete_subrun(disk, run, sub)
        results.append(r)
        if r.get('success'):
            freed += r.get('freed_bytes', 0)
    return {'results': results, 'freed_bytes': freed, 'freed_h': human(freed),
            'n_deleted': sum(1 for r in results if r.get('success')),
            'n_failed': sum(1 for r in results if not r.get('success'))}


# --- Restore (EOS -> local HDD) -------------------------------------------
# The inverse of delete: pull a run back from EOS onto the HDD. EOS mirrors the
# HDD layout (not the SSD raw staging), so restore always targets the HDD. Only
# files missing or size-mismatched locally are fetched (xrdcp -f), so it is
# idempotent and cheap to re-run — exactly the reverse of the backup sync.

def _xrdcp_download(eos_file: str, local_path: Path):
    """Copy one file EOS -> local via native xrdcp. Returns (ok, stderr)."""
    url, _ = _eos_config()
    try:
        local_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return False, str(e)
    src = f"{url}//{eos_file.lstrip('/')}"
    r = subprocess.run(['xrdcp', '-f', '--nopbar', src, str(local_path)],
                       capture_output=True, text=True)
    return (r.returncode == 0), (r.stderr or '').strip()


def list_eos_runs():
    """Sorted run_N names present on EOS, or None if the listing failed."""
    url, eos_runs = _eos_config()
    r = subprocess.run(['xrdfs', url, 'ls', eos_runs], capture_output=True, text=True)
    if r.returncode != 0:
        return None
    out = []
    for line in r.stdout.splitlines():
        name = line.rstrip('/').rsplit('/', 1)[-1]
        if RUN_NAME_RE.match(name):
            out.append(name)
    return sorted(out, key=_run_num)


def scan_restore() -> dict:
    """List every run on EOS and, for each, how it compares to the local HDD:
    complete (already local), partial, or missing. 'To fetch' is the bytes that
    would be pulled (files absent or size-mismatched locally)."""
    runs = list_eos_runs()
    if runs is None:
        raise RuntimeError('could not list runs on EOS (Kerberos/network?)')
    act = active_run()
    _, eos_runs = _eos_config()
    results = []
    fetch_total = 0
    for run in runs:
        remote = _remote_size_map(f"{eos_runs}/{run}")
        r = {'run': run, 'disk': 'hdd', 'active': run == act}
        if remote is None:
            r.update(status='error', restorable=False, eos_bytes=0, size_h='—',
                     total=0, have=0, fetch_files=0, fetch_bytes=0, fetch_h='—')
            results.append(r)
            continue
        eos_bytes = sum(remote.values())
        total = len(remote)
        local_root = HDD_RUNS_DIR / run
        local = _local_size_map(local_root) if local_root.is_dir() else {}
        have = fetch_bytes = 0
        for rel, sz in remote.items():
            if local.get(rel) == sz:
                have += 1
            else:
                fetch_bytes += sz
        fetch_files = total - have
        status = 'complete' if fetch_files == 0 else ('missing' if not local else 'partial')
        restorable = fetch_files > 0 and not r['active']
        r.update(status=status, restorable=restorable, eos_bytes=eos_bytes,
                 size_h=human(eos_bytes), total=total, have=have,
                 fetch_files=fetch_files, fetch_bytes=fetch_bytes, fetch_h=human(fetch_bytes))
        if restorable:
            fetch_total += fetch_bytes
        results.append(r)
    return {
        'runs': results, 'n_runs': len(results),
        'n_restorable': sum(1 for r in results if r['restorable']),
        'fetch_bytes_total': fetch_total, 'fetch_bytes_total_h': human(fetch_total),
        'active_run': act, 'usage': disk_usage().get('hdd', {}),
        'scanned_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }


def restore_run(run: str) -> dict:
    """Pull one run from EOS onto the HDD. Copies only files missing or size-
    mismatched locally. Refuses the active run (would clobber live writes) and
    aborts if the HDD lacks free space for the fetch."""
    res = {'run': run, 'disk': 'hdd', 'success': False, 'restored_files': 0,
           'fetched_bytes': 0, 'fetched_h': '0 B', 'message': ''}
    if not RUN_NAME_RE.match(run or ''):
        res['message'] = f'invalid run name {run!r}'
        return res
    if run == active_run():
        res['message'] = f'{run} is the active run — refusing'
        return res
    _, eos_runs = _eos_config()
    eos_run = f"{eos_runs}/{run}"
    remote = _remote_size_map(eos_run)
    if remote is None:
        res['message'] = 'could not list run on EOS (Kerberos/network?)'
        return res
    if not remote:
        res['message'] = 'run not found on EOS'
        return res

    local_root = HDD_RUNS_DIR / run
    to_fetch = []
    for rel, sz in remote.items():
        lp = local_root / rel
        try:
            match = lp.is_file() and lp.stat().st_size == sz
        except OSError:
            match = False
        if not match:
            to_fetch.append((rel, sz))

    need = sum(sz for _, sz in to_fetch)
    if need == 0:
        res['success'] = True
        res['message'] = 'already complete on HDD (nothing to fetch)'
        return res

    try:
        free = shutil.disk_usage(HDD_FS_PATH).free
    except OSError:
        free = None
    MARGIN = 5 * 1024 ** 3   # keep 5 GB headroom on the HDD
    if free is not None and need > free - MARGIN:
        res['message'] = f'not enough free space: need {human(need)}, have {human(free)}'
        return res

    fetched = nfiles = 0
    failed = []
    for rel, sz in to_fetch:
        ok, err = _xrdcp_download(f"{eos_run}/{rel}", local_root / rel)
        if ok:
            fetched += sz
            nfiles += 1
        else:
            failed.append(rel)
    res.update(restored_files=nfiles, fetched_bytes=fetched, fetched_h=human(fetched))
    if failed:
        res['success'] = False
        res['message'] = f'{len(failed)} file(s) failed to copy; {nfiles} restored'
        _log_delete(f"RESTORE partial hdd/{run}: {nfiles} ok, {len(failed)} failed")
    else:
        res['success'] = True
        res['message'] = f'restored {nfiles} files ({human(fetched)})'
        _log_delete(f"RESTORED hdd/{run}: {nfiles} files, {human(fetched)}")
    return res


def restore_runs(runs: list) -> dict:
    """Restore several runs; each independent. Reports per-run outcomes."""
    results = []
    fetched = 0
    for run in runs:
        r = restore_run(run)
        results.append(r)
        if r.get('success'):
            fetched += r.get('fetched_bytes', 0)
    return {'results': results, 'fetched_bytes': fetched, 'fetched_h': human(fetched),
            'n_restored': sum(1 for r in results if r.get('success')),
            'n_failed': sum(1 for r in results if not r.get('success'))}


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='Scan DREAM data disks for safe-to-delete runs')
    ap.add_argument('disk', choices=['hdd', 'ssd'])
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()
    out = scan(args.disk)
    if args.json:
        print(json.dumps(out, indent=2))
    else:
        u = out['usage']
        if u:
            print(f"{out['label']}: {human(u.get('free', 0))} free of {human(u.get('total', 0))} "
                  f"({u.get('pct', 0)}% used)")
        print(f"{'RUN':10} {'SIZE':>10}  {'SAFE':>5}  REASON")
        print('-' * 78)
        for r in out['runs']:
            print(f"{r['run']:10} {r['size_h']:>10}  {'YES' if r['safe'] else 'no':>5}  {r['reason']}")
        print('-' * 78)
        print(f"{out['n_safe']}/{out['n_runs']} runs safe to delete — "
              f"would free {out['safe_bytes_h']}")
