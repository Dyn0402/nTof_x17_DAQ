# METHOD — Micromegas track rate vs HV vs time-since-flash vs beam intensity

**What this is:** a reusable recipe for measuring the per-trigger 2D-track rate of the
Micromegas detectors and slicing it by any combination of **HV scan point**, **time since
the gamma flash**, and **beam-pulse intensity**. First applied to run_67
(2026-07-24); the same three scripts run on any flash-anchored HV-scan run.

**Scripts (version-controlled):**
`~/PycharmProjects/nTof_x17/ntof_july_analysis/track_rate_hv_time_intensity/`
(`~/ana` → `nTof_x17`)
- `build_cache.py` — tracking + denominator, once per run, cached to CSV
- `plots.py` — HV × time figures and the best-HV-per-band table
- `intensity_split.py` — the beam-intensity split (adds the pulse-intensity axis)
- `README.md` — usage + dependency notes

**Outputs** (cache/, figures/, CSVs) live per-run under `~/beam_july/analysis/`, e.g. the
first application: `~/beam_july/analysis/July_HV_Scan/run67_track_hv_time/` (its README has
the run_67 results and caveats). Point the scripts at an output dir with `--cache`/`--out`.

Run these with the tracking venv: `~/PycharmProjects/nTof_x17/.venv/bin/python`.

---

## 0. TL;DR — run it on a new dataset

```bash
cd ~/ana/ntof_july_analysis/track_rate_hv_time_intensity      # the scripts
V=~/PycharmProjects/nTof_x17/.venv/bin/python
C=~/beam_july/analysis/July_HV_Scan/run_XX/cache              # this run's cache dir
F=~/beam_july/analysis/July_HV_Scan/run_XX/figures

# 1. tracking + denominator for the new run
$V build_cache.py --run run_XX --cache $C

# 2. HV x time figures + best-HV-per-band table
$V plots.py --cache $C --out $F

# 3. beam-intensity split (adds the pulse-intensity axis)
$V intensity_split.py --run run_XX --cache $C --out $F
```

All three scripts take `--cache`/`--out` (and `build_cache.py`/`intensity_split.py` take
`--run`), so one checkout drives many runs. Default (no flags) is `./cache` and
`./figures` next to the scripts — the run_67 layout.

`build_cache.py` is **incremental** (skips sub-runs already in `subruns.csv`) and safe to
re-run as a reprocess catches up. **Never run two builders against one cache dir** — both
append and silently double-count (this happened once on run_67; symptom is a duplicated
row in `subruns.csv`).

The sub-run **name parser is the only run-specific piece** and it already knows three
naming styles (see §6). Everything else is generic.

---

## 1. Prerequisite: hits MUST be common-noise-subtracted (CNS)

**This is the single most important gotcha. Get it wrong and every number is ~1000× too
high and physically meaningless.**

The RAW hit-finding chain applies no common-mode subtraction unless the processor's
`common_noise_subtraction` flag is on. It was configured **off** from 2026-07-02
(commit `ca7baed`) to 2026-07-23, so every RAW run in that window is common-mode
dominated: a coherent ~343 ADC per-64-ch-connector baseline swing crosses the ~80 ADC hit
threshold across the whole plane at one drift time, and the seed-and-grow finder chops
each full-plane same-time band into dozens of ±90°, zero-time-span "tracks". Diagnosis:
`~/beam_july/analysis/waveform_cns_study/` and `detA_track_freq_run70/`. Effect: run_70
Det A spurious hits/event median 2732 → 34 (~79×) once CNS is on; the "30% track rate"
was entirely common-mode.

**Before trusting any track number:**
1. Confirm `processor_config.py` / `config/processor_config.json` has
   `common_noise_subtraction: true` for the era the data was processed in.
2. `build_cache.py` guards this with `CNS_CUTOFF` (a wall-clock time): it uses only
   sub-runs whose `combined_hits` files were **written after** the cutoff, so pre-CNS
   files are skipped rather than silently included. Set it to the mtime after which the
   run was (re)processed with CNS on. For any run processed after 2026-07-23 the default
   passes trivially; set `CNS_CUTOFF = 0` to disable.
