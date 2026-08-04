#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
One-shot NXCALS backfill for the n_TOF beam-intensity and SPS spill logs.

Both watchers only look a few minutes into the past (beam: LOOKBACK_S = 600 s,
SPS: SCALAR_LOOKBACK_S = 900 s), so any stretch when they were down is simply
absent from the per-day CSVs. Every host reboot longer than that window leaves a
hole. NXCALS keeps the history regardless, so it can be recovered after the fact.

Sibling of `backfill_tax_nxcals.py` (same structure, same guarantees) — that one
covers the H4 barrier, this one covers the two beam datasets:

    beam    beam_intensity_YYYY-MM-DD.csv   FTN.BCT477 -> ~/beam_july/slow_control/beam_intensity
    spill   sps_spill_YYYY-MM-DD.csv        SPSQC:*    -> ~/beam_july/slow_control/sps_spill

Deliberately NOT named `backfill_nxcals.py`: the banco fork already has a file by
that name whose `sps_spill` schema is 17 columns (9 base + h4_bend/h4_gif/
h4_hna162 extras) against mx17's 9. Keeping the names distinct stops a two-way
merge from silently swapping one for the other. See
[[nxcals-backfill-recipe-and-banco-history-cap]].

MUST run on a host that can reach NXCALS (Technical Network): ntof-x17-daq is
TN-trusted, lxplus can reach it, banco cannot.

    /home/mx17/venvs/nxcals/bin/python sps_monitor/backfill_beam_nxcals.py \
        --start 2026-07-18 --end 2026-07-22

Safe to re-run: each day is rewritten from the union of what was on disk and what
NXCALS returned, deduplicated on unix_ts. It refuses to touch the CURRENT day by
default, because the live watchers are appending to those files and a
read-modify-write would race them (--include-today overrides, for use with the
watchers stopped).

It also REPAIRS partially-empty spill rows, which is a second kind of hole worth
knowing about (measured 2026-08-02: ~7 % of all FTARGET cycles). The live SPS
watcher builds each row by taking SPSQC:DESTINATION as the spine and matching the
other six scalars onto it with `_nearest(..., tol=1.0)` inside a 900 s lookback.
Each SPSQC variable is ingested with its own slightly different timestamp, so for
cycles near the trailing edge of that window the companions have not landed yet
and the row is written with those columns EMPTY — permanently, since the watcher
never revisits a row. A whole-day query has every point, so the match succeeds.
Blank-filling only ever writes into an empty cell; a value already on disk is
never overwritten (--no-fill-blanks disables it entirely).

