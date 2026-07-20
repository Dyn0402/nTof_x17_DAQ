#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
prep_zs_thresholds.py — build a production zero-suppression threshold set from an
EXISTING pedestal run, WITHOUT re-running pedestals.

Rationale (2026-07-19): the DREAM `*_thr.prg` is a plain ASCII text file that stores,
per channel, the 5-sigma-over-baseline threshold that a pedestal-threshold run emitted
(`Sys PedRun Threshold = 5`). We never need to re-take pedestals just to change the ZS
operating point — take the LATEST pedestal run and edit the threshold file:

  1. rescale every per-channel threshold by k/5  (k=8 -> x1.6), IDENTICAL to the
     beam-validated harness ~/beam_july/test/zs_rate_scan/gen_zs_ladder.py:rescale_thr
     (pure multiply of the stored value, clamp to 12-bit). Matching it byte-for-byte is
     what keeps our k8 the SAME threshold set that was validated on beam (Phase 8,
     FINDINGS_2026-07-18). Do NOT "improve" the math here without re-validating.
  2. inject tracer channels (threshold forced to 0 on FEU-channels 0 / 224 / 511 =
     Dream0.ch0 / Dream3.ch32 / Dream7.ch63) so every event carries a start/middle/end
     integrity watermark (see zs_ipd_safety FINDINGS_2026-07-18: tracer-511 also monitors
     the FEU size-truncation systematic for free).

Output is a self-contained pedestal SET laid out exactly like a real pedestal run
(`<out_root>/<out_name>/pedestals/<same filenames>`), so the production DAQ consumes it
with NO code change: set the run config's
    dream_daq_info['pedestals_dir'] = <out_root>            (default: ~/beam_july/pedestals)
    dream_daq_info['pedestals']     = <out_name>            (explicit, not 'latest')
and dream_daq_control.get_pedestals() copies + renames the prg's into the run dir under
the canonical names (dream_thresholds_NN_thr.prg / dream_pedestals_NN_ped.prg) that the
ZS cfg template's per-FEU Feu_RunCtrl_ZsFile / PdFile lines reference.

The out_name deliberately does NOT parse as `pedestals_<datetime>`, so `pedestals='latest'`
will never pick it by accident — reference it explicitly.

Usage:
  # default: latest ped run, k=8, tracers on, auto out_name
  python dream_scripts/prep_zs_thresholds.py

  python dream_scripts/prep_zs_thresholds.py --k 8 --ped-run pedestals_07-18-26_14-06-43
  python dream_scripts/prep_zs_thresholds.py --k 3.5 --no-tracers --out-name zs_k3p5_test
  python dream_scripts/prep_zs_thresholds.py --show   # just print what would be produced
