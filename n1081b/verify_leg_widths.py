#!/usr/bin/env python3
"""Verify the TRUE native pulse width of M1 (wall) and M2 (scint) output monos, as
seen at M3's (.242) input — the Task 2 LA check that never happened because M1
dropped offline mid-task in the original 2026-07-11 run.

Why this needs special handling: M3's input Gate&Delay reshapes every pulse to the
gate width regardless of what arrives (see `gd_verify_m3.py`) — so with G&D enabled
(the standing config since 2026-07-11), the LA at M3's input always reads ~20 ns
no matter what M1/M2's own monos are actually set to. To see the real upstream
width this script:
  1. snapshots M3's input G&D config on all 4 sections (wall=ch0, scint=ch1),
  2. disables G&D on all of them,
  3. runs an LA sweep (all 4 sectors, OR-triggered on scint) and reports the
     measured wall/scint pulse widths + wall-vs-scint skew per sector,
  4. restores M3's G&D config exactly as it was, verified by readback.

Run this right after `setup_leg_monos.py <ns>` to confirm M1/M2 actually narrowed
to the target width (not just that the SDK accepted the write).

Usage: verify_leg_widths.py [n_frames]     (default 80, same as la_skew_stats.py)
"""
import sys
import time

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from n1081b_sdk import N1081B  # noqa: E402
from m3_timing_lib import connect, snapshot_m3_inputs, restore_m3_inputs, SECTIONS  # noqa: E402

M3_IP = "192.168.10.242"
MODE = N1081B.LogicAnalyzerTriggerMode
EDGE = N1081B.LogicAnalyzerTriggerEdge
N_FRAMES = int(sys.argv[1]) if len(sys.argv) > 1 else 80
WINDOW_NS = 500


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


def med(x):
    x = sorted(x)
    return x[len(x) // 2] if x else None


def measure(n_frames):
    """Fresh LA connection; returns {sector_idx: {'wall':[...], 'scint':[...], 'skew':[...]}}."""
    d = N1081B(M3_IP)
    d.connect()
    d.ws.settimeout(6)
    d.login("password")
    flags = []
    for _ in range(4):
        flags += [False, True, False, False, False, False] + [False] * 4  # scint(panel2) rising
    d.set_logic_analyzer_trigger(MODE.LA_TRIGGER_OR, MODE.LA_TRIGGER_OFF,
                                 EDGE.LA_EDGE_RISING, *flags)
    out = {s: {"wall": [], "scint": [], "skew": []} for s in range(4)}
    frames = 0
    for _ in range(n_frames):
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
            for _, w in wall:
                out[s]["wall"].append(w * 10)
            for _, w in scint:
                out[s]["scint"].append(w * 10)
            for sst, _ in scint:
                near = [p for p in wall if abs((p[0] - sst) * 10) <= WINDOW_NS]
                if near:
                    best = min(near, key=lambda p: abs(p[0] - sst))
                    out[s]["skew"].append((best[0] - sst) * 10)
    d.disconnect()
    return out, frames


def main():
    dc = connect(M3_IP)
    try:
        print("Snapshotting M3 input G&D (all sectors, ch0/1) ...")
        snap = snapshot_m3_inputs(dc)
        print("Disabling G&D on all sectors to expose native M1/M2 pulse widths ...")
        for sname in SECTIONS:
            s = getattr(N1081B.Section, sname)
            for ch in (0, 1):
                c = dc.get_input_channel_configuration(s, ch)["data"]
                dc.set_input_channel_configuration(s, ch, c["status"], False, 0, 0, c["invert"])
    finally:
        dc.disconnect()

    try:
        print(f"Measuring native widths, {N_FRAMES} frames ...")
        data, frames = measure(N_FRAMES)
        print(f"{frames}/{N_FRAMES} frames captured\n")
        for s, name in enumerate("ABCD"):
            ww, sw, sk = sorted(data[s]["wall"]), sorted(data[s]["scint"]), sorted(data[s]["skew"])
            print(f"sector {name}: wall n={len(ww)} med={med(ww)} ns | "
                  f"scint n={len(sw)} med={med(sw)} ns | "
                  f"skew(wall-scint) n={len(sk)} med={med(sk)} ns")
    finally:
        dc = connect(M3_IP)
        try:
            print("\nRestoring M3 input G&D to the pre-check config ...")
            ok = restore_m3_inputs(dc, snap)
            print("  restored+verified" if ok else "  <<< CHECK MANUALLY, restore may have failed")
        finally:
            dc.disconnect()


if __name__ == "__main__":
    main()
