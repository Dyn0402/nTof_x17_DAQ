#!/usr/bin/env python3
"""Configure the AND(singles, pulser)+veto PACED trigger on M4 (.243).

Purpose (2026-07-20 comb-pacing test — see docs/DREAM_flash_comb_study_2026-07-19.md):
test whether GATING the scint trigger to narrow, widely-spaced windows (so no
triggers are *generated* during the readout dead time, rather than generated and
vetoed) changes the post-flash comb. Pairs with a DETERMINISTIC M6.D pulser whose
PERIOD is swept 2->20 ms (set separately with `set_pulser.py --fixed --period P
--width 500`). This script only sets the TRIGGER ROUTING; it does not touch the
pulser period/width.

Trigger wiring:
  C = AND(lemo0 = Singles [M4.A], lemo4 = M6.D pulser, lemo5 = veto-line INVERTED)
      -> a DREAM trigger only when a scint single lands inside a pulser-high window
         AND inside the 30 ms N93B post-PS enable window (line LOW = enable, so
         inverted at the input; matches m4c-veto-gate + setup_vetoAND_test).
  D = OR(lemo0 = PS/gamma-flash line, lemo1 = C out)
      -> keep the flash on D so tooth-0 still anchors every beam pulse for the
         event-time-since-flash comparison against the default (combed) runs.

RATE CAVEAT (operator already briefed): with width ~1 us / period 10 ms the AND
duty is ~1e-4, so the paced-trigger rate is singles_rate x 1e-4 (~0.1 Hz at ~1 kHz
beam-on singles). Subruns must be long, and the flash on D still fires ~11x/spill
independent of C, so tooth-0 is always populated.

NOTE the flash on D.in0 carries NO G&D delay here (enable_gd=False, delay=0): at the
paced-run latency the flash is read wherever it lands; if you want it co-framed next
to a scint pulse use set_ps_trigger_delay.py separately (not needed for the comb
timing study, which anchors on the flash saturation in-data).

Usage:
  setup_and_pace_trigger.py            # apply + read-back verify
  setup_and_pace_trigger.py --status   # print current C/D config, do not write

Mirrors setup_vetoAND_test.py's board-session hygiene; every write is read-back
verified and the script exits nonzero on any mismatch. VOLATILE .243 settings —
re-apply after any power cycle.
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

M4_IP = "192.168.10.243"
SEC_C = N1081B.Section.SEC_C
SEC_D = N1081B.Section.SEC_D
WRITE_GAP_S = 0.3  # pace config writes per the board-hygiene guardrail


class _Board:
    """Route d.method(args) call sites AND trigger_mode helpers' s.call(...) through
    one locked, clean-closing board_session (see n1081b/CLAUDE.md)."""
    def __init__(self, session):
        self._s = session

    def __getattr__(self, name):
        return lambda *a, **k: self._s.call(name, *a, **k)

    def call(self, name, *a, **k):
        return self._s.call(name, *a, **k)

    def close(self):
        self._s.__exit__(None, None, None)


def connect(ip=M4_IP):
    s = board_session(ip, purpose="setup_and_pace_trigger", min_gap_s=WRITE_GAP_S)
    s.__enter__()   # acquire lock + connect; raises BoardBusy/Wedged/Quarantined
    return _Board(s)


def status():
    d = connect()
    try:
        fn = {s['section']: s['function_name']
              for s in (d.get_sections_function() or {}).get('data', [])}
        c_fc = (d.get_function_configuration(SEC_C) or {}).get('data') or {}
        d_fc = (d.get_function_configuration(SEC_D) or {}).get('data') or {}
        print(f"  SEC_C: fn={fn.get(2)} lemos={tm.lemos_enabled(c_fc)}")
        print(f"  SEC_D: fn={fn.get(3)} lemos={tm.lemos_enabled(d_fc)}")
        for ch in (0, 4, 5):
            ci = d.get_input_channel_configuration(SEC_C, ch)['data']
            print(f"    C.in{ch}: status={ci['status']} gd={ci['enable_gd']} "
                  f"gate={ci['gate']} delay={ci['delay']} invert={ci['invert']}")
    finally:
        d.close()
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--status", action="store_true",
                    help="print current C/D config and exit (no writes)")
    args = ap.parse_args()

    try:
        if args.status:
            return status()

        ok = True
        d = connect()
        try:
            fn = {s['section']: s['function_name']
                  for s in (d.get_sections_function() or {}).get('data', [])}
            print(f"before: C={fn.get(2)} D={fn.get(3)}")

            # ---- C = AND(lemo0 singles, lemo4 pulser, lemo5 veto-inverted) ----
            if fn.get(2) != 'and':
                d.set_section_function(SEC_C, N1081B.FunctionType.FN_AND)
            # configure_and(sec, in0..in5, <?>, 0): enable lemos 0, 4, 5
            d.configure_and(SEC_C, True, False, False, False, True, True, False, 0)

            # C.in0 singles: enabled, no G&D, no invert
            c0 = d.get_input_channel_configuration(SEC_C, 0)['data']
            d.set_input_channel_configuration(SEC_C, 0, True, False, c0['gate'], 0, False)
            # C.in4 pulser: enabled, no G&D, no invert
            c4 = d.get_input_channel_configuration(SEC_C, 4)['data']
            d.set_input_channel_configuration(SEC_C, 4, True, False, c4['gate'], 0, False)
            # C.in5 veto line: enabled, no G&D (level must stay a level), INVERT (LOW=enable)
            c5 = d.get_input_channel_configuration(SEC_C, 5)['data']
            d.set_input_channel_configuration(SEC_C, 5, True, False, c5['gate'], 0, True)

            # ---- D = OR(lemo0 flash, lemo1 = C out) ----
            tm.set_d_or(d, [0, 1])
            d0 = d.get_input_channel_configuration(SEC_D, 0)['data']
            d.set_input_channel_configuration(SEC_D, 0, True, False, d0['gate'], 0, False)
            d1 = d.get_input_channel_configuration(SEC_D, 1)['data']
            d.set_input_channel_configuration(SEC_D, 1, True, False, d1['gate'], 0, False)

            # ---- verify ----
            fn = {s['section']: s['function_name']
                  for s in (d.get_sections_function() or {}).get('data', [])}
            c_en = tm.lemos_enabled((d.get_function_configuration(SEC_C) or {}).get('data') or {})
            d_en = tm.lemos_enabled((d.get_function_configuration(SEC_D) or {}).get('data') or {})
            r0 = d.get_input_channel_configuration(SEC_C, 0)['data']
            r4 = d.get_input_channel_configuration(SEC_C, 4)['data']
            r5 = d.get_input_channel_configuration(SEC_C, 5)['data']
            rd0 = d.get_input_channel_configuration(SEC_D, 0)['data']
            rd1 = d.get_input_channel_configuration(SEC_D, 1)['data']

            checks = [
                ("C function = and", fn.get(2) == 'and'),
                ("C lemos = [0,4,5]", c_en == [0, 4, 5]),
                ("D function = or", fn.get(3) == 'or'),
                ("D lemos = [0,1]", d_en == [0, 1]),
                ("C.in0 singles on, gd off, no inv",
                 r0['status'] and not r0['enable_gd'] and not r0['invert'] and r0['delay'] == 0),
                ("C.in4 pulser on, gd off, no inv",
                 r4['status'] and not r4['enable_gd'] and not r4['invert'] and r4['delay'] == 0),
                ("C.in5 veto on, gd off, INVERTED",
                 r5['status'] and not r5['enable_gd'] and r5['invert'] and r5['delay'] == 0),
                ("D.in0 flash on, gd off, no inv",
                 rd0['status'] and not rd0['enable_gd'] and not rd0['invert'] and rd0['delay'] == 0),
                ("D.in1 C-out on, gd off, no inv",
                 rd1['status'] and not rd1['enable_gd'] and not rd1['invert'] and rd1['delay'] == 0),
            ]
            for name, good in checks:
                print(f"  {'OK  ' if good else 'FAIL'} {name}")
                ok = ok and good
        finally:
            d.close()

        # ---- .245 pulser sanity: report only (period is swept via set_pulser --fixed) ----
        p = connect("192.168.10.245")
        try:
            pf = (p.get_function_configuration(SEC_D) or {}).get('data') or {}
            ft = pf.get('frequency_type')
            print(f"  INFO .245 M6.D pulser: freq_type={ft} "
                  f"({'DETERMINISTIC' if ft == 0 else 'Poisson' if ft == 1 else '?'}) "
                  f"period={pf.get('period')} width={pf.get('width')}")
            if ft != 0:
                print("       ^ NOTE: set DETERMINISTIC for the paced test: "
                      "set_pulser.py --fixed --period <ns> --width 500")
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
