#!/usr/bin/env python3
"""Analyze a rate_campaign.py JSON: Phase-1 wall/scint rate ratios, Phase-2 scint threshold
scan (wall-normalized), Phase-3 good-stats Singles/Doubles. Saves plots and writes a LaTeX
report. Usage: analyze_rate_campaign.py snapshots/rate_campaign_run1.json [outdir]
"""
import json
import os
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

path = sys.argv[1]
outdir = sys.argv[2] if len(sys.argv) > 2 else os.path.dirname(os.path.abspath(path))
D = json.load(open(path))
recs = D["records"]
WALL = ["W1", "W2", "W3", "W4"]
SCINT = ["S1", "S2", "S3", "S4"]
COL = ["#d1495b", "#edae49", "#00798c", "#66a182"]


def bins(phase):
    return [r for r in recs if r["phase"] == phase]


def beamon(rs):
    return [r for r in rs if r.get("beam_on")]


def sem(a):
    a = np.asarray(a, float)
    return a.std(ddof=1) / np.sqrt(len(a)) if len(a) > 1 else float("nan")


# ---------------- Phase 1: rate ratios ----------------
p1 = beamon(bins("P1_ratios"))
wall_abs = [np.mean([r["walls"][i] for r in p1]) for i in range(4)]
scint_abs = [np.mean([r["scints"][i] for r in p1]) for i in range(4)]
# per-bin fractions -> beam-independent
wfrac = np.array([[r["walls"][i] / sum(r["walls"]) for i in range(4)] for r in p1])
sfrac = np.array([[r["scints"][i] / sum(r["scints"]) for i in range(4)] for r in p1])
wfrac_m, wfrac_e = wfrac.mean(0), wfrac.std(0, ddof=1) / np.sqrt(len(wfrac))
sfrac_m, sfrac_e = sfrac.mean(0), sfrac.std(0, ddof=1) / np.sqrt(len(sfrac))

fig, ax = plt.subplots(1, 2, figsize=(10, 4))
ax[0].bar(WALL, wfrac_m, yerr=wfrac_e, color=COL, capsize=4)
ax[0].axhline(0.25, ls=":", color="k", alpha=0.5)
ax[0].set_title(f"Wall rate fraction (n={len(p1)} beam-on bins)")
ax[0].set_ylabel("fraction of 4-wall sum")
ax[1].bar(SCINT, sfrac_m, yerr=sfrac_e, color=COL, capsize=4)
ax[1].axhline(0.25, ls=":", color="k", alpha=0.5)
ax[1].set_title("Scint rate fraction")
ax[1].set_ylabel("fraction of 4-scint sum")
for a in ax:
    a.grid(alpha=0.3, axis="y")
fig.tight_layout()
fig.savefig(f"{outdir}/rate_ratios.png", dpi=120)

# ---------------- Phase 2: threshold scan ----------------
p2 = bins("P2_thrscan")
Ts = sorted({r["threshold"] for r in p2}, reverse=True)  # -40 ... -140
scan = {}
for T in Ts:
    b = [r for r in p2 if r["threshold"] == T]
    wsum = np.array([sum(r["walls"]) for r in b], float)
    row = {"n": len(b), "wall_sum": wsum.mean()}
    for i in range(4):
        rn = np.array([r["scints"][i] for r in b]) / wsum
        row[f"scint{i}"] = (rn.mean(), sem(rn))
        row[f"scint{i}_abs"] = np.mean([r["scints"][i] for r in b])
    for key, arr in [("singles", [r["singles"] for r in b]),
                     ("doubles", [r["doubles"] for r in b if r["doubles"] is not None]),
                     ("sectors", [sum(r["sectors"]) for r in b])]:
        a = np.array(arr, float) / (wsum.mean() if key != "wall_sum" else 1)
        row[key] = (a.mean(), sem(a))
    scan[T] = row

