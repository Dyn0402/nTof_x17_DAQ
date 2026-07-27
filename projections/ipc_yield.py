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
# The reweighted IN-GATE IPC production spectrum — the same input
# analysis/flash_comb/tools/ipc_spectrum_vs_runs.py plots, and the one to use.
# It is the sub-keV thermal campaign reweighted by ENDF/B-VIII.0 sigma_ng/sigma_np,
# 4.1e8 effective counts, so it resolves the thermal peak at 5.3 ms. (An earlier
# draft of this script integrated the raw six-decade table by hand, which gives a
# monotonic 1/t staircase and loses the peak entirely — don't.)
IPC_NPZ = ("/home/mx17/CLionProjects/MX17_Full_Geant/analysis/reweight/"
           "ipc_ingate_spectrum.npz")

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
# Look for the thermal peak above this; below it the epithermal shoulder at the
# 1 ms gate edge is higher and would be mislabelled as the peak.
THERMAL_SEARCH_MS = 3.5


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
def ipc_spectrum(npz_path=IPC_NPZ):
    """The in-gate IPC arrival spectrum, as published.

    Returns (t_ms, dNdt [IPC pairs/pulse/ms], meta). Taken verbatim — no
    re-derivation — so this plot and the flash_comb ones cannot drift apart. The
    grid runs 1.0 to 31.6 ms, i.e. the gate at 1.99 eV down to 2 meV; there is no
    IPC expectation past that, which matters for the 25-80 ms region."""
    Z = np.load(npz_path, allow_pickle=True)
    meta = {
        "ipc_per_pulse": float(Z["ipc_per_pulse_ingate"]),
        "ipc_per_day": float(Z["ipc_per_day_ingate"]),
        "alpha_ipc": float(Z["alpha_ipc"]),
        "pulses_per_day": float(Z["pulses_per_day"]),
        "flight_ms": float(Z["flight_ms"]),
        "gate_ms": float(Z["gate_ms"]),
    }
    return Z["t_ms"], Z["dNdt_ipc_per_pulse_per_ms"], meta


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


