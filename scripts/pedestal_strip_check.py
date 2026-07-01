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

BASE_SOFT  = os.environ.get('BASE_SOFT', '/home/mx17/CLionProjects/mm_strip_reconstruction/build')
DECODE_EXE = os.environ.get('DECODE_EXE', os.path.join(BASE_SOFT, 'decoder', 'decode'))
N_THREADS  = max(1, (os.cpu_count() or 1) - 2)

N_CH_PER_FEU = 512   # 8 DREAMs × 64 channels
N_CH_DREAM   = 64

# Thresholds relative to the global reference CNS RMS
DEAD_STUCK_FRAC    = 0.40  # ch RAW rms < F × FEU raw-median → dead/stuck (low outlier)
NOISY_RAW_MULT     = 2.0   # ch RAW rms > N × FEU raw-median → noisy (high outlier)
NOISY_FLOAT_MULT   = 3.0   # CNS rms > N × ref → reference line for floating/disconnected cables
DREAM_SUSPECT_MULT = 2.5   # dream median > N × ref → suspect/disconnected cable
DREAM_BAD_FRAC     = 0.50  # fraction bad channels for a DREAM to be "disconnected"

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

def analyze_pedestal_root(root_path, chunk_events=128):
    """
    Per-channel mean, raw RMS, and CNS RMS (vectorized, chunked over events).

    Raw RMS  = std(raw amplitudes) across all events/samples.
    CNS RMS  = std after subtracting, per (event, sample), the median across each
               64-ch DREAM block — the per-channel residual mean is removed, matching
               WaveformAnalyzer::computePedestals() in C++.

    Each chunk of events is scattered into a dense (event, channel, sample) grid;
    missing cells stay NaN and are excluded via the NaN-aware reductions, so the block
    medians and RMSs are computed over the present channels exactly as the original
    per-event loop did. The stats are additive over events, so chunking keeps the peak
    memory bounded (some runs are 400 samples × 512 ch × ~1000 events per FEU).
    """
    import uproot

    with uproot.open(root_path) as f:
        nt = f['nt']
        n_events  = nt.num_entries
        ch_arrs   = nt['channel'].array(library='np')
        samp_arrs = nt['sample'].array(library='np')
        amp_arrs  = nt['amplitude'].array(library='np')

    n_blk  = N_CH_PER_FEU // N_CH_DREAM
    n_samp = 1
    for a in samp_arrs:
        if len(a):
            n_samp = max(n_samp, int(np.asarray(a).max()) + 1)

    raw_s  = np.zeros(N_CH_PER_FEU); raw_s2 = np.zeros(N_CH_PER_FEU)
    cns_s  = np.zeros(N_CH_PER_FEU); cns_s2 = np.zeros(N_CH_PER_FEU)
    cnt    = np.zeros(N_CH_PER_FEU)

    for start in range(0, n_events, chunk_events):
        end = min(start + chunk_events, n_events)
        chs = [np.asarray(a) for a in ch_arrs[start:end]]
        sps = [np.asarray(a) for a in samp_arrs[start:end]]
        ams = [np.asarray(a) for a in amp_arrs[start:end]]
        m   = len(chs)
        lens = np.fromiter((len(a) for a in chs), dtype=np.int64, count=m)
        ev = np.repeat(np.arange(m, dtype=np.int64), lens)
        ch = np.concatenate(chs).astype(np.int64)
        sp = np.concatenate(sps).astype(np.int64)
        am = np.concatenate(ams).astype(np.float64)

        grid = np.full((m, N_CH_PER_FEU, n_samp), np.nan, dtype=np.float64)
        grid[ev, ch, sp] = am

        # Common-noise subtraction: per (event, sample), median across the 64 channels
        # of each DREAM block, subtracted from each channel.
        blocks = grid.reshape(m, n_blk, N_CH_DREAM, n_samp)
        with np.errstate(invalid='ignore'):
            block_med = np.nanmedian(blocks, axis=2, keepdims=True)
        resid = (blocks - block_med).reshape(m, N_CH_PER_FEU, n_samp)

        cnt    += (~np.isnan(grid)).sum(axis=(0, 2))
        raw_s  += np.nansum(grid, axis=(0, 2))
        raw_s2 += np.nansum(grid * grid, axis=(0, 2))
        cns_s  += np.nansum(resid, axis=(0, 2))
        cns_s2 += np.nansum(resid * resid, axis=(0, 2))

    stats = {}
    for ch in range(N_CH_PER_FEU):
        n = int(cnt[ch])
        if n == 0:
            continue
        mean    = raw_s[ch] / n
        raw_rms = float(np.sqrt(max(0.0, raw_s2[ch] / n - mean * mean)))
        # std-dev of the CNS-subtracted values: subtract the per-channel residual mean
        # (each strip's DC offset from its block median), else the "RMS" is dominated by
        # baseline spread, not noise.
        cmean   = cns_s[ch] / n
        cns_rms = float(np.sqrt(max(0.0, cns_s2[ch] / n - cmean * cmean)))
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


