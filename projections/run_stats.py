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

import bisect
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
# How far back to load beam-pulse CSVs. The rate fit only wants recent sub-runs, and
# the ledger now reaches back past the pulse record.
PULSE_WINDOW_DAYS = 10

COSMIC_BEAM_TYPES = {"cosmics", "cosmic"}

# DAQ characterization runs, counted as NEITHER beam nor cosmics. Everything that is not a
# COSMIC_BEAM_TYPE is otherwise treated as beam physics, so a saturating-pulser ladder
# (run_config_ipd_ladder_pulser.py: 13 points x 20 000 events in ~7 min) would post a
# quarter of a million fake events to the beam projection and dent the achieved-rate
# reference. A run opts out by setting `beam_type` here.
NON_PHYSICS_BEAM_TYPES = {"pulser", "test", "daq_test"}


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


def _stamp(pattern, names):
    """First YYMMDD_HHhMM stamp matching `pattern` in `names`, as a datetime."""
    for name in sorted(names):
        m = re.search(pattern, name)
        if m:
            day = datetime.strptime(m.group(1), "%y%m%d")
            return day.replace(hour=int(m.group(2)), minute=int(m.group(3)))
    return None


def _subrun_start(subrun_path):
    """(start_time, source) for a sub-run, from the most reliable name available.

    Two sources, because the first one disappears: once a run has been backed up to
    EOS the space manager deletes its .fdf files, so `datrun_...` names are gone from
    older runs — but `RunCtrl_YYMMDD_HHhMM.log` survives and carries the same stamp
    (verified identical on run_79). Falling through to the directory mtime would
    otherwise date every cleaned run to the moment it was CLEANED, which stacks a
    dozen runs onto one instant and turns the cumulative curve into a cliff.
    """
    raw = os.path.join(subrun_path, "raw_daq_data")
    try:
        names = os.listdir(raw)
    except OSError:
        return None, None

    t = _stamp(r"datrun_(\d{6})_(\d{2})H(\d{2})", names)
    if t is not None:
        return t, "datrun"
    t = _stamp(r"RunCtrl_(\d{6})_(\d{2})H(\d{2})", names)
    if t is not None:
        return t, "runctrl"
    try:
        return datetime.fromtimestamp(os.path.getmtime(raw)), "mtime"
    except OSError:
        return None, None


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
        if beam_type in NON_PHYSICS_BEAM_TYPES:
            continue
        planned = {s.get("sub_run_name"): float(s.get("run_time") or 0)
                   for s in cfg.get("sub_runs", [])}
        try:
            _, per_subrun = get_total_events_for_run(run_dir, run_name)
        except FileNotFoundError:
            continue

        for subrun, events in per_subrun.items():
            t0, src = _subrun_start(os.path.join(run_dir, run_name, subrun))
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
                "t_source": src,
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


def load_actual_downtime(t_from, t_to, csv_dir=BEAM_CSV_DIR):
    """Measured beam-off intervals [(start, end), ...] in [t_from, t_to], from
    gaps between beam pulses in the per-day CSVs.

    Same method (and same STOP_S/LOGGER_GAP_S constants) as
    beam_monitor/analyze_stop_durations.py, which derives STOP_S=80s from the
    empty band in the pulse-gap distribution — see that file for the measurement.
    A gap only counts as a real stop if the CSV kept logging rows all the way
    through it; otherwise a dead logger would be indistinguishable from a beam
    stop and would inflate this list with fake downtime."""
    from beam_monitor.analyze_stop_durations import STOP_S, LOGGER_GAP_S, PULSE_E10

    rows = []
    day = t_from.date()
    while day <= t_to.date():
        path = os.path.join(csv_dir, f"beam_intensity_{day:%Y-%m-%d}.csv")
        if os.path.exists(path):
            try:
                rows.append(pd.read_csv(path, usecols=["unix_ts", "intensity_e10"]))
            except Exception as e:
                print(f"[run_stats] Skipping {path}: {e}", file=sys.stderr)
        day += timedelta(days=1)
    if not rows:
        return []

    df = pd.concat(rows, ignore_index=True).sort_values("unix_ts")
    df = df.drop_duplicates(subset="unix_ts")
    t_all = df.unix_ts.to_numpy()
    pulses = t_all[df.intensity_e10.to_numpy() > PULSE_E10]
    if len(pulses) < 2:
        return []
    gaps = np.diff(pulses)
    starts, ends = pulses[:-1], pulses[1:]

    out = []
    for i in np.where(gaps > STOP_S)[0]:
        a, b = starts[i], ends[i]
        between = t_all[(t_all > a) & (t_all < b)]
        if len(between) < 2:
            continue  # too few rows to tell a stop from a dead logger
        if np.max(np.diff(np.concatenate(([a], between, [b])))) >= LOGGER_GAP_S:
            continue  # the logger itself went quiet inside this gap
        out.append((datetime.fromtimestamp(a), datetime.fromtimestamp(b)))

    return [(a, b) for a, b in out if b > t_from and a < t_to]


