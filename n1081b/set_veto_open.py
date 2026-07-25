#!/usr/bin/env python3
"""Open the M4.C 30 ms veto by making C a PLAIN OR (beam-off pulser work).

WHY: in `flash_random` mode M4.C is FN_OR_VETO, so the pulser only passes while the
PS/beam-derived veto line is LOW. With BEAM OFF that line never opens, the pulser never
reaches the TCM, and a DREAM run records 0 events at IntRate 0.00 Hz. Making C a plain
FN_OR ignores the veto line entirely (the deliberate use of the M4.C gotcha, exactly as
setup_fulltime_trigger.py does for run_35).

This is the MINIMAL version of that: it touches ONLY section C's function type and lemo
enables. It does NOT touch section D, the D.in0 PS delay, the pulser, or mesh injection —
so the only thing needing restoration afterwards is C itself.

RESTORE: `trigger_mode.py scint --singles --ps-pickup` (or any trigger_mode command) puts C
back to FN_OR_VETO — verified: trigger_mode.set_c_or_veto calls set_section_function first,
so the veto becomes live again rather than silently staying inert.

Goes through n1081b_session (mandatory). Read-back verified.

Usage:
  set_veto_open.py            # C = plain OR, lemo4 (pulser) only
  set_veto_open.py --lemos 4  # explicit
  set_veto_open.py --show     # report C's current function + enabled lemos, no writes
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from n1081b_sdk import N1081B
import trigger_mode as tm

M4_IP = "192.168.10.243"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--lemos", type=int, nargs="+", default=[4],
                    help="C lemos to enable (default: 4 = the M6.D pulser)")
    ap.add_argument("--show", action="store_true", help="report state and exit, no writes")
    args = ap.parse_args()

    s = tm.connect(M4_IP, purpose="set_veto_open")
    try:
        fn = {sec['section']: sec['function_name']
              for sec in (s.call("get_sections_function") or {}).get('data', [])}
        st = tm.get_cd_state(s)
        print(f"BEFORE  .243 C: fn={fn.get(2)} lemos={tm.lemos_enabled(st['SEC_C'])}")
        if args.show:
            return 0

        # Order matters: the function type must be selected FIRST — configure_or alone does
        # not change it (the firmware ignores the callback name). Same gotcha as or_veto.
        if fn.get(2) != 'or':
            s.call("set_section_function", N1081B.Section.SEC_C, N1081B.FunctionType.FN_OR)
        en = [i in args.lemos for i in range(6)]
        s.call("configure_or", N1081B.Section.SEC_C, *en, False, 0)

        fn = {sec['section']: sec['function_name']
              for sec in (s.call("get_sections_function") or {}).get('data', [])}
        st = tm.get_cd_state(s)
        got = tm.lemos_enabled(st['SEC_C'])
        ok = fn.get(2) == 'or' and got == sorted(args.lemos)
        print(f"AFTER   .243 C: fn={fn.get(2)} lemos={got}  (plain OR = veto OPEN)")
        print("READBACK OK" if ok else "!! READBACK FAIL")
        return 0 if ok else 1
    finally:
        s.close()


if __name__ == "__main__":
    sys.exit(main())
