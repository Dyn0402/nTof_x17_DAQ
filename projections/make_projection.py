#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on July 27 2026
Created in PyCharm
Created as nTof_x17_DAQ/projections/make_projection.py

@author: Dylan Neff, dylan

Freeze a statistics projection: measure the current beam data-taking rate, run it
through the beam-availability schedule, and write the resulting cumulative curve
to saved/projection_<date>.json.

Frozen on purpose. The point is to make a prediction today and find out later how
well it held, so a saved projection is never recomputed — make a new one instead
(weekly, or whenever the schedule changes) and let plot_progress.py show them
side by side against the real curve.

Usage:
    python make_projection.py                 # freeze one, named for today
    python make_projection.py --label rev2    # a second one on the same day
    python make_projection.py --dry-run       # print the numbers, save nothing
"""

import argparse
import json
import os
from datetime import datetime

import run_stats
import schedule as sched_mod

HERE = os.path.dirname(os.path.abspath(__file__))
SAVED_DIR = os.path.join(HERE, "saved")


def build(sched, rate, anchor_events, anchor_note="", cosmic_rate=None,
          anchor_cosmics=0):
    """Cumulative projected beam triggers — and cosmics — on the schedule's grid.

    Anchored on the events already recorded at projection_start, so the projected
    curve is continuous with the real one instead of starting from zero.

    Cosmics accumulate during the scheduled beam-off blocks, on the assumption that
    a beam stop is spent taking a cosmic run (which is what run_80 was). If a
    downtime is spent with the detectors off instead, the cosmic curve will
    over-predict — it is a plan, not a measurement."""
    eff = float(sched.get("efficiency", 1.0))
    per_hour = rate["events_per_beam_hour"]
    cos_per_hour = (cosmic_rate or {}).get("events_per_hour", 0.0)
    curve = sched_mod.availability_and_downtime_curve(sched)

    points = [{
        "t": t.isoformat(timespec="minutes"),
        "beam_hours": round(h, 4),
        "down_hours": round(d, 4),
        "events": round(anchor_events + per_hour * eff * h),
        "events_at_100pct": round(anchor_events + per_hour * h),
        "cosmics": round(anchor_cosmics + cos_per_hour * eff * d),
        # Flat while beam is up, zero while it is not — the shape the rate panel draws.
        "rate_hz": 0.0 if sched_mod.is_down(sched, t) else round(per_hour / 3600.0, 3),
    } for t, h, d in curve]

    s = sched_mod.summary(sched)
    return {
        "created": datetime.now().isoformat(timespec="seconds"),
        "anchor_events": anchor_events,
        "anchor_cosmics": anchor_cosmics,
        "anchor_note": anchor_note,
        "efficiency": eff,
        "rate": rate,
        "cosmic_rate": cosmic_rate or {},
        "schedule": {
            "projection_start": sched["projection_start"],
            "run_end": sched["run_end"],
            "downtimes": sched.get("downtimes", []),
            "generic_daily_downtime": sched.get("generic_daily_downtime"),
            "beam_hours": round(s["beam_hours"], 2),
            "downtime_hours": round(s["downtime_hours"], 2),
            "effective_hours": round(s["effective_hours"], 2),
        },
        "final_events": points[-1]["events"] if points else anchor_events,
        "final_events_at_100pct": points[-1]["events_at_100pct"] if points else anchor_events,
        "final_cosmics": points[-1]["cosmics"] if points else anchor_cosmics,
        "expected_rate_hz": round(rate["events_per_beam_hour"] / 3600.0, 2),
        "points": points,
    }


def anchor_at(beam_df, t_start):
    """Beam events already on disk at the projection start."""
    if not len(beam_df):
        return 0, "no beam sub-runs yet"
    done = beam_df[beam_df.t_end <= t_start]
    note = (f"{len(done)} beam sub-runs complete through "
            f"{done.t_end.max():%Y-%m-%d %H:%M}" if len(done) else "no beam sub-runs complete yet")
    return int(done.events.sum()), note


def main():
    ap = argparse.ArgumentParser(description="Freeze a statistics projection.")
    ap.add_argument("--label", help="suffix for the saved filename (default: today's date)")
    ap.add_argument("--dry-run", action="store_true", help="print, don't save")
    ap.add_argument("--first-run", type=int, default=None,
                    help="first run to count (default: every run in the ledger)")
    args = ap.parse_args()

    sched = sched_mod.load_schedule()
    stats = run_stats.summarise(first_run=args.first_run,
                                rate_first_run=sched.get("rate_first_run"))
    rate = stats["rate"]
    if not rate:
        raise SystemExit("No completed beam sub-runs found — nothing to project from.")

    t_start = sched_mod._parse(sched["projection_start"])
    anchor, note = anchor_at(stats["beam"], t_start)
    anchor_cos, _ = anchor_at(stats["cosmic"], t_start)
    proj = build(sched, rate, anchor, note,
                 cosmic_rate=stats.get("cosmic_rate"), anchor_cosmics=anchor_cos)

    s = sched_mod.summary(sched)
    counted = f"run_{args.first_run}+" if args.first_run else "every recorded run"
    fitted = ", ".join(stats.get("rate_runs") or []) or "?"
    print(f"Counting {counted}; forward rate fitted on {fitted} "
          f"({rate['window_start']} -> {rate['window_end']})")
    print(f"  events/pulse         {rate['events_per_pulse']:>12.1f}   (production point)")
    print(f"  pulses/hour          {rate['pulses_per_hour']:>12.0f}   (machine, wider window)")
    print(f"  events/beam hour     {rate['events_per_beam_hour']:>12,.0f}")
    print()
    print(f"Schedule {s['start']:%a %d %b %H:%M} -> {s['end']:%a %d %b %H:%M}")
    print(f"  beam available       {s['beam_hours']:>12.1f} h")
    print(f"  x {s['efficiency']:.0%} efficiency      {s['effective_hours']:>12.1f} h")
    print()
    print(f"  already recorded     {anchor:>12,}   ({note})")
    print(f"  projected to add     {proj['final_events'] - anchor:>12,}")
    print(f"  PROJECTED TOTAL      {proj['final_events']:>12,}   by {sched['run_end']}")
    print(f"  (at 100% efficiency  {proj['final_events_at_100pct']:>12,})")
    print()
    crate = stats.get("cosmic_rate") or {}
    print(f"  cosmics so far       {stats['cosmic_events']:>12,}  (not included above)")
    if crate:
        print(f"  cosmic rate          {crate['hz']:>12.1f} Hz")
        print(f"  PROJECTED COSMICS    {proj['final_cosmics']:>12,}   "
              f"({s['downtime_hours']:.0f} h of scheduled beam-off)")

    if args.dry_run:
        return

    os.makedirs(SAVED_DIR, exist_ok=True)
    label = args.label or datetime.now().strftime("%Y-%m-%d")
    path = os.path.join(SAVED_DIR, f"projection_{label}.json")
    if os.path.exists(path):
        raise SystemExit(f"{path} already exists — projections are frozen. "
                         f"Use --label to make a distinct one.")
    with open(path, "w") as f:
        json.dump(proj, f, indent=1)
    print(f"Frozen -> {path}")


if __name__ == "__main__":
    main()
