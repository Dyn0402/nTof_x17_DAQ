#!/usr/bin/env python3
"""v2 (2026-07-17) two-set beam-normalized wall-vs-scint delay scan at M3 G&D.

Supersedes timing_task3_scan.py for tonight's post-FIFO rescan. Same physics
(sweep the RELATIVE wall/scint delay per sector, window at M3 input G&D, both
legs gated to --gate; positive = wall delayed, negative = scint delayed; M5
scalers count; beam-gated; fixed-reference two-set normalization), but:

  * BOARD HYGIENE: one board_session on M3 held for the whole scan (v1's
    m3_timing_lib.connect() raw-SDK + per-point reconnect churn is retired);
    per-count M5 sessions via open_session (BoardBusy retries so poll_modules
    can interleave). NOTE: while this runs, poll_modules' per-subrun snapshot
    will skip M3 (we hold its lock) -- expected, fault-isolated.
  * --center C: the plastics now arrive ~16 ns (cable) + FIFO insertion delay
    LATE, so the plateau center should sit at wall-delay ~ +16..+30 ns.
    Grid = C + {-30,-20,-15,-10,-5,0,+5,+10,+15,+20,+30,+45}, with the C point
    retaken at start/mid/end (drift bracket). HELD sectors sit at --hold-delay
    (default = C, NOT 0: a delay-0 reference could be far down the plateau
    edge and would divide by a small noisy number).
  * --probe: quick un-normalized locator FIRST -- ALL four sectors swept
    together over {0,10,15,20,25,30,40,50} (or --delays), C/sqrt(wall*scint)
    as the beam-drift cross-check. Use it to pick --center.
  * --apply D: no scan; set wall-delay D / scint-delay 0 / gate 20 on ALL
    sectors, verify, take one sanity count, exit. This is how the analyzed
    result is committed.
  * --restore-delay: wall delay left on ALL sectors at exit (gate always
    restored to 20 ns). Default 0 = historical baseline.

Usage (pass negative --delays lists as --delays="-20,..." -- argparse rejects
the space-separated form):
  .venv/bin/python n1081b/timing_delay_scan_v2.py --probe [--probe-count 30]
  .venv/bin/python n1081b/timing_delay_scan_v2.py --center 20 [--count 60]
      [--stamp v2run1] [--gate 20] [--hold-delay N] [--restore-delay N]
  .venv/bin/python n1081b/timing_delay_scan_v2.py --apply 20
  analyze: .venv/bin/python n1081b/analyze_timing_scan.py \\
      n1081b/snapshots/timing_scan_<stamp>.json [out.png]
"""
import argparse
import json
import math
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from n1081b_sdk import N1081B  # noqa: E402
from rate_scan_2d import open_session, log  # noqa: E402  (board_session + retries)

M3_IP = "192.168.10.242"
M5_IP = "192.168.10.244"
SECTIONS = ["SEC_A", "SEC_B", "SEC_C", "SEC_D"]
WALL_CH, SCINT_CH = 0, 1
SEC_IDX = {"SEC_A": 0, "SEC_B": 1, "SEC_C": 2, "SEC_D": 3}
SETS = [
    {"name": "set1", "hold": ["SEC_C", "SEC_D"], "sweep": ["SEC_A", "SEC_B"]},
    {"name": "set2", "hold": ["SEC_A", "SEC_B"], "sweep": ["SEC_C", "SEC_D"]},
]
RESTORE_GATE_NS = 20
SETTLE_S = 2
STALE_S = 90
BEAM_POLL_S = 15
MAX_RETAKE = 2

REPO = os.path.dirname(_HERE)
BEAM_STATE = os.path.join(REPO, "config", "beam_state.json")

PROBE_DELAYS = [0, 10, 15, 20, 25, 30, 40, 50]
CENTER_OFFSETS = [-30, -20, -15, -10, -5, 0, 5, 10, 15, 20, 30, 45]


def beam_state():
    try:
        with open(BEAM_STATE) as f:
            d = json.load(f)
        d["_age_s"] = time.time() - os.path.getmtime(BEAM_STATE)
        d["_ok"] = bool(d.get("beam_on")) and d["_age_s"] < STALE_S
        return d
    except Exception as e:  # noqa: BLE001
        return {"_ok": False, "_age_s": None, "_err": repr(e)}


def wait_for_beam():
    waited = 0
    while True:
        b = beam_state()
        if b.get("_ok"):
            return b
        log(f"    [beam] waiting (beam_on={b.get('beam_on')} "
            f"age={None if b.get('_age_s') is None else round(b['_age_s'])}s); waited {waited}s")
        time.sleep(BEAM_POLL_S)
        waited += BEAM_POLL_S


def _sec(name):
    return getattr(N1081B.Section, name)


