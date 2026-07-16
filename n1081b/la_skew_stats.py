#!/usr/bin/env python3
"""Per-sector wall-vs-scint skew statistics at Module 3, via its logic analyzer.

Trigger on any scint leg (panel 2, rising). For every section in each frame
that has BOTH a scint pulse and a wall pulse within +-500 ns of it, record
skew = t_wall_lead - t_scint_lead (ns).  Positive => wall arrives later.

Usage: la_skew_stats.py [n_frames]
"""
import sys
import time

from n1081b_sdk import N1081B

IP = "192.168.10.242"
MODE = N1081B.LogicAnalyzerTriggerMode
EDGE = N1081B.LogicAnalyzerTriggerEdge
N_FRAMES = int(sys.argv[1]) if len(sys.argv) > 1 else 80
WINDOW_NS = 500

d = N1081B(IP)
d.connect()
d.ws.settimeout(6)
assert d.login("password")

flags = []
for _ in range(4):
    flags += [False, True, False, False, False, False] + [False] * 4
d.set_logic_analyzer_trigger(MODE.LA_TRIGGER_OR, MODE.LA_TRIGGER_OFF,
                             EDGE.LA_EDGE_RISING, *flags)


def pulses(bits):
    out, start = [], None
    for i, v in enumerate(bits):
        if v and start is None:
            start = i
        elif not v and start is not None:
            out.append((start, i - start))
            start = None
    if start is not None:
        out.append((start, len(bits) - start))
    return out


skews = {s: [] for s in range(4)}
widths = {s: {"wall": [], "scint": []} for s in range(4)}
frames = 0
t_start = time.time()
for _ in range(N_FRAMES):
    d.start_logic_analyzer()
    t0 = time.time()
    data = None
    while time.time() - t0 < 4:
        r = d.get_logic_analyzer_data()
        dd = r.get("data") if isinstance(r, dict) else None
        if isinstance(dd, dict) and dd.get("inputs"):
            data = dd
            break
        time.sleep(0.05)
    if not data:
        continue
    frames += 1
    ins = data["inputs"]
    for s in range(4):
        wall = pulses(ins[s * 6 + 0])
        scint = pulses(ins[s * 6 + 1])
        for st, w in wall:
            widths[s]["wall"].append(w * 10)
        for st, w in scint:
            widths[s]["scint"].append(w * 10)
        for sst, _ in scint:
            near = [w for w in wall if abs((w[0] - sst) * 10) <= WINDOW_NS]
            if near:
                best = min(near, key=lambda p: abs(p[0] - sst))
                skews[s].append((best[0] - sst) * 10)

print(f"{frames}/{N_FRAMES} frames in {time.time()-t_start:.0f} s")
for s, name in enumerate("ABCD"):
    sk = sorted(skews[s])
    ww = sorted(widths[s]["wall"])
    sw = sorted(widths[s]["scint"])
    def med(x):
        return x[len(x) // 2] if x else None
    print(f"sector {name}: pairs={len(sk)}"
          + (f" skew ns: med={med(sk)} min={sk[0]} max={sk[-1]} all={sk}" if sk else " (no wall+scint pairs)")
          )
    print(f"   widths ns: wall n={len(ww)} med={med(ww)} | scint n={len(sw)} med={med(sw)}")
d.disconnect()
