#!/usr/bin/env python3
"""Compare SiPM-wall (M1) singles rates at the CURRENT thresholds vs a NEW
per-sector set, back-to-back so the beam barely moves between them.

Why (2026-07-19): new SiPM calibration suggests the standing wall thresholds
(A15/B16/C15/D16 mV) sit a little low. This measures wall singles (M5 SEC_A
counter) at the current set and at NEW = A25/B35/C34/D36, and normalises each
reading by the plastic singles (M5 SEC_B), which stay pinned at the −30/−38
baseline throughout and therefore track the beam intensity in real time -- so
the comparison is beam-robust without any e10-linearity assumption.

Non-destructive: walls are restored to the current set on exit. All board
contact goes through the rate_scan_2d rig -> board_session (one process per
board, clean closes, floor guard). Safe alongside a live flash/flash_random
run (M1/M2 are not in that trigger path).

Usage:
  .venv/bin/python n1081b/wall_rate_compare.py [--reps 6] [--dwell 10]
      [--new "A:25,B:35,C:34,D:36"] [--label wallcmp] [--dry-run]
"""
import argparse
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from rate_scan_2d import (ThresholdRig, m5_counter_rates, check_safe_mode,  # noqa: E402
                          wait_for_beam, daq_alive, log, THRESHOLD_FLOOR_MV)

OUTPUT_ROOT = os.path.expanduser("~/beam_july/wall_rate_compare")
SETTLE_S = 2
SEC_IDX = {"A": 0, "B": 1, "C": 2, "D": 3}   # M5 counter channel per sector
CURRENT_WALLS = {"A": 15, "B": 16, "C": 15, "D": 16}   # documented standing set


def _spec(s):
    """'A:25,B:35,...' -> {sec: mv}, positive + floor validated (walls)."""
    d = {k.upper(): int(v) for k, v in
         (kv.split(":") for kv in s.replace(" ", "").split(","))}
    for sec, v in d.items():
        if sec not in "ABCD":
            raise SystemExit(f"bad sector '{sec}' in {s!r}")
        if v <= 0:
            raise SystemExit(f"wall threshold {v} must be positive")
        if v < THRESHOLD_FLOOR_MV:
            raise SystemExit(f"|{v}| below the {THRESHOLD_FLOOR_MV} mV floor")
    return d


def measure(tag, reps, dwell, points_path):
    """reps back-to-back dwells; return list of per-rep {walls, plastics, e10}."""
    import scint_hv_lib as shv  # sys.path prepared by the rate_scan_2d import
    rows = []
    for i in range(reps):
        wait_for_beam()
        time.sleep(SETTLE_S)
        rates, dt = m5_counter_rates(dwell, sections=("SEC_A", "SEC_B"))
        state = shv.read_beam_state()
        walls = {s: rates["SEC_A"].get(SEC_IDX[s]) for s in "ABCD"}
        plas = {s: rates["SEC_B"].get(SEC_IDX[s]) for s in "ABCD"}
        e10 = (state or {}).get("last_pulse_e10")
        rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "cond": tag, "rep": i,
               "walls_hz": walls, "plastic_hz": plas, "dwell_s": round(dt, 1),
               "e10": e10, "plastic_sum": round(sum(v for v in plas.values() if v), 1)}
        with open(points_path, "a") as f:
            f.write(json.dumps(rec) + "\n")
        log(f"  [{tag:>7} {i + 1}/{reps}] walls "
            f"A{walls['A']:.0f} B{walls['B']:.0f} C{walls['C']:.0f} D{walls['D']:.0f} Hz "
            f"| plas_sum {rec['plastic_sum']:.0f} | e10={e10}")
        rows.append(rec)
    return rows