def set_gd(s3, ch, gate, delay, sections):
    """Set G&D on input `ch` of the given M3 sections, preserving status/invert;
    read-back verified."""
    ok = True
    for sname in sections:
        sec = _sec(sname)
        c = s3.call("get_input_channel_configuration", sec, ch)["data"]
        s3.call("set_input_channel_configuration", sec, ch,
                c["status"], True, gate, delay, c["invert"])
        r = s3.call("get_input_channel_configuration", sec, ch)["data"]
        good = r["enable_gd"] and r["gate"] == gate and r["delay"] == delay
        ok = ok and good
        if not good:
            log(f"    !! {sname} in-ch{ch} verify FAILED: "
                f"gd={r['enable_gd']} gate={r['gate']} delay={r['delay']}")
    return ok


def set_signed(s3, sections, D, gate):
    """Signed delay D on `sections`: D>0 wall delayed by D, D<0 scint delayed by -D."""
    dwall = D if D > 0 else 0
    dscint = -D if D < 0 else 0
    ok = set_gd(s3, WALL_CH, gate, dwall, sections)
    ok = set_gd(s3, SCINT_CH, gate, dscint, sections) and ok
    return ok, dwall, dscint


def read_m5(count):
    """Per-section per-channel Hz from M5 scaler deltas (fresh session per count)."""
    s = open_session(M5_IP, "timing_scan_v2 count")
    try:
        def snap():
            out = {}
            for name in ("SEC_A", "SEC_B", "SEC_C"):
                res = s.call("get_function_results", _sec(name))
                ctrs = ((res or {}).get("data") or {}).get("counters") or []
                out[name] = [c.get("value") for c in ctrs]
            return out
        r0 = snap()
        t0 = time.time()
        time.sleep(count)
        r1 = snap()
        dt = time.time() - t0
        rates = {}
        for name in r0:
            n = min(len(r0[name]), len(r1[name]), 4)
            rates[name] = {i: round((r1[name][i] - r0[name][i]) / dt, 3) for i in range(n)}
        return rates
    finally:
        s.__exit__(None, None, None)


def sector_rates(r):
    out = {}
    for sn, i in SEC_IDX.items():
        out[sn] = {"wall": r["SEC_A"].get(i, 0.0),
                   "scint": r["SEC_B"].get(i, 0.0),
                   "C": r["SEC_C"].get(i, 0.0)}
    return out


def counted_point(count):
    """Beam-gated M5 count with retakes; returns (sector_rates, b0, b1, stable)."""
    for attempt in range(MAX_RETAKE + 1):
        b0 = wait_for_beam()
        time.sleep(SETTLE_S)
        r = read_m5(count)
        b1 = beam_state()
        stable = bool(b0.get("_ok") and b1.get("_ok"))
        if stable or attempt == MAX_RETAKE:
            return sector_rates(r), b0, b1, stable
        log(f"    beam unstable (attempt {attempt + 1}) -- retaking")


def run_probe(s3, delays, count, gate, out_path):
    """ALL sectors together; no held reference; C/sqrt(wall*scint) cross-check."""
    results = {"probe": True, "gate_ns": gate, "count_s": count,
               "delays": delays, "rows": []}
    log(f"=== PROBE: all sectors, gate={gate}, delays {delays}, {count}s/pt ===")
    for k, D in enumerate(delays):
        ok, dw, ds = set_signed(s3, SECTIONS, D, gate)
        if not ok:
            log(f"  !! D={D}: verify FAILED -- skipping")
            continue
        sr, b0, b1, stable = counted_point(count)
        row = {"signed_delay": D, "wall_delay": dw, "scint_delay": ds,
               "sectors": sr, "beam_stable": stable,
               "beam_e10_after": b1.get("last_pulse_e10")}
        results["rows"].append(row)
        with open(out_path, "w") as f:
            json.dump(results, f, indent=1)
        cells = []
        for sn in SECTIONS:
            C, w, s_ = sr[sn]["C"], sr[sn]["wall"], sr[sn]["scint"]
            norm = C / math.sqrt(w * s_) if w > 0 and s_ > 0 else float("nan")
            cells.append(f"{sn[-1]}:C={C:5.1f} n={norm:.4f}")
        log(f"  [probe {k + 1}/{len(delays)}] D={D:+4d} " + "  ".join(cells)
            + ("" if stable else "  (beam UNSTABLE)"))
    best = {}
    for sn in SECTIONS:
        rows = [(r["sectors"][sn]["C"], r["signed_delay"]) for r in results["rows"]]
        if rows:
            best[sn[-1]] = max(rows)[1]
    log(f"probe raw-C argmax per sector: {best}  -> pick --center accordingly")
    return results