def region_table(meas, projected_total=None, ipc_t=None, ipc_d=None):
    """Per region: triggers/spill, share of the in-gate total, the triggers the
    projection says that region delivers by the end of the run, and the region's
    share of the expected IPC."""
    edges = meas["edges"]
    counts = meas["counts"].astype(float)
    n_flash = float(meas["n_spill_flash"])
    centres = 0.5 * (edges[:-1] + edges[1:])

    gate = (centres >= REGIONS[0][0]) & (centres <= REGIONS[-1][1])
    total_in_gate = counts[gate].sum()

    trapz = getattr(np, "trapezoid", np.trapz)
    ipc_total = trapz(ipc_d, ipc_t) if ipc_t is not None else None

    rows = []
    for lo, hi in REGIONS:
        m = (centres >= lo) & (centres < hi)
        n = counts[m].sum()
        share = n / total_in_gate if total_in_gate else 0.0

        ipc_share = ipc_partial = None
        if ipc_t is not None and ipc_total:
            mi = (ipc_t >= lo) & (ipc_t <= hi)
            ipc_share = float(trapz(ipc_d[mi], ipc_t[mi]) / ipc_total) if mi.sum() > 1 else 0.0
            # Flag regions the spectrum only partly covers, so a small IPC share
            # there is not mistaken for a measurement.
            ipc_partial = hi > float(ipc_t.max())

        rows.append({
            "lo": lo, "hi": hi,
            "counts": int(n),
            "per_spill": n / n_flash if n_flash else 0.0,
            "share": share,
            "projected": share * projected_total if projected_total else None,
            "ipc_share": ipc_share,
            "ipc_partial": ipc_partial,
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

    ipc_t, ipc_d, ipc_meta = ipc_spectrum()
    t_cov = float(ipc_t.max())

    projected_total = latest_projection_total()
    rows, total_in_gate = region_table(meas, projected_total, ipc_t, ipc_d)

    edges = meas["edges"]
    centres = 0.5 * (edges[:-1] + edges[1:])
    n_flash = float(meas["n_spill_flash"])
    per_spill_per_ms = meas["counts"].astype(float) / n_flash / BIN_MS

    # -------------------------------------------------------------- figure
    INK, INK2, MUTED = "#0b0b0b", "#52514e", "#898781"
    GRID, AXIS, SURFACE = "#e1e0d9", "#c3c2b7", "#fcfcfb"
    MEASURED, EXPECTED = "#2a78d6", "#eb6834"

    fig, ax = plt.subplots(figsize=(12.6, 6.8))
    fig.patch.set_facecolor(SURFACE)
    # A true overlay on twin y axes, matching the house format of
    # analysis/flash_comb/tools/ipc_spectrum_vs_runs.py. Two scales in one frame is
    # normally the wrong call — the crossing points carry no meaning — so the axes,
    # their labels and their ticks are colour-matched to their curves and the
    # regions are the thing actually being compared.
    axr = ax.twinx()

    for a in (ax, axr):
        a.set_facecolor(SURFACE)
    ax.grid(True, color=GRID, linewidth=0.8, alpha=0.9)
    ax.set_axisbelow(True)

    # Alternating region bands.
    for i, (lo, hi) in enumerate(REGIONS):
        ax.axvspan(lo, hi, color=MUTED, alpha=0.09 if i % 2 == 0 else 0.03,
                   linewidth=0, zorder=0)
        ax.axvline(lo, color=AXIS, linewidth=0.8, alpha=0.7, zorder=1)
    ax.axvline(REGIONS[-1][1], color=AXIS, linewidth=0.8, alpha=0.7, zorder=1)

    # --- measured triggers (right axis, behind) ---
    axr.step(centres, per_spill_per_ms, where="mid", color=MEASURED,
             linewidth=0.9, alpha=0.85, zorder=2,
             label=f"run_79 recorded triggers ({BIN_MS * 1000:.0f} $\\mu$s bins)")
    axr.fill_between(centres, 0, per_spill_per_ms, step="mid",
                     color=MEASURED, alpha=0.13, linewidth=0, zorder=2)
    gate = centres >= TPLOT_MIN
    gate_max = float(per_spill_per_ms[gate].max())
    axr.set_ylim(0, gate_max * 1.75)
    axr.set_ylabel("run_79 recorded triggers / spill / ms", color=MEASURED, fontsize=10)
    axr.tick_params(axis="y", colors=MEASURED, labelsize=9, length=3)

    # --- expected IPC (left axis, in front) ---
    ax.plot(ipc_t, ipc_d * 1e6, color=EXPECTED, linewidth=2.4, zorder=4,
            label="In-gate IPC production (reweighted Geant4)")
    ax.fill_between(ipc_t, 0, ipc_d * 1e6, color=EXPECTED, alpha=0.20,
                    linewidth=0, zorder=3)
    ax.set_ylim(0, float((ipc_d * 1e6).max()) * 1.55)
    ax.set_ylabel("IPC pairs / pulse / ms   [$\\times10^{-6}$]",
                  color=EXPECTED, fontsize=10)
    ax.tick_params(axis="y", colors=EXPECTED, labelsize=9, length=3)

    # The thermal peak is the feature worth naming — it is where the IPC actually is.
    # Search above THERMAL_SEARCH_MS, as ipc_spectrum_vs_runs.py does: the global
    # maximum sits at the 1 ms gate edge, on the epithermal shoulder, and reporting
    # that as "the thermal peak" would name the wrong feature at the wrong energy.
    m_th = ipc_t > THERMAL_SEARCH_MS
    i_pk = int(np.flatnonzero(m_th)[np.argmax(ipc_d[m_th])])
    t_pk = float(ipc_t[i_pk])
    E_pk = (ipc_meta["flight_ms"] / t_pk) ** 2
    ax.annotate(f"thermal peak {t_pk:.1f} ms\n(E $\\approx$ {E_pk * 1e3:.0f} meV)",
                xy=(t_pk, float(ipc_d[i_pk] * 1e6)),
                xytext=(t_pk * 1.9, float(ipc_d[i_pk] * 1e6) * 1.42),
                color=EXPECTED, fontsize=9.5, fontweight="bold", linespacing=1.35,
                arrowprops=dict(arrowstyle="->", color=EXPECTED, lw=1.4))

    if t_cov < TMAX:
        ax.axvspan(t_cov, TMAX, color=MUTED, alpha=0.14, linewidth=0, zorder=1)
        ax.text(float(np.sqrt(t_cov * TMAX)), 0.42,
                f"no IPC expectation past {t_cov:.0f} ms\n(spectrum stops at 2 meV)",
                transform=ax.get_xaxis_transform(), ha="center", va="center",
                fontsize=8.5, color=MUTED, linespacing=1.4, zorder=5)

    ax.set_xlabel("neutron arrival time  t  [ms]      (t = 0 is the gamma flash)",
                  color=INK2, fontsize=10.5)
    # Log x: it spreads the five regions to roughly even widths, which both
    # un-crowds their labels and stops the 25-80 ms band from occupying two thirds
    # of the frame while contributing a third of the triggers. The thermal peak
    # stays legible, which a linear axis out to 80 ms would not manage.
    ax.set_xscale("log")
    ax.set_xlim(TPLOT_MIN, TMAX)
    ax.set_xticks([1, 2, 3, 5, 8, 10, 15, 25, 40, 80])
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:g}"))
    ax.xaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
    for s in ("top",):
        ax.spines[s].set_visible(False)
        axr.spines[s].set_visible(False)
    ax.spines["left"].set_color(EXPECTED)
    axr.spines["right"].set_color(MEASURED)
    axr.spines["left"].set_visible(False)
    ax.spines["bottom"].set_color(AXIS)
    ax.tick_params(axis="x", colors=MUTED, labelsize=9, length=3)
    for lbl in ax.get_xticklabels():
        lbl.set_color(INK2)

    # Region labels at the geometric middle of each band.
    for r in rows:
        mid = float(np.sqrt(r["lo"] * r["hi"]))
        txt = f"{r['lo']:g}–{r['hi']:g} ms\n{r['share'] * 100:.0f}% · {r['per_spill']:.1f}/spill"
        if r["projected"]:
            txt += f"\n→ {r['projected'] / 1e6:.2f}M"
        if r["ipc_share"] is not None:
            txt += f"\nIPC {r['ipc_share'] * 100:.0f}%{'*' if r['ipc_partial'] else ''}"
        ax.text(mid, 0.985, txt, transform=ax.get_xaxis_transform(),
                ha="center", va="top", fontsize=8.5, color=INK2, linespacing=1.4,
                zorder=6,
                bbox=dict(boxstyle="round,pad=0.3", facecolor=SURFACE,
                          edgecolor=AXIS, linewidth=0.8, alpha=0.95))

    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = axr.get_legend_handles_labels()
    leg = ax.legend(h1 + h2, l1 + l2, loc="center right", frameon=False, fontsize=9.5)
    for t in leg.get_texts():
        t.set_color(INK2)

    ax.set_title("In-gate IPC spectrum vs run_79's recorded triggers",
                 color=INK, fontsize=14, fontweight="bold", loc="left", pad=34)
    sub = (f"$\\int$ = {ipc_meta['ipc_per_pulse']:.2e} IPC/pulse = "
           f"{ipc_meta['ipc_per_day']:.2f} IPC/day  ·  "
           f"run_79 {int(meas['n_spill_flash']):,} flash-anchored spills of "
           f"{int(meas['n_spill_total']):,} · FEU {str(meas['feu'])}")
    if projected_total:
        sub += f"  ·  → scaled to the {projected_total / 1e6:.1f}M projection"
    ax.text(0, 1.045, sub, transform=ax.transAxes, color=MUTED,
            fontsize=9.5, va="bottom")

    fig.text(0.008, -0.02,
             f"Generated {datetime.now():%Y-%m-%d %H:%M} · IPC = MX17_Full_Geant "
             f"analysis/reweight/ipc_ingate_spectrum.npz (sub-keV thermal campaign "
             f"reweighted by ENDF/B-VIII.0 $\\sigma_{{n\\gamma}}/\\sigma_{{np}}$) · "
             f"flash tagged by ADC saturation, unflashed spills dropped · "
             f"* region only partly covered by the spectrum",
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