fig, ax = plt.subplots(1, 3, figsize=(14, 4))
for i in range(4):
    y = [scan[T][f"scint{i}"][0] for T in Ts]
    e = [scan[T][f"scint{i}"][1] for T in Ts]
    ax[0].errorbar(Ts, y, yerr=e, marker="o", color=COL[i], label=SCINT[i], capsize=3)
ax[0].set_title("Scint rate / wall-sum vs threshold")
ax[0].set_xlabel("M2 discriminator threshold [mV]"); ax[0].set_ylabel("scint / wall-sum"); ax[0].legend()
for k, a, ttl in [("singles", ax[1], "Singles / wall-sum"), ("doubles", ax[2], "Doubles / wall-sum")]:
    y = [scan[T][k][0] for T in Ts]; e = [scan[T][k][1] for T in Ts]
    a.errorbar(Ts, y, yerr=e, marker="s", color="#00798c", capsize=3)
    a.set_title(f"{ttl} vs threshold"); a.set_xlabel("threshold [mV]")
for a in ax:
    a.grid(alpha=0.3); a.axvline(-80, ls=":", color="k", alpha=0.5)
fig.tight_layout()
fig.savefig(f"{outdir}/threshold_scan.png", dpi=120)

# equalization: target = MEDIAN of the four wall-normalized -80 mV rates, so hot channels move
# to higher |threshold| and cold channels to lower |threshold| (both within the scanned range).
def interp_thr(i, target):
    xs = np.array(Ts, float)                                  # thresholds (negative)
    ys = np.array([scan[T][f"scint{i}"][0] for T in Ts])      # rate at each threshold
    order = np.argsort(xs)                                    # ascending threshold: -140..-40
    xs, ys = xs[order], ys[order]                             # ys then increases with threshold
    target = min(max(target, ys.min()), ys.max())            # clamp into achievable range
    return float(np.interp(target, ys, xs))                  # ys increasing -> valid interp

nom_rate = {i: scan[-80][f"scint{i}"][0] for i in range(4)} if -80 in scan else {}
equal, eq_target = {}, None
if nom_rate:
    eq_target = float(np.median(list(nom_rate.values())))     # median of the 4 channels at -80
    for i in range(4):
        equal[i] = interp_thr(i, eq_target)

# ---------------- Phase 3: good-stats S/D ----------------
p3 = beamon(bins("P3_goodstats"))
def stat(key):
    rates = np.array([r[key] for r in p3 if r[key] is not None], float)
    durs = np.array([r["dur"] for r in p3 if r[key] is not None], float)
    counts = (rates * durs).sum(); T = durs.sum()
    return dict(rate=counts / T, poiss=np.sqrt(max(counts, 1)) / T, sem=sem(rates),
                counts=int(counts), tsec=T)
S, Dd = stat("singles"), stat("doubles")
beam_e10 = [r["beam_e10"] for r in p3 if r.get("beam_e10")]