def _feu_raw_median(channel_stats):
    """Median raw RMS across all present channels in a FEU (the dead/noisy reference)."""
    vals = [s['raw_rms'] for s in channel_stats.values() if not np.isnan(s['raw_rms'])]
    return float(np.median(vals)) if vals else float('nan')


def classify(channel_stats, dream_stats, ref_rms):
    """
    Returns ch_cls {ch->status} and dream_cls {dream->status}.

    Channel statuses: ok | dead_stuck | noisy_floating | missing
    DREAM statuses:   ok | suspect_cable | disconnected_cable
    """
    feu_med_raw = _feu_raw_median(channel_stats)
    has_med     = not np.isnan(feu_med_raw) and feu_med_raw > 0

    ch_cls = {}
    for ch in range(N_CH_PER_FEU):
        if ch not in channel_stats:
            ch_cls[ch] = 'missing'; continue
        raw_rms = channel_stats[ch]['raw_rms']

        # Both dead and noisy are judged on the RAW RMS relative to the FEU's raw
        # median: a live strip's raw waveform carries common-mode + noise, so raw RMS
        # is tightly clustered across the FEU. A strip far BELOW the median has no
        # variation (dead/stuck); one far ABOVE it is abnormally noisy.
        if np.isnan(raw_rms) or (has_med and raw_rms < DEAD_STUCK_FRAC * feu_med_raw):
            ch_cls[ch] = 'dead_stuck'
        elif has_med and raw_rms > NOISY_RAW_MULT * feu_med_raw:
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

_S_ORDER = ['ok', 'dead_stuck', 'noisy_floating', 'missing']
_S_COLOR = {'ok': 'steelblue', 'dead_stuck': 'limegreen', 'noisy_floating': 'red',
            'missing': 'black'}
