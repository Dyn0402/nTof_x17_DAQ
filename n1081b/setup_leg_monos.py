#!/usr/bin/env python3
"""Set M1 (.240) and M2 (.241) output monostable widths — the leg pulse widths
feeding M3's coincidence AND.

Background: Task 2 of `HANDOFF_2026-07-11_trigger_timing.md` planned to thin these
monos from 50 -> 20 ns as the primary way to narrow the sector-AND window, but M1
went offline mid-task (network dead) — so the window was imposed at M3's *input*
Gate&Delay instead (see `m3_timing_lib.py`, `gd_verify_m3.py`: G&D reshapes the
pulse to the gate width regardless of the upstream mono, so M1/M2 stayed at their
original 50 ns and the M3 G&D width has been the only thing narrowing the window
since 2026-07-11). M1 (and all 6 modules) are back online as of 2026-07 — this
script finally completes that orphaned Task 2 step: thin M1's and M2's own output
monos to match whatever window width is under test (native mono width no longer
just window-dressing once G&D delay 15 ns is being evaluated).

  M1 (.240) sections A-D, output ch0-3, mono 50 -> --mono ns
    EXCEPT SEC_A ch3 (stray inverted RAW copy on an unknown cable, untouched)
  M2 (.241) sections A-D, output ch0 only, mono 50 -> --mono ns
    (ch1 stays at 100 -- it's the M5 scaler tap, unrelated to the M3 AND window)

Usage (run on mx17-daq; boards on the private DAQ net):
  .venv/bin/python n1081b/setup_leg_monos.py 15          # thin to 15 ns
  .venv/bin/python n1081b/setup_leg_monos.py 20          # thin to 20 ns
  .venv/bin/python n1081b/setup_leg_monos.py 50          # restore original (undo)
  .venv/bin/python n1081b/setup_leg_monos.py 15 --dry-run

Before/after per-channel state is appended to snapshots/leg_monos_log.jsonl. Verify
the PHYSICAL width afterward with `verify_leg_widths.py` (G&D must be temporarily
off on M3 to see the true native pulse, not the G&D-reshaped one).
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

M1_IP = "192.168.10.240"
M2_IP = "192.168.10.241"
LOG_PATH = Path(__file__).resolve().parent / "snapshots" / "leg_monos_log.jsonl"
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


def connect(ip):
    # M1 (.240) / M2 (.241) are current-firmware (login required); default
    # require_login=True keeps the original raise-on-login-failure behavior.
    s = board_session(ip, purpose="setup_leg_monos", min_gap_s=WRITE_GAP_S)
    s.__enter__()   # acquire lock + connect; raises BoardBusy/Wedged/Quarantined
    return _Board(s)


def m1_targets():
    """(section_name, ch) pairs for M1, skipping SEC_A ch3."""
    for name in ("A", "B", "C", "D"):
        for ch in range(4):
            if name == "A" and ch == 3:
                continue
            yield name, ch


def read_mono(d, sec, ch):
    o = d.get_output_channel_configuration(sec, ch)["data"]
    return {"status": o["status"], "enable_mono": o["enable_mono"],
            "mono_value": o["mono_value"], "invert": o["invert"]}


def set_mono(d, sec, ch, mono_ns, invert):
    d.set_output_channel_configuration(sec, ch, True, True, int(mono_ns), invert)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mono_ns", type=int, help="target mono width in ns (15, 20, or 50 to restore)")
    ap.add_argument("--dry-run", action="store_true", help="read + print current state only")
    args = ap.parse_args()

    ok = True
    records = {"M1": {}, "M2": {}}

    try:
        d1 = connect(M1_IP)
        try:
            for name, ch in m1_targets():
                sec = getattr(N1081B.Section, f"SEC_{name}")
                before = read_mono(d1, sec, ch)
                if args.dry_run:
                    print(f"M1 SEC_{name} ch{ch}: {before}  (dry-run, no change)")
                    continue
                set_mono(d1, sec, ch, args.mono_ns, before["invert"])
                after = read_mono(d1, sec, ch)
                good = after["mono_value"] == args.mono_ns and after["enable_mono"] is True
                ok = ok and good
                records["M1"][f"{name}.{ch}"] = {"before": before, "after": after, "verified": good}
                print(f"M1 SEC_{name} ch{ch}: mono {before['mono_value']}->{after['mono_value']} ns  "
                      f"{'OK' if good else '!! READBACK MISMATCH'}")
        finally:
            d1.close()

        d2 = connect(M2_IP)
        try:
            for name in ("A", "B", "C", "D"):
                sec = getattr(N1081B.Section, f"SEC_{name}")
                before = read_mono(d2, sec, 0)
                if args.dry_run:
                    print(f"M2 SEC_{name} ch0: {before}  (dry-run, no change)")
                    continue
                set_mono(d2, sec, 0, args.mono_ns, before["invert"])
                after = read_mono(d2, sec, 0)
                good = after["mono_value"] == args.mono_ns and after["enable_mono"] is True
                ok = ok and good
                records["M2"][f"{name}.0"] = {"before": before, "after": after, "verified": good}
                print(f"M2 SEC_{name} ch0: mono {before['mono_value']}->{after['mono_value']} ns  "
                      f"{'OK' if good else '!! READBACK MISMATCH'}")
        finally:
            d2.close()
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
                "mono_ns": args.mono_ns, "verified": ok, "sections": records,
            }) + "\n")
        print("\nSETUP " + ("COMPLETE — leg monos verified."
                            if ok else "FAILED — read-back mismatch, check manually!"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