def load_beam_class_minutes(t_from, t_to, csv_dir=BEAM_CSV_DIR):
    """{minute_unix_ts: beam_class} in [t_from, t_to] from the beam watcher's
    per-minute beam_class_*.csv (classes from beam_monitor/beam_class.py:
    on / off_ntof / off_ps / no_data). Empty dict when no class CSVs cover the
    window (they start 2026-07-01)."""
    out = {}
    day = t_from.date()
    while day <= t_to.date():
        path = os.path.join(csv_dir, f"beam_class_{day:%Y-%m-%d}.csv")
        if os.path.exists(path):
            try:
                d = pd.read_csv(path, usecols=["unix_ts", "beam_class"])
                for t, c in zip(d.unix_ts.values, d.beam_class.values):
                    out[float(t)] = str(c)
            except Exception as e:
                print(f"[run_stats] Skipping {path}: {e}", file=sys.stderr)
        day += timedelta(days=1)
    lo, hi = t_from.timestamp(), t_to.timestamp()
    return {t: c for t, c in out.items() if lo <= t <= hi}


def classify_downtime(intervals, class_minutes, min_run_min=5):
    """Tag each (start, end) datetime interval from load_actual_downtime with WHY
    the beam was off: 'ps' (machine-side), 'ntof' (nTOF-side: access / pulled
    from the supercycle) or None (no classification data).

    An outage can genuinely change cause partway — 2026-08-04 was a requested
    access from 08:43 whose afternoon was swallowed by a machine-wide outage —
    so an interval whose per-minute classes switch is SPLIT at the transitions
    and each piece labelled separately (the output can be longer than the
    input). A majority label here would paint the whole 11 h band with whichever
    cause lasted longer. Runs shorter than `min_run_min` minutes are absorbed
    into their larger neighbour so one flickery minute (a sparse machine-alive
    pulse just missing a bin) cannot shred a band into slivers."""
    if not class_minutes:
        return [(a, b, None) for a, b in intervals]
    ts = sorted(class_minutes)
    out = []
    for a, b in intervals:
        lo, hi = a.timestamp(), b.timestamp()
        # Consecutive same-class runs of the off-classified minutes inside the
        # stop ('on'/'no_data' minutes don't break a run — they carry no cause).
        runs = []                      # [cls, first_minute_ts, n_minutes]
        i = bisect.bisect_left(ts, lo - 60)
        while i < len(ts) and ts[i] < hi:
            c = class_minutes[ts[i]]
            if c in ("off_ps", "off_ntof"):
                if runs and runs[-1][0] == c:
                    runs[-1][2] += 1
                else:
                    runs.append([c, ts[i], 1])
            i += 1
        # Smooth: fold every sub-threshold run into a neighbour (previous when
        # there is one), re-coalescing as we go, until only real runs remain.
        changed = True
        while changed and len(runs) > 1:
            changed = False
            for k, r in enumerate(runs):
                if r[2] >= min_run_min:
                    continue
                if k > 0:
                    runs[k - 1][2] += r[2]
                else:
                    runs[1][1] = r[1]
                    runs[1][2] += r[2]
                del runs[k]
                # Merging can leave two same-class neighbours — coalesce.
                for j in range(len(runs) - 1, 0, -1):
                    if runs[j][0] == runs[j - 1][0]:
                        runs[j - 1][2] += runs[j][2]
                        del runs[j]
                changed = True
                break
        if not runs:
            out.append((a, b, None))
            continue
        # Sub-interval boundaries sit where the next run's first minute starts;
        # the outer edges keep the pulse-measured stop boundaries.
        marks = [a] + [datetime.fromtimestamp(r[1]) for r in runs[1:]] + [b]
        for (lo_dt, hi_dt), r in zip(zip(marks[:-1], marks[1:]), runs):
            if hi_dt > lo_dt:
                out.append((lo_dt, hi_dt, "ntof" if r[0] == "off_ntof" else "ps"))
    return out


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


