#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on July 27 2026
Created in PyCharm
Created as nTof_x17_DAQ/projections/reference_maxima.py

@author: Dylan Neff, dylan

Best trigger rate and best beam delivery achieved so far, written to
reference_maxima.json for the stats page to grade the live numbers against.

Both use the 99th percentile, not the outright maximum. A single lucky sub-run or
one exceptional 10-minute bucket is not a standard worth grading against, and a
ceiling that ratchets up on noise makes every later measurement look worse for no
reason. p99 sits just under the true peak in both cases and is stable.

Refresh occasionally — these only ratchet upward, so a stale file understates
nothing except the ceiling itself.

Usage:
    python reference_maxima.py            # rescan and rewrite
    python reference_maxima.py --show     # print the current file
"""

import argparse
import glob
import json
import os
from datetime import datetime

import numpy as np
import pandas as pd

import run_stats

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(HERE, "reference_maxima.json")

BUCKET_S = 600.0        # must match the page's beam-history bucket
PCT = 99


def trigger_reference():
    """Best sustained beam trigger rate, from the ledger."""
    df = run_stats.load_stats(sync=False)
    b = df[(~df.is_cosmic) & (df.hours > 0)].copy()
    b["hz"] = b.events / (b.hours * 3600.0)
    b = b[b.hz > 0]
    if not len(b):
        return {}
    top = b.nlargest(1, "hz").iloc[0]
    return {
        "hz_p99": float(np.percentile(b.hz, PCT)),
        "hz_max": float(b.hz.max()),
        "hz_median": float(b.hz.median()),
        "n_subruns": int(len(b)),
        "best_subrun": f"{top['run']}/{top['subrun']}",
    }


def beam_reference(csv_dir=run_stats.BEAM_CSV_DIR):
    """Best beam delivery in protons/day, over every daily CSV we still have."""
    chunks = []
    days = 0
    for path in sorted(glob.glob(os.path.join(csv_dir, "beam_intensity_2*.csv"))):
        try:
            d = pd.read_csv(path, usecols=["unix_ts", "intensity_e10"])
        except Exception:
            continue
        d = d[d.intensity_e10 > run_stats.PULSE_THRESHOLD_E10]
        if not len(d):
            continue
        bucket = (d.unix_ts // BUCKET_S).astype(np.int64)
        # Protons delivered in each bucket, scaled to a full day.
        chunks.append((d.groupby(bucket).intensity_e10.sum()
                       * 1e10 * (86400.0 / BUCKET_S)).values)
        days += 1
    if not chunks:
        return {}
    allb = np.concatenate(chunks)
    return {
        "protons_per_day_p99": float(np.percentile(allb, PCT)),
        "protons_per_day_max": float(allb.max()),
        "protons_per_day_median": float(np.median(allb)),
        "n_buckets": int(allb.size),
        "n_days": days,
        "bucket_s": BUCKET_S,
    }


def build():
    return {
        "computed": datetime.now().isoformat(timespec="seconds"),
        "percentile": PCT,
        "trigger": trigger_reference(),
        "beam": beam_reference(),
    }


def load(path=OUT_PATH):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser(description="Best-achieved reference maxima.")
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args()

    if args.show:
        print(json.dumps(load() or {}, indent=2))
        return

    ref = build()
    with open(OUT_PATH, "w") as f:
        json.dump(ref, f, indent=1)

    t, b = ref["trigger"], ref["beam"]
    if t:
        print(f"Trigger rate  p{PCT} {t['hz_p99']:6.2f} Hz   "
              f"(max {t['hz_max']:.2f} in {t['best_subrun']}, "
              f"median {t['hz_median']:.2f}, n={t['n_subruns']})")
    if b:
        print(f"Beam delivery p{PCT} {b['protons_per_day_p99']:.3e} p/day  "
              f"(max {b['protons_per_day_max']:.3e}, "
              f"median {b['protons_per_day_median']:.3e}, "
              f"{b['n_buckets']} buckets over {b['n_days']} days)")
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
