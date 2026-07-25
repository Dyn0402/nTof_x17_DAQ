#!/bin/bash
# reprocess_all_cns.sh — bulk-reprocess completed runs with common-noise subtraction ON.
# 2026-07-24: CNS was off (commit ca7baed) until run_70; these older runs' combined_hits are
# common-mode contaminated. This deletes their derived products (hits_root + combined_hits_root),
# keeps decoded_root (the rebuild source), and re-runs reprocess_run.py (analyze+combine with
# --cns 1 via config/processor_config.json, which must have common_noise_subtraction: true).
#
# SAFETY: only deletes a run's derived files if EVERY data sub-run still has decoded_root OR
# raw_daq_data, so a group can always be rebuilt. The processor_watcher MUST have these runs in
# exclude_runs (restarted) first, so the two never touch the same files. Revert the exclusion
# and restart the watcher when this finishes.
#
# Usage: nohup bash reprocess_all_cns.sh > /path/to/log 2>&1 &   (or launched in background)
set -u
cd /home/mx17/PycharmProjects/nTof_x17_DAQ
BASE=/mnt/data/x17/beam_july/runs
RUNS="run_69 run_68 run_67_recon run_67"   # quick first, the 65-subrun run_67 last
JOBS=2                                       # gentle: leave cores for the live watcher

for run in $RUNS; do
  echo "===== $run  $(date '+%F %T') ====="
  rundir="$BASE/$run"
  if [ ! -d "$rundir" ]; then echo "  SKIP: $rundir missing"; continue; fi
  missing=0; nsub=0
  for s in "$rundir"/*/; do
    [ -d "${s}raw_daq_data" ] || continue          # data sub-runs only
    nsub=$((nsub+1))
    nd=$(ls "${s}decoded_root/"*.root 2>/dev/null | wc -l)
    nr=$(ls "${s}raw_daq_data/"*.fdf 2>/dev/null | wc -l)
    if [ "$nd" -eq 0 ] && [ "$nr" -eq 0 ]; then missing=$((missing+1)); fi
  done
  if [ "$missing" -gt 0 ]; then
    echo "  SKIP $run: $missing/$nsub sub-runs lack BOTH decoded and raw — not safe to delete"
    continue
  fi
  echo "  $nsub data sub-runs, all rebuildable — deleting hits_root + combined_hits_root"
  rm -rf "$rundir"/*/hits_root "$rundir"/*/combined_hits_root
  .venv/bin/python reprocess_run.py "$run" --jobs "$JOBS"
  echo "  --- $run done $(date '+%T') ---"
done
echo "===== ALL RUNS DONE $(date '+%F %T') ====="
