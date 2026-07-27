#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on July 27 2026
Created in PyCharm
Created as nTof_x17_DAQ/projections/run82_comb.py

@author: Dylan Neff, dylan

run_82: the watermark x inter-packet-delay 2x2, one panel per setting, drawn the
same way as ipc_yield.py — in-gate IPC as the light-blue field, the measured
trigger yield in front, log time axis.

run_82 takes each of the four (Hwm, IPD) points TWICE in an interleaved order
(h2i5, h1i2, h2i2, h1i5, h1i5, h2i2, h1i2, h2i5). That design exists to cancel
beam drift, so the two repeats of a setting are pooled here rather than plotted
separately — and their agreement is checked and printed, because pooling two
repeats that disagree would hide exactly what the interleaving was meant to expose.

The figure of merit is the one run_82 was launched to move: the coefficient of
variation of the trigger yield across 1-10 ms in 0.1 ms bins. Coarser bins hide the
comb — the same band reads CV 0.42 at 0.5 ms bins and 0.87 at 0.1 ms — so the bin
width is not a free choice here.

Usage:
    /usr/bin/python3 run82_comb.py            # needs uproot: system python
    /usr/bin/python3 run82_comb.py --refresh
"""

import argparse
import json
import os
from collections import defaultdict
from datetime import datetime

import numpy as np

import ipc_yield as iy

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(HERE, "cache", "run82_tsf.npz")
PLOT_DIR = os.path.join(HERE, "plots")

RUN = "run_82"
# The comb lives in 1-10 ms and only shows up in fine bins.
COMB_BAND = (1.0, 10.0)
COMB_BIN_MS = 0.1
# A bin below this fraction of the band mean counts as starved. run_82's own
# write-up describes the gaps as "near-empty", and 25% of the mean is the line that
# reproduces its 33% starved fraction on run_79.
STARVED_FRAC = 0.25


def subrun_params(run=RUN):
    """{sub_run_name: {hwm, lwm, ipd, ...}} from the run's own config."""
    with open(os.path.join(iy.RUNS_ROOT, run, "run_config.json")) as f:
        cfg = json.load(f)
    out = {}
    for s in cfg.get("sub_runs", []):
        out[s["sub_run_name"]] = {
            "hwm": s.get("ovr_wrn_hwm"),
            "lwm": s.get("ovr_wrn_lwm"),
            "ipd": s.get("inter_packet_delay"),
            "run_time": s.get("run_time"),
        }
    return out


def extract_all(run=RUN, verbose=True):
    """Per-sub-run flash-anchored histograms, on ipc_yield's binning."""
    edges = iy.hist_edges()
    params = subrun_params(run)
    names, counts, nsp, nfl, nev = [], [], [], [], []
    for d in iy._subrun_dirs(run):
        name = os.path.basename(d)
        r = iy.extract_subrun(d, edges=edges)
        if r is None:
            continue
        names.append(name)
        counts.append(r["counts"])
        nsp.append(r["n_spill_total"])
        nfl.append(r["n_spill_flash"])
        nev.append(r["n_events"])
        if verbose:
            p = params.get(name, {})
            print(f"  {name:22s} Hwm {p.get('hwm')} IPD {p.get('ipd')}  "
                  f"spills {r['n_spill_total']:4d}  flash "
                  f"{100 * r['n_spill_flash'] / max(r['n_spill_total'], 1):5.1f}%  "
                  f"events {r['n_events']:6d}")
    return {
        "edges": edges, "names": np.array(names), "counts": np.array(counts),
        "n_spill_total": np.array(nsp), "n_spill_flash": np.array(nfl),
        "n_events": np.array(nev), "run": run,
        "created": datetime.now().isoformat(timespec="seconds"),
    }


def get_measured(refresh=False):
    if not refresh and os.path.exists(CACHE_PATH):
        z = np.load(CACHE_PATH, allow_pickle=True)
        return {k: z[k] for k in z.files}
    print(f"Extracting flash-anchored times from {RUN}...")
    d = extract_all()
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    np.savez_compressed(CACHE_PATH, **d)
    print(f"Cached -> {CACHE_PATH}")
    return d


