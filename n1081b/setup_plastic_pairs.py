#!/usr/bin/env python3
"""Set Module 2 (.241) walls back to the 2-plastic-scintillator OR configuration.

Background: the plan to swap the L1 layer to a single liquid scintillator per
wall (see `setup_liqscint_walls.py`, applied to walls A+D on 2026-07-13) was
**reversed on 2026-07-14** — we are going back to 2 plastic scintillators per
wall (Input 1 + Input 2, OR'd) on all four sections, at a new calibrated
threshold of **-15 mV** (replaces both the old -80 mV plastic-pair level and
the -50 mV liquid-scint level).

  per wall (= Module 2 section):
    - input stage : DISCR / 50 ohm, threshold = --threshold mV (default -15, calibrated)
    - function    : FN_OR with lemo 0 AND lemo 1 enabled (2 plastics per wall)
    - Input 2 (lemo 1) discriminator input channel is re-enabled (walls A, D had
      it disabled during the liquid-scint conversion)

Usage (run on mx17-daq; boards are on the private DAQ net):
  .venv/bin/python n1081b/setup_plastic_pairs.py                  # A B C D, -15 mV
  .venv/bin/python n1081b/setup_plastic_pairs.py --walls A D      # just the walls that were converted
  .venv/bin/python n1081b/setup_plastic_pairs.py --threshold -20  # override level
  .venv/bin/python n1081b/setup_plastic_pairs.py --dry-run        # read-only preview

Before/after per-section state is appended to snapshots/plastic_pairs_log.jsonl.
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
LOG_PATH = Path(__file__).resolve().parent / "snapshots" / "plastic_pairs_log.jsonl"
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
    s = board_session(ip, purpose="setup_plastic_pairs", min_gap_s=WRITE_GAP_S)
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
    """Set the section to 2-plastic OR: threshold + OR(lemo0, lemo1)."""
    # input stage: DISCR / 50 ohm, new calibrated threshold
    d.set_input_configuration(sec, DISCR, _V(0), int(threshold), IMP50)
    # function: OR with lemo 0 + lemo 1 (Input 1 + Input 2) enabled
    d.set_section_function(sec, N1081B.FunctionType.FN_OR)
    d.configure_or(sec, True, True, False, False, False, False, False, 0)
    # re-enable Input 2 (lemo 1) discriminator channel (may be disabled from
    # the liquid-scint conversion)
    c1 = d.get_input_channel_configuration(sec, 1)["data"]
    d.set_input_channel_configuration(sec, 1, True, c1["enable_gd"],
                                      c1["gate"], c1["delay"], c1["invert"])


def verify(state, threshold):
    return (state["threshold"] == int(threshold)
            and state["standard"] == 2
            and state["or_lemos"] == [0, 1]
            and state["in1_status"] is True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--walls", nargs="+", default=["A", "B", "C", "D"],
                    metavar="X", help="wall/section letters to set (default: A B C D)")
    ap.add_argument("--threshold", type=int, default=-15,
                    help="calibrated per-section threshold in mV (default: -15)")
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
        print("\nSETUP " + ("COMPLETE — 2-plastic-pair walls verified."
                            if ok else "FAILED — read-back mismatch, DO NOT trust the trigger!"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
