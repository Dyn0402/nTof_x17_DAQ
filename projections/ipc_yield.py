#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on July 27 2026
Created in PyCharm
Created as nTof_x17_DAQ/projections/ipc_yield.py

@author: Dylan Neff, dylan

Expected IPC arrival vs time since the gamma flash, with the MEASURED run_79 trigger
yield underneath it in fine bins, and the trigger totals each time region is
projected to deliver by the end of the run.

Two panels rather than one overlay: the two curves are ~1e-6 IPC pairs/pulse/ms and
~2 triggers/spill/ms. A twin y-axis would put two unrelated scales on one frame and
invite reading a crossing point that means nothing, so they share only the x axis —
the same layout as analysis/flash_comb/tools/make_ipc_comb_ms.py, which this follows.

IPC expectation
    Geant4 thermal capture campaign, MX17_Full_Geant/docs/report/
    thermal_captures_subkev_full.json — per-decade radiative capture rates from
    `rad_per_pulse_npxbr` (the 7.4e8 (n,p) counts x the measured ng/np branching,
    far better statistics than the handful of direct (n,g) events), times
    ALPHA_IPC pairs per capture. Neutron TOF over the 19.5 m EAR2 flight path maps
    energy to arrival time, t[ms] = 1.41/sqrt(E[eV]). Within a decade the n_TOF flux
    is ~isolethargic, so dN/dlog(t) is flat and dN/dt ~ 1/t.

Measured yield
    run_79, FEU 01 only (every FEU reads every event, so one FEU is the whole event
    list at 1/8 the I/O). Anchored on the GAMMA FLASH ITSELF, tagged by ADC
    saturation, not on "the first event we happened to record": run_79's flash
    capture is ~97%, so in ~3 spills per hundred the first recorded event is the
    ~1 ms gate opening rather than the flash, and anchoring on it would smear the
    whole axis by a millisecond. Spills with no captured flash are dropped.

The extraction is a few minutes, so it is cached. Nothing here changes as data
accumulates — run_79 is a fixed reference measurement.

Usage:
    python ipc_yield.py                 # plot from cache (extracting if absent)
    python ipc_yield.py --refresh       # re-extract from the ROOT files
    python ipc_yield.py --out foo.png