def measure_rate(beam_df, pulses, min_pulse_fraction=0.8, delivery_df=None):
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
    # Keep only sub-runs the pulse record actually covers. A sub-run from before the
    # CSVs start has real events but zero pulses, and dividing one by the other would
    # inflate events/pulse without bound. A genuinely beam-off sub-run also has zero
    # pulses and has no business in a beam-rate fit either, so one test covers both.
    df = df[df.pulses > 0]
    if not len(df):
        return {}

    # events/pulse comes from `df` — the current production point, because it is a
    # property of the detector and trigger.
    total_events = int(df.events.sum())
    total_pulses = int(df.pulses.sum())
    ev_per_pulse = total_events / total_pulses if total_pulses else float("nan")

    # pulses/hour comes from `delivery_df` — a WIDER window, because it is a property
    # of the machine and is noisy hour to hour. Taking it from a single production
    # sub-run that happened to catch poor beam (run_86's first hour: 605 pulses/h
    # against a normal ~1050) would halve the projection for a reason that has
    # nothing to do with the detector.
    dd = df if delivery_df is None else add_pulse_counts(delivery_df, pulses)
    dd = dd[(dd.hours > 0) & (dd.pulses > 0)]
    if not len(dd):
        dd = df
    per_hour = dd.pulses / dd.hours
    nominal = float(per_hour.median()) if len(per_hour) else 0.0
    full = dd[per_hour >= min_pulse_fraction * nominal] if nominal else dd
    pulses_per_hour = float((full.pulses / full.hours).median()) if len(full) else nominal

    return {
        "events_per_pulse": ev_per_pulse,
        "events_per_pulse_std": float((df.events / df.pulses.replace(0, np.nan)).std()),
        "pulses_per_hour": pulses_per_hour,
        "events_per_beam_hour": ev_per_pulse * pulses_per_hour,
        # For comparison: raw events/hour including partially-beam-off sub-runs.
        "events_per_hour_observed": total_events / float(df.hours.sum()),
        "n_subruns": int(len(df)),
        "n_subruns_delivery": int(len(dd)),
        "n_subruns_full_beam": int(len(full)),
        "total_events": total_events,
        "total_pulses": total_pulses,
        "total_hours": float(df.hours.sum()),
        "window_start": df.t_start.min().isoformat(timespec="minutes"),
        "window_end": df.t_end.max().isoformat(timespec="minutes"),
    }


LEDGER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "stats_ledger.csv")
# The epochs are STORED, not re-derived on load. Re-deriving them from the naive
# local timestamp strings would silently shift every historical row when the machine
# leaves CEST for CET in October — and pandas would read those naive strings as UTC
# anyway. Persisting the number computed at scan time makes the ledger self-contained.
LEDGER_COLS = ["run", "subrun", "beam_type", "is_cosmic", "t_start", "t_end",
               "t_start_unix", "t_end_unix", "hours", "events", "t_source"]


def load_ledger(path=LEDGER_PATH):
    """The persistent per-sub-run record. Empty frame if there isn't one yet."""
    if not os.path.exists(path):
        return pd.DataFrame(columns=LEDGER_COLS)
    df = pd.read_csv(path, parse_dates=["t_start", "t_end"])
    df["is_cosmic"] = df["is_cosmic"].astype(bool)
    return df


def sync_ledger(disk_df, path=LEDGER_PATH):
    """Fold a disk scan into the ledger and write it back.

    The ledger is what makes these statistics survive the disk. Runs get deleted
    once they are safely on EOS — run_1 through run_66 already are — and their event
    counts would go with them, silently shrinking the cumulative total. So every
    scan is merged in and rows are NEVER dropped for being absent from disk: a
    sub-run that has aged out keeps its recorded numbers forever.

    Disk wins on conflict, since a re-processed sub-run can legitimately change its
    count. Returns (combined, n_added, n_updated, n_ledger_only)."""
    ledger = load_ledger(path)
    disk = disk_df[[c for c in LEDGER_COLS if c in disk_df.columns]].copy()

    key = ["run", "subrun"]
    if len(ledger):
        merged = ledger.set_index(key)
        incoming = disk.set_index(key)
        n_added = len(incoming.index.difference(merged.index))
        common = incoming.index.intersection(merged.index)
        n_updated = int((merged.loc[common, "events"].sort_index().values
                         != incoming.loc[common, "events"].sort_index().values).sum())
        n_only = len(merged.index.difference(incoming.index))
        merged = incoming.combine_first(merged)
        combined = merged.reset_index()
    else:
        combined = disk
        n_added, n_updated, n_only = len(disk), 0, 0

    combined = combined.sort_values("t_start").reset_index(drop=True)
    combined.to_csv(path, index=False, columns=LEDGER_COLS)
    return combined, n_added, n_updated, n_only


