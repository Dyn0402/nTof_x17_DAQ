#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Autonomous EOS backup watcher for nTof DREAM DAQ data.

Syncs the entire source_dir to eos_dir, excluding specified subdirectories.
The runs_subdir gets smart per-subrun sync (waits for each subrun to be stable
before transferring).  All other subdirs are synced wholesale on a slower
extra_sync_interval cadence.

Every file under each run is backed up: subrun subdirectories AND loose
run-level files (dream_daq.log, run_config.json, backups, etc.).  Loose files
are refreshed whenever any subrun of that run syncs.

A slow full-reconcile sweep (reconcile_interval, default once a day) runs while
the watcher is otherwise idle: it re-lists EVERY run on EOS and re-copies any
file that is missing or size-mismatched, INCLUDING runs long marked stale.  This
is what propagates after-the-fact edits (e.g. a run_config.json rewrite) to old
runs, which the fast per-subrun path alone would never revisit.

Transfers use the native xrootd protocol (xrdcp/xrdfs), NOT the FUSE mount:
the legacy xrootdfs mount cannot mkdir/rename/overwrite, so rsync-over-FUSE
fails for any new directory.  Files already on EOS at the same size are skipped
(data is write-once); size-mismatched files are re-copied (xrdcp -f overwrites).

Handles Kerberos via kinit -R (renewal) and falls back to a GPG-encrypted
password for a full re-kinit when renewal fails.

Usage:
    python backup_watcher.py <backup_config_json_path>