_S_LABEL = {'ok': 'OK', 'dead_stuck': 'Dead/stuck (raw RMS << FEU median)',
            'noisy_floating': 'Noisy (raw RMS >> FEU median)', 'missing': 'Missing'}
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

    # Panel 0: pedestal mean — self-scaled to the data, with mean and median lines
    ax = axes[0]
    ax.scatter(chs, means, c=colors, s=6, linewidths=0, zorder=3)
    mean_of_means = float(np.mean(means)) if means else float('nan')
    med_of_means  = float(np.median(means)) if means else float('nan')
    if not np.isnan(mean_of_means):
        ax.axhline(mean_of_means, color='gray', lw=1.0, ls='-',  label=f'Mean {mean_of_means:.1f}')
        ax.axhline(med_of_means,  color='gray', lw=1.0, ls='--', label=f'Median {med_of_means:.1f}')
    ax.set_ylabel('Pedestal mean (ADC)', fontsize=9)
    ax.legend(fontsize=7, loc='upper right'); ax.grid(alpha=0.3); _dream_vlines(ax)

    # Panel 1: RAW RMS — drives the dead (green) / noisy (red) colours. Thresholds are
    # a factor × the FEU's raw median (single lines across the whole FEU). Linear y.
    ax = axes[1]
    ax.scatter(chs, r_rms, c=colors, s=6, linewidths=0, zorder=3)
    feu_med_raw = _feu_raw_median(ch_s)
    if not np.isnan(feu_med_raw):
        ax.axhline(feu_med_raw, color='gray', lw=1.0, ls='-',
                   label=f'FEU raw median {feu_med_raw:.1f}')
        ax.axhline(DEAD_STUCK_FRAC * feu_med_raw, color=_S_COLOR['dead_stuck'], lw=1.0, ls='--',
                   label=f'Dead < {DEAD_STUCK_FRAC:g}× median = {DEAD_STUCK_FRAC*feu_med_raw:.1f}')
        ax.axhline(NOISY_RAW_MULT * feu_med_raw, color=_S_COLOR['noisy_floating'], lw=1.0, ls='--',
                   label=f'Noisy > {NOISY_RAW_MULT:g}× median = {NOISY_RAW_MULT*feu_med_raw:.1f}')
    ax.set_ylabel('Raw RMS (ADC)', fontsize=9)
    ax.legend(fontsize=7, loc='upper right'); ax.grid(alpha=0.3); _dream_vlines(ax)

    # Panel 2: CNS RMS (true noise) — global ref + disconnected-cable threshold (DREAM-level)
    ax = axes[2]
    ax.scatter(chs, c_rms, c=colors, s=6, linewidths=0, zorder=3)
    ax.axhline(ref_rms, color='steelblue', lw=0.9, ls=':', label=f'Global ref {ref_rms:.1f}')
    ax.axhline(DREAM_SUSPECT_MULT * ref_rms, color='red', lw=0.9, ls='--',
               label=f'Disconnected cable > {DREAM_SUSPECT_MULT:g}× ref = {DREAM_SUSPECT_MULT*ref_rms:.1f}')
    ax.set_ylabel('CNS RMS (ADC)', fontsize=9)
    ax.legend(fontsize=7, loc='upper right'); ax.grid(alpha=0.3); _dream_vlines(ax)

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
    bcolors  = [_D_COLOR.get(dream_cls.get(ds['dream'], 'ok'), 'tab:green') for ds in dream_stats]

    dead_per, noisy_per, n_bad_per_dream = [], [], []
    for ds in dream_stats:
        d = ds['dream']
        chs_d  = range(d*N_CH_DREAM, (d+1)*N_CH_DREAM)
        n_dead = sum(1 for c in chs_d if ch_cls.get(c) == 'dead_stuck')
        n_nois = sum(1 for c in chs_d if ch_cls.get(c) == 'noisy_floating')
        dead_per.append(n_dead); noisy_per.append(n_nois)
        n_bad_per_dream.append(sum(1 for c in chs_d if ch_cls.get(c, 'ok') != 'ok'))

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
    bd = ax.bar(xs - w/2, dead_per,  w, color=_S_COLOR['dead_stuck'],
                edgecolor='k', linewidth=0.6, label='Dead')
    bn = ax.bar(xs + w/2, noisy_per, w, color=_S_COLOR['noisy_floating'],
                edgecolor='k', linewidth=0.6, label='Noisy')
    ax.bar_label(bd, fontsize=7, padding=1)
    ax.bar_label(bn, fontsize=7, padding=1)
    ax.set_xticks(xs)
    ax.set_xticklabels([f'D{ds["dream"]}' for ds in dream_stats], fontsize=9)
    ax.set_ylabel('Channel count')
    ax.set_title('Dead / noisy channel count per DREAM cable')
    ax.grid(alpha=0.3, axis='y')
    ax.legend(fontsize=8, loc='upper right')

    plt.tight_layout()
    return fig


