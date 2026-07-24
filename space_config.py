#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Standalone space-watcher configuration for nTof.
Edit the constants below, then run this script to regenerate config/space_config.json.
start_servers.sh (and the Flask Start button) read that JSON to launch space_watcher.py.

Policy in one line: keep at least `low_water_gb` free on the SSD by deleting the
OLDEST runs that are provably safe (SSD -> HDD -> EOS verified), and never more
runs than needed to get back over the line.
"""

import json
import os

CONFIG = {
    # --- The space policy ---------------------------------------------------
    # Floor we promise to maintain on the SSD filesystem. Dropping below this is
    # what wakes the watcher up and makes it delete anything at all.
    'low_water_gb': 100,

    # Once freeing, keep going until this much is free. The gap above low_water
    # is pure hysteresis: without it the watcher would sit exactly on the floor
    # and delete again a few minutes later. 20 GB is about an hour of beam
    # acquisition at the measured ~22 GB/hr raw rate, so a single freeing pass
    # buys an hour of quiet.
    'target_free_gb': 120,

    # --- Guards on what may be deleted --------------------------------------
    # Never delete the N most recent runs, even when they are fully EOS-verified
    # and the disk is full. This is the "in case something is wrong" reserve: it
    # keeps the runs you are most likely to want to re-examine locally, and it
    # bounds the damage if the verification itself is ever wrong. The active
    # (currently acquiring) run is ALWAYS protected on top of this, by
    # space_manager.delete_run itself.
    'keep_recent_runs': 3,

    # Free space below which the two guards above (keep_recent_runs, min_age_hours)
    # are BOTH ignored and any EOS-verified run may be deleted, newest included.
    # They are conveniences; a disk that hits zero stops acquisition outright, and
    # by then the run you were protecting has nowhere to be written anyway. What
    # this does NOT relax is safety: the active run and the SSD -> HDD -> EOS
    # verification are enforced inside space_manager.delete_run regardless.
    # 50 GB is a bit over two hours of beam at the measured ~22 GB/hr raw rate —
    # enough warning to notice, not so much that the guards never apply.
    'emergency_gb': 50,

    # Skip any run whose newest file was written less than this many hours ago.
    # Backup and processing both work off the HDD copy, so this is not strictly
    # required — it is a belt-and-braces margin against deleting a run that some
    # tool is still touching.
    'min_age_hours': 2,

    # --- Watcher behavior ---------------------------------------------------
    # Seconds between free-space checks. The check itself is a cheap statvfs;
    # the expensive part (per-run EOS listings) only happens once free space is
    # actually below low_water_gb.
    'poll_interval': 300,

    # Report what would be deleted, delete nothing. Flip to true to arm a
    # dry-run-only observation period.
    'dry_run': False,

    # Seconds to wait before re-attempting a freeing pass that freed nothing
    # (e.g. nothing is EOS-verified yet). Stops a full disk from re-listing all
    # of EOS every poll_interval.
    'retry_interval': 1800,
}

if __name__ == '__main__':
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config', 'space_config.json')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as f:
        json.dump(CONFIG, f, indent=4)
    print(f'Written: {out_path}')
