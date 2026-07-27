# METHOD — optimising the DREAM readout window (latency, n_samples, flash framing)

**What this is:** the reusable recipe for placing and sizing the DREAM acquisition window
against the actual Micromegas drift signal, plus the trigger-FIFO watermark setting that
controls how evenly triggers are spread across the flash gate. First applied 2026-07-26
(run_77 → run_78 scan → run_79 production).

**Result of the first application** — run_77 → run_79, all four detectors, same HV:

| | run_77 | run_79 | |
|---|---|---|---|
| latency | 35 | **27** | signal onset moved from sample 10 → 2 |
| n_samples | 32 | **20** | 95% of drift charge + margin |
| OvrWrnHwm / Lwm | 11 / 8 (RunCtrl cap) | **2 / 1** | forced |
| M4.D1 PS delay | 1800 ns | **1440 ns** | flash re-framed to sample 5 |
| triggers per pulse | 88.85 | **104.33** | +17% |
| drain between bursts | 1.087 ms | **0.548 ms** | |
| 1–10 ms evenness (CV, 0.5 ms bins) | 1.500 | **0.425** | 3.5× flatter |
| disk | ~8.4 MB/s | ~5.3 MB/s | −37% |

---

## 1. ⚠ The trap: do NOT use track `time_min` to judge window alignment

The obvious metric — "what fraction of reconstructed drift tracks have their first hit in
sample 0" — is **wrong**, and it fails in a way that looks completely convincing.

On run_77 it read A 23.9 / B 36.7 / C 19.0 / D 45.1%, and it *survived* a full rejection
ladder (flash events, 3 events after each flash, any event with a saturated hit, and
high-pile-up events): Det A moved only 35.0% → 23.9% across all four cuts. The natural
reading is "real signal is falling off the front of the window." That reading is false.

Two independent disproofs, both from the latency scan:

- **The sample-0 population does not drain when the window moves later.** Det A across
  latency 33/35/39/43/47/51: 15.6, 14.1, 19.2, 16.4, 17.0, 20.9% — flat. Genuinely clipped
  signal *must* drain as you move the window off it.
- **It moves at the wrong rate.** The track `t_min` peak appears to shift ~1 sample per 3
  latency units, while the charge profile and the gamma flash both shift exactly 1:1.

It is a hit-finder edge artefact: spurious same-time hits at the window boundary get grown
into a "track" with `t_min = 0`. **Detectors B and D are dominated by it** — their `t_min`
peak sits at sample 0–1 at *every* latency — so they are useless for window timing. A and C
resolve the real signal above it.

## 2. The metric that works: the charge-weighted sample profile

Per sub-run, from `combined_hits_root`:

- keep hits with `amplitude >= 200`, `0 <= max_sample < n_samples`, `saturated == False`
- drop flash events (first event of each burst — the >200 ms gap rule)
- keep only **low-occupancy events**: ≤30 hits with `amp >= 200` in that event
- **detectors A and C only** (FEUs 3,4,7,8) — see §1
- histogram `max_sample` **weighted by amplitude**

Then subtract the flat pile-up floor (≈1.8%/sample; estimate it as the 20th percentile or
the median of the pre-signal samples) and **zero out samples 0 and `n_samples-1`**, which
carry edge artefacts in both directions.

What this shows: the drift signal is a **~19-sample-wide band** (that width *is* the drift
time), not a narrow peak. Its leading edge moves **1:1 with latency**:

```
latency  33 -> onset sample  8
latency  35 -> onset sample 10        <- run_77 ran here: TEN wasted lead-in samples
latency  39 -> onset sample 14
latency  43 -> onset sample 18        (band then truncated by the window end)
```

Charge containment from onset, measured at latency 33 (the point where the whole band is
inside the window):

| fraction of signal charge | samples from onset |
|---|---|
| 90% | 12 |
| **95%** | **14** |
| 99% | 21 |

## 3. Choosing latency and n_samples

```
latency    = latency_ref - (onset_ref - desired_lead_in)      # 1:1, desired_lead_in ~2
n_samples  = desired_lead_in + width_95 + margin              # 2 + 14 + 4 = 20
```

Keep real margin at the end. Drift time scales inversely with drift velocity, so a later
drop from drift 700 → 600 V lengthens the band by roughly 15% (~16 samples) and would clip
at n=18. n=20 still contains it.

## 4. Flash framing — the M4.D1 (SEC_D in0) gate-and-delay

Latency moves the flash too, so re-frame it *after* fixing the track window. The relation is
exact and was verified at four latencies with the delay held at 1800 ns (measured flash peak
5 / 7 / 11 / 15 at latency 33 / 35 / 39 / 43):

```
flash_peak_sample = 2.0 + latency - delay_ns / 60
  =>  delay_ns = 60 * (2.0 + latency - target_peak_sample)
```

