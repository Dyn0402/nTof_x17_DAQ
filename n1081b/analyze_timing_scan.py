#!/usr/bin/env python3
"""Analyze a two-set beam-normalized delay scan (timing_task3_scan.py output):
per sector, build the normalized coincidence curve C/ref vs signed delay (scint-delayed
negative, wall-delayed positive), estimate the plateau center (half-max midpoint) and FWHM.
Center = residual wall-vs-scint skew. Optionally writes a PNG if matplotlib is available.

Usage: analyze_timing_scan.py n1081b/snapshots/timing_scan_run2.json [out.png]
"""
import json
import sys
from collections import defaultdict

path = sys.argv[1]
png = sys.argv[2] if len(sys.argv) > 2 else None
d = json.load(open(path))

# sector -> {signed_delay: [C/ref samples]}
curves = defaultdict(lambda: defaultdict(list))
for S in d["sets"]:
    for row in S["rows"]:
        ref = row["c_ref"]
        for s in S["sweep"]:
            C = row["sectors"][s]["C"]
            if ref and ref > 0:
                curves[s][row["signed_delay"]].append(C / ref)


def interp_cross(pts, level, rising):
    """First delay where the curve crosses `level`, scanning pts (sorted by delay)."""
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if rising and y0 < level <= y1:
            return x0 + (level - y0) * (x1 - x0) / (y1 - y0)
        if not rising and y0 >= level > y1:
            return x0 + (level - y0) * (x1 - x0) / (y1 - y0)
    return None


print(f"=== {path} ===")
print(f"{'sec':>3} {'top':>6} {'center':>7} {'FWHM':>6}   curve (signed delay: C/ref)")
summary = {}
for s in ("SEC_A", "SEC_B", "SEC_C", "SEC_D"):
    xs = sorted(curves[s])
    ys = [sum(curves[s][x]) / len(curves[s][x]) for x in xs]
    pts = list(zip(xs, ys))
    top = max(ys)
    half = top / 2
    left = interp_cross(pts, half, rising=True)
    right = interp_cross(pts, half, rising=False)
    center = (left + right) / 2 if left is not None and right is not None else None
    fwhm = (right - left) if left is not None and right is not None else None
    summary[s] = {"top": top, "center": center, "fwhm": fwhm}
    cs = "  ".join(f"{x:+d}:{y:.3f}" for x, y in pts)
    print(f"{s[-1]:>3} {top:6.3f} "
          f"{('%+.1f' % center) if center is not None else '   n/a':>7} "
          f"{('%.0f' % fwhm) if fwhm is not None else ' n/a':>6}   {cs}")

print("\nHandoff outcome rule: |center| <= 10 ns -> no per-sector delay; "
      ">10 ns -> delay the earlier leg by center.")
for s, v in summary.items():
    c = v["center"]
    if c is None:
        verdict = "no half-max crossing (check curve)"
    elif abs(c) <= 10:
        verdict = f"|center|={abs(c):.1f} <= 10 -> NO delay"
    else:
        leg = "scint" if c > 0 else "wall"   # positive center = wall-delayed optimum -> wall is early -> delay wall? see note
        verdict = f"|center|={abs(c):.1f} > 10 -> delay earlier leg ~{abs(c):.0f} ns"
    print(f"  {s[-1]}: {verdict}")

if png:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(8, 5))
        for s, color in zip(("SEC_A", "SEC_B", "SEC_C", "SEC_D"),
                            ("#d1495b", "#edae49", "#00798c", "#66a182")):
            xs = sorted(curves[s])
            ys = [sum(curves[s][x]) / len(curves[s][x]) for x in xs]
            ax.plot(xs, ys, "o-", color=color, label=f"sector {s[-1]}")
            c = summary[s]["center"]
            if c is not None:
                ax.axvline(c, color=color, ls=":", alpha=0.5)
        ax.axvline(0, color="k", lw=0.6, alpha=0.4)
        ax.set_xlabel("signed leg delay  (scint-delayed < 0 < wall-delayed)  [ns]")
        ax.set_ylabel("normalized coincidence  C / C_ref")
        ax.set_title("M3 sector coincidence vs relative leg delay (20 ns gate, beam-normalized)")
        ax.legend()
        ax.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(png, dpi=120)
        print(f"\nplot -> {png}")
    except Exception as e:  # noqa: BLE001
        print(f"\n(no plot: {e!r})")
