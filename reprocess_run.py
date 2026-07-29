#!/usr/bin/env python3
"""
Reprocess every incomplete file-group of one run, in place.

Why this exists: processor_watcher walks runs NEWEST-first and returns after a
single file_num, so while a run is actively taking data it never reaches an older
run's backlog. To rebuild run_61 (48 groups missing FEUs after the copy_on_fly
race, plus 84 files deleted by repair_truncated_decodes.py) without waiting for
run_63 to finish, this drives the same pipeline directly.

It imports processor_watcher and calls its `_process_file_num`, so decode /
analyze / combine are byte-for-byte the same operations with the same pedestal
resolution, sample period and FEU->detector map. Nothing is duplicated here.

IMPORTANT: add the run to `exclude_runs` in config/processor_config.json and
restart the watcher before running this, so the two cannot decode the same file
simultaneously. Remove the exclusion afterwards.

  .venv/bin/python reprocess_run.py run_61 [--jobs N] [--dry-run]
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import processor_watcher as W  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('run')
    ap.add_argument('--config', default='config/processor_config.json')
    ap.add_argument('--jobs', type=int, default=3,
                    help='decode/analyze threads (keep below core count so the '
                         'live DAQ and its decoding are not starved)')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    with open(args.config) as f:
        cfg = json.load(f)

    runs_dir = Path(cfg['runs_dir'])
    raw_inner = cfg.get('raw_daq_inner_dir', 'raw_daq_data')
    decoded_inner = cfg.get('decoded_root_inner_dir', 'decoded_root')
    hits_inner = cfg.get('hits_inner_dir', 'hits_root')
    combined_inner = cfg.get('combined_hits_inner_dir', 'combined_hits_root')
    run_dir = runs_dir / args.run

    sample_period = W._read_sample_period(run_dir)
    feu_det_map = W._read_feu_detector_map(run_dir)
    expected_feus = W._read_expected_feus(run_dir)
    print(f'{args.run}: expected_feus={expected_feus} sample_period={sample_period}')

    todo = []
    for subrun_dir in sorted(d for d in run_dir.iterdir() if d.is_dir()):
        raw_dir = subrun_dir / raw_inner
        if not raw_dir.exists():
            continue
        ped_dir = W._resolve_pedestal_dir(raw_dir, cfg.get('pedestal_loc', 'find'),
                                          cfg.get('pedestal_dir', ''))
        all_fnums = W._get_data_file_nums(raw_dir)
        done = W._get_processed_file_nums(
            subrun_dir, combined_inner, hits_inner, decoded_inner,
            cfg.get('do_combine', True), cfg.get('do_analyze', True),
            expected_feus, raw_dir)
        for fnum in sorted(all_fnums - done):
            group = [raw_dir / f for f in os.listdir(raw_dir)
                     if W._is_data_fdf(f) and W._extract_file_num(f) == fnum]
            if group:
                todo.append((subrun_dir, ped_dir, fnum, group))

    print(f'{len(todo)} incomplete file-groups to rebuild')
    if args.dry_run:
        for sd, _, fnum, g in todo:
            print(f'   {sd.name} b{fnum:03d} ({len(g)} FDFs)')
        return

    t0 = time.time()
    for i, (subrun_dir, ped_dir, fnum, group) in enumerate(todo, 1):
        print(f'[{i}/{len(todo)}] {subrun_dir.name} b{fnum:03d} '
              f'({len(group)} FDFs)', flush=True)
        try:
            W._process_file_num(
                fnum, group, subrun_dir, ped_dir,
                decoded_inner, hits_inner, combined_inner,
                cfg['decode_executable'], cfg['analyze_executable'],
                cfg['combine_executable'],
                cfg.get('do_decode', True), cfg.get('do_analyze', True),
                cfg.get('do_combine', True),
                cfg.get('save_fdfs', True), cfg.get('save_decoded', True),
                args.jobs, sample_period,
                cfg.get('common_noise_subtraction', False), feu_det_map)
        except Exception as e:                              # noqa: BLE001
            import traceback
            print(f'   ERROR on {subrun_dir.name} b{fnum:03d}: {e!r}')
            traceback.print_exc()
    print(f'done in {(time.time() - t0) / 60:.1f} min')


if __name__ == '__main__':
    main()
