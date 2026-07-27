#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on July 27 2026
Created in PyCharm
Created as nTof_x17_DAQ/projections/schedule.py

@author: Dylan Neff, dylan

Beam-availability model: turn the schedule in schedule.json (known beam stops,
accesses, and a budgeted allowance for the accesses we don't know about yet) into
"how many hours of beam are available between now and time t".

Everything is local wall-clock time, matching the DAQ's own timestamps.

The efficiency factor lives here conceptually but is applied by the caller, so a
plot can show the same availability curve at more than one efficiency.
"""

import json
import os
from datetime import datetime, timedelta

SCHEDULE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schedule.json")


def _parse(s):
    return datetime.fromisoformat(s)


def load_schedule(path=SCHEDULE_PATH):
    with open(path) as f:
        return json.load(f)


def downtime_intervals(sched):
    """All downtime as a merged, sorted list of (start, end) datetimes — the
    explicit entries plus the generated daily allowance. Merging matters: a
    generated daily access that overlaps an explicit one must not be subtracted
    twice, which would invent beam time that was never lost."""
    end = _parse(sched["run_end"])
    intervals = [(_parse(d["start"]), _parse(d["end"])) for d in sched.get("downtimes", [])]

    gen = sched.get("generic_daily_downtime") or {}
    if gen.get("hours_per_day"):
        day = _parse(gen["from_date"]).date() if "from_date" in gen else None
        if day is not None:
            hours = float(gen["hours_per_day"])
            start_hour = int(gen.get("start_hour", 10))
            while day <= end.date():
                t0 = datetime.combine(day, datetime.min.time()).replace(hour=start_hour)
                intervals.append((t0, t0 + timedelta(hours=hours)))
                day += timedelta(days=1)

    intervals.sort()
    merged = []
    for a, b in intervals:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def downtime_hours(intervals, t0, t1):
    """Hours of scheduled downtime inside [t0, t1)."""
    total = 0.0
    for a, b in intervals:
        lo, hi = max(a, t0), min(b, t1)
        if hi > lo:
            total += (hi - lo).total_seconds() / 3600.0
    return total


def beam_hours(sched, t0, t1, intervals=None):
    """Hours of beam-available time in [t0, t1), i.e. elapsed minus downtime."""
    if t1 <= t0:
        return 0.0
    if intervals is None:
        intervals = downtime_intervals(sched)
    elapsed = (t1 - t0).total_seconds() / 3600.0
    return max(0.0, elapsed - downtime_hours(intervals, t0, t1))


def availability_curve(sched, step_minutes=15, start=None, end=None):
    """[(t, cumulative_beam_hours_since_start), ...] on a regular grid.

    The grid is deliberately fine (15 min) so the flat shelves during accesses land
    on the right minute rather than being smeared across an hourly sample."""
    t0 = start or _parse(sched["projection_start"])
    t1 = end or _parse(sched["run_end"])
    intervals = downtime_intervals(sched)
    step = timedelta(minutes=step_minutes)

    out, t, cum = [], t0, 0.0
    prev = t0
    while t <= t1:
        cum += beam_hours(sched, prev, t, intervals)
        out.append((t, cum))
        prev, t = t, t + step
    if out and out[-1][0] < t1:
        cum += beam_hours(sched, prev, t1, intervals)
        out.append((t1, cum))
    return out


def availability_and_downtime_curve(sched, step_minutes=15, start=None, end=None):
    """[(t, cumulative_beam_hours, cumulative_downtime_hours), ...].

    Both halves on one grid: beam hours drive the trigger projection, downtime hours
    drive the cosmic projection, and they must be sampled identically or the two
    curves would disagree about when a beam stop started."""
    t0 = start or _parse(sched["projection_start"])
    t1 = end or _parse(sched["run_end"])
    intervals = downtime_intervals(sched)
    step = timedelta(minutes=step_minutes)

    out, t, up, down = [], t0, 0.0, 0.0
    prev = t0
    while t <= t1:
        d = downtime_hours(intervals, prev, t)
        up += max(0.0, (t - prev).total_seconds() / 3600.0 - d)
        down += d
        out.append((t, up, down))
        prev, t = t, t + step
    if out and out[-1][0] < t1:
        d = downtime_hours(intervals, prev, t1)
        up += max(0.0, (t1 - prev).total_seconds() / 3600.0 - d)
        down += d
        out.append((t1, up, down))
    return out


def is_down(sched, when, intervals=None):
    """True if `when` falls inside a scheduled beam-off block."""
    if intervals is None:
        intervals = downtime_intervals(sched)
    return any(a <= when < b for a, b in intervals)


def summary(sched):
    t0, t1 = _parse(sched["projection_start"]), _parse(sched["run_end"])
    intervals = downtime_intervals(sched)
    elapsed = (t1 - t0).total_seconds() / 3600.0
    down = downtime_hours(intervals, t0, t1)
    eff = float(sched.get("efficiency", 1.0))
    return {
        "start": t0, "end": t1,
        "elapsed_hours": elapsed,
        "downtime_hours": down,
        "beam_hours": elapsed - down,
        "efficiency": eff,
        "effective_hours": (elapsed - down) * eff,
        "n_downtime_blocks": len(intervals),
    }


def main():
    sched = load_schedule()
    s = summary(sched)
    print(f"{s['start']:%a %d %b %H:%M}  ->  {s['end']:%a %d %b %H:%M}")
    print(f"  elapsed          {s['elapsed_hours']:8.1f} h")
    print(f"  scheduled down   {s['downtime_hours']:8.1f} h  ({s['n_downtime_blocks']} blocks)")
    print(f"  beam available   {s['beam_hours']:8.1f} h")
    print(f"  x {s['efficiency']:.0%} efficiency = {s['effective_hours']:8.1f} h of data taking")
    print("\nDowntime blocks:")
    for a, b in downtime_intervals(sched):
        print(f"  {a:%a %d %b %H:%M} -> {b:%H:%M}   {(b - a).total_seconds() / 3600:4.1f} h")


if __name__ == "__main__":
    main()