def load_stats(run_dir=RUN_DIR, first_run=None, sync=True):
    """Every sub-run we have ever recorded: the ledger, refreshed from disk.

    first_run=None means everything. Rows whose timestamp could only be taken from
    a directory mtime are kept but flagged in `t_source` — those runs were cleaned
    before this ledger existed and their times are the cleanup time, not the
    acquisition time."""
    df = load_ledger()
    if sync:
        try:
            disk = scan_runs(run_dir, first_run=1)
            if len(disk):
                df, added, updated, only = sync_ledger(disk)
                if added or updated:
                    print(f"[run_stats] Ledger: +{added} new, {updated} updated, "
                          f"{only} from deleted runs")
        except Exception as e:
            print(f"[run_stats] Disk scan failed, using ledger alone: {e}",
                  file=sys.stderr)
    # The ledger persists rows written before NON_PHYSICS_BEAM_TYPES existed (e.g. the
    # sub-12-second pulser characterization sub-runs from run_90-94), so scan_runs'
    # filter alone doesn't keep them out — they must also be dropped here, or they
    # sit in "beam" forever with events/12s rates that dwarf real beam and blow out
    # any plot's rate axis.
    if len(df):
        df = df[~df.beam_type.isin(NON_PHYSICS_BEAM_TYPES)]
    if first_run is not None and len(df):
        n = df.run.map(run_number)
        df = df[n.notna() & (n >= first_run)]
    return df.sort_values("t_start").reset_index(drop=True)


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


def summarise(run_dir=RUN_DIR, first_run=None, sync=True, rate_first_run=None):
    """Everything the projection and the plots need, in one call.

    Two different questions, two different subsets:

      first_run=None      counts EVERY run we have a record of, on disk or not.
                          That is the right set for "how much have we recorded".

      rate_first_run=N    fits the forward rate on runs >= N only. That is a
                          different question — "what will we record from here" — and
                          the answer must not be diluted by old HV, threshold and
                          latency scans, which ran at deliberately bad settings. The
                          whole history averages 79 events/pulse against production's
                          104, so leaving this unset understates the projection by a
                          quarter.
    """
    df = load_stats(run_dir, first_run=first_run, sync=sync)
    if not len(df):
        return {"subruns": df, "beam": df, "cosmic": df, "rate": {}, "pulses": np.array([])}
    beam = df[~df.is_cosmic].reset_index(drop=True)
    cosmic = df[df.is_cosmic].reset_index(drop=True)
    # Pulse CSVs only exist for the last few weeks and are only needed for the rate
    # fit, which uses recent sub-runs — so window the lookup rather than trying to
    # load a month of beam history for runs whose files are long gone.
    t_hi = df.t_end.max().to_pydatetime()
    t_lo = max(df.t_start.min().to_pydatetime(), t_hi - timedelta(days=PULSE_WINDOW_DAYS))
    pulses = load_beam_pulses(t_lo, t_hi)

    rate_beam = beam
    if rate_first_run is not None and len(beam):
        n = beam.run.map(run_number)
        rate_beam = beam[n.notna() & (n >= rate_first_run)]

    return {
        "subruns": df,
        "beam": beam,
        "cosmic": cosmic,
        "pulses": pulses,
        "rate": measure_rate(rate_beam, pulses, delivery_df=beam),
        "rate_first_run": rate_first_run,
        "rate_runs": sorted(rate_beam.run.unique().tolist()) if len(rate_beam) else [],
        "cosmic_rate": measure_cosmic_rate(cosmic),
        "beam_events": int(beam.events.sum()),
        "cosmic_events": int(cosmic.events.sum()),
    }


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Recorded statistics, ledger-backed.")
    ap.add_argument("--rate-first-run", type=int, default=86)
    ap.add_argument("--no-sync", action="store_true", help="ledger only, skip the disk scan")
    args = ap.parse_args()

    s = summarise(rate_first_run=args.rate_first_run, sync=not args.no_sync)
    beam, cosmic, rate = s["beam"], s["cosmic"], s["rate"]
    print(f"Beam sub-runs   : {len(beam):>4}   events {s['beam_events']:>12,}")
    print(f"Cosmic sub-runs : {len(cosmic):>4}   events {s['cosmic_events']:>12,}")
    if rate:
        print(f"\nForward rate fitted on {', '.join(s['rate_runs'])}")
        print(f"Window {rate['window_start']} -> {rate['window_end']}")
        print(f"  events/pulse       {rate['events_per_pulse']:.1f}")
        print(f"  pulses/hour        {rate['pulses_per_hour']:.0f}")
        print(f"  events/beam hour   {rate['events_per_beam_hour']:,.0f}")
        print(f"  observed events/h  {rate['events_per_hour_observed']:,.0f}"
              f"  (over {rate['n_subruns']} production sub-run(s))")
        print(f"  delivery window    {rate['n_subruns_full_beam']}/"
              f"{rate['n_subruns_delivery']} sub-runs fully beam-on")


if __name__ == "__main__":
    main()
