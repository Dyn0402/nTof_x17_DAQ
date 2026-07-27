#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on July 27 2026
Created in PyCharm
Created as nTof_x17_DAQ/projections/run_stats.py

@author: Dylan Neff, dylan

Data layer for the statistics projection: walk the run directories from a given
starting run, split beam from cosmics, and return one record per completed
sub-run with its start time, live time and DREAM event count.

Beam vs cosmics comes from `beam_type` in each run's run_config.json
('neutrons' vs 'cosmics') — not from sub-run name matching, which would break the
moment a run gets named differently.

Event counts come from get_run_events.get_total_events_for_run(), i.e. the per-FEU
count in the RunCtrl log each sub-run leaves behind. An in-progress sub-run has no
log yet and is simply absent — never counted as a zero, which would drag the rate
down every time you look at it mid-sub-run.
"""

import os
import re
import sys
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_DIR)
from get_run_events import get_total_events_for_run   # noqa: E402

RUN_DIR = "/home/mx17/beam_july/runs"
BEAM_CSV_DIR = "/home/mx17/beam_july/slow_control/beam_intensity"

# beam_watcher's own pulse threshold (config/beam_state.json: pulse_threshold_e10)
PULSE_THRESHOLD_E10 = 50.0

COSMIC_BEAM_TYPES = {"cosmics", "cosmic"}


def run_number(run_name):
    """'run_79' -> 79, anything else -> None."""
    m = re.fullmatch(r"run_(\d+)", run_name)
    return int(m.group(1)) if m else None


def _run_config(run_dir, run_name):
    import json
    path = os.path.join(run_dir, run_name, "run_config.json")
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def _subrun_start(subrun_path):
    """Sub-run start time from the datrun filenames the DAQ writes,
    Mx17_<subrun>_datrun_260726_18H07_000_01.fdf -> 2026-07-26 18:07 (local).
    Falls back to the raw_daq_data mtime, which is close enough to keep a sub-run
    on the plot rather than dropping it."""
    raw = os.path.join(subrun_path, "raw_daq_data")
    try:
        names = os.listdir(raw)
    except OSError:
        return None
    for name in sorted(names):
        m = re.search(r"datrun_(\d{6})_(\d{2})H(\d{2})", name)
        if m:
            day = datetime.strptime(m.group(1), "%y%m%d")
            return day.replace(hour=int(m.group(2)), minute=int(m.group(3)))
    try:
        return datetime.fromtimestamp(os.path.getmtime(raw))
    except OSError:
        return None


def scan_runs(run_dir=RUN_DIR, first_run=79, last_run=None):
    """One record per completed sub-run in runs >= first_run.

    Returns a DataFrame with columns:
        run, subrun, beam_type, is_cosmic, t_start, t_end, hours, events
    sorted by t_start. Sub-runs without an event count (in progress, or a failed
    run that never wrote a log) are omitted."""
    rows = []
    for run_name in sorted(os.listdir(run_dir)):
        n = run_number(run_name)
        if n is None or n < first_run or (last_run is not None and n > last_run):
            continue
        cfg = _run_config(run_dir, run_name)
        beam_type = (cfg.get("beam_type") or "unknown").lower()
        planned = {s.get("sub_run_name"): float(s.get("run_time") or 0)
                   for s in cfg.get("sub_runs", [])}
        try:
            _, per_subrun = get_total_events_for_run(run_dir, run_name)
        except FileNotFoundError:
            continue

        for subrun, events in per_subrun.items():
            t0 = _subrun_start(os.path.join(run_dir, run_name, subrun))
            if t0 is None:
                continue
            minutes = planned.get(subrun, 60.0)
            t1 = t0 + timedelta(minutes=minutes)
            rows.append({
                "run": run_name,
                "subrun": subrun,
                "beam_type": beam_type,
                "is_cosmic": beam_type in COSMIC_BEAM_TYPES,
                "t_start": t0,
                "t_end": t1,
                # Epochs are computed HERE, from plain datetimes, and carried as
                # columns. Do not recompute them from the DataFrame's t_start:
                # pandas coerces these to Timestamp, whose .timestamp() reads a
                # naive value as UTC while datetime's reads it as local — a silent
                # 2 h shift under CEST that mis-aligns every beam-pulse window.
                "t_start_unix": t0.timestamp(),
                "t_end_unix": t1.timestamp(),
                "hours": minutes / 60.0,
                "events": int(events),
            })

    df = pd.DataFrame(rows)
    return df.sort_values("t_start").reset_index(drop=True) if len(df) else df


def load_beam_pulses(t_from, t_to, csv_dir=BEAM_CSV_DIR):
    """Unix timestamps of beam pulses in [t_from, t_to], from the beam watcher's
    per-day CSVs. A 'pulse' is a row above the watcher's own intensity threshold."""
    out = []
    day = t_from.date()
    while day <= t_to.date():
        path = os.path.join(csv_dir, f"beam_intensity_{day:%Y-%m-%d}.csv")
        if os.path.exists(path):
            try:
                d = pd.read_csv(path, usecols=["unix_ts", "intensity_e10"])
                out.append(d[d.intensity_e10 > PULSE_THRESHOLD_E10].unix_ts.values)
            except Exception as e:
                print(f"[run_stats] Skipping {path}: {e}", file=sys.stderr)
        day += timedelta(days=1)
    if not out:
        return np.array([])
    p = np.concatenate(out)
    return p[(p >= t_from.timestamp()) & (p <= t_to.timestamp())]


