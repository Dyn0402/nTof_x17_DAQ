#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generate a PNG + PDF table of the relevant detector mappings (FEU / HV / cards /
cable length) from run_config_beam.py, for the logbook. Re-run after editing the
run config to regenerate.

Usage:
    python scripts/make_mapping_table.py [--out-dir config/mapping_tables]
"""

import os
import sys
import argparse
from datetime import datetime

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# Anchor to the repo root so `run_config_beam` imports and its relative file reads
# (hv_creds.txt) and the default output dir resolve no matter where this is invoked from.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
os.chdir(REPO_ROOT)

from run_config_beam import Config


def fmt_hv(channel):
    """Format an hv_channels (card, ch) tuple. Unknown -> blank, to be filled in by hand."""
    if not isinstance(channel, (tuple, list)) or len(channel) < 2:
        return ''
    card, ch = channel[0], channel[1]
    if card is None or ch is None:
        return ''
    return f'({card}, {ch})'


def summarize_axis(dream_feus, prefix):
    """Summarize the FEU number(s) used by connectors whose key starts with prefix."""
    feus = []
    for key, mapping in dream_feus.items():
        if not key.startswith(prefix):
            continue
        if isinstance(mapping, (tuple, list)) and len(mapping) >= 1:
            feus.append(mapping[0])
    uniq = sorted(set(feus))
    if not uniq:
        return '-'
    return ', '.join(str(f) for f in uniq)


def summarize_cable(cable_map):
    """Summarize cable lengths across connectors (single value if uniform)."""
    if not isinstance(cable_map, dict) or not cable_map:
        return '-'
    uniq = sorted(set(cable_map.values()))
    return ', '.join(str(v) for v in uniq)


def build_rows(config):
    columns = ['Detector', 'Alias', 'MX cards', 'Drift gap',
               'HV drift\n(card, ch)', 'HV resist\n(card, ch)', 'X FEU', 'Y FEU', 'Cable len']
    rows = []
    for det in config.detectors:
        if det.get('det_type') != 'mx17':
            continue
        dream_feus = det.get('dream_feus', {}) or {}
        hv = det.get('hv_channels', {}) or {}
        rows.append([
            det.get('name', '-'),
            det.get('alias', '-'),
            det.get('mx_cards', '-'),
            det.get('drift_gap', '-'),
            fmt_hv(hv.get('drift')),
            fmt_hv(hv.get('resist')),
            summarize_axis(dream_feus, 'x'),
            summarize_axis(dream_feus, 'y'),
            summarize_cable(det.get('dream_feu_cable_length', {})),
        ])
    return columns, rows


def render(columns, rows, run_name, out_base):
    n_rows = len(rows) + 1
    fig_h = 1.1 + 0.42 * n_rows
    fig, ax = plt.subplots(figsize=(13, fig_h))
    ax.axis('off')

    title = f'nTof x17 detector mapping  —  run "{run_name}"'
    subtitle = f'Source: run_config_beam.py   Generated: {datetime.now():%Y-%m-%d %H:%M}'
    ax.set_title(f'{title}\n{subtitle}', fontsize=13, fontweight='bold', pad=12)

    table = ax.table(cellText=rows, colLabels=columns, cellLoc='center', loc='upper center')
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1, 1.6)

    # Style header row.
    for col in range(len(columns)):
        cell = table[0, col]
        cell.set_facecolor('#2c3e50')
        cell.set_text_props(color='white', fontweight='bold')

    # Zebra striping for readability.
    for r in range(1, len(rows) + 1):
        for col in range(len(columns)):
            if r % 2 == 0:
                table[r, col].set_facecolor('#f2f4f6')

    fig.tight_layout()
    png_path = f'{out_base}.png'
    pdf_path = f'{out_base}.pdf'
    fig.savefig(png_path, dpi=200, bbox_inches='tight')
    fig.savefig(pdf_path, bbox_inches='tight')
    plt.close(fig)
    return png_path, pdf_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out-dir', default='config/mapping_tables',
                        help='Directory to write the table (default: config/mapping_tables)')
    args = parser.parse_args()

    config = Config()
    columns, rows = build_rows(config)
    if not rows:
        raise SystemExit('No mx17 detectors found in the run config.')

    os.makedirs(args.out_dir, exist_ok=True)
    out_base = os.path.join(args.out_dir, 'detector_mapping')
    png_path, pdf_path = render(columns, rows, config.run_name, out_base)
    print(f'Wrote {png_path}')
    print(f'Wrote {pdf_path}')


if __name__ == '__main__':
    main()