Config keys (see backup_config.py to generate the JSON):
  source_dir          : local top-level data directory (e.g. /mnt/data/x17/beam_may/)
  eos_dir             : EOS destination (locally FUSE-mounted, same structure)
  xrootd_url          : native xrootd endpoint (e.g. root://eospublic.cern.ch)
  runs_subdir         : name of the runs subdir that gets smart per-subrun sync
  exclude_dirs        : list of subdir names to never sync (e.g. ['dream_run'])
  gpg_pass_file       : path to GPG-encrypted CERN password (~/.cern_pass.gpg)
  cern_principal      : Kerberos principal (e.g. dneff@CERN.CH)
  kinit_interval      : seconds between kinit renewal attempts   (default: 3600)
  include_runs        : list of run dir names to sync exclusively (null = all)
  exclude_runs        : list of run dir names to skip             (null = none)
  poll_interval       : seconds between runs-dir scans           (default: 30)
  stale_run_days      : runs with no new data for N days skipped (default: 10)
  extra_sync_interval : seconds between full syncs of non-runs dirs (default: 300)
  reconcile_interval  : seconds between full-reconcile sweeps of all runs,
                        run only while idle (default: 86400 = once a day)
  rsync_extra_args    : extra arguments passed verbatim to rsync  (default: [])
"""

import re
import sys
import json
import time
import datetime
import subprocess
from pathlib import Path


def main():
    if len(sys.argv) != 2:
        print("Usage: python backup_watcher.py <backup_config_json_path>")
        sys.exit(1)
    with open(sys.argv[1]) as f:
        config = json.load(f)
    run_watcher(config, Path(sys.argv[1]))


# ---------------------------------------------------------------------------
# Main watcher loop
# ---------------------------------------------------------------------------

def run_watcher(config: dict, config_path: Path):
    global _XROOTD_URL, _XRDCP_EXTRA

    source_dir    = Path(config['source_dir'])
    eos_dir       = Path(config['eos_dir'])
    runs_subdir   = config.get('runs_subdir', 'runs')
    exclude_dirs  = set(config.get('exclude_dirs', []))
    gpg_pass_file = Path(config['gpg_pass_file'])
    keytab_file   = Path(config['keytab_file']) if config.get('keytab_file') else None
    cern_principal = config['cern_principal']

    _XROOTD_URL   = config.get('xrootd_url', 'root://eospublic.cern.ch').rstrip('/')
    _XRDCP_EXTRA  = config.get('xrdcp_extra_args', [])

    kinit_interval      = config.get('kinit_interval',      3600)
    extra_sync_interval = config.get('extra_sync_interval',  300)
    poll_interval       = config.get('poll_interval',         30)
    stale_run_days      = config.get('stale_run_days',        10)
    reconcile_interval  = config.get('reconcile_interval',  86400)

    include_runs = set(config['include_runs']) if config.get('include_runs') else None
    exclude_runs = set(config['exclude_runs']) if config.get('exclude_runs') else set()

    runs_dir     = source_dir / runs_subdir
    eos_runs_dir = eos_dir    / runs_subdir

    print(f"[backup] source_dir         : {source_dir}")
    print(f"[backup] eos_dir            : {eos_dir}")
    print(f"[backup] xrootd_url         : {_XROOTD_URL}")
    print(f"[backup] runs_subdir        : {runs_subdir}")
    print(f"[backup] exclude_dirs       : {sorted(exclude_dirs)}")
    print(f"[backup] principal          : {cern_principal}")
    print(f"[backup] kinit_interval     : {kinit_interval}s")
    print(f"[backup] extra_sync_interval: {extra_sync_interval}s")
    if include_runs:
        print(f"[backup] include_runs       : {sorted(include_runs)}")
    if exclude_runs:
        print(f"[backup] exclude_runs       : {sorted(exclude_runs)}")
    print(f"[backup] poll               : {poll_interval}s  stale_after={stale_run_days}d")
    print(f"[backup] xrdcp timeout      : {_XRDCP_TIMEOUT_BASE:.0f}s + 1s per "
          f"{_XRDCP_MIN_RATE:.0f} MB  (xrdfs ls {_XRDFS_TIMEOUT:.0f}s)")

    state_path = config_path.parent / 'backup_state.json'
    loose_state_path = config_path.parent / 'backup_loose_state.json'
    # (run_name, subrun_name) -> total dir size at last successful rsync
    synced_sizes: dict = _load_state(state_path)
    # run_name -> loose-file signature at last successful loose sync
    loose_sigs:   dict = _load_loose_state(loose_state_path)
    # (run_name, subrun_name) -> total dir size from previous poll (stable check)
    prev_sizes: dict = {}

    checked_stale_runs: set = set()

    last_kinit_check  = -kinit_interval   # trigger immediately on first iteration
    last_extra_sync   = -extra_sync_interval
    last_reconcile    = -reconcile_interval  # reconcile on first idle after startup
    kerberos_ok       = False

    idle_ticks = 0
    idle_line  = False
    _SPINNER   = ['-', '\\', '|', '/']

    def _end_idle():
        nonlocal idle_line
        if idle_line:
            sys.stdout.write('\n')
            sys.stdout.flush()
            idle_line = False

    while True:
        now = time.time()

        # --- Kerberos refresh ---
        if now - last_kinit_check >= kinit_interval:
            ok, method = _refresh_kerberos(cern_principal, keytab_file, gpg_pass_file)
            last_kinit_check = now
            kerberos_ok = ok
            _end_idle()
            if ok:
                print(f"[backup] Kerberos OK ({method})")
            else:
                print(f"[backup] Kerberos FAILED: {method}")

        found_new = False

        if not source_dir.exists():
            pass
        elif not kerberos_ok:
            _end_idle()
            print("[backup] Skipping scan — Kerberos not authenticated")
        else:
            # --- Smart per-subrun sync for runs_subdir ---
            if runs_dir.exists():
                for run_dir in _runs_newest_first(runs_dir):
                    if include_runs is not None and run_dir.name not in include_runs:
                        continue
                    if run_dir.name in exclude_runs:
                        continue
                    # --- Run-level loose files (before the stale skip) ---
                    # run_config.json & co. live directly in the run dir, so the
                    # per-subrun path below never looks at them: _xrd_loose_files
                    # only runs after a SUBRUN syncs, and a subrun only syncs when
                    # its size changes. An edit made after a run ends therefore
                    # stayed invisible until the once-a-day reconcile, and the
                    # missing/changed file blocked whole-run deletion in the Disk
                    # Space tab. Comparing a cheap local signature closes that 24 h
                    # window to one poll. Deliberately ahead of the
                    # stale/checked_stale_runs skips — a bulk config rewrite
                    # touches old runs too — and free when nothing has changed
                    # (local stat of a handful of files).
                    sig = _loose_signature(run_dir)
                    if sig and loose_sigs.get(run_dir.name) != sig:
                        _end_idle()
                        print(f"[backup] loose files changed in {run_dir.name} — syncing")
                        if _xrd_loose_files(run_dir, eos_runs_dir / run_dir.name):
                            loose_sigs[run_dir.name] = sig
                            _save_loose_state(loose_state_path, loose_sigs)
                        else:
                            # Leave the signature stale so the next poll retries.
                            print(f"[backup] loose-file sync FAILED for {run_dir.name}")

                    if run_dir.name in checked_stale_runs:
                        continue

                    is_stale = _run_is_stale(run_dir, stale_run_days)

                    for subrun_dir in sorted(run_dir.iterdir()):
                        if not subrun_dir.is_dir():
                            continue

                        key = (run_dir.name, subrun_dir.name)
                        current_size = _dir_size(subrun_dir)

                        # Stable check: size must match the previous poll
                        if prev_sizes.get(key) != current_size:
                            prev_sizes[key] = current_size
                            continue

                        # Skip if already rsynced at this exact size
                        if synced_sizes.get(key) == current_size:
                            continue

                        _end_idle()
                        mb = current_size // (1024 * 1024)
                        print(f"[backup] {run_dir.name}/{subrun_dir.name}  size={mb}MB")

                        ok = _xrd_sync_tree(subrun_dir, eos_runs_dir / run_dir.name / subrun_dir.name)
                        if ok:
                            _xrd_loose_files(run_dir, eos_runs_dir / run_dir.name)
                            synced_sizes[key] = current_size
                            _save_state(state_path, synced_sizes)
                            found_new = True
                        else:
                            print(f"[backup] sync FAILED for {run_dir.name}/{subrun_dir.name}")

                    if is_stale:
                        checked_stale_runs.add(run_dir.name)
                        _end_idle()
                        print(f"[backup] Marked stale (will skip): {run_dir.name}")

            # --- Periodic full sync for all other subdirs ---
            if now - last_extra_sync >= extra_sync_interval:
                last_extra_sync = now
                for subdir in sorted(source_dir.iterdir()):
                    if not subdir.is_dir():
                        continue
                    if subdir.name == runs_subdir:
                        continue
                    if subdir.name in exclude_dirs:
                        continue
                    _end_idle()
                    print(f"[backup] extra sync: {subdir.name}/")
                    if _xrd_sync_tree(subdir, eos_dir / subdir.name):
                        print(f"[backup] extra sync done: {subdir.name}/")
                    else:
                        print(f"[backup] extra sync FAILED: {subdir.name}/")

            # --- Full-reconcile sweep (idle-only backstop) ---
            # Re-verifies EVERY run against EOS and re-copies any missing or
            # size-mismatched file, ignoring the stale-skip and per-subrun size
            # caches. This is what propagates after-the-fact edits (e.g. a bulk
            # run_config.json rewrite) and loose files to old/stale runs, which
            # the fast per-subrun path never revisits. Only runs while idle so it
            # never competes with live data transfer.
            if not found_new and runs_dir.exists() and now - last_reconcile >= reconcile_interval:
                last_reconcile = now
                _end_idle()
                print(f"[backup] full reconcile: verifying all runs against EOS")
                n_runs = n_gap_runs = 0
                for run_dir in _runs_newest_first(runs_dir):
                    if include_runs is not None and run_dir.name not in include_runs:
                        continue
                    if run_dir.name in exclude_runs:
                        continue
                    n_runs += 1
                    # Recursive sync of the whole run: subruns + loose files.
                    if not _xrd_sync_tree(run_dir, eos_runs_dir / run_dir.name):
                        n_gap_runs += 1
                        print(f"[backup] reconcile: sync gaps remain in {run_dir.name}")
                print(f"[backup] full reconcile done: {n_runs} runs checked, "
                      f"{n_gap_runs} with unresolved gaps")

        if found_new:
            idle_ticks = 0
        else:
            idle_ticks += 1
            elapsed = idle_ticks * poll_interval
            ts = datetime.datetime.now().strftime('%H:%M:%S')
            sp = _SPINNER[idle_ticks % 4]
            if not source_dir.exists():
                msg = f'[backup] {sp} waiting for source_dir  #{idle_ticks}  {ts}'
            elif not kerberos_ok:
                msg = f'[backup] {sp} AUTH ERROR — Kerberos not valid  {ts}'
            else:
                msg = f'[backup] {sp} idle  #{idle_ticks}  {elapsed}s  {ts}'
            sys.stdout.write(f'\r{msg}          ')
            sys.stdout.flush()
            idle_line = True

        time.sleep(poll_interval)


# ---------------------------------------------------------------------------
# Kerberos
# ---------------------------------------------------------------------------

def _refresh_kerberos(principal: str, keytab_file: Path, gpg_pass_file: Path) -> tuple:
    """Keep a valid ticket. Order: renew existing -> keytab (non-interactive,
    survives reboot) -> GPG-decrypted password (last resort; needs a cached gpg
    passphrase, so it does NOT work unattended after a reboot)."""
    result = subprocess.run(['kinit', '-R'], capture_output=True)
    if result.returncode == 0:
        return True, 'renewed'

    # Keytab: the reboot-safe path. No password, no gpg-agent, no tty. The
    # keytab is regenerated with bash_scripts/regen_cern_keytab.sh whenever the
    # CERN password changes (AD salt is CERN.CHdylan.neff, not the default).
    if keytab_file and keytab_file.exists():
        kt = subprocess.run(
            ['kinit', '-kt', str(keytab_file), principal],
            capture_output=True,
        )
        if kt.returncode == 0:
            return True, 'keytab kinit'
        stderr = kt.stderr.decode(errors='replace').strip()
        # fall through to gpg only if the keytab is stale/wrong
        keytab_err = f'keytab kinit failed: {stderr}'
    else:
        keytab_err = f'keytab not found: {keytab_file}'

    if not gpg_pass_file.exists():
        return False, f'{keytab_err}; GPG password file not found: {gpg_pass_file}'

    # Decrypt via gpg-agent — prompts for GPG passphrase via pinentry once per
    # boot; subsequent calls use the cached passphrase from the agent.
    gpg = subprocess.run(
        ['gpg', '--batch', '--yes', '--decrypt', str(gpg_pass_file)],
        capture_output=True,
    )
    if gpg.returncode != 0:
        stderr = gpg.stderr.decode(errors='replace').strip()
        return False, f'gpg decrypt failed (passphrase not cached?): {stderr}'

    kinit = subprocess.run(
        ['kinit', principal],
        input=gpg.stdout,
        capture_output=True,
    )
    if kinit.returncode == 0:
        return True, 'full kinit'
    stderr = kinit.stderr.decode(errors='replace').strip()
    return False, f'kinit failed: {stderr}'


# ---------------------------------------------------------------------------
# XRootD transfer helpers
#
# The EOS FUSE mount (legacy xrootdfs) cannot mkdir/rename/overwrite, so
# rsync-over-FUSE fails for every new directory.  The native xrootd protocol
# has no such limitation, so all transfers go through xrdcp/xrdfs instead.
# ---------------------------------------------------------------------------

_XROOTD_URL  = None   # e.g. 'root://eospublic.cern.ch' — set by run_watcher()
_XRDCP_EXTRA = []     # extra xrdcp args from config — set by run_watcher()

# Wall-clock budget for one transfer.  Deliberately NOT config keys: the JSON is
# regenerated from backup_config.py at every boot, so a tunable here would be a
# tunable that silently reverts.  An xrdcp to EOS can wedge with the connection
# open and no bytes moving, and its own
# internal expiry is far longer than anything useful here: on 2026-07-28 a single
# 202 MB .fdf sat for 22+ minutes while a fresh xrdcp of the same size ran in
# under a second, and the watcher — single-threaded, sequential over sorted runs
# — was blocked behind it for six hours.  That is how run_85..run_91 never
# reached EOS at all, which in turn kept the SSD staging unprunable (nothing is
# deletable until every staged .fdf is verified on EOS).  A killed transfer costs
# nothing: the file is simply retried on the next poll.
#
# 2026-07-29 RETUNE.  The timeout above stopped the six-hour blocks but was still
# ~7x too generous, and that alone refilled the disk to 0 B: the watcher had not
# completed a single pass in 34 h and run_95..run_100 had ZERO files on EOS.
# Re-measured that night: the wedge rate is unchanged at 1/20 = 5%, and a wedge is
# SIZE-INDEPENDENT — a 14 kB .prg hung the full 70 s while the other 19 averaged
# 0.08 s.  A wedged transfer reads its source fully, sends ~2.9 kB, gets acked and
# then freezes, i.e. it is blocked on the OPEN, not on data.  So a big budget buys
# nothing a small one does not: healthy transfers run at ~107 MB/s (200 MB in
# 1.9 s), so 10 MB/s of allowance is already a 10x margin, and the base only has
# to cover the handshake.  200 MB now gets 35 s (the measured optimum in the
# benchmark table) and a 14 kB .prg gets 15 s instead of 60 s.
_XRDCP_TIMEOUT_BASE = 15.0    # seconds of handshake/allocation allowance
_XRDCP_MIN_RATE     = 10.0    # MB/s a transfer must beat to be considered alive
_XRDCP_TIMEOUT_MAX  = 120.0   # ceiling, so no single file can re-create the stall
_XRDFS_TIMEOUT      = 120.0   # a recursive `xrdfs ls -R` can hang the same way

# Immediate in-pass retries after a wedge.  Measured: retrying a wedged transfer
# straight away cleared 8/8 (6 on attempt 2, 1 on attempt 3) in 0.4-6.9 s.  The
# old code deferred the retry to the next poll, so every wedged file also cost a
# full re-walk of the tree before it got another chance -- which is what turned a
# 5% wedge rate into a pass that never finished.  3 attempts covers the measured
# distribution; anything still wedged is left for the next poll as before.
_XRDCP_ATTEMPTS = 3


def _runs_newest_first(runs_dir: Path) -> list:
    """Run dirs ordered newest run number first, then unnumbered dirs by name.

    Two reasons this is not plain sorted().  First, plain sorted() is LEXICAL, so
    'run_100' sorts before 'run_87' and the run numbering silently stops meaning
    anything past 99.  Second and more important: the pass is sequential and can
    take hours, so whatever it walks last may not be reached at all.  The newest
    runs are exactly the ones still occupying the SSD staging that space_manager
    wants to prune, so they are the ones that must go first -- on 2026-07-29 the
    ascending walk was still grinding run_89 while run_95..run_100 had nothing on
    EOS at all and the disk filled to 0 B behind them.
    """
    def key(p: Path):
        m = re.fullmatch(r'run_(\d+)', p.name)
        return (0, -int(m.group(1))) if m else (1, p.name)
    return sorted((p for p in runs_dir.iterdir() if p.is_dir()), key=key)


def _xrd_url(eos_path: Path) -> str:
    """Native xrootd URL for an absolute EOS path: root://host//eos/..."""
    return f"{_XROOTD_URL}//{str(eos_path).lstrip('/')}"


def _xrdcp_timeout(size_bytes: int) -> float:
    """Seconds to allow one transfer of size_bytes before killing it.

    Scaled by size so a big file is not cut off mid-flight, with the floor rate
    set 10x below what this link actually does (measured ~107 MB/s to eospublic,
    ~190 MB/s off the HDD).  The largest file in the tree is a 202 MB .fdf, which
    gets 35 s against a healthy time of 1.9 s; the ceiling keeps an outlier from
    re-creating the stall.  Do not widen this to "be safe": a wedge does not
    resolve with more time (measured: still dead at 70 s on a 14 kB file), so
    extra budget is pure dead time and the retry is what actually recovers it.
    """
    return min(_XRDCP_TIMEOUT_MAX,
               _XRDCP_TIMEOUT_BASE + size_bytes / (_XRDCP_MIN_RATE * 1024 * 1024))


def _remote_size_map(eos_dir: Path, recursive: bool = True) -> dict:
    """{relative_path: size} for files under eos_dir on EOS.

    recursive=True walks the whole tree (relpath keys); recursive=False lists only
    the immediate directory (bare-filename keys) — used for the cheap loose-file check.
    Empty dict if the directory does not exist yet (so all files get copied).
    Parses `xrdfs <url> ls -l [-R]` lines: '<flags> <owner> <group> <size> <date> <time> <path>'.
    """
    ls_args = ['ls', '-l', '-R', str(eos_dir)] if recursive else ['ls', '-l', str(eos_dir)]
    try:
        result = subprocess.run(
            ['xrdfs', _XROOTD_URL, *ls_args],
            capture_output=True, text=True, timeout=_XRDFS_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        # Same failure mode as a wedged xrdcp. An empty map is already the
        # "directory not there yet" answer, so the caller just re-copies.
        print(f"[backup] xrdfs ls TIMED OUT after {_XRDFS_TIMEOUT:.0f}s: {eos_dir}")
        return {}
    if result.returncode != 0:
        return {}
    base = str(eos_dir).rstrip('/') + '/'
    sizes: dict = {}
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


def _xrdcp_file(local: Path, eos_path: Path) -> bool:
    """Copy one local file to EOS via native xrdcp (-f overwrite, -p make dirs).

    Killed if it stalls (see _xrdcp_timeout) rather than left to xrdcp's own
    expiry, which blocks this single-threaded loop for tens of minutes per file.
    """
    try:
        size = local.stat().st_size
    except OSError:
        size = 0
    budget = _xrdcp_timeout(size)
    mb = size / (1024 * 1024)

    for attempt in range(1, _XRDCP_ATTEMPTS + 1):
        try:
            result = subprocess.run(
                ['xrdcp', '-f', '-p', '--nopbar', *_XRDCP_EXTRA, str(local), _xrd_url(eos_path)],
                capture_output=True, text=True, timeout=budget,
            )
        except subprocess.TimeoutExpired:
            # subprocess.run has already killed the child.  Retry straight away:
            # a wedge is a property of that one connection, not of the file or
            # the link, so attempt 2 normally flies (measured 0.4-6.9 s).
            print(f"[backup] xrdcp WEDGED after {budget:.0f}s ({mb:.1f} MB) "
                  f"{local.name} — killed, attempt {attempt}/{_XRDCP_ATTEMPTS}")
            continue
        if result.returncode == 0:
            if attempt > 1:
                print(f"[backup] xrdcp recovered on attempt {attempt}: {local.name}")
            return True
        # A real error (permissions, quota, bad path) will not fix itself by
        # retrying, so report and give up on this file for this pass.
        print(f"[backup] xrdcp FAILED (exit {result.returncode}) {local.name}: "
              f"{result.stderr.strip()[:200]}")
        return False

    # Still wedged after every attempt. Not recorded as synced, so the next poll
    # retries this file from scratch.
    print(f"[backup] xrdcp gave up after {_XRDCP_ATTEMPTS} attempts: {local.name}")
    return False


def _xrd_sync_tree(local_dir: Path, eos_dir: Path) -> bool:
    """Copy every file under local_dir into eos_dir on EOS, skipping files already
    there at the same size (data is write-once). Returns True if nothing failed.

    Incomplete trees self-heal: absent files copy, size-matched files skip, and a
    partial file (size mismatch) is re-copied — native xrdcp -f can overwrite it.
    """
    remote_sizes = _remote_size_map(eos_dir)
    all_ok, copied, skipped = True, 0, 0
    for f in sorted(local_dir.rglob('*')):
        if not f.is_file():
            continue
        rel = f.relative_to(local_dir).as_posix()
        try:
            local_size = f.stat().st_size
        except OSError:
            continue
        if remote_sizes.get(rel) == local_size:
            skipped += 1
            continue
        if _xrdcp_file(f, eos_dir / rel):
            copied += 1
        else:
            all_ok = False
    if copied:
        print(f"[backup] xrdcp -> {eos_dir}: {copied} new, {skipped} already there")
    return all_ok


def _xrd_loose_files(run_dir: Path, eos_run_dir: Path) -> bool:
    """Copy every loose file sitting directly in run_dir (not in a subrun subdir):
    dream_daq.log, run_config.json and its backups, notes, etc.

    Size-checked against EOS so unchanged files are skipped; changed files (e.g. an
    edited run_config.json) are re-copied since xrdcp -f overwrites. The per-subrun
    _xrd_sync_tree only walks subrun subdirectories, so these top-level files would
    otherwise never be backed up.

    Returns True if nothing failed, so the caller can avoid caching a signature
    for a sync that did not actually land.
    """
    remote_sizes = _remote_size_map(eos_run_dir, recursive=False)
    all_ok = True
    for f in sorted(run_dir.iterdir()):
        if not f.is_file():
            continue
        try:
            local_size = f.stat().st_size
        except OSError:
            continue
        if remote_sizes.get(f.name) == local_size:
            continue
        if not _xrdcp_file(f, eos_run_dir / f.name):
            all_ok = False
    return all_ok


def _loose_signature(run_dir: Path) -> str:
    """name:size for every loose file directly in run_dir, as one string.

    Pure local stat of a handful of files — cheap enough to evaluate for every run
    on every poll. Comparing it against the last synced signature is what lets the
    watcher notice run-level edits (a run_config.json rewrite, added notes) without
    waiting for a subrun to change size or for the daily reconcile.
    """
    parts = []
    try:
        for f in sorted(run_dir.iterdir()):
            if not f.is_file():
                continue
            try:
                parts.append(f'{f.name}:{f.stat().st_size}')
            except OSError:
                pass
    except OSError:
        return ''
    return '|'.join(parts)


# ---------------------------------------------------------------------------
# Directory helpers
# ---------------------------------------------------------------------------

def _dir_size(path: Path) -> int:
    total = 0
    try:
        for f in path.rglob('*'):
            if f.is_file():
                try:
                    total += f.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _run_is_stale(run_dir: Path, stale_days: float) -> bool:
    cutoff = time.time() - stale_days * 86400
    newest = 0.0
    found_any = False
    for subrun in run_dir.iterdir():
        if not subrun.is_dir():
            continue
        found_any = True
        mtime = subrun.stat().st_mtime
        if mtime > newest:
            newest = mtime
    if not found_any:
        return False
    return newest < cutoff


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def _load_state(state_path: Path) -> dict:
    if not state_path.exists():
        return {}
    try:
        with open(state_path) as f:
            raw = json.load(f)
        return {tuple(k.split('/', 1)): v for k, v in raw.items()}
    except Exception as e:
        print(f"[backup] Could not load state from {state_path}: {e}")
        return {}


def _load_loose_state(path: Path) -> dict:
    """{run_name: loose-file signature} from the last successful loose sync.

    Kept in its own file rather than backup_state.json because that one is keyed
    'run/subrun' and _load_state() splits every key on '/'.
    """
    try:
        with open(path) as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save_loose_state(path: Path, sigs: dict):
    try:
        with open(path, 'w') as f:
            json.dump(sigs, f, indent=2)
    except Exception as e:
        print(f"[backup] Could not save loose state to {path}: {e}")


def _save_state(state_path: Path, synced_sizes: dict):
    try:
        raw = {f"{k[0]}/{k[1]}": v for k, v in synced_sizes.items()}
        with open(state_path, 'w') as f:
            json.dump(raw, f, indent=2)
    except Exception as e:
        print(f"[backup] Could not save state to {state_path}: {e}")


if __name__ == '__main__':
    main()
