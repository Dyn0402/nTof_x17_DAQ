# Reading n_TOF raw stream files (`runNNNNNN_M_sS.raw`)

Reverse-engineered on 2026-07-22 from the files themselves, cross-checked against the
published description of the DAQ. Reader: `~/beam_july/analysis/sipm_wall_filesize/ntof_raw.py`.

**Why this exists.** The SiPM-wall dropout study started from file *sizes*; this lets us
look at the actual waveforms instead. It is also the only way to answer "which detector
stopped contributing", which size alone cannot.

## Where the files are

    root://eospublic.cern.ch//eos/experiment/ntof/DAQ/2026/EAR2/X17_measurement/<run>/stream1/

`<run>` is the n_TOF run number (224524, 224528, …), one directory per run, ~150 files of
~1–2.5 GiB each, named `run<run>_<seq>_s1.raw.finished`. There is also a small `stream0`.
No FUSE mount — use xrootd. To pull just the head of a file (a few events is plenty):

    xrdfs root://eospublic.cern.ch cat <path> | head -c 150000000 > sample.bin

The reader stops cleanly at a truncated final bank, so a partial download is fine.

## Format

A file is a flat sequence of **banks** (the BOS-bank scheme described in Masi et al.,
*The CERN n_TOF Facility Data Acquisition System*, ICALEPCS'2017, THPHA195). Every bank is

| field | type | note |
|---|---|---|
| tag | `char[4]` | `RCTR`, `MODH`, `EVEH`, `ADDH`, `ACQC` |
| version | `uint32` | |
| reserved | `uint32` | 0 in everything seen |
| length | `uint32` | payload length in 32-bit **words** |
| payload | `uint8[4*length]` | |

Header is 16 bytes; banks are contiguous, no padding between them. Order in the file:

    RCTR                      run control: run number, area ('EAR2'), start time
    MODH                      module header: one record per configured channel
    [ EVEH ADDH ACQC ACQC … ] repeated once per proton pulse

### `RCTR` — run control
`uint32 run`, `uint32 file_seq`, …, `char[4] area` at byte 12, then timestamps and counts
(word 7 = number of configured channels, 51 for our 2026 setup).

### `MODH` — module header
`uint32 n_channels`, then `n_channels` records of **88 bytes**:

    char[4]  detector    'WALA'..'WALD', 'PSSA'..'PSSD', 'LIQA'..'LIQD', 'LIQU',
                         'SILI', 'TOF2', 'VGR1'..'VGR4', 'RMPA', 'RMPC', 'PKUP', 'EAST'
    uint32   channel
    char[4]  card        'S014'
    uint8[4] ids         (chassis / card / channel ids)
    float32  1000.0      sampling rate in MS/s  -> 1 GS/s, 1 sample = 1 ns
    uint32   20000       acquisition window in us -> 20 ms = 20 000 000 samples
    float32  ~2018       full scale (mV)
    ...      offsets / thresholds (int32 threshold, float32 baseline offset, 256, 512)
    'INTC'   uint32 n    length of the always-kept initial block: 30000 for the walls
                         (30 us around the gamma flash), 50000 for PKUP

### `EVEH` — event header
`uint32` words: `w[2]` = run, `w[3]` = PS cycle / event counter, `w[4]` = run-wide id,
plus two 64-bit timestamps. `ADDH` follows with beam-cycle info (`lsaCycle` ASCII).

### `ACQC` — acquired channel (one bank per channel per event)

    char[4]  detector
    uint32   channel
    uint32   flags
    then a sequence of ZERO-SUPPRESSED BLOCKS, each:
        uint64 start     first sample index within the 20 ms window (= ns since t0)
        uint64 n         samples in this block
        int16[n]         samples, little-endian, SIGNED ADC counts
    padded to a 4-byte boundary at the end of the bank

**Zero suppression is the whole story for file size.** The first block is always kept
(the `INTC` length, 30 µs covering the gamma flash); everything after it is written only
where the signal crossed threshold. A live wall channel emits ~500–700 blocks / ~700 k
samples per event; a dead one emits *only* the mandatory first block.

**Samples are `int16_t`, not `uint16`** (`ntoflib/include/ReaderStructACQC.h`; the cards
are S014/ADQ14 at 16 bits). Every channel carries a ±950 mV `baselineOffsetmV` inside a
±1002 mV range, i.e. it is parked ~95 % of the way toward the rail *opposite* its pulse
direction, so a full-size pulse crosses zero and an unsigned decode turns it into an
apparent jump "through 0 and back from 65 535". Measured baselines (run 224619):

| group | offset | polarity | baseline (signed) | flash amplitude |
|---|---|---|---|---|
| LIQ, PSS, SILI | +950 mV | negative-going | +26 300 … +31 200 | 40 000 – 64 000 |
| WAL | −950 mV | positive-going | ≈ −31 400 | ≈ 32 200 |
| PKUP, RMP | −950 mV | positive-going | −26 700 / −26 300 | beam-proportional |

Two consequences worth knowing before reading samples:

* the zero-suppression **fill value is `0x8000` = −32 768**, bit-identical to the
  negative rail — fill and a genuine clip are told apart only by context (a clip is
  approached sample by sample, a fill is not);
* a block's `start` is the zero-suppression *trigger* sample, but its payload begins
  **259 samples earlier**, so a sample index converts to PSA `tof` as
  `start + j - (259 if start > 0 else 0)`. The always-kept flash block has `start == 0`
  and no pre-samples.

History: this reader decoded the samples unsigned until 2026-07-30, which cost three
retracted findings — see `stream1_monitor/SIGNED_DECODE_FIX_NOTE.md`.

## Using the reader

```python
from ntof_raw import iter_banks, iter_events, channel_summary, parse_modh

# what is in the file, cheaply — no sample decoding
n_ev, banks, payload_bytes, zs_blocks = channel_summary('sample.bin')

# waveforms
for ev in iter_events('sample.bin'):
    for (det, chan), blocks in ev['channels'].items():
        for start, samples in blocks:     # start = ns since the proton pulse
            ...
```

Command line: `python3 ntof_raw.py sample.bin` prints the run header, the channel map and
a per-detector table of banks / MiB per event / ZS blocks per bank. That table alone
identifies a dead detector in seconds.

## Gotchas

- The 20 ms window at 1 GS/s is 20 M samples per channel per event, but the file only ever
  holds the suppressed subset. Never assume a contiguous trace — always walk the blocks
  and use their `start`.
- One event is ~75 MB across all 51 channels, so a 2 GiB file holds only ~26 proton pulses.
- `.finished` in the name means the file is closed; files still being written lack it.
- The format note referenced by the ICALEPCS paper (Masi et al., "n_TOF DAQ raw data file
  format proposal in CASTOR") is unpublished, so everything above is empirical. It parses
  150 MB of two different runs with zero bank-boundary errors, but treat the *meaning* of
  the MODH float fields as informed guesswork.
