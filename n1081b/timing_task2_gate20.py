#!/usr/bin/env python3
"""Task 2 (adapted) — impose the 20 ns coincidence window at M3 (.242) input G&D on
BOTH legs (wall ch0 + scint ch1) of all four sectors, replacing the offline M1's output
mono thinning. Measures M5 (.244) sector/wall/scint rates before and after and reports the
drift-robust efficiency  eff = (C/sqrt(A*B))_after / (..)_before  per sector.

Leaves the board with gate=20/delay=0 on both legs (the delay-scan zero point) if the user
proceeds; a snapshot of the original M3 input config is written to disk for restoration.

Accept if eff >= 0.9 per sector (handoff Task 2). Run on mx17-daq:
    .venv/bin/python n1081b/timing_task2_gate20.py [count_seconds]
"""
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from m3_timing_lib import (  # noqa: E402
    connect, snapshot_m3_inputs, read_m5_rates, set_m3_gd, M3_IP, WALL_CH, SCINT_CH,
)

T = int(sys.argv[1]) if len(sys.argv) > 1 else 120
SNAP_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "snapshots", "m3_inputs_pretiming.json")


def eff_row(before, after):
    """Per-sector eff = (C/sqrt(A*B))_after / (C/sqrt(A*B))_before, ch0-3."""
    out = {}
    for i in range(4):
        def metric(r):
            a = r['SEC_A'].get(i, 0); b = r['SEC_B'].get(i, 0); c = r['SEC_C'].get(i, 0)
            denom = math.sqrt(a * b) if a > 0 and b > 0 else 0
            return (c / denom) if denom > 0 else None
        mb, ma = metric(before), metric(after)
        out[i] = (ma / mb) if (mb and ma) else None
    return out


def show(tag, r):
    for sname, lbl in [('SEC_A', 'walls'), ('SEC_B', 'scints'), ('SEC_C', 'sectors')]:
        print(f"  {tag} {sname}({lbl:7s}): "
              + "  ".join(f"ch{i}={r[sname].get(i)}" for i in range(4)))


def main():
    print(f"=== Task 2: gate BOTH M3 legs to 20 ns, all sectors ({T}s counts) ===")
    d = connect(M3_IP)
    snap = snapshot_m3_inputs(d)
    with open(SNAP_PATH, 'w') as f:
        json.dump({s: {str(c): v for c, v in ch.items()} for s, ch in snap.items()}, f, indent=1)
    print(f"Saved original M3 input config -> {SNAP_PATH}")

    # sanity: both legs should currently be gd-off (baseline)
    non_baseline = [(s, ch) for s in snap for ch in snap[s]
                    if snap[s][ch]['enable_gd'] or snap[s][ch]['delay']]
    if non_baseline:
        print(f"  NOTE: some legs already have G&D set: {non_baseline}")

    print(f"\n[before] counting M5 rates for {T}s (M3 legs gd-off)...")
    before = read_m5_rates(T)
    show("before", before)

    print("\nApplying gate=20 delay=0 to M3 wall(ch0)+scint(ch1), all sectors...")
    ok = set_m3_gd(d, WALL_CH, True, 20, 0)
    ok = set_m3_gd(d, SCINT_CH, True, 20, 0) and ok
    print("  apply+verify:", "OK" if ok else "<<< VERIFY FAILED")
    if not ok:
        print("  Aborting; restoring baseline.")
        set_m3_gd(d, WALL_CH, False, 0, 0)
        set_m3_gd(d, SCINT_CH, False, 0, 0)
        d.disconnect()
        return 2

    print(f"\n[after] counting M5 rates for {T}s (both legs gate=20)...")
    after = read_m5_rates(T)
    show("after", after)

    print("\n=== per-sector efficiency (eff = C/sqrt(A*B) after/before) ===")
    effs = eff_row(before, after)
    allpass = True
    for i in range(4):
        e = effs[i]
        verdict = "n/a" if e is None else ("PASS" if e >= 0.9 else "LOW")
        if e is not None and e < 0.9:
            allpass = False
        print(f"  sector {chr(65+i)}: eff={e if e is None else round(e,3)}  {verdict}"
              f"   (C before={before['SEC_C'].get(i)} after={after['SEC_C'].get(i)} Hz)")
    print(f"\n=== {'ALL sectors eff>=0.9 — OK to proceed to delay scan' if allpass else 'SOME sectors low — review before scan'} ===")
    print("Board left with both M3 legs at gate=20/delay=0 (delay-scan zero point).")
    print(f"To restore baseline later: set both legs gd-off, or reload {SNAP_PATH}")
    d.disconnect()
    return 0 if allpass else 1


if __name__ == "__main__":
    sys.exit(main())