3. Sanity check with `plots.py`'s `angle_sanity.png`: a clean detector shows a real
   off-±90° angle population and time spans extending to 0.3–0.6 µs. A pure ±90° spike
   with time_span ≈ 0 means the hits are still common-mode — stop and fix the processor.

**Pre-CNS analyses are superseded and must not be cited:** `July_HV_Scan/run67_scan/`,
`run61_scan/`, and the run_70 "30%" all predate the fix.

---

## 2. Definitions (identical across every dataset)

- **Physics trigger** — a decoded DREAM event that is not the gamma flash. The flash is
  the first event of each burst (a >200 ms gap precedes it) and rails every detector, so
  it is excluded from BOTH numerator and denominator.
- **time since flash** `dt_ms` — event time minus its burst's flash time. Flash-anchored
  runs give one flash per PS pulse, then the N93B gate admits Singles from ~1 ms to
  ~81 ms after it.
- **Hit** — `amplitude ≥ 200` ADC (`beam_track_finding.MIN_HIT_AMP`).
- **Track (per projection)** — a seed-and-grow track with `n_hits ≥ 4`
  (`build_cache.py`), from `nTof_x17/beam_track_finding.collect_all_tracks`.
- **Drift track** — `n_hits ≥ 5`, `time_span ≥ 0.10 µs`, `|angle| < 80°`. The time-span
  and angle cuts are what separate a genuine drift-time-spread track from a same-time
  charge-sharing / residual-common-mode cluster. **This is the cut that makes the number
  physical** — without it you re-admit the common-mode fragments.
- **2D drift track** — an event with a drift track in BOTH the x and the y projection.
- **Low pile-up** — additionally `Σ n_hits over the event's tracks ≤ 30`. The loose 2D
  selection is inflated by busy/pile-up events (median occupancy ~226 hits) where
  residual structure fakes an X&Y coincidence, so every result is quoted both ways.
- **Rate** — 2D-drift events ÷ physics triggers, as a percentage, in a given HV × dt (×
  intensity) cell. The trigger is scintillator-based and independent of the Micromegas,
  so the denominator does not move with mesh HV — the ratio is a genuine efficiency, not a
  rate artefact.

Detector → FEU map (fixed hardware): A = 3/4, B = 5/6, C = 7/8, D = 1/2 (x/y).

---

## 3. The denominator gotcha (why it is NOT the combined-hits event count)

With CNS on, a trigger whose only signal was common mode now has **zero hits** and is
absent from `combined_hits`. Counting the denominator there drops those triggers and
undercounts by ~40% (measured on run_70). So the denominator is taken from the **decoded
trigger list** (`flash_timing_lib.load_subrun`, which reads every decoded FEU event and
dedupes on the hardware `timestamp`), NOT from combined hits. The numerator comes from
combined hits. Numerator ⊂ denominator by construction.

---

## 4. The beam-intensity split (`intensity_split.py`)

**Recipe source:** `nTof_x17/ntof_july_analysis/pulse_match.py`, with
`run30_flash_intensity.py` as the consumer example. This is the canonical July way to get
per-event beam intensity — reuse it, don't reinvent.

**How pulse_match works:** it clusters a sub-run's event times (0.5 s gap → one cluster
per beam pulse), anchors the DREAM clock with the datrun filename timestamp, then fits the
residual offset by maximising how many clusters land within 0.35 s of a real PS pulse in
the beam_watcher per-pulse log
(`/mnt/data/x17/beam_july/slow_control/beam_intensity/beam_intensity_<date>.csv`,
`intensity_e10` column, pulses ≥ 50e10). Every event inherits its cluster's pulse
intensity. Results cache in `nTof_x17/ntof_july_analysis/cache_pulse_match/`.

