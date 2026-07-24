# Banco (P2/SPS) fork vs local mm_strip_reconstruction — comparison notes

**Date:** 2026-07-24 (evening)
**Banco side:** `banco_cern:/local/home/banco/mm_dream_reconstruction/` (NOT a git repo)
and `banco_cern:/local/home/banco/DAQ_Control_Dream_Beam/processor_watcher.py`
**Local side:** `~/CLionProjects/mm_strip_reconstruction/` at `a1cce79` (clean tree)
and `nTof_x17_DAQ/processor_watcher.py`
**Method:** rsynced banco source to scratchpad, diffed file-by-file against local git.

---

## 1. Genealogy — verified common base

The banco fork is **exactly local commit `f404575`** ("DreamDecoder.cpp added eof
breaks") plus edits made **today 2026-07-24 at 17:05 CEST** to three files. Verified:

- `decoder/`, `feu_hit_combiner/`, `orchestrator/process_run.py`, `clusterizer/`,
  `common/`, `qa_waveforms/` are byte-identical to `f404575`.
- The three edited files each have a `.bak_20260724` sibling (created by the edit at
  17:05:32) that is byte-identical to `f404575`. So banco's *entire* divergence is
  that one edit session; there is no other hidden drift.
- Their binary was rebuilt 17:05:38, seconds after the edit, and the
  `processor_watcher.py` in their DAQ repo was extended to match (see §3.3).

Since the fork is not under git on their machine, any future sync is manual file
copies — diff before overwriting anything there.

## 2. Both sides independently fixed the same derivative-trigger/ZS bug today

Same root cause, two different repairs, a few hours apart:

- **Local** (commits `8ec6769`→`a1cce79`, merged with `b9add2f`): **replaced** the
  derivative trigger with the unified threshold-run + valley-prominence finder.
- **Banco** (17:05 edit): **patched** the derivative trigger in place.

### Banco's derivative-seed patch (in their `findPulseRegions`)

Their diagnosis (comment in their `WaveformAnalyzer.cpp`): the seed is the max of the
*smoothed* derivative, which for short ZS stubs / early-window pulses lands **before**
the waveform crosses `ampThr`; the region grower only extends contiguous
above-threshold samples starting *at* the seed, so `wf[seed+1] <= ampThr` terminated
the region at width 1 and the width cut rejected the pulse. Fix: advance each seed to
the first above-threshold sample before growing (with empty-region guards).

**Their measured impact at the SPS (worth keeping as validation numbers):** the bug
silently dropped **~24% of real in-time P2_OUT hits, ~47% of P2_MID, and ~97% of
P2_IN** (P2_IN pulses peak one sample earlier).

### What their patch still does NOT cover (all handled by the local rewrite)

- Pulses with **no positive-derivative seed at all** — e.g. already above threshold
  and falling at the window start (`truncLeft`-type). No seed → still dropped.
- Slow risers whose smoothed derivative stays below `derivThresholdSigma`.
- Local-baseline = *minimum* of pre-pulse window (biased low ~0.5σ, latches onto
  undershoots); local rewrite uses median + guard gap (`baselineGapSamples`).
- Integer threshold crossings / ToT (local: interpolated floats), no
  `trunc_left/right` branches, no `significance` branch, no matched-filter gate,
  no saturation run-length logic, none of the 12x perf rework (`DenseEvent`).
- Their CNS path is still the old order (CNS before densify — the corruption bug
  from `b9add2f`) — dormant because they run `--cns 0`, but one config flip away.
  Local now **force-disables CNS on ZS input** (`f90e82b`); banco only has the flag.

## 3. Things BANCO has that local does NOT

### 3.1 `--zs-baseline` flag — **probably want to port this** ⚠

New in their 17:05 edit (`WaveformAnalyzer.{h,cpp}`, `analyze_waveforms.cpp`):

> For zero-suppressed data whose pedestals were already subtracted **on the FEU**
> (waveforms re-centred at 256): subtract the uniform 256 baseline instead of the
> pedestal file's per-channel RAW means — those differ from 256 by **−15…−127 ADC**
> per channel and shift every threshold/amplitude by that much. The pedestal file is
> still used for the per-channel noise RMS.

Implementation: `setZsBaseline(bool)`; in `subtractPedestal()` return
`ampl - zeroSupressedBaseline` when set; `max_adc_ped_sub` computed against 256
likewise; CLI `--zs-baseline <0|1>`.

**Local status:** NOT present. Local HEAD only falls back to 256 when *no pedestal
file is given* (and then with RMS=1). When a pedestal file IS provided — which the
x17 processor always does (`pedestal_loc: 'find'`) — local subtracts the pedestal
file's raw per-channel means even on ZS data. If x17 ZS runs use on-FEU pedestal
subtraction (check `dream_daq_info.pedestal_subtraction` in the run_config.json of a
ZS run), **local ZS amplitudes/thresholds carry the same −15…−127 ADC per-channel
bias banco just fixed.**

### 3.2 Decode-hang watchdog in their `processor_watcher.py`

Their decoder (same `DreamDecoder.cpp` as local `f404575`) can **infinite-loop on
certain FDFs** — 100% CPU, input position and output ROOT both frozen; they saw it
2026-07-23 and -24 on different files/FEUs (also their commit `b8e38b4` "decoder hang
found"). Since the watcher is sequential, one hang blocks the whole pipeline. Their
mitigation:

- `_decode_file` runs the decoder via `Popen(start_new_session=True)` and polls the
  output ROOT; if it stops growing for `decode_stall_timeout_s` (180 s) or exceeds
  `decode_hard_timeout_s` (1800 s), SIGKILL the process group, delete the partial
  ROOT, rename the FDF to `<name>.hang` (kept as a reproducer), raise
  `DecodeTimeout`.
- `_process_file_num` catches it and continues with surviving FEUs, so the subrun
  completes minus that plane. Config-overridable timeouts.

**Local status:** plain `subprocess.run` with no timeout. The underlying decoder bug
is presumably present locally too (same source). Two follow-ups: port the watchdog,
and grab their `.hang` reproducer files to actually fix the decoder loop.

### 3.3 `zs_baseline` plumbing in their watcher

`_read_zs_baseline(run_dir)`: True when `dream_daq_info.zero_suppress` AND
`dream_daq_info.pedestal_subtraction` in `run_config.json` → passes
`--zs-baseline 1` to `analyze_waveforms`. Per-run automatic; no config knob needed.

## 4. Things LOCAL has that banco does NOT

- The whole waveform-analysis rewrite (`8ec6769`, `c9665cf`, `781c5b6`, `018f575`):
  unified pulse finder (threshold runs + gap bridging + valley-prominence pile-up
  split, per-region analysis bounds), median local baseline with guard gap,
  full-span integral, interpolated crossings, `trunc_left/right` + `significance`
  branches, run-length saturation detection, matched-filter (boxcar) gate as default
  with `--thr`/`--mf` CLI (auto width ~300 ns / tps; auto-off on ZS/no-pedestal),
  `DenseEvent` reusable buffers (~12x faster), CNS densify-then-subtract fix
  (`b9add2f`) and CNS auto-forced OFF on ZS input (`f90e82b`).
- `process_run.py`: derives matched-filter width from the Dream `.cfg` shaping time
  (`a1cce79`). Banco's `process_run.py` is the old `f404575` one (their watcher
  doesn't use it anyway).
- `visualize_fdf.py` end-to-end debug tool (`2477566`).
- Local `processor_watcher.py`: **newest-first single-file scan** (process one
  file_num then rescan, so the newest data is always worked first; banco walks
  oldest-first and drains everything), and `_read_feu_detector_map` → `det=` labels
  in analyze log lines. Note local watcher does NOT pass `--thr`/`--mf` (compiled
  defaults apply).
- Trivial: README (rewritten locally), .gitignore.

## 5. Consequences for the downloaded meshscan data

`/media/dylan/data/P2/TB_July2026_H4/runs/beam_nominal_meshscan_1` (76G, complete,
verified size; pedestal run `pedestals_07-23-26_17-00-32` alongside):

- The run was taken Jul 23 and processed with their **pre-fix** binary → the
  shipped `hits_root`/`combined_hits_root` suffer the seed bug (≈97% of P2_IN hits
  missing, ≈47% P2_MID, ≈24% P2_OUT) **and** the wrong per-channel baseline
  (no `--zs-baseline`). Treat them as placeholders only.
- Raw FDFs + pedestals are intact → reprocess locally with the rewritten analyzer.
  For an apples-to-apples check we can also reprocess with their patched fork
  (rsynced copy in scratchpad `banco_cmp/`, incl. `.bak` originals) and compare
  hit counts per detector against the 24/47/97% numbers.
- Their `drift_scan_1/2` (Jul 24, before the 17:05 rebuild) have the same problem
  on their side.

## 6. Suggested actions

1. Port `--zs-baseline` (analyzer + watcher auto-detection) into local — after
   confirming x17 ZS runs use on-FEU pedestal subtraction. Biggest correctness gap
   on our side.
2. Port the decode-hang watchdog into `nTof_x17_DAQ/processor_watcher.py`; ask banco
   for a `.hang` FDF to debug `DreamDecoder`'s infinite loop properly.
3. Offer banco the rewritten analyzer (their fork is a manual-copy target; their
   17:05 patch is subsumed by it *except* `--zs-baseline`, which must be merged in,
   not overwritten).
4. Reprocess the downloaded meshscan with the fixed local analyzer before anyone
   looks at efficiency-vs-gain from it.
