#!/usr/bin/env python3
"""One-time trigger setup for run_30 (mesh on/off x flash/random/scint scans).

Sets the STATIC part of the trigger config — function types, function lemo
enables, injection delay, pulser — which the n1081b_scan_watcher then modulates
per scan block via INPUT-CHANNEL statuses / the M4.D-in0 G&D / M6.B outputs
(config/n1081b_scan_schedule.json). Idempotent; read-back verified.

  .243 M4.C = or_veto (REAL — set_section_function first, see m4c gotcha),
              function lemos 0 (Singles) + 4 (pulser); lemo5 = 30 ms veto.
  .243 M4.D = or, function lemos 0 (PS line) + 1 (C out).
  .243 input channels C0/C1/C4, D0/D1: status True (watcher toggles per block).
  .245 M6.B in0: G&D gate 50, delay 1260 ns (injection optimum), status True;
              outputs 0-3 enabled (watcher toggles for mesh Off blocks).
  .245 M6.D  = Poisson pulse generator, period 1.5 ms, width 100.

Run:  .venv/bin/python n1081b/setup_run30_trigger.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from n1081b_sdk import N1081B  # noqa: E402
from n1081b_session import (board_session, BoardBusyError, BoardWedgedError,  # noqa: E402
                            BoardQuarantinedError)
import trigger_mode as tm      # noqa: E402

INJECTION_DELAY_NS = 1260
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
    s = board_session(ip, purpose="setup_run30_trigger", min_gap_s=WRITE_GAP_S,
                      require_login=(ip != "192.168.10.245"))
    s.__enter__()   # acquire lock + connect; raises BoardBusy/Wedged/Quarantined
    return _Board(s)


def main():
    ok = True

    try:
        # ---------------- .243 (M4): functions + lemo enables ----------------
        d = connect("192.168.10.243")
        try:
            tm.set_c_or_veto(d, [0, 4])          # Singles + pulser (selects FN_OR_VETO)
            tm.set_d_or(d, [0, 1])               # PS line + C out
            st = tm.get_cd_state(d)
            fns = {s['section']: s['function_name']
                   for s in (d.get_sections_function() or {}).get('data', [])}
            c_ok = fns.get(2) == 'or_veto' and tm.lemos_enabled(st['SEC_C']) == [0, 4]
            d_ok = fns.get(3) == 'or' and tm.lemos_enabled(st['SEC_D']) == [0, 1]
            print(f".243 C: fn={fns.get(2)} lemos={tm.lemos_enabled(st['SEC_C'])} "
                  f"{'OK' if c_ok else '!! FAIL'}")
            print(f".243 D: fn={fns.get(3)} lemos={tm.lemos_enabled(st['SEC_D'])} "
                  f"{'OK' if d_ok else '!! FAIL'}")
            ok = ok and c_ok and d_ok

            # Input channels the watcher drives: make sure they start enabled/sane.
            for sec_name, chans in (('SEC_C', (0, 1, 4)), ('SEC_D', (0, 1))):
                sec = getattr(N1081B.Section, sec_name)
                for ch in chans:
                    c = d.get_input_channel_configuration(sec, ch)['data']
                    d.set_input_channel_configuration(sec, ch, True, False, c['gate'],
                                                      0, c['invert'])
                    r = d.get_input_channel_configuration(sec, ch)['data']
                    good = r['status'] and not r['enable_gd'] and r['delay'] == 0
                    print(f".243 {sec_name} in{ch}: status={r['status']} gd={r['enable_gd']} "
                          f"delay={r['delay']} {'OK' if good else '!! FAIL'}")
                    ok = ok and good
        finally:
            d.close()

        # ---------------- .245 (M6): injection + pulser ----------------
        d = connect("192.168.10.245")
        try:
            sec_b = N1081B.Section.SEC_B
            c = d.get_input_channel_configuration(sec_b, 0)['data']
            d.set_input_channel_configuration(sec_b, 0, True, True, c['gate'],
                                              INJECTION_DELAY_NS, c['invert'])
            r = d.get_input_channel_configuration(sec_b, 0)['data']
            good = r['status'] and r['enable_gd'] and r['delay'] == INJECTION_DELAY_NS
            print(f".245 B in0: status={r['status']} gd={r['enable_gd']} gate={r['gate']} "
                  f"delay={r['delay']} {'OK' if good else '!! FAIL'}")
            ok = ok and good

            for ch in range(4):
                co = d.get_output_channel_configuration(sec_b, ch)['data']
                d.set_output_channel_configuration(sec_b, ch, True, co['enable_mono'],
                                                   co['mono_value'], co['invert'])
                ro = d.get_output_channel_configuration(sec_b, ch)['data']
                print(f".245 B out{ch}: status={ro['status']} "
                      f"{'OK' if ro['status'] else '!! FAIL'}")
                ok = ok and ro['status']

            sec_d = N1081B.Section.SEC_D
            d.configure_pulse_generator(sec_d, N1081B.StatisticMode.STAT_POISSON,
                                        100, 1500000, True, True, True, True)
            pg = (d.get_function_configuration(sec_d) or {}).get('data') or {}
            good = pg.get('period') == 1500000 and pg.get('width') == 100
            print(f".245 D pulser: period={pg.get('period')} width={pg.get('width')} "
                  f"freq_type={pg.get('frequency_type')} {'OK' if good else '!! FAIL'}")
            ok = ok and good
        finally:
            d.close()
    except (BoardBusyError, BoardWedgedError, BoardQuarantinedError) as e:
        print(f"!! ABORTED (board unavailable): {e!r}", file=sys.stderr)
        print("   A board is held/wedged/resting; do NOT force or retry — let it rest.",
              file=sys.stderr)
        return 2

    print("\nSETUP " + ("COMPLETE — start the watcher, then the run." if ok
                        else "FAILED — fix before running!"))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
