#!/usr/bin/env python3
"""Finish the DREAM chain for one run, keyed on the ACQUISITION, not the file number.

Why this exists (and why `reprocess_run.py` cannot do it):

`processor_watcher` identifies a unit of work by its 3-digit *file number*. A
sub-run that was stopped and restarted reuses those numbers, so one sub-run can
hold several acquisitions all called b000. Three consequences, all seen on EOS:

  * `_get_processed_file_nums` marks b000 done as soon as ONE acquisition's
    combined file exists, so the others are never revisited -- they are the
    "fully decoded and analysed but never combined" groups.
  * `_get_feu_hits_map(hits_dir, fnum)` collapses every acquisition's hits into
    one {FEU: path} dict, last-one-wins per FEU. The single combined file it
    writes is therefore a MIXTURE: e.g. run_9/scan10_dr800_A495_04 holds FEUs
    1,2,3,4,7 from the 12H41 acquisition and 5,6,8 from 14H23, 1h42m apart.
  * `reprocess_run.py` inherits both, so pointing it at these runs would rewrite
    the same mixture.

Here an acquisition is (date, time, file_num) parsed from the DREAM filename, so
each one decodes, analyses and combines strictly against its own FEU files.

Decode and combine reuse `processor_watcher`'s own functions unchanged (including
the hang watchdog and the cross-FEU truncation guard). Analysis mirrors
`_analyze_file` exactly, with `--mf` added so a run whose existing products
predate the 2026-07-24 matched-filter analyser can be finished on its own recipe.

  python3 reprocess_acquisitions.py run_54 [--jobs N] [--dry-run]
                                   [--cns 0|1] [--mf N] [--only-combine]

`--cns`/`--mf` default to the values in the processor config / the analyser's own
defaults, i.e. today's recipe. Pass them explicitly to match an older run.
"""
import argparse
import collections
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import processor_watcher as W  # noqa: E402

try:
    import ROOT
    ROOT.gErrorIgnoreLevel = ROOT.kFatal
except ImportError:          # only --check-decodes needs it
    ROOT = None

# Mx17_<tag>_datrun_<yymmdd>_<HH>H<MM>_<num>_<feu>.<ext>
ACQ_RE = re.compile(r"^Mx17_(?P<tag>.+)_datrun_(?P<date>\d{6})_(?P<time>\d+H\d+)_"
                    r"(?P<num>\d{3})_(?P<feu>\d{2})(?P<suffix>[._].*)$")


def acq_of(name):
    """((date, time, num), feu) for a data product file, or None."""
    if "_pedestals_" in name:
        return None
    m = ACQ_RE.match(name)
    if not m:
        return None
    return (m.group("date"), m.group("time"), m.group("num")), int(m.group("feu"))


def scan_subrun(subrun_dir, raw_inner, decoded_inner, hits_inner, combined_inner):
    """{acq_key: {'raw': {feu: path}, 'dec': {...}, 'hits': {...}, 'cmb': [paths]}}"""
    acqs = collections.defaultdict(
        lambda: {"raw": {}, "dec": {}, "hits": {}, "cmb": []})

    raw_dir = subrun_dir / raw_inner
    if raw_dir.is_dir():
        for f in raw_dir.iterdir():
            if not W._is_data_fdf(f.name):
                continue
            a = acq_of(f.name)
            if a:
                acqs[a[0]]["raw"][a[1]] = f

    for inner, key in ((decoded_inner, "dec"), (hits_inner, "hits")):
        d = subrun_dir / inner
        if not d.is_dir():
            continue
        for f in d.iterdir():
            if not f.name.endswith(".root"):
                continue
            a = acq_of(f.name)
            if a:
                acqs[a[0]][key][a[1]] = f

    d = subrun_dir / combined_inner
    if d.is_dir():
        for f in d.iterdir():
            if not f.name.endswith(".root") or "_pedestals_" in f.name:
                continue
            m = re.match(r"^Mx17_(?P<tag>.+)_datrun_(?P<date>\d{6})_"
                         r"(?P<time>\d+H\d+)_(?P<num>\d{3})_feu-combined_hits\.root$",
                         f.name)
            if m:
                acqs[(m.group("date"), m.group("time"), m.group("num"))]["cmb"].append(f)
    return acqs


