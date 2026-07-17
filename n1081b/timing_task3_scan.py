#!/usr/bin/env python3
"""Task 3 (adapted, two-set beam-normalized) — delay curve scan, wall vs scint per sector,
window imposed at M3 (.242) input G&D. BOTH legs always gated to the same width (GATE_NS,
default 20; pass a third CLI arg to test other widths, e.g. 15);
only the relative delay is swept, so the common G&D insertion latency cancels (Task 1).

2026-07 rerun note: originally run at 20 ns while M1 (.240) was offline (workaround —
window lived only at M3 G&D, not at M1/M2's own output monostables). M1 (and all 6
modules) are back online now; GATE_NS=15 is the hardware floor
(UM8139: monostable 15 ns-1 us). If 15 ns shows no clean plateau or a center that eats
into the edge (watch sector A: it was already off-center at -6.8 ns / FWHM 39 ns at
20 ns; see snapshots/timing_scan_run2.json + analyze_timing_scan.py), rerun with
GATE_NS=20 to reconfirm the known-good baseline still holds.

The trigger rates are REAL BEAM data (see memory: beam on/off + intensity swings dominate).
Two robustness measures:
  1) BEAM GATING — count only while config/beam_state.json says beam_on and is fresh; if the
     beam drops during a 60 s count, retake the point. Beam intensity is logged per point.
  2) FIXED-REFERENCE NORMALIZATION (user's idea) — run TWO sets. In each set, hold 2 sectors
     at delay=0 as a live beam monitor and sweep the other 2. The held sectors' coincidence
     rate is measured SIMULTANEOUSLY, so norm = C_swept / sum(C_held) divides out beam
     fluctuations using the same physics (a wall&scint coincidence), not just singles.
       set1: hold {C,D}, sweep {A,B}      set2: hold {A,B}, sweep {C,D}
  Singles are also recorded so C/sqrt(A*B) is available as a cross-check.

Signed delay axis: scint-delayed = negative, wall-delayed = positive (both legs
gate=GATE_NS). Per point: fresh M3 connection sets the swept sectors' delay (verified);
M5 (.244) counts C/A/B for COUNT_S. Results stream to snapshots/timing_scan_<stamp>.json.
On exit ALL sectors return to gate=RESTORE_GATE_NS/delay=0 (the known-good 20 ns
baseline, regardless of what GATE_NS the scan itself used) — a scan run at 15 ns
does NOT leave the board at 15 ns; that's a deliberate decision made after analyzing
the results, applied separately.  Run on mx17-daq:
    .venv/bin/python n1081b/timing_task3_scan.py [count_seconds] [stamp] [gate_ns]
"""
import json
import math
import os
import sys
import time

if os.environ.get("N1081B_ALLOW_LEGACY") != "1":
    sys.exit(
        "REFUSING TO RUN: superseded by timing_delay_scan_v2.py (2026-07-17).\n"
        "This script's exit restore sets ALL M3 sectors to gate=20/delay=0, which\n"
        "silently UNDOES the standing +20 ns wall-leg delay (post-FIFO plastic\n"
        "lateness compensation — see HANDOFF_2026-07-17_night_trigger_scans.md).\n"
        "Use: .venv/bin/python n1081b/timing_delay_scan_v2.py --center 20 "
        "--restore-delay 20\n"
        "(or set N1081B_ALLOW_LEGACY=1 to run this legacy version anyway).")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from m3_timing_lib import (  # noqa: E402
    connect, set_m3_gd, read_m5_rates, M3_IP, WALL_CH, SCINT_CH, SECTIONS,
)

COUNT_S = int(sys.argv[1]) if len(sys.argv) > 1 else 60
STAMP = sys.argv[2] if len(sys.argv) > 2 else "run"
GATE_NS = int(sys.argv[3]) if len(sys.argv) > 3 else 20
RESTORE_GATE_NS = 20    # known-good baseline the board always returns to on exit
SETTLE_S = 2
STALE_S = 90            # beam_state.json older than this = don't trust beam_on
BEAM_POLL_S = 15        # how often to re-check while waiting for beam
MAX_RETAKE = 2          # retakes if beam drops mid-count

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BEAM_STATE = os.path.join(REPO, "config", "beam_state.json")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "snapshots", f"timing_scan_{STAMP}.json")

SEC_IDX = {"SEC_A": 0, "SEC_B": 1, "SEC_C": 2, "SEC_D": 3}
SETS = [
    {"name": "set1", "hold": ["SEC_C", "SEC_D"], "sweep": ["SEC_A", "SEC_B"]},
    {"name": "set2", "hold": ["SEC_A", "SEC_B"], "sweep": ["SEC_C", "SEC_D"]},
]
SIGNED_DELAYS = [0, -5, -10, -15, -20, -30, -40, -60, 0,
                 5, 10, 15, 20, 30, 40, 60, 0]


def log(m):
    print(m, flush=True)


def beam_state():
    """Current beam dict + freshness. Returns {} if unreadable."""
    try:
        with open(BEAM_STATE) as f:
            d = json.load(f)
        d["_age_s"] = time.time() - os.path.getmtime(BEAM_STATE)
        d["_ok"] = bool(d.get("beam_on")) and d["_age_s"] < STALE_S
        return d
    except Exception as e:  # noqa: BLE001
        return {"_ok": False, "_age_s": None, "_err": repr(e)}