"""
import argparse
import os
import re
import shutil
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from dream_threshold_manager import DreamThresholdManager

BASE_SIGMA = 5.0        # thr.prg is produced at 5 sigma (Sys PedRun Threshold = 5)
TWELVE_BIT_MAX = 4095   # thresholds are 12-bit
DEFAULT_PED_ROOT = os.path.expanduser("~/beam_july/pedestals")
# FEU-channel indices forced to threshold 0 as per-event tracers (start / middle / end
# of the payload). FEU-ch -> (dream, channel) = (ch // 64, ch % 64).
DEFAULT_TRACERS = [0, 224, 511]


def find_latest_ped_run(root):
    """Newest pedestals_<datetime> dir under root (same rule as get_pedestals / gen_zs_ladder)."""
    dirs = []
    for item in os.listdir(root):
        p = os.path.join(root, item)
        if not (os.path.isdir(p) and item.startswith("pedestals_")):
            continue
        for fmt in ("%m-%d-%y_%H-%M-%S", "%m-%d-%Y_%H-%M-%S"):
            try:
                dirs.append((datetime.strptime(item[len("pedestals_"):], fmt), item))
                break
            except ValueError:
                continue
    if not dirs:
        sys.exit(f"no pedestals_* runs under {root}")
    dirs.sort()
    return dirs[-1][1]


def rescale_and_tracer(src_prg, dst_prg, k, tracers):
    """dst_prg = src_prg with every channel scaled by k/BASE_SIGMA (clamped 12-bit), then
    tracer FEU-channels forced to 0. Returns (n_channels, n_clipped, n_tracers_set)."""
    mgr = DreamThresholdManager()
    mgr.read_prg(src_prg)
    factor = k / BASE_SIGMA
    n, clipped = 0, 0
    for d in range(8):
        for c in range(64):
            v = int(round(mgr.get_threshold(d, c) * factor))   # SAME as gen_zs_ladder.rescale_thr
            if v > TWELVE_BIT_MAX:
                v, hit = TWELVE_BIT_MAX, True
            else:
                hit = False
            v = max(0, v)
            mgr.set_threshold(d, c, v)
            n += 1
            clipped += hit
    n_tr = 0
    for fc in tracers:
        d, c = fc // 64, fc % 64
        if 0 <= d < 8 and 0 <= c < 64:
            mgr.set_threshold(d, c, 0)
            n_tr += 1
    mgr.write_prg(dst_prg)
    return n, clipped, n_tr


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--k", type=float, default=8.0, help="sigma multiplier (default 8)")
    ap.add_argument("--ped-root", default=DEFAULT_PED_ROOT,
                    help=f"pedestals root (default {DEFAULT_PED_ROOT})")
    ap.add_argument("--ped-run", default=None,
                    help="source pedestals_* dir name (default: latest)")
    ap.add_argument("--out-root", default=None,
                    help="where to write the set (default: same as --ped-root, so "
                         "get_pedestals finds it with pedestals_dir unchanged)")
    ap.add_argument("--out-name", default=None,
                    help="output set dir name (default: zs_k<k>[_tracer]_from_<srcstamp>)")
    ap.add_argument("--tracers", default=",".join(map(str, DEFAULT_TRACERS)),
                    help="comma-sep FEU-channel indices forced to thr 0 (default 0,224,511)")
    ap.add_argument("--no-tracers", action="store_true", help="do not inject any tracers")
    ap.add_argument("--show", action="store_true", help="print plan and exit, write nothing")
    args = ap.parse_args()

    tracers = [] if args.no_tracers else [int(x) for x in args.tracers.split(",") if x.strip()]

    ped_run = args.ped_run or find_latest_ped_run(args.ped_root)
    src_dir = os.path.join(args.ped_root, ped_run, "pedestals")
    if not os.path.isdir(src_dir):
        sys.exit(f"no pedestals/ subdir in {os.path.join(args.ped_root, ped_run)}")
    thr_files = sorted(f for f in os.listdir(src_dir) if f.endswith("_thr.prg"))
    ped_files = sorted(f for f in os.listdir(src_dir) if f.endswith("_ped.prg"))
    grace_files = sorted(f for f in os.listdir(src_dir) if f.startswith("Grace_"))
    if not thr_files:
        sys.exit(f"no *_thr.prg in {src_dir}")

    out_root = args.out_root or args.ped_root
    srcstamp = ped_run[len("pedestals_"):] if ped_run.startswith("pedestals_") else ped_run
    ktag = f"k{args.k:g}".replace(".", "p")
    out_name = args.out_name or (
        f"zs_{ktag}{'_tracer' if tracers else ''}_from_{srcstamp}")
    out_dir = os.path.join(out_root, out_name, "pedestals")

    print(f"Source pedestal run : {ped_run}")
    print(f"  {len(thr_files)} thr, {len(ped_files)} ped, {len(grace_files)} Grace files")
    print(f"k (sigma)           : {args.k}  (rescale factor {args.k/BASE_SIGMA:.3f})")
    print(f"tracers (FEU-ch->0) : {tracers or 'NONE'}")
    print(f"Output set          : {os.path.join(out_root, out_name)}")
    print(f"  -> config: pedestals_dir='{out_root}', pedestals='{out_name}'")
    if args.show:
        print("\n--show: nothing written.")
        return

    os.makedirs(out_dir, exist_ok=True)
    for f in ped_files:                    # pedestal baselines: verbatim
        shutil.copy(os.path.join(src_dir, f), os.path.join(out_dir, f))
    for f in grace_files:                  # Grace noise pars: verbatim
        shutil.copy(os.path.join(src_dir, f), os.path.join(out_dir, f))
    tot_clip = 0
    for f in thr_files:                    # thresholds: rescaled + tracers
        _, clip, _ = rescale_and_tracer(os.path.join(src_dir, f),
                                         os.path.join(out_dir, f), args.k, tracers)
        tot_clip += clip
    with open(os.path.join(out_root, out_name, "SOURCE_PED_RUN.txt"), "w") as fh:
        fh.write(f"source_ped_run: {ped_run}\nsource_dir: {src_dir}\n"
                 f"k_sigma: {args.k}\nrescale_factor: {args.k/BASE_SIGMA}\n"
                 f"tracers_feu_ch: {tracers}\n"
                 f"generated: {datetime.now():%Y-%m-%d %H:%M:%S}\n"
                 f"tool: dream_scripts/prep_zs_thresholds.py\n")
    note = f"  ({tot_clip} ch clipped at {TWELVE_BIT_MAX})" if tot_clip else ""
    print(f"\nWrote {len(thr_files)} thr (x{args.k/BASE_SIGMA:.2f}) + {len(ped_files)} ped "
          f"+ {len(grace_files)} Grace to {out_dir}{note}")
    print("Done.")


if __name__ == "__main__":
    main()
