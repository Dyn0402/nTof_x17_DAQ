#!/usr/bin/env python3
"""
compare_pedestals.py

Compare pedestal QA runs using the per-run `pedestal_channels.csv` files written by
pedestal_strip_check.py. Reads CSVs only (no decode/analysis), so it is instant.

Usage:
    # compare the two most recent runs under a pedestals parent directory
    python3 scripts/compare_pedestals.py /media/dylan/data/x17/beam_july/pedestals

    # compare specific run directories (2 or more)
    python3 scripts/compare_pedestals.py DIR1 DIR2 [DIR3 ...]

    # compare ALL runs found under the parent directory
    python3 scripts/compare_pedestals.py --all /media/.../pedestals

Each run directory must contain a `pedestal_channels.csv` (produced by running
pedestal_strip_check.py on that run). Outputs, written to the parent/output dir:
    compare_runs.png          per-FEU dead/noisy bars, one series per run
    compare_summary.csv       per (FEU, connector) dead/noisy for every compared run
    compare_changes.csv       channels whose status changed between the two newest runs
"""

import os, sys, csv, re, glob, argparse
from datetime import datetime

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

NF, NC, ND = 8, 512, 64


def dir_time(d):
    """Sort key for a run dir: parse pedestals_MM-DD-YY_HH-MM-SS, else fall back to mtime."""
    m = re.search(r'(\d{2})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})', os.path.basename(d))
    if m:
        mo, da, yr, H, M, S = map(int, m.groups())
        try:
            return datetime(2000 + yr, mo, da, H, M, S)
        except ValueError:
            pass
    return datetime.fromtimestamp(os.path.getmtime(os.path.join(d, 'pedestal_channels.csv')))


def find_runs(parent):
    return sorted((d for d in glob.glob(os.path.join(parent, '*'))
                   if os.path.isdir(d) and os.path.exists(os.path.join(d, 'pedestal_channels.csv'))),
                  key=dir_time)


