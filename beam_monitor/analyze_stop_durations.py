#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analyze_stop_durations.py — how long do n_TOF beam stops last?

This is the measurement behind `mode_watcher.BEAM_DOWN_MIN` (how long the beam must be
away before we give up on it and swap the DAQ to cosmics). Re-run it after a machine
schedule change; the answer is a property of the accelerator, not of our DAQ.

    .venv/bin/python beam_monitor/analyze_stop_durations.py

METHOD, and the two traps in it
  1. A "pulse" is a logged point above `pulse_threshold_e10` (50). Near-zero points are
     logged continuously — every ~1.2-2.4 s — both inside structural gaps and during real
     stops, so a low reading does NOT mean beam off. Only the gap between PULSES does.
  2. ⚠ A gap in the pulse series can also mean OUR LOGGER DIED, which is not beam
     behaviour and would inflate every statistic here. So a gap only counts as a beam stop
     if the CSV kept receiving rows all the way through it (no row-to-row gap above
     LOGGER_GAP_S). On the 27 d sample this rejected 3 of 133 candidates — including one
     of 9.8 h, which alone would have skewed the tail badly.

  STOP_S = 80 s is the beam-off boundary from `beam-off-threshold-empty-band`: the pulse
  gap distribution is bimodal with an empty band, longest gap with beam demonstrably
  running 38.4 s, shortest genuine stop 86.4 s. Nothing in this analysis is sensitive to
  where in 45-75 s the cut sits.

RESULT on 2026-06-30..07-27 (642 h, 1.11 M rows, 511 k pulses, 130 real stops)
  * stops are 4.9/day and cost 115.5 h = 18.0% of wall-clock
  * the SHORT-HICCUP population dies out sharply: 23 stops in 1-2 min, 18 in 2-3, 13 in
    3-4, then only 4 in 4-5 and 2 in 5-6. Past ~4 min the distribution goes flat and long.
  * conditional survival is the decision quantity, and it has a knee in the same place:
    given the beam has been away T minutes, P(it stays away 15 more) is 51% at T=2, 70% at
    T=4, 72% at T=5 — and then PLATEAUS (72% at T=7, 72% at T=8, 79% at T=10).
  => 5 min sits just past the end of the short-hiccup population and at the knee of the
     survival curve. Below ~4 min you are switching on hiccups that would have fixed
     themselves; above ~10 min you are buying very little discrimination for real dead time.
"""
import glob
import os

import numpy as np
import pandas as pd

CSV_GLOB = os.path.expanduser(
    '~/beam_july/slow_control/beam_intensity/beam_intensity_*.csv')
PULSE_E10 = 50.0       # pulse_threshold_e10, same value beam_watcher publishes
STOP_S = 80.0          # BEAM_OFF_GAP_S — inside the empty 45-75 s band
LOGGER_GAP_S = 120.0   # no ROWS at all for this long => logger down, not a beam stop
SUBRUN_MIN = 15.0      # cosmic sub-run length used by switch_mode.py
RETURN_COST_S = 70.0   # beam lost at the return changeover (detect ~8 s + --go ~60 s)


def load_stops(csv_glob=CSV_GLOB):
    """-> (stop durations in minutes, span_hours, n_rejected)."""
    files = sorted(glob.glob(csv_glob))
    if not files:
        raise SystemExit(f'no CSVs matched {csv_glob}')
    df = pd.concat([pd.read_csv(f, usecols=['unix_ts', 'intensity_e10']) for f in files],
                   ignore_index=True)
    df = df.sort_values('unix_ts').drop_duplicates(subset='unix_ts').reset_index(drop=True)
    t_all = df['unix_ts'].to_numpy()
    pulses = t_all[df['intensity_e10'].to_numpy() > PULSE_E10]
    gaps = np.diff(pulses)
    starts, ends = pulses[:-1], pulses[1:]

    real, rejected = [], 0
    for i in np.where(gaps > STOP_S)[0]:
        a, b = starts[i], ends[i]
        rows = t_all[(t_all > a) & (t_all < b)]
        if len(rows) < 2:
            rejected += 1
            continue
        if np.max(np.diff(np.concatenate(([a], rows, [b])))) < LOGGER_GAP_S:
            real.append(gaps[i])
        else:
            rejected += 1
    return np.sort(np.array(real)) / 60.0, (t_all[-1] - t_all[0]) / 3600.0, rejected, len(files)


def main():
    stops, span_h, rejected, nfiles = load_stops()
    print(f'{nfiles} CSVs | {span_h:.0f} h | {len(stops)} real stops '
          f'({rejected} rejected as logger-down) | {len(stops) / span_h * 24:.1f}/day')
    print(f'downtime {stops.sum() / 60:.1f} h = {stops.sum() / 60 / span_h * 100:.1f}% '
          f'of wall-clock ({stops.sum() / 60 / (span_h / 24):.1f} h/day)')

    print('\n=== duration percentiles (min) ===')
    print('  ' + '  '.join(f'p{q}={np.percentile(stops, q):.1f}' for q in (10, 25, 50, 75, 90, 95)))
    print(f'  min={stops.min():.1f}  max={stops.max():.1f}  mean={stops.mean():.1f}')

    print('\n=== where the short-hiccup population ends (1 min bins) ===')
    h, _ = np.histogram(stops, bins=np.arange(0, 13))
    for i, c in enumerate(h):
        print(f'  {i:2d}-{i + 1:<2d} min {c:3d} {"#" * c}')

    print('\n=== conditional survival: it has been down T min, what now? ===')
    print(f'  {"T":>4} {"n":>5} {"P(+15min)":>10} {"median rest":>12} {"mean rest":>10}')
    for T in (2, 3, 4, 5, 6, 7, 8, 10, 15, 20, 30):
        sel = stops[stops > T]
        if len(sel) < 4:
            continue
        rest = sel - T
        print(f'  {T:>4} {len(sel):>5} {(rest > 15).mean() * 100:>9.0f}% '
              f'{np.median(rest):>11.1f}m {rest.mean():>9.1f}m')

    print(f'\n=== trade-off (cosmic sub-run {SUBRUN_MIN:g} min, '
          f'return costs {RETURN_COST_S:g} s of beam) ===')
    print(f'  {"T":>4} {"switch":>7} {"/day":>6} {"cosmic h":>9} {"beam lost h":>12} '
          f'{">=1 full sub-run":>17}')
    for T in (2, 3, 4, 5, 7, 10, 15, 20, 30):
        sel = stops[stops > T]
        rest = sel - T
        print(f'  {T:>4} {len(sel):>7} {len(sel) / span_h * 24:>6.1f} '
              f'{rest.sum() / 60:>9.1f} {len(sel) * RETURN_COST_S / 3600:>12.2f} '
              f'{(rest >= SUBRUN_MIN).mean() * 100:>16.0f}%')

    print('\n=== sub-run length is the other lever (fraction yielding >=1 COMPLETE sub-run) ===')
    print(f'  {"T":>4}' + ''.join(f'{L:>8.0f}m' for L in (5, 10, 15, 20)))
    for T in (3, 5, 7, 10):
        sel = stops[stops > T]
        rest = sel - T
        print(f'  {T:>4}' + ''.join(f'{(rest >= L).mean() * 100:>8.0f}%' for L in (5, 10, 15, 20)))


if __name__ == '__main__':
    main()
