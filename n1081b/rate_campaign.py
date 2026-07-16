#!/usr/bin/env python3
"""N1081B rate-measurement campaign (FEU DAQ offline). Three phases, all binned with beam
state recorded so analysis can beam-normalize:

  Phase 1  RATE RATIOS (~25 min, nominal thresholds): count walls (M5.A), scints (M5.B),
           sectors (M5.C), Singles (M5.D0), Doubles (M4.B TOTAL) in 30 s bins. -> per-wall
           and per-scint rate ratios (each channel / the 4-channel sum), beam-independent.

  Phase 2  SCINT THRESHOLD SCAN (~30 min): set ALL four M2 (.241) scint sections to a common
           discriminator threshold T in {-40,-60,-80,-100,-120,-140} mV; at each, 5x 60 s bins.
           Walls (M5.A) are independent of the scint threshold -> used as the beam monitor.
           -> scint rate, sector, Singles, Doubles vs threshold. M2 restored to snapshot after.

  Phase 3  GOOD-STATS SINGLES/DOUBLES (~50 min, nominal thresholds): 60 s bins. Long average
           of Singles and Doubles with statistical errors, plus per-1e10-proton normalization.

Results stream to snapshots/rate_campaign_<stamp>.json (one record per bin). M2 thresholds are
snapshotted at start and restored in a finally block. Run on mx17-daq:
    .venv/bin/python n1081b/rate_campaign.py [stamp]
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from m3_timing_lib import connect, read_m5_rates, M5_IP  # noqa: E402
from n1081b_sdk import N1081B  # noqa: E402

M4_IP, M2_IP = "192.168.10.243", "192.168.10.241"
SEC_B_M4 = N1081B.Section.SEC_B
M2_SECTIONS = [N1081B.Section.SEC_A, N1081B.Section.SEC_B,
               N1081B.Section.SEC_C, N1081B.Section.SEC_D]
DISCR = N1081B.SignalStandard.STANDARD_DISCRIMINATOR
IMP50 = N1081B.SignalImpedance.IMPEDANCE_50

STAMP = sys.argv[1] if len(sys.argv) > 1 else "run"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "snapshots", f"rate_campaign_{STAMP}.json")
BEAM_STATE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "config", "beam_state.json")

PHASE1_MIN, P1_BIN = 25, 30
THRESHOLDS = [-40, -60, -80, -100, -120, -140]
P2_BINS_PER_T, P2_BIN = 5, 60
PHASE3_MIN, P3_BIN = 50, 60


class _V:
    def __init__(self, v):
        self.value = v


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def beam_state():
    try:
        with open(BEAM_STATE) as f:
            d = json.load(f)
        d["_fresh"] = (time.time() - os.path.getmtime(BEAM_STATE)) < 90
        return d
    except Exception:
        return {"_fresh": False}


def doubles_once():
    try:
        m4 = connect(M4_IP)
        try:
            c = (m4.get_function_results(SEC_B_M4).get("data") or {}).get("counters", [])
            return c[0]["value"] if c else None
        finally:
            m4.disconnect()
    except Exception:
        return None


def snapshot_m2(d):
    return {s.name: d.get_input_configuration(s)["data"] for s in M2_SECTIONS}


def set_m2_threshold_all(d, T):
    ok = True
    for s in M2_SECTIONS:
        d.set_input_configuration(s, DISCR, _V(0), int(T), IMP50)
        rb = d.get_input_configuration(s)["data"]
        good = (rb["threshold"] == int(T) and rb["standard"] == 2)
        ok = ok and good
        if not good:
            log(f"  !! M2 {s.name} threshold verify FAILED: {rb}")
    return ok


def restore_m2(d, snap):
    ok = True
    for s in M2_SECTIONS:
        c = snap[s.name]
        d.set_input_configuration(s, DISCR, _V(int(c["standard_sub"])), int(c["threshold"]), IMP50)
        rb = d.get_input_configuration(s)["data"]
        ok = ok and rb["threshold"] == int(c["threshold"])
    return ok


records = []


def count_bin(dur, phase, threshold):
    """One measurement bin: M5 rates over `dur` + Doubles (M4.B) + beam. Appends a record."""
    b0 = beam_state()
    d0 = doubles_once()
    t0 = time.time()
    r = read_m5_rates(dur)                 # holds its own M5 connection, sleeps `dur`
    d1 = doubles_once()
    dt = time.time() - t0
    b1 = beam_state()
    dbl = ((d1 - d0) / dt) if (d0 is not None and d1 is not None and d1 >= d0) else None
    rec = {
        "phase": phase, "threshold": threshold, "t": t0, "dur": round(dt, 1),
        "walls": [r["SEC_A"].get(i) for i in range(4)],
        "scints": [r["SEC_B"].get(i) for i in range(4)],
        "sectors": [r["SEC_C"].get(i) for i in range(4)],
        "singles": r["SEC_D"].get(0),
        "doubles": dbl,
        "beam_on": bool(b0.get("beam_on") and b1.get("beam_on") and b1.get("_fresh")),
        "beam_e10": b1.get("last_pulse_e10"),
        "pulses_10min": b1.get("pulses_10min"),
    }
    records.append(rec)
    with open(OUT, "w") as f:
        json.dump({"stamp": STAMP, "thresholds": THRESHOLDS, "records": records}, f, indent=1)
    ws = "/".join(f"{x:.0f}" for x in rec["walls"])
    sc = "/".join(f"{x:.0f}" for x in rec["scints"])
    log(f"  [{phase} T={threshold}] walls {ws}  scints {sc}  "
        f"S={rec['singles']:.1f} D={'%.2f'%dbl if dbl is not None else 'na'} "
        f"beam={'on' if rec['beam_on'] else 'OFF'} {rec['beam_e10']}")


def main():
    log(f"=== N1081B rate campaign -> {OUT} ===")
    m2 = connect(M2_IP)
    snap = snapshot_m2(m2)
    log("M2 threshold snapshot: " + ", ".join(f"{k}={v['threshold']}mV" for k, v in snap.items()))
    try:
        # -------- Phase 1: rate ratios (nominal) --------
        log(f"PHASE 1: rate ratios, {PHASE1_MIN} min, {P1_BIN}s bins (nominal thresholds)")
        t_end = time.time() + PHASE1_MIN * 60
        while time.time() < t_end:
            count_bin(P1_BIN, "P1_ratios", "nominal")

        # -------- Phase 2: scint threshold scan --------
        log(f"PHASE 2: scint threshold scan {THRESHOLDS} mV, {P2_BINS_PER_T}x{P2_BIN}s each")
        for T in THRESHOLDS:
            if not set_m2_threshold_all(m2, T):
                log(f"  !! could not set all M2 sections to {T} mV — skipping point")
                continue
            time.sleep(3)
            for _ in range(P2_BINS_PER_T):
                count_bin(P2_BIN, "P2_thrscan", T)
        log("Restoring M2 thresholds to snapshot...")
        log("  restored" if restore_m2(m2, snap) else "  !! RESTORE FAILED — check M2")

        # -------- Phase 3: good-stats Singles/Doubles --------
        log(f"PHASE 3: good-stats Singles/Doubles, {PHASE3_MIN} min, {P3_BIN}s bins (nominal)")
        t_end = time.time() + PHASE3_MIN * 60
        while time.time() < t_end:
            count_bin(P3_BIN, "P3_goodstats", "nominal")
    finally:
        log("FINALLY: ensuring M2 restored to snapshot...")
        try:
            restore_m2(m2, snap)
            after = snapshot_m2(m2)
            log("M2 now: " + ", ".join(f"{k}={v['threshold']}mV" for k, v in after.items()))
        except Exception as e:  # noqa: BLE001
            log(f"  !! restore error: {e!r} — CHECK M2 THRESHOLDS MANUALLY")
        m2.disconnect()
        log(f"done. {len(records)} bins -> {OUT}")


if __name__ == "__main__":
    main()
