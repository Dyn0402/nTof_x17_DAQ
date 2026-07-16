#!/usr/bin/env python3
"""Resume systematic_threshold_scan_v3.py after the transient websocket timeout
that hit at 2026-07-15 17:24 (network blip, not a logic bug -- see HANDOFF).
Checkpointed JSON had sector A/B fully done (49/49 each, applied), sector C at
42/49 (missing the wall=+141mV row), sector D not started. Picks up exactly
there instead of re-running ~1h53min of already-good data.
"""
import json
import sys
import time

sys.path.insert(0, "n1081b")
from systematic_threshold_scan_v3 import (
    Rig, check_safe_mode, daq_alive, SECTORS, COINC_SECTORS, GATE_S, thresholds_grid,
)

OUT = "n1081b/snapshots/systematic_scan_v3_2026-07-15.json"


def log(msg):
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def measure_point(rig, sector, wt, st, dwell):
    rig.set_threshold("wall", sector, wt)
    rig.set_threshold("scint", sector, st)
    wall_hz, scint_hz = rig.measure_singles(dwell)
    coinc_on = rig.measure_coinc(dwell)
    coinc_off_all = rig.measure_coinc_offplateau(sector, dwell)
    coinc_off = coinc_off_all[sector]

    others = [s for s in SECTORS if s != sector]
    ref_coinc_sectors = [s for s in others if s in COINC_SECTORS]
    wall_ref = sum(wall_hz[s] for s in others) / len(others)
    coinc_ref_on = sum(coinc_on[s] for s in ref_coinc_sectors) / max(1, len(ref_coinc_sectors))
    coinc_ref_off = sum(coinc_off_all[s] for s in ref_coinc_sectors) / max(1, len(ref_coinc_sectors))
    drift = (coinc_ref_off / coinc_ref_on) if coinc_ref_on else 1.0
    coinc_off_corrected = round(coinc_off / drift, 3) if drift else coinc_off

    accidental_formula_hz = round(wall_hz[sector] * scint_hz[sector] * GATE_S, 6)
    true_hz_measured = round(coinc_on[sector] - coinc_off_corrected, 3)
    coinc_norm = round(coinc_on[sector] / coinc_ref_on, 4) if coinc_ref_on else None

    pt = {"wall_mv": wt, "scint_mv": st,
          "wall_hz": wall_hz[sector], "scint_hz": scint_hz[sector],
          "wall_ref_hz": round(wall_ref, 2),
          "coinc_on_hz": coinc_on[sector], "coinc_off_hz_raw": coinc_off,
          "coinc_off_hz": coinc_off_corrected, "drift_factor": round(drift, 4),
          "coinc_ref_hz": round(coinc_ref_on, 3) if coinc_ref_on else None,
          "true_hz_measured": true_hz_measured,
          "accidental_formula_hz": accidental_formula_hz,
          "coinc_norm": coinc_norm}
    log(f"    W={wt:+5d} S={st:+5d}mV  wall={wall_hz[sector]:7.1f} scint={scint_hz[sector]:7.1f}  "
        f"on={coinc_on[sector]:6.2f} off={coinc_off_corrected:6.3f}(raw {coinc_off:6.3f} "
        f"drift {drift:.2f}) true={true_hz_measured:6.2f}  norm={coinc_norm}")
    return pt


def main():
    results = json.load(open(OUT))

    log("current mode: " + check_safe_mode())
    if not daq_alive():
        raise RuntimeError("daq_control tmux session not found")

    rig = Rig()
    try:
        # --- finish sector C's missing wall=+141mV row ---
        c = results["sectors"]["C"]
        done_walls = sorted(set(p["wall_mv"] for p in c["points"]))
        missing_walls = [w for w in c["wall_grid"] if w not in done_walls]
        if missing_walls:
            log(f"  --- sector C: finishing missing wall row(s) {missing_walls} ---")
            for wt in missing_walls:
                for st in c["scint_grid"]:
                    pt = measure_point(rig, "C", wt, st, dwell=15)
                    c["points"].append(pt)
                    json.dump(results, open(OUT, "w"), indent=2)
        if "applied_wall_mv" not in c or c.get("applied_wall_mv") is None:
            best = max(c["points"], key=lambda p: p["true_hz_measured"])
            rig.set_threshold("wall", "C", best["wall_mv"])
            rig.set_threshold("scint", "C", best["scint_mv"])
            c["applied_wall_mv"] = best["wall_mv"]
            c["applied_scint_mv"] = best["scint_mv"]
            log(f"    -> applied SEC_C: wall={best['wall_mv']:+d}mV scint={best['scint_mv']:+d}mV "
                f"(max measured true_hz={best['true_hz_measured']})")
            json.dump(results, open(OUT, "w"), indent=2)

        # --- sector D: 1D singles sweeps (never started) ---
        if "sector_d_singles" not in results:
            log("  --- sector D: 1D singles sweeps (no coincidence tap) ---")
            d_points = {"wall": [], "scint": []}
            for board in ("wall", "scint"):
                base = rig.get_threshold(board, "D")
                grid = thresholds_grid(base, 7)
                for t in grid:
                    rig.set_threshold(board, "D", t)
                    wall_hz, scint_hz = rig.measure_singles(15)
                    d_points[board].append({"threshold_mv": t, "wall_hz": wall_hz["D"], "scint_hz": scint_hz["D"]})
                    log(f"    D {board} T={t:+5d}mV  wall={wall_hz['D']:7.1f}Hz scint={scint_hz['D']:7.1f}Hz")
                rig.set_threshold(board, "D", base)
            results["sector_d_singles"] = d_points
            json.dump(results, open(OUT, "w"), indent=2)

        log(f"wrote {OUT}")
    finally:
        rig.close()
        log("final mode: " + check_safe_mode())
        log("DAQ alive: " + str(daq_alive()))


if __name__ == "__main__":
    sys.exit(main())
