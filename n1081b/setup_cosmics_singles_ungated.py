#!/usr/bin/env python3
"""UNGATED scint Singles for COSMICS (beam off).

With no beam there are no PS pulses, so the N93B 30 ms veto window never opens
and a normal veto-gated scint trigger (`trigger_mode.py scint ...`) would be
vetoed ~100 % of the time (RUN_MODES_2026-07.md Mode 3 §Veto). For cosmics we
therefore OPEN the veto by making M4.C a *plain* OR (veto input inert), exactly
as the run_31 cosmic+beam run did (setup_run31_trigger.py), but without the
flash line (no beam):

  .243 M4.C = plain FN_OR, lemo0 only  -> Singles, veto OPEN (ungated)
  .243 M4.D = FN_OR, lemo1 only        -> C out (flash line off; no beam)

RESTORE to the standard veto-gated scint trigger when beam returns:
  .venv/bin/python n1081b/trigger_mode.py scint --doubles   (re-selects
  FN_OR_VETO on C so the 30 ms gate is active again). This script does NOT
  touch any input G&D, so nothing else needs undoing.

Run:  .venv/bin/python n1081b/setup_cosmics_singles_ungated.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from n1081b_sdk import N1081B  # noqa: E402
import trigger_mode as tm      # noqa: E402


def main():
    d = tm.connect(purpose="setup_cosmics_singles_ungated")
    try:
        # C = plain OR(Singles = lemo0) -> veto OPEN by construction (ungated).
        d.set_section_function(N1081B.Section.SEC_C, N1081B.FunctionType.FN_OR)
        d.configure_or(N1081B.Section.SEC_C,
                       True, False, False, False, False, False, False, 0)
        # D = OR(lemo1 = C out); flash line (lemo0) off.
        tm.set_d_or(d, [1])

        fns = {s['section']: s['function_name']
               for s in (d.get_sections_function() or {}).get('data', [])}
        st = tm.get_cd_state(d)
        c_ok = fns.get(2) == 'or' and tm.lemos_enabled(st['SEC_C']) == [0]
        d_ok = fns.get(3) == 'or' and tm.lemos_enabled(st['SEC_D']) == [1]
        print(f".243 C: fn={fns.get(2)} lemos={tm.lemos_enabled(st['SEC_C'])} "
              f"(plain OR = veto OPEN) {'OK' if c_ok else '!! FAIL'}")
        print(f".243 D: fn={fns.get(3)} lemos={tm.lemos_enabled(st['SEC_D'])} "
              f"{'OK' if d_ok else '!! FAIL'}")
        return 0 if (c_ok and d_ok) else 1
    finally:
        d.close()


if __name__ == '__main__':
    sys.exit(main())
