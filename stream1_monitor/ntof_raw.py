#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Minimal reader for n_TOF DAQ raw stream files (run<RUN>_<n>_s<stream>.raw).

VENDORED COPY. The original lives in ~/beam_july/analysis/sipm_wall_filesize/ and the
format is documented in docs/NTOF_RAW_FORMAT.md. It is copied here so the watcher does
not import from a live analysis directory; re-copy it if the reader is fixed there.

Format (reverse-engineered from the files themselves; matches the BOS-bank
description in Masi et al., ICALEPCS'2017 THPHA195, whose own format note
"n_TOF DAQ raw data file format proposal in CASTOR" is unpublished):

  A file is a flat sequence of BANKS.  Every bank is

      char[4]  tag        e.g. 'RCTR', 'MODH', 'EVEH', 'ADDH', 'ACQC'
      uint32   version
      uint32   reserved   (0 in everything seen so far)
      uint32   length     payload length in 32-bit WORDS
      uint8[4*length]     payload

  Top-level banks, in file order:
      RCTR  run control  — run number, area ('EAR2'), start time, counts
      MODH  module header — one fixed-size record per configured channel:
              char[4] detector name ('WALA'..'WALD', 'PSSA'..'PSSD', 'PKUP',
              'LIQU', 'SILI', 'TOF2', 'VGR1'..'VGR4', ...), uint32 channel id,
              char[4] card name ('S014'), ids, then floats (offset, gain,
              thresholds) and a nested 'INTC' record whose first word is the
              number of samples in the acquisition window.
      then, repeating per proton pulse:
      EVEH  event header — event number, run, timestamps
      ADDH  additional header — beam/cycle info ('lsaCycle' ASCII)
      ACQC  acquired channel — one bank per channel per event:
              char[4] detector, uint32 channel, uint32 flags, then a sequence
              of ZERO-SUPPRESSED BLOCKS, each
                  uint64 start   first sample index within the 20 ms window
                  uint64 n       number of samples in this block
                  uint16[n]      samples (little-endian, unsigned ADC counts)
              padded to a 4-byte boundary at the end of the bank.

  Timing: MODH gives 1000.0 MS/s and a 20000 us window -> 20 000 000 samples
  per event, so a sample index is directly a time in NANOSECONDS since the
  proton pulse.  MODH's nested INTC value (30000 for the walls, 50000 for PKUP)
  is the length of the initial always-kept block: the first 30 us around the
  gamma flash is recorded unconditionally, everything after it survives only if
  it crosses the zero-suppression threshold.

  That is why file size tracks detector liveness: a live wall channel emits
  ~500-700 blocks / ~700 k samples per event, a dead one emits ONLY the
  mandatory first block (1 block, 30 000 samples) and nothing else.

Usage:
    from ntof_raw import iter_banks, iter_events, channel_summary
    for ev in iter_events('run224524_0_s1.raw'):
        print(ev['event'], {k: len(v) for k, v in ev['channels'].items()})