def load_run(d):
    cls = np.full((NF, NC), 'missing', dtype=object)
    raw = np.full((NF, NC), np.nan)
    cns = np.full((NF, NC), np.nan)
    n_events, ref = 0, float('nan')
    with open(os.path.join(d, 'pedestal_channels.csv')) as f:
        for row in csv.DictReader(f):
            feu, ch = int(row['feu']) - 1, int(row['channel'])
            cls[feu, ch] = row['status']
            if row['raw_rms']:
                raw[feu, ch] = float(row['raw_rms'])
            if row['cns_rms']:
                cns[feu, ch] = float(row['cns_rms'])
            n_events = int(row['n_events']); ref = float(row['ref_cns_rms'])
    return {'label': os.path.basename(d), 'cls': cls, 'raw': raw, 'cns': cns,
            'n_events': n_events, 'ref': ref}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('paths', nargs='+', help='pedestals parent dir, or 2+ run dirs')
    ap.add_argument('--all', action='store_true', help='compare all runs under the parent dir')
    ap.add_argument('--out', default=None, help='output dir (default: parent dir / first path)')
    args = ap.parse_args()

    if len(args.paths) == 1 and os.path.isdir(args.paths[0]) \
            and not os.path.exists(os.path.join(args.paths[0], 'pedestal_channels.csv')):
        parent = args.paths[0]
        runs = find_runs(parent)
        if len(runs) < 2:
            raise SystemExit(f'Need >=2 runs with pedestal_channels.csv under {parent}; found {len(runs)}.')
        run_dirs = runs if args.all else runs[-2:]
        out_dir = args.out or parent
    else:
        run_dirs = args.paths
        out_dir = args.out or os.path.dirname(os.path.abspath(run_dirs[-1]))

    R = [load_run(d) for d in run_dirs]
    labels = [r['label'] for r in R]
    print(f'Comparing {len(R)} runs:')
    for r in R:
        print(f'  {r["label"]}')

    dead = lambda r: r['cls'] == 'dead_stuck'
    nois = lambda r: r['cls'] == 'noisy_floating'

    # ---- Per-run overview -------------------------------------------------
    print('\n' + '=' * 70)
    print(f'{"run":<26}{"events":>8}{"refCNS":>8}{"dead":>7}{"noisy":>7}')
    for r in R:
        print(f'{r["label"]:<26}{r["n_events"]:>8}{r["ref"]:>8.1f}'
              f'{int(dead(r).sum()):>7}{int(nois(r).sum()):>7}')

    # ---- Per-FEU dead/noisy ----------------------------------------------
    print('\nPER-FEU dead / noisy')
    print('FEU  ' + '  '.join(f'{l[:16]:>16}' for l in labels))
    for feu in range(NF):
        cells = [f'{int(dead(r)[feu].sum()):>4}/{int(nois(r)[feu].sum()):<4}' for r in R]
        print(f'{feu+1:02d}   ' + '  '.join(f'{c:>16}' for c in cells))

    # ---- Summary CSV: per (FEU, connector) for every run -----------------
    summ = os.path.join(out_dir, 'compare_summary.csv')
    with open(summ, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['feu', 'connector'] +
                   [f'{r["label"]}_dead' for r in R] + [f'{r["label"]}_noisy' for r in R])
        for feu in range(NF):
            for d in range(NC // ND):
                lo, hi = d * ND, (d + 1) * ND
                dd = [int(dead(r)[feu][lo:hi].sum()) for r in R]
                nn = [int(nois(r)[feu][lo:hi].sum()) for r in R]
                w.writerow([feu + 1, d] + dd + nn)
    print(f'\n[csv] {summ}')

    # ---- Changes CSV: channels whose status changed between the 2 newest --
    prev, cur = R[-2], R[-1]
    chg = os.path.join(out_dir, 'compare_changes.csv')
    n_chg = 0
    with open(chg, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['feu', 'connector', 'channel',
                    f'{prev["label"]}_status', f'{cur["label"]}_status',
                    f'{prev["label"]}_raw', f'{cur["label"]}_raw',
                    f'{prev["label"]}_cns', f'{cur["label"]}_cns'])
        for feu in range(NF):
            for ch in range(NC):
                a, b = prev['cls'][feu, ch], cur['cls'][feu, ch]
                if a != b:
                    n_chg += 1
                    w.writerow([feu + 1, ch // ND, ch, a, b,
                                f'{prev["raw"][feu,ch]:.2f}', f'{cur["raw"][feu,ch]:.2f}',
                                f'{prev["cns"][feu,ch]:.2f}', f'{cur["cns"][feu,ch]:.2f}'])
    print(f'[csv] {chg}   ({n_chg} channels changed between '
          f'{prev["label"]} -> {cur["label"]})')

    # ---- Figure: per-FEU dead/noisy grouped by run -----------------------
    fig, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=True)
    x = np.arange(NF); w = 0.8 / len(R)
    colors = plt.cm.tab10(np.linspace(0, 1, 10))
    for k, (name, fn) in enumerate((('Dead', dead), ('Noisy', nois))):
        ax = axes[k]
        for m, r in enumerate(R):
            counts = [int(fn(r)[feu].sum()) for feu in range(NF)]
            ax.bar(x + (m - (len(R) - 1) / 2) * w, counts, w, label=r['label'], color=colors[m % 10])
        ax.set_ylabel(f'{name} channels'); ax.grid(alpha=0.3, axis='y'); ax.legend(fontsize=8)
    axes[1].set_xticks(x); axes[1].set_xticklabels([f'FEU{f+1:02d}' for f in range(NF)])
    axes[0].set_title('Dead / noisy channels per FEU, by pedestal run')
    fig.tight_layout()
    png = os.path.join(out_dir, 'compare_runs.png')
    fig.savefig(png, dpi=120, bbox_inches='tight')
    print(f'[fig] {png}')


if __name__ == '__main__':
    main()
