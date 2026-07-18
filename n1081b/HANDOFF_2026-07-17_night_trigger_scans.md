# HANDOFF 2026-07-17 night — post-FIFO trigger scans (thresholds + timing + 2D)

Context: earlier today the M2 plastic inputs were moved from BNC splits to linear
fan-in/fan-out modules (~2x amplitude), module zeros were re-adjusted (M2 D1 is
BROKEN: baseline ~ -15 mV low), the M1 SiPM-sum baselines were re-zeroed, and the
FIFOs feeding M1 wander ~3 mV. All previous threshold calibrations were therefore
stale. Plastics also arrive an extra ~16 ns (cable) + FIFO insertion late.
run_52 (flash_random gas-change monitor) ran untouched throughout — only
M1/M2/M3/M5 were scanned; M4/M6 never written.

## New tools (this session)
* `n1081b/threshold_ladder.py` — 1D threshold->rate ladders per board/section via
  M5 scalers; `--apply-wall/--apply-plastic` set-and-exit mode.
* `n1081b/timing_delay_scan_v2.py` — board_session rewrite of timing_task3_scan
  (retires the raw-SDK m3_timing_lib connect + per-point reconnect churn);
  adds `--probe` (coarse all-sector locator), `--center` grids, `--apply D`.
* `rate_scan_2d.py` grew grid overrides: `--wall-nominal --wall-mults
  --plastic-ladder --plastic-baseline`.
* Gotcha: pass negative lists as `--plastic-ladder="-20,..."` (argparse).

## 1. Threshold ladders (19:26–19:38)
Data: `~/beam_july/threshold_ladder/2026-07-17_19-26-23_night_full_ladder/`,
`.../2026-07-17_19-34-08_d_wall_zoom/`.

* **Walls (M1, positive):** flat plateau **+14 → +50 mV** (~800–950 Hz @ ~848 e10,
  all four walls uniform after the re-zero). Noise bump at **+12**, inverted
  (collapsing) response at **+10**. With the ~3 mV FIFO wander, nominal chosen
  just above the bump: **A:15 B:16 C:15 D:16 mV**.
* **Plastics (M2, negative):** with the 2x FIFO amplitude, old thresholds map ~2x
  deeper. A/B/C show noise-saturation inversion only at −10 (wall ≈ −10..−13)
  → baseline **−30 mV** (2x the old −15).
* **Plastic D (broken D1, baseline ~ −15 mV low): channel goes COMPLETELY dead
  (0 Hz, continuous retrigger — no edges) at −24 mV and shallower.** Zoom shows a
  smooth relative response −44 → −26 (D/(A,B,C held) 0.48 → 0.81), then collapse
  at −24. Noise wall ≈ **−24/−25**; with 1.5x margin **D baseline = −38 mV**.
  D wall stays usable (46 Hz sector-D coincidences at −38) — no need to suppress
  D triggers, just never set it shallower than ~ −36.
* Caveat: during the wall sweep of the full ladder the plastics sat at the last
  ladder value (−10, D dead) — wall SINGLES are valid, coincidence columns are not.

Applied baseline (LEFT ON THE BOARDS): walls A:15 B:16 C:15 D:16; plastics
A/B/C −30, D −38 (verified read-back 19:39).

## 2. Timing: wall-vs-scint delay at M3 G&D (19:39–20:20)
Probe (`timing_scan_night_probe.json`): raw-C argmax at wall-delay **+25 ns**
(A/B/D 25, C 30) — the FIFO+cable lateness measured directly.

Full two-set beam-normalized scan (`timing_scan_night_v2run1.json`, gate 20 ns,
2x15 pts x 60 s, held sectors at +25; PNG alongside):

| sector | plateau center (wall-delay ns) | FWHM | top C/ref |
|---|---|---|---|
| A | left edge < −5 (plateau +5..+30) | n/a | 0.56 |
| B | **+17.8** | 45 | 0.51 |
| C | **+22.3** | 34 | 0.61 |
| D | **+23.6** | 36 | 0.41 |

Spread ≤ 6 ns → uniform delay OK. **APPLIED: wall-leg delay +20 ns, gate 20 ns,
all four M3 sectors, ch0(wall) delayed / ch1(scint) 0 (verified 21:00).**
≥10 ns margin to every plateau edge. Post-apply sanity: coincidences
A 175/B 164/C 197/D 137 Hz at ~1.2 kHz wall singles.
NOTE: the pre-FIFO baseline (delay 0 both legs) is what old snapshots
(`dump_2026-07-16_pre_run47.json`) record — do NOT "restore" a pre-07-17 dump
onto M3 without re-adding the +20.

## 3. 2D rate scan, post-FIFO grid (21:00–, `2026-07-17_21-00-35_night_2d_postfifo`)
Grid: walls x{1.0,1.5,2.2,3.3} of new nominals x plastics {−20,−30,−44,−66},
held baseline −30, `--skip-hv` (plastic HV untouched tonight; scint_hv plastic_scan_2
ended 19:05 restoring per-channel nominals).

