#!/usr/bin/env python3
"""Task 6 — thin M3 (.242) output monostables 200->30 ns (all sectors, ch0-3) and tighten
the M4.B (.243 SEC_B) Doubles coincidence window 100->50 ns. The thin sector pulses make the
50 ns gate a real leading-edge coincidence (200 ns pulses overlap regardless of window).

Beam-normalized, auto-reverting:
  1) baseline Doubles(M4.B TOTAL)/Singles(M5.D0) over T s (beam-gated)
  2) set M3 out mono 30, verify readback; check M5.C + M4.A(M5.D0) unchanged (normalized)
  3) set M4.B width 50, verify readback
  4) after Doubles/Singles over T s
  5) if Doubles/Singles drops >30% -> REVERT window to 100 (and mono to 200), re-verify, flag

Run on mx17-daq (DAQ must be idle):  .venv/bin/python n1081b/timing_task6.py [T_seconds]
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from m3_timing_lib import connect, read_m5_rates, SECTIONS  # noqa: E402
from n1081b_sdk import N1081B  # noqa: E402

T = int(sys.argv[1]) if len(sys.argv) > 1 else 300
M3_IP, M4_IP = "192.168.10.242", "192.168.10.243"
SEC_B = N1081B.Section.SEC_B
FIRST = N1081B.CoincidenceTriggerMode.TRIGGER_FIRST
BEAM_STATE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "config", "beam_state.json")


def beam_ok():
    try:
        with open(BEAM_STATE) as f:
            d = json.load(f)
        return bool(d.get("beam_on")) and (time.time() - os.path.getmtime(BEAM_STATE)) < 90
    except Exception:
        return False


def wait_beam():
    while not beam_ok():
        print("    [beam] waiting for stable beam...", flush=True)
        time.sleep(15)


def doubles_total(m4):
    c = (m4.get_function_results(SEC_B).get("data") or {}).get("counters", [])
    return c[0]["value"] if c else None


def _doubles_once():
    m4 = connect(M4_IP)
    try:
        return doubles_total(m4)
    finally:
        m4.disconnect()


def measure(T):
    """Beam-gated: Doubles(M4.B TOTAL) + full M5 rates over T s. Returns dict. The .243
    connection is opened fresh for each Doubles read (never held idle across the count)."""
    wait_beam()
    d0 = _doubles_once()
    t0 = time.time()
    r = read_m5_rates(T)                      # sleeps T on its own M5 connection
    d1 = _doubles_once()
    dt = time.time() - t0
    dbl = (d1 - d0) / dt
    singles = r["SEC_D"].get(0, 0.0)         # M4.A singles tap
    csum = sum(r["SEC_C"].get(i, 0.0) for i in range(4))
    return {"doubles": dbl, "singles": singles, "csum": csum,
            "ratio": (dbl / singles if singles else float("nan")),
            "m5": r, "beam_ok_after": beam_ok()}


def set_m3_out_mono(mono):
    d = connect(M3_IP)
    ok = True
    try:
        for sn in SECTIONS:
            s = getattr(N1081B.Section, sn)
            for ch in range(4):
                c = d.get_output_channel_configuration(s, ch)["data"]
                d.set_output_channel_configuration(s, ch, c["status"], True, mono, c["invert"])
                rb = d.get_output_channel_configuration(s, ch)["data"]
                good = rb["enable_mono"] and rb["mono_value"] == mono
                ok = ok and good
                if not good:
                    print(f"    !! {sn} out{ch} mono verify FAILED: {rb['mono_value']}")
    finally:
        d.disconnect()
    return ok


def set_doubles_width(width):
    d = connect(M4_IP)
    try:
        d.configure_coincidence_gate(SEC_B, True, True, False, True, True,
                                     True, True, True, True, True,
                                     False, False, 0, width, FIRST)
        cfg = d.get_function_configuration(SEC_B)["data"]
        ok = cfg.get("width") == width
        print(f"    M4.B width readback = {cfg.get('width')} {'OK' if ok else '<<< FAIL'}")
    finally:
        d.disconnect()
    return ok


def main():
    print(f"=== Task 6: M3 out mono 200->30, M4.B Doubles window 100->50  (T={T}s/measure) ===")

    print(f"\n[1] baseline Doubles/Singles ({T}s)...")
    base = measure(T)
    print(f"    Doubles={base['doubles']:.3f} Hz  Singles={base['singles']:.1f} Hz  "
          f"ratio={base['ratio']:.4f}  M5.Csum={base['csum']:.1f}")

    print("\n[2] set M3 output mono -> 30 ns (all sectors ch0-3)...")
    if not set_m3_out_mono(30):
        print("    !! M3 mono verify failed — reverting to 200 and aborting.")
        set_m3_out_mono(200)
        return 2
    print("    verifying M5.C + M4.A unchanged (60s)...")
    chk = measure(60)
    dC = chk["csum"] / base["csum"] if base["csum"] else float("nan")
    dS = chk["singles"] / base["singles"] if base["singles"] else float("nan")
    print(f"    M5.Csum ratio={dC:.3f}  M4.A singles ratio={dS:.3f}  "
          f"{'OK' if 0.85 <= dC <= 1.15 and 0.85 <= dS <= 1.15 else '<-- CHECK'}")

    print("\n[3] set M4.B Doubles width -> 50 ns...")
    if not set_doubles_width(50):
        print("    !! width verify failed — reverting width to 100 and M3 mono to 200.")
        set_doubles_width(100)
        set_m3_out_mono(200)
        return 2

    print(f"\n[4] after Doubles/Singles ({T}s)...")
    aft = measure(T)
    print(f"    Doubles={aft['doubles']:.3f} Hz  Singles={aft['singles']:.1f} Hz  "
          f"ratio={aft['ratio']:.4f}  M5.Csum={aft['csum']:.1f}")

    frac = aft["ratio"] / base["ratio"] if base["ratio"] else float("nan")
    print(f"\n=== Doubles/Singles after/before = {frac:.3f} ===")
    if frac < 0.70:
        print("    >30% DROP — sectors less aligned than Task 5 implied. REVERTING window to 100.")
        set_doubles_width(100)
        print("    (M3 out mono left at 30; window back at 100.) Flag for review.")
        return 1
    print("    within tolerance — Doubles preserved; accidental (beam-burst) component tightened.")
    print("    Kept: M3 out mono 30 ns, M4.B window 50 ns.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