**The split:** July pulses are **bimodal** — ~410e10 and ~850e10 — so events split at
`E10_SPLIT = 600.0` into LOW (~410) and HIGH (~850). Confirm the bimodality on a new run
first (`python pulse_match.py run_XX <subrun>` prints the intensity quartiles); if a run's
pulses are unimodal or centred elsewhere, adjust `E10_SPLIT` or split on the per-run
median instead.

`intensity_split.py` **auto-refreshes** the pulse cache when `build_cache.py` has added
sub-runs since it last ran (it compares sub-run sets and rematches only the new ones,
reusing `cache_pulse_match/` for the rest), so you never need to remember `--rebuild` after
extending the tracking cache — use `--rebuild` only to force a full refit.

**Match quality to check before trusting the split:** `intensity_split.py` prints the
matched fraction. run_67 was ideal — **100% of triggers matched**, 164/164 clusters per
sub-run at ~1 ms residual RMS. If the matched fraction is low (say < 90%), the clock
offset fit probably failed (wrong day file, DST, a sub-run with too few pulses to anchor);
inspect `offset_s` and `resid_rms_ms` per sub-run before reading the physics.

**The numerator/denominator alignment subtlety (important, and generic):**
- The **numerator** (tracks) carries a combined-hits `event_id`, so pulse_match's
  per-event intensity map applies directly.
- The **denominator** (`cache/events.csv`) is built from the decoded trigger list and has
  **no event ids**. So intensity is mapped to it **by burst order**: re-derive the decoded
  triggers' burst index, take pulse_match's per-**burst** intensity (one pulse per burst
  on a flash-anchored run), and assign by index. `intensity_split.py` **asserts the burst
  counts from the two paths are equal** before doing this and skips the sub-run on a
  mismatch — never let it map blindly, or intensities silently shift by a burst.

This burst-order trick only works because the run is flash-anchored (one flash = one burst
= one PS pulse). For a non-flash-anchored run you would instead need event ids in the
denominator (e.g. rebuild `events.csv` from combined hits, accepting the ~40% CNS
undercount, or add event ids to the decoded reader).

---

## 5. run_67 results (what the method produced first — full detail in the dir README)

Three-axis result on the CNS-clean hits, 0.90 + 1.13 MIP, drift 500/600/700:

1. **The resist-HV optimum moves with time since flash.** Det A best resist: ~530 V at
   1–4 ms → 540 V at 4–8 ms → ≥550 V at 8+ ms. Reproduces at all three drift settings and
   is independent of plastic threshold. (Confirms the pre-CNS run_61 finding — it survives
   the common-mode correction.)
2. **The early bands ROLL OVER above ~540 V** — high HV *destroys* early-time
   reconstructable tracks (Det A 4–8 ms: 1.5% at 540 V → 0.4% at 550 V). Late bands rise
   monotonically; 550 V is the top of the scan, not a maximum.
3. **…but the roll-over is a HIGH-INTENSITY effect only.** On LOW (~410e10) pulses the
   early bands rise monotonically to 550 V with no collapse; on HIGH (~850e10) they
   collapse. The HIGH/LOW rate ratio in Det A's 1–4 ms band swings ~80× across the ladder
   (~4× at 530 V → ~0.05× at 550 V, crossing unity ~535 V). **The 20–81 ms band is
   intensity-independent** (ratio ≈ 1, flat) in A/C/D. Reads as occupancy/space-charge:
   high gain + a dense high-intensity early window = unreconstructable.

**Operational takeaway:** "cap HV near the flash" is really "cap it *on high-intensity
pulses*" — and since every event now carries its e10, that can be selected offline instead
of paid for in the HV setting.

Per-detector reliability, carries to any run:
- **Det A** — clean reference (M1), highest confidence.
- **Det B** — noise-dominated / bad M1; 0–0.35% everywhere, **not readable** as a
  measurement.
- **Det C** — usable.
- **Det D** — **suspect**: ~12× Det A's track count, highest same-time fraction (59% at
  |angle| > 80°) → residual coherent structure may survive CNS. Late-band trend agrees
  with C, but treat its absolute scale with caution.

