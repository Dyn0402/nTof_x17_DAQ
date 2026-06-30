# pedestal_strip_check.py — Pedestal QA & Dead-Strip Finder

Quickly decode the latest pedestal FDF files from a run directory, compute
per-channel noise statistics, and produce a PDF report identifying disconnected
or dead strips across all FEUs.

---

## Quick start

```bash
python3 pedestal_strip_check.py [data_dir] [output_dir]
```

`data_dir` defaults to `/mnt/data/x17/beam_july/test/test1`.
`output_dir` defaults to the same directory as the data.
The PDF report is written to `<output_dir>/pedestal_strip_check.pdf`.

**Dependencies:** `uproot`, `numpy`, `matplotlib`
(install with `pip install uproot awkward numpy matplotlib`).
The C++ `decode` executable from `mm_strip_reconstruction` must be built and
pointed to by `DECODE_EXE` at the top of the script.

---

## What "latest FDFs" means

The script parses the `YYMMDD_HHhMM` timestamp embedded in every `_pedthr_`
FDF filename (e.g. `Mx17_test_pedthr_260629_17H47_000_01.fdf` → 2026-06-29
17:47). All FDFs sharing the most recent timestamp in the directory are selected
as the current pedestal run. This means you can safely accumulate multiple
pedestal runs in one directory without mixing them.

---

## Pipeline

```
_pedthr_ FDFs  ──decode (C++)──>  .root  ──uproot──>  per-ch stats  ──>  PDF
```

1. **Decode (parallel):** Each FDF is passed to the C++ `decode` executable,
   which writes a ROOT file containing a TTree `nt` with branches `channel`,
   `sample`, and `amplitude`. ROOT files land next to the FDFs and are reused
   on subsequent runs (no re-decode unless you delete them).

2. **Compute statistics per channel:**
   - **Raw RMS** — `std(amplitude)` across all events and samples for that
     channel. Includes both electronic noise and any common-mode fluctuations
     shared across the 64-channel DREAM block.
   - **CNS RMS** (common-noise-subtracted) — identical to the C++
     `WaveformAnalyzer::computePedestals()` method. Per event, for each sample
     index, the median amplitude across all 64 channels in the same DREAM block
     is subtracted before computing the RMS. This removes correlated
     (common-mode) noise and gives the true per-strip noise floor.
   - **Common-mode noise** — estimated as `sqrt(raw_rms² − cns_rms²)` per
     DREAM. A large common-mode relative to the CNS RMS suggests coupling on
     that cable or noisy power/ground.
   - **Pedestal mean** — raw mean amplitude, used to detect railed channels.

3. **Classification (adaptive thresholds):**
   The global reference RMS is the median of all per-DREAM CNS RMS medians
   across every FEU. Individual channels are then classified as:

   | Status | Criterion | Interpretation |
   |---|---|---|
   | `ok` | CNS RMS within normal range | Strip connected, noise normal |
   | `noisy_floating` | CNS RMS > 3× global ref | Floating input — likely not plugged in |
   | `dead_stuck` | CNS RMS < 10% of DREAM median | Stuck strip, no variation |
   | `railed_low` | Mean < 15 ADC | Input biased to zero rail |
   | `railed_high` | Mean > 4080 ADC | Input biased to max rail |
   | `missing` | Channel absent from data | Never seen in any event |

   A DREAM (cable) is flagged:
   - `disconnected_cable` — median CNS RMS > 2.5× global ref (whole cable floating)
   - `suspect_cable` — ≥ 50% of its channels are individually bad

4. **PDF report (4 pages per FEU + 2 overview pages):**

   - **Page 1 — Channel map:** Grid of all FEUs × 512 channels, coloured by
     status. DREAMs are labelled D0–D7. Good for a one-glance system overview.
   - **Page 2 — Summary table:** One row per FEU with event count, channel
     counts by status, median CNS RMS, and which DREAMs are problematic.
   - **Per FEU — RMS vs channel:** Three panels (mean, raw RMS, CNS RMS) vs
     channel number, coloured by status. Reference and threshold lines drawn.
     DREAM status badges (OK/SUSP/DISC) shown across the top.
   - **Per FEU — DREAM bar chart:** Eight groups, one per DREAM/cable. Each
     group shows median raw RMS (light bar) and CNS RMS (solid bar) side by
     side, annotated with bad-channel count and cable status. Bottom panel
     shows the common-mode noise estimate per cable.

---

## Tunable parameters (top of script)

| Parameter | Default | Meaning |
|---|---|---|
| `BASE_SOFT` | `…/mm_strip_reconstruction/build` | Path to compiled executables |
| `NOISY_FLOAT_MULT` | `3.0` | CNS RMS multiple above reference → noisy/floating |
| `DEAD_STUCK_FRAC` | `0.10` | CNS RMS fraction below DREAM median → dead |
| `DREAM_SUSPECT_MULT` | `2.5` | DREAM median multiple above reference → suspect |
| `DREAM_BAD_FRAC` | `0.50` | Fraction of bad channels to label a DREAM suspect |
| `RAIL_LOW_MEAN` | `15` ADC | Mean below this → railed low |
| `RAIL_HIGH_MEAN` | `4080` ADC | Mean above this → railed high |

---

## Interpreting results

**Not plugged in (most common case):** A DREAM cable that is not connected to
the detector strips has 64 floating inputs. These pick up EMI and show CNS RMS
typically 3–10× higher than connected strips. The whole DREAM will be flagged
`disconnected_cable` and all its channels `noisy_floating`.

**Individual dead strips:** A single strip with broken continuity may appear
as one `noisy_floating` channel within an otherwise healthy DREAM.

**Stuck strip:** A strip shorted to ground or power shows very low RMS
(`dead_stuck`). Common during commissioning if a connector pin is bent.

**Common-mode noise:** If the common-mode noise (bottom panel of the DREAM bar
chart) is large relative to CNS RMS, the cable is picking up interference that
is correlated across all 64 strips. This usually indicates a grounding or
shielding issue with that cable — the detector itself may be fine.

---

## Relationship to the main processing pipeline

This script is independent of `process_run.py` and needs only the raw FDF
files. It does **not** run `analyze_waveforms` or produce hits files — it only
decodes and reads the raw waveform data to compute noise statistics. The
decoded ROOT files it produces are the same format used by the main pipeline,
so they can be reused there.
