#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on July 27 2026
Created in PyCharm
Created as nTof_x17_DAQ/projections/live.py

@author: Dylan Neff, dylan

Compact projection summary for the public statistics page: how much beam data we
have, where the frozen projection says we should be right now, and where it says
we land.

Kept separate from make_projection.py because this runs repeatedly against a
projection that is already frozen — it only ever reads one, never creates one.
"""

import glob
import json
import os
from datetime import datetime

import run_stats

HERE = os.path.dirname(os.path.abspath(__file__))
SAVED_DIR = os.path.join(HERE, "saved")


def latest_projection(saved_dir=SAVED_DIR):
    """The most recently created frozen projection, or None."""
    paths = sorted(glob.glob(os.path.join(saved_dir, "projection_*.json")))
    if not paths:
        return None
    best, best_created = None, ""
    for path in paths:
        try:
            with open(path) as f:
                p = json.load(f)
        except Exception:
            continue
        if p.get("created", "") >= best_created:
            best, best_created = p, p.get("created", "")
            best["_name"] = os.path.basename(path)[len("projection_"):-len(".json")]
    return best


def expected_at(projection, when):
    """Projected cumulative events at `when`, interpolated between grid points.
    Before the projection starts this is its anchor; after it ends, its final value."""
    pts = projection.get("points") or []
    if not pts:
        return projection.get("anchor_events", 0)
    prev = pts[0]
    if when <= datetime.fromisoformat(prev["t"]):
        return prev["events"]
    for pt in pts[1:]:
        t = datetime.fromisoformat(pt["t"])
        t_prev = datetime.fromisoformat(prev["t"])
        if when <= t:
            span = (t - t_prev).total_seconds()
            if span <= 0:
                return pt["events"]
            f = (when - t_prev).total_seconds() / span
            return prev["events"] + f * (pt["events"] - prev["events"])
        prev = pt
    return pts[-1]["events"]


def summary(first_run=79, now=None):
    """Everything the page needs about statistics and the projection."""
    now = now or datetime.now()
    stats = run_stats.summarise(first_run=first_run)
    rate = stats.get("rate") or {}

    crate = stats.get("cosmic_rate") or {}
    out = {
        "first_run": f"run_{first_run}",
        "beam_events": stats.get("beam_events", 0),
        "cosmic_events": stats.get("cosmic_events", 0),
        "cosmic_hz": round(crate.get("hz", 0), 1) or None,
        "n_beam_subruns": int(len(stats["beam"])),
        "last_subrun_end": (stats["beam"].t_end.max().isoformat(timespec="minutes")
                            if len(stats["beam"]) else None),
        "events_per_pulse": round(rate.get("events_per_pulse", 0), 1) or None,
        "pulses_per_hour": round(rate.get("pulses_per_hour", 0)) or None,
        "events_per_beam_hour": round(rate.get("events_per_beam_hour", 0)) or None,
        "projection": None,
    }

    p = latest_projection()
    if not p:
        return out

    expected = expected_at(p, now)
    recorded = out["beam_events"]
    final = p.get("final_events", 0)
    out["projection"] = {
        "name": p.get("_name"),
        "created": p.get("created"),
        "efficiency": p.get("efficiency"),
        "run_end": p.get("schedule", {}).get("run_end"),
        "final_events": final,
        "final_events_at_100pct": p.get("final_events_at_100pct"),
        "final_cosmics": p.get("final_cosmics"),
        "expected_rate_hz": p.get("expected_rate_hz"),
        "expected_now": round(expected),
        "delta": round(recorded - expected),
        # Guarded: expected is the projection's anchor early on, never zero in
        # practice, but a projection created before any data would divide by zero.
        "delta_pct": round(100.0 * (recorded - expected) / expected, 1) if expected else None,
        "pct_complete": round(100.0 * recorded / final, 1) if final else None,
    }
    return out


def main():
    import sys
    print(json.dumps(summary(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
