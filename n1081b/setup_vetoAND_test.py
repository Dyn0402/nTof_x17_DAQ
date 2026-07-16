#!/usr/bin/env python3
"""Configure the transparent-AND veto test (HANDOFF_2026-07-13 §3) on M4 (.243).

C = AND(lemo4 pulser, lemo5 veto-line INVERTED)  -> fires only in the 30 ms
    post-PS enable window (line LOW = enable, so invert at the input).
D = OR(lemo1 = C out) only — no flash line, clean gated-pulser sample.

--anti flips C.in5 back to invert=False (pulser passes OUTSIDE windows).
Every write is read-back verified; exits nonzero on any mismatch.
"""
import argparse
import os
import sys

if "__file__" in globals():
    _HERE = os.path.dirname(os.path.abspath(__file__))
    if _HERE not in sys.path:
        sys.path.insert(0, _HERE)

import trigger_mode as tm
from n1081b_sdk import N1081B
from n1081b_session import (board_session, BoardBusyError, BoardWedgedError,
                            BoardQuarantinedError)

SEC_C = N1081B.Section.SEC_C
SEC_D = N1081B.Section.SEC_D
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
    # .245 (M6) is old-firmware: login() returns False but get/set still work, so
    # tolerate a failed login there (matches poll_modules / scan_watcher). Other
    # boards keep the default require_login=True (raise on login failure).
    s = board_session(ip, purpose="setup_vetoAND_test", min_gap_s=WRITE_GAP_S,
                      require_login=(ip != "192.168.10.245"))
    s.__enter__()   # acquire lock + connect; raises BoardBusy/Wedged/Quarantined
    return _Board(s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--anti", action="store_true",
                    help="anti-window variant: C.in5 NOT inverted")
    args = ap.parse_args()
    invert_veto = not args.anti

    ok = True
    try:
        d = connect()
        try:
            # ---- C = AND(lemo4, lemo5) ----
            fn = {s['section']: s['function_name']
                  for s in (d.get_sections_function() or {}).get('data', [])}
            print(f"before: C={fn.get(2)} D={fn.get(3)}")
            if fn.get(2) != 'and':
                d.set_section_function(SEC_C, N1081B.FunctionType.FN_AND)
            d.configure_and(SEC_C, False, False, False, False, True, True, False, 0)

            # C.in4 pulser: enabled, no G&D, no invert
            c4 = d.get_input_channel_configuration(SEC_C, 4)['data']
            d.set_input_channel_configuration(SEC_C, 4, True, False, c4['gate'], 0, False)
            # C.in5 veto line: enabled, no G&D (level must stay a level), invert per mode
            c5 = d.get_input_channel_configuration(SEC_C, 5)['data']
            d.set_input_channel_configuration(SEC_C, 5, True, False, c5['gate'], 0, invert_veto)

            # ---- D = OR(lemo1 = C out) only ----
            tm.set_d_or(d, [1])
            d1 = d.get_input_channel_configuration(SEC_D, 1)['data']
            d.set_input_channel_configuration(SEC_D, 1, True, False, d1['gate'], 0, False)

            # ---- verify ----
            fn = {s['section']: s['function_name']
                  for s in (d.get_sections_function() or {}).get('data', [])}
            c_fc = (d.get_function_configuration(SEC_C) or {}).get('data') or {}
            d_fc = (d.get_function_configuration(SEC_D) or {}).get('data') or {}
            c_en = tm.lemos_enabled(c_fc)
            d_en = tm.lemos_enabled(d_fc)
            r4 = d.get_input_channel_configuration(SEC_C, 4)['data']
            r5 = d.get_input_channel_configuration(SEC_C, 5)['data']
            rd1 = d.get_input_channel_configuration(SEC_D, 1)['data']

            checks = [
                ("C function = and", fn.get(2) == 'and'),
                ("C lemos = [4,5]", c_en == [4, 5]),
                ("D function = or", fn.get(3) == 'or'),
                ("D lemos = [1]", d_en == [1]),
                ("C.in4 on, gd off, no inv",
                 r4['status'] and not r4['enable_gd'] and not r4['invert'] and r4['delay'] == 0),
                (f"C.in5 on, gd off, invert={invert_veto}",
                 r5['status'] and not r5['enable_gd'] and r5['invert'] == invert_veto
                 and r5['delay'] == 0),
                ("D.in1 on, gd off, no inv",
                 rd1['status'] and not rd1['enable_gd'] and not rd1['invert']
                 and rd1['delay'] == 0),
            ]
            for name, good in checks:
                print(f"  {'OK  ' if good else 'FAIL'} {name}")
                ok = ok and good
        finally:
            d.close()

        # ---- .245 pulser sanity: Poisson, period 1.5 ms, width 100 ----
        p = connect("192.168.10.245")
        try:
            pf = (p.get_function_configuration(SEC_D) or {}).get('data') or {}
            good = (pf.get('frequency_type') == 1 and pf.get('period') == 1500000
                    and pf.get('width') == 100)
            print(f"  {'OK  ' if good else 'FAIL'} .245 D pulser: "
                  f"freq_type={pf.get('frequency_type')} period={pf.get('period')} "
                  f"width={pf.get('width')}")
            ok = ok and good
        finally:
            p.close()
    except (BoardBusyError, BoardWedgedError, BoardQuarantinedError) as e:
        print(f"!! ABORTED (board unavailable): {e!r}", file=sys.stderr)
        print("   A board is held/wedged/resting; do NOT force or retry — let it rest.",
              file=sys.stderr)
        sys.exit(2)

    print("ALL OK" if ok else "CONFIG FAILED — do not take data")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
