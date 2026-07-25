#!/usr/bin/env python3
"""
Repair truncated decoded ROOTs left by dead decode processes.

Background (2026-07-21). Three independent ways the processing chain produced
partial data while the raw FDFs stayed perfectly intact:

  1. processor_watcher fired on a subset of FEUs (copy_on_fly race) and marked
     the group done -> decoded files MISSING. Fixed in processor_watcher.py; the
     FEU-completeness demotion makes those groups self-heal.
  2. A decode process died part-way, leaving a SHORT ROOT file. `_decode_file`
     had no return-code check and Step 1 skips any output that already exists,
     so the truncated file was permanent. This script repairs those.
  3. Combine ran before every FEU's hits existed -> stale combined file. Fixed by
     the mtime-based rebuild in processor_watcher.py, which triggers as soon as
     any hits file is regenerated.

Proof that this is NOT a hardware/FEU fault: re-decoding
run_61/sngPS_dr500_r520_009 batch 000 from the raw FDFs gives exactly 2599
events for ALL EIGHT FEUs (rc=0), against stored files of FEU04=810, FEU05=1166,
FEU06=416, FEU08=2040. Every FEU recorded every trigger.

Detection: within one (subrun, file_num) group every FEU must decode to the same
number of events -- they all see the same triggers. Any decoded file with fewer
entries than the group maximum is truncated. This script deletes those decoded
files and their dependent hits files; the fixed watcher then re-decodes,
re-analyzes and (because the new hits are newer than the combined output)
rebuilds the combined file.

Requires uproot, which lives in the ANALYSIS venv, not the DAQ one:
  ~/PycharmProjects/nTof_x17/.venv/bin/python repair_truncated_decodes.py [runs...]
  ... --apply     to actually delete (default is a dry run)
"""
import argparse
import collections
import glob
import os
import re
import sys

import uproot

BASE = '/mnt/data/x17/beam_july/runs'


def scan_run(run):
    """Yield (subrun, file_num, group_max, {feu: (entries, decoded_path)})."""
    rd = os.path.join(BASE, run)
    if not os.path.isdir(rd):
        return
    for sub in sorted(os.listdir(rd)):
        sd = os.path.join(rd, sub)
        if not os.path.isdir(sd):
            continue
        groups = collections.defaultdict(dict)
        for f in glob.glob(os.path.join(sd, 'decoded_root', '*datrun*.root')):
            m = re.search(r'_(\d{3})_(\d{2})\.root$', f)
            if not m or '_pedestals_' in os.path.basename(f):
                continue
            try:
                n = uproot.open(f)['nt'].num_entries
            except Exception as e:                       # unreadable = worthless
                print(f'  [!] unreadable {f}: {e!r}')
                n = 0
            groups[m.group(1)][m.group(2)] = (n, f)
        for fnum, d in sorted(groups.items()):
            if len(d) < 2:
                continue
            gmax = max(v[0] for v in d.values())
            yield sub, fnum, gmax, d


def hits_for(decoded_path):
    """The hits file derived from a decoded ROOT, if present."""
    sd = os.path.dirname(os.path.dirname(decoded_path))
    name = os.path.basename(decoded_path).replace('.root', '_hits.root')
    p = os.path.join(sd, 'hits_root', name)
    return p if os.path.exists(p) else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('runs', nargs='*', default=['run_60', 'run_61', 'run_62',
                                                'run_63'])
    ap.add_argument('--apply', action='store_true',
                    help='actually delete (default: dry run)')
    ap.add_argument('--min-frac', type=float, default=0.999,
                    help='flag a FEU whose entries are below this fraction of '
                         'the group max (default 0.999 = any deficit)')
    args = ap.parse_args()

    total_files = total_events = 0
    to_delete = []
    for run in args.runs:
        n_grp = n_bad = 0
        for sub, fnum, gmax, d in scan_run(run):
            n_grp += 1
            bad = {feu: v for feu, v in d.items()
                   if gmax > 0 and v[0] < args.min_frac * gmax}
            if not bad:
                continue
            n_bad += 1
            lost = sum(gmax - v[0] for v in bad.values())
            total_events += lost
            print(f'  {run}/{sub} b{fnum}: max={gmax}  ' +
                  ', '.join(f'FEU{k}={v[0]}' for k, v in sorted(bad.items())))
            for feu, (n, path) in sorted(bad.items()):
                to_delete.append(path)
                h = hits_for(path)
                if h:
                    to_delete.append(h)
        print(f'{run}: {n_bad}/{n_grp} groups truncated')
    total_files = len(to_delete)

    print(f'\n{total_files} files to delete '
          f'({total_events} decoded events to be recovered)')
    if not args.apply:
        print('DRY RUN — nothing deleted. Re-run with --apply to proceed.')
        for p in to_delete[:20]:
            print('   would delete', p)
        if total_files > 20:
            print(f'   ... and {total_files - 20} more')
        return

    removed = 0
    for p in to_delete:
        try:
            os.remove(p)
            removed += 1
        except OSError as e:
            print(f'  [!] could not remove {p}: {e}')
    print(f'deleted {removed}/{total_files} files. The processor_watcher will '
          f're-decode, re-analyze and rebuild the combined outputs from the '
          f'(intact) raw FDFs.')


if __name__ == '__main__':
    sys.exit(main())
