#!/usr/bin/env python3
"""Trigger setup for run_31 (gas-change overnight watch): FLASH + UNGATED
Singles = maximal track statistics.

  .243 M4.C = plain FN_OR, lemo0 only  -> Singles with the 30 ms veto OPENED
              (cosmics + beam events both trigger; doubles/pulser excluded)
  .243 M4.D = FN_OR, lemos 0 + 1       -> PS flash line OR C out
  .243 M4.D in0 G&D = gate 100, delay 1980 ns -> the flash lands in the
              32 x 60 ns window at latency 35 (~sample 8, verified in the
              run_30 scint blocks; undelayed it would sit at sample ~48 = out)

RESTORE AFTERWARD (back to the standard veto-gated scint config):
  .venv/bin/python n1081b/trigger_mode.py scint --singles   (re-selects
  FN_OR_VETO on C), then zero D.in0's G&D:
  set_input_channel_configuration(SEC_D, 0, True, False, 100, 0, False).

Run:  .venv/bin/python n1081b/setup_run31_trigger.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from n1081b_sdk import N1081B  # noqa: E402
from n1081b_session import (board_session, BoardBusyError, BoardWedgedError,  # noqa: E402
                            BoardQuarantinedError)
import trigger_mode as tm      # noqa: E402

FLASH_DELAY_NS = 1980
WRITE_GAP_S = 0.3  # pace config writes per the board-hygiene guardrail


class _Board:
    """Adapter routing existing d.method(args) call sites AND the trigger_mode
    helpers' s.call(...) through a board_session (lock + pacing + breaker +
    guaranteed clean close). See n1081b/CLAUDE.md."""
    def __init__(self, session):
        self._s = session

    def __getattr__(self, name):
        return lambda *a, **k: self._s.call(name, *a, **k)

    def call(self, name, *a, **k):
        return self._s.call(name, *a, **k)

    def close(self):
        self._s.__exit__(None, None, None)


def connect(ip="192.168.10.243"):
    # .243 (M4) is current-firmware (login required); require_login default True keeps
    # the raise-on-login-failure behavior.
    s = board_session(ip, purpose="setup_run31_trigger", min_gap_s=WRITE_GAP_S,
                      require_login=(ip != "192.168.10.245"))
    s.__enter__()   # acquire lock + connect; raises BoardBusy/Wedged/Quarantined
    return _Board(s)


def main():
    ok = True
    try:
        d = connect("192.168.10.243")
        try:
            # C = plain OR(Singles) — veto OPEN by construction
            d.set_section_function(N1081B.Section.SEC_C, N1081B.FunctionType.FN_OR)
            d.configure_or(N1081B.Section.SEC_C,
                           True, False, False, False, False, False, False, 0)
            # D = OR(flash, C out)
            tm.set_d_or(d, [0, 1])

            fns = {s['section']: s['function_name']
                   for s in (d.get_sections_function() or {}).get('data', [])}
            st = tm.get_cd_state(d)
            c_ok = fns.get(2) == 'or' and tm.lemos_enabled(st['SEC_C']) == [0]
            d_ok = fns.get(3) == 'or' and tm.lemos_enabled(st['SEC_D']) == [0, 1]
            print(f".243 C: fn={fns.get(2)} lemos={tm.lemos_enabled(st['SEC_C'])} "
                  f"(plain OR = veto OPEN) {'OK' if c_ok else '!! FAIL'}")
            print(f".243 D: fn={fns.get(3)} lemos={tm.lemos_enabled(st['SEC_D'])} "
                  f"{'OK' if d_ok else '!! FAIL'}")
            ok = ok and c_ok and d_ok

            # D.in0: delay the PS line into the latency-35 window
            sec_d = N1081B.Section.SEC_D
            c = d.get_input_channel_configuration(sec_d, 0)['data']
            d.set_input_channel_configuration(sec_d, 0, True, True, 100,
                                              FLASH_DELAY_NS, c['invert'])
            r = d.get_input_channel_configuration(sec_d, 0)['data']
            good = r['status'] and r['enable_gd'] and r['delay'] == FLASH_DELAY_NS
            print(f".243 D in0: status={r['status']} gd={r['enable_gd']} "
                  f"gate={r['gate']} delay={r['delay']} {'OK' if good else '!! FAIL'}")
            ok = ok and good

            # C/D input channels used: make sure they are enabled, no stray G&D
            for sec_name, chans in (('SEC_C', (0,)), ('SEC_D', (1,))):
                sec = getattr(N1081B.Section, sec_name)
                for ch in chans:
                    c = d.get_input_channel_configuration(sec, ch)['data']
                    d.set_input_channel_configuration(sec, ch, True, False,
                                                      c['gate'], 0, c['invert'])
                    r = d.get_input_channel_configuration(sec, ch)['data']
                    good = r['status'] and not r['enable_gd']
                    print(f".243 {sec_name} in{ch}: status={r['status']} "
                          f"gd={r['enable_gd']} {'OK' if good else '!! FAIL'}")
                    ok = ok and good
        finally:
            d.close()
    except (BoardBusyError, BoardWedgedError, BoardQuarantinedError) as e:
        print(f"!! ABORTED (board unavailable): {e!r}", file=sys.stderr)
        print("   A board is held/wedged/resting; do NOT force or retry — let it rest.",
              file=sys.stderr)
        return 2

    print("\nSETUP " + ("COMPLETE — flash + ungated Singles live; start run_31."
                        if ok else "FAILED — fix before running!"))
    print("(Restore after: trigger_mode.py scint --singles + zero D.in0 G&D "
          "— see docstring.)")
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
