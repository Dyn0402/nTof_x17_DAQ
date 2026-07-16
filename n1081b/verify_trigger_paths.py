#!/usr/bin/env python3
"""Phase-1 trigger-path verification on M4 (.243) for the 3-mode DREAM trigger
setup (see HANDOFF: latency tuning). Sequential tests, all reversible; leaves
the board in scint(both) mode at the end.

  1. Flash-line ID:   D=OR(lemo0), LA on D.out0 -> rate should be ~beam pulse
                      rate (~0.28 Hz) and intervals ~ PS-cycle quantized.
  2. Pulser panel ID: C=OR(lemoN) for N in 2,3,4 -> LA on C.out0; the pulser
                      lemo fires steadily (~667 Hz, re-arm limited).
  3. Veto windowing:  C=or_veto(pulser lemo) -> LA re-arm loop; triggers should
                      cluster in ~30 ms bursts at the beam pulse rate.
  4. Flash correlation: scint config; LA on A.out0 (Singles) and B.out0
                      (Doubles): fraction of frames with a pulse on D.in0
                      (flash line) within the +-20 us frame.

.243 gotcha: input-trigger (mode_in) is broken -- always trigger on OUTPUTS.
Frames still contain all 24 inputs. LA: 2048 samples x 10 ns.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from n1081b_sdk import N1081B  # noqa: E402
import trigger_mode as tm      # noqa: E402

MODE = N1081B.LogicAnalyzerTriggerMode
EDGE = N1081B.LogicAnalyzerTriggerEdge

# flag layout: 4 sections x [in0..in5, out0..out3]
def out_flag_index(section, ch):
    return section * 10 + 6 + ch

def in_arr(data, section, lemo):
    return data["inputs"][section * 6 + lemo]

def set_la_out_trigger(d, section, ch, edge=None):
    flags = [False] * 40
    flags[out_flag_index(section, ch)] = True
    d.set_logic_analyzer_trigger(MODE.LA_TRIGGER_OFF, MODE.LA_TRIGGER_OR,
                                 edge or EDGE.LA_EDGE_RISING, *flags)

def la_capture(d, timeout):
    """Arm LA, poll until frame or timeout. Returns (host_time, data|None)."""
    d.start_logic_analyzer()
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = d.get_logic_analyzer_data()
        dd = r.get("data") if isinstance(r, dict) else None
        if isinstance(dd, dict) and dd.get("inputs"):
            return time.time(), dd
        time.sleep(0.05)
    return time.time(), None

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

def frac_high(bits):
    return sum(1 for b in bits if b) / max(len(bits), 1)


if __name__ != "__main__":
    raise ImportError("verify_trigger_paths.py is a script, not a library — "
                      "import set_la_out_trigger/la_capture/pulses from it only "
                      "via importlib with __name__ tricks, or copy the helpers.")

d = tm.connect()
try:
    # ---------------- 1. flash-line ID on D.in0 ----------------
    print("=== 1. Flash-line ID: D=OR(lemo0), LA on D.out0 ===", flush=True)
    tm.set_d_or(d, [0])
    print("  D lemos:", tm.lemos_enabled(tm.get_cd_state(d)["SEC_D"]))
    set_la_out_trigger(d, 3, 0)
    times, widths, in0_high = [], [], []
    t_start = time.time()
    while len(times) < 20 and time.time() - t_start < 180:
        t, data = la_capture(d, 20)
        if data is None:
            print("  (20 s arm timeout, no trigger)", flush=True)
            continue
        times.append(t)
        p = pulses(in_arr(data, 3, 0))
        widths += [w * 10 for _, w in p]
        in0_high.append(frac_high(in_arr(data, 3, 0)))
    if len(times) >= 3:
        span = times[-1] - times[0]
        ivals = [round(b - a, 2) for a, b in zip(times, times[1:])]
        print(f"  {len(times)} triggers in {span:.1f} s -> rate {(len(times)-1)/span:.3f} Hz "
              f"(beam pulse rate ~0.28 Hz)")
        print(f"  intervals s: {ivals}")
        print(f"  D.in0 pulse widths ns: {sorted(set(widths))}  frac_high med: "
              f"{sorted(in0_high)[len(in0_high)//2]:.3f}")
    else:
        print(f"  ONLY {len(times)} triggers in {time.time()-t_start:.0f} s — "
              "flash line NOT confirmed on D.in0!")

    # ---------------- 2. pulser panel ID on C ----------------
    print("=== 2. Pulser panel ID: C=OR(lemoN), LA on C.out0 ===", flush=True)
    pulser_lemo = None
    for lemo in (2, 3, 4):
        d.configure_or(N1081B.Section.SEC_C, *(i == lemo for i in range(6)), False, 0)
        set_la_out_trigger(d, 2, 0)
        hits, t0 = 0, time.time()
        w_ns = []
        while time.time() - t0 < 6:
            t, data = la_capture(d, 3)
            if data is not None:
                hits += 1
                w_ns += [w * 10 for _, w in pulses(in_arr(data, 2, lemo))]
        print(f"  lemo{lemo} (panel {lemo+1}): {hits} LA triggers in 6 s"
              + (f", input widths ns {sorted(set(w_ns))}" if w_ns else ""))
        if hits >= 3:
            pulser_lemo = lemo
            break
    print(f"  => pulser lemo = {pulser_lemo}")

    # ---------------- 3. veto windowing ----------------
    if pulser_lemo is not None:
        print("=== 3. Veto windowing: C=or_veto(pulser), LA re-arm 120 s ===", flush=True)
        tm.set_c_or_veto(d, [pulser_lemo])
        set_la_out_trigger(d, 2, 0)
        stamps, veto_high = [], []
        t0 = time.time()
        while time.time() - t0 < 120:
            t, data = la_capture(d, 10)
            if data is None:
                continue
            stamps.append(t)
            veto_high.append(frac_high(in_arr(data, 2, 5)))
        bursts = []
        for t in stamps:
            if bursts and t - bursts[-1][-1] < 0.5:
                bursts[-1].append(t)
            else:
                bursts.append([t])
        print(f"  {len(stamps)} triggers in 120 s -> {len(bursts)} bursts "
              f"({len(bursts)/120:.3f} bursts/s vs beam ~0.28 Hz)")
        print(f"  triggers/burst: {[len(b) for b in bursts]}")
        print(f"  burst spans s: {[round(b[-1]-b[0], 3) for b in bursts if len(b) > 1]}")
        if veto_high:
            print(f"  C.in5 (veto/enable) frac_high med: "
                  f"{sorted(veto_high)[len(veto_high)//2]:.3f} (expect ~1 in-window)")

    # ---------------- 4. flash correlation of Singles / Doubles ----------------
    print("=== 4. Flash correlation (D.in0 pulse within +-20 us frame) ===", flush=True)
    tm.set_c_or_veto(d, [0, 1])
    tm.set_d_or(d, [1])
    for label, sec, ch, n_want, tmo in (("Singles (A.out0)", 0, 0, 60, 5),
                                        ("Doubles (B.out0)", 1, 0, 40, 15)):
        set_la_out_trigger(d, sec, ch)
        got, with_flash, t0 = 0, 0, time.time()
        while got < n_want and time.time() - t0 < 150:
            t, data = la_capture(d, tmo)
            if data is None:
                continue
            got += 1
            if pulses(in_arr(data, 3, 0)):
                with_flash += 1
        print(f"  {label}: {got} frames, {with_flash} with flash pulse in-frame "
              f"({with_flash/max(got,1):.2%})", flush=True)
        if label.startswith("Doubles") and got == 0:
            print("    (no Doubles triggers on B.out0 -- trying B.out2 = window copy)")
            set_la_out_trigger(d, 1, 2)
            t, data = la_capture(d, 30)
            print("    B.out2 trigger:", "YES" if data is not None else "no")

    # ---------------- restore scint(both) ----------------
    print("=== restore: scint(both) ===")
finally:
    d.close()

# use the CLI path for the restore so trigger_mode.py gets an end-to-end test
import subprocess  # noqa: E402
sys.exit(subprocess.call([sys.executable, str(Path(__file__).parent / "trigger_mode.py"),
                          "scint", "--both"]))
