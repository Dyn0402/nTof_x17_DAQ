#!/usr/bin/env bash
#
# pedestal_qa.sh — regular pedestal QA driver.
#
# 1. Sync new pedestal runs' _pedthr_ FDFs from the DAQ machine.
# 2. Run pedestal_strip_check.py on any run that doesn't yet have results
#    (produces <run>/pedestal_strip_check.pdf + pedestal_channels.csv + pedestal_summary.csv).
# 3. Compare the two most recent runs (compare_runs.png + compare_summary.csv + compare_changes.csv).
#
# Config via env vars (defaults shown):
#   PED_REMOTE_HOST=daq
#   PED_REMOTE_DIR=/home/mx17/beam_july/pedestals
#   PED_LOCAL=/media/dylan/data/x17/beam_july/pedestals
#   DECODE_EXE=/home/dylan/CLionProjects/mm_strip_reconstruction/cmake-build-debug/decoder/decode
# Flags:  --force  re-run QA even if a run already has pedestal_channels.csv
#         --no-sync  skip the rsync step (analyze/compare what is already local)
set -euo pipefail

REMOTE_HOST=${PED_REMOTE_HOST:-daq}
REMOTE_DIR=${PED_REMOTE_DIR:-/home/mx17/beam_july/pedestals}
LOCAL=${PED_LOCAL:-/media/dylan/data/x17/beam_july/pedestals}
REPO=$(cd "$(dirname "$0")/.." && pwd)
PY=${PED_PYTHON:-$REPO/.venv/bin/python3}
export DECODE_EXE=${DECODE_EXE:-/home/dylan/CLionProjects/mm_strip_reconstruction/cmake-build-debug/decoder/decode}

FORCE=0; SYNC=1
for a in "$@"; do
    case "$a" in
        --force) FORCE=1 ;;
        --no-sync) SYNC=0 ;;
        *) echo "unknown arg: $a" >&2; exit 2 ;;
    esac
done

mkdir -p "$LOCAL"

if [ "$SYNC" -eq 1 ]; then
    echo "== sync pedthr FDFs from $REMOTE_HOST:$REMOTE_DIR =="
    for run in $(ssh "$REMOTE_HOST" "cd '$REMOTE_DIR' && ls -d */ 2>/dev/null"); do
        run=${run%/}
        mkdir -p "$LOCAL/$run"
        # Remote layout is usually <run>/pedestals/*.fdf; fall back to <run>/*.fdf.
        rsync -ah "$REMOTE_HOST:$REMOTE_DIR/$run/pedestals/"*_pedthr_*.fdf "$LOCAL/$run/" 2>/dev/null \
          || rsync -ah "$REMOTE_HOST:$REMOTE_DIR/$run/"*_pedthr_*.fdf "$LOCAL/$run/" 2>/dev/null \
          || echo "  (no pedthr FDFs for $run)"
    done
fi

echo "== per-run QA =="
for d in "$LOCAL"/*/; do
    ls "$d"*_pedthr_*.fdf >/dev/null 2>&1 || continue
    if [ "$FORCE" -eq 1 ] || [ ! -f "$d/pedestal_channels.csv" ]; then
        echo "-- QA $d"
        "$PY" "$REPO/scripts/pedestal_strip_check.py" "$d"
    else
        echo "-- skip (already done) $d"
    fi
done

echo "== compare two most recent runs =="
"$PY" "$REPO/scripts/compare_pedestals.py" "$LOCAL"
echo "== done. Outputs under $LOCAL =="
