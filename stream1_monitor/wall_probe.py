#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Direct SiPM-wall liveness probe: read the first ~2 MB of an n_TOF stream1 raw
file and measure the gamma-flash amplitude of the wall channels in it.

This is the waveform-level replacement for the file-size proxy.  File size says
"something stopped contributing"; this says "WALC ch2's flash is 600 counts
instead of 32 000", which is unambiguous and needs no moving baseline.

Why it is cheap: the gamma flash lives in the first zero-suppressed block of
every ACQC bank (the DAQ always keeps the first 30 us), and the first wall bank
starts ~14 kB into the file.  A 2 MB budget-limited read costs ~0.12 s -- the
same as the `xrdfs ls -l` the monitor already does every 120 s, because the cost
is connection setup, not bytes.

Standalone:
    python3 wall_probe.py                          # newest .finished file
    python3 wall_probe.py 224528                   # newest file of a run
    python3 wall_probe.py 224528 10                # a specific sequence number

As a library:
    from wall_probe import probe_latest, probe_file
    result = probe_latest()          # dict, see `verdict` / `flash_median`

Format details: docs/NTOF_RAW_FORMAT.md.  Findings that motivated it:
docs/HANDOFF_2026-07-22_sipm_wall_dropouts.md.
"""

import json
import os
import subprocess
import sys

import numpy as np

try:
    from .ntof_raw import iter_banks, parse_acqc
except ImportError:                                  # run as a script
    from ntof_raw import iter_banks, parse_acqc

XROOTD_URL = 'root://eospublic.cern.ch'
EOS_BASE = '/eos/experiment/ntof/DAQ/2026/EAR2/X17_measurement'

READ_BYTES = 2 << 20        # 2 MiB budget per probe
WALL_DETS = ('WALA', 'WALB', 'WALC', 'WALD')

# A live wall channel's flash swings from a baseline near the ADC's negative rail
# (~-31 400 counts; samples are SIGNED int16 -- see ntof_raw.parse_acqc) up through
# zero, an amplitude of ~32 200.  A collapsed one gives 350-1200.  Two orders of
# magnitude apart, so the threshold is not delicate and -- unlike a size baseline --
# it does not move with the run configuration.
#
# Both cuts predate the 2026-07-30 signed-decode fix and are unchanged by it: the
# unsigned decode read the above-zero half of the pulse as 65 536 minus itself and so
# reported the same swing as ~34 100, i.e. a live wall moved 34 100 -> 32 200 and a
# dead one did not move at all.  Both stay far from either cut.
FLASH_DEAD_BELOW = 5000.0
FLASH_LIVE_ABOVE = 20000.0


def _run(cmd, **kw):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, **kw)


def latest_run():
    r = _run(f'xrdfs {XROOTD_URL} ls {EOS_BASE}')
    runs = [int(l.rsplit('/', 1)[-1]) for l in r.stdout.split()
            if l.rsplit('/', 1)[-1].isdigit()]
    if not runs:
        raise RuntimeError(f'no runs listed under {EOS_BASE}: {r.stderr.strip()}')
    return max(runs)


def latest_file(run):
    """Newest closed (.finished) file of a run, by sequence number."""
    r = _run(f'xrdfs {XROOTD_URL} ls {EOS_BASE}/{run}/stream1')
    best, best_seq = None, -1
    for line in r.stdout.split():
        name = line.rsplit('/', 1)[-1]
        if not name.endswith('.finished'):
            continue
        try:
            seq = int(name.split('_')[1])
        except (IndexError, ValueError):
            continue
        if seq > best_seq:
            best, best_seq = line, seq
    if best is None:
        raise RuntimeError(f'no .finished files in {EOS_BASE}/{run}/stream1')
    return best, best_seq


def read_head(eos_path, nbytes=READ_BYTES, timeout=120):
    """First `nbytes` of an EOS file.  The pipe is closed once we have enough,
    so only about that much crosses the network."""
    cmd = (f'xrdfs {XROOTD_URL} cat {eos_path} 2>/dev/null | head -c {nbytes}')
    p = subprocess.run(cmd, shell=True, capture_output=True, timeout=timeout)
    return p.stdout


def probe_bytes(blob):
    """Measure every wall channel whose ACQC bank fits inside `blob`."""
    import io
    channels = []
    for off, tag, version, payload in iter_banks(io.BytesIO(blob)):
        if tag != 'ACQC':
            continue
        det = payload[0:4].decode('ascii', 'replace')
        if det not in WALL_DETS:
            continue
        _det, chan, blocks = parse_acqc(payload)
        if not blocks:
            continue
        start, samples = blocks[0]
        if start != 0 or len(samples) < 1000:
            continue                       # not the mandatory flash block
        s = samples.astype(float)
        base = float(np.median(s[:2000]))
        channels.append({
            'det': det, 'chan': int(chan),
            'bank_bytes': len(payload),
            'baseline': round(base, 1),
            'rms': round(float(np.std(s[:2000])), 2),
            'flash_amplitude': round(float(np.max(np.abs(s - base))), 1),
            'zs_blocks': len(blocks),
        })
    return channels


def verdict_from(channels):
    if not channels:
        return 'unknown', None
    flash = float(np.median([c['flash_amplitude'] for c in channels]))
    if flash < FLASH_DEAD_BELOW:
        return 'dead', flash
    if flash > FLASH_LIVE_ABOVE:
        return 'live', flash
    return 'degraded', flash


def probe_file(eos_path, nbytes=READ_BYTES):
    blob = read_head(eos_path, nbytes)
    channels = probe_bytes(blob)
    verdict, flash = verdict_from(channels)
    return {
        'path': eos_path,
        'bytes_read': len(blob),
        'wall_channels_measured': len(channels),
        'dets_measured': sorted({c['det'] for c in channels}),
        'flash_median': None if flash is None else round(flash, 1),
        'verdict': verdict,
        'channels': channels,
        # True = we hit the byte budget, so later walls were not reached.  This
        # is the NORMAL case for a live file (its banks are ~1 MB each), which
        # is why a 'live' verdict from few channels is not a whole-wall clearance.
        'budget_truncated': len(blob) >= nbytes,
    }


def probe_latest(run=None, nbytes=READ_BYTES):
    run = run or latest_run()
    path, seq = latest_file(run)
    out = probe_file(path, nbytes)
    out['run'], out['seq'] = run, seq
    return out


def main(argv):
    run = int(argv[1]) if len(argv) > 1 else None
    if len(argv) > 2:
        path = f'{EOS_BASE}/{run}/stream1/run{run}_{int(argv[2])}_s1.raw.finished'
        res = probe_file(path)
        res['run'], res['seq'] = run, int(argv[2])
    else:
        res = probe_latest(run)
    print(f'run {res["run"]} seq {res["seq"]}  read {res["bytes_read"]/1e6:.2f} MB')
    print(f'  VERDICT: {res["verdict"].upper()}   median flash '
          f'{res["flash_median"]}  ({res["wall_channels_measured"]} wall channels: '
          f'{", ".join(res["dets_measured"])})')
    for c in res['channels']:
        print(f'   {c["det"]}.{c["chan"]}  flash {c["flash_amplitude"]:8.0f}  '
              f'baseline {c["baseline"]:8.0f}  rms {c["rms"]:5.1f}  '
              f'bank {c["bank_bytes"]/1e3:7.0f} kB  blocks {c["zs_blocks"]}')
    print(json.dumps({k: v for k, v in res.items() if k != 'channels'}, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