"""

import argparse
import glob
import json
import os
from datetime import datetime

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(HERE, "cache", "run79_tsf.npz")
PLOT_DIR = os.path.join(HERE, "plots")

RUNS_ROOT = "/home/mx17/beam_july/runs"
GEANT_JSON = ("/home/mx17/CLionProjects/MX17_Full_Geant/docs/report/"
              "thermal_captures_subkev_full.json")

RUN = "run_79"
FEU = "01"
TICK_MS = 1e-5
SPILL_GAP_MS = 200.0
SAT = 3500.0            # |amplitude| at/above this counts as a saturated cell
MIN_SAT_CELLS = 40      # the flash lights up many cells at once; ordinary events ~none

BIN_MS = 0.05           # the "very fine" step
TMIN, TMAX = 0.0, 81.0
# Plot from the gate, not from zero: below 1 ms the N93B gate vetoes everything
# (that veto is what kills the flash ringing), so there is nothing to show, and the
# t=0 flash itself is ~20x the physics plateau and would flatten the whole panel.
TPLOT_MIN = 1.0
ALPHA_IPC = 0.0021      # IPC pairs per radiative capture
FLIGHT = 1.41           # t[ms] = FLIGHT / sqrt(E[eV]), 19.5 m EAR2

REGIONS = [(1.0, 3.0), (3.0, 8.0), (8.0, 15.0), (15.0, 25.0), (25.0, 80.0)]


def tof_ms(E_eV):
    return FLIGHT / np.sqrt(E_eV)


# ------------------------------------------------------------------ measured
def _subrun_dirs(run=RUN):
    root = os.path.join(RUNS_ROOT, run)
    return [os.path.join(root, d) for d in sorted(os.listdir(root))
            if os.path.isdir(os.path.join(root, d))]


def extract(run=RUN, feu=FEU, verbose=True):
    """Flash-anchored time-since-flash histogram over every sub-run of `run`."""
    import uproot

    edges = np.arange(TMIN, TMAX + BIN_MS, BIN_MS)
    counts = np.zeros(len(edges) - 1, dtype=np.int64)
    n_spill_tot = n_spill_flash = 0
    n_events = 0
    subruns = []

    for d in _subrun_dirs(run):
        files = sorted(glob.glob(f"{d}/decoded_root/*_{feu}.root"))
        if not files:
            continue
        ts_all, sat_all = [], []
        for f in files:
            try:
                t = uproot.open(f)["nt"]
                ts_all.append(t["timestamp"].array(library="np").astype(np.int64))
                amp = t["amplitude"].array(library="np")
            except Exception:
                continue
            sat_all.append(np.array([int((np.abs(np.asarray(x)) >= SAT).sum())
                                     for x in amp]))
        if not ts_all:
            continue
        ts = np.concatenate(ts_all)
        sat = np.concatenate(sat_all)
        o = np.argsort(ts)
        ts, sat = ts[o], sat[o]
        n_events += ts.size

        brk = np.where(np.diff(ts) * TICK_MS > SPILL_GAP_MS)[0]
        st = np.concatenate([[0], brk + 1])
        en = np.concatenate([brk + 1, [ts.size]])
        n_spill_tot += len(st)

        got = 0
        for s, e in zip(st, en):
            seg_ts, seg_sat = ts[s:e], sat[s:e]
            fi = np.where(seg_sat >= MIN_SAT_CELLS)[0]
            if fi.size == 0:
                continue                      # flash not recorded — drop the spill
            got += 1
            rel = (seg_ts - seg_ts[fi[0]]) * TICK_MS
            counts += np.histogram(rel, bins=edges)[0]
        n_spill_flash += got
        subruns.append(os.path.basename(d))
        if verbose:
            print(f"  {os.path.basename(d):16s} spills {len(st):5d}  "
                  f"flash {100 * got / max(len(st), 1):5.1f}%")

    return {
        "edges": edges, "counts": counts,
        "n_spill_total": n_spill_tot, "n_spill_flash": n_spill_flash,
        "n_events": n_events, "run": run, "feu": feu,
        "subruns": np.array(subruns),
        "created": datetime.now().isoformat(timespec="seconds"),
    }


def save_cache(data, path=CACHE_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    np.savez_compressed(path, **data)
    return path


def load_cache(path=CACHE_PATH):
    if not os.path.exists(path):
        return None
    z = np.load(path, allow_pickle=True)
    return {k: z[k] for k in z.files}


def get_measured(refresh=False):
    if not refresh:
        d = load_cache()
        if d is not None:
            return d
    print(f"Extracting flash-anchored times from {RUN} (FEU {FEU}) — a few minutes...")
    d = extract()
    print(f"Cached -> {save_cache(d)}")
    return d


# ----------------------------------------------------------------- expected
def ipc_density(tgrid, geant_json=GEANT_JSON):
    """IPC pairs per pulse per ms on `tgrid` [ms], and the longest TOF the
    simulation actually covers.

    The density is piecewise per energy decade, so it steps at decade boundaries —
    that is the model, not noise. Beyond `t_cov` the thermal campaign has no flux
    (it stops at 1 meV), so the curve there is absence of simulation, not absence
    of IPC."""
    with open(geant_json) as f:
        J = json.load(f)
    dens = np.zeros_like(tgrid, dtype=float)
    t_cov = 0.0
    for r in J["decades"]:
        ipc = r["rad_per_pulse_npxbr"] * ALPHA_IPC
        t_hi, t_lo = tof_ms(r["E_lo_eV"]), tof_ms(r["E_hi_eV"])   # low E = long TOF
        t_cov = max(t_cov, t_hi)
        if t_hi <= tgrid[0] or t_lo >= tgrid[-1]:
            continue
        norm = np.log(t_hi / t_lo)             # dN/dt ~ 1/t across the full decade
        m = (tgrid >= t_lo) & (tgrid <= t_hi)
        dens[m] += ipc / norm / tgrid[m]
    return dens, t_cov


def latest_projection_total(saved_dir=os.path.join(HERE, "saved")):
    """final_events of the newest frozen projection, or None.

    Read straight from the JSON rather than through live.py: that pulls in
    run_stats and pandas, and this script has to run under the interpreter that
    has uproot, which is a different one. Keeping the dependency sets disjoint is
    what lets each half run where it can."""
    best, best_created = None, ""
    for path in sorted(glob.glob(os.path.join(saved_dir, "projection_*.json"))):
        try:
            with open(path) as f:
                p = json.load(f)
        except Exception:
            continue
        if p.get("created", "") >= best_created:
            best, best_created = p, p.get("created", "")
    return best.get("final_events") if best else None


def region_table(meas, projected_total=None):
    """Per region: triggers/spill, share of the in-gate total, and the triggers the
    projection says that region delivers by the end of the run."""
    edges = meas["edges"]
    counts = meas["counts"].astype(float)
    n_flash = float(meas["n_spill_flash"])
    centres = 0.5 * (edges[:-1] + edges[1:])

    gate = (centres >= REGIONS[0][0]) & (centres <= REGIONS[-1][1])
    total_in_gate = counts[gate].sum()

    rows = []
    for lo, hi in REGIONS:
        m = (centres >= lo) & (centres < hi)
        n = counts[m].sum()
        share = n / total_in_gate if total_in_gate else 0.0
        rows.append({
            "lo": lo, "hi": hi,
            "counts": int(n),
            "per_spill": n / n_flash if n_flash else 0.0,
            "share": share,
            "projected": share * projected_total if projected_total else None,
        })
    return rows, total_in_gate


def main():
    ap = argparse.ArgumentParser(description="IPC expectation vs measured run_79 yield.")
    ap.add_argument("--refresh", action="store_true", help="re-extract from ROOT files")
    ap.add_argument("--out", help="output PNG path")
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args()

    import matplotlib
    if not args.show:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    meas = get_measured(refresh=args.refresh)

    projected_total = latest_projection_total()
    rows, total_in_gate = region_table(meas, projected_total)

    edges = meas["edges"]
    centres = 0.5 * (edges[:-1] + edges[1:])
    n_flash = float(meas["n_spill_flash"])
    per_spill_per_ms = meas["counts"].astype(float) / n_flash / BIN_MS

    tgrid = np.geomspace(TPLOT_MIN, TMAX, 2000)
    dens, t_cov = ipc_density(tgrid)

    # -------------------------------------------------------------- figure
    INK, INK2, MUTED = "#0b0b0b", "#52514e", "#898781"
    GRID, AXIS, SURFACE = "#e1e0d9", "#c3c2b7", "#fcfcfb"
    MEASURED, EXPECTED = "#2a78d6", "#eb6834"

    fig, (axA, axB) = plt.subplots(2, 1, figsize=(12.0, 8.6), sharex=True,
                                   height_ratios=[1.0, 1.25],
                                   gridspec_kw={"hspace": 0.13})
    fig.patch.set_facecolor(SURFACE)

    def style(ax):
        ax.set_facecolor(SURFACE)
        ax.grid(True, color=GRID, linewidth=0.8, alpha=0.9)
        ax.set_axisbelow(True)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        for s in ("left", "bottom"):
            ax.spines[s].set_color(AXIS)
        ax.tick_params(colors=MUTED, labelsize=9, length=3)
        for lbl in ax.get_xticklabels() + ax.get_yticklabels():
            lbl.set_color(INK2)

    # Alternating region bands, drawn on both panels so a region reads as one column.
    for i, (lo, hi) in enumerate(REGIONS):
        for ax in (axA, axB):
            ax.axvspan(lo, hi, color=MUTED, alpha=0.10 if i % 2 == 0 else 0.04,
                       linewidth=0, zorder=0)
        for ax in (axA, axB):
            ax.axvline(lo, color=AXIS, linewidth=0.8, alpha=0.7, zorder=1)
    axA.axvline(REGIONS[-1][1], color=AXIS, linewidth=0.8, alpha=0.7, zorder=1)
    axB.axvline(REGIONS[-1][1], color=AXIS, linewidth=0.8, alpha=0.7, zorder=1)

    # (a) expected IPC
    m_cov = tgrid <= t_cov
    y = dens[m_cov] * 1e6
    # Bound the log axis to the data. A fill anchored at "zero" on a log scale runs
    # to the smallest representable value and would stretch the panel over nine
    # empty decades, so the fill floor and the axis floor are the same number.
    y_lo, y_hi = float(y.min()) * 0.55, float(y.max()) * 1.9
    axA.plot(tgrid[m_cov], y, color=EXPECTED, linewidth=2.2)
    axA.fill_between(tgrid[m_cov], y_lo, y, color=EXPECTED, alpha=0.16, linewidth=0)
    axA.set_ylim(y_lo, y_hi)
    if t_cov < TMAX:
        axA.axvspan(t_cov, TMAX, color=MUTED, alpha=0.16, linewidth=0, zorder=2)
        axA.text(float(np.sqrt(t_cov * TMAX)), 0.5, "not simulated\n(campaign stops at 1 meV)",
                 transform=axA.get_xaxis_transform(), ha="center", va="center",
                 fontsize=8.5, color=MUTED, linespacing=1.4)
    axA.set_ylabel("IPC pairs / pulse / ms  [$\\times10^{-6}$]", color=INK2, fontsize=10)
    # Log-log: the arrival density is ~1/t, which spans a decade and a half across
    # this window. On a linear axis the 1-3 ms band alone sets the scale and every
    # later region reads as identically zero, which is exactly the comparison the
    # region breakdown is meant to make.
    axA.set_yscale("log")
    style(axA)
    # One series per panel, so the panel title names it and there is no legend box.
    axA.set_title("Expected IPC arrival  —  $^{3}$He(n,$\\gamma$) $\\times\\ \\alpha$, "
                  "Geant4 thermal campaign",
                  color=INK2, fontsize=10.5, loc="left", pad=6)

    # (b) measured run_79 yield
    axB.step(centres, per_spill_per_ms, where="mid", color=MEASURED, linewidth=1.1)
    axB.fill_between(centres, 0, per_spill_per_ms, step="mid",
                     color=MEASURED, alpha=0.14, linewidth=0)
    axB.set_ylabel("Triggers / spill / ms", color=INK2, fontsize=10)
    axB.set_xlabel("time since the gamma flash   [ms]", color=INK2, fontsize=10.5)
    # Log x on both panels: it spreads the five regions to roughly even widths, which
    # both un-crowds their labels and stops the 25-80 ms band from occupying two
    # thirds of the frame while contributing a third of the triggers.
    axB.set_xscale("log")
    axB.set_xlim(TPLOT_MIN, TMAX)
    gate = centres >= TPLOT_MIN
    gate_max = per_spill_per_ms[gate].max()
    axB.set_ylim(0, gate_max * 1.42)
    style(axB)
    for ax in (axA, axB):
        ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(
            lambda v, _: f"{v:g}"))
        ax.xaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
    axB.set_xticks([1, 2, 3, 5, 8, 10, 15, 25, 40, 80])

    # Region labels, at the geometric middle of each band so they sit centred once
    # the axis is logarithmic.
    for r in rows:
        mid = float(np.sqrt(r["lo"] * r["hi"]))
        txt = f"{r['lo']:g}–{r['hi']:g} ms\n{r['share'] * 100:.0f}% · {r['per_spill']:.1f}/spill"
        if r["projected"]:
            txt += f"\n→ {r['projected'] / 1e6:.2f}M"
        axB.text(mid, gate_max * 1.38, txt, ha="center", va="top",
                 fontsize=8.5, color=INK2, linespacing=1.4,
                 bbox=dict(boxstyle="round,pad=0.3", facecolor=SURFACE,
                           edgecolor=AXIS, linewidth=0.8, alpha=0.95))

    axB.set_title(f"Recorded run_79 triggers  —  {BIN_MS * 1000:.0f} $\\mu$s bins, "
                  f"flash-anchored",
                  color=INK2, fontsize=10.5, loc="left", pad=6)

    fig.suptitle("Expected IPC arrival vs the measured run_79 trigger yield",
                 color=INK, fontsize=14, fontweight="bold", x=0.125, ha="left", y=0.975)
    sub = (f"run_79 · {int(meas['n_spill_flash']):,} flash-anchored spills of "
           f"{int(meas['n_spill_total']):,} ({100 * n_flash / max(int(meas['n_spill_total']), 1):.0f}% "
           f"flash capture) · FEU {str(meas['feu'])}")
    if projected_total:
        sub += f" · → totals scaled to the {projected_total / 1e6:.1f}M projection"
    axA.text(0, 1.10, sub, transform=axA.transAxes, color=MUTED,
             fontsize=9.5, va="bottom")

    fig.text(0.008, 0.012,
             f"Generated {datetime.now():%Y-%m-%d %H:%M} · IPC from MX17_Full_Geant "
             f"thermal_captures_subkev_full · TOF t[ms]=1.41/√E[eV] over 19.5 m EAR2 · "
             f"flash tagged by ADC saturation, unflashed spills dropped",
             color=MUTED, fontsize=8)

    out = args.out or os.path.join(PLOT_DIR, "ipc_yield.png")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    fig.savefig(out, dpi=150, bbox_inches="tight", facecolor=SURFACE)
    print(f"[ipc_yield] Wrote {out}")

    print(f"\n{'region':>12}  {'triggers':>12}  {'/spill':>8}  {'share':>7}  {'projected':>12}")
    for r in rows:
        pr = f"{r['projected'] / 1e6:.2f}M" if r["projected"] else "—"
        print(f"{r['lo']:5g}-{r['hi']:<5g}  {r['counts']:>12,}  {r['per_spill']:>8.2f}  "
              f"{r['share'] * 100:>6.1f}%  {pr:>12}")

    if args.show:
        plt.show()


if __name__ == "__main__":
    main()
