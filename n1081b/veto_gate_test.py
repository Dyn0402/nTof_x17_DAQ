#!/usr/bin/env python3
"""Definitive M4.C veto-gate behavior test (2026-07-11, item 2 follow-up).

Setup: D = OR(lemo0)  -> D.out0 = PS/flash line (0.29 Hz timing reference)
       C = or_veto(lemo4) -> C.out0 = pulser (667 Hz) through the veto
LA triggers on C.out0 OR D.out0 (rising); each capture is host-stamped and
classified by which output pulsed. If the N93B 30 ms gate works, C-captures
occur ONLY within ~30 ms after a D (PS) capture and the C rate is ~0.3-0.6 Hz;
if the pulser passes ungated, the C rate is re-arm-limited (~5-7 Hz) with
uniform timing. Also records the C.in5 (veto line) level in each frame class.

Restores scint(both) + D=OR(1)?  NO - leaves flash-mode D; caller restores.
"""
import time

from n1081b_sdk import N1081B
import trigger_mode as tm

MODE = N1081B.LogicAnalyzerTriggerMode
EDGE = N1081B.LogicAnalyzerTriggerEdge
DURATION = 240


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


d = tm.connect()
try:
    tm.set_d_or(d, [0])
    tm.set_c_or_veto(d, [4])
    st = tm.get_cd_state(d)
    print("C lemos:", tm.lemos_enabled(st["SEC_C"]), " D lemos:", tm.lemos_enabled(st["SEC_D"]))

    flags = [False] * 40
    flags[2 * 10 + 6 + 0] = True   # C.out0
    flags[3 * 10 + 6 + 0] = True   # D.out0
    d.set_logic_analyzer_trigger(MODE.LA_TRIGGER_OFF, MODE.LA_TRIGGER_OR,
                                 EDGE.LA_EDGE_RISING, *flags)

    caps = []   # (host_t, is_c, is_d, c_in5_frac_high)
    t0 = time.time()
    while time.time() - t0 < DURATION:
        d.start_logic_analyzer()
        ta = time.time()
        data = None
        while time.time() - ta < 8:
            r = d.get_logic_analyzer_data()
            dd = r.get("data") if isinstance(r, dict) else None
            if isinstance(dd, dict) and dd.get("inputs"):
                data = dd
                break
            time.sleep(0.04)
        if data is None:
            continue
        t = time.time()
        outs = data["outputs"]
        is_c = bool(pulses(outs[2 * 4 + 0]))
        is_d = bool(pulses(outs[3 * 4 + 0]))
        in5 = data["inputs"][2 * 6 + 5]
        frac5 = sum(1 for b in in5 if b) / max(len(in5), 1)
        caps.append((t, is_c, is_d, frac5))

    c_t = [c[0] for c in caps if c[1]]
    d_t = [c[0] for c in caps if c[2] and not c[1]]
    both = sum(1 for c in caps if c[1] and c[2])
    span = caps[-1][0] - caps[0][0] if len(caps) > 1 else 1
    print(f"\n{len(caps)} captures in {span:.0f} s: C-type {len(c_t)} "
          f"({len(c_t)/span:.2f} Hz), D-type {len(d_t)} ({len(d_t)/span:.2f} Hz), both {both}")

    # Delta-t from each C capture to the most recent D capture
    if c_t and d_t:
        dts = []
        for tc in c_t:
            prev = [td for td in d_t if td <= tc]
            if prev:
                dts.append(tc - prev[-1])
        in30 = sum(1 for x in dts if x <= 0.035)
        print(f"C-captures with a prior D reference: {len(dts)}; "
              f"within 35 ms of last PS: {in30} ({in30/max(len(dts),1):.0%})")
        dts_s = sorted(dts)
        print("dt quartiles (s):", [round(dts_s[int(q*(len(dts_s)-1))], 3) for q in (0, .25, .5, .75, 1)])

    for label, sel in (("C-type", lambda c: c[1]), ("D-type", lambda c: c[2] and not c[1])):
        f = sorted(c[3] for c in caps if sel(c))
        if f:
            print(f"C.in5 frac_high during {label} frames: med {f[len(f)//2]:.3f} "
                  f"min {f[0]:.3f} max {f[-1]:.3f}")

    verdict = ("GATED (veto working: pulser only after PS)"
               if c_t and len(c_t) / span < 1.5 else
               "UNGATED (pulser passes continuously)")
    print("VERDICT:", verdict)
finally:
    d.close()
