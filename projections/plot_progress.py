#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on July 27 2026
Created in PyCharm
Created as nTof_x17_DAQ/projections/plot_progress.py

@author: Dylan Neff, dylan

Plot cumulative beam triggers against every frozen projection in saved/, plus a
small cosmics counter underneath.

Each projection is drawn from its own creation date onward, so a later projection
that starts from a higher anchor visibly picks up where reality actually got to —
which is the comparison worth looking at.

Usage:
    python plot_progress.py                 # writes plots/progress_<today>.png
    python plot_progress.py --show          # and opens a window
    python plot_progress.py --out foo.png
"""

import argparse
import glob
import json
import os
from datetime import datetime

import matplotlib
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

import run_stats
import schedule as sched_mod

HERE = os.path.dirname(os.path.abspath(__file__))
SAVED_DIR = os.path.join(HERE, "saved")
PLOT_DIR = os.path.join(HERE, "plots")

# dataviz reference palette, light surface.
INK, INK2, MUTED = "#0b0b0b", "#52514e", "#898781"
GRID, AXIS = "#e1e0d9", "#c3c2b7"
SURFACE = "#fcfcfb"
ACTUAL = "#2a78d6"       # categorical slot 1 (blue) — beam
PROJECTION = "#5598e7"   # light blue — projection line, distinct from ACTUAL's beam blue
COSMIC = "#eb6834"       # categorical slot 2 (orange) — cosmics, everywhere
ACTUAL_DOWN = "#e34948"  # categorical slot 8 (red) — measured beam-off, kept off
                         # the blues/orange already claimed by data lines above
MILLION = 1e6


def load_projections(saved_dir=SAVED_DIR):
    out = []
    for path in sorted(glob.glob(os.path.join(saved_dir, "projection_*.json"))):
        try:
            with open(path) as f:
                p = json.load(f)
            p["_name"] = os.path.basename(path)[len("projection_"):-len(".json")]
            out.append(p)
        except Exception as e:
            print(f"[plot] Skipping {path}: {e}")
    return out


def _style(ax):
    ax.set_facecolor(SURFACE)
    # y only: day-spaced vertical gridlines read as beam stoppages at a glance,
    # which is exactly the confusion the downtime shading below is trying to
    # avoid. Lighter than the original weight so they stay recessive under the
    # scheduled/actual bands rather than competing with them.
    ax.grid(True, axis="y", color=GRID, linewidth=0.8, alpha=0.45)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(AXIS)
        ax.spines[side].set_linewidth(1.0)
    ax.tick_params(colors=MUTED, labelsize=9, length=3)
    for lbl in ax.get_xticklabels() + ax.get_yticklabels():
        lbl.set_color(INK2)


# Scheduled gets the top sliver, actual (the one worth seeing clearly) gets the
# larger bottom band. A deliberate overlap band (0.62-0.70) lets a stop that
# happened on schedule show its own blended tint, while a stop that wasn't on the
# schedule (or a scheduled one the beam skipped) still reads as "only one band
# present" outside that strip.
SCHED_YSPAN = (0.62, 1.0)
ACTUAL_YSPAN = (0.0, 0.70)


def shade_downtime(ax, sched, t_lo, t_hi, label=True, now=None, yspan=SCHED_YSPAN):
    """Grey out scheduled beam-off periods so flat stretches in the curves are
    visibly explained rather than looking like a DAQ fault.

    Split into the top sliver (yspan) only where it can be checked against
    actual data, i.e. before `now`; the future — where there's no actual band to
    share the space with — gets the full height instead, so an upcoming access
    doesn't read as a thin, easy-to-miss strip."""
    first = True
    for a, b in sched_mod.downtime_intervals(sched):
        a, b = max(a, t_lo), min(b, t_hi)
        if b <= a:
            continue
        past_end = min(b, now) if now is not None else b
        segments = []
        if past_end > a:
            segments.append((a, past_end, yspan))
        if now is None or b > now:
            segments.append((max(a, now) if now is not None else a, b, (0.0, 1.0)))
        for seg_a, seg_b, ys in segments:
            if seg_b <= seg_a:
                continue
            ax.axvspan(seg_a, seg_b, ymin=ys[0], ymax=ys[1],
                       color=MUTED, alpha=0.22, linewidth=0,
                       label="Scheduled beam off / access" if (first and label) else None)
            first = False


def shade_actual_downtime(ax, intervals, t_lo, t_hi, label=True, yspan=ACTUAL_YSPAN):
    """Red-tint MEASURED beam-off periods (from the beam-intensity pulse record),
    stacked below the grey scheduled band rather than overlaid on it, so the two
    are readable as two separate strips instead of a blended tint."""
    first = True
    for a, b in intervals:
        if b <= t_lo or a >= t_hi:
            continue
        ax.axvspan(max(a, t_lo), min(b, t_hi), ymin=yspan[0], ymax=yspan[1],
                   color=ACTUAL_DOWN, alpha=0.24, linewidth=0,
                   label="Actual beam off (measured)" if (first and label) else None)
        first = False