* COMPLETE (finished 23:39 after a 21:27–~23:15 beam stop parked it in its
  beam gate; M5 restored to counters, verified). 30/32 points valid.
* Sector coincidences 100–280 Hz everywhere; harshest corner (x3.3, −66) costs
  ~40% on A / ~35-45% on B/D. Rate leverage of thresholds remains weak,
  consistent with the 07-16 conclusion; ~30-45k TT tags/point banked for the
  offline in-window analysis (analyze.py from ~/beam_july/analysis/rate_scan_2d).
* D reads 0 at plastic −20 in pass BD (expected — dead channel below −24).
* Points `BD_w1.0_p-44` and `BD_w1.0_p-66` were caught by the beam-death
  transition (beam_e10 ~10) — EXCLUDE from the main scan; clean retakes (plus
  AC duplicates as consistency checks) in
  `~/beam_july/rate_scan_2d/2026-07-17_23-40-18_night_2d_retake/`
  (BD retakes: D coinc 130 Hz @ −44, 110 Hz @ −66).

## 3a. In-window Singles/Doubles analysis (07-18 00:00–00:30)
Full analysis + LaTeX slides:
`~/beam_july/analysis/rate_scan_2d/night_0717/` (`FINDINGS_2026-07-18.md`,
`analyze_night.py`, `key_numbers.json`, `slides/slides.pdf`). Headlines:
* Singles 1696/window nominal (70–90× DREAM budget grid-wide) — Singles-gated
  stays infeasible; thresholds buy ≤1.2× merged.
* Doubles 79/window total, 92% in the first ms; **clean tail 5.6/window
  (~2× the 07-16 yield, ~50–60k two-arm candidates / 8 h)** →
  **Doubles-gated remains the overnight trigger** (~7–8 recorded/window).
* In-window fraction 0.87 (median 0.86, was 0.77) — the +20 ns alignment.
* ⚠ **PS/γ-flash line on M5 TT panel 3 gave ZERO edges all night** (both
  scans); windows anchored on pulser clusters instead (07-16 method, results
  unaffected). **Check panel-3 cabling at next access.**
* Third exclusion found in analysis: `BD_w1.0_p-30` beam-compromised
  (nominal cell rests on its clean AC twin). Method fix: t0 = first
  burst-start tag (lone pre-flash accidentals were faking tail counts).

## 3b. Equalized-HV re-check (07-18 02:21–02:29) — thresholds RATIFIED
The night scans above ran on the FLAT plastic-PMT nominals plastic_scan_2
restored at 19:05 (1325/1275/1325/1300×5), NOT the run224466 gain-equalized
set (Δ up to +120 V on C_L / −117 V on D_R). At 02:21 the PMTs were ramped to
the equalized values (now the STANDING HV — `scint_hv_config.py` nominal_v
updated) and the plastic ladders rerun
(`~/beam_july/threshold_ladder/2026-07-18_02-{21-43_eqhv_full_ladder,25-49_eqhv_d_zoom}/`):
* A/B/C singles now track within ~2% at every threshold — equalization works.
* **D at −38 vs A/B/C at −30: rate ratio 1.03** — the applied baselines are
  rate-matched; **no threshold change needed** (walls 15/16/15/16 + plastics
  −30/−30/−30/−38 stand).
* D dead boundary at equalized HV: partial −26, dead ≤ −24 (module-input
  baseline effect, HV-independent as expected).
* A/B/C noise wall now ~−13/−16 (was ~−10/−13 flat) → −30 keeps ~2.3× margin.
* Caveat inherited by §3: the 2D scan's absolute rates (and the 2.1×-vs-07-16
  Singles ratio) partly reflect the flat-HV offsets (hot C_L, cold D_R), not
  FIFO gain alone; in-window fractions and the Doubles-gated conclusion are
  unaffected (per-window structure, not absolute gain).

## 4. State left on the boards (intended, do not blindly revert)
Final verify 23:47: `applied+verified: {'wall': {'A': 15, 'B': 16, 'C': 15,
'D': 16}, 'plastic': {'A': -30, 'B': -30, 'C': -30, 'D': -38}}` (rate_scan_2d's
exit restore had put D back to −30; re-applied −38 as the last board write).
* M1 thresholds A:15 B:16 C:15 D:16 mV; M2 thresholds A/B/C −30, D −38 mV.
* M3: all sectors ch0 (wall) G&D delay **+20 ns**, ch1 (scint) delay 0, both
  gate 20 ns. Output monos unchanged (30 ns). M1/M2 leg monos unchanged (15 ns).
* M4, M5, M6: untouched by the scans (M5 SEC_D cycled TT<->counter by
  rate_scan_2d and restored; trigger mode flash_random verified before/after
  each phase).
* Plastic PMT HV (CAEN card 07): **run224466 gain-equalized set since 07-18
  02:21** (A_L 1303 / A_R 1242 / B_L 1376 / B_R 1279 / C_L 1180 / C_R 1307 /
  D_L 1303 / D_R 1417) — `scint_hv_config.py` nominal_v now matches, so scan
  end-restores keep it.
