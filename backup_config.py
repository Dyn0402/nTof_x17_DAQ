#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Standalone backup watcher configuration for nTof.
Edit the constants below, then run this script to regenerate config/backup_config.json.
The flask UI's Start Backup button reads that JSON to launch backup_watcher.py.
"""

import json
import os

SOURCE_DIR     = '/mnt/data/x17/beam_july/'
EOS_DIR        = '/eos/experiment/ntof/data/x17/july_beam/'
XROOTD_URL     = 'root://eospublic.cern.ch'
CERN_PRINCIPAL = 'dneff@CERN.CH'
GPG_PASS_FILE  = '/home/mx17/.cern_pass.gpg'
KEYTAB_FILE    = '/home/mx17/.keytab/mx17_cern.keytab'

CONFIG = {
    # Local top-level data directory
    'source_dir': SOURCE_DIR,

    # EOS destination path (mirrored structure). Transfers use the native xrootd
    # protocol (xrdcp/xrdfs), NOT the FUSE mount: the legacy xrootdfs mount cannot
    # mkdir/rename/overwrite, so rsync-over-FUSE fails for any new directory.
    'eos_dir': EOS_DIR,

    # Native xrootd endpoint for the EOS instance holding eos_dir. Full URLs are
    # built as f"{xrootd_url}//{absolute_eos_path}" (note the double slash).
    'xrootd_url': XROOTD_URL,

    # EXTRA locations that still count as a backup, for space_manager's
    # "is this local file safe to delete?" question only. READ-ONLY: the watcher
    # never writes or deletes here, they are only searched for an existing copy.
    #
    # Why this exists: a campaign outlives any single destination — a quota fills,
    # an allocation moves — and data pushed to the OLD path is still perfectly
    # good. Verify against eos_dir alone and every historical run reads as "not
    # backed up", which permanently blocks its local disk from being reclaimed.
    # List superseded destinations here when eos_dir ever changes.
    #
    # Format: [{'xrootd_url': 'root://eosuser.cern.ch', 'eos_dir': '/eos/user/d/dneff/old/'}]
    'verify_eos_locations': [],

    # Subdir of source_dir that gets smart per-subrun sync
    'runs_subdir': 'runs',

    # Subdirs of source_dir to never sync
    'exclude_dirs': ['dream_run', 'analysis'],

    # Reboot-safe Kerberos: a keytab lets backup_watcher re-kinit with NO password,
    # gpg-agent, or tty — so it self-heals after a reboot. Regenerate it whenever the
    # CERN password changes with bash_scripts/regen_cern_keytab.sh (the AD key salt is
    # CERN.CHdylan.neff, not the ktutil default, so a naive keytab silently fails).
    'keytab_file': KEYTAB_FILE,

    # GPG-encrypted CERN password file — LAST-RESORT fallback only. Needs a gpg
    # passphrase cached in the agent, which a reboot wipes, so it does NOT work
    # unattended. Kept for manual use. (created with: gpg --encrypt -r KEY -o ~/.cern_pass.gpg)
    'gpg_pass_file': GPG_PASS_FILE,

    # Kerberos principal for kinit
    'cern_principal': CERN_PRINCIPAL,

    # Seconds between kinit renewal attempts (ticket lasts ~25h, renew well before expiry)
    'kinit_interval': 3600,

    # Run filtering for the runs_subdir (same semantics as processor_config.py)
    'include_runs': None,   # e.g. ['run_1', 'run_2'] — only sync these; None = all
    'exclude_runs': None,   # e.g. ['run_35']          — skip these

    # Watcher behavior
    'poll_interval':       30,     # seconds between runs-dir scans
    'stale_run_days':      10,     # runs with no new data for this many days are skipped
    'extra_sync_interval': 300,    # seconds between full syncs of non-runs subdirs
    'reconcile_interval':  86400,  # seconds between idle-only full-reconcile sweeps of
                                   # ALL runs (verify vs EOS, re-copy missing/changed
                                   # files incl. stale runs); once a day

    # Extra arguments passed verbatim to xrdcp (e.g. ['-S', '4'] for 4 parallel data
    # streams per file, or ['--retry', '3'] on flaky WAN links). '-f' (overwrite) and
    # '-p' (create parent dirs) are always applied by the watcher.
    'xrdcp_extra_args': [],
}

if __name__ == '__main__':
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config', 'backup_config.json')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as f:
        json.dump(CONFIG, f, indent=4)
    print(f'Written: {out_path}')
