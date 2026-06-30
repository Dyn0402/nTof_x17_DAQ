#!/usr/bin/env python3
"""
pedestal_strip_check.py

Find the latest pedestal FDFs in a directory, decode them, analyze per-channel
statistics (raw + CNS RMS), and produce a QA PDF identifying dead/disconnected strips.

Usage:
    python3 pedestal_strip_check.py [data_dir] [output_dir]

"Latest" = the _pedthr_ FDF group sharing the most recent YYMMDD_HHhMM timestamp.
Groups of 64 channels = one DREAM chip = one physical cable.
"""

import os, re, sys, subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.colors import ListedColormap, BoundaryNorm

# ─── Configuration ────────────────────────────────────────────────────────────

BASE_SOFT  = '/home/mx17/CLionProjects/mm_strip_reconstruction/build'
DECODE_EXE = os.path.join(BASE_SOFT, 'decoder', 'decode')
N_THREADS  = max(1, (os.cpu_count() or 1) - 2)

N_CH_PER_FEU = 512   # 8 DREAMs × 64 channels
N_CH_DREAM   = 64

# Thresholds relative to the global reference CNS RMS
NOISY_FLOAT_MULT   = 3.0   # ch rms > N × ref → noisy/floating (not plugged in)
DEAD_STUCK_FRAC    = 0.10  # ch rms < F × dream_median → stuck/dead
DREAM_SUSPECT_MULT = 2.5   # dream median > N × ref → suspect cable
DREAM_BAD_FRAC     = 0.50  # fraction bad channels for a DREAM to be "disconnected"

RAIL_LOW_MEAN  = 15.0
RAIL_HIGH_MEAN = 4080.0

# ─── Filename helpers ─────────────────────────────────────────────────────────

_TS_RE  = re.compile(r'_pedthr_(\d{6}_\d{2}[Hh]\d{2})_')
_NUM_RE = re.compile(r'_(\d{3})_(\d{2})\.(fdf|root)$', re.IGNORECASE)


def _parse_ts(fn):
    m = _TS_RE.search(fn)
    if not m: return None
    try: return datetime.strptime(m.group(1).upper().replace('H', ''), '%y%m%d_%H%M')
    except ValueError: return None


def _feu_num(fn):
    m = _NUM_RE.search(os.path.basename(fn))
    return int(m.group(2)) if m else None


def find_latest_ped_fdfs(data_dir):
    fdfs = [f for f in os.listdir(data_dir) if f.endswith('.fdf') and '_pedthr_' in f]
    if not fdfs:
        raise RuntimeError(f'No _pedthr_ FDFs in {data_dir}')
    by_ts = {}
    for f in fdfs:
        ts = _parse_ts(f)
        if ts: by_ts.setdefault(ts, []).append(f)
    if not by_ts:
        raise RuntimeError('Cannot parse timestamps from FDFs')
    latest = max(by_ts)
    chosen = sorted(by_ts[latest])
    print(f'[info] Latest pedestal run: {latest:%Y-%m-%d %H:%M}  '
          f'({len(by_ts)} run(s) in dir, chose latest → {len(chosen)} FDFs)')
    for f in chosen: print(f'         {f}')
    return [os.path.join(data_dir, f) for f in chosen]


# ─── Decode ──────────────────────────────────────────────────────────────────

def _decode_one(fdf, root):
    if not os.path.exists(DECODE_EXE):
        raise FileNotFoundError(DECODE_EXE)
    subprocess.run([DECODE_EXE, fdf, root], check=True)
    return root


