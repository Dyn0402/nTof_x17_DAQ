#!/usr/bin/env python3
"""Set the gate&delay on the PS/gamma-flash leg of the final trigger OR — M4 (.243)
SEC_D input channel 0 (= lemo0 = the PS/flash line into the DREAM trigger cable).

This retards ONLY the PS trigger relative to the DREAM readout, sliding the flash
earlier within the readout window, WITHOUT touching the Doubles/scint leg (M4.D in1)
or the M6.A 9.6 us common PS fan-out delay (mesh injection / SiPM blanking stay put).

Measured 2026-07-19 (ps_flash_framing test): flash peak sample = latency + 8 at
60 ns/sample. At latency 35 / 32 smp the flash sits at ~43 (off the 32-sample
window). Adding delay d ns pulls it earlier by d/60 samples; d=1800 -> peak ~13,
which aligns with the Doubles MM pulse at latency-24 = ~11.

Goes through n1081b_session (mandatory) and is read-back verified.

Usage:
  set_ps_trigger_delay.py --delay 1800          # enable G&D, delay 1800 ns, gate kept
  set_ps_trigger_delay.py --delay 0 --disable    # turn the G&D leg back off
  set_ps_trigger_delay.py --show                 # just print the current M4.D in0 config
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from n1081b_sdk import N1081B
from n1081b_session import (board_session, BoardBusyError, BoardWedgedError,
                            BoardQuarantinedError)

M4_IP = "192.168.10.243"
SEC = N1081B.Section.SEC_D
CH = 0  # lemo0 = PS/gamma-flash line
WRITE_GAP_S = 0.3


def _get(s):
    return s.call("get_input_channel_configuration", SEC, CH)["data"]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--delay", type=int, help="delay in ns to program on M4.D in0")
    ap.add_argument("--gate", type=int, default=None, help="gate width ns (default: keep current)")
    ap.add_argument("--disable", action="store_true", help="set enable_gd False (bypass G&D)")
    ap.add_argument("--show", action="store_true", help="print current config and exit")
    args = ap.parse_args()

    try:
        with board_session(M4_IP, purpose="set PS trigger delay (M4.D in0)",
                           min_gap_s=WRITE_GAP_S) as s:
            before = _get(s)
            print(f"BEFORE  M4.D in0: status={before['status']} enable_gd={before['enable_gd']} "
                  f"gate={before['gate']} delay={before['delay']} invert={before['invert']}")
            if args.show:
                return 0
            if args.delay is None:
                sys.exit("give --delay (or --show)")

            enable_gd = not args.disable
            gate = args.gate if args.gate is not None else before["gate"]
            s.call("set_input_channel_configuration", SEC, CH,
                   before["status"], enable_gd, gate, args.delay, before["invert"])
            after = _get(s)
            print(f"AFTER   M4.D in0: status={after['status']} enable_gd={after['enable_gd']} "
                  f"gate={after['gate']} delay={after['delay']} invert={after['invert']}")

            ok = (after["enable_gd"] == enable_gd and after["gate"] == gate
                  and after["delay"] == args.delay and after["status"] == before["status"])
            print("READBACK OK" if ok else "!! READBACK MISMATCH — do not start a run")
            return 0 if ok else 1
    except BoardBusyError as e:
        print(f"!! board in use by another process: {e}", file=sys.stderr)
        print("   aborted — wait for it to finish, do NOT force.", file=sys.stderr)
        return 2
    except (BoardWedgedError, BoardQuarantinedError) as e:
        print(f"!! board unavailable: {e}", file=sys.stderr)
        print("   aborted — leave the board alone to rest; do NOT start a run.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
