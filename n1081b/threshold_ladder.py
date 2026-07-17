#!/usr/bin/env python3
"""1D threshold -> singles-rate ladders on M1 (SiPM walls) and M2 (plastics).

Why (2026-07-17 night): the trigger front-end changed today --
  * M2 plastics now arrive via linear fan-in/fan-out (no more BNC split):
    ~2x amplitude, so every old plastic threshold is stale by ~2x in signal
    terms (old -15 mV ~= new -30 mV);
  * module zeros were re-adjusted, but M2 D1 is BROKEN with a baseline
    ~-15 mV low -> expect a SEC_D noise wall near -(15+10) = -25 mV
    (rate collapse / inverted response below it, RUN_MODES par.4);
  * M1 SiPM-sum baselines were re-zeroed (uniformity unknown) and the FIFOs
    feeding M1 wander ~3 mV -> re-check per-wall noise walls + monotonicity.

This tool re-measures rate-vs-threshold per section BEFORE any 2D scan: it
sets the chosen sections of one board to each ladder value (per-section
singles are independent M5 channels, so one dwell reads all four at once),
beam-gated, and appends JSONL. The other board is locked but untouched.
ORIGINAL thresholds are restored on exit. Safe alongside a live
flash/flash_random run (M1/M2 are not in that trigger path).

All board contact goes through the rate_scan_2d rig -> board_session
(one process per board, clean closes, floor guard).

Usage (NOTE the = form -- argparse rejects space-separated values starting
with '-'):
  .venv/bin/python n1081b/threshold_ladder.py [--board plastic|wall|both]
      [--sections ABCD] [--dwell 15] [--plastic-ladder="-80,-66,..."]
      [--wall-ladder "50,42,..."] [--label ladder] [--dry-run]

  D1 noise-wall zoom example:
  .venv/bin/python n1081b/threshold_ladder.py --board plastic --sections D \\
      --plastic-ladder="-40,-35,-30,-28,-26,-24,-22,-20,-18,-16,-14,-12,-10" \\
      --label d_wall_zoom
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
                          daq_alive, wait_for_beam, log, THRESHOLD_FLOOR_MV)

OUTPUT_ROOT = os.path.expanduser("~/beam_july/threshold_ladder")
SETTLE_S = 2

# Deep -> shallow so the noisiest (at-floor) settings are visited last + briefly.
DEFAULT_PLASTIC = [-80, -66, -54, -44, -36, -30, -25, -20, -16, -13, -10]
DEFAULT_WALL = [50, 42, 35, 29, 24, 20, 17, 14, 12, 10]


def _ints(s):
    return [int(x) for x in s.replace(" ", "").split(",") if x]


def _spec(s, board):
    """'-30' (uniform) or 'A:-30,B:-30,...' -> {sec: mv}, sign+floor validated."""
    s = s.replace(" ", "")
    if ":" in s:
        d = {k.upper(): int(v) for k, v in (kv.split(":") for kv in s.split(","))}
    else:
        d = {sec: int(s) for sec in "ABCD"}
    for sec, v in d.items():
        if sec not in "ABCD":
            raise SystemExit(f"bad sector '{sec}' in {s!r}")
        if abs(v) < THRESHOLD_FLOOR_MV:
            raise SystemExit(f"|{v}| mV below the {THRESHOLD_FLOOR_MV} mV floor")
        if (v >= 0) if board == "plastic" else (v <= 0):
            raise SystemExit(f"{board} threshold {v} has the wrong sign")
    return d


def sweep(rig, board, ladder, sections, dwell, points_path):
    import scint_hv_lib as shv  # sys.path prepared by the rate_scan_2d import
    log(f"--- {board} ladder over sections {''.join(sections)}: {ladder} ---")
    for i, thr in enumerate(ladder):
        for sec in sections:
            rig.set(board, sec, thr)
        wait_for_beam()
        time.sleep(SETTLE_S)
        rates, dt = m5_counter_rates(dwell)
        state = shv.read_beam_state()
        rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "board": board,
               "thr_mv": thr, "sections": list(sections),
               "counters_hz": rates, "dwell_s": round(dt, 1),
               "beam_state": {k: state.get(k) for k in
                              ("beam_on", "last_pulse_e10", "pulses_10min")
                              } if state else None}
        with open(points_path, "a") as f:
            f.write(json.dumps(rec) + "\n")
        own = rates["SEC_A" if board == "wall" else "SEC_B"]
        log(f"  [{i + 1:2d}/{len(ladder)}] {board} {thr:+4d} mV | "
            f"{'walls' if board == 'wall' else 'scints'} {own} | "
            f"coinc {rates.get('SEC_C')} | e10={rec['beam_state'] and rec['beam_state'].get('last_pulse_e10')}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--board", choices=("plastic", "wall", "both"), default="both")
    ap.add_argument("--sections", default="ABCD",
                    help="subset like 'D' or 'AD'; unlisted sections stay at original")
    ap.add_argument("--dwell", type=float, default=15.0)
    ap.add_argument("--plastic-ladder", default=None, help="comma ints, negative mV")
    ap.add_argument("--wall-ladder", default=None, help="comma ints, positive mV")
    ap.add_argument("--label", default="ladder")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply-plastic", default=None, metavar="MV",
                    help='set-and-EXIT (no sweep, no restore): uniform "-30" '
                         'or per-sector "A:-30,B:-30,C:-30,D:-36"')
    ap.add_argument("--apply-wall", default=None, metavar="MV",
                    help='set-and-EXIT: uniform "15" or per-sector "A:15,B:16,..."')
    args = ap.parse_args()

    if args.apply_plastic or args.apply_wall:
        mode = check_safe_mode()
        log(f"trigger mode: {mode} (safe) -- APPLY mode, thresholds will be LEFT as set")
        rig = ThresholdRig()
        try:
            log(f"before: {rig.original}")
            for board, spec in (("plastic", args.apply_plastic),
                                ("wall", args.apply_wall)):
                if spec:
                    for sec, mv in _spec(spec, board).items():
                        rig.set(board, sec, mv)
            now = {b: {s: rig.get(rig.m1 if b == "wall" else rig.m2, s)
                       for s in "ABCD"} for b in ("wall", "plastic")}
            log(f"applied+verified: {now}")
        finally:
            rig.close()
        return

    sections = [c for c in args.sections.upper() if c in "ABCD"]
    plastic = _ints(args.plastic_ladder) if args.plastic_ladder else DEFAULT_PLASTIC
    wall = _ints(args.wall_ladder) if args.wall_ladder else DEFAULT_WALL
    for v in plastic + wall:
        if abs(v) < THRESHOLD_FLOOR_MV:
            raise SystemExit(f"|{v}| mV below the {THRESHOLD_FLOOR_MV} mV hardware floor")
    if any(v >= 0 for v in plastic) or any(v <= 0 for v in wall):
        raise SystemExit("plastic ladder must be negative, wall ladder positive")

    todo = []
    if args.board in ("plastic", "both"):
        todo.append(("plastic", plastic))
    if args.board in ("wall", "both"):
        todo.append(("wall", wall))
    n_pts = sum(len(l) for _, l in todo)
    est = n_pts * (args.dwell + SETTLE_S + 4)
    log(f"{n_pts} points x ~{args.dwell + SETTLE_S + 4:.0f} s -> ~{est / 60:.0f} min "
        f"(sections {''.join(sections)})")
    for b, l in todo:
        log(f"  {b}: {l}")
    if args.dry_run:
        return

    mode = check_safe_mode()
    log(f"trigger mode: {mode} (safe); daq_control tmux alive: {daq_alive()}")

    outdir = os.path.join(OUTPUT_ROOT, f"{time.strftime('%Y-%m-%d_%H-%M-%S')}_{args.label}")
    os.makedirs(outdir, exist_ok=True)
    points_path = os.path.join(outdir, "points.jsonl")
    log(f"output: {outdir}")

    rig = ThresholdRig()
    try:
        with open(os.path.join(outdir, "config.json"), "w") as f:
            json.dump({"started": time.strftime("%Y-%m-%d %H:%M:%S"), "mode": mode,
                       "sections": sections, "dwell_s": args.dwell,
                       "ladders": {b: l for b, l in todo},
                       "original_thresholds": rig.original, "argv": sys.argv}, f, indent=1)
        log(f"original thresholds: {rig.original}")
        for b, l in todo:
            sweep(rig, b, l, sections, args.dwell, points_path)
    except KeyboardInterrupt:
        log("Interrupted -- restoring...")
    finally:
        try:
            for b in ("wall", "plastic"):
                for sec in "ABCD":
                    rig.set(b, sec, rig.original[b][sec])
            log(f"originals restored: {rig.original}")
        except Exception as e:  # noqa: BLE001
            log(f"WARNING: restore incomplete ({e!r}) -- check M1/M2 manually")
        rig.close()
    log(f"DONE -- data in {outdir}")


if __name__ == "__main__":
    main()
