# run_57 flash-recovery HV map — DAQ dead-time after the gamma flash vs resist HV

**Date:** 2026-07-20 · **Run:** run_57 (Mode-2 `flash_random`, Ar/iC4H10 90/10, 3He
target, no beamline filter) · **Analysis:**
`~/beam_july/analysis/flash_recovery/run57/` (= `/mnt/data/x17/beam_july/analysis/flash_recovery/run57/`)

## What this measures

In `flash_random` mode the gamma flash rails all four detectors (defines t = 0),
then a Poisson pulser fires random triggers at uncorrelated times inside the ~30 ms
post-flash gate. Each random trigger *probes* the front-end state at some
time-since-flash. **Recovery time** = the first time-since-flash at which the
baseline noise has returned and stays back (liveness = median-over-512-channels of
each channel's std across the 32 samples; flat ~2 ADC = blanked/dead, ~50–130 ADC =
alive; threshold 15 ADC). It is the DAQ/front-end dead time the flash costs you at a
given gain — **lower resist HV → less flash charge dumped → shorter dead time.**

Recovery is quantized to the analysis log-time bins, so values land on a discrete
ladder (…, 13.9, 16.1, 18.6, 21.5, 24.9 ms). **24.9 ms is the top bin = "does not
recover within the ~30 ms gate."** All values below are the aggregate over pulse
intensity.

## HV → recovery-time map (aggregate)

Det A/B/C share the resist HV; **Det D is driven 10 V below A/B/C** (its own column
below, on its own voltage grid). Det A also ran with **drift = 600 V** (vs 800 V on
B/C/D) in run_57 — see caveats.

| A/B/C resist HV [V] | A rec [ms] | B rec [ms] | C rec [ms] |
|---:|---:|---:|---:|
| 580 | 21.5 | 21.5 | 24.9 |
| 576 | 18.6 | 16.1 | 24.9 |
| 572 | 18.6 | 16.1 | 21.5 |
| 568 | 13.9 | 13.9 | 21.5 |
| 564 | 12.0 | 12.0 | 21.5 |
| 560 | 13.9 | 10.4 | 16.1 |
| 556 | 12.0 |  8.9 | 16.1 |
| 552 |  5.8 |  5.0 | 13.9 |
| 548 |  4.3 |  4.3 |  7.7 |
| 544 |  3.2 |  3.2 |  8.9 |
| 540 |  5.0 |  1.2 |  8.9 |
| 536 |  3.7 |  0.9 |  6.7 |
| 532 |  2.4 |  0.9 |  3.7 |
| 528 |  2.1 |  0.6 |  2.8 |
| 524 |  1.0 |  0.5 |  2.4 |
| 520 |  0.9 |  0.5 |  0.9 |

(full 2 V grid in `metrics_run_57_perdet.csv`, `cls == all`)

| Det D resist HV [V] | D rec [ms] |
|---:|---:|
| 570 | 24.9 (window-limited) |
| 560 | 24.9 (window-limited) |
| 550 | 24.9 (window-limited) |
| 540 | 24.9 (window-limited) |
| 538 | 21.5 |
| 530 | 21.5 |
| 522 | 21.5 |
| 520 | 18.6 |
| 516 | 16.1 |
| 512 | 13.9 |
| 510 | 13.9 |

## Takeaways for optimizing data taking

- **Recovery time rises steeply and monotonically with gain (resist HV).** Every
  extra ~10 V of resist buys a few ms of post-flash dead time.
- **Order (worst → best) at fixed HV: C ≳ A ≈ B.** Det C recovers slowest of the
  three same-HV detectors; A and B track each other closely.
- **Det D is window-limited (≥ ~22 ms, pinned at the 30 ms ceiling) above ~540 V**
  and only drops into the resolvable range below ~520 V. Two things confound D: it
  runs 10 V lower than A/B/C already, and its liveness is contaminated by
  noise/common-mode (the standing "bad-D" caveat across all flash-recovery runs) —
  so the D numbers are an upper bound, read with care.
- **Rule of thumb for a low-dead-time working point:** to keep post-flash dead time
  under ~5 ms you want A/B ≲ 550 V and C ≲ 540 V; under ~1 ms wants ≲ 522–526 V.
  These trade directly against gas gain / signal size — this map is the dead-time
  side of that tradeoff only.

## Cross-run consistency (same gas)

Compared directly to **run_42** (same Ar/iC4H10 90/10, so a raw-HV overlay with no
gas remap; `~/beam_july/analysis/flash_recovery/run57_vs_run42/`). run_57 is the
high-gain top of run_42 (560→475 V) pushed 20 V higher (to 580) at 2.5× finer −2 V
steps. In the 520–560 V overlap the two agree to within ~1 log-bin
(mean |Δ|: A 0.9, B 1.3, C 2.4, D 0.5 ms) — the recovery-vs-gain curve reproduces.
Notably **Det A overlays run_42 despite the 600 V vs 800 V drift difference**, so at
these fields drift has little effect on recovery.

## Caveat / what's next

Recovery here is a **noise-return (pedestal-liveness)** proxy for when the front-end
un-blanks — it is not a direct measure of when the detector can register a *track*
again. The follow-up run will look for **actual reconstructed tracks vs
time-since-flash**, a more direct test of when useful physics resumes after the
flash. Expect the track-based recovery to be equal or *longer* than this
noise-return map (gain can sag after the pedestal noise is already back).
