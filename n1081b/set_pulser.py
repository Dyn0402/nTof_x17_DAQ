#!/usr/bin/env python3
"""Configure the M6.D (.245) random pulse generator — the flash_random random trigger.

Design (RUN_MODES_2026-07.md Mode 2): Poisson, period 1.5 ms (1500000 ns), width 100,
all four outputs enabled. Through the 30 ms N93B gate this averages ~5-6 Hz of DREAM
triggers. The pulser feeds M4.C lemo4; enable it there via `trigger_mode.py flash_random`.

Goes through n1081b_session (mandatory). NOTE: .245 runs old firmware and serves
get/set with login disabled -> require_login=False. Read-back verified.

⚠ Do NOT set period >= 150 ms (150000000 ns): the generator silently kills its output
above its range limit (RUN_MODES §Gotchas). Design is 1.5 ms.

Usage:
  set_pulser.py                       # design: Poisson, period 1500000 ns, width 100
  set_pulser.py --period 1500000 --width 100
  set_pulser.py --show
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

M6_IP = "192.168.10.245"
SEC = N1081B.Section.SEC_D
WRITE_GAP_S = 0.3
KILL_PERIOD_NS = 150_000_000  # >= this silently kills the generator output


def _get(s):
    return (s.call("get_function_configuration", SEC) or {}).get("data") or {}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--period", type=int, default=1_500_000, help="pulser period ns (default 1500000 = 1.5 ms)")
    ap.add_argument("--width", type=int, default=100, help="pulse width (default 100)")
    ap.add_argument("--show", action="store_true", help="print current M6.D pulser config and exit")
    args = ap.parse_args()

    try:
        with board_session(M6_IP, purpose="set M6.D random pulser",
                           min_gap_s=WRITE_GAP_S, require_login=False) as s:
            before = _get(s)
            print(f"BEFORE  M6.D pulser: freq_type={before.get('frequency_type')} "
                  f"period={before.get('period')} width={before.get('width')}")
            if args.show:
                return 0
            if args.period >= KILL_PERIOD_NS:
                sys.exit(f"refusing period {args.period} ns >= {KILL_PERIOD_NS} (silently kills output)")

            s.call("configure_pulse_generator", SEC, N1081B.StatisticMode.STAT_POISSON,
                   args.width, args.period, True, True, True, True)
            after = _get(s)
            print(f"AFTER   M6.D pulser: freq_type={after.get('frequency_type')} "
                  f"period={after.get('period')} width={after.get('width')}")

            ok = (after.get("period") == args.period and after.get("width") == args.width
                  and after.get("frequency_type") == 1)  # 1 = Poisson
            print("READBACK OK (Poisson)" if ok else "!! READBACK MISMATCH — do not start a run")
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