def decode_fdfs(fdf_paths, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    pending, result = {}, {}
    with ThreadPoolExecutor(max_workers=N_THREADS) as pool:
        for fdf in fdf_paths:
            feu = _feu_num(fdf)
            if feu is None:
                print(f'[warn] No FEU number in {os.path.basename(fdf)}'); continue
            root = os.path.join(out_dir, os.path.basename(fdf).replace('.fdf', '.root'))
            if os.path.exists(root):
                print(f'[decode] {os.path.basename(fdf)} already done, reusing')
                result[feu] = root; continue
            print(f'[decode] {os.path.basename(fdf)}')
            pending[pool.submit(_decode_one, fdf, root)] = (feu, root)
        for fut in as_completed(pending):
            feu, root = pending[fut]
            try:
                fut.result(); result[feu] = root
            except Exception as e:
                print(f'[error] FEU {feu:02d}: {e}')
    return result


# ─── Pedestal analysis ────────────────────────────────────────────────────────

def analyze_pedestal_root(root_path):
    """
    Per-channel mean, raw RMS, and CNS RMS.

    Raw RMS  = std(raw amplitudes) across all events/samples.
    CNS RMS  = std after subtracting the per-sample median across each 64-ch
               DREAM block — matches WaveformAnalyzer::computePedestals() in C++.
    """
    import uproot

    with uproot.open(root_path) as f:
        nt = f['nt']
        n_events  = nt.num_entries
        ch_arrs   = nt['channel'].array(library='np')
        samp_arrs = nt['sample'].array(library='np')
        amp_arrs  = nt['amplitude'].array(library='np')

    raw_s  = {}; raw_s2 = {}; raw_n = {}
    cns_s2 = {}; cns_n  = {}

    for evt_chs, evt_samps, evt_amps in zip(ch_arrs, samp_arrs, amp_arrs):
        evt_chs  = np.asarray(evt_chs,  dtype=np.int32)
        evt_samps= np.asarray(evt_samps,dtype=np.int32)
        evt_amps = np.asarray(evt_amps, dtype=np.float32)

        # raw accumulation
        for ch, amp in zip(evt_chs.tolist(), evt_amps.tolist()):
            raw_s[ch]  = raw_s.get(ch, 0.0)  + amp
            raw_s2[ch] = raw_s2.get(ch, 0.0) + amp * amp
            raw_n[ch]  = raw_n.get(ch, 0)    + 1

        # CNS: per sample, subtract median of 64-ch DREAM block
        for s in np.unique(evt_samps):
            mask_s = evt_samps == s
            chs_s  = evt_chs[mask_s]
            amps_s = evt_amps[mask_s]
            for d in range(N_CH_PER_FEU // N_CH_DREAM):
                lo, hi = d * N_CH_DREAM, (d + 1) * N_CH_DREAM
                mask_d = (chs_s >= lo) & (chs_s < hi)
                if not mask_d.any(): continue
                median = float(np.median(amps_s[mask_d]))
                for ch, amp in zip(chs_s[mask_d].tolist(), amps_s[mask_d].tolist()):
                    r = amp - median
                    cns_s2[ch] = cns_s2.get(ch, 0.0) + r * r
                    cns_n[ch]  = cns_n.get(ch, 0)    + 1

    stats = {}
    for ch in raw_n:
        n    = raw_n[ch]
        mean = raw_s[ch] / n
        raw_rms = float(np.sqrt(max(0.0, raw_s2[ch] / n - mean ** 2)))
        cn = cns_n.get(ch, 0)
        cns_rms = float(np.sqrt(cns_s2.get(ch, 0.0) / cn)) if cn > 0 else float('nan')
        stats[ch] = {'mean': float(mean), 'raw_rms': raw_rms,
                     'cns_rms': cns_rms, 'count': n}

    return {'channel_stats': stats, 'n_events': n_events}


def compute_dream_stats(channel_stats):
    """Per-DREAM summary: median mean/raw_rms/cns_rms and common-noise estimate."""
    result = []
    for d in range(N_CH_PER_FEU // N_CH_DREAM):
        chs = range(d * N_CH_DREAM, (d + 1) * N_CH_DREAM)
        raw  = [channel_stats[c]['raw_rms'] for c in chs if c in channel_stats]
        cns  = [channel_stats[c]['cns_rms'] for c in chs
                if c in channel_stats and not np.isnan(channel_stats[c]['cns_rms'])]
        means= [channel_stats[c]['mean'] for c in chs if c in channel_stats]

        med_raw  = float(np.median(raw))  if raw  else float('nan')
        med_cns  = float(np.median(cns))  if cns  else float('nan')
        med_mean = float(np.median(means))if means else float('nan')
        common   = float(np.sqrt(max(0.0, med_raw**2 - med_cns**2))) \
                   if not (np.isnan(med_raw) or np.isnan(med_cns)) else float('nan')

        result.append({'dream': d, 'n_present': len(raw),
                       'med_raw_rms': med_raw, 'med_cns_rms': med_cns,
                       'med_mean': med_mean, 'common_noise': common})
    return result


def _global_ref_rms(all_dream_stats):
    """Median of per-DREAM CNS RMS medians across all FEUs (robust global reference)."""
    vals = [ds['med_cns_rms'] for feu_ds in all_dream_stats.values()
            for ds in feu_ds if not np.isnan(ds['med_cns_rms'])]
    return float(np.median(vals)) if vals else 1.0


def classify(channel_stats, dream_stats, ref_rms):
    """
    Returns ch_cls {ch->status} and dream_cls {dream->status}.

    Channel statuses: ok | dead_stuck | noisy_floating | railed_low | railed_high | missing
    DREAM statuses:   ok | suspect_cable | disconnected_cable
    """
    dream_med = {ds['dream']: ds['med_cns_rms'] for ds in dream_stats}

    ch_cls = {}
    for ch in range(N_CH_PER_FEU):
        if ch not in channel_stats:
            ch_cls[ch] = 'missing'; continue
        s    = channel_stats[ch]
        rms  = s['cns_rms']
        mean = s['mean']
        d    = ch // N_CH_DREAM
        d_med = dream_med.get(d, ref_rms)

        if np.isnan(rms) or (d_med > 0 and rms < DEAD_STUCK_FRAC * d_med):
            ch_cls[ch] = 'dead_stuck'
        elif mean < RAIL_LOW_MEAN:
            ch_cls[ch] = 'railed_low'
        elif mean > RAIL_HIGH_MEAN:
            ch_cls[ch] = 'railed_high'
        elif rms > NOISY_FLOAT_MULT * ref_rms:
            ch_cls[ch] = 'noisy_floating'
        else:
            ch_cls[ch] = 'ok'

    dream_cls = {}
    for ds in dream_stats:
        d      = ds['dream']
        med    = ds['med_cns_rms']
        chs_d  = range(d * N_CH_DREAM, (d + 1) * N_CH_DREAM)
        n_bad  = sum(1 for c in chs_d if ch_cls.get(c, 'ok') != 'ok')
        if not np.isnan(med) and med > DREAM_SUSPECT_MULT * ref_rms:
            dream_cls[d] = 'disconnected_cable'
        elif n_bad / N_CH_DREAM > DREAM_BAD_FRAC:
            dream_cls[d] = 'suspect_cable'
        else:
            dream_cls[d] = 'ok'

    return ch_cls, dream_cls


# ─── Plot helpers ─────────────────────────────────────────────────────────────

_S_ORDER = ['ok', 'dead_stuck', 'noisy_floating', 'railed_low', 'railed_high', 'missing']
_S_COLOR = {'ok': 'steelblue', 'dead_stuck': 'limegreen', 'noisy_floating': 'red',
            'railed_low': 'purple', 'railed_high': 'saddlebrown', 'missing': 'black'}
_S_LABEL = {'ok': 'OK', 'dead_stuck': 'Dead/stuck (low RMS)',
            'noisy_floating': 'Noisy/floating (not plugged in?)',
            'railed_low': 'Railed low', 'railed_high': 'Railed high', 'missing': 'Missing'}
_D_COLOR = {'ok': 'tab:green', 'suspect_cable': 'tab:orange', 'disconnected_cable': 'tab:red'}
_D_LABEL = {'ok': 'OK', 'suspect_cable': 'Suspect', 'disconnected_cable': 'Disconnected'}

_S_INT  = {s: i for i, s in enumerate(_S_ORDER)}
_CMAP   = ListedColormap([_S_COLOR[s] for s in _S_ORDER])
_NORM   = BoundaryNorm(range(len(_S_ORDER) + 1), len(_S_ORDER))


def _dream_vlines(ax):
    for b in range(N_CH_DREAM, N_CH_PER_FEU, N_CH_DREAM):
        ax.axvline(b - 0.5, color='gray', lw=0.5, ls=':')


# ─── Plot 1: channel map (all FEUs) ──────────────────────────────────────────

def plot_channel_map(all_feu_data):
    feu_nums = sorted(all_feu_data)
    n_feu = len(feu_nums)
    grid  = np.zeros((n_feu, N_CH_PER_FEU), dtype=int)
    for row, feu in enumerate(feu_nums):
        for ch, s in all_feu_data[feu]['ch_cls'].items():
            if ch < N_CH_PER_FEU:
                grid[row, ch] = _S_INT.get(s, 0)

    fig, ax = plt.subplots(figsize=(18, max(3, n_feu * 0.8 + 2)))
    ax.imshow(grid, aspect='auto', cmap=_CMAP, norm=_NORM, interpolation='none',
              origin='upper', extent=[-0.5, N_CH_PER_FEU - 0.5, n_feu - 0.5, -0.5])

    ax.set_yticks(range(n_feu))
    ax.set_yticklabels([f'FEU {f:02d}' for f in feu_nums])
    # Dream label ticks
    tick_xs = [d * N_CH_DREAM + N_CH_DREAM / 2 - 0.5 for d in range(N_CH_PER_FEU // N_CH_DREAM)]
    ax.set_xticks(tick_xs)
    ax.set_xticklabels([f'D{d}' for d in range(N_CH_PER_FEU // N_CH_DREAM)], fontsize=9)
    for b in range(N_CH_DREAM, N_CH_PER_FEU, N_CH_DREAM):
        ax.axvline(b - 0.5, color='gray', lw=0.8)
    ax.set_xlabel('DREAM (D0–D7 = cable 0–7, each 64 channels)')
    ax.set_title('Channel status map — all FEUs')
    handles = [mpatches.Patch(facecolor=_S_COLOR[s], label=_S_LABEL[s]) for s in _S_ORDER]
    ax.legend(handles=handles, loc='upper right', fontsize=8, ncol=3, framealpha=0.9)
    plt.tight_layout()
    return fig


# ─── Plot 2: per-FEU raw+CNS RMS vs channel ──────────────────────────────────

def plot_feu_rms(feu_num, stats, ch_cls, dream_cls, ref_rms):
    ch_s   = stats['channel_stats']
    n_evt  = stats['n_events']
    chs    = sorted(ch_s)
    means  = [ch_s[c]['mean']    for c in chs]
    r_rms  = [ch_s[c]['raw_rms'] for c in chs]
    c_rms  = [ch_s[c]['cns_rms'] if not np.isnan(ch_s[c]['cns_rms']) else 0 for c in chs]
    colors = [_S_COLOR.get(ch_cls.get(c, 'ok'), 'steelblue') for c in chs]

    fig, axes = plt.subplots(3, 1, figsize=(14, 10), sharex=True)
    fig.suptitle(f'FEU {feu_num:02d}  —  Per-channel pedestal  ({n_evt} events)', fontsize=12)

    for ax, vals, ylabel, extra in [
        (axes[0], means,  'Pedestal mean (ADC)', []),
        (axes[1], r_rms,  'Raw RMS (ADC)',        [('gray', '--', f'Median {np.median(r_rms):.1f}', np.median(r_rms))]),
        (axes[2], c_rms,  'CNS RMS (ADC)',        [('steelblue', ':', f'Global ref {ref_rms:.1f}', ref_rms),
                                                    ('red', '--', f'{NOISY_FLOAT_MULT}x ref = {NOISY_FLOAT_MULT*ref_rms:.1f}', NOISY_FLOAT_MULT*ref_rms),
                                                    ('gray', '-.', f'Median {np.median(c_rms):.1f}', np.median(c_rms))]),
    ]:
        ax.scatter(chs, vals, c=colors, s=6, linewidths=0, zorder=3)
        for color, ls, lbl, val in extra:
            ax.axhline(val, color=color, lw=0.9, ls=ls, label=lbl)
        if axes[0] is ax:
            ax.axhline(RAIL_LOW_MEAN,  color='purple',      lw=0.9, ls='--', label=f'Rail low {RAIL_LOW_MEAN:.0f}')
            ax.axhline(RAIL_HIGH_MEAN, color='saddlebrown', lw=0.9, ls='--', label=f'Rail high {RAIL_HIGH_MEAN:.0f}')
        ax.set_ylabel(ylabel, fontsize=9)
        ax.legend(fontsize=7, loc='upper right')
        ax.grid(alpha=0.3)
        _dream_vlines(ax)

    # DREAM status badges along top
    ylim = axes[0].get_ylim()
    for d in range(N_CH_PER_FEU // N_CH_DREAM):
        xc  = d * N_CH_DREAM + N_CH_DREAM / 2 - 0.5
        cls = dream_cls.get(d, 'ok')
        col = _D_COLOR[cls]
        badge = {'ok': 'OK', 'suspect_cable': 'SUSP', 'disconnected_cable': 'DISC'}[cls]
        axes[0].text(xc, ylim[1], f'D{d} {badge}', ha='center', va='bottom',
                     fontsize=7, color=col, fontweight='bold')

    axes[2].set_xlabel('Channel  (DREAM x 64 + strip)', fontsize=9)
    plt.tight_layout()
    return fig


# ─── Plot 3: per-DREAM bar chart ──────────────────────────────────────────────

def plot_dream_bars(feu_num, dream_stats, dream_cls, ch_cls, ref_rms):
    n = len(dream_stats)
    xs = np.arange(n)
    w  = 0.32

    raw_vals = [ds['med_raw_rms']   for ds in dream_stats]
    cns_vals = [ds['med_cns_rms'] if not np.isnan(ds['med_cns_rms']) else 0 for ds in dream_stats]
    cm_vals  = [ds['common_noise'] if not np.isnan(ds['common_noise']) else 0 for ds in dream_stats]
    bcolors  = [_D_COLOR.get(dream_cls.get(ds['dream'], 'ok'), 'tab:green') for ds in dream_stats]

    n_bad_per_dream = []
    for ds in dream_stats:
        d = ds['dream']
        n_bad = sum(1 for c in range(d*N_CH_DREAM, (d+1)*N_CH_DREAM)
                    if ch_cls.get(c, 'ok') != 'ok')
        n_bad_per_dream.append(n_bad)

    fig, axes = plt.subplots(2, 1, figsize=(12, 8))
    fig.suptitle(f'FEU {feu_num:02d}  —  Per-DREAM (cable) summary', fontsize=12)

    ax = axes[0]
    b1 = ax.bar(xs - w/2, raw_vals, w, label='Raw RMS (median)',
                color=bcolors, alpha=0.5, edgecolor='k', linewidth=0.6)
    b2 = ax.bar(xs + w/2, cns_vals, w, label='CNS RMS (median)',
                color=bcolors, alpha=1.0, edgecolor='k', linewidth=0.6)
    ax.axhline(ref_rms,                    color='steelblue', lw=1.2, ls='--',
               label=f'Global ref RMS  {ref_rms:.1f}')
    ax.axhline(NOISY_FLOAT_MULT * ref_rms, color='red',       lw=1.2, ls='--',
               label=f'Noisy threshold  {NOISY_FLOAT_MULT}x ref = {NOISY_FLOAT_MULT*ref_rms:.1f}')
    ax.set_xticks(xs)
    ax.set_xticklabels([f'D{ds["dream"]}\nch{ds["dream"]*64}–{ds["dream"]*64+63}' for ds in dream_stats], fontsize=8)
    ax.set_ylabel('Median RMS (ADC)')
    ax.set_title('Median per-DREAM RMS  (light bar = raw, solid = CNS)')
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3, axis='y')

    # annotate n_bad and status
    for i, ds in enumerate(dream_stats):
        ymax = max(raw_vals[i], cns_vals[i])
        dcls = dream_cls.get(ds['dream'], 'ok')
        lbl  = _D_LABEL[dcls]
        ax.text(xs[i], ymax * 1.04, f'{lbl}\n{n_bad_per_dream[i]} bad',
                ha='center', va='bottom', fontsize=7,
                color=_D_COLOR[dcls], fontweight='bold')

    ax = axes[1]
    ax.bar(xs, cm_vals, color=bcolors, edgecolor='k', linewidth=0.6, alpha=0.85)
    ax.set_xticks(xs)
    ax.set_xticklabels([f'D{ds["dream"]}' for ds in dream_stats], fontsize=9)
    ax.set_ylabel('Common-mode noise (ADC)\n= sqrt(raw_rms^2 - cns_rms^2)')
    ax.set_title('Estimated common-mode noise per DREAM cable')
    ax.grid(alpha=0.3, axis='y')
    legend_handles = [mpatches.Patch(facecolor=_D_COLOR[k], label=_D_LABEL[k])
                      for k in _D_COLOR]
    ax.legend(handles=legend_handles, fontsize=8, loc='upper right')

    plt.tight_layout()
    return fig


# ─── Plot 4: summary table ────────────────────────────────────────────────────

def plot_summary_table(all_feu_data, ref_rms):
    feu_nums = sorted(all_feu_data)
    col_labels = ['FEU', 'Events', 'OK chs', 'Noisy/Float', 'Dead/Stuck',
                  'Railed/Miss', 'Med CNS RMS', 'Problem DREAMs']
    rows, row_colors = [], []

    for feu in feu_nums:
        d      = all_feu_data[feu]
        ch_cls = d['ch_cls']
        d_cls  = d['dream_cls']
        n_ok   = sum(1 for s in ch_cls.values() if s == 'ok')
        n_nois = sum(1 for s in ch_cls.values() if s == 'noisy_floating')
        n_dead = sum(1 for s in ch_cls.values() if s == 'dead_stuck')
        n_misc = sum(1 for s in ch_cls.values() if s not in ('ok','noisy_floating','dead_stuck'))
        cns_v  = [v['cns_rms'] for v in d['stats']['channel_stats'].values()
                  if not np.isnan(v['cns_rms'])]
        med_cns= f'{np.median(cns_v):.1f}' if cns_v else 'N/A'
        bad_ds = ', '.join(f'D{dd}' for dd, ds in sorted(d_cls.items()) if ds != 'ok') or '—'

        rows.append([f'FEU {feu:02d}', d['stats']['n_events'],
                     n_ok, n_nois, n_dead, n_misc, med_cns, bad_ds])

        n_bad = N_CH_PER_FEU - n_ok
        frac  = n_bad / N_CH_PER_FEU
        row_colors.append('#ffcccc' if frac > 0.5 else '#fff3cc' if frac > 0.1 else '#d4edda')

    fig_h = max(3, len(feu_nums) * 0.55 + 3)
    fig, ax = plt.subplots(figsize=(15, fig_h))
    ax.axis('off')
    ax.set_title(
        f'Summary — Global reference CNS RMS: {ref_rms:.1f} ADC  |  '
        f'Noisy threshold: >{NOISY_FLOAT_MULT}x = {NOISY_FLOAT_MULT*ref_rms:.1f}  |  '
        f'Dead threshold: <{DEAD_STUCK_FRAC}x DREAM median',
        fontsize=10, pad=14)

    tbl = ax.table(cellText=rows, colLabels=col_labels,
                   loc='center', cellLoc='center')
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(9)
    tbl.auto_set_column_width(list(range(len(col_labels))))
    for i, bg in enumerate(row_colors):
        for j in range(len(col_labels)):
            tbl[i + 1, j].set_facecolor(bg)
    # header row
    for j in range(len(col_labels)):
        tbl[0, j].set_facecolor('#c8d8e8')
        tbl[0, j].set_text_props(fontweight='bold')

    plt.tight_layout()
    return fig


# ─── Console summary ──────────────────────────────────────────────────────────

def print_summary(all_feu_data, ref_rms):
    print('\n' + '=' * 65)
    print('PEDESTAL QA SUMMARY')
    print(f'  Global reference CNS RMS : {ref_rms:.1f} ADC')
    print(f'  Noisy/float threshold    : > {NOISY_FLOAT_MULT}x ref = {NOISY_FLOAT_MULT*ref_rms:.1f} ADC')
    print(f'  Dead/stuck threshold     : < {DEAD_STUCK_FRAC}x DREAM median')
    print('=' * 65)
    total_bad = 0
    for feu in sorted(all_feu_data):
        d      = all_feu_data[feu]
        ch_cls = d['ch_cls']
        d_cls  = d['dream_cls']
        bad    = {c: s for c, s in ch_cls.items() if s != 'ok'}
        total_bad += len(bad)
        counts = {}
        for s in bad.values(): counts[s] = counts.get(s, 0) + 1
        tag = ' | '.join(f'{v} {k}' for k, v in counts.items())
        print(f'\nFEU {feu:02d}  ({d["stats"]["n_events"]} events):')
        print(f'  {len(bad)} bad channels [{tag}]' if bad else '  All channels OK')
        for dd, dcls in sorted(d_cls.items()):
            if dcls != 'ok':
                ds = next(x for x in d['dream_stats'] if x['dream'] == dd)
                print(f'  --> DREAM {dd} (ch {dd*N_CH_DREAM}-{dd*N_CH_DREAM+63}): '
                      f'{dcls}  med_cns_rms={ds["med_cns_rms"]:.1f} ADC')
    print(f'\nTotal bad channels: {total_bad} / {len(all_feu_data)*N_CH_PER_FEU}')
    print('=' * 65)


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    data_dir   = sys.argv[1] if len(sys.argv) > 1 else '/mnt/data/x17/beam_july/test/test1'
    output_dir = sys.argv[2] if len(sys.argv) > 2 else data_dir
    os.makedirs(output_dir, exist_ok=True)
    print(f'[info] Data dir:   {data_dir}')
    print(f'[info] Output dir: {output_dir}')

    fdf_paths    = find_latest_ped_fdfs(data_dir)
    feu_root_map = decode_fdfs(fdf_paths, data_dir)
    if not feu_root_map:
        print('[error] No ROOT files produced.'); sys.exit(1)

    # Analyze per-FEU
    raw_data = {}
    for feu in sorted(feu_root_map):
        print(f'[analyze] FEU {feu:02d}')
        stats   = analyze_pedestal_root(feu_root_map[feu])
        d_stats = compute_dream_stats(stats['channel_stats'])
        raw_data[feu] = {'stats': stats, 'dream_stats': d_stats}

    # Global reference RMS: median across all DREAM CNS medians
    ref_rms = _global_ref_rms({feu: d['dream_stats'] for feu, d in raw_data.items()})
    print(f'\n[info] Global reference CNS RMS: {ref_rms:.1f} ADC')

    # Classify channels and DREAMs
    all_feu_data = {}
    for feu, d in raw_data.items():
        ch_cls, dream_cls = classify(d['stats']['channel_stats'], d['dream_stats'], ref_rms)
        all_feu_data[feu] = {**d, 'ch_cls': ch_cls, 'dream_cls': dream_cls}

    print_summary(all_feu_data, ref_rms)

    # Write PDF
    pdf_path = os.path.join(output_dir, 'pedestal_strip_check.pdf')
    with PdfPages(pdf_path) as pdf:
        # p1: overview channel map
        pdf.savefig(plot_channel_map(all_feu_data), bbox_inches='tight')
        plt.close('all')
        # p2: summary table
        pdf.savefig(plot_summary_table(all_feu_data, ref_rms), bbox_inches='tight')
        plt.close('all')
        # per-FEU pages
        for feu in sorted(all_feu_data):
            d = all_feu_data[feu]
            pdf.savefig(plot_feu_rms(feu, d['stats'], d['ch_cls'],
                                     d['dream_cls'], ref_rms), bbox_inches='tight')
            plt.close('all')
            pdf.savefig(plot_dream_bars(feu, d['dream_stats'], d['dream_cls'],
                                        d['ch_cls'], ref_rms), bbox_inches='tight')
            plt.close('all')

    print(f'\n[done] PDF -> {pdf_path}')


if __name__ == '__main__':
    main()