"""

import struct
import sys
from collections import Counter, OrderedDict

HDR = struct.Struct('<4sIII')
HDR_SIZE = HDR.size          # 16

TOP_TAGS = {b'RCTR', b'MODH', b'EVEH', b'ADDH', b'ACQC', b'EVDH'}


def iter_banks(src, max_bytes=None):
    """Yield (offset, tag, version, payload) for each top-level bank.

    `src` is a path or an open binary file.  Reading stops cleanly at a
    truncated final bank, so a partially-downloaded file is fine.
    """
    own = False
    if isinstance(src, (str, bytes)):
        f = open(src, 'rb')
        own = True
    else:
        f = src
    try:
        off = 0
        while True:
            if max_bytes is not None and off >= max_bytes:
                return
            head = f.read(HDR_SIZE)
            if len(head) < HDR_SIZE:
                return
            tag, version, _res, length = HDR.unpack(head)
            nbytes = length * 4
            if tag not in TOP_TAGS or nbytes < 0 or nbytes > (1 << 31):
                raise ValueError(f'bad bank at offset {off}: tag={tag!r} len={length}')
            payload = f.read(nbytes)
            if len(payload) < nbytes:
                return                      # truncated tail
            yield off, tag.decode('ascii'), version, payload
            off += HDR_SIZE + nbytes
    finally:
        if own:
            f.close()


def parse_rctr(payload):
    run = struct.unpack_from('<I', payload, 0)[0]
    area = payload[12:16].decode('ascii', 'replace')
    return {'run': run, 'area': area,
            'words': struct.unpack_from('<8I', payload, 0)}


def parse_modh(payload):
    """Channel configuration records.  Returns list of dicts.

    Payload is uint32 n_channels followed by n_channels fixed 88-byte records:
    name(4) chan(4) card(4) ids(4) then floats and a nested INTC(4)+n_samples(4).
    """
    out = []
    stride = 88
    n_chan = struct.unpack_from('<I', payload, 0)[0]
    for i in range(n_chan):
        base = 4 + i * stride
        if base + stride > len(payload):
            break
        name = payload[base:base + 4]
        if not name.isalnum():
            continue
        chan, card = struct.unpack_from('<I4s', payload, base + 4)
        ids = struct.unpack_from('<4B', payload, base + 12)
        floats = struct.unpack_from('<6f', payload, base + 16)
        intc_off = payload.find(b'INTC', base, base + stride)
        nsamp = (struct.unpack_from('<I', payload, intc_off + 4)[0]
                 if intc_off > 0 else None)
        out.append({'det': name.decode('ascii', 'replace'), 'chan': chan,
                    'card': card.decode('ascii', 'replace'), 'ids': ids,
                    'floats': floats, 'n_samples': nsamp})
    return out


def parse_eveh(payload):
    w = struct.unpack_from('<10I', payload, 0)
    # w[2] = run number, w[3] = PS cycle / event counter, w[4] = a run-wide id
    return {'run': w[2], 'event': w[3], 'words': w}


SAMPLE_RATE_HZ = 1_000_000_000     # 1 GS/s -> 1 sample = 1 ns (from MODH)
WINDOW_SAMPLES = 20_000_000        # 20 ms window


def parse_acqc(payload, with_samples=True):
    """Acquired-channel bank -> (detector, channel, blocks).

    blocks is a list of (start_sample, samples) with samples a uint16 array
    (or just the length when with_samples=False).
    """
    import numpy as np
    det = payload[0:4].decode('ascii', 'replace')
    chan = struct.unpack_from('<I', payload, 4)[0]
    blocks = []
    pos = 12
    while pos + 16 <= len(payload):
        start, n = struct.unpack_from('<QQ', payload, pos)
        if n == 0 or pos + 16 + 2 * n > len(payload) + 2:
            break                                   # trailing pad word
        if with_samples:
            blocks.append((start, np.frombuffer(payload, dtype='<u2',
                                                offset=pos + 16,
                                                count=min(n, (len(payload) - pos - 16) // 2))))
        else:
            blocks.append((start, int(n)))
        pos += 16 + 2 * n
    return det, chan, blocks


def iter_events(src, max_bytes=None, with_samples=True):
    """Yield one dict per proton pulse: header + {(' det', chan): samples}."""
    cur = None
    for off, tag, version, payload in iter_banks(src, max_bytes):
        if tag == 'EVEH':
            if cur is not None:
                yield cur
            cur = dict(parse_eveh(payload), channels=OrderedDict(), offset=off)
        elif tag == 'ACQC' and cur is not None:
            det, chan, blocks = parse_acqc(payload, with_samples)
            cur['channels'][(det, chan)] = blocks
    if cur is not None:
        yield cur


def channel_summary(src, max_bytes=None):
    """Per-detector bank count and total payload bytes — the cheap 'who is
    contributing' view that does not need the samples."""
    n_ev = 0
    banks = Counter()
    payload_bytes = Counter()
    zs_blocks = Counter()
    for off, tag, version, payload in iter_banks(src, max_bytes):
        if tag == 'EVEH':
            n_ev += 1
        elif tag == 'ACQC':
            det = payload[0:4].decode('ascii', 'replace')
            banks[det] += 1
            payload_bytes[det] += len(payload)
            zs_blocks[det] += len(parse_acqc(payload, with_samples=False)[2])
    return n_ev, banks, payload_bytes, zs_blocks


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    path = argv[1]
    print(f'== {path}')
    for off, tag, version, payload in iter_banks(path, max_bytes=2 << 20):
        print(f'  0x{off:08x}  {tag}  v{version}  {len(payload)} B')
        if tag == 'RCTR':
            print('     ', parse_rctr(payload))
        elif tag == 'MODH':
            cfg = parse_modh(payload)
            print(f'      {len(cfg)} channels configured:')
            per = Counter(c['det'] for c in cfg)
            print('     ', dict(per))
            print('      n_samples:', sorted({c['n_samples'] for c in cfg}))
        elif tag == 'EVEH':
            print('     ', parse_eveh(payload))
            break
    n_ev, banks, pb, zs = channel_summary(path)
    print(f'\n  events: {n_ev}')
    print('  detector   banks   payload MiB   MiB/event   ZS blocks/bank')
    for det in sorted(banks):
        print(f'  {det:8s} {banks[det]:6d}   {pb[det]/1024**2:10.1f} '
              f'{pb[det]/1024**2/max(n_ev,1):11.2f} {zs[det]/banks[det]:14.1f}')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
