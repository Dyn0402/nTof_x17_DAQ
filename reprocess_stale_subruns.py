#!/usr/bin/env python3
"""Reprocess only the PREVIOUS (done-acquiring) subruns of a run, skipping any
subrun still being acquired (so it never collides with the live processor_watcher,
which walks newest-first).

For each eligible subrun: delete hits_root + combined_hits_root, then rebuild
every file-group via processor_watcher._process_file_num (byte-identical pipeline,
reads executables from the config JSON -> Release binary).

A subrun is eligible iff:
  - it has real data (raw_daq_data with datrun FDFs), AND
  - its newest raw FDF is older than --acquire-window minutes (NOT live), AND
  - it currently has hits/combined output (i.e. was already processed by the old
    binary) unless --include-unprocessed is given.

Processes oldest-subrun-first (furthest from the watcher's newest-first cursor).

  reprocess_stale_subruns.py <run> [--jobs N] [--acquire-window MIN] [--dry-run]
                             [--limit K] [--subruns a,b,c]
"""
import argparse, json, os, sys, time, shutil, re
from pathlib import Path

sys.path.insert(0, "/home/mx17/PycharmProjects/nTof_x17_DAQ")
os.chdir("/home/mx17/PycharmProjects/nTof_x17_DAQ")
import processor_watcher as W  # noqa: E402


def newest_fdf_mtime(raw_dir: Path):
    m = 0.0
    for f in raw_dir.iterdir():
        if W._is_data_fdf(f.name):
            try:
                m = max(m, f.stat().st_mtime)
            except OSError:
                pass
    return m


def subrun_index(name):
    nums = re.findall(r"\d+", name)
    return int(nums[-1]) if nums else -1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--config", default="config/processor_config.json")
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--acquire-window", type=float, default=15.0,
                    help="skip subruns whose newest FDF is within this many minutes (live)")
    ap.add_argument("--limit", type=int, default=0, help="process at most K subruns (0=all)")
    ap.add_argument("--subruns", default="", help="comma list: only these subrun names")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = json.load(open(args.config))
    runs_dir = Path(cfg["runs_dir"])
    raw_inner = cfg.get("raw_daq_inner_dir", "raw_daq_data")
    dec_inner = cfg.get("decoded_root_inner_dir", "decoded_root")
    hits_inner = cfg.get("hits_inner_dir", "hits_root")
    comb_inner = cfg.get("combined_hits_inner_dir", "combined_hits_root")
    run_dir = runs_dir / args.run

    sample_period = W._read_sample_period(run_dir)
    feu_det_map = W._read_feu_detector_map(run_dir)
    try:
        expected_feus = W._read_expected_feus(run_dir)
    except Exception:
        expected_feus = None

    only = set(s for s in args.subruns.split(",") if s)
    now = time.time()
    win = args.acquire_window * 60.0

    # classify
    eligible, skipped_live, skipped_noout = [], [], []
    for sd in sorted((d for d in run_dir.iterdir() if d.is_dir()), key=lambda d: subrun_index(d.name)):
        raw_dir = sd / raw_inner
        if not raw_dir.exists():
            continue
        if only and sd.name not in only:
            continue
        nf = newest_fdf_mtime(raw_dir)
        age_min = (now - nf) / 60.0 if nf else 1e9
        n_hits = len(list((sd / hits_inner).glob("*_hits.root"))) if (sd / hits_inner).exists() else 0
        n_comb = len(list((sd / comb_inner).glob("*feu-combined*"))) if (sd / comb_inner).exists() else 0
        if age_min < args.acquire_window:
            skipped_live.append((sd.name, age_min)); continue
        # Include no-output subruns too: a reprocess pass must (re)build them
        # (e.g. a freshly-taken run with no prior output). delete-outputs is a
        # no-op when there is nothing there; _process_file_num decodes as needed.
        eligible.append((sd, raw_dir, age_min, n_hits, n_comb))

    if args.limit and len(eligible) > args.limit:
        eligible = eligible[:args.limit]

    print(f"{args.run}: {len(eligible)} eligible (previous) subruns, "
          f"{len(skipped_live)} skipped LIVE, {len(skipped_noout)} skipped no-output")
    for name, age in skipped_live:
        print(f"   LIVE skip: {name} (newest fdf {age:.1f} min ago)")
    for sd, raw_dir, age, nh, nc in eligible:
        print(f"   redo: {sd.name:34s} age={age/60:.1f}h hits={nh} comb={nc}")
    if args.dry_run:
        return

    t0 = time.time()
    for i, (sd, raw_dir, age, nh, nc) in enumerate(eligible, 1):
        # delete stale outputs so _get_processed_file_nums treats groups as incomplete
        for inner in (hits_inner, comb_inner):
            d = sd / inner
            if d.exists():
                shutil.rmtree(d)
        ped_dir = W._resolve_pedestal_dir(raw_dir, cfg.get("pedestal_loc", "find"), cfg.get("pedestal_dir", ""))
        all_fnums = W._get_data_file_nums(raw_dir)
        print(f"[{i}/{len(eligible)}] {sd.name}: {len(all_fnums)} file-groups", flush=True)
        for fnum in sorted(all_fnums):
            group = [raw_dir / f for f in os.listdir(raw_dir)
                     if W._is_data_fdf(f) and W._extract_file_num(f) == fnum]
            if not group:
                continue
            try:
                W._process_file_num(
                    fnum, group, sd, ped_dir, dec_inner, hits_inner, comb_inner,
                    cfg["decode_executable"], cfg["analyze_executable"], cfg["combine_executable"],
                    cfg.get("do_decode", True), cfg.get("do_analyze", True), cfg.get("do_combine", True),
                    cfg.get("save_fdfs", True), cfg.get("save_decoded", True),
                    args.jobs, sample_period, cfg.get("common_noise_subtraction", True), feu_det_map)
            except Exception as e:
                import traceback
                print(f"   ERROR {sd.name} b{fnum:03d}: {e!r}")
                traceback.print_exc()
    print(f"done {len(eligible)} subruns in {(time.time()-t0)/60:.1f} min")


if __name__ == "__main__":
    main()