At latency 27 for a peak at sample 5 → **1440 ns**. Apply with
`n1081b/set_ps_trigger_delay.py --delay <ns>` (read-back is automatic). A target of sample 5
leaves ~3 samples of pre-flash baseline. The far flash tail is no longer framed in a 20-sample
window — irrelevant for track physics (flash events are excluded from numerator and
denominator) but **flash-recovery studies must re-frame**.

## 5. The watermark: evenness vs total throughput

`Main_Trig_OvrWrnHwm` sets the trigger-FIFO depth at which the FEU asserts BUSY, and it *is*
the trigger-burst depth. RunCtrl clamps it **downward only**, from
`buf = (512 - latency) // n_samples`, so any value below that cap passes through unchanged.

**Forcing Hwm=2 flattens the acceptance comb.** On RAW + IPD 5 (run_78 vs run_77): 1–10 ms
CV 1.500 → 0.513, drain 1.087 → 0.664 ms, starved bins filled (3.0–3.5 ms: 0.103 → 0.927
triggers/pulse), at a cost of −10% total triggers taken almost entirely out of the 1.0–1.5 ms
spike. Shortening `n_samples` then repaid that cost with interest (run_79: 104.33 trig/pulse,
*above* run_77's 88.85).

⚠ **The 0.5 ms-bin CV used above is NOT sufficient on its own — 2026-07-27.** It is wider
than the dead gap it is supposed to detect, so it averages each comb tooth together with its
own gap and reports "flat". run_79 reads CV 0.420 at 0.5 ms but **0.584 at 0.25 ms and 0.874
at 0.1 ms**, with 12 starved gaps (median 0.35 ms) covering 33% of the 1–10 ms band. Always
report the CV at bins **finer than the drain**, plus the starved-bin fraction. Tool:
`analysis/flash_comb/tools/flash_time_spikiness.py`. See
`docs/PLAN_comb_spikiness_2026-07-27.md`.

⚠ **`docs/FEU_WATERMARKS_2026-07-22.md` reaches the opposite conclusion** ("lowering Hwm
monotonically hurts"). That scan ran on **ZS + IPD 10, where the comb was already gone**, and
optimised *total throughput in a band*. Different configuration, different objective, opposite
answer. Match the watermark to whether a comb actually exists in the configuration you are
running.

## 6. Verification checklist — a null here is untrustworthy without it

1. **Archived cfg** — `grep -E "^Feu \* Dream \* 12|^Sys NbOfSamples|^Feu \* Main_Trig_OvrWrn" ~/july_dream/dream_run/<run>/<subrun>/*.cfg`
   ⚠ **grep the line with NO leading `#`.** The template carries two commented
   `Feu * Dream * 12` lines (`0x005F`, `0x0001`) *before* the live one, so
   `grep "Dream \* 12" | head -1` returns `0x005F` for every sub-run and looks exactly like
   "the latency override never applied." This cost a false alarm mid-scan.
2. **Hardware watermark** — `.venv/bin/python dream_scripts/feu_trig_counters.py` (read-only)
   must show your Hwm/Lwm on all 8 FEUs. Overrides were silently dropped for weeks by a
   long-lived `dream_daq` server that predated the plumbing; the cfg is not the last word.
3. **Signal onset** at the intended sample, from the §2 charge profile.
4. **Flash peak** at the intended sample, from flash events only.
5. **Evenness** — CV of the 1–10 ms distribution against the previous config, at **0.25 ms
   and 0.1 ms bins as well as 0.5 ms**, plus the starved-bin fraction. The 0.5 ms number
   alone is not trustworthy (see the warning in §5).

## 7. Cost of the measurement

A latency ladder needs **~2 minutes per point**, not more. At ~1225 triggers/min that is
~1500 drift tracks on Det A and ~870 on Det C — σ≈1.5% on metrics that move by >10 points
between adjacent steps. Sub-run overhead is ~22 s. Six points, one cycle: **~24 minutes
wall-clock**, including the HV/threshold re-assertion each sub-run.

Bound the ladder so RunCtrl's watermark cap does not move under it: with n=32 the cap is a
constant 11 only for latency 33–64 (at 31 it is 12, at 71 it is 10). Assert this in the
config generator — it caught a bad ladder on the first attempt here.

**Related:** `docs/METHOD_track_rate_vs_hv_time_intensity.md` (the tracking/denominator
recipe), `docs/FEU_WATERMARKS_2026-07-22.md` (register map, `feu_trig_counters.py`),
`docs/DREAM_flash_comb_study_2026-07-19.md` (comb mechanism),
`run_configs/run_config_latency_scan.py` (run_78), `run_configs/run_config_stats_optimized.py` (run_79).
