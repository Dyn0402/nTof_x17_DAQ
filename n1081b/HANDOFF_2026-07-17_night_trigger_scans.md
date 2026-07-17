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

* Pass AC complete (16 pts): sector coincidences 100–200 Hz everywhere; harshest
  corner (x3.3, −66) costs only ~40% on A / ~30% on C. Rate leverage of
  thresholds remains weak, consistent with the 07-16 conclusion.
* D reads 0 at plastic −20 (expected — dead channel below −24).
* **Beam OFF 21:27–(ongoing at writeup)**; scan parked in its beam gate mid pass
  BD. Points `BD_w1.0_p-44` and `BD_w1.0_p-66` were caught by the beam-death
  transition (beam_e10 ~10) — EXCLUDE both from analysis (retake queued if beam
  returns in time).

## 4. State left on the boards (intended, do not blindly revert)
* M1 thresholds A:15 B:16 C:15 D:16 mV; M2 thresholds A/B/C −30, D −38 mV.
* M3: all sectors ch0 (wall) G&D delay **+20 ns**, ch1 (scint) delay 0, both
  gate 20 ns. Output monos unchanged (30 ns). M1/M2 leg monos unchanged (15 ns).
* M4, M5, M6: untouched by the scans (M5 SEC_D cycled TT<->counter by
  rate_scan_2d and restored; trigger mode flash_random verified before/after
  each phase).