# ─── Plot 4: summary table ────────────────────────────────────────────────────

def plot_summary_table(all_feu_data, ref_rms):
    feu_nums = sorted(all_feu_data)
    col_labels = ['FEU', 'Events', 'OK chs', 'Noisy', 'Dead/Stuck',
                  'Missing', 'Med Raw RMS', 'Med CNS RMS', 'Problem DREAMs']
    rows, row_colors = [], []

    for feu in feu_nums:
        d      = all_feu_data[feu]
        ch_cls = d['ch_cls']
        d_cls  = d['dream_cls']
        n_ok   = sum(1 for s in ch_cls.values() if s == 'ok')
        n_nois = sum(1 for s in ch_cls.values() if s == 'noisy_floating')
        n_dead = sum(1 for s in ch_cls.values() if s == 'dead_stuck')
        n_misc = sum(1 for s in ch_cls.values() if s not in ('ok','noisy_floating','dead_stuck'))
        raw_v  = [v['raw_rms'] for v in d['stats']['channel_stats'].values()
                  if not np.isnan(v['raw_rms'])]
        cns_v  = [v['cns_rms'] for v in d['stats']['channel_stats'].values()
                  if not np.isnan(v['cns_rms'])]
        med_raw= f'{np.median(raw_v):.1f}' if raw_v else 'N/A'
        med_cns= f'{np.median(cns_v):.1f}' if cns_v else 'N/A'
        bad_ds = ', '.join(f'D{dd}' for dd, ds in sorted(d_cls.items()) if ds != 'ok') or '—'

        rows.append([f'FEU {feu:02d}', d['stats']['n_events'],
                     n_ok, n_nois, n_dead, n_misc, med_raw, med_cns, bad_ds])

        n_bad = N_CH_PER_FEU - n_ok
        frac  = n_bad / N_CH_PER_FEU
        row_colors.append('#ffcccc' if frac > 0.5 else '#fff3cc' if frac > 0.1 else '#d4edda')

    fig_h = max(3, len(feu_nums) * 0.55 + 3)
    fig, ax = plt.subplots(figsize=(15, fig_h))
    ax.axis('off')
    ax.set_title(
        f'Summary — Dead: raw RMS <{DEAD_STUCK_FRAC:g}× FEU raw-median  |  '
        f'Noisy: raw RMS >{NOISY_RAW_MULT:g}× FEU raw-median  |  '
        f'Disconnected cable: CNS median >{DREAM_SUSPECT_MULT:g}× global ref ({ref_rms:.1f} ADC)',
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


# ─── Plot 5: per-cable (DREAM) bad-channel count table ────────────────────────

def plot_dream_bad_table(all_feu_data):
    """Grid: rows = FEU, cols = D0..D7 cables, each cell = '<dead> / <noisy>' channel
    counts. Cell colour reflects the count: green = 0, yellow = some, red = >= half."""
    feu_nums = sorted(all_feu_data)
    n_dream  = N_CH_PER_FEU // N_CH_DREAM
    col_labels = ['FEU'] + [f'D{d}' for d in range(n_dream)]
    rows, cell_bg = [], []

    for feu in feu_nums:
        d      = all_feu_data[feu]
        ch_cls = d['ch_cls']
        row  = [f'FEU {feu:02d}']
        bgs  = ['white']
        for dd in range(n_dream):
            chs    = range(dd * N_CH_DREAM, (dd + 1) * N_CH_DREAM)
            n_dead = sum(1 for c in chs if ch_cls.get(c) == 'dead_stuck')
            n_nois = sum(1 for c in chs if ch_cls.get(c) == 'noisy_floating')
            row.append(f'{n_dead} / {n_nois}')
            # Colour reflects the displayed count (dead + noisy).
            n_bad = n_dead + n_nois
            if n_bad >= N_CH_DREAM // 2:
                bgs.append('#f4b6b6')   # red: at least half the cable bad
            elif n_bad > 0:
                bgs.append('#fff3cc')   # yellow: some bad channels
            else:
                bgs.append('#d4edda')   # green: no dead/noisy channels
        rows.append(row); cell_bg.append(bgs)

    fig_h = max(3, len(feu_nums) * 0.55 + 2.5)
    fig, ax = plt.subplots(figsize=(13, fig_h))
    ax.axis('off')
    ax.set_title('Bad channels per cable (DREAM) — cell = dead / noisy count\n'
                 'colour by count: green = 0, yellow = some, red = >= half the cable',
                 fontsize=10, pad=14)

    tbl = ax.table(cellText=rows, colLabels=col_labels, loc='center', cellLoc='center')
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(9)
    tbl.auto_set_column_width(list(range(len(col_labels))))
    tbl.scale(1, 1.5)
    for i, bgs in enumerate(cell_bg):
        for j, bg in enumerate(bgs):
            tbl[i + 1, j].set_facecolor(bg)
    for j in range(len(col_labels)):
        tbl[0, j].set_facecolor('#c8d8e8')
        tbl[0, j].set_text_props(fontweight='bold')

    plt.tight_layout()
    return fig


# ─── Plot 6: dead/noisy bar charts per connector, one row per FEU ─────────────

def plot_dead_noisy_bars_all(all_feu_data):
    """One row per FEU (like the channel map), each a grouped bar chart of the
    dead and noisy channel counts for the 8 connectors (DREAMs D0-D7). Shared y so
    FEUs are directly comparable; every non-zero bar is annotated with its count."""
    feu_nums = sorted(all_feu_data)
    n_feu    = len(feu_nums)
    n_dream  = N_CH_PER_FEU // N_CH_DREAM
    xs, w    = np.arange(n_dream), 0.4

    dead, noisy, ymax = {}, {}, 1
    for feu in feu_nums:
        ch_cls = all_feu_data[feu]['ch_cls']
        dd, nn = [], []
        for d in range(n_dream):
            chs = range(d * N_CH_DREAM, (d + 1) * N_CH_DREAM)
            dd.append(sum(1 for c in chs if ch_cls.get(c) == 'dead_stuck'))
            nn.append(sum(1 for c in chs if ch_cls.get(c) == 'noisy_floating'))
        dead[feu], noisy[feu] = dd, nn
        ymax = max(ymax, max(dd + nn))

    fig, axes = plt.subplots(n_feu, 1, figsize=(12, max(4, n_feu * 1.15 + 1)), sharex=True)
    if n_feu == 1:
        axes = [axes]
    fig.suptitle('Dead / noisy channel count per connector (DREAM D0-D7), by FEU', fontsize=12)

    for ax, feu in zip(axes, feu_nums):
        bd = ax.bar(xs - w/2, dead[feu],  w, color=_S_COLOR['dead_stuck'],
                    edgecolor='k', linewidth=0.4, label='Dead')
        bn = ax.bar(xs + w/2, noisy[feu], w, color=_S_COLOR['noisy_floating'],
                    edgecolor='k', linewidth=0.4, label='Noisy')
        ax.bar_label(bd, labels=[str(v) if v else '' for v in dead[feu]],  fontsize=7, padding=1)
        ax.bar_label(bn, labels=[str(v) if v else '' for v in noisy[feu]], fontsize=7, padding=1)
        ax.set_ylim(0, ymax * 1.18)
        ax.set_ylabel(f'FEU {feu:02d}', fontsize=9, rotation=0, ha='right', va='center')
        ax.grid(alpha=0.3, axis='y')

    axes[0].legend(fontsize=8, loc='upper right', ncol=2)
    axes[-1].set_xticks(xs)
    axes[-1].set_xticklabels([f'D{d}' for d in range(n_dream)])
    axes[-1].set_xlabel('Connector (DREAM D0-D7 = cable 0-7, each 64 channels)')
    plt.tight_layout()
    return fig


# ─── Console summary ──────────────────────────────────────────────────────────

def print_summary(all_feu_data, ref_rms):
    print('\n' + '=' * 65)
    print('PEDESTAL QA SUMMARY')
    print(f'  Global reference CNS RMS : {ref_rms:.1f} ADC  (for disconnected-cable flag)')
    print(f'  Noisy threshold          : raw RMS > {NOISY_RAW_MULT}x FEU raw-median')
    print(f'  Dead/stuck threshold     : raw RMS < {DEAD_STUCK_FRAC}x FEU raw-median')
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


# ─── CSV output (per-run status, for tracking / comparison) ───────────────────

def write_channel_csv(all_feu_data, ref_rms, csv_path):
    """One row per channel: the per-run status record used for cross-run comparison."""
    import csv
    with open(csv_path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['feu', 'connector', 'channel', 'strip', 'status',
                    'raw_rms', 'cns_rms', 'mean', 'n_events', 'ref_cns_rms'])
        for feu in sorted(all_feu_data):
            d      = all_feu_data[feu]
            ch_s   = d['stats']['channel_stats']
            ch_cls = d['ch_cls']
            n_evt  = d['stats']['n_events']
            for ch in range(N_CH_PER_FEU):
                s = ch_s.get(ch)
                raw = f'{s["raw_rms"]:.3f}' if s else ''
                cns = f'{s["cns_rms"]:.3f}' if s and not np.isnan(s['cns_rms']) else ''
                mean = f'{s["mean"]:.2f}' if s else ''
                w.writerow([feu, ch // N_CH_DREAM, ch, ch % N_CH_DREAM,
                            ch_cls.get(ch, 'missing'), raw, cns, mean, n_evt, f'{ref_rms:.3f}'])
    print(f'[done] channel CSV -> {csv_path}')


def write_summary_csv(all_feu_data, csv_path):
    """One row per (FEU, connector): dead/noisy counts, median RMS, cable status."""
    import csv
    with open(csv_path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['feu', 'connector', 'n_dead', 'n_noisy', 'n_ok',
                    'med_raw_rms', 'med_cns_rms', 'cable_status'])
        for feu in sorted(all_feu_data):
            d      = all_feu_data[feu]
            ch_cls = d['ch_cls']
            d_cls  = d['dream_cls']
            dmap   = {ds['dream']: ds for ds in d['dream_stats']}
            for dd in range(N_CH_PER_FEU // N_CH_DREAM):
                chs = range(dd * N_CH_DREAM, (dd + 1) * N_CH_DREAM)
                n_dead = sum(1 for c in chs if ch_cls.get(c) == 'dead_stuck')
                n_nois = sum(1 for c in chs if ch_cls.get(c) == 'noisy_floating')
                n_ok   = sum(1 for c in chs if ch_cls.get(c) == 'ok')
                ds     = dmap.get(dd, {})
                mr = f'{ds.get("med_raw_rms", float("nan")):.3f}'
                mc = f'{ds.get("med_cns_rms", float("nan")):.3f}'
                w.writerow([feu, dd, n_dead, n_nois, n_ok, mr, mc, d_cls.get(dd, 'ok')])
    print(f'[done] summary CSV -> {csv_path}')


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
        # p3: per-cable (DREAM) dead/noisy count table
        pdf.savefig(plot_dream_bad_table(all_feu_data), bbox_inches='tight')
        plt.close('all')
        # p4: dead/noisy bar charts per connector, one row per FEU
        pdf.savefig(plot_dead_noisy_bars_all(all_feu_data), bbox_inches='tight')
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

    # Per-run status CSVs (used by compare_pedestals.py for quick cross-run diffs).
    write_channel_csv(all_feu_data, ref_rms, os.path.join(output_dir, 'pedestal_channels.csv'))
    write_summary_csv(all_feu_data, os.path.join(output_dir, 'pedestal_summary.csv'))


if __name__ == '__main__':
    main()