def add_pulse_counts(df, pulses):
    """Add a `pulses` column: beam pulses inside each sub-run's time window."""
    if not len(df):
        return df
    df = df.copy()
    df["pulses"] = [int(((pulses >= a) & (pulses < b)).sum())
                    for a, b in zip(df.t_start_unix, df.t_end_unix)]
    return df


def cumulative(df):
    """(times, cumulative_events) stepping at each sub-run's end time, starting
    from zero at the first sub-run's start."""
    if not len(df):
        return [], []
    times, totals, running = [df.t_start.iloc[0]], [0], 0
    for _, r in df.iterrows():
        running += r.events
        times.append(r.t_end)
        totals.append(running)
    return times, totals


def measure_rate(beam_df, pulses, min_pulse_fraction=0.8):
    """Measure the beam data-taking rate.

    Decomposed deliberately into two factors, because they fail differently:
      events_per_pulse   — detector/trigger performance. Measured at ~104 and
                           flat to ~1% across run_79, so it is the trustworthy half.
      pulses_per_hour    — what the machine delivers. Varies with supercycle and
                           destination sharing, so it is the half to be careful about.

    pulses_per_hour is a median over sub-runs that were *fully* beam-on: a sub-run
    that caught the start of a beam stop has a real event count but only partial
    beam, and averaging it in would quietly understate the rate we can expect while
    beam is actually up. The schedule model handles downtime separately — counting
    it here as well would double-penalise the projection.

    Returns a dict of the fitted parameters and the sample it used.
    """
    if not len(beam_df):
        return {}
    df = add_pulse_counts(beam_df, pulses)
    df = df[df.hours > 0]

    per_hour = df.pulses / df.hours
    nominal = float(per_hour.median()) if len(per_hour) else 0.0
    full = df[per_hour >= min_pulse_fraction * nominal] if nominal else df

    total_events = int(df.events.sum())
    total_pulses = int(df.pulses.sum())
    ev_per_pulse = total_events / total_pulses if total_pulses else float("nan")
    pulses_per_hour = float((full.pulses / full.hours).median()) if len(full) else nominal

    return {
        "events_per_pulse": ev_per_pulse,
        "events_per_pulse_std": float((df.events / df.pulses.replace(0, np.nan)).std()),
        "pulses_per_hour": pulses_per_hour,
        "events_per_beam_hour": ev_per_pulse * pulses_per_hour,
        # For comparison: raw events/hour including partially-beam-off sub-runs.
        "events_per_hour_observed": total_events / float(df.hours.sum()),
        "n_subruns": int(len(df)),
        "n_subruns_full_beam": int(len(full)),
        "total_events": total_events,
        "total_pulses": total_pulses,
        "total_hours": float(df.hours.sum()),
        "window_start": df.t_start.min().isoformat(timespec="minutes"),
        "window_end": df.t_end.max().isoformat(timespec="minutes"),
    }


def measure_cosmic_rate(cosmic_df):
    """Cosmic trigger rate, for projecting what we pick up during beam-off periods.

    No pulse normalisation here — cosmics do not care about the machine, which is
    exactly why they are the thing worth counting when the beam is down."""
    if not len(cosmic_df):
        return {}
    hours = float(cosmic_df.hours.sum())
    events = int(cosmic_df.events.sum())
    if hours <= 0:
        return {}
    return {
        "events_per_hour": events / hours,
        "hz": events / (hours * 3600.0),
        "n_subruns": int(len(cosmic_df)),
        "total_events": events,
        "total_hours": hours,
    }


def summarise(run_dir=RUN_DIR, first_run=79):
    """Everything the projection and the plots need, in one call."""
    df = scan_runs(run_dir, first_run=first_run)
    if not len(df):
        return {"subruns": df, "beam": df, "cosmic": df, "rate": {}, "pulses": np.array([])}
    beam = df[~df.is_cosmic].reset_index(drop=True)
    cosmic = df[df.is_cosmic].reset_index(drop=True)
    pulses = load_beam_pulses(df.t_start.min().to_pydatetime(),
                              df.t_end.max().to_pydatetime())
    return {
        "subruns": df,
        "beam": beam,
        "cosmic": cosmic,
        "pulses": pulses,
        "rate": measure_rate(beam, pulses),
        "cosmic_rate": measure_cosmic_rate(cosmic),
        "beam_events": int(beam.events.sum()),
        "cosmic_events": int(cosmic.events.sum()),
    }


def main():
    s = summarise()
    beam, cosmic, rate = s["beam"], s["cosmic"], s["rate"]
    print(f"Beam sub-runs   : {len(beam):>4}   events {s['beam_events']:>12,}")
    print(f"Cosmic sub-runs : {len(cosmic):>4}   events {s['cosmic_events']:>12,}")
    if rate:
        print(f"\nWindow {rate['window_start']} -> {rate['window_end']}")
        print(f"  events/pulse       {rate['events_per_pulse']:.1f}")
        print(f"  pulses/hour        {rate['pulses_per_hour']:.0f}")
        print(f"  events/beam hour   {rate['events_per_beam_hour']:,.0f}")
        print(f"  observed events/h  {rate['events_per_hour_observed']:,.0f}"
              f"  ({rate['n_subruns_full_beam']}/{rate['n_subruns']} sub-runs fully beam-on)")


if __name__ == "__main__":
    main()