def main():
    ap = argparse.ArgumentParser(description="Plot statistics progress vs projections.")
    ap.add_argument("--out", help="output PNG path")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--first-run", type=int, default=None)
    args = ap.parse_args()

    if not args.show:
        matplotlib.use("Agg")

    sched = sched_mod.load_schedule()
    stats = run_stats.summarise(first_run=args.first_run,
                                rate_first_run=sched.get("rate_first_run"))
    beam, cosmic = stats["beam"], stats["cosmic"]
    if not len(beam):
        raise SystemExit("No completed beam sub-runs to plot.")

    projections = load_projections()
    t_end = sched_mod._parse(sched["run_end"])
    t_lo = beam.t_start.min().to_pydatetime()
    now = datetime.now()
    # Measured stops only exist up to now, never into the projected future.
    actual_downtime = run_stats.load_actual_downtime(t_lo, min(t_end, now))

    fig, (ax, axr, axc) = plt.subplots(
        3, 1, figsize=(11.5, 10.4), height_ratios=[3.0, 1.5, 1.2], sharex=True,
        gridspec_kw={"hspace": 0.16})
    fig.patch.set_facecolor(SURFACE)

    shade_downtime(ax, sched, t_lo, t_end, now=now)
    shade_actual_downtime(ax, actual_downtime, t_lo, t_end)

    # --- projections, oldest first so the newest draws on top ---
    for i, p in enumerate(projections):
        newest = (i == len(projections) - 1)
        ts = [datetime.fromisoformat(pt["t"]) for pt in p["points"]]
        ev = [pt["events"] / MILLION for pt in p["points"]]
        ax.plot(ts, ev, color=PROJECTION, linewidth=2.0 if newest else 1.4,
                linestyle="--", alpha=1.0 if newest else 0.45,
                label=f"Projection {p['_name']} ({p['efficiency']:.0%} eff.)")
        if newest:
            hi = [pt["events_at_100pct"] / MILLION for pt in p["points"]]
            ax.fill_between(ts, ev, hi, color=PROJECTION, alpha=0.09, linewidth=0)
            ax.plot(ts, hi, color=PROJECTION, linewidth=1.0, alpha=0.35,
                    linestyle=":", label="Same rate at 100% efficiency")
            ax.annotate(f"{p['final_events'] / MILLION:.1f}M",
                        xy=(ts[-1], ev[-1]), xytext=(-4, 6), textcoords="offset points",
                        ha="right", color=PROJECTION, fontsize=11, fontweight="bold")

    # --- actual ---
    bt, bc = run_stats.cumulative(beam)
    ax.plot(bt, [c / MILLION for c in bc], color=ACTUAL, linewidth=2.2,
            label="Recorded (beam runs)")
    ax.plot([bt[-1]], [bc[-1] / MILLION], marker="o", markersize=6, color=ACTUAL,
            markeredgecolor=SURFACE, markeredgewidth=1.5, zorder=5)
    # Up-left of the endpoint: the projection leaves to the upper right and the
    # recorded curve arrives from the lower left, so that corner is the free one.
    ax.annotate(f"{bc[-1] / MILLION:.2f}M", xy=(bt[-1], bc[-1] / MILLION),
                xytext=(-8, 9), textcoords="offset points", ha="right",
                color=ACTUAL, fontsize=11, fontweight="bold")

    rate = stats["rate"]
    ax.set_title("nTOF x17 — cumulative beam triggers vs projection",
                 color=INK, fontsize=14, fontweight="bold", loc="left", pad=32)
    counted = f"run_{args.first_run}+" if args.first_run else "all recorded runs"
    fitted = ", ".join(stats.get("rate_runs") or []) or "?"
    ax.text(0, 1.012,
            f"counting {counted} · forward rate from {fitted}: "
            f"{rate['events_per_pulse']:.0f} events/pulse × "
            f"{rate['pulses_per_hour']:.0f} pulses/h = "
            f"{rate['events_per_beam_hour'] / 1000:.0f}k per beam hour",
            transform=ax.transAxes, color=MUTED, fontsize=9.5, va="bottom")
    ax.set_ylabel("Cumulative triggers (millions)", color=INK2, fontsize=10)
    ax.set_xlim(t_lo, t_end)
    ax.set_ylim(bottom=0)
    _style(ax)

    # Recorded data leads the legend; it is the series the eye should find first.
    handles, labels = ax.get_legend_handles_labels()
    order = sorted(range(len(labels)), key=lambda i: not labels[i].startswith("Recorded"))
    leg = ax.legend([handles[i] for i in order], [labels[i] for i in order],
                    loc="upper left", frameon=False, fontsize=9.5)
    for t in leg.get_texts():
        t.set_color(INK2)

    # --- expected vs actual instantaneous rate ---
    shade_downtime(axr, sched, t_lo, t_end, label=False, now=now)
    shade_actual_downtime(axr, actual_downtime, t_lo, t_end, label=False)
    newest = projections[-1] if projections else None
    if newest:
        ts = [datetime.fromisoformat(pt["t"]) for pt in newest["points"]]
        axr.step(ts, [pt.get("rate_hz", 0) for pt in newest["points"]], where="post",
                 color=ACTUAL, linewidth=1.8, linestyle="--",
                 label="Expected beam rate")
        # During a scheduled stop the beam rate is zero and we take cosmics instead —
        # the same assumption that feeds the projected cosmic total below.
        cos_hz = (newest.get("cosmic_rate") or {}).get("hz")
        if cos_hz:
            axr.step(ts, [0.0 if pt.get("rate_hz", 0) else cos_hz for pt in newest["points"]],
                     where="post", color=COSMIC, linewidth=1.8, linestyle="--",
                     label="Expected cosmic rate")

    # Actual rate as one flat bar per sub-run: it is an average over that sub-run,
    # not an instantaneous sample, and drawing it as a point would imply otherwise.
    for df, colour, lab in ((beam, ACTUAL, "Recorded beam sub-runs"),
                            (cosmic, COSMIC, "Recorded cosmic sub-runs")):
        first = True
        for _, r in df.iterrows():
            if r.hours <= 0:
                continue
            axr.hlines(r.events / (r.hours * 3600.0), r.t_start, r.t_end,
                       color=colour, linewidth=2.6,
                       label=lab if first else None, zorder=4)
            first = False

    axr.set_ylabel("Trigger rate (Hz)", color=INK2, fontsize=10)
    # Headroom above the flat expected line so the legend has somewhere to sit that
    # is not on top of the data.
    top = max([r.events / (r.hours * 3600.0)
               for df in (beam, cosmic) for _, r in df.iterrows() if r.hours > 0]
              + [newest["expected_rate_hz"] if newest else 0] + [1])
    axr.set_ylim(0, top * 1.5)
    _style(axr)
    legr = axr.legend(loc="upper right", frameon=False, fontsize=8.5, ncols=2)
    for t in legr.get_texts():
        t.set_color(INK2)

    # --- the little cosmics counter ---
    shade_downtime(axc, sched, t_lo, t_end, label=False, now=now)
    shade_actual_downtime(axc, actual_downtime, t_lo, t_end, label=False)
    if newest and newest.get("final_cosmics"):
        ts = [datetime.fromisoformat(pt["t"]) for pt in newest["points"]]
        axc.plot(ts, [pt.get("cosmics", 0) / MILLION for pt in newest["points"]],
                 color=COSMIC, linewidth=1.6, linestyle="--", alpha=0.9,
                 label="Projected (accumulates during beam-off)")
        axc.annotate(f"{newest['final_cosmics'] / MILLION:.2f}M",
                     xy=(ts[-1], newest["final_cosmics"] / MILLION),
                     xytext=(-4, 5), textcoords="offset points", ha="right",
                     color=COSMIC, fontsize=10, fontweight="bold")
    if len(cosmic):
        ct, cc = run_stats.cumulative(cosmic)
        axc.plot(ct, [c / MILLION for c in cc], color=COSMIC, linewidth=2.0,
                 label="Recorded cosmics")
        axc.plot([ct[-1]], [cc[-1] / MILLION], marker="o", markersize=5, color=COSMIC,
                 markeredgecolor=SURFACE, markeredgewidth=1.5, zorder=5)
        # Up-left: the projected curve climbs away to the upper right from here.
        axc.annotate(f"{cc[-1] / MILLION:.2f}M", xy=(ct[-1], cc[-1] / MILLION),
                     xytext=(-7, 7), textcoords="offset points", ha="right",
                     color=COSMIC, fontsize=10, fontweight="bold")
    else:
        axc.text(0.5, 0.5, "no cosmic runs yet", transform=axc.transAxes,
                 ha="center", va="center", color=MUTED, fontsize=10)
    axc.set_ylabel("Cosmics (millions)", color=INK2, fontsize=10)
    legc = axc.legend(loc="upper left", frameon=False, fontsize=8.5)
    for t in legc.get_texts():
        t.set_color(INK2)
    axc.set_ylim(bottom=0)
    _style(axc)
    axc.xaxis.set_major_locator(mdates.DayLocator(interval=2))
    axc.xaxis.set_major_formatter(mdates.DateFormatter("%a\n%d %b"))
    axc.xaxis.set_minor_locator(mdates.DayLocator())

    fig.text(0.008, 0.012,
             f"Generated {datetime.now():%Y-%m-%d %H:%M} · beam/cosmic split from "
             f"run_config.json beam_type · projections frozen, never recomputed",
             color=MUTED, fontsize=8)

    out = args.out or os.path.join(PLOT_DIR, f"progress_{datetime.now():%Y-%m-%d}.png")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    fig.savefig(out, dpi=150, bbox_inches="tight", facecolor=SURFACE)
    print(f"[plot] Wrote {out}")
    if args.show:
        plt.show()


if __name__ == "__main__":
    main()