def comb_metrics(counts, edges, n_flash):
    """CV and starved fraction across COMB_BAND, rebinned to COMB_BIN_MS."""
    centres = 0.5 * (edges[:-1] + edges[1:])
    m = (centres >= COMB_BAND[0]) & (centres < COMB_BAND[1])
    fine = counts[m].astype(float)
    group = int(round(COMB_BIN_MS / (edges[1] - edges[0])))
    n = (fine.size // group) * group
    rebin = fine[:n].reshape(-1, group).sum(axis=1)
    if rebin.size == 0 or rebin.mean() == 0:
        return {"cv": float("nan"), "starved": float("nan"), "per_spill": 0.0}
    return {
        "cv": float(rebin.std() / rebin.mean()),
        "starved": float((rebin < STARVED_FRAC * rebin.mean()).mean()),
        "per_spill": float(fine.sum() / n_flash) if n_flash else 0.0,
    }


def group_by_setting(meas, params):
    """Pool the interleaved repeats of each (Hwm, IPD) point."""
    edges = meas["edges"]
    groups = defaultdict(lambda: {"counts": np.zeros(len(edges) - 1, dtype=np.int64),
                                  "n_spill_flash": 0, "n_spill_total": 0,
                                  "subruns": [], "repeats": []})
    for i, name in enumerate(meas["names"]):
        name = str(name)
        p = params.get(name)
        if p is None:
            continue
        key = (p["hwm"], p["ipd"])
        g = groups[key]
        g["counts"] += meas["counts"][i]
        g["n_spill_flash"] += int(meas["n_spill_flash"][i])
        g["n_spill_total"] += int(meas["n_spill_total"][i])
        g["subruns"].append(name)
        g["lwm"] = p["lwm"]
        # Per-repeat metrics, so pooling can be justified rather than assumed.
        g["repeats"].append(comb_metrics(meas["counts"][i], edges,
                                         int(meas["n_spill_flash"][i])))
    for key, g in groups.items():
        g.update(comb_metrics(g["counts"], edges, g["n_spill_flash"]))
    return groups


def main():
    ap = argparse.ArgumentParser(description="run_82 watermark x IPD comb comparison.")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--out", default=os.path.join(PLOT_DIR, "run82_comb.png"))
    args = ap.parse_args()

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    meas = get_measured(refresh=args.refresh)
    params = subrun_params()
    groups = group_by_setting(meas, params)

    # Sort Hwm desc then IPD desc, so the production point (Hwm 2, IPD 5) leads.
    keys = sorted(groups, key=lambda k: (-k[0], -k[1]))

    ipc_t, ipc_d, ipc_meta = iy.ipc_spectrum()
    t_cov = float(ipc_t.max())

    edges = meas["edges"]
    centres = 0.5 * (edges[:-1] + edges[1:])
    bin_ms = float(edges[1] - edges[0])

    # run_79 as the production baseline, if its cache is there.
    ref = iy.load_cache()
    ref_m = comb_metrics(ref["counts"], ref["edges"],
                         int(ref["n_spill_flash"])) if ref is not None else None

    print(f"\n{'setting':>16}  {'spills':>7}  {'ev/spill':>9}  {'CV':>6}  "
          f"{'starved':>8}  repeats (CV)")
    for k in keys:
        g = groups[k]
        rp = ", ".join(f"{r['cv']:.2f}" for r in g["repeats"])
        print(f"  Hwm {k[0]} / IPD {k[1]:<3}  {g['n_spill_flash']:>7d}  "
              f"{g['per_spill']:>9.1f}  {g['cv']:>6.2f}  {g['starved'] * 100:>7.0f}%  {rp}")
    if ref_m:
        print(f"{'run_79 (Hwm2/IPD5)':>16}  {int(ref['n_spill_flash']):>7d}  "
              f"{ref_m['per_spill']:>9.1f}  {ref_m['cv']:>6.2f}  {ref_m['starved'] * 100:>7.0f}%")

    # ----------------------------------------------------------------- figure
    INK, INK2, MUTED = "#0b0b0b", "#52514e", "#898781"
    GRID, AXIS, SURFACE = "#e1e0d9", "#c3c2b7", "#fcfcfb"
    IPC_LINE, IPC_FILL = "#5598e7", "#9ec5f4"
    DATA = "#c0392b"
    GOOD = "#0ca30c"

    n = len(keys)
    fig, axes = plt.subplots(n, 1, figsize=(12.6, 2.75 * n + 1.1), sharex=True,
                             gridspec_kw={"hspace": 0.16})
    fig.patch.set_facecolor(SURFACE)
    if n == 1:
        axes = [axes]

    # One shared trigger scale across the panels — the whole point is comparing
    # shapes between settings, which a per-panel autoscale would quietly defeat.
    ymax = max(float((groups[k]["counts"].astype(float)
                      / max(groups[k]["n_spill_flash"], 1) / bin_ms)
                     [centres >= iy.TPLOT_MIN].max()) for k in keys)
    ipc_max = float((ipc_d * 1e6).max())
    best = min(keys, key=lambda k: groups[k]["cv"])

    for ax, k in zip(axes, keys):
        g = groups[k]
        per_spill_per_ms = (g["counts"].astype(float)
                            / max(g["n_spill_flash"], 1) / bin_ms)
        axr = ax.twinx()
        for a in (ax, axr):
            a.set_facecolor(SURFACE)
        ax.grid(True, color=GRID, linewidth=0.8, alpha=0.9)
        ax.set_axisbelow(True)
        ax.axvspan(COMB_BAND[0], COMB_BAND[1], color=MUTED, alpha=0.07,
                   linewidth=0, zorder=0)

        ax.fill_between(ipc_t, 0, ipc_d * 1e6, color=IPC_FILL, alpha=0.75,
                        linewidth=0, zorder=2)
        ax.plot(ipc_t, ipc_d * 1e6, color=IPC_LINE, linewidth=1.5, zorder=3)
        ax.set_ylim(0, ipc_max * 1.35)
        ax.tick_params(axis="y", colors=IPC_LINE, labelsize=8.5, length=3)
        ax.set_ylabel("IPC / pulse / ms\n[$\\times10^{-6}$]", color=IPC_LINE, fontsize=8.5)

        axr.step(centres, per_spill_per_ms, where="mid", color=DATA,
                 linewidth=0.9, zorder=5)
        axr.set_ylim(0, ymax * 1.30)
        axr.tick_params(axis="y", colors=DATA, labelsize=8.5, length=3)
        axr.set_ylabel("triggers / spill / ms", color=DATA, fontsize=8.5)

        if t_cov < iy.TMAX:
            ax.axvspan(t_cov, iy.TMAX, color=MUTED, alpha=0.12, linewidth=0, zorder=1)

        winner = (k == best)
        label = (f"Hwm {k[0]} / Lwm {g.get('lwm')}   ·   IPD {k[1]}\n"
                 f"CV(1–10 ms, 0.1 ms bins) = {g['cv']:.2f}   ·   "
                 f"{g['starved'] * 100:.0f}% starved\n"
                 f"{g['per_spill']:.1f} triggers/spill in 1–10 ms   ·   "
                 f"{g['n_spill_flash']} spills")
        ax.text(0.012, 0.94, label, transform=ax.transAxes, ha="left", va="top",
                fontsize=9, color=INK2, linespacing=1.5, zorder=8,
                bbox=dict(boxstyle="round,pad=0.4", facecolor=SURFACE,
                          edgecolor=GOOD if winner else AXIS,
                          linewidth=1.6 if winner else 0.8, alpha=0.95))
        if winner:
            ax.text(0.30, 0.94, "flattest", transform=ax.transAxes,
                    ha="left", va="top", fontsize=9, color=GOOD,
                    fontweight="bold", zorder=9)

        ax.set_xscale("log")
        ax.set_xlim(iy.TPLOT_MIN, iy.TMAX)
        ax.set_xticks([1, 2, 3, 5, 8, 10, 15, 25, 40, 80])
        ax.xaxis.set_major_formatter(
            matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:g}"))
        ax.xaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
        ax.spines["top"].set_visible(False)
        axr.spines["top"].set_visible(False)
        ax.spines["left"].set_color(IPC_LINE)
        axr.spines["right"].set_color(DATA)
        axr.spines["left"].set_visible(False)
        ax.spines["bottom"].set_color(AXIS)
        ax.tick_params(axis="x", colors=MUTED, labelsize=9, length=3)
        for lbl in ax.get_xticklabels():
            lbl.set_color(INK2)

    axes[-1].set_xlabel("neutron arrival time  t  [ms]      (t = 0 is the gamma flash)",
                        color=INK2, fontsize=10.5)

    axes[0].set_title("run_82 — watermark x inter-packet-delay, one panel per setting",
                      color=INK, fontsize=14, fontweight="bold", loc="left", pad=42)
    sub = ("each setting = its two interleaved repeats pooled  ·  shared axes across "
           "panels  ·  grey band = the 1–10 ms comb region")
    if ref_m:
        sub += f"  ·  run_79 production reference: CV {ref_m['cv']:.2f}"
    axes[0].text(0, 1.075, sub, transform=axes[0].transAxes, color=MUTED,
                 fontsize=9.5, va="bottom")

    fig.text(0.008, -0.004,
             f"Generated {datetime.now():%Y-%m-%d %H:%M} · light blue = in-gate IPC "
             f"(reweighted Geant4) · dark red = recorded triggers, {bin_ms * 1000:.0f} "
             f"$\\mu$s bins, flash-anchored · CV over 1–10 ms in "
             f"{COMB_BIN_MS * 1000:.0f} $\\mu$s bins (coarser bins hide the comb) · "
             f"starved = bins below {STARVED_FRAC:.0%} of the band mean",
             color=MUTED, fontsize=8)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    fig.savefig(args.out, dpi=150, bbox_inches="tight", facecolor=SURFACE)
    print(f"\n[run82_comb] Wrote {args.out}")


if __name__ == "__main__":
    main()
