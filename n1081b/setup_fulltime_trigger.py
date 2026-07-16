#!/usr/bin/env python3
"""One-time STATIC trigger setup for run_35 — full-temporal-space random scan.

Goal: sample the FULL time axis (not just the 30 ms post-beam window) with the
M6.D random pulser, alongside the usual PS/gamma-flash trigger, at a handful of
HV points with the mesh injection ON. To do that we OPEN the 30 ms veto and
throttle the pulser to ~10 % of its usual rate so the ungated stream stays a
manageable data volume.

Sets the STATIC part (function types + lemo enables + pulser period + mesh
injection). The per-sub-run modulation (which is just "keep mesh ON, keep the
pulser input live, cut Singles/Doubles at C") is applied INLINE by daq_control
from config/n1081b_scan_schedule.json['scans']['fulltimeOn'] — see
n1081b/scan_control.py. Idempotent; read-back verified.

  .243 M4.C = plain FN_OR, lemo4 ONLY  -> pulser only, 30 ms VETO OPEN by
              construction (plain OR ignores the veto line — the deliberate use
              of the m4c gotcha). Singles(lemo0)/Doubles(lemo1) excluded at the
              FUNCTION level, so they cannot leak even if an input toggles.
  .243 M4.D = FN_OR, lemos 0 + 1       -> PS/flash line OR C out.
  .243 D.in0 G&D OFF (delay 0)         -> mode-2 framing (latency 5), NOT the
              scint +1980 ns delay.
  .243 input channels: C4 + D0 + D1 status True; C0 + C1 status False.
  .245 M6.B in0: G&D gate 50, delay 1260 ns (injection optimum), status True;
              outputs 0-3 enabled (mesh injection ON — this run is mesh-ON only).
  .245 M6.D  = Poisson pulse generator, period 15 ms (~67 Hz = 10 % of the usual
              1.5 ms/667 Hz), width 100. (Do NOT use 150 ms: it silently kills
              the output — generator range limit, see RUN_MODES_2026-07 Gotchas.)

RESTORE AFTERWARD (back to the standard veto-gated random config for run_34-style
runs): re-select the real or_veto on C and restore the 1.5 ms pulser:
  .venv/bin/python n1081b/setup_run30_trigger.py

Run:  .venv/bin/python n1081b/setup_fulltime_trigger.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from n1081b_sdk import N1081B  # noqa: E402
from n1081b_session import (board_session, BoardBusyError, BoardWedgedError,  # noqa: E402
                            BoardQuarantinedError)
import trigger_mode as tm      # noqa: E402

INJECTION_DELAY_NS = 1260      # M6.B in0 mesh-injection optimum (2026-07-11)
PULSER_PERIOD_NS   = 15_000_000  # 15 ms Poisson = ~67 Hz = 10 % of the 1.5 ms rate
PULSER_WIDTH       = 100
PULSER_LEMO        = 4         # M4.C lemo of the M6.D pulser (verify_trigger_paths test 2)
WRITE_GAP_S        = 0.3       # pace config writes per the board-hygiene guardrail


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
    s = board_session(ip, purpose="setup_fulltime_trigger", min_gap_s=WRITE_GAP_S,
                      require_login=(ip != "192.168.10.245"))
    s.__enter__()   # acquire lock + connect; raises BoardBusy/Wedged/Quarantined
    return _Board(s)


def main():
    ok = True

    try:
        # ---------------- .243 (M4): functions + lemo enables ----------------
        d = connect("192.168.10.243")
        try:
            # C = plain OR(pulser only) — 30 ms veto OPEN by construction; Singles &
            # Doubles excluded at the function level (belt-and-suspenders with the
            # schedule's input-status cuts).
            d.set_section_function(N1081B.Section.SEC_C, N1081B.FunctionType.FN_OR)
            en_c = [i == PULSER_LEMO for i in range(6)]        # only lemo4
            d.configure_or(N1081B.Section.SEC_C, *en_c, False, 0)
            # D = OR(PS/flash line, C out)
            tm.set_d_or(d, [0, 1])

            fns = {s['section']: s['function_name']
                   for s in (d.get_sections_function() or {}).get('data', [])}
            st = tm.get_cd_state(d)
            c_ok = fns.get(2) == 'or' and tm.lemos_enabled(st['SEC_C']) == [PULSER_LEMO]
            d_ok = fns.get(3) == 'or' and tm.lemos_enabled(st['SEC_D']) == [0, 1]
            print(f".243 C: fn={fns.get(2)} lemos={tm.lemos_enabled(st['SEC_C'])} "
                  f"(plain OR w/ pulser only = veto OPEN) {'OK' if c_ok else '!! FAIL'}")
            print(f".243 D: fn={fns.get(3)} lemos={tm.lemos_enabled(st['SEC_D'])} "
                  f"{'OK' if d_ok else '!! FAIL'}")
            ok = ok and c_ok and d_ok

            # Input channels the schedule drives / this run relies on:
            #   C4 (pulser), D0 (PS line, G&D OFF), D1 (C out) -> status True
            #   C0 (Singles), C1 (Doubles)                     -> status False
            want = {('SEC_C', 4): True, ('SEC_D', 0): True, ('SEC_D', 1): True,
                    ('SEC_C', 0): False, ('SEC_C', 1): False}
            for (sec_name, ch), status in want.items():
                sec = getattr(N1081B.Section, sec_name)
                c = d.get_input_channel_configuration(sec, ch)['data']
                d.set_input_channel_configuration(sec, ch, status, False, c['gate'],
                                                  0, c['invert'])
                r = d.get_input_channel_configuration(sec, ch)['data']
                good = (r['status'] == status) and not r['enable_gd'] and r['delay'] == 0
                print(f".243 {sec_name} in{ch}: status={r['status']} (want {status}) "
                      f"gd={r['enable_gd']} delay={r['delay']} {'OK' if good else '!! FAIL'}")
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
                                        PULSER_WIDTH, PULSER_PERIOD_NS, True, True, True, True)
            pg = (d.get_function_configuration(sec_d) or {}).get('data') or {}
            good = pg.get('period') == PULSER_PERIOD_NS and pg.get('width') == PULSER_WIDTH
            print(f".245 D pulser: period={pg.get('period')} (want {PULSER_PERIOD_NS}) "
                  f"width={pg.get('width')} freq_type={pg.get('frequency_type')} "
                  f"{'OK' if good else '!! FAIL'}")
            ok = ok and good
        finally:
            d.close()
    except (BoardBusyError, BoardWedgedError, BoardQuarantinedError) as e:
        print(f"!! ABORTED (board unavailable): {e!r}", file=sys.stderr)
        print("   A board is held/wedged/resting; do NOT force or retry — let it rest.",
              file=sys.stderr)
        return 2

    print("\nSETUP " + ("COMPLETE — PS + ungated ~67 Hz pulser, mesh ON; "
                        "start run_35 (n1081b_scan='on')." if ok
                        else "FAILED — fix before running!"))
    print("(Restore after: n1081b/setup_run30_trigger.py -> back to veto-gated "
          "or_veto + 1.5 ms pulser.)")
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
