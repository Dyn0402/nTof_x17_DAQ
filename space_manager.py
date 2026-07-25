#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Disk-space management for nTof DREAM data.

Provides a read-only *scan/check* and a heavily guarded *delete* for freeing
space in the two places DREAM data lives, plus a *restore* that pulls runs back
from EOS:

  HDD  /mnt/data/x17/beam_july/runs/<run>/<subrun>/<component>/   processed -> EOS
  SSD  /home/mx17/july_dream/dream_run/<run>/<subrun>/            raw .fdf staging

Deletion works at three granularities, all sharing one safety model:

  * whole run     delete_run() / delete_runs()
  * whole subrun  delete_subrun() / delete_subruns()
  * component     delete_components() over (run, subrun, component) triples,
                  e.g. "drop the decoded waveforms from every subrun of run_71
                  but keep the combined hits"

COMPONENTS (see the table below) are the deletable pieces of a subrun:

  dream_run           SSD raw .fdf acquisition staging. NOT backed up to EOS (it
                      is in backup_config's exclude_dirs) — it is a duplicate of
                      the HDD raw_daq_data copy. Safe iff every staged .fdf is
                      present on EOS under that subrun's raw_daq_data at matching
                      size (the same bytes, just the authoritative copy). The
                      small non-.fdf staging artifacts (.prg/.cfg/.par/logs and
                      the scratch decoded_root) are reproducible and do not block.
  raw_fdf             the *.fdf files inside raw_daq_data. Deliberately NOT the
                      whole directory: run_time.txt, RunCtrl_*.log,
                      pedestal_run.txt, *.cfg and *.prg live there too, are
                      negligible in size, and are the run's provenance — they are
                      always preserved.
  decoded_root        decoded waveforms (whole directory)
  hits_root           per-FEU hits, pre-combination (whole directory)
  combined_hits_root  combined hits, the physics product (whole directory)

Safety model — a thing is only ever "safe to delete" when its data is provably
preserved on EOS: EVERY file it covers must be present on EOS at matching size
(relative path + byte size; data is write-once). This is exactly the check
backup_watcher uses. backup_watcher is push-only (it never removes anything from
EOS), so deleting locally cannot propagate to the backup.

The SSD raw staging is verified straight against EOS (its .fdf files looked up
under the sibling raw_daq_data path), not via the HDD copy. That is strictly
stronger than the old SSD -> HDD -> EOS chain — the HDD hop was only ever a proxy
for "the bytes survive somewhere else" — and it stays correct when the HDD-side
raw FDFs are pruned by a component delete.

Extra guards beyond the EOS verification:
  * the run named in config/current_run_state.json is never deletable WHILE
    daq_control actually shows a run in progress (see active_run);
  * the NEWEST run on disk (by mtime) is never deletable — between runs the state
    file may already point at the next run while this one still has files in
    flight;
  * a subrun missing its .subrun_complete marker is never deletable (possibly
    still being written / crashed mid-subrun). At run granularity ANY incomplete
    subrun blocks the whole run; at subrun/component granularity only the
    offending subrun is blocked.

Nothing here trusts a caller-supplied verdict: every delete entry point re-runs
the full verification itself, against a FRESH EOS listing, immediately before it
removes anything, and refuses any path that does not resolve inside the expected
root.

Performance — every xrdfs invocation costs ~5-10 s of connect + Kerberos
handshake against EOS, regardless of how much it lists. So this module issues
exactly ONE `xrdfs ls -l -R` for the entire runs tree and partitions the result
in memory, instead of one call per run (or per subrun). That turned a
linear-in-run-count scan into a constant one, and is what makes per-component
verification affordable at all — the naive form would need a call per component
per subrun.

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

# Run dirs are named by whoever created them: run_75, but also zs_singles,
# hwm_beam, adcdel, recov_0722... backup_watcher syncs every dir under runs/
# without a name filter, so anything on disk is backed up and must be visible
# here too — a run_\d+ filter used to hide ~40 of the 57 runs from this tool.
# The charset is deliberately tight (no dots at the start, no slashes) because
# this regex is also a delete-path guard; the resolved-path checks in every
# delete are the real protection.
RUN_NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*$')
# A subrun is a directory directly under a run dir, with the SAME name on SSD,
# HDD and EOS (e.g. m090On_dr500_r520_062, or pedestals). Same guard role.
SUBRUN_NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*$')

DISKS = {
    'hdd': {'label': 'HDD (processed runs)',   'root': HDD_RUNS_DIR,  'fs': HDD_FS_PATH},
    'ssd': {'label': 'SSD (raw dream_run)',    'root': SSD_DREAM_DIR, 'fs': SSD_FS_PATH},
}


# --- Components -------------------------------------------------------------
# 'disk'   : which of the two trees the component lives in.
# 'dir'    : directory under the subrun that holds the component ('' = the subrun
#            dir itself, used by dream_run whose .fdf files are loose).
# 'suffix' : if set, only files with this suffix belong to the component; the rest
#            of the directory is left alone. This is what keeps run_time.txt &
#            friends when the raw FDFs are dropped.
# 'derived': True for products the processor can recompute from an earlier stage;
#            used only for UI wording.
COMPONENTS = {
    'dream_run': {
        'label': 'Raw staging (SSD)',
        'blurb': 'Duplicate .fdf copies left behind by acquisition on the SSD. Never backed '
                 'up — verified against the raw_daq_data copy on EOS.',
        'disk': 'ssd', 'dir': '', 'suffix': None,
        'derived': False, 'order': 0,
    },
    'raw_fdf': {
        'label': 'Raw FDFs (HDD)',
        'blurb': 'The *.fdf files in raw_daq_data. Logs, run_time.txt, pedestal_run.txt, '
                 '*.cfg and *.prg in the same directory are always kept.',
        'disk': 'hdd', 'dir': 'raw_daq_data', 'suffix': '.fdf',
        'derived': False, 'order': 1,
    },
    'decoded_root': {
        'label': 'Decoded waveforms',
        'blurb': 'decoded_root/ — full waveforms, re-derivable from the FDFs.',
        'disk': 'hdd', 'dir': 'decoded_root', 'suffix': None,
        'derived': True, 'order': 2,
    },
    'hits_root': {
        'label': 'Per-FEU hits',
        'blurb': 'hits_root/ — per-FEU hits before combination.',
        'disk': 'hdd', 'dir': 'hits_root', 'suffix': None,
        'derived': True, 'order': 3,
    },
    'combined_hits_root': {
        'label': 'Combined hits',
        'blurb': 'combined_hits_root/ — the physics product. Also the processor\'s '
                 '"already done" marker (see reprocess_warnings).',
        'disk': 'hdd', 'dir': 'combined_hits_root', 'suffix': None,
        'derived': True, 'order': 4,
    },
}

COMPONENT_ORDER = sorted(COMPONENTS, key=lambda c: COMPONENTS[c]['order'])

# Directories under a subrun that belong to a component (for classifying the
# local walk of the HDD tree). dream_run is a separate tree so it is not in here.
_DIR_TO_COMPONENT = {c['dir']: k for k, c in COMPONENTS.items()
                     if c['disk'] == 'hdd' and c['dir']}

# processor_watcher._get_processed_file_nums() treats the LAST enabled pipeline
# stage's output directory as the "this file_num is done" marker. With the default
# do_combine=True that is combined_hits_root: delete it while the FDFs are still
# on disk and the watcher will re-decode, re-analyze and re-combine the subrun
# from scratch. Deleting decoded_root/hits_root while combined_hits_root survives
# is invisible to it.
REPROCESS_SENTINEL = 'combined_hits_root'
REPROCESS_INPUT    = 'raw_fdf'


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
    try:
        result = subprocess.run(
            ['xrdfs', url, 'ls', '-l', '-R', eos_dir],
            capture_output=True, text=True,
        )
    except OSError:
        return None   # xrdfs not installed / not on PATH -> cannot verify
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


# One recursive listing of the WHOLE runs tree, briefly cached. See the module
# docstring: the cost of xrdfs is per-invocation, not per-entry, so this single
# call replaces what used to be one call per run AND one per subrun. The TTL only
# exists so that the scan -> preflight -> confirm click path does not pay for it
# three times; every delete re-lists with force=True and never trusts the cache.
_REMOTE_TTL = 90.0
_remote_cache = {'t': 0.0, 'map': None}

# How long the last EOS listing took, and how many entries it returned. Used ONLY
# to drive the progress estimate in the GUI. `xrdfs ls -R` cannot be tracked for
# real: the whole cost is connect + Kerberos + the server-side directory walk,
# which emits nothing until it is finished and then dumps every line at once. So
# the listing phase can honestly show only an elapsed-vs-typical estimate; the
# phases after it are counted for real.
SCAN_HINT_PATH = os.path.join(BASE_DIR, 'logs', 'space_scan_hint.json')
DEFAULT_LISTING_S = 9.0


def _read_hint() -> dict:
    try:
        with open(SCAN_HINT_PATH) as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _write_hint(**kw):
    h = _read_hint()
    h.update(kw)
    try:
        os.makedirs(os.path.dirname(SCAN_HINT_PATH), exist_ok=True)
        with open(SCAN_HINT_PATH, 'w') as f:
            json.dump(h, f)
    except Exception:
        pass


def listing_estimate_s() -> float:
    """Best guess at how long the next EOS listing will take, from the last one."""
    try:
        v = float(_read_hint().get('listing_s') or 0)
    except (TypeError, ValueError):
        v = 0.0
    return v if 0.5 <= v <= 300 else DEFAULT_LISTING_S


def _noop_progress(phase, done, total, msg, item=None):
    pass


def _remote_runs_map(force: bool = False, progress=None):
    """{'<run>/<subrun>/<component>/<file>': size} for the entire EOS runs tree,
    or None if the listing failed. Failures are never cached."""
    progress = progress or _noop_progress
    now = time.time()
    if (not force and _remote_cache['map'] is not None
            and now - _remote_cache['t'] < _REMOTE_TTL):
        progress('listing', 1, 1, f"{len(_remote_cache['map'])} files (cached)")
        return _remote_cache['map']
    _, eos_runs = _eos_config()
    progress('listing', 0, None, 'contacting EOS (xrdfs)…')
    t0 = time.time()
    m = _remote_size_map(eos_runs)
    dt = time.time() - t0
    if m is not None:
        _remote_cache['map'] = m
        _remote_cache['t'] = time.time()
        _write_hint(listing_s=round(dt, 2), entries=len(m))
        progress('listing', 1, 1, f'{len(m)} files listed on EOS in {dt:.1f}s')
    else:
        progress('listing', 1, 1, 'EOS listing FAILED')
    return m


def _partition_by_run(rmap: dict) -> dict:
    """{run: {relpath-within-run: size}} from the flat whole-tree listing."""
    out = {}
    for k, v in rmap.items():
        run, _, rest = k.partition('/')
        if rest:
            out.setdefault(run, {})[rest] = v
    return out


def _run_remote(run: str, force: bool = False):
    """The EOS map for ONE run, sourced from the shared whole-tree listing, or
    None if that listing failed."""
    rmap = _remote_runs_map(force=force)
    if rmap is None:
        return None
    return _partition_by_run(rmap).get(run, {})


def invalidate_remote_cache():
    _remote_cache['map'] = None
    _remote_cache['t'] = 0.0


# --- Local tree ------------------------------------------------------------

def _component_of(rel_parts):
    """Classify a path relative to an HDD RUN root into (subrun, component).

    component is None for files that belong to no deletable component — the
    run-level loose files (dream_daq.log, run_config.json), the subrun-level loose
    files (hv_monitor.csv, n1081b_config.json, .subrun_complete) and the non-.fdf
    contents of raw_daq_data. Those are always preserved.
    """
    if len(rel_parts) < 2:
        return None, None                      # <run>/<file>
    subrun = rel_parts[0]
    if len(rel_parts) == 2:
        return subrun, None                    # <subrun>/<file>
    comp = _DIR_TO_COMPONENT.get(rel_parts[1])
    if comp is None:
        return subrun, None                    # unknown subdir -> not deletable
    suffix = COMPONENTS[comp]['suffix']
    if suffix and not rel_parts[-1].lower().endswith(suffix):
        return subrun, None                    # e.g. run_time.txt in raw_daq_data
    return subrun, comp


def _local_tree() -> dict:
    """Walk the HDD runs tree ONCE and the SSD dream_run tree once, and return

      {run: {'subruns': {subrun: {'components': {comp: {rel: size}},
                                  'other': {'files': n, 'size': b}}},
             'other': {'files': n, 'size': b}}}

    where component relpaths are relative to the RUN root, so they line up
    directly with _partition_by_run() keys for the EOS comparison.
    """
    tree = {}

    def _run_entry(run):
        return tree.setdefault(run, {'subruns': {}, 'other': {'files': 0, 'size': 0}})

    def _sub_entry(run, subrun):
        return _run_entry(run)['subruns'].setdefault(
            subrun, {'components': {}, 'other': {'files': 0, 'size': 0}})

    if HDD_RUNS_DIR.is_dir():
        for f in HDD_RUNS_DIR.rglob('*'):
            try:
                if not f.is_file() or f.is_symlink():
                    continue
                size = f.stat().st_size
                rel = f.relative_to(HDD_RUNS_DIR).as_posix()
            except OSError:
                continue
            parts = rel.split('/')
            run = parts[0]
            if not RUN_NAME_RE.match(run):
                continue
            subrun, comp = _component_of(parts[1:])
            if subrun is None:
                e = _run_entry(run)['other']
                e['files'] += 1
                e['size'] += size
                continue
            se = _sub_entry(run, subrun)
            if comp is None:
                se['other']['files'] += 1
                se['other']['size'] += size
            else:
                se['components'].setdefault(comp, {})['/'.join(parts[1:])] = size

    # dream_run/<run>/<subrun>/<file> — a separate tree on the SSD, mapped onto
    # the same run/subrun grid so the UI can show it as just another component.
    if SSD_DREAM_DIR.is_dir():
        for f in SSD_DREAM_DIR.rglob('*'):
            try:
                if not f.is_file() or f.is_symlink():
                    continue
                size = f.stat().st_size
                rel = f.relative_to(SSD_DREAM_DIR).as_posix()
            except OSError:
                continue
            parts = rel.split('/')
            if len(parts) < 3:
                continue                       # loose file, not inside a subrun
            run, subrun = parts[0], parts[1]
            if not RUN_NAME_RE.match(run):
                continue
            _sub_entry(run, subrun)['components'].setdefault(
                'dream_run', {})['/'.join(parts[1:])] = size

    return tree


# --- Helpers ---------------------------------------------------------------

def _run_num(name: str) -> int:
    m = re.search(r'(\d+)', name)
    return int(m.group(1)) if m else -1


def _run_mtime(run: str) -> float:
    """When a run was last WRITTEN, across both disks (-1 if absent). Used to
    order runs oldest-first, which is what space_watcher's newest-N reserve and
    the GUI listing both mean by 'newest'. Run names are free-form (run_75, but
    also zs_singles, hwm_beam), so a numeric sort cannot be relied on.

    Directory mtimes are useless for this: removing anything re-stamps its
    parent, so pruning a run would make it look freshly written and push exactly
    the runs we have been reclaiming to the "newest" end. Preference order per
    subrun, each immune to our own deletions:
      1. its .subrun_complete marker — written when the DAQ finished it;
      2. the newest FILE it still contains (deleting siblings does not restamp
         the survivors);
      3. the subrun directory, for a subrun that holds no files at all.
    """
    t = -1.0
    for root in (HDD_RUNS_DIR / run, SSD_DREAM_DIR / run):
        try:
            subs = list(root.iterdir())
        except OSError:
            continue
        best = -1.0
        for sub in subs:
            marker = sub / '.subrun_complete'
            try:
                if marker.is_file():
                    best = max(best, marker.stat().st_mtime)
                    continue
            except OSError:
                pass
            best = max(best, _newest_file_mtime(sub))
        if best < 0:
            try:
                best = root.stat().st_mtime
            except OSError:
                continue
        t = max(t, best)
    return t


def _newest_file_mtime(path: Path) -> float:
    """Newest mtime of any FILE under path (-1 if there are none), falling back
    to path's own mtime when it holds no files at all.

    File mtimes, never directory mtimes: unlinking a file re-stamps its parent
    directory, so any dir-based "was this touched recently?" test reports our own
    deletions as fresh writes. That is what made ten fully-backed-up runs read as
    "possibly mid-write" the moment a component prune ran inside their one
    unmarked subrun.
    """
    t = -1.0
    try:
        for f in path.rglob('*'):
            try:
                if f.is_file() and not f.is_symlink():
                    t = max(t, f.stat().st_mtime)
            except OSError:
                pass
    except OSError:
        return -1.0
    if t < 0:
        try:
            return path.stat().st_mtime
        except OSError:
            return -1.0
    return t


# Line markers in daq_control's tmux pane (same source flask_app/daq_status.py
# scrapes). Scanned newest-first: the first marker hit decides. Anything not
# matching either list (e.g. periodic [status] lines) keeps scanning.
_DAQ_IDLE_FLAGS = ('Run complete', 'donzo', 'Daq control session started')
_DAQ_BUSY_FLAGS = ('Dream DAQ starting', 'Prepping DAQs', 'Ramping HVs for',
                   'Starting DAQ Control', 'Finished with sub run', '[pause]',
                   'Stopping DAQ process', 'Dream DAQ taking pedestals')


def daq_mid_run() -> bool:
    """True while daq_control's tmux pane shows a run in progress. Defaults to
    False when tmux/the pane is missing or no marker is visible — a live run is
    still protected then by the newest-run and incomplete-subrun guards, and
    defaulting True would recreate the stale-'acquiring' problem this solves."""
    try:
        out = subprocess.run(
            ['tmux', 'capture-pane', '-pJ', '-S', '-50', '-t', 'daq_control:0.0'],
            capture_output=True, text=True,
        )
    except OSError:
        return False
    if out.returncode != 0:
        return False
    for line in reversed(out.stdout.splitlines()):
        if any(f in line for f in _DAQ_IDLE_FLAGS):
            return False
        if any(f in line for f in _DAQ_BUSY_FLAGS):
            return True
    return False


def active_run() -> str:
    """Name of the run currently being acquired (never deletable), or ''.

    config/current_run_state.json is a LAST-SEEN-run tracker — the GUI writes it
    for its event counter and never clears it when a run ends — so the name only
    counts as active while daq_control actually shows a run in progress.
    """
    try:
        with open(CURRENT_RUN_STATE) as f:
            name = json.load(f).get('run_name', '') or ''
    except Exception:
        return ''
    return name if (name and daq_mid_run()) else ''


def newest_run() -> str:
    """Name of the run dir with the most recent mtime on either disk (never
    deletable — it may still be receiving files even if the state file already
    points elsewhere), or ''."""
    newest, newest_t = '', -1.0
    for root in (HDD_RUNS_DIR, SSD_DREAM_DIR):
        try:
            entries = list(root.iterdir())
        except OSError:
            continue
        for p in entries:
            if not (p.is_dir() and RUN_NAME_RE.match(p.name)):
                continue
            try:
                t = p.stat().st_mtime
            except OSError:
                continue
            if t > newest_t:
                newest, newest_t = p.name, t
    return newest


def subrun_complete(run: str, subrun: str) -> bool:
    """True when daq_control's end-of-subrun marker is present. The marker is
    written on the HDD side, so it is authoritative for the SSD staging too."""
    return (HDD_RUNS_DIR / run / subrun / '.subrun_complete').is_file()


# An unmarked subrun means one of two very different things: it is being written
# right now, or the DAQ was stopped in the middle of it days ago (every long run
# here ends up with exactly one of those). Only the first is a reason to refuse a
# delete, and the difference is visible in the mtime — so the marker guard is
# time-bounded. Data safety does not rest on this: an unmarked subrun still has
# to be fully present on EOS like everything else.
INCOMPLETE_GRACE_S = 2 * 3600


def mid_write_subrun(run: str, subrun: str) -> bool:
    """True when a subrun has no .subrun_complete marker AND still holds a file
    written recently on either disk — i.e. it may be being written right now.

    Judged on FILE mtimes (see _newest_file_mtime): a deletion inside the subrun
    restamps its directories, so a directory-based test flags our own pruning as
    a live write and locks the run out of any further deletion.
    """
    if subrun_complete(run, subrun):
        return False
    now = time.time()
    for root in (HDD_RUNS_DIR, SSD_DREAM_DIR):
        t = _newest_file_mtime(root / run / subrun)
        if t > 0 and now - t < INCOMPLETE_GRACE_S:
            return True
    return False


def incomplete_subruns(run: str, recent_only: bool = False) -> list:
    """Subruns of a run missing their .subrun_complete marker. With
    recent_only=True, just the ones that may still be mid-write — those are what
    block deleting the run as a whole."""
    out = []
    try:
        for sub in sorted((HDD_RUNS_DIR / run).iterdir()):
            if not sub.is_dir() or (sub / '.subrun_complete').is_file():
                continue
            if recent_only and not mid_write_subrun(run, sub.name):
                continue
            out.append(sub.name)
    except OSError:
        pass
    return out


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

def _verify_files(local: dict, remote: dict) -> dict:
    """Compare a {rel: size} local set against a run's EOS map."""
    ok = missing = mismatch = 0
    for rel, sz in local.items():
        rsz = remote.get(rel)
        if rsz == sz:
            ok += 1
        elif rsz is None:
            missing += 1
        else:
            mismatch += 1
    return {'ok': ok, 'missing': missing, 'mismatch': mismatch}


def _staging_to_eos(rel: str):
    """Map an SSD staging relpath (<subrun>/[dirs/]<name>.fdf, relative to the
    run root) onto where its authoritative copy lives on EOS
    (<subrun>/raw_daq_data/<name>.fdf), or None if it cannot be placed."""
    parts = rel.split('/')
    if len(parts) < 2:
        return None                     # loose file at the run root — no subrun
    return '/'.join([parts[0], 'raw_daq_data', parts[-1]])


def _verify_staging(local: dict, remote: dict, what: str = 'staging') -> dict:
    """Verdict for a set of SSD staging files ({relpath-within-run: size}).

    Only the .fdf files carry data worth protecting; each is looked up under the
    sibling raw_daq_data path on EOS. The non-.fdf staging artifacts (.prg/.cfg/
    .par, logs, the scratch decoded_root) are reproducible and never block.
    """
    res = {'files': len(local), 'size': sum(local.values()),
           'ok': 0, 'missing': 0, 'mismatch': 0, 'safe': False, 'reason': '',
           'fdf_total': 0, 'staging_files': 0}
    if not local:
        res['reason'] = 'nothing present'
        return res
    fdf = {rel: sz for rel, sz in local.items() if rel.lower().endswith('.fdf')}
    res['fdf_total'] = len(fdf)
    res['staging_files'] = len(local) - len(fdf)
    if not fdf:
        res['safe'] = True
        res['reason'] = f'{len(local)} reproducible staging file(s), no .fdf'
        return res

    mapped, unplaceable = {}, 0
    for rel, sz in fdf.items():
        key = _staging_to_eos(rel)
        if key is None:
            unplaceable += 1
        else:
            mapped[key] = sz
    counts = _verify_files(mapped, remote)
    counts['missing'] += unplaceable
    res.update(counts)
    if counts['missing'] == 0 and counts['mismatch'] == 0:
        res['safe'] = True
        res['reason'] = (f"all {counts['ok']} raw .fdf verified on EOS via raw_daq_data"
                         + (f"; {res['staging_files']} {what} file(s) reproducible"
                            if res['staging_files'] else ''))
    else:
        res['reason'] = (f"{counts['missing']} missing + {counts['mismatch']} "
                         f"size-mismatched under raw_daq_data on EOS — NOT safe")
    return res


def _verify_component(comp: str, local: dict, remote: dict) -> dict:
    """Verdict for one component of one subrun. `local` is {relpath-within-run:
    size} for the component's files; `remote` is the EOS map for that run."""
    res = {'component': comp, 'files': len(local), 'size': sum(local.values()),
           'ok': 0, 'missing': 0, 'mismatch': 0, 'safe': False, 'reason': ''}
    if not local:
        res['reason'] = 'nothing present'
        return res

    if COMPONENTS[comp]['disk'] == 'ssd':
        v = _verify_staging(local, remote)
        v['component'] = comp
        return v

    counts = _verify_files(local, remote)
    res.update(counts)
    if counts['missing'] == 0 and counts['mismatch'] == 0:
        res['safe'] = True
        res['reason'] = f"all {counts['ok']} files verified on EOS"
    else:
        res['reason'] = (f"{counts['missing']} missing + {counts['mismatch']} "
                         f"size-mismatched on EOS")
    return res


def verify_hdd_run(run: str, force: bool = False) -> dict:
    """Compare an HDD run against EOS, file by file (relpath + size), over the
    complete run tree — raw subrun data, processing outputs, loose files and
    markers alike. Sources the single whole-tree EOS listing."""
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
    remote = _run_remote(run, force=force)
    if remote is None:
        res['unverifiable'] = True
        res['reason'] = 'could not list runs on EOS (Kerberos/network?) — NOT safe'
        return res
    counts = _verify_files(local, remote)
    res.update(counts)
    if (counts['missing'] == 0 and counts['mismatch'] == 0
            and counts['ok'] == len(local) and len(local) > 0):
        res['safe'] = True
        res['reason'] = f"all {counts['ok']} files verified on EOS"
    elif len(local) == 0:
        res['reason'] = 'run directory is empty'
    else:
        res['reason'] = (f"{counts['missing']} missing + {counts['mismatch']} "
                         f"size-mismatched on EOS")
    return res


def verify_ssd_run(run: str, force: bool = False) -> dict:
    """An SSD raw run is safe iff every staged .fdf is on EOS (under that
    subrun's raw_daq_data) at matching size. See the module docstring on why this
    goes straight to EOS rather than through the HDD copy."""
    root = SSD_DREAM_DIR / run
    res = {'run': run, 'disk': 'ssd', 'size': 0, 'files': 0,
           'fdf_total': 0, 'staging_files': 0,
           'safe': False, 'reason': '', 'unverifiable': False}
    if not root.is_dir():
        res['reason'] = 'run directory not found on SSD'
        return res
    local = _local_size_map(root)
    res['files'] = len(local)
    res['size'] = sum(local.values())
    remote = _run_remote(run, force=force)
    if remote is None:
        res['unverifiable'] = True
        res['reason'] = 'could not list runs on EOS (Kerberos/network?) — NOT safe'
        return res
    v = _verify_staging(local, remote)
    size, files = res['size'], res['files']
    res.update(v)
    res.update(run=run, disk='ssd', size=size, files=files, unverifiable=False)
    return res


def verify_run(disk: str, run: str, force: bool = False) -> dict:
    return (verify_hdd_run(run, force=force) if disk == 'hdd'
            else verify_ssd_run(run, force=force))


# --- Subrun verification ---------------------------------------------------
# A subrun is a self-contained directory mirrored across all three tiers (SSD raw
# -> HDD processed -> EOS), so the run-level safety model applies unchanged, one
# directory deeper. The only extra signal is `.subrun_complete`, the HDD-side
# marker the DAQ writes when a subrun finishes; it is mirrored to EOS like any
# other file, so it never trips the file-set comparison.

def verify_hdd_subrun(run: str, subrun: str, force: bool = False) -> dict:
    """Compare one HDD subrun directory against its EOS mirror, exactly as
    verify_hdd_run does for a whole run, scoped one level deeper."""
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
    remote = _run_remote(run, force=force)
    if remote is None:
        res['unverifiable'] = True
        res['reason'] = 'could not list runs on EOS (Kerberos/network?) — NOT safe'
        return res
    # Re-scope the run map to this subrun.
    prefix = subrun + '/'
    sub_remote = {k[len(prefix):]: v for k, v in remote.items() if k.startswith(prefix)}
    counts = _verify_files(local, sub_remote)
    res.update(counts)
    if (counts['missing'] == 0 and counts['mismatch'] == 0
            and counts['ok'] == len(local) and len(local) > 0):
        res['safe'] = True
        res['reason'] = f"all {counts['ok']} files verified on EOS"
    elif len(local) == 0:
        res['reason'] = 'subrun directory is empty'
    else:
        res['reason'] = (f"{counts['missing']} missing + {counts['mismatch']} "
                         f"size-mismatched on EOS")
    return res


def verify_ssd_subrun(run: str, subrun: str, force: bool = False) -> dict:
    """An SSD raw subrun is safe iff every staged .fdf is on EOS under that
    subrun's raw_daq_data at matching size. Mirrors verify_ssd_run, scoped to one
    subrun."""
    root = SSD_DREAM_DIR / run / subrun
    res = {'run': run, 'subrun': subrun, 'disk': 'ssd', 'size': 0, 'files': 0,
           'fdf_total': 0, 'staging_files': 0,
           'safe': False, 'reason': '', 'unverifiable': False,
           'complete': subrun_complete(run, subrun)}
    if not root.is_dir():
        res['reason'] = 'subrun directory not found on SSD'
        return res
    local = {f"{subrun}/{rel}": sz for rel, sz in _local_size_map(root).items()}
    res['files'] = len(local)
    res['size'] = sum(local.values())
    remote = _run_remote(run, force=force)
    if remote is None:
        res['unverifiable'] = True
        res['reason'] = 'could not list runs on EOS (Kerberos/network?) — NOT safe'
        return res
    v = _verify_staging(local, remote)
    size, files, complete = res['size'], res['files'], res['complete']
    res.update(v)
    res.update(run=run, subrun=subrun, disk='ssd', size=size, files=files,
               complete=complete, unverifiable=False)
    return res


def verify_subrun(disk: str, run: str, subrun: str, force: bool = False) -> dict:
    return (verify_hdd_subrun(run, subrun, force=force) if disk == 'hdd'
            else verify_ssd_subrun(run, subrun, force=force))


# --- Local guards ----------------------------------------------------------

def _apply_local_guards(v: dict, run: str, act: str, newest: str) -> dict:
    """Downgrade a verify verdict for runs that must never be deleted no matter
    what EOS says: the active run, the newest run on disk, and runs with
    incomplete subruns. The guard text is APPENDED to the EOS verdict, never
    replacing it — the guard says why the run is not deletable, not whether it is
    backed up, and hiding the backup status reads as 'not on EOS' in the GUI."""
    v['active'] = (run == act)
    v['newest'] = (run == newest)
    guard = ''
    if v['active']:
        guard = 'currently acquiring — never deletable while active'
    elif v['newest']:
        guard = 'newest run on disk (possibly still being written) — refusing'
    else:
        inc = incomplete_subruns(run, recent_only=True)
        if inc:
            guard = (f'{len(inc)} unmarked subrun(s) touched in the last '
                     f'{INCOMPLETE_GRACE_S / 3600:.0f} h (possibly mid-write) — refusing')
    if guard:
        v['safe'] = False
        v['reason'] = f"{v['reason']} · {guard}" if v.get('reason') else guard
    return v


def _run_guard(run: str, act: str, newest: str) -> str:
    """Run-wide reason this run may not be touched at all, or ''. Unlike
    _apply_local_guards this does NOT consider incomplete subruns: at subrun and
    component granularity an incomplete subrun blocks only itself."""
    if run == act:
        return 'currently acquiring — never deletable while active'
    if run == newest:
        return 'newest run on disk (possibly still being written) — refusing'
    return ''


# --- Scan ------------------------------------------------------------------

def list_runs(disk: str) -> list:
    """Run directory names on a disk, OLDEST FIRST (by directory mtime).

    space_watcher's newest-N reserve and its oldest-first deletion order both
    read this ordering, and run names are free-form (run_75, zs_singles,
    hwm_beam), so mtime is the only meaningful "age" available.
    """
    root = DISKS[disk]['root']
    if not root.is_dir():
        return []
    runs = [p.name for p in root.iterdir() if p.is_dir() and RUN_NAME_RE.match(p.name)]
    return sorted(runs, key=lambda r: (_run_mtime(r), r))


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


def local_scan(disk: str) -> dict:
    """What is on the disk right now — local stat() only, no EOS access, so it is
    instant and works even when Kerberos/the network is down. Reports each run's
    size plus the local guard flags (active / newest / incomplete subruns). Only
    the full EOS scan() can mark a run safe to delete."""
    if disk not in DISKS:
        raise ValueError(f'unknown disk {disk!r}')
    act = active_run()
    newest = newest_run()
    root = DISKS[disk]['root']
    results = []
    for run in list_runs(disk):
        local = _local_size_map(root / run)
        inc = incomplete_subruns(run, recent_only=True)
        r = {'run': run, 'disk': disk,
             'size': sum(local.values()), 'files': len(local),
             'active': run == act, 'newest': run == newest,
             'incomplete': len(inc)}
        r['size_h'] = human(r['size'])
        if r['active']:
            r['note'] = 'currently acquiring — never deletable while active'
        elif r['newest']:
            r['note'] = 'newest run on disk — never deletable'
        elif inc:
            r['note'] = f'{len(inc)} unmarked subrun(s) recently touched'
        else:
            r['note'] = 'not yet verified against EOS'
        results.append(r)
    total = sum(r['size'] for r in results)
    return {
        'disk': disk, 'label': DISKS[disk]['label'],
        'runs': results, 'n_runs': len(results),
        'total_bytes': total, 'total_h': human(total),
        'active_run': act, 'newest_run': newest,
        'usage': disk_usage().get(disk, {}),
        'scanned_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }


def scan(disk: str, runs=None, force: bool = True, progress=None) -> dict:
    """Verify every run (or a subset) on a disk; return per-run verdicts. ONE EOS
    listing covers the whole tree, not one per run — this is the check the user
    explicitly asks for, so it re-lists by default rather than reusing the cache."""
    progress = progress or _noop_progress
    if disk not in DISKS:
        raise ValueError(f'unknown disk {disk!r}')
    names = runs if runs else list_runs(disk)
    act = active_run()
    newest = newest_run()
    _remote_runs_map(force=force, progress=progress)     # one shared listing
    results = []
    for i, run in enumerate(names):
        progress('verify', i, len(names), f'verifying {run}')
        v = verify_run(disk, run)
        v = _apply_local_guards(v, run, act, newest)
        v['size_h'] = human(v.get('size', 0))
        results.append(v)
    progress('verify', len(names), len(names), 'verification complete')
    safe_bytes = sum(r['size'] for r in results if r['safe'])
    return {
        'disk': disk, 'label': DISKS[disk]['label'],
        'runs': results,
        'n_runs': len(results),
        'n_safe': sum(1 for r in results if r['safe']),
        'safe_bytes': safe_bytes, 'safe_bytes_h': human(safe_bytes),
        'active_run': act, 'newest_run': newest,
        'usage': disk_usage().get(disk, {}),
        'scanned_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }


def scan_subruns(disk: str, run: str, force: bool = False) -> dict:
    """Per-subrun verdicts for one run — the finer-grained sibling of scan().

    Every subrun is verified independently against the shared EOS listing. A
    subrun is only ever deletable once its HDD copy carries the
    `.subrun_complete` marker AND it verifies, so the in-progress subrun can
    never be a candidate even if a verification happens to pass mid-write.
    Completed subruns of the acquiring run ARE eligible — that is the whole
    point: a long run can be pruned while it is still being taken.
    """
    if disk not in DISKS:
        raise ValueError(f'unknown disk {disk!r}')
    if not RUN_NAME_RE.match(run or ''):
        raise ValueError(f'invalid run name {run!r}')
    act = active_run()
    newest = newest_run()
    is_active_run = (run == act)
    _remote_runs_map(force=force)                        # one shared listing
    results = []
    for sub in list_subruns(disk, run):
        v = verify_subrun(disk, run, sub)
        v['active_run'] = is_active_run
        v['held_active'] = mid_write_subrun(run, sub)
        # A subrun that may still be being written is never deletable, whatever
        # EOS says. An unmarked but long-untouched one (the DAQ was stopped mid
        # subrun days ago) is judged on its EOS verdict like any other.
        if v['held_active'] and v['safe']:
            v['safe'] = False
            v['reason'] = 'unmarked and recently written — never deletable yet'
        results.append(v)
        v['size_h'] = human(v.get('size', 0))
    safe_bytes = sum(r['size'] for r in results if r['safe'])
    total_bytes = sum(r['size'] for r in results)
    return {
        'disk': disk, 'run': run, 'active': is_active_run,
        'subruns': results,
        'n_subruns': len(results),
        'n_safe': sum(1 for r in results if r['safe']),
        'safe_bytes': safe_bytes, 'safe_bytes_h': human(safe_bytes),
        'total_bytes': total_bytes, 'total_bytes_h': human(total_bytes),
        'active_run': act, 'newest_run': newest,
        'scanned_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }


def component_scan(verify: bool = True, force: bool = False, progress=None) -> dict:
    """The run -> subrun -> component tree with a delete verdict on every
    component, across BOTH disks.

    verify=False skips EOS entirely (instant, works offline): sizes and local
    guards are filled in but nothing is marked safe. verify=True issues ONE
    recursive EOS listing and verifies every component from it.

    progress(phase, done, total, msg) is called through the three phases — 'scan'
    (local walk), 'listing' (the opaque EOS call) and 'verify' (per run, genuinely
    counted).
    """
    progress = progress or _noop_progress
    progress('scan', 0, None, 'walking the local run trees…')
    act = active_run()
    newest = newest_run()
    tree = _local_tree()
    progress('scan', 1, 1, f'{len(tree)} run(s) on disk')

    rmap = _remote_runs_map(force=force, progress=progress) if verify else None
    unverifiable = verify and rmap is None
    by_run = _partition_by_run(rmap) if rmap is not None else {}

    runs_out = []
    names = sorted(tree, key=lambda r: (_run_mtime(r), r))
    for idx, run in enumerate(names):
        progress('verify', idx, len(names), f'verifying {run}')
        rentry = tree[run]
        remote = by_run.get(run, {})
        guard = _run_guard(run, act, newest)

        subs_out = []
        run_comp = {c: {'size': 0, 'files': 0, 'safe_size': 0, 'n_safe': 0, 'n_total': 0}
                    for c in COMPONENT_ORDER}
        for subrun in sorted(rentry['subruns'], key=_subrun_key):
            sentry = rentry['subruns'][subrun]
            complete = subrun_complete(run, subrun)
            sub_guard = guard or ('unmarked and recently written (possibly mid-write) — refusing'
                                  if mid_write_subrun(run, subrun) else '')

            comps_out = {}
            for comp in COMPONENT_ORDER:
                local = sentry['components'].get(comp)
                if not local:
                    continue
                if verify and not unverifiable:
                    v = _verify_component(comp, local, remote)
                else:
                    v = {'component': comp, 'files': len(local),
                         'size': sum(local.values()), 'ok': 0, 'missing': 0,
                         'mismatch': 0, 'safe': False,
                         'reason': ('could not list EOS (Kerberos/network?) — NOT safe'
                                    if unverifiable else 'not yet verified against EOS')}
                if sub_guard:
                    v['safe'] = False
                    v['reason'] = f"{v['reason']} · {sub_guard}" if v['reason'] else sub_guard
                v['size_h'] = human(v['size'])
                comps_out[comp] = v

                agg = run_comp[comp]
                agg['size'] += v['size']
                agg['files'] += v['files']
                agg['n_total'] += 1
                if v['safe']:
                    agg['n_safe'] += 1
                    agg['safe_size'] += v['size']

            sub_size = sum(v['size'] for v in comps_out.values()) + sentry['other']['size']
            subs_out.append({
                'subrun': subrun, 'run': run, 'complete': complete,
                'guard': sub_guard, 'components': comps_out,
                'other': sentry['other'], 'other_h': human(sentry['other']['size']),
                'size': sub_size, 'size_h': human(sub_size),
            })

        run_comp = {c: v for c, v in run_comp.items() if v['n_total']}
        for c, v in run_comp.items():
            v['size_h'] = human(v['size'])
            v['safe_size_h'] = human(v['safe_size'])
        run_size = (sum(v['size'] for v in run_comp.values())
                    + rentry['other']['size']
                    + sum(s['other']['size'] for s in subs_out))
        runs_out.append({
            'run': run, 'guard': guard,
            'active': run == act, 'newest': run == newest,
            'components': run_comp, 'subruns': subs_out,
            'n_subruns': len(subs_out),
            'other': rentry['other'], 'other_h': human(rentry['other']['size']),
            'size': run_size, 'size_h': human(run_size),
        })

    progress('verify', len(names), len(names), 'verification complete')

    totals = {c: {'size': 0, 'safe_size': 0} for c in COMPONENT_ORDER}
    for r in runs_out:
        for c, v in r['components'].items():
            totals[c]['size'] += v['size']
            totals[c]['safe_size'] += v['safe_size']
    for c, v in totals.items():
        v['size_h'] = human(v['size'])
        v['safe_size_h'] = human(v['safe_size'])

    grand = sum(r['size'] for r in runs_out)
    safe_total = sum(v['safe_size'] for v in totals.values())
    return {
        'runs': runs_out, 'n_runs': len(runs_out),
        'components': {c: dict(COMPONENTS[c], key=c) for c in COMPONENT_ORDER},
        'component_order': COMPONENT_ORDER,
        'totals': totals,
        'total_bytes': grand, 'total_h': human(grand),
        'safe_bytes': safe_total, 'safe_bytes_h': human(safe_total),
        'verified': bool(verify and not unverifiable),
        'unverifiable': unverifiable,
        'active_run': act, 'newest_run': newest,
        'reprocess_sentinel': REPROCESS_SENTINEL,
        'usage': disk_usage(),
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


def _batch_listing():
    """Take ONE fresh whole-tree EOS listing to verify a batch of deletes against.

    A listing costs a full xrdfs round trip (measured ~20-25 s against this EOS),
    so re-taking it per run turned a 36-run freeing pass into ~15 minutes of
    almost pure waiting. Reusing one listing across the batch is safe because
    staleness here is FAIL-SAFE in one direction only: nothing we run ever
    removes files from EOS (backup_watcher is push-only), so a file listed as
    present is still present, while a file backed up *since* the listing merely
    reads as not-yet-safe and its delete is refused. The local side — which is
    the side that changes under us — is always re-read from disk immediately
    before unlinking.
    """
    invalidate_remote_cache()
    _remote_runs_map(force=True)


def delete_run(disk: str, run: str, force: bool = True) -> dict:
    """Delete one run directory from a disk, but ONLY after re-verifying, here,
    that it is safe. Never trusts a caller verdict.

    Guards, in order:
      1. disk is known; run matches RUN_NAME_RE.
      2. target resolves to a real directory sitting DIRECTLY under the disk root
         (no symlinks, no traversal, no partial-name tricks).
      3. run is not the active run, not the newest run on disk, and has no subrun
         missing its .subrun_complete marker.
      4. verify_run() says SAFE.

    force=False reuses the cached EOS listing (<=90 s old) instead of paying a
    fresh whole-tree xrdfs per run — see _batch_listing() for why staleness here
    is fail-safe. The LOCAL side is always re-read from disk.
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

    verdict = verify_run(disk, run, force=force)
    verdict = _apply_local_guards(verdict, run, active_run(), newest_run())
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
    _batch_listing()
    results = []
    freed = 0
    for run in runs:
        r = delete_run(disk, run, force=False)
        results.append(r)
        if r.get('success'):
            freed += r.get('freed_bytes', 0)
    return {'results': results, 'freed_bytes': freed, 'freed_h': human(freed),
            'n_deleted': sum(1 for r in results if r.get('success')),
            'n_failed': sum(1 for r in results if not r.get('success'))}


def delete_subrun(disk: str, run: str, subrun: str, force: bool = True) -> dict:
    """Delete one subrun directory from a disk, only after re-verifying, here,
    that it is safe. Same never-trust-the-caller model as delete_run, one level
    deeper.

    Guards, in order:
      1. disk is known; run and subrun match their name regexes.
      2. target resolves to a real directory sitting DIRECTLY under a real run
         directory that itself sits DIRECTLY under the disk root (no symlinks, no
         traversal, no partial-name tricks). The run dir is never itself the target.
      3. the run is neither active nor the newest on disk, and the subrun is
         marked `.subrun_complete` (an in-progress subrun is never deletable).
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

    # A subrun that may still be being written is never deletable. The newest run
    # is off-limits at run granularity but NOT here: pruning completed subruns of
    # the run being taken is exactly what keeps a long run from filling the disk.
    if mid_write_subrun(run, subrun):
        _log_delete(f"REFUSED {disk}/{run}/{subrun}: unmarked and recently written")
        return {'success': False,
                'message': f'{run}/{subrun} is unmarked and recently written — refusing'}

    verdict = verify_subrun(disk, run, subrun, force=force)
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
    _batch_listing()
    results = []
    freed = 0
    for sub in subruns:
        r = delete_subrun(disk, run, sub, force=False)
        results.append(r)
        if r.get('success'):
            freed += r.get('freed_bytes', 0)
    if disk == 'ssd':
        _prune_empty_dream_run_dirs([run])
    return {'results': results, 'freed_bytes': freed, 'freed_h': human(freed),
            'n_deleted': sum(1 for r in results if r.get('success')),
            'n_failed': sum(1 for r in results if not r.get('success'))}


# --- Component delete ------------------------------------------------------

def _component_path(run: str, subrun: str, comp: str):
    """(path, root) for a component of a subrun, or (None, None) if the names or
    the component key are not valid. The caller still has to confirm the resolved
    path sits inside root."""
    if comp not in COMPONENTS:
        return None, None
    if not RUN_NAME_RE.match(run or '') or not SUBRUN_NAME_RE.match(subrun or ''):
        return None, None
    spec = COMPONENTS[comp]
    root = DISKS[spec['disk']]['root']
    if spec['disk'] == 'ssd':
        return root / run / subrun, root
    return root / run / subrun / spec['dir'], root


def _normalize_items(items):
    """[(run, subrun, component)] from the wire format, de-duplicated and with
    unknown/invalid entries dropped."""
    out, seen = [], set()
    for it in items or []:
        if not isinstance(it, dict):
            continue
        run = it.get('run')
        subrun = it.get('subrun')
        comp = it.get('component')
        if comp not in COMPONENTS:
            continue
        if not RUN_NAME_RE.match(run or '') or not SUBRUN_NAME_RE.match(subrun or ''):
            continue
        key = (run, subrun, comp)
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _component_contents(run: str, subrun: str, comp: str) -> dict:
    """{relpath-within-run: (size, Path)} for the files this component covers,
    read fresh from disk (not from a cached scan). Keys line up with the EOS map;
    the Paths are what a delete actually unlinks, so verification and removal are
    guaranteed to be talking about the same file set."""
    path, _root = _component_path(run, subrun, comp)
    if path is None or not path.is_dir():
        return {}
    spec = COMPONENTS[comp]
    out = {}
    for f in path.rglob('*'):
        try:
            if not f.is_file() or f.is_symlink():
                continue
            if spec['suffix'] and not f.name.lower().endswith(spec['suffix']):
                continue
            size = f.stat().st_size
        except OSError:
            continue
        inner = f.relative_to(path).as_posix()
        rel = (f"{subrun}/{inner}" if spec['disk'] == 'ssd'
               else f"{subrun}/{spec['dir']}/{inner}")
        out[rel] = (size, f)
    return out


def _component_local_files(run: str, subrun: str, comp: str) -> dict:
    """{relpath-within-run: size} — the verification view of _component_contents."""
    return {rel: sz for rel, (sz, _) in _component_contents(run, subrun, comp).items()}


def preflight_components(items) -> dict:
    """Dry-run a component selection: what it would free, what is refused, and
    which subruns the processor would reprocess afterwards.

    Uses the cached EOS listing (this runs on every selection change); the real
    delete re-verifies against a fresh one.
    """
    triples = _normalize_items(items)
    act, newest = active_run(), newest_run()
    rmap = _remote_runs_map()
    by_run = _partition_by_run(rmap) if rmap is not None else {}

    ok_items, refused = [], []
    freed = 0
    selected = {}                        # {(run, subrun): {components}}
    for run, subrun, comp in triples:
        selected.setdefault((run, subrun), set()).add(comp)
        local = _component_local_files(run, subrun, comp)
        entry = {'run': run, 'subrun': subrun, 'component': comp,
                 'label': COMPONENTS[comp]['label'],
                 'size': sum(local.values()), 'files': len(local)}
        entry['size_h'] = human(entry['size'])

        guard = _run_guard(run, act, newest)
        if not guard and mid_write_subrun(run, subrun):
            guard = 'unmarked and recently written (possibly mid-write)'
        if guard:
            entry['reason'] = guard
            refused.append(entry)
            continue
        if not local:
            entry['reason'] = 'nothing present'
            refused.append(entry)
            continue
        if rmap is None:
            entry['reason'] = 'could not list EOS (Kerberos/network?) — NOT safe'
            refused.append(entry)
            continue
        v = _verify_component(comp, local, by_run.get(run, {}))
        if not v['safe']:
            entry['reason'] = v['reason']
            refused.append(entry)
            continue
        entry['reason'] = v['reason']
        ok_items.append(entry)
        freed += entry['size']

    # Deleting the processor's "done" marker while its input FDFs survive makes
    # the watcher redo the whole pipeline for that subrun. Only warn when the
    # FDFs will actually still be there afterwards.
    warnings = []
    for (run, subrun), comps in sorted(selected.items()):
        if REPROCESS_SENTINEL not in comps:
            continue
        if not any(i['run'] == run and i['subrun'] == subrun
                   and i['component'] == REPROCESS_SENTINEL for i in ok_items):
            continue          # refused anyway, nothing will be removed
        if REPROCESS_INPUT in comps:
            continue          # FDFs go too -> nothing left to reprocess from
        if _component_local_files(run, subrun, REPROCESS_INPUT):
            warnings.append({'run': run, 'subrun': subrun,
                             'message': f'{run}/{subrun}: combined hits removed while the '
                                        f'raw FDFs stay — the processor will re-decode, '
                                        f're-analyze and re-combine this subrun'})

    return {
        'items': ok_items, 'refused': refused,
        'n_ok': len(ok_items), 'n_refused': len(refused),
        'freed_bytes': freed, 'freed_h': human(freed),
        'reprocess_warnings': warnings,
        'unverifiable': rmap is None,
    }


def _delete_component(run: str, subrun: str, comp: str, remote: dict,
                      act: str, newest: str) -> dict:
    """Delete one component of one subrun after re-verifying it here.

    Guards, in order:
      1. component key known; run/subrun match their name regexes.
      2. target resolves to a real directory inside the component's own root (no
         symlinks, no traversal).
      3. run is not active and not the newest; the subrun has .subrun_complete.
      4. a fresh verification says SAFE for exactly the files about to go.
    """
    res = {'run': run, 'subrun': subrun, 'component': comp,
           'label': COMPONENTS.get(comp, {}).get('label', comp),
           'success': False, 'freed_bytes': 0, 'freed_h': '0 B', 'message': ''}

    path, root = _component_path(run, subrun, comp)
    if path is None:
        res['message'] = 'invalid run/subrun/component'
        return res
    try:
        rtarget = path.resolve()
        rroot = root.resolve()
    except OSError as e:
        res['message'] = f'cannot resolve path: {e}'
        return res
    if path.is_symlink():
        res['message'] = 'refusing to delete a symlink'
        return res
    if not rtarget.is_dir():
        res['message'] = 'not present'
        return res
    if rroot not in rtarget.parents:
        res['message'] = 'path escapes the managed root'
        return res

    guard = _run_guard(run, act, newest)
    if not guard and mid_write_subrun(run, subrun):
        guard = 'unmarked and recently written (possibly mid-write)'
    if guard:
        res['message'] = f'not safe: {guard}'
        _log_delete(f"REFUSED {run}/{subrun}/{comp}: {guard}")
        return res

    contents = _component_contents(run, subrun, comp)
    if not contents:
        res['message'] = 'nothing present'
        return res
    local = {rel: sz for rel, (sz, _) in contents.items()}
    v = _verify_component(comp, local, remote)
    if not v['safe']:
        res['message'] = f"not safe: {v['reason']}"
        _log_delete(f"REFUSED {run}/{subrun}/{comp}: {v['reason']}")
        return res

    spec = COMPONENTS[comp]
    size = sum(local.values())
    try:
        if spec['suffix']:
            # File-scoped component: remove exactly the files just verified,
            # leaving the directory and its metadata (run_time.txt, RunCtrl logs,
            # pedestal_run.txt, *.cfg, *.prg) untouched.
            for _, f in contents.values():
                f.unlink()
        else:
            shutil.rmtree(rtarget)
    except Exception as e:
        _log_delete(f"ERROR deleting {run}/{subrun}/{comp}: {e}")
        res['message'] = f'delete failed: {e}'
        return res

    res.update(success=True, freed_bytes=size, freed_h=human(size),
               message=f'freed {human(size)}')
    _log_delete(f"DELETED {run}/{subrun}/{comp}  freed={human(size)}  ({v['reason']})")
    return res


def delete_components(items, progress=None) -> dict:
    """Delete a set of (run, subrun, component) triples. Every one is
    independently re-verified here against a FRESH EOS listing — a caller verdict,
    or the cached listing a preflight used, is never trusted.

    One xrdfs call covers the whole batch; see the module docstring on why that
    matters. progress(phase, done, total, msg, item) reports the opaque 'listing'
    phase and then a real byte-weighted 'delete' phase, handing back each per-item
    result as it lands so the GUI can log it live.
    """
    progress = progress or _noop_progress
    triples = _normalize_items(items)
    if not triples:
        return {'results': [], 'n_deleted': 0, 'n_failed': 0,
                'freed_bytes': 0, 'freed_h': '0 B',
                'message': 'nothing valid selected'}

    act, newest = active_run(), newest_run()
    rmap = _remote_runs_map(force=True, progress=progress)
    if rmap is None:
        return {'results': [], 'n_deleted': 0, 'n_failed': len(triples),
                'freed_bytes': 0, 'freed_h': '0 B',
                'message': 'could not list runs on EOS (Kerberos/network?) — '
                           'refusing to delete anything'}
    by_run = _partition_by_run(rmap)

    # Weight the bar by bytes, not item count: dropping one 12 GB raw_fdf and one
    # 200 MB combined_hits are not half the job each.
    ordered = sorted(triples, key=lambda t: (t[0], t[1], COMPONENTS[t[2]]['order']))
    weights = [sum(_component_local_files(*t).values()) for t in ordered]
    total = sum(weights) or 1

    results = []
    freed = done = 0
    for (run, subrun, comp), w in zip(ordered, weights):
        progress('delete', done, total, f'{run}/{subrun} — {COMPONENTS[comp]["label"]}')
        r = _delete_component(run, subrun, comp, by_run.get(run, {}), act, newest)
        results.append(r)
        if r['success']:
            freed += r['freed_bytes']
        done += w
        progress('delete', done, total,
                 f'{run}/{subrun} — {COMPONENTS[comp]["label"]}', item=r)

    _prune_empty_dream_run_dirs({run for run, _, c in triples if c == 'dream_run'})
    progress('delete', total, total, f'freed {human(freed)}')

    return {'results': results,
            'n_deleted': sum(1 for r in results if r['success']),
            'n_failed': sum(1 for r in results if not r['success']),
            'freed_bytes': freed, 'freed_h': human(freed)}


def _prune_empty_dream_run_dirs(runs):
    """Remove dream_run/<run> once its last subrun has gone, so the SSD staging
    tree does not accumulate empty shells. Only ever removes directories that
    contain no files at all."""
    for run in runs:
        if not RUN_NAME_RE.match(run or ''):
            continue
        d = SSD_DREAM_DIR / run
        try:
            if not d.is_dir() or d.is_symlink():
                continue
            if any(f.is_file() for f in d.rglob('*')):
                continue
            shutil.rmtree(d)
            _log_delete(f"PRUNED empty dream_run/{run}")
        except OSError:
            pass


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
    try:
        r = subprocess.run(['xrdcp', '-f', '--nopbar', src, str(local_path)],
                           capture_output=True, text=True)
    except OSError as e:
        return False, f'xrdcp not available: {e}'
    return (r.returncode == 0), (r.stderr or '').strip()


def list_eos_runs():
    """Sorted run names present on EOS, or None if the listing failed."""
    url, eos_runs = _eos_config()
    try:
        r = subprocess.run(['xrdfs', url, 'ls', eos_runs], capture_output=True, text=True)
    except OSError:
        return None
    if r.returncode != 0:
        return None
    out = []
    for line in r.stdout.splitlines():
        name = line.rstrip('/').rsplit('/', 1)[-1]
        if RUN_NAME_RE.match(name):
            out.append(name)
    return sorted(out)


def scan_restore() -> dict:
    """List every run on EOS and, for each, how it compares to the local HDD:
    complete (already local), partial, or missing. 'To fetch' is the bytes that
    would be pulled (files absent or size-mismatched locally)."""
    runs = list_eos_runs()
    if runs is None:
        raise RuntimeError('could not list runs on EOS (Kerberos/network?)')
    act = active_run()
    rmap = _remote_runs_map(force=True)
    if rmap is None:
        raise RuntimeError('could not list runs on EOS (Kerberos/network?)')
    by_run = _partition_by_run(rmap)
    results = []
    fetch_total = 0
    for run in runs:
        remote = by_run.get(run, {})
        r = {'run': run, 'disk': 'hdd', 'active': run == act}
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
    ap.add_argument('disk', choices=['hdd', 'ssd', 'components'],
                    help="'components' prints the per-component breakdown across both disks")
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()

    if args.disk == 'components':
        out = component_scan(verify=True, force=True)
        if args.json:
            print(json.dumps(out, indent=2))
        else:
            print(f"{'COMPONENT':22} {'ON DISK':>10}  {'RECLAIMABLE':>12}")
            print('-' * 48)
            for c in out['component_order']:
                t = out['totals'][c]
                print(f"{COMPONENTS[c]['label']:22} {t['size_h']:>10}  {t['safe_size_h']:>12}")
            print('-' * 48)
            print(f"{out['n_runs']} runs · {out['total_h']} on disk · "
                  f"{out['safe_bytes_h']} reclaimable")
        raise SystemExit(0)

    out = scan(args.disk)
    if args.json:
        print(json.dumps(out, indent=2))
    else:
        u = out['usage']
        if u:
            print(f"{out['label']}: {human(u.get('free', 0))} free of {human(u.get('total', 0))} "
                  f"({u.get('pct', 0)}% used)")
        print(f"{'RUN':28} {'SIZE':>10}  {'SAFE':>5}  REASON")
        print('-' * 96)
        for r in out['runs']:
            print(f"{r['run']:28} {r['size_h']:>10}  {'YES' if r['safe'] else 'no':>5}  {r['reason']}")
        print('-' * 96)
        print(f"{out['n_safe']}/{out['n_runs']} runs safe to delete — "
              f"would free {out['safe_bytes_h']}")