def summarize(cur, new):
    """Per-sector mean wall Hz + plastic-normalized (wall/plastic_sum) for each
    condition, and NEW/CURRENT ratio on the beam-robust normalized value."""
    def agg(rows):
        n = len(rows)
        w = {s: sum(r["walls_hz"][s] for r in rows) / n for s in "ABCD"}
        # normalized: per-rep wall/plastic_sum, then mean (each rep beam-cancelled)
        wn = {s: sum(r["walls_hz"][s] / r["plastic_sum"] for r in rows) / n
              for s in "ABCD"}
        e10 = sum(r["e10"] for r in rows if r["e10"]) / max(1, sum(1 for r in rows if r["e10"]))
        return {"wall_hz": w, "wall_norm": wn, "e10_mean": round(e10, 1)}
    a, b = agg(cur), agg(new)
    out = {"current": a, "new": b, "per_sector": {}}
    for s in "ABCD":
        keep = b["wall_norm"][s] / a["wall_norm"][s] if a["wall_norm"][s] else None
        out["per_sector"][s] = {
            "cur_hz": round(a["wall_hz"][s], 1), "new_hz": round(b["wall_hz"][s], 1),
            "keep_frac_normed": round(keep, 4) if keep is not None else None,
            "drop_pct_normed": round((1 - keep) * 100, 1) if keep is not None else None}
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--new", default="A:25,B:35,C:34,D:36")
    ap.add_argument("--reps", type=int, default=6)
    ap.add_argument("--dwell", type=float, default=10.0)
    ap.add_argument("--label", default="wallcmp")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    new_walls = _spec(args.new)

    est = 2 * args.reps * (args.dwell + SETTLE_S + 4)
    log(f"wall compare: current {CURRENT_WALLS} vs new {new_walls}")
    log(f"{args.reps} reps/condition x2 x ~{args.dwell + SETTLE_S + 4:.0f}s -> ~{est / 60:.1f} min")
    if args.dry_run:
        return

    mode = check_safe_mode()
    log(f"trigger mode: {mode} (safe); daq_control tmux alive: {daq_alive()}")

    outdir = os.path.join(OUTPUT_ROOT, f"{time.strftime('%Y-%m-%d_%H-%M-%S')}_{args.label}")
    os.makedirs(outdir, exist_ok=True)
    points_path = os.path.join(outdir, "points.jsonl")
    log(f"output: {outdir}")

    rig = ThresholdRig()
    started_walls = dict(rig.original["wall"])
    try:
        log(f"walls at start: {started_walls} (plastics {rig.original['plastic']})")
        cur = measure("current", args.reps, args.dwell, points_path)
        for s, mv in new_walls.items():
            rig.set("wall", s, mv)
        rb = {s: rig.get(rig.m1, s) for s in "ABCD"}
        log(f"applied NEW walls, read-back {rb}")
        new = measure("new", args.reps, args.dwell, points_path)
        summ = summarize(cur, new)
        with open(os.path.join(outdir, "summary.json"), "w") as f:
            json.dump({"current_walls": started_walls, "new_walls": new_walls,
                       "reps": args.reps, "dwell_s": args.dwell,
                       "summary": summ}, f, indent=1)
        log("=== SUMMARY (wall singles, plastic-normalized) ===")
        for s in "ABCD":
            d = summ["per_sector"][s]
            log(f"  {s}: {CURRENT_WALLS[s]:>2}mV {d['cur_hz']:>7.0f}Hz  ->  "
                f"{new_walls[s]:>2}mV {d['new_hz']:>7.0f}Hz   keep "
                f"{d['keep_frac_normed']:.2f}  (−{d['drop_pct_normed']:.0f}%)")
    except KeyboardInterrupt:
        log("Interrupted -- restoring walls...")
    finally:
        try:
            for s, mv in started_walls.items():
                rig.set("wall", s, mv)
            log(f"walls restored to {started_walls}")
        except Exception as e:  # noqa: BLE001
            log(f"WARNING: wall restore incomplete ({e!r}) -- check M1 manually")
        rig.close()
    log(f"DONE -- data in {outdir}")


if __name__ == "__main__":
    main()
