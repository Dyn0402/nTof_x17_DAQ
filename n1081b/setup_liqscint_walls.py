#!/usr/bin/env python3
"""Convert Module 2 (.241) walls from 2-plastic OR to 1-liquid-scintillator input.

Background: Module 2 sections A-D each discriminate the layer-1 scintillator of one
wall and feed `liq_i` to the Module 3 sector AND. Originally each section held **two
plastic scintillators** on Input 1 + Input 2 (lemo 0 + lemo 1), OR'd together, at a
shared per-section threshold of -80 mV.

As the plastics are swapped out for a **single liquid-scintillator layer** (one wall
at a time), the affected section drops to **one live input on Input 1 (lemo 0)** and
its calibrated threshold changes. This script applies that conversion for the named
walls and read-back-verifies every write.

  per wall (= Module 2 section):
    - input stage : DISCR / 50 ohm, threshold = --threshold mV (default -50, calibrated)
    - function    : FN_OR with ONLY lemo 0 enabled (Input 1 = liquid scint);
                    lemo 1 (old 2nd plastic) disabled -> OR passes Input 1 through
    - Input 2 (lemo 1) discriminator input channel is disabled too (cable removed)

Threshold is PER SECTION on the N1081B, but each converted section now has a single
live input, so the shared-threshold caveat no longer bites for these walls.

  2026-07-13 : walls A, D converted (liquid scint installed on A + D).
  +2 days    : run again with `--walls B C` when the remaining walls are done.

Usage (run on mx17-daq; boards are on the private DAQ net):
  .venv/bin/python n1081b/setup_liqscint_walls.py                 # A D, -50 mV
  .venv/bin/python n1081b/setup_liqscint_walls.py --walls B C     # the other two
  .venv/bin/python n1081b/setup_liqscint_walls.py --threshold -60 # override level
  .venv/bin/python n1081b/setup_liqscint_walls.py --dry-run       # read-only preview

Before/after per-section state is appended to snapshots/liqscint_walls_log.jsonl.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

if "__file__" in globals():
    _HERE = os.path.dirname(os.path.abspath(__file__))
    if _HERE not in sys.path:
        sys.path.insert(0, _HERE)

from n1081b_sdk import N1081B
from n1081b_session import (board_session, BoardBusyError, BoardWedgedError,
                            BoardQuarantinedError)

M2_IP = "192.168.10.241"
DISCR = N1081B.SignalStandard.STANDARD_DISCRIMINATOR
IMP50 = N1081B.SignalImpedance.IMPEDANCE_50
LOG_PATH = Path(__file__).resolve().parent / "snapshots" / "liqscint_walls_log.jsonl"
WRITE_GAP_S = 0.3  # pace config writes per the board-hygiene guardrail


class _Board:
    """Adapter routing existing d.method(args) call sites through a board_session
    (lock + pacing + breaker + guaranteed clean close). See n1081b/CLAUDE.md."""
    def __init__(self, session):
        self._s = session

    def __getattr__(self, name):
        return lambda *a, **k: self._s.call(name, *a, **k)

    def close(self):
        self._s.__exit__(None, None, None)


class _V:
    """Wrap the discriminator standard_special value (standard_sub = 0)."""
    def __init__(self, v):
        self.value = v


def connect(ip=M2_IP):
    # .241 is current-firmware (login required); default require_login=True keeps the
    # original raise-on-login-failure behavior.
    s = board_session(ip, purpose="setup_liqscint_walls", min_gap_s=WRITE_GAP_S)
    s.__enter__()   # acquire lock + connect; raises BoardBusy/Wedged/Quarantined
    return _Board(s)


def sec_state(d, sec):
    ic = d.get_input_configuration(sec)["data"]
    fc = d.get_function_configuration(sec)["data"]
    in1 = d.get_input_channel_configuration(sec, 1)["data"]
    return {
        "threshold": ic["threshold"],
        "standard": ic["standard"],
        "standard_sub": ic["standard_sub"],
        "imp": ic["imp"],
        "or_lemos": sorted(l["lemo"] for l in fc["lemo_enables"] if l["enable"]),
        "in1_status": in1["status"],
    }


def convert(d, sec, threshold):
    """Set the section to single-input liquid-scint: threshold + OR(lemo0) only."""
    # input stage: DISCR / 50 ohm, new calibrated threshold
    d.set_input_configuration(sec, DISCR, _V(0), int(threshold), IMP50)
    # function: OR with only lemo 0 (Input 1) enabled
    d.set_section_function(sec, N1081B.FunctionType.FN_OR)
    d.configure_or(sec, True, False, False, False, False, False, False, 0)
    # disable the now-unused Input 2 (lemo 1) discriminator channel (cable removed)
    c1 = d.get_input_channel_configuration(sec, 1)["data"]
    d.set_input_channel_configuration(sec, 1, False, c1["enable_gd"],
                                      c1["gate"], c1["delay"], c1["invert"])


def verify(state, threshold):
    return (state["threshold"] == int(threshold)
            and state["standard"] == 2
            and state["or_lemos"] == [0]
            and state["in1_status"] is False)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--walls", nargs="+", default=["A", "D"],
                    metavar="X", help="wall/section letters to convert (default: A D)")
    ap.add_argument("--threshold", type=int, default=-50,
                    help="calibrated per-section threshold in mV (default: -50)")
    ap.add_argument("--dry-run", action="store_true",
                    help="read + print current state only, write nothing")
    args = ap.parse_args()

    walls = [w.upper() for w in args.walls]
    for w in walls:
        if w not in ("A", "B", "C", "D"):
            ap.error(f"bad wall {w!r}; must be one of A B C D")

    ok = True
    records = {}
    try:
        d = connect()
        try:
            for w in walls:
                sec = getattr(N1081B.Section, f"SEC_{w}")
                before = sec_state(d, sec)
                if args.dry_run:
                    print(f"Wall {w}: {before}  (dry-run, no change)")
                    records[w] = {"before": before, "after": None}
                    continue
                convert(d, sec, args.threshold)
                after = sec_state(d, sec)
                good = verify(after, args.threshold)
                ok = ok and good
                records[w] = {"before": before, "after": after, "verified": good}
                print(f"Wall {w}: thr {before['threshold']}->{after['threshold']} mV, "
                      f"OR lemos {before['or_lemos']}->{after['or_lemos']}, "
                      f"in1 {before['in1_status']}->{after['in1_status']}  "
                      f"{'OK' if good else '!! READBACK MISMATCH'}")
        finally:
            d.close()
    except (BoardBusyError, BoardWedgedError, BoardQuarantinedError) as e:
        print(f"!! ABORTED (board unavailable): {e!r}", file=sys.stderr)
        print("   A board is held/wedged/resting; do NOT force or retry — let it rest.",
              file=sys.stderr)
        return 2

    if not args.dry_run:
        LOG_PATH.parent.mkdir(exist_ok=True)
        with open(LOG_PATH, "a") as f:
            f.write(json.dumps({
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "ip": M2_IP, "walls": walls, "threshold": args.threshold,
                "verified": ok, "sections": records,
            }) + "\n")
        print("\nSETUP " + ("COMPLETE — converted walls verified."
                            if ok else "FAILED — read-back mismatch, DO NOT trust the trigger!"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
