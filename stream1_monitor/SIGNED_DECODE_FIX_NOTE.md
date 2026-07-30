# TODO: `ntof_raw.py` decodes stream1 samples with the wrong sign

**Written 2026-07-30 by the X17 analysis side (Dylan Neff / session note).
Nothing in this repo has been changed — this note exists so a session can pick
the fix up cold.** The evidence lives in the analysis repo:
`nTof_x17/ntof_processing/FINDINGS_2026-07-29_signed_decoding.md`.

## The bug, in one line

`stream1_monitor/ntof_raw.py:163` reads ACQC payload samples as `<u2`
(unsigned 16-bit). **They are `int16_t`.**

```python
blocks.append((start, np.frombuffer(payload, dtype='<u2',      # <-- should be '<i2'
                                    offset=pos + 16,
                                    count=min(n, (len(payload) - pos - 16) // 2))))
```

The docstring above it ("samples a uint16 array") is wrong for the same reason.

## Why we are sure

- **ntoflib reads them signed.** `ntoflib/include/ReaderStructACQC.h:41` declares
  the payload as `std::vector<int16_t> data`. Forks are cloned on lxplus at
  `/afs/cern.ch/user/d/dneff/ntof_src/{ntof-detector,ntof-raw-2-root}`.
- **The DAQ settings we write into our own output files agree.** ±32 768 codes
  span ±1002 mV and every channel carries a `baselineOffsetmV` of ±950 mV, i.e.
  it is parked ~95 % of the way toward the rail opposite its pulse direction:

  | group | offset | polarity | measured baseline (signed) |
  |---|---|---|---|
  | LIQ, PSS | +950 mV | negative-going | LIQA +31 222, PSSA +30 830 |
  | WAL, PKUP | −950 mV | positive-going | WALA −31 407, PKUP −26 664 |

  Read unsigned, the WAL/PKUP baselines come out as ~34 100 instead of −31 400.
- **Decoded signed, every trace is continuous.** What the unsigned decode shows
  as a pulse "running through 0 and reappearing near 65 535" is simply a pulse
  crossing zero.
- The cards are S014 (ADQ14) and ntoflib's `getNbBits()` hard-codes 16 for that
  type (`ReaderStructMODH.cpp:665-690`); the data agree — sample values populate
  all residues mod 4, and both −32 768 and +32 767 occur.

## What it cost us

Three findings were recorded and later retracted, and one of them went out in a
draft of the handoff package to n_TOF before being caught:

1. "The ADC wraps under-range instead of clipping" — **there is no wrap.**
   Saturated samples sit at exactly a rail code.
2. "The largest measurable amplitude is the baseline, ~31 000" — it is
   **~63 800**, twice that. A cut at 31 000 sits mid-range: on LIQA it removes
   30 784 hits of which 22 940 (75 %) are ordinary half-scale pulses.
3. "`satuflag` catches only a third to a half of saturated liquid hits" — it
   catches them: 119 of 123 clipped runs carry a flagged hit within 100 ns.

## The fix

1. `ntof_raw.py:163`: `'<u2'` → `'<i2'`, and fix the `parse_acqc` docstring
   ("samples a uint16 array" → int16).
2. **Check the two consumers in this directory.** Both work baseline-relative,
   so they are correct for traces that stay on one side of zero and wrong only
   where a trace crosses it — which is exactly what a large pulse does:
   - `stream1_size_controller.py:502` — `parse_acqc(..., with_samples=True)`
     feeds the baseline/RMS and the FLATLINED/ZEROED detection
     (`WAVEFORM_BASELINE_SAMPLES = 2000`). This is **operational**: it drives the
     stream1 size decisions, so re-check the thresholds calibrated on
     2026-07-22 (51 samples × 16 detectors) still mean the same thing after the
     sign change. Baseline and RMS on quiet leading samples should be unchanged;
     confirm rather than assume.
   - `wall_probe.py:109-112` — `samples.astype(float)` on block 0. Wall pulses
     are positive-going from a **negative** baseline, so this one changes
     wherever a wall pulse crosses zero.
3. Grep wider before declaring it done — anything that reads a `samples` array
   out of `iter_events`/`parse_acqc` inherits the bug:
   `grep -rn "parse_acqc\|iter_events" --include=*.py .`
4. While in there, two related facts worth a comment in the code:
   - **The zero-suppression fill value is `0x8000`, bit-identical to the negative
     rail** (`-32768` signed). Fill and a genuine clip are distinguishable only
     by context — a clip is approached sample by sample, a fill is not. On LIQA
     we counted 17 fill runs against 14 genuine clips in three raw chunks.
   - **A block's `start` is the zero-suppression trigger sample, but the payload
     begins 259 samples earlier** (the pre-samples). So converting a sample index
     to PSA `tof` needs

     ```
     tof = start + j - (259 if start > 0 else 0)
     ```

     Verified against the reprocessed trees: −258.7 ns on LIQA (135/135 pulses,
     spread 1.1 ns) and −258.9 ns on LIQD (85/85). The flash block starts at 0,
     carries no pre-samples, and matches with no offset. Without this the raw and
     reconstructed time bases look mysteriously offset by ~20-28 ns per detector,
     which is what we chased for a while.

## How to verify the fix

Reference chunks are on the analysis laptop at
`/media/dylan/data/x17/ntof_raw_224572/head_*.bin` (7 chunks, 3.0 GB). The
analysis-side tools that already do a signed decode, and can be diffed against
whatever this repo produces after the change, are in
`nTof_x17/ntof_processing/liq_study/`: `saturation_examples.py`,
`saturation_clip_or_wrap.py`, `dump_clips.py`, `verify_satuflag.py`,
`time_base_offset.py`.

Expect after the fix: WAL/PKUP baselines negative (~−31 400 / −26 660), no
sample-to-sample discontinuities of ~65 000, clipped samples exactly at
±32 768/±32 767, and rail-to-rail flips only inside the γ-flash on LIQA/LIQB.

## Still open, and NOT part of this fix

Whether the PSA's wall/undershoot saturation blind spot is worth a merge request
upstream. A wall's saturation is a negative undershoot, opposite to its pulse
direction, so it never lands inside a found pulse window and
`AnalyseSaturation` (`PSA_Functions.cc:2793-2806`) never sees it. That is an
n_TOF-side change in `ntof-detector`, not a change here.
