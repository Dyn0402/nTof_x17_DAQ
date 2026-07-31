#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analyze_gap_band.py — how long can a pulse gap be with the beam still running?

This is the measurement behind `rule_beam_off`'s `early_gap_seconds` (the data-axis gap
at which the Telegram monitor calls the beam down) and behind
`beam_intensity_controller.BEAM_OFF_GAP_S`. Its sibling
`analyze_stop_durations.py` answers the other half — how long stops LAST, which is what
`mode_watcher.BEAM_DOWN_MIN` is set from. Re-run both after a machine schedule change;
the answer is a property of the accelerator, not of our DAQ.

    .venv/bin/python beam_monitor/analyze_gap_band.py [--since 2026-07-01]

METHOD, and the traps in it
  1. A "pulse" is a logged point above PULSE_THRESHOLD_E10 (50). Near-zero points keep
     being logged every ~1.2-2.4 s through BOTH structural gaps and real stops, so a low
     reading does not mean beam off — only the gap between PULSES carries it.
  2. ⚠ Do NOT classify a gap as "healthy" merely because the beam is running on both
     sides of it: that is also true across an 8 h stop. The useful statistic is the shape
     of the SHORT-GAP distribution, which is quantised by the supercycle and dies out on
     its own; the threshold belongs just above where it dies out.
  3. A gap in the pulse series can also mean OUR LOGGER DIED. Gaps not covered by CSV rows
     throughout are excluded, exactly as in analyze_stop_durations.py.

Result 2026-07-31 (731 h, 587 k pulses, 2026-07-01..31): the structural population runs
out at 44.4 s (4 gaps in 41-46 s, then nothing until 48.4 s). Gaps crossing 55 s that
recover inside 3 min: 1.94/day, against 1.54/day already crossing the 80 s boundary the
wall-clock rule effectively used — so the early detector costs ~0.4 extra self-clearing
alerts a day and buys ~40 s of warning.
"""
import argparse
import csv
import glob
import os
from bisect import bisect_left, bisect_right
from datetime import datetime

CSV_GLOB = '/home/mx17/beam_july/slow_control/beam_intensity/beam_intensity_*.csv'
PULSE_THRESHOLD_E10 = 50.0
LOGGER_GAP_S = 60.0        # row-to-row hole above this = our logger died, not the beam
RECOVERED_S = 180.0        # back inside 3 min = the alert would have self-cleared


def load(csv_glob, since):
    """-> (all point timestamps, pulse timestamps), both sorted."""
    pts, pulses = [], []
    for fn in sorted(glob.glob(csv_glob)):
        day = os.path.basename(fn)[len('beam_intensity_'):-len('.csv')]
        if since and day < since:
            continue
        with open(fn) as f:
            for row in csv.DictReader(f):
                try:
                    t, v = float(row['unix_ts']), float(row['intensity_e10'])
                except (TypeError, ValueError, KeyError):
                    continue
                pts.append(t)
                if v >= PULSE_THRESHOLD_E10:
                    pulses.append(t)
    pts.sort()
    pulses.sort()
    return pts, pulses


def covered(pts, a, b):
    """True if CSV rows are present throughout [a, b] (trap 3)."""
    i, j = bisect_left(pts, a), bisect_right(pts, b)
    seg = pts[max(0, i - 1):j + 1]
    return all(y - x <= LOGGER_GAP_S for x, y in zip(seg, seg[1:]))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--since', default=None, help='first CSV day to use, e.g. 2026-07-01')
    ap.add_argument('--csv-glob', default=CSV_GLOB)
    args = ap.parse_args()

    pts, pulses = load(args.csv_glob, args.since)
    if len(pulses) < 2:
        raise SystemExit('not enough pulses — check --since / --csv-glob')
    days = (pts[-1] - pts[0]) / 86400.0
    print(f'{datetime.fromtimestamp(pts[0]):%Y-%m-%d} .. {datetime.fromtimestamp(pts[-1]):%Y-%m-%d}'
          f'  ({days:.1f} d, {len(pts)} points, {len(pulses)} pulses)\n')

    gaps = [(a, b - a) for a, b in zip(pulses, pulses[1:])
            if b - a >= 10 and covered(pts, a, b)]

    print('short-gap structure — 2.4 s bins (the supercycle quantisation):')
    lo = 10.0
    while lo < 130:
        n = sum(1 for _, g in gaps if lo <= g < lo + 2.4)
        if n:
            print(f'  {lo:6.1f}-{lo + 2.4:6.1f}s {n:6d} {"#" * min(n, 50)}')
        lo += 2.4

    print('\nlongest gaps below 2 min, with their local context '
          '(pulses in the 5 min before / after):')

    def n_pulses(a, b):
        return bisect_right(pulses, b) - bisect_left(pulses, a)

    for a, g in sorted((x for x in gaps if 35 <= x[1] <= 120), key=lambda x: x[1])[-20:]:
        print(f'  {datetime.fromtimestamp(a):%Y-%m-%d %H:%M:%S}  {g:6.1f}s   '
              f'{n_pulses(a - 300, a):3d} before / {n_pulses(a + g, a + g + 300):3d} after')

    print(f'\ncost of a candidate threshold ("spurious" = beam back inside '
          f'{RECOVERED_S:.0f} s, so the alert self-clears):')
    print(f'  {"thr":>5s} {"spurious":>9s} {"per day":>8s} {"real stops":>11s}')
    for t in (45, 50, 55, 60, 65, 70, 75, 80, 86, 90):
        crossing = [g for _, g in gaps if g >= t]
        spurious = [g for g in crossing if g < RECOVERED_S]
        print(f'  {t:5d} {len(spurious):9d} {len(spurious) / days:8.2f} '
              f'{len(crossing) - len(spurious):11d}')
    print('\n⚠ Set early_gap_seconds above where the binned structure dies out, not at the '
          'cheapest number in the table.')


if __name__ == '__main__':
    main()
