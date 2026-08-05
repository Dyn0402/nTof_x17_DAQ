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
from datetime import datetime, timedelta

import run_stats
import schedule as sched_mod

HERE = os.path.dirname(os.path.abspath(__file__))
SAVED_DIR = os.path.join(HERE, "saved")


def load_projections(saved_dir=SAVED_DIR):
    """Every frozen projection, oldest first. Duplicated from plot_progress.py
    rather than imported from it: that module pulls in matplotlib, which this
    page's Flask process should not have to carry just to read some JSON."""
    out = []
    for path in sorted(glob.glob(os.path.join(saved_dir, "projection_*.json"))):
        try:
            with open(path) as f:
                p = json.load(f)
            p["_name"] = os.path.basename(path)[len("projection_"):-len(".json")]
            out.append(p)
        except Exception as e:
            print(f"[live] Skipping {path}: {e}")
    return out


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


def summary(first_run=None, now=None, rate_first_run=None):
    """Everything the page needs about statistics and the projection.

    first_run=None counts every run in the ledger, including ones long since deleted
    from disk. The forward rate is fitted only on the production point named by
    schedule.json's rate_first_run — a different question, different subset."""
    now = now or datetime.now()
    if rate_first_run is None:
        try:
            import schedule as sched_mod
            rate_first_run = sched_mod.load_schedule().get("rate_first_run")
        except Exception:
            rate_first_run = None
    stats = run_stats.summarise(first_run=first_run, rate_first_run=rate_first_run)
    rate = stats.get("rate") or {}

    crate = stats.get("cosmic_rate") or {}
    out = {
        "first_run": f"run_{first_run}" if first_run else "all recorded runs",
        "rate_runs": stats.get("rate_runs") or [],
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


def cumulative_series(first_run=None, rate_first_run=None):
    """The full cumulative-triggers-vs-projection curves as plain JSON-able
    series — everything projections/plot_progress.py's top panel draws, minus
    the drawing. Lets the Shift tab render it with Plotly to match the
    dashboard's own dark styling instead of embedding a matplotlib PNG."""
    sched = sched_mod.load_schedule()
    if rate_first_run is None:
        rate_first_run = sched.get("rate_first_run")
    stats = run_stats.summarise(first_run=first_run, rate_first_run=rate_first_run)
    beam = stats["beam"]
    if not len(beam):
        return None

    t_end = sched_mod._parse(sched["run_end"])
    t_lo = beam.t_start.min().to_pydatetime()

    downtime = []
    for a, b in sched_mod.downtime_intervals(sched):
        a, b = max(a, t_lo), min(b, t_end)
        if b > a:
            downtime.append([a.isoformat(), b.isoformat()])

    projections = load_projections()
    proj_out = []
    for i, p in enumerate(projections):
        pts = p.get("points") or []
        proj_out.append({
            "name": p["_name"],
            "newest": i == len(projections) - 1,
            "efficiency": p.get("efficiency"),
            "final_events": p.get("final_events"),
            "t": [pt["t"] for pt in pts],
            "events": [pt["events"] for pt in pts],
            "events_at_100pct": [pt.get("events_at_100pct") for pt in pts],
        })

    bt, bc = run_stats.cumulative(beam)
    rate = stats.get("rate") or {}
    rate_runs = stats.get("rate_runs") or []

    def _f(x):
        return float(x) if x is not None else None

    return {
        "t_lo": t_lo.isoformat(),
        "t_end": t_end.isoformat(),
        "downtime": downtime,
        "projections": proj_out,
        # numpy scalars (from pandas aggregation) aren't all JSON-serializable —
        # int64 in particular, unlike float64, doesn't subclass the builtin type.
        "recorded": {"t": [t.isoformat() for t in bt], "cumulative": [int(c) for c in bc]},
        "counted": f"run_{first_run}+" if first_run else "all recorded runs",
        "rate_runs": rate_runs,
        "events_per_pulse": _f(rate.get("events_per_pulse")),
        "pulses_per_hour": _f(rate.get("pulses_per_hour")),
        "events_per_beam_hour": _f(rate.get("events_per_beam_hour")),
    }


def _run_mode(group):
    """beam / cosmics / pulser for a whole run.

    By majority, not by the first sub-run: a handful of rows carry beam_type
    'unknown' because their run_config could not be read at scan time (9 of them in
    run_72 and run_76 today), and those land on is_cosmic=False. Taking iloc[0]
    would let one unreadable first sub-run relabel an entire cosmics run as beam."""
    if group.beam_type.isin(run_stats.NON_PHYSICS_BEAM_TYPES).any():
        return "pulser"
    return "cosmics" if bool(group.is_cosmic.mean() > 0.5) else "beam"


def run_history(days=7.0, now=None, min_gap_min=5.0, sync=False):
    """The run list (everything) plus a drawable sub-run timeline (recent window).

    Two different questions, so two different spans in one block:

      `runs`  EVERY run in the ledger, per-run aggregate. This is the run list, and
              it is small — the ledger starts at run_67, where the pre-ledger runs
              had already been deleted from disk.
      `subs`  only the last `days`, as flat [start_epoch, seconds, events, is_cosmic]
              rows. A wall-clock timeline over the whole ledger would be unreadable,
              and per-sub-run rows are the bulky part of the payload.

    Two things the raw ledger will mislead you about, both fixed here:

    `hours` is the sub-run's NOMINAL window, not how long it actually ran. When
    mode_watcher changes over mid-sub-run it stops the run where it stands, so a
    one-hour sub-run cut off after a minute still carries hours=1.0. Dividing events
    by that invents a rate 60x too low and makes a perfectly healthy changeover look
    like a dead detector. So every run also gets `h_air` — wall-clock from its own
    start to the start of the next run, which is what actually elapsed — and `rate`
    is computed on that. `trunc` marks the runs where the two disagree.

    Sub-runs of consecutive runs OVERLAP in the ledger for the same reason (the old
    run's nominal window outlives the changeover), so each sub-run's drawn width is
    clipped to its run's real end. Without that the bars of a changeover pair sit on
    top of each other.

    Non-physics runs (the saturating-pulser DAQ characterisations, run_90/92/94) are
    LISTED but flagged `physics: false` and left out of every total — a run list that
    silently omits three run numbers is confusing, and folding 200 kHz pulser
    sub-runs into the statistics would wreck them."""
    now = now or datetime.now()
    if sync:                        # refresh the ledger from disk before reading it
        run_stats.load_stats(first_run=None, sync=True)
    # Straight from the ledger, so the non-physics runs survive to be labelled rather
    # than filtered out the way load_stats() does.
    df = run_stats.load_ledger()
    if not len(df):
        return None
    df = df.sort_values("t_start").reset_index(drop=True)

    # Per-run aggregate over EVERY run. `t1_air` is capped by the next run's start,
    # because that is where mode_watcher actually stopped this one.
    runs = []
    for name, g in df.groupby("run", sort=False):
        mode = _run_mode(g)
        runs.append({
            "run": name,
            "num": run_stats.run_number(name),
            "mode": mode,
            "physics": mode != "pulser",
            "t0": g.t_start.min().to_pydatetime(),
            "t1": g.t_end.max().to_pydatetime(),
            "n": int(len(g)),
            "h": round(float(g.hours.sum()), 2),
            "ev": int(g.events.sum()),
        })
    runs.sort(key=lambda r: r["t0"])
    for i, r in enumerate(runs):
        cap = runs[i + 1]["t0"] if i + 1 < len(runs) else r["t1"]
        r["t1_air"] = min(r["t1"], max(cap, r["t0"]))
        r["h_air"] = round((r["t1_air"] - r["t0"]).total_seconds() / 3600.0, 2)
        r["trunc"] = r["h_air"] < r["h"] - 0.05
        # Below ~a minute the quotient is noise, not a rate.
        r["rate"] = round(r["ev"] / r["h_air"]) if r["h_air"] > 0.02 else None

    # Everything below this line is the recent window only.
    phys = df[~df.beam_type.isin(run_stats.NON_PHYSICS_BEAM_TYPES)]
    t_hi = phys.t_end.max().to_pydatetime()
    t_lo = t_hi - timedelta(days=float(days))
    win = phys[phys.t_end >= t_lo]

    air_end = {r["run"]: r["t1_air"] for r in runs}
    subs = []
    for row in win.itertuples():
        end = min(row.t_end.to_pydatetime(), air_end.get(row.run, row.t_end.to_pydatetime()))
        secs = max(60.0, (end - row.t_start.to_pydatetime()).total_seconds())
        subs.append([round(row.t_start.timestamp()), round(secs),
                     int(row.events), int(bool(row.is_cosmic))])

    # Measured beam-off intervals, same derivation the stop-duration study uses
    # (logger-gap guarded, so a dead logger never reads as downtime).
    # The timeline's axis runs from the first sub-run in the window, not from t_lo,
    # and beam_uptime_pct is measured against that same span -- so downtime is
    # queried and clipped on the axis, or the numerator and denominator would be
    # over different intervals.
    axis_lo = win.t_start.min().to_pydatetime() if len(win) else t_lo
    # Downtime is loaded over the WHOLE ledger span (not just the drawn window)
    # so the per-run off-time split below covers every run in the list. Each
    # interval is tagged with WHY the beam was off ('ps' machine-side / 'ntof'
    # our side / None before the class record starts) from the per-minute
    # beam_class CSVs — see beam_monitor/beam_class.py.
    ledger_lo = df.t_start.min().to_pydatetime()
    gaps, off_s = [], 0.0
    off_split = {"ps": 0.0, "ntof": 0.0, None: 0.0}
    for r in runs:
        r["off_ps_h"] = r["off_ntof_h"] = r["off_unk_h"] = 0.0
    try:
        # classify_downtime SPLITS a stop whose cause changed partway, so every
        # (a, b, cls) piece is single-cause and attribution is a straight sum.
        down = run_stats.classify_downtime(
            run_stats.load_actual_downtime(ledger_lo, t_hi),
            run_stats.load_beam_class_minutes(ledger_lo, t_hi))
        for a, b, cls in down:
            # load_actual_downtime returns every interval that OVERLAPS the window,
            # unclipped: a two-day stop that began before t_lo would otherwise put
            # 48 h of downtime into a 7-day window that contains only a few hours of
            # it, understating uptime (and able to drive it negative). It would also
            # be drawn past the left edge of the timeline, over the y-axis labels.
            a2, b2 = max(a, axis_lo), min(b, t_hi)
            secs = (b2 - a2).total_seconds()
            if secs > 0:
                off_s += secs
                off_split[cls] += secs
                if secs >= float(min_gap_min) * 60.0:
                    gaps.append([round(a2.timestamp()), round(b2.timestamp()), cls])
            # Per-run split, on the same real-elapsed span the rate uses.
            for r in runs:
                ra = max(a, r["t0"])
                rb = min(b, r["t1_air"])
                s = (rb - ra).total_seconds()
                if s <= 0:
                    continue
                key = {"ps": "off_ps_h", "ntof": "off_ntof_h"}.get(cls, "off_unk_h")
                r[key] += s / 3600.0
        for r in runs:
            for k in ("off_ps_h", "off_ntof_h", "off_unk_h"):
                r[k] = round(r[k], 2)
    except Exception as e:                                        # noqa: BLE001
        print(f"[live] Downtime lookup failed: {e}")

    def _totals(frame, extra=None):
        out = {
            "n_subruns": int(len(frame)),
            "events": int(frame.events.sum()),
            "beam_events": int(frame[~frame.is_cosmic].events.sum()),
            "cosmic_events": int(frame[frame.is_cosmic].events.sum()),
            "hours": round(float(frame.hours.sum()), 2),
        }
        out.update(extra or {})
        return out

    win_span_s = (t_hi - axis_lo).total_seconds() if len(win) else 0
    return {
        "generated": now.isoformat(timespec="seconds"),
        "days": float(days),
        "min_gap_min": float(min_gap_min),
        # Window (the timeline).
        "t_lo": (win.t_start.min().to_pydatetime().isoformat(timespec="minutes")
                 if len(win) else None),
        "t_hi": t_hi.isoformat(timespec="minutes"),
        "subs": subs,
        "gaps": gaps,
        "totals": _totals(win, {
            "n_runs": len({r for r in win.run}),
            "beam_off_h": round(off_s / 3600.0, 2),
            "beam_off_ps_h": round(off_split["ps"] / 3600.0, 2),
            "beam_off_ntof_h": round(off_split["ntof"] / 3600.0, 2),
            "beam_uptime_pct": (round(100.0 * (1.0 - off_s / win_span_s), 1)
                                if win_span_s > 0 else None),
        }),
        # All time (the run list).
        "runs": [dict(r, t0=r["t0"].isoformat(timespec="minutes"),
                      t1=r["t1"].isoformat(timespec="minutes"),
                      t1_air=r["t1_air"].isoformat(timespec="minutes"),
                      t0_epoch=round(r["t0"].timestamp())) for r in runs],
        "all_time": _totals(phys, {
            "n_runs": sum(1 for r in runs if r["physics"]),
            "n_other_runs": sum(1 for r in runs if not r["physics"]),
            "t_lo": df.t_start.min().to_pydatetime().isoformat(timespec="minutes"),
            "t_hi": t_hi.isoformat(timespec="minutes"),
        }),
    }


def main():
    import sys
    print(json.dumps(summary(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
