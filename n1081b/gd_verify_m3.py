#!/usr/bin/env python3
"""Task 1 (adapted) — verify M3 (.242) input Gate&Delay does width-shaping AND delay,
since with M1 offline the M3 input G&D now imposes the 20 ns coincidence window itself
(replacing M1/M2 output mono thinning) as well as the Task-3 delay sweep.

Method: drive the .242 logic analyzer (its INPUT-trigger works, unlike .243). Trigger on
any scint leg (panel 2) rising, OR across the 4 sections. In each frame, for SEC_A read the
wall (panel 1 = in-ch0) and scint (panel 2 = in-ch1) pulse widths, and the wall-vs-scint
leading-edge skew (t_wall - t_scint). Compare four phases:

  A baseline           gd off both legs            expect wall~50 scint~50  skew ~0-20
  B gate wall          ch0 gd gate=20 delay=0      expect wall~20 scint~50  skew ~same
  C gate both          +ch1 gd gate=20 delay=0     expect wall~20 scint~20  skew ~same
  D delay scint +50    ch1 gate=20 delay=50        expect scint moves +50 -> skew ~= baseline-50

Only SEC_A is touched; every change is reverted in a finally block (gd off, gate/delay 0).
Run on mx17-daq:  .venv/bin/python n1081b/gd_verify_m3.py [n_frames]
"""
import sys
import time

from n1081b_sdk import N1081B

IP = "192.168.10.242"
MODE = N1081B.LogicAnalyzerTriggerMode
EDGE = N1081B.LogicAnalyzerTriggerEdge
SEC_A = N1081B.Section.SEC_A
N_FRAMES = int(sys.argv[1]) if len(sys.argv) > 1 else 60
WINDOW_NS = 500
WALL_CH, SCINT_CH = 0, 1  # SEC_A input channels: panel1=wall, panel2=scint


def connect():
    d = N1081B(IP)
    assert d.connect(), "connect failed"
    d.ws.settimeout(6)
    assert d.login("password"), "login failed"
    return d


def pulses(bits):
    out, start = [], None
    for i, v in enumerate(bits):
        if v and start is None:
            start = i
        elif not v and start is not None:
            out.append((start, i - start))
            start = None
    if start is not None:
        out.append((start, len(bits) - start))
    return out


def med(x):
    x = sorted(x)
    return x[len(x) // 2] if x else None


def set_in(d, ch, enable_gd, gate, delay):
    """Set SEC_A input `ch` G&D, preserving status/invert (read first)."""
    c = d.get_input_channel_configuration(SEC_A, ch)['data']
    d.set_input_channel_configuration(SEC_A, ch, c['status'], enable_gd, gate, delay, c['invert'])
    r = d.get_input_channel_configuration(SEC_A, ch)['data']
    ok = (r['enable_gd'] == enable_gd and r['gate'] == gate and r['delay'] == delay)
    print(f"    set in-ch{ch} gd={enable_gd} gate={gate} delay={delay}  ->  "
          f"readback gd={r['enable_gd']} gate={r['gate']} delay={r['delay']} "
          f"{'OK' if ok else '<<< VERIFY FAILED'}")
    return ok


def measure(n_frames):
    """Fresh LA connection (avoid websocket desync); return (wall_w, scint_w, skew) lists."""
    d = connect()
    flags = []
    for _ in range(4):
        flags += [False, True, False, False, False, False] + [False] * 4  # scint(panel2) rising
    d.set_logic_analyzer_trigger(MODE.LA_TRIGGER_OR, MODE.LA_TRIGGER_OFF,
                                 EDGE.LA_EDGE_RISING, *flags)
    wall_w, scint_w, skew = [], [], []
    frames = 0
    for _ in range(n_frames):
        d.start_logic_analyzer()
        t0 = time.time()
        data = None
        while time.time() - t0 < 4:
            r = d.get_logic_analyzer_data()
            dd = r.get("data") if isinstance(r, dict) else None
            if isinstance(dd, dict) and dd.get("inputs"):
                data = dd
                break
            time.sleep(0.05)
        if not data:
            continue
        frames += 1
        ins = data["inputs"]          # section A occupies input rows 0..5 (panel = idx+1)
        wall = pulses(ins[0 * 6 + WALL_CH])
        scint = pulses(ins[0 * 6 + SCINT_CH])
        for _, w in wall:
            wall_w.append(w * 10)
        for _, w in scint:
            scint_w.append(w * 10)
        for sst, _ in scint:
            near = [p for p in wall if abs((p[0] - sst) * 10) <= WINDOW_NS]
            if near:
                best = min(near, key=lambda p: abs(p[0] - sst))
                skew.append((best[0] - sst) * 10)
    d.disconnect()
    return wall_w, scint_w, skew, frames


def report(tag, n_frames):
    ww, sw, sk, fr = measure(n_frames)
    print(f"  [{tag}] frames={fr}  wall_w: n={len(ww)} med={med(ww)}  "
          f"scint_w: n={len(sw)} med={med(sw)}  skew(wall-scint): n={len(sk)} med={med(sk)}"
          + (f" min={min(sk)} max={max(sk)}" if sk else ""))
    return med(ww), med(sw), med(sk)


def main():
    print(f"=== M3 (.242) SEC_A G&D verification, {N_FRAMES} frames/phase ===")
    dc = connect()  # config connection (separate from LA connections)
    try:
        print("Phase A: baseline (gd off both legs)")
        report("A baseline", N_FRAMES)

        print("Phase B: gate WALL ch0 -> 20 ns (delay 0)")
        set_in(dc, WALL_CH, True, 20, 0)
        report("B wall=20", N_FRAMES)

        print("Phase C: gate SCINT ch1 -> 20 ns too (delay 0)")
        set_in(dc, SCINT_CH, True, 20, 0)
        report("C both=20", N_FRAMES)

        print("Phase D: delay SCINT ch1 by +50 ns (gate 20)")
        set_in(dc, SCINT_CH, True, 20, 50)
        report("D scint+50", N_FRAMES)
    finally:
        print("Restoring SEC_A in-ch0/ch1 to gd off, gate 0, delay 0 ...")
        ok0 = set_in(dc, WALL_CH, False, 0, 0)
        ok1 = set_in(dc, SCINT_CH, False, 0, 0)
        print("  restore", "OK" if (ok0 and ok1) else "<<< CHECK MANUALLY")
        dc.disconnect()


if __name__ == "__main__":
    main()