# ---------------- Beam-off baseline (optional 3rd arg) ----------------
beamoff_section = ""
boff_path = sys.argv[3] if len(sys.argv) > 3 else None
if boff_path and os.path.exists(boff_path):
    bo = json.load(open(boff_path))["records"]

    def bavg(key, i=None):
        v = [(r[key][i] if i is not None else r[key]) for r in bo if r.get(key) is not None]
        return sum(v) / len(v) if v else float("nan")
    ow = [bavg("walls", i) for i in range(4)]
    osc = [bavg("scints", i) for i in range(4)]
    oS, oD = bavg("singles"), bavg("doubles")
    onS, onD = S["rate"], Dd["rate"]
    ind_w = [wall_abs[i] - ow[i] for i in range(4)]
    ind_s = [scint_abs[i] - osc[i] for i in range(4)]
    w2w1_off, w2w1_on = ow[1] / ow[0], wall_abs[1] / wall_abs[0]
    s3s4_off = osc[2] / osc[3]
    s3s4_ind = (scint_abs[2] - osc[2]) / (scint_abs[3] - osc[3])
    dfloor = oD / onD
    beamoff_section = rf"""
\section*{{4. Beam-off baseline \& beam-induced rates}}
{len(bo)} beam-off 30\,s bins vs.\ the beam-on means above. "Beam-induced" $=$ on $-$ off.
\begin{{center}}\begin{{tabular}}{{l|ccc}}
 & Beam-off (Hz) & Beam-on (Hz) & Beam-induced (Hz) \\ \hline
Wall W1 & {ow[0]:.0f} & {wall_abs[0]:.0f} & {ind_w[0]:.0f} \\
Wall W2 & {ow[1]:.0f} & {wall_abs[1]:.0f} & {ind_w[1]:.0f} \\
Wall W3 & {ow[2]:.0f} & {wall_abs[2]:.0f} & {ind_w[2]:.0f} \\
Wall W4 & {ow[3]:.0f} & {wall_abs[3]:.0f} & {ind_w[3]:.0f} \\ \hline
Scint S1 & {osc[0]:.1f} & {scint_abs[0]:.0f} & {ind_s[0]:.0f} \\
Scint S2 & {osc[1]:.1f} & {scint_abs[1]:.0f} & {ind_s[1]:.0f} \\
Scint S3 & {osc[2]:.1f} & {scint_abs[2]:.0f} & {ind_s[2]:.0f} \\
Scint S4 & {osc[3]:.1f} & {scint_abs[3]:.0f} & {ind_s[3]:.0f} \\ \hline
Singles & {oS:.1f} & {onS:.1f} & {onS-oS:.1f} \\
Doubles & {oD:.3f} & {onD:.3f} & {onD-oD:.3f} \\
\end{{tabular}}\end{{center}}
\textbf{{Interpretation:}}
\begin{{itemize}}\setlength{{\itemsep}}{{1pt}}
\item \textbf{{W2's excess is intrinsic, not beam.}} W2/W1 $= {w2w1_off:.2f}\times$ beam-off and
${w2w1_on:.2f}\times$ beam-on --- the same multiplicative offset, so it is a detector property
(SiPM dark/cosmic rate), not beam geometry.
\item \textbf{{S3's excess is beam flux, not noise.}} Beam-off the scints are nearly uniform
(S3/S4 $= {s3s4_off:.1f}\times$), but the beam-induced part makes S3/S4 $= {s3s4_ind:.1f}\times$
--- S3 sees genuinely more beam. Equalizing it at the discriminator would cut \emph{{real signal}},
not noise.
\item Cosmic/accidental floor: {100*dfloor:.0f}\% of the beam-on Doubles ({oD:.3f} of {onD:.3f}\,Hz)
is present with no beam.
\end{{itemize}}
"""

