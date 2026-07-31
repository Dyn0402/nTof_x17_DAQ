# DREAM ↔ n_TOF matching: the time calibration

**Source: DREAM run_79 (`stat090_0000` + `stat090_0001`, 2061 bunches, 213 420
non-flash triggers) ↔ n_TOF run 224572 processed with `v12_liqpileup`.
Analysis: `nTof_x17/ntof_dream_merge/match_study/`.
Authoritative write-up: `nTof_x17/ntof_dream_merge/DREAM_NTOF_CALIBRATION.md`
(slides: `match_study/latex/dream_ntof_matching_slides.pdf`).**

## ⚠ Two very different kinds of number live in this directory

| file | kind | who owns it | lifetime |
|---|---|---|---|
| `n1081b_thresholds_run_79.json` | **DAQ** — hardware state | the shifter | until someone turns a knob |
| `time_map_*.json` | **offline** — fitted | this analysis | one (DREAM run, n_TOF processing) pair |
| `ntof_internal_alignment_*.json` | **offline** — measured | this analysis | one n_TOF processing |

The DAQ file records what the discriminators **were set to**; it is an *input*
to the offline trigger emulation, not a result of it. If you want to know what
to **set**, that is `../wal_trigger/` and `../pss_trigger/`, which carry
threshold→efficiency/purity/rate scans.

Nothing in the two offline files is dialled into any instrument. They are
applied in software, and **none of them transfers** — see "What transfers".

## What this gives you

```
t_nTOF [ns] = t_DREAM · (1 + K + δk_b) + T0 + a_arm + δa_b
accept        |t_candidate − t_nTOF| < 25 ns          (one band)
```

| | run_79 ↔ 224572 / v12_liqpileup |
|---|---|
| `K` | 1.103724e−4 |
| `T0` | −253.64 ns |
| `a_arm` | A −16.81, B +7.55, C +1.62, D −0.83 ns |
| `δa_b`, `δk_b` | fitted per bunch, always |
| accept window | ±25 ns |

Performance at that window, wall AND plastic: **95.84 % efficiency, 0.049 %
accidental** (measured, not modelled), purity 99.998 %, two-arm ambiguity
0.15 %. Match resolution 6 ns, flat from 1 ms to 80 ms.

## ⚠ What transfers, and what does not

- **`K`, `T0` and the per-arm offsets do NOT transfer between processings.** The
  same constants fitted on the *official* processing of the *same* run leave a
  −45 ns offset and a 1.35 % rate error on `v12`. Re-fit per run pair.
- **Neither do the wall top/bottom offsets.** ±32–39 ns on the official file,
  within ±5.5 ns on v12, same bunches and same estimator. It was the old
  flash-finder timing, not cabling. Measure it on the file you analyse.
- **The plastic γ-flash constants do not transport between runs**; the liquids
  do (`../flash_timing/README.md`).
- **`δa_b`, `δk_b` are per bunch by construction** — the DREAM timestamp clock
  wanders ~1 ppm from burst to burst, which is 60 ns of drift at 60 ms.
- **The DAQ thresholds are per sub-run.** Read them from that sub-run's own
  `n1081b_config.json`; do not assume they held across a run.

## ⚠ Two settings that are easy to get wrong

- **`tflash` repair: OFF.** `ntof_dream_merge/tflash_repair.py` was built for
  the broken *official* flash finding, and `ntof_io` defaults it **on**. On a
  reprocessed file it is not a no-op — it would shift LIQC/D by ~15 ns and add
  25 ns RMS on PSSC, while the stored time base already has the liquids within
  1 ns of the walls.
- **No n_TOF internal offsets are applied**, because on v12 none are needed
  (liquid-vs-wall −0.8…+0.2 ns; wall-vs-plastic per channel RMS 2.3 ns inside a
  20 ns logic pulse). That is a *measurement*, not an assumption — re-measure
  per processing rather than carrying this forward.

## Cross-check against the flash timing calibration

The v12 liquid flash times reproduce `../flash_timing/`'s divert-off constants
to **0.1–0.5 ns** (LIQA −1708.1 vs −1708.22, LIQB −1710.5 vs −1710.30, LIQC
−1695.7 vs −1695.56, LIQD −1701.0 vs −1701.56) — two independent measurements,
one on seven divert-off runs and one on this run's own data. The plastics sit
31–50 ns away, exactly as that README warns.

## Is the per-bunch fit honest?

It is fitted on matched triggers and then used to match, so it was audited:
107 matched triggers per bunch for 2 parameters, split-half ρ = +0.996 (0.92 ppm
of real drift against 0.06 ppm of fit noise), a 3–5 % in-sample vs
cross-validated gap, and — decisively — the efficiency in a ±500 ns window is
**identical to five decimal places** with and without the correction, so it
concentrates matches rather than creating them. Details in
`DREAM_NTOF_CALIBRATION.md` §5.

## Files

| file | what |
|---|---|
| `time_map_run_79_run224572_v12_liqpileup.json` | K, T0, per-arm offsets, the window, and the measured performance per time-since-flash bin |
| `ntof_internal_alignment_run224572_v12_liqpileup.json` | flash-vs-pickup per tree, wall-vs-plastic, liquid-vs-wall, the top/bottom table, and the `tflash` repair setting |
| `n1081b_thresholds_run_79.json` | the discriminator thresholds the run actually held, per sub-run |

## Regenerate

```
cd ~/PycharmProjects/nTof_x17/ntof_dream_merge/match_study/scripts
../../../.venv/bin/python export_daq_calib.py
```

The pipeline that produces the inputs is in `match_study/README.md`; only
`build_candidates.py` is slow (~7 min per sub-run).