---

## 6. Porting to another run

**What is generic:** the CNS guard, tracking, drift-track cuts, decoded-list denominator,
HV × time binning, the pile-up control, and the whole intensity-split machinery.

**What is run-specific — the sub-run name parser.** `build_cache.py` has `SUB_PATTERNS`,
a list of `(regex, parser)` pairs mapping a sub-run name to its scan-axis values. Three
styles are already handled:

| style | example | axes extracted |
|---|---|---|
| run_67 / run_64 | `m090On_dr500_r520_062` | mip, drift, resist |
| run_71 | `acmeshOff_dr600_ri0_0041` | mesh, drift, resist_idx |
| run_70 | `m141On_mip1p41_006` | mip (no HV axis) |

Add a pattern for a new convention; anything matching none is skipped. The axis columns
that appear in the CSVs are the union in `AXES` — `plots.py` groups on `resist` / `drift` /
`mip`, so a run whose HV axis is `resist_idx` (run_71) needs those columns mapped to real
volts (join the run's HV log or `run_config.json` sub-run list) before the HV plots mean
anything. The intensity split and the time-since-flash axis need no such per-run work.

**`--cache`/`--out`/`--run`:** all three scripts take these flags (see §0), so one checkout
drives many runs into separate cache/figure dirs without copying the directory or editing
constants.

**Other things that may need a per-run look:**
- `CNS_CUTOFF` — set to the mtime after which THIS run was processed with CNS on.
- `E10_SPLIT` — confirm the pulse-intensity histogram is still bimodal at ~600e10.
- Time bands (`BANDS`) — tuned to the 1–81 ms N93B gate; adjust if the gate window moved.
- Detector caveats above are hardware-state dependent; re-check `angle_sanity.png` per run.

---

## 7. Files & outputs

```
ntof_july_analysis/track_rate_hv_time_intensity/   (in the nTof_x17 / ~/ana repo)
  build_cache.py            --run/--cache; SUB_PATTERNS is the only run-specific edit
  plots.py                  --cache/--out; HV × time figures + rate_vs_hv_time.csv
  intensity_split.py        --run/--cache/--out; --rebuild refits pulse matching
  README.md                 usage + dependencies

<output-dir>/                                       (per-run under ~/beam_july/analysis/)
  cache/                                            (--cache)
    tracks.csv              one row per track: det, subrun, <axes>, event_id, projection,
                            n_hits, angle_deg, time_min, time_span, pos_span, dt_ms
    events.csv              one row per physics trigger (denominator): subrun, <axes>, dt_ms
    subruns.csv             bookkeeping: n_flash, n_phys, per-det track counts
    e10_tracks.csv          subrun, event_id, e10          (from pulse_match)
    e10_events.csv          subrun, dt_ms, e10             (denominator, burst-order mapped)
  figures/                                          (--out; the CSV tables land here too)
    rate_vs_hv_time.csv        best-HV table (plots.py)
    intensity_split.csv        the intensity numbers (intensity_split.py)
    angle_sanity.png        CNS quality check — READ FIRST on any new run
    rate_vs_hv.png          headline: rate vs resist HV per dt band (loose + low-pileup)
    hv_time_heatmap.png     resist × dt map, ★ = best HV per time slice
    rate_vs_time.png        rate vs dt, one line per resist HV
    drift_panels.png        rate vs resist, split by drift HV
    threshold_check.png     1.13 vs 0.90 MIP overlay (threshold dependence)
    intensity_rate_vs_hv.png   rate vs HV, LOW vs HIGH intensity
    intensity_heatmap.png      resist × dt map per intensity class
    intensity_ratio.png        HIGH/LOW rate ratio vs HV per dt band
```

Related memory: `run67-track-hv-time-cns`, `raw-combined-hits-common-mode`,
`pulse-intensity-split-analysis`, `run61-tracking-efficiency` (pre-CNS, superseded).
