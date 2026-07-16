#!/usr/bin/env python3
"""Inter-wall timing from an M5 section-A time-tag CSV (mod5_timetag_logger.py).
For each wall pair histogram Δt = t_i - t_j over |Δt| <= WINDOW using the board clock
(a monotonic ns counter, no wrap over a run). True coincident hits (a particle crossing
both walls, or beam-correlated) make a peak on a flat accidental background; the peak
position is the inter-wall electronic skew. Walls = panels 1,2,4,5.

Accept if every pair's |peak| <= 20 ns (handoff Task 5). Usage:
    analyze_walls_tt.py n1081b/snapshots/walls_tt_v1.csv [out.png]
"""
import csv
import sys
from itertools import combinations

import numpy as np

path = sys.argv[1]
png = sys.argv[2] if len(sys.argv) > 2 else None
WALLS = [1, 2, 4, 5]
WINDOW = 500          # ns, half-range of the correlation histogram
BIN = 10              # ns (board granularity)

t = {w: [] for w in WALLS}
with open(path) as f:
    r = csv.reader(f)
    next(r)
    for row in r:
        ch = int(row[2])
        if ch in t:
            t[ch].append(int(row[3]))
for w in WALLS:
    t[w] = np.sort(np.array(t[w], dtype=np.int64))
print(f"=== {path} ===")
print("hits per wall: " + "  ".join(f"panel{w}={len(t[w])}" for w in WALLS))

edges = np.arange(-WINDOW - BIN / 2, WINDOW + BIN, BIN)
centers = (edges[:-1] + edges[1:]) / 2


def corr(ti, tj):
    """All Δt = ti - tj within +-WINDOW (histogram of the cross-correlation)."""
    lo = np.searchsorted(tj, ti - WINDOW, side="left")
    hi = np.searchsorted(tj, ti + WINDOW, side="right")
    deltas = []
    for k in range(len(ti)):
        if hi[k] > lo[k]:
            deltas.append(ti[k] - tj[lo[k]:hi[k]])
    if not deltas:
        return np.zeros(len(centers))
    d = np.concatenate(deltas)
    h, _ = np.histogram(d, bins=edges)
    return h


def peak_stats(h):
    """Peak position (centroid over the peak bin +-2 bins), plus peak/background."""
    kmax = int(np.argmax(h))
    lo, hi = max(0, kmax - 2), min(len(h), kmax + 3)
    w = h[lo:hi]
    pos = float(np.sum(centers[lo:hi] * w) / np.sum(w)) if w.sum() else float("nan")
    # background = median of bins outside +-100 ns
    far = h[np.abs(centers) > 100]
    bg = float(np.median(far)) if len(far) else 0.0
    return pos, int(h[kmax]), bg


results = {}
print(f"\n{'pair':>7} {'peak_ns':>8} {'peakcnt':>8} {'bkg':>6}  {'signif':>7}")
for i, j in combinations(WALLS, 2):
    h = corr(t[i], t[j])
    pos, pk, bg = peak_stats(h)
    signif = (pk - bg) / (bg ** 0.5) if bg > 0 else float("inf")
    results[(i, j)] = (pos, pk, bg, h)
    print(f"  {i}-{j:<3} {pos:8.1f} {pk:8d} {bg:6.1f}  {signif:7.1f}")

print("\nSkew of each wall relative to panel 1 (peak of pair 1-w, sign = w earlier(+)/later):")
maxabs = 0.0
for w in WALLS:
    if w == 1:
        skew = 0.0
    else:
        pos = results[(1, w)][0]  # Δt = t1 - tw ; +pos => wall1 later => w earlier
        skew = pos
    maxabs = max(maxabs, abs(skew))
    print(f"  panel {w}: {skew:+.1f} ns")
verdict = "PASS (all walls within +-20 ns)" if maxabs <= 20 else \
          f"OUT: worst |skew|={maxabs:.1f} ns > 20 -> trim early walls at M4.A/B input G&D"
print(f"\n{verdict}")

if png:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(8, 5))
        for (i, j), (pos, pk, bg, h) in results.items():
            ax.plot(centers, h, label=f"{i}-{j} (peak {pos:+.0f} ns)")
        ax.axvline(0, color="k", lw=0.6, alpha=0.4)
        ax.set_xlim(-200, 200)
        ax.set_xlabel("Δt = t_i - t_j  [ns]")
        ax.set_ylabel("coincident pairs / 10 ns")
        ax.set_title("Inter-wall time-tag correlation (M5.A)")
        ax.legend(fontsize=8)
        ax.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(png, dpi=120)
        print(f"plot -> {png}")
    except Exception as e:  # noqa: BLE001
        print(f"(no plot: {e!r})")