def run_scan(s3, delays, hold_delay, count, gate, out_path):
    results = {"count_s": count, "settle_s": SETTLE_S, "gate_ns": gate,
               "hold_delay": hold_delay, "signed_delays": delays,
               "sets": [], "sec_idx": SEC_IDX}
    log(f"=== two-set scan: gate={gate}, hold_delay={hold_delay:+d}, "
        f"{len(SETS)}x{len(delays)} pts x {count}s -> {out_path} ===")
    for S in SETS:
        hold, sweep_secs = S["hold"], S["sweep"]
        log(f"\n--- {S['name']}: hold {hold} at {hold_delay:+d} (beam ref), sweep {sweep_secs} ---")
        ok, _, _ = set_signed(s3, hold, hold_delay, gate)
        if not ok:
            log("  !! failed to set held sectors -- skipping set")
            continue
        set_rows = []
        results["sets"].append({"name": S["name"], "hold": hold,
                                "sweep": sweep_secs, "rows": set_rows})
        for k, D in enumerate(delays):
            ok, dw, ds = set_signed(s3, sweep_secs, D, gate)
            if not ok:
                log(f"  !! D={D}: swept-leg verify FAILED -- skipping point")
                continue
            sr, b0, b1, stable = counted_point(count)
            cref = sum(sr[h]["C"] for h in hold)
            row = {"signed_delay": D, "wall_delay": dw, "scint_delay": ds,
                   "sectors": sr, "c_ref": cref, "beam_stable": stable,
                   "beam_e10_before": b0.get("last_pulse_e10"),
                   "beam_e10_after": b1.get("last_pulse_e10"),
                   "pulses_10min": b1.get("pulses_10min")}
            set_rows.append(row)
            with open(out_path, "w") as f:
                json.dump(results, f, indent=1)
            cells = []
            for sn in sweep_secs:
                C = sr[sn]["C"]
                norm = C / cref if cref > 0 else float("nan")
                cells.append(f"{sn[-1]}:C={C:5.1f} C/ref={norm:.3f}")
            log(f"  [{S['name']} {k + 1:2d}/{len(delays)}] D={D:+4d} "
                f"(w+{dw},s+{ds}) ref={cref:5.1f} e10={b1.get('last_pulse_e10')}  "
                + "  ".join(cells) + ("" if stable else "  (beam UNSTABLE)"))
    return results


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--count", type=int, default=60)
    ap.add_argument("--stamp", default="v2run1")
    ap.add_argument("--gate", type=int, default=20)
    ap.add_argument("--center", type=int, default=None,
                    help="expected plateau center (wall-delay ns); builds the grid")
    ap.add_argument("--hold-delay", type=int, default=None,
                    help="held-sector delay (default = --center, else 0)")
    ap.add_argument("--delays", default=None,
                    help="explicit comma list of signed delays (overrides --center)")
    ap.add_argument("--probe", action="store_true",
                    help="coarse all-sector locator, no normalization")
    ap.add_argument("--probe-count", type=int, default=30)
    ap.add_argument("--restore-delay", type=int, default=0,
                    help="wall delay left on ALL sectors at exit (gate goes to 20)")
    ap.add_argument("--apply", type=int, default=None, metavar="D",
                    help="just set wall-delay D / gate 20 on all sectors + sanity count")
    args = ap.parse_args()

    if args.delays:
        delays = [int(x) for x in args.delays.replace(" ", "").split(",") if x]
    elif args.center is not None:
        C = args.center
        offs = CENTER_OFFSETS
        mid = len(offs) // 2
        delays = ([C] + [C + o for o in offs[:mid]] + [C]
                  + [C + o for o in offs[mid:]] + [C])
    else:
        delays = PROBE_DELAYS if args.probe else \
            [0, -5, -10, -15, -20, -30, 0, 5, 10, 15, 20, 25, 30, 35, 40, 50, 60, 0]
    hold_delay = args.hold_delay if args.hold_delay is not None else (args.center or 0)

    out_path = os.path.join(_HERE, "snapshots",
                            f"timing_scan_{args.stamp}{'_probe' if args.probe else ''}.json")

    s3 = open_session(M3_IP, "timing_scan_v2 G&D")
    try:
        if args.apply is not None:
            ok, dw, ds = set_signed(s3, SECTIONS, args.apply, RESTORE_GATE_NS)
            log(f"APPLY wall-delay {args.apply:+d} gate {RESTORE_GATE_NS}: "
                + ("verified" if ok else "<<< VERIFY FAILED, check manually"))
            sr, _, b1, _ = counted_point(30)
            for sn in SECTIONS:
                log(f"  {sn}: wall={sr[sn]['wall']:.1f} scint={sr[sn]['scint']:.1f} "
                    f"C={sr[sn]['C']:.1f} Hz")
            return  # applied state is the point -- no exit restore
        try:
            if args.probe:
                run_probe(s3, delays, args.probe_count, args.gate, out_path)
            else:
                run_scan(s3, delays, hold_delay, args.count, args.gate, out_path)
        finally:
            log(f"\nRestoring ALL sectors to gate={RESTORE_GATE_NS}/wall-delay="
                f"{args.restore_delay:+d} ...")
            ok, _, _ = set_signed(s3, SECTIONS, args.restore_delay, RESTORE_GATE_NS)
            log("  restored+verified" if ok else "  <<< CHECK MANUALLY")
            log(f"Results: {out_path}")
    finally:
        s3.__exit__(None, None, None)


if __name__ == "__main__":
    main()