def wait_for_beam():
    """Block until beam_on and the state file is fresh; return the beam dict."""
    waited = 0
    while True:
        b = beam_state()
        if b.get("_ok"):
            return b
        why = (f"beam_on={b.get('beam_on')} age={None if b.get('_age_s') is None else round(b['_age_s'])}s"
               f" pulses_10min={b.get('pulses_10min')}")
        log(f"    [beam] waiting for stable beam ({why}); waited {waited}s")
        time.sleep(BEAM_POLL_S)
        waited += BEAM_POLL_S


def set_delays(sections, D):
    """Set swept `sections` to signed delay D (both legs gate=GATE_NS). Fresh M3 connection."""
    dscint = -D if D <= 0 else 0
    dwall = D if D > 0 else 0
    d = connect(M3_IP)
    try:
        ok = set_m3_gd(d, WALL_CH, True, GATE_NS, dwall, sections=sections)
        ok = set_m3_gd(d, SCINT_CH, True, GATE_NS, dscint, sections=sections) and ok
    finally:
        d.disconnect()
    return ok, dwall, dscint


def hold_zero(sections, gate=GATE_NS):
    d = connect(M3_IP)
    try:
        ok = set_m3_gd(d, WALL_CH, True, gate, 0, sections=sections)
        ok = set_m3_gd(d, SCINT_CH, True, gate, 0, sections=sections) and ok
    finally:
        d.disconnect()
    return ok


def sector_rates(r):
    """{SEC_x: {'wall':A, 'scint':B, 'C':C}} from a read_m5_rates() result."""
    out = {}
    for sn, i in SEC_IDX.items():
        out[sn] = {"wall": r["SEC_A"].get(i, 0.0),
                   "scint": r["SEC_B"].get(i, 0.0),
                   "C": r["SEC_C"].get(i, 0.0)}
    return out


def main():
    log(f"=== Task 3 two-set beam-normalized delay scan: gate={GATE_NS} ns, "
        f"{len(SETS)}x{len(SIGNED_DELAYS)} pts x {COUNT_S}s -> {OUT} ===")
    results = {"count_s": COUNT_S, "settle_s": SETTLE_S, "gate_ns": GATE_NS,
               "signed_delays": SIGNED_DELAYS, "sets": [], "sec_idx": SEC_IDX}
    try:
        for S in SETS:
            hold, sweep = S["hold"], S["sweep"]
            log(f"\n--- {S['name']}: hold {hold} (delay 0, beam ref), sweep {sweep} ---")
            if not hold_zero(hold):
                log("  !! failed to set held sectors to delay 0 — skipping set")
                continue
            set_rows = []
            results["sets"].append({"name": S["name"], "hold": hold,
                                    "sweep": sweep, "rows": set_rows})
            for k, D in enumerate(SIGNED_DELAYS):
                ok, dw, ds = set_delays(sweep, D)
                if not ok:
                    log(f"  !! D={D}: swept-leg verify FAILED — skipping point")
                    continue
                # count, retaking if beam drops during the interval
                for attempt in range(MAX_RETAKE + 1):
                    b0 = wait_for_beam()
                    time.sleep(SETTLE_S)
                    r = read_m5_rates(COUNT_S)
                    b1 = beam_state()
                    stable = bool(b0.get("_ok") and b1.get("_ok"))
                    if stable or attempt == MAX_RETAKE:
                        break
                    log(f"    beam unstable during D={D} (attempt {attempt+1}) — retaking")
                sr = sector_rates(r)
                cref = sum(sr[h]["C"] for h in hold)
                row = {"signed_delay": D, "wall_delay": dw, "scint_delay": ds,
                       "sectors": sr, "c_ref": cref, "beam_stable": stable,
                       "beam_e10_before": b0.get("last_pulse_e10"),
                       "beam_e10_after": b1.get("last_pulse_e10"),
                       "pulses_10min": b1.get("pulses_10min")}
                set_rows.append(row)
                with open(OUT, "w") as f:
                    json.dump(results, f, indent=1, default=lambda o: None)
                cells = []
                for s in sweep:
                    C = sr[s]["C"]
                    norm = C / cref if cref > 0 else float("nan")
                    cells.append(f"{s[-1]}:C={C:5.1f} C/ref={norm:.3f}")
                flag = "" if stable else "  (beam UNSTABLE)"
                log(f"  [{S['name']} {k+1:2d}/{len(SIGNED_DELAYS)}] D={D:+4d} "
                    f"(w+{dw},s+{ds}) ref={cref:5.1f} e10={b1.get('last_pulse_e10')}  "
                    + "  ".join(cells) + flag)
    finally:
        log(f"\nRestoring ALL sectors to gate={RESTORE_GATE_NS}/delay=0 (known-good baseline) ...")
        ok = hold_zero(SECTIONS, gate=RESTORE_GATE_NS)
        log("  restored+verified" if ok else "  <<< CHECK MANUALLY")
        log(f"Results: {OUT}")


if __name__ == "__main__":
    main()