Backfilling PAST days is safe with the watchers running: each watcher seeds its
`_last_logged_ts` from the NEWEST per-day file only, and older days cannot move
that maximum.
"""

import os
import sys
import csv
import argparse
from datetime import datetime, timedelta

_REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_DIR)

from beam_monitor.beam_intensity_controller import BEAM_VARIABLE, BEAM_LOG_DIR
from sps_monitor.sps_spill_controller import (SPS_LOG_DIR, SPSQC_VARS,
                                              EXTRACTED_SCALE_E10, _nearest)

BEAM_CSV_FIELDS = ["timestamp", "unix_ts", "intensity_e10"]
SPILL_CSV_FIELDS = ["timestamp", "unix_ts", "destination", "extracted_e10",
                    "spill_len_ms", "duty_factor", "extraction_time_ms",
                    "beam_out_time_ms", "cycle_len_ms"]


def log(msg):
    print(f"[beam-backfill {datetime.now():%H:%M:%S}] {msg}", flush=True)


def _read_existing(path):
    """{unix_ts: row} for a day already on disk, so a re-run adds to the file
    rather than replacing it."""
    if not os.path.exists(path):
        return {}
    out = {}
    try:
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                try:
                    out[round(float(r["unix_ts"]), 3)] = r
                except (TypeError, ValueError, KeyError):
                    continue
    except Exception as e:
        log(f"  WARN could not read {os.path.basename(path)}: {e}")
    return out


def _write_day(path, rows_by_ts, fields):
    """Atomic-ish rewrite: temp file then replace, so an interrupted run cannot
    leave a half-written day behind."""
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for ts in sorted(rows_by_ts):
            w.writerow(rows_by_ts[ts])
    os.replace(tmp, path)


# --- per-dataset day builders ----------------------------------------------
# Each returns {unix_ts: row-dict} for one day, in the SAME shape and rounding
# the live watcher writes, so backfilled and live rows are indistinguishable.

def fetch_beam_day(db, t0, t1):
    res = db.get(BEAM_VARIABLE, t0, t1)
    ts, vals = res.get(BEAM_VARIABLE, ([], []))
    rows = {}
    for t, v in zip(ts, vals):
        # NXCALS hands these back as numpy scalars, which are not python
        # int/float — a bare float() in a try is the only reliable coercion.
        try:
            t = round(float(t), 3)
            v = float(v)
        except (TypeError, ValueError):
            continue
        rows[t] = {
            "timestamp": datetime.fromtimestamp(t).isoformat(timespec="milliseconds"),
            "unix_ts": t,
            "intensity_e10": round(v, 3),
        }
    return rows


def fetch_spill_day(db, t0, t1):
    """One row per SPS cycle. DESTINATION is the spine (one point per cycle);
    the other scalars are matched onto it with the controller's own _nearest,
    because each SPSQC variable is logged with its own slightly different
    timestamp."""
    res = db.get(list(SPSQC_VARS.values()), t0, t1)
    scal = {}
    for key, var in SPSQC_VARS.items():
        ts, vals = res.get(var, ([], []))
        scal[key] = ([float(t) for t in ts], list(vals))

    dest_t, dest_v = scal["destination"]
    rows = {}
    for t, dest in zip(dest_t, dest_v):
        t = round(float(t), 3)
        row = {"timestamp": datetime.fromtimestamp(t).isoformat(timespec="milliseconds"),
               "unix_ts": t,
               "destination": str(dest)}
        for key in SPSQC_VARS:
            if key == "destination":
                continue
            v = _nearest(*scal[key], t)
            v = float(v) if v is not None else None
            if v is not None and key == "extracted_e10":
                # SPSQC:EXTRACTED_INTENSITY is raw protons, not 1e10 units.
                v *= EXTRACTED_SCALE_E10
            row[key] = round(v, 4) if isinstance(v, float) else v
        rows[t] = row
    return rows


DATASETS = {
    "beam": {"prefix": "beam_intensity_", "dir": BEAM_LOG_DIR,
             "fields": BEAM_CSV_FIELDS, "fetch": fetch_beam_day},
    "spill": {"prefix": "sps_spill_", "dir": SPS_LOG_DIR,
              "fields": SPILL_CSV_FIELDS, "fetch": fetch_spill_day},
}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", required=True, help="first day, YYYY-MM-DD")
    ap.add_argument("--end", default=None, help="last day, YYYY-MM-DD (default: yesterday)")
    ap.add_argument("--datasets", default="beam,spill",
                    help="comma-separated subset of: beam, spill")
    ap.add_argument("--beam-out", default=None, help=f"override beam dir (default {BEAM_LOG_DIR})")
    ap.add_argument("--spill-out", default=None, help=f"override spill dir (default {SPS_LOG_DIR})")
    ap.add_argument("--include-today", action="store_true",
                    help="also rewrite today's file — ONLY with the watchers stopped")
    ap.add_argument("--no-fill-blanks", action="store_true",
                    help="only add missing rows; leave empty cells in existing rows alone")
    ap.add_argument("--driver-port", default="5051",
                    help="Spark driver port; keep away from 5011 (live watcher), "
                         "5001 (Flask), 5031 (banco's scalars backfill) and "
                         "5041 (the TAX backfill)")
    args = ap.parse_args()

    wanted = [d.strip() for d in args.datasets.split(",") if d.strip()]
    bad = [d for d in wanted if d not in DATASETS]
    if bad:
        log(f"unknown dataset(s): {', '.join(bad)} (choose from {', '.join(DATASETS)})")
        return 2
    if args.beam_out:
        DATASETS["beam"]["dir"] = args.beam_out
    if args.spill_out:
        DATASETS["spill"]["dir"] = args.spill_out

    today = datetime.now().date()
    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = (datetime.strptime(args.end, "%Y-%m-%d").date() if args.end
           else today - timedelta(days=1))
    if end >= today and not args.include_today:
        end = today - timedelta(days=1)
        log(f"clamped end to {end} (today is live — use --include-today to override)")
    if start > end:
        log("nothing to do: start is after end")
        return 0

    log(f"target {start} .. {end}   datasets={','.join(wanted)}")
    for name in wanted:
        log(f"  {name:6s} -> {DATASETS[name]['dir']}")

    import pytimber
    log("starting NXCALS session (Spark spin-up, ~1 min)...")
    db = pytimber.LoggingDB(source="nxcals", sparkprops={
        "spark.driver.port": args.driver_port, "spark.ui.enabled": "false"})
    log("NXCALS session up")

    totals = {name: 0 for name in wanted}
    fills = {name: 0 for name in wanted}
    day = start
    while day <= end:
        t0 = datetime.combine(day, datetime.min.time())
        t1 = t0 + timedelta(days=1)
        for name in wanted:
            spec = DATASETS[name]
            path = os.path.join(spec["dir"], f"{spec['prefix']}{day:%Y-%m-%d}.csv")
            try:
                fetched = spec["fetch"](db, t0, t1)
            except Exception as e:
                log(f"  {day} {name:6s} QUERY FAILED: {type(e).__name__}: {e}")
                continue
            existing = _read_existing(path)
            before = len(existing)
            filled = 0
            for ts, row in fetched.items():
                old = existing.get(ts)
                if old is None:
                    existing[ts] = row
                    continue
                if args.no_fill_blanks:
                    continue
                # Repair only: write into empty cells, never over a real value.
                touched = False
                for k in spec["fields"]:
                    if old.get(k) in ("", None) and row.get(k) not in ("", None):
                        old[k] = row[k]
                        touched = True
                filled += touched
            new = len(existing) - before
            totals[name] += new
            fills[name] += filled
            if new or filled or not os.path.exists(path):
                _write_day(path, existing, spec["fields"])
            log(f"  {day} {name:6s} {new:6d} new rows ({before} -> {len(existing)}), "
                f"{filled:5d} rows blank-filled")
        day += timedelta(days=1)

    log("done — " + ", ".join(f"{n}: {c} new rows / {fills[n]} blank-filled"
                              for n, c in totals.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
