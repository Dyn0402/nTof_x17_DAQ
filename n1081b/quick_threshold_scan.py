#!/usr/bin/env python3
"""Fast threshold-vs-singles-rate sweep, one board/section at a time, short dwell.

Built for a time-boxed rate-equalization pass (2026-07-15): find a per-section
threshold that brings an outlier detector's singles rate down toward its
neighbors, without the ~60s/point rigor of `rate_campaign.py` / `timing_task3_scan.py`.
Short dwell means noisier per-point stats -- treat this as a fast triage tool,
not a final calibration (a proper equalization pass is still owed).

M1 (.240, walls): DISCR, positive threshold (raise = fewer counts)
M2 (.241, scints): DISCR, negative threshold (more negative = fewer counts)

Usage:
  .venv/bin/python n1081b/quick_threshold_scan.py 240 B 30 50 70 90 120 150 --dwell 4
  .venv/bin/python n1081b/quick_threshold_scan.py 241 C -15 -30 -50 -80 --dwell 4

Prints rate at each candidate for ALL FOUR sections of that board (only the
named section's threshold changes), so you can see cross-talk/context. Restores
the section's original threshold on exit unless --apply is given.
"""
import os as _os, sys as _sys
if _os.environ.get("N1081B_ALLOW_LEGACY") != "1":
    _sys.exit(
        "REFUSING TO RUN: pre-FIFO legacy tool (thresholds/grids are ~2x stale "
        "since the 2026-07-17 fan-in/fan-out re-cabling + recalibration; wall D "
        "is DEAD <= -24 mV). Use n1081b/threshold_ladder.py / rate_scan_2d.py. "
        "See HANDOFF_2026-07-17_night_trigger_scans.md. "
        "Set N1081B_ALLOW_LEGACY=1 to override.")

import argparse
import sys
import time

from n1081b_sdk import N1081B

M5_IP = "192.168.10.244"
BOARD_SCALER_SEC = {240: "SEC_A", 241: "SEC_B", 242: "SEC_C"}  # wall/scint/sector tap


class _V:
    def __init__(self, v):
        self.value = v


def connect(ip):
    d = N1081B(ip)
    if not d.connect():
        raise RuntimeError(f"connect to {ip} failed")
    d.ws.settimeout(8)
    if not d.login("password"):
        raise RuntimeError(f"login to {ip} failed")
    return d


def read_scaler(sec_name, dwell):
    d = connect(M5_IP)
    try:
        sec = getattr(N1081B.Section, sec_name)
        r0 = {c: v for c, v in enumerate(x["value"] for x in
              (d.get_function_results(sec).get("data") or {}).get("counters", []))}
        t0 = time.time()
        time.sleep(dwell)
        dt = time.time() - t0
        r1 = {c: v for c, v in enumerate(x["value"] for x in
              (d.get_function_results(sec).get("data") or {}).get("counters", []))}
        return {c: round((r1[c] - r0[c]) / dt, 1) for c in r0}
    finally:
        d.disconnect()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("board_last_octet", type=int, choices=[240, 241])
    ap.add_argument("section", choices=["A", "B", "C", "D"])
    ap.add_argument("thresholds", nargs="+", type=int)
    ap.add_argument("--dwell", type=float, default=4.0)
    ap.add_argument("--apply", action="store_true",
                    help="leave the LAST threshold in the list applied (default: restore original)")
    args = ap.parse_args()

    ip = f"192.168.10.{args.board_last_octet}"
    scaler_sec = BOARD_SCALER_SEC[args.board_last_octet]
    sec_idx = {"A": 0, "B": 1, "C": 2, "D": 3}[args.section]

    d = connect(ip)
    sec = getattr(N1081B.Section, f"SEC_{args.section}")
    orig = d.get_input_configuration(sec)["data"]
    std_sub = orig["standard_sub"]
    print(f"{ip} SEC_{args.section}: original threshold={orig['threshold']} mV "
          f"(will {'apply last value' if args.apply else 'restore this'} on exit)")

    try:
        for T in args.thresholds:
            d.set_input_configuration(sec, N1081B.SignalStandard.STANDARD_DISCRIMINATOR,
                                      _V(std_sub), int(T), N1081B.SignalImpedance.IMPEDANCE_50)
            rb = d.get_input_configuration(sec)["data"]
            ok = rb["threshold"] == int(T)
            rates = read_scaler(scaler_sec, args.dwell)
            print(f"  T={T:+4d} mV  {'OK' if ok else '!! VERIFY FAIL'}  "
                  f"rates(A,B,C,D)=({rates.get(0)},{rates.get(1)},{rates.get(2)},{rates.get(3)})  "
                  f"<- this section = {rates.get(sec_idx)}")
    finally:
        if args.apply:
            final = args.thresholds[-1]
            d.set_input_configuration(sec, N1081B.SignalStandard.STANDARD_DISCRIMINATOR,
                                      _V(std_sub), int(final), N1081B.SignalImpedance.IMPEDANCE_50)
            rb = d.get_input_configuration(sec)["data"]
            print(f"APPLIED: SEC_{args.section} threshold left at {rb['threshold']} mV")
        else:
            d.set_input_configuration(sec, N1081B.SignalStandard.STANDARD_DISCRIMINATOR,
                                      _V(std_sub), int(orig["threshold"]), N1081B.SignalImpedance.IMPEDANCE_50)
            rb = d.get_input_configuration(sec)["data"]
            print(f"RESTORED: SEC_{args.section} threshold back to {rb['threshold']} mV")
        d.disconnect()


if __name__ == "__main__":
    sys.exit(main())