def stage_ped_pick(ped_dir, pick, work):
    """A directory holding only the chosen pedestal set, as symlinks."""
    dst = Path(work) / 'pedpick' / f"{Path(ped_dir).parent.name}__{pick}"
    dst.mkdir(parents=True, exist_ok=True)
    n = 0
    for f in sorted(os.listdir(ped_dir)):
        if pick in f and f.endswith('.root'):
            link = dst / f
            if not link.exists():
                link.symlink_to(os.path.join(ped_dir, f))
            n += 1
    if n == 0:
        raise RuntimeError(f"--ped-pick {pick!r} matched no .root in {ped_dir}")
    print(f"[ped] using {n} pedestal files matching {pick!r} from "
          f"{Path(ped_dir).parent.name}")
    return dst


def analyze_one(root_path, ped_dir, hits_path, analyze_exe, sample_period,
                cns, feu_det_map, mf, zs_baseline=False):
    """Mirror of processor_watcher._analyze_file, plus --mf passthrough."""
    m = re.search(r'_(\d{3})_(\d{2})', os.path.basename(str(root_path)))
    if not m:
        print(f"[analyze] cannot extract FEU from {root_path}, skipping")
        return
    feu_num = int(m.group(2))
    detector = (feu_det_map or {}).get(feu_num)

    ped_path = ''
    if ped_dir and os.path.isdir(ped_dir):
        ped_files = [
            f for f in os.listdir(ped_dir)
            if '_pedthr_' in f and f.endswith('.root')
            and re.search(r'_(\d{3})_(\d{2})', f)
            and int(re.search(r'_(\d{3})_(\d{2})', f).group(2)) == feu_num
        ]
        if len(ped_files) == 1:
            ped_path = os.path.join(ped_dir, ped_files[0])
        elif len(ped_files) > 1:
            # The watcher SKIPS here, silently and forever. That is what stalled
            # run_54: its pedestal directory holds a second, unrelated set taken
            # two days later. Refuse loudly instead -- the caller must pass
            # --ped-pick so the choice is recorded, never guessed.
            raise RuntimeError(
                f"{len(ped_files)} pedestals for FEU {feu_num} in {ped_dir}: "
                f"{sorted(ped_files)} -- pass --ped-pick <substring>")
        else:
            print(f"[analyze] no pedestal for FEU {feu_num}, continuing without")

    det_tag = f"det={detector} " if detector else ""
    print(f"[analyze] {det_tag}feu={feu_num:02d}  {os.path.basename(str(root_path))}")
    cmd = [analyze_exe, str(root_path), str(hits_path), ped_path]
    if sample_period is not None:
        cmd += ['--tps', str(sample_period)]
    cmd += ['--cns', '1' if cns else '0']
    if mf is not None:
        cmd += ['--mf', str(mf)]
    if zs_baseline:
        cmd += ['--zs-baseline', '1']
    import subprocess
    subprocess.run(cmd)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('run')
    ap.add_argument('--config', default='config/processor_config.json')
    ap.add_argument('--jobs', type=int, default=4)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--cns', type=int, default=None,
                    help='0/1; default = processor config')
    ap.add_argument('--mf', type=int, default=None,
                    help="matched-filter width; 0 = pre-2026-07-24 behaviour")
    ap.add_argument('--ped-pick', default=None,
                    help='substring selecting one set when a pedestal dir holds several')
    ap.add_argument('--only-combine', action='store_true',
                    help='never decode or analyse; only rebuild combined files')
    ap.add_argument('--subrun', default=None, help='restrict to one sub-run')
    ap.add_argument('--analyze-exe', default=None,
                    help='override the config analyser (e.g. the pre-2026-07-24 build)')
    ap.add_argument('--combine-exe', default=None,
                    help='override the config combiner. The 2026-07-24 combiner ADDS '
                         'trunc_left/trunc_right/significance branches (defaulted for '
                         'old inputs), so a run whose other combined files predate it '
                         'must be finished with the old build or its sub-run ends up '
                         'holding two tree schemas.')
    ap.add_argument('--force-combine', action='store_true',
                    help='rebuild every combined file, even ones that look current')
    ap.add_argument('--verify-json', default=None,
                    help='verify_combines.py output. Sharing a file_num only makes a '
                         'combined file SUSPECT; this says which ones were actually '
                         'measured to be mixtures, so the verified-clean ones are left '
                         'untouched instead of needlessly rewritten.')
    ap.add_argument('--limit', type=int, default=None,
                    help='process at most N acquisitions (for timing a batch)')
    ap.add_argument('--check-decodes', action='store_true',
                    help='before deciding, compare the event counts of the decodes '
                         'ALREADY on disk within each acquisition and delete any that '
                         'are short. Every FEU of one acquisition sees the same '
                         'triggers, so a deficit means a decode died part-way -- which '
                         'is what a killed or timed-out run of this script leaves '
                         'behind. Makes a resumed run self-healing. Costs one ROOT '
                         'header open per decoded file.')
    ap.add_argument('--work', default=os.path.expanduser('~/x17proc/work'),
                    help='scratch dir for staged pedestals; never inside the data tree')
    args = ap.parse_args()

    with open(args.config) as f:
        cfg = json.load(f)

    runs_dir = Path(cfg['runs_dir'])
    raw_inner = cfg.get('raw_daq_inner_dir', 'raw_daq_data')
    decoded_inner = cfg.get('decoded_root_inner_dir', 'decoded_root')
    hits_inner = cfg.get('hits_inner_dir', 'hits_root')
    combined_inner = cfg.get('combined_hits_inner_dir', 'combined_hits_root')
    run_dir = runs_dir / args.run

    cns = cfg.get('common_noise_subtraction', True) if args.cns is None \
        else bool(args.cns)
    analyze_exe = args.analyze_exe or cfg['analyze_executable']
    combine_exe = args.combine_exe or cfg['combine_executable']
    sample_period = W._read_sample_period(run_dir)
    feu_det_map = W._read_feu_detector_map(run_dir)
    print(f"{args.run}: tps={sample_period} cns={int(cns)} "
          f"mf={'default' if args.mf is None else args.mf}\n"
          f"  analyzer: {analyze_exe}\n"
          f"  combiner: {combine_exe}\n"
          f"  feu_det_map={feu_det_map}")

    clean = set()
    if args.verify_json:
        for r in json.load(open(args.verify_json)):
            if r.get('clean'):
                clean.add((r['run'], r['sub'], r['acq']))
        print(f"  {len(clean)} acquisitions verified clean; their combines are kept")

    todo = []
    for subrun_dir in sorted(d for d in run_dir.iterdir() if d.is_dir()):
        if args.subrun and subrun_dir.name != args.subrun:
            continue
        raw_dir = subrun_dir / raw_inner
        if not raw_dir.is_dir():
            continue
        ped_dir = W._resolve_pedestal_dir(raw_dir, cfg.get('pedestal_loc', 'find'),
                                          cfg.get('pedestal_dir', ''))
        acqs = scan_subrun(subrun_dir, raw_inner, decoded_inner, hits_inner,
                           combined_inner)
        # A file_num shared by several acquisitions is the mixed-combine case:
        # every combined file under it is suspect and gets rebuilt, even when it
        # already exists and looks fresh.
        shared = {n for n, c in collections.Counter(
            k[2] for k in acqs).items() if c > 1}
        for key, a in sorted(acqs.items()):
            if not a['raw']:
                continue
            if all(p.stat().st_size == 0 for p in a['raw'].values()):
                continue                      # acquisition that never wrote data
            if args.check_decodes and a['dec']:
                counts = {}
                for feu, p in sorted(a['dec'].items()):
                    tf = ROOT.TFile.Open(str(p))
                    t = tf.Get('nt') if tf and not tf.IsZombie() else None
                    counts[feu] = t.GetEntries() if t else 0
                    if tf:
                        tf.Close()
                gmax = max(counts.values()) if counts else 0
                for feu, n in sorted(counts.items()):
                    if gmax and n < gmax:
                        print(f"[check] {subrun_dir.name} {'|'.join(key)} feu {feu}: "
                              f"{n} events vs {gmax} — dropping decode + hits")
                        a['dec'][feu].unlink(missing_ok=True)
                        del a['dec'][feu]
                        h = a['hits'].pop(feu, None)
                        if h is not None:
                            h.unlink(missing_ok=True)

            need_dec = set(a['raw']) - set(a['dec'])
            need_hit = set(a['raw']) - set(a['hits'])
            mixed = (key[2] in shared
                     and (args.run, subrun_dir.name, "|".join(key)) not in clean)
            need_cmb = (not a['cmb']) or mixed or bool(need_hit) or args.force_combine
            if not (need_dec or need_hit or need_cmb):
                continue
            todo.append({'sub': subrun_dir, 'ped': ped_dir, 'key': key, 'a': a,
                         'need_dec': sorted(need_dec), 'need_hit': sorted(need_hit),
                         'mixed': mixed})

    if args.limit:
        todo = todo[:args.limit]
    ndec = sum(len(t['need_dec']) for t in todo)
    print(f"{len(todo)} acquisitions to finish ({ndec} FDFs to decode)")
    if args.dry_run:
        for t in todo:
            print(f"   {t['sub'].name} {'|'.join(t['key'])}: "
                  f"decode={t['need_dec']} analyze={t['need_hit']} "
                  f"combine={'REBUILD(mixed)' if t['mixed'] else 'yes'}")
        return

    t0 = time.time()
    for i, t in enumerate(todo, 1):
        sub, key, a = t['sub'], t['key'], t['a']
        tag = f"{sub.name} {'|'.join(key)}"
        print(f"\n[{i}/{len(todo)}] {tag}", flush=True)
        decoded_dir = sub / decoded_inner
        hits_dir = sub / hits_inner
        combined_dir = sub / combined_inner

        # --- decode (processor_watcher's own function: watchdog + rc checks)
        if t['need_dec'] and not args.only_combine:
            W.create_dir_if_not_exist(str(decoded_dir))
            counts = {}
            with ThreadPoolExecutor(max_workers=args.jobs) as pool:
                futs = {}
                for feu in t['need_dec']:
                    fdf = a['raw'][feu]
                    out = decoded_dir / fdf.name.replace('.fdf', '.root')
                    futs[pool.submit(W._decode_file, str(fdf), str(out),
                                     cfg['decode_executable'])] = (feu, fdf, out)
                for fut in as_completed(futs):
                    feu, fdf, out = futs[fut]
                    try:
                        counts[feu] = fut.result()
                    except W.DecodeTimeout as e:
                        print(f"[decode]  quarantined {os.path.basename(e.hang_path)}")
                        counts[feu] = None
                    else:
                        if counts[feu] is not None:
                            a['dec'][feu] = out
            # cross-FEU truncation guard, over the WHOLE acquisition (existing
            # decodes included) -- a short file is only visible against its peers.
            allc = dict(counts)
            good = [c for c in allc.values() if c and c > 0]
            if good:
                gmax = max(good)
                for feu, c in sorted(allc.items()):
                    if c is None or c <= 0 or c >= gmax:
                        continue
                    print(f"[decode]  TRUNCATED feu {feu}: {c} vs {gmax} — re-decoding")
                    fdf = a['raw'][feu]
                    out = decoded_dir / fdf.name.replace('.fdf', '.root')
                    if out.exists():
                        out.unlink()
                    try:
                        c2 = W._decode_file(str(fdf), str(out),
                                            cfg['decode_executable'])
                    except W.DecodeTimeout:
                        continue
                    if c2 is not None:
                        a['dec'][feu] = out
                    if c2 is None or (c2 > 0 and c2 < gmax):
                        print(f"[decode]  WARNING feu {feu} still short ({c2} vs {gmax})")

        # --- analyze
        if not args.only_combine:
            W.create_dir_if_not_exist(str(hits_dir))
            ped_dir = t['ped']
            if args.ped_pick and ped_dir:
                # Narrow a multi-set pedestal directory to the chosen acquisition
                # by staging symlinks -- the analyser takes one file per FEU, and
                # refuses (above) when a directory offers several. Staged OUTSIDE
                # the data tree: nothing this script does may leave scratch files
                # in the archived run directories on EOS.
                ped_dir = str(stage_ped_pick(ped_dir, args.ped_pick, args.work))
            tasks = []
            with ThreadPoolExecutor(max_workers=args.jobs) as pool:
                for feu in sorted(a['dec']):
                    if feu in a['hits']:
                        continue
                    root_path = a['dec'][feu]
                    hits_path = hits_dir / root_path.name.replace('.root', '_hits.root')
                    tasks.append(pool.submit(
                        analyze_one, root_path, ped_dir, hits_path,
                        analyze_exe, sample_period, cns,
                        feu_det_map, args.mf))
                for fut in as_completed(tasks):
                    fut.result()
            for feu in sorted(a['dec']):
                hp = hits_dir / a['dec'][feu].name.replace('.root', '_hits.root')
                if hp.exists():
                    a['hits'][feu] = hp

        # --- combine, strictly within this acquisition
        W.create_dir_if_not_exist(str(combined_dir))
        feu_hits = {feu: str(p) for feu, p in sorted(a['hits'].items())}
        if not feu_hits:
            print("[combine] no hits for this acquisition, skipping")
            continue
        name = W._make_combined_name(next(iter(feu_hits.values())))
        out = combined_dir / name
        if len(feu_hits) < len(a['raw']):
            print(f"[combine] WARNING only {len(feu_hits)}/{len(a['raw'])} FEUs")
        W._combine_hits(feu_hits, str(out), combine_exe)

    print(f"\ndone in {(time.time() - t0) / 60:.1f} min")


if __name__ == '__main__':
    main()