# ---------------- LaTeX ----------------
def f2(x): return f"{x:.2f}"
tex = rf"""\documentclass[11pt]{{article}}
\usepackage[margin=0.85in]{{geometry}}
\usepackage{{amsmath,graphicx,array}}
\usepackage[colorlinks=true]{{hyperref}}
\setlength{{\parindent}}{{0pt}}\setlength{{\parskip}}{{5pt}}
\title{{\vspace{{-1.5em}}n\_TOF X17 --- N1081B Trigger Rate Measurements}}
\date{{2026-07-11 (FEU DAQ offline; parasitic beam)}}
\begin{{document}}\maketitle\vspace{{-3em}}

\section*{{1. Element-to-element rate variation (beam-normalized)}}
Phase 1: {len(p1)} beam-on 30\,s bins. Each channel's rate as a fraction of its 4-channel sum
(beam-independent). Uniform would be 0.25 each (dotted line).

\begin{{center}}\includegraphics[width=0.95\textwidth]{{rate_ratios.png}}\end{{center}}

\begin{{center}}\begin{{tabular}}{{l|cccc}}
 & W1 & W2 & W3 & W4 \\ \hline
Wall fraction & {f2(wfrac_m[0])} & {f2(wfrac_m[1])} & {f2(wfrac_m[2])} & {f2(wfrac_m[3])} \\
Wall rate (Hz) & {wall_abs[0]:.0f} & {wall_abs[1]:.0f} & {wall_abs[2]:.0f} & {wall_abs[3]:.0f} \\ \hline
 & S1 & S2 & S3 & S4 \\ \hline
Scint fraction & {f2(sfrac_m[0])} & {f2(sfrac_m[1])} & {f2(sfrac_m[2])} & {f2(sfrac_m[3])} \\
Scint rate (Hz) & {scint_abs[0]:.0f} & {scint_abs[1]:.0f} & {scint_abs[2]:.0f} & {scint_abs[3]:.0f} \\
\end{{tabular}}\end{{center}}
Hottest wall: \textbf{{W{int(np.argmax(wfrac_m))+1}}} ({wfrac_m.max()/wfrac_m.min():.2f}$\times$ the coldest).
Hottest scint: \textbf{{S{int(np.argmax(sfrac_m))+1}}} ({sfrac_m.max()/sfrac_m.min():.2f}$\times$ the coldest).

\section*{{2. Scint threshold scan (walls as beam monitor)}}
Phase 2: all four M2 scint sections set to a common discriminator threshold; rates normalized
by the wall sum (threshold-independent). Nominal $-80$\,mV marked (dotted).

\begin{{center}}\includegraphics[width=0.98\textwidth]{{threshold_scan.png}}\end{{center}}

\textbf{{Recommended equalizing thresholds}} (bring each scint to the \emph{{median}} of the
four wall-normalized rates at $-80$\,mV; hot channels move to higher $|$threshold$|$, cold to
lower; \emph{{not applied}} --- config left at $-80$\,mV):
\begin{{center}}\begin{{tabular}}{{l|cccc}}
 & S1 & S2 & S3 & S4 \\ \hline
Rec.\ threshold (mV) & {(f2(equal.get(0,float('nan'))) if equal else 'n/a')} & {(f2(equal.get(1,float('nan'))) if equal else 'n/a')} & {(f2(equal.get(2,float('nan'))) if equal else 'n/a')} & {(f2(equal.get(3,float('nan'))) if equal else 'n/a')} \\
\end{{tabular}}\end{{center}}

\section*{{3. Singles \& Doubles trigger rates (good statistics)}}
Phase 3: {len(p3)} beam-on {int(np.median([r['dur'] for r in p3])) if p3 else 0}\,s bins,
nominal thresholds, beam $\sim${np.mean(beam_e10):.0f}$\times10^{{10}}$ protons.
\begin{{center}}\begin{{tabular}}{{l|ccc}}
Signal & Rate (Hz) & Poisson err & counts \\ \hline
Singles (M4.A) & {S['rate']:.2f} & $\pm${S['poiss']:.2f} & {S['counts']} \\
Doubles (M4.B) & {Dd['rate']:.3f} & $\pm${Dd['poiss']:.3f} & {Dd['counts']} \\
\end{{tabular}}\end{{center}}
Doubles/Singles $= {Dd['rate']/S['rate']:.4f}$. Integration time {S['tsec']/60:.0f}\,min (beam-on).
{beamoff_section}
\vspace{{1em}}\hrule\vspace{{0.4em}}
{{\small Data: \texttt{{{os.path.basename(path).replace('_', r'\_')}}}; plots \texttt{{rate\_ratios.png}},
\texttt{{threshold\_scan.png}}. Walls measured via M5.A (M1 outputs still flow though M1 is
network-offline); scint thresholds set per M2 section.}}
\end{{document}}
"""
open(f"{outdir}/rate_report.tex", "w").write(tex)
print(f"wrote plots + {outdir}/rate_report.tex")
print(f"Phase1 beam-on bins={len(p1)}  Phase2 pts={len(Ts)}  Phase3 beam-on bins={len(p3)}")
print(f"Singles={S['rate']:.2f}+-{S['poiss']:.2f} Hz  Doubles={Dd['rate']:.3f}+-{Dd['poiss']:.3f} Hz  ({Dd['counts']} doubles)")
if equal:
    print("equalizing thresholds (mV):", {f'S{i+1}': round(equal[i],1) for i in range(4)}, "target=median")
