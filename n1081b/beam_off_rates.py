#!/usr/bin/env python3
"""Quick beam-OFF rate baseline (nominal thresholds): walls (M5.A), scints (M5.B), sectors
(M5.C), Singles (M5.D0), Doubles (M4.B) in 30 s bins for N minutes. Writes JSON + prints
mean rates. Run on mx17-daq:  .venv/bin/python n1081b/beam_off_rates.py [minutes] [stamp]"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from m3_timing_lib import connect, read_m5_rates  # noqa: E402
from n1081b_sdk import N1081B  # noqa: E402

MIN = float(sys.argv[1]) if len(sys.argv) > 1 else 4.0
STAMP = sys.argv[2] if len(sys.argv) > 2 else "run"
BIN = 30
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "snapshots", f"beam_off_rates_{STAMP}.json")
BEAM = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "beam_state.json")


def doubles():
    try:
        m = connect("192.168.10.243")
        try:
            c = (m.get_function_results(N1081B.Section.SEC_B).get("data") or {}).get("counters", [])
            return c[0]["value"] if c else None
        finally:
            m.disconnect()
    except Exception:
        return None


def beam_on():
    try:
        d = json.load(open(BEAM))
        return bool(d.get("beam_on")), d.get("last_pulse_e10")
    except Exception:
        return None, None


recs = []
t_end = time.time() + MIN * 60
print(f"beam-off rate baseline: {MIN} min, {BIN}s bins -> {OUT}", flush=True)
while time.time() < t_end:
    d0 = doubles(); t0 = time.time()
    r = read_m5_rates(BIN); d1 = doubles(); dt = time.time() - t0
    on, e10 = beam_on()
    rec = {"walls": [r["SEC_A"].get(i) for i in range(4)],
           "scints": [r["SEC_B"].get(i) for i in range(4)],
           "sectors": [r["SEC_C"].get(i) for i in range(4)],
           "singles": r["SEC_D"].get(0),
           "doubles": ((d1 - d0) / dt) if (d0 is not None and d1 is not None) else None,
           "beam_on": on, "beam_e10": e10, "dur": round(dt, 1)}
    recs.append(rec)
    json.dump({"records": recs}, open(OUT, "w"), indent=1)
    print(f"  walls {'/'.join(f'{x:.1f}' for x in rec['walls'])}  scints {'/'.join(f'{x:.1f}' for x in rec['scints'])}"
          f"  sect {'/'.join(f'{x:.1f}' for x in rec['sectors'])}  S={rec['singles']:.2f}"
          f"  D={('%.3f'%rec['doubles']) if rec['doubles'] is not None else 'na'}  beam={'on' if on else 'OFF'}", flush=True)

# summary
n = len(recs)
def avg(key, i=None):
    vals = [(r[key][i] if i is not None else r[key]) for r in recs if (r[key] is not None)]
    return sum(vals) / len(vals) if vals else float("nan")
print(f"\n=== MEAN over {n} bins ({MIN} min) ===", flush=True)
print("walls Hz :", [f"{avg('walls',i):.1f}" for i in range(4)])
print("scints Hz:", [f"{avg('scints',i):.1f}" for i in range(4)])
print("sectors Hz:", [f"{avg('sectors',i):.2f}" for i in range(4)])
print(f"Singles Hz: {avg('singles'):.2f}   Doubles Hz: {avg('doubles'):.3f}")
