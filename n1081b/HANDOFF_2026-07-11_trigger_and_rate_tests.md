# HANDOFF — Trigger timing + N1081B rate tests (2026-07-11)

What was done overnight 2026-07-10 → 07-11 on the n_TOF X17 trigger (N1081B modules), while the
DREAM FEU DAQ was unavailable. Two bodies of work: (A) finished the **trigger timing
optimization** (the `HANDOFF_2026-07-11_trigger_timing.md` task list) and fixed the Singles
trigger; (B) ran an **N1081B scaler-rate measurement campaign**. All rates here are **real beam
data** (parasitic ~400–850 ×10¹⁰ p), not cosmic — see `config/beam_state.json` / `beam_monitor/`.

Companion doc for the still-pending DREAM readout timing: `HANDOFF_2026-07-11_dream_latency_tuning.md`.

---

## 0. Current hardware state (as of 2026-07-11)

- **Network outage RESOLVED**: earlier tonight all 8 FEUs + M1(.240) + M6(.245) dropped off the
  network together (shared switch port-bank, not power — TCM stayed up on FEU power); they are
  **all back UP now** after the switch recovered. See `feu-m1-m6-switch-outage` memory note.
- **Trigger boards M2–M5 (.241–.244)** hold the final timing config below. **M1(.240)** is back
  on the network but its outputs ran the whole time on the last-latched config.
- **M2 (.241) scint discriminator thresholds restored to −80 mV** on all four sections.
- **run_29** (`run_config_beam.py`) is built and validated but NOT launched (was blocked on FEUs).

---

## A. Trigger timing optimization (as-executed)

### A.1 Constraint: M1 was offline → window imposed at M3, not M1/M2 monos
M1 (.240) went network-unreachable mid-work (still physically output walls). So the 20 ns
sector-AND coincidence window is imposed at **M3 (.242) INPUT Gate&Delay** on BOTH legs
(wall = in-ch0, scint = in-ch1, `enable_gd=True, gate=20, delay=0`) instead of at the M1/M2
output monostables. Verified equivalent on the LA (`gd_verify_m3.py`): G&D width-shapes 50→20 ns
and delay tracks to <10 ns. **Key gotcha: the G&D block adds a ~20 ns fixed insertion latency
that cancels only when BOTH legs are gated** — always gate both, never one.

### A.2 Delay-curve scan → NO per-sector delays needed
`timing_task3_scan.py` (two-set, beam-normalized `C/C_ref`: hold 2 sectors at delay 0 as a live
beam monitor, sweep the other 2). Signed delay ±60 ns, both legs gate=20. Analyze with
`analyze_timing_scan.py`. Per-sector plateau center / FWHM:

| Sector | center (ns) | FWHM (ns) |
|---|---|---|
| A | −6.8 | 39 |
| B | −0.1 | 36 |
| C | −3.4 | 38 |
| D | +3.1 | 39 |

All |center| ≤ 10 ns → **no per-sector delay applied**; the symmetric 20 ns window is
well-centered on every sector. FWHM ≈ w₁+w₂ = 40 ns confirms the gate. Stayed at 20 ns
(did **not** drop to 15 ns — would clip sector A). Plot `snapshots/timing_scan_run2.png`.

### A.3 Inter-wall alignment (Task 5) → no trim
`mod5_timetag_logger.py --section A` + `analyze_walls_tt.py`. All four wall pairs' time-tag
correlation peaks within ±11 ns (< ±20 ns tolerance) → walls mutually aligned, no M4.A/B trim.
Board clock is a monotonic ~46-bit ns counter (no wrap); walls = panels 1,2,4,5. Plot
`snapshots/walls_tt_v1.png`, data `walls_tt_v1.csv`.

### A.4 M3 output monos + Doubles window (Task 6)
`timing_task6.py`: M3 (.242) output monostables **200 → 30 ns** (all sectors); M4.B (.243 SEC_B)
Doubles coincidence window **100 → 50 ns**. Beam-normalized Doubles/Singles unchanged (0.988×) →
real doubles preserved, beam-burst accidentals tightened.

### A.5 Singles trigger fix — M4.D was wedged
The DREAM external trigger is **M4.D (.243 SEC_D) out0**. It was dead because D's OR had its
**panel-1 input (lemo 0) stuck HIGH**, holding the OR permanently high so the output mono never
re-triggered. The live Singles|Doubles signal actually arrives on **D panel 2 (lemo 1)**.
**Fix applied:** `configure_or(SEC_D, en1 only)` — D OR now uses lemo 1 only, dropping the
stuck-high lemo 0. **D.out0 now fires at the Singles rate** (LA-verified, ~44 Hz). Full chain:

```
Singles (M4.A out) ─┐
Doubles (M4.B out) ─┴─► M4.C in0,in1 ─► C = OR (no veto) ─► C out0 ─► M4.D in1 ─► D = OR(lemo1) ─► D out0 = DREAM trigger
```
⚠️ This is a live-board setting; it reverts if .243 power-cycles. Re-apply with
`configure_or(N1081B.Section.SEC_D, False,True,False,False,False,False, False,0)`.

### A.6 FINAL trigger config (what's set on the boards now)
- M3 (.242) inputs: `gd=True, gate=20, delay=0` on ch0(wall)+ch1(scint), all four sectors.
- M3 (.242) outputs: mono **30 ns**, all sectors.
- M4.B (.243) Doubles `configure_coincidence_gate` lemos 0,1,3,4, FIRST, **width 50 ns**.
- M4.C (.243) = **or_veto** (lemo0=Singles, lemo1=Doubles OR inputs; **lemo5 = veto**, an
  inverted-NIM ~30 ms delay-timer enable gate: HIGH in-window = enable, LOW outside = veto).
  Reports as function name `or` but IS or_veto. lemo5 reads ~100% high on an LA triggered on
  Singles because Singles are window-gated — that is the ENABLE state, not a stuck line. See the
  `m4c-veto-gate` memory note. M4.D = OR(lemo1) → DREAM trigger; D.in0 (another delay-timer line)
  stays disabled in D's OR.
- M2 (.241) scint discriminators = −80 mV (nominal).
- Snapshots: pre `snapshots/dump_2026-07-10_pre_timing_scan.json`, final
  `snapshots/dump_2026-07-11_timing_final.json`, trigger-fixed
  `snapshots/dump_2026-07-11_trigger_singles_fixed.json`, original M3 inputs
  `snapshots/m3_inputs_pretiming.json`.

---

## B. N1081B rate measurement campaign

`rate_campaign.py` (three phases, 30–60 s bins, beam recorded per bin) → `analyze_rate_campaign.py`
→ report `snapshots/rate_report_2026-07-11.pdf`. Data `snapshots/rate_campaign_run1.json`.
Scalers: M5.A walls, M5.B scints, M5.C sectors, M5.D0 = M4.A Singles; Doubles = M4.B TOTAL.
Walls (M1 outputs) are independent of the M2 scint threshold → used as the beam monitor.

### B.1 Element-to-element rate variation (beam-normalized fractions of the 4-channel sum)
- **Walls** W1/W2/W3/W4 = 0.22 / **0.31** / 0.23 / 0.25 (abs 652 / 907 / 667 / 723 Hz). W2 ~1.4×.
- **Scints** S1/S2/S3/S4 = 0.25 / 0.17 / **0.49** / 0.09 (abs 73 / 51 / 147 / 28 Hz). **S3 = 5.3× S4.**
- Plot `snapshots/rate_ratios.png`.

### B.2 Scint threshold scan (M2, all sections set common, −40…−140 mV)
`set_input_configuration(section, DISCRIMINATOR, sub=0, threshold_mV, IMPEDANCE_50)` — threshold
is **per section** (both plates in a section share it; no per-plate control). Wall-sum stayed flat
(~3000 Hz), a good normalizer. Singles **133 → 11.5 Hz**, Doubles **1.05 → 0.54 Hz** across the
range. **Recommended equalizing thresholds** (median target; NOT applied — left at −80):
S1 −88, S2 −75, **S3 −122**, S4 −60 mV. Plot `snapshots/threshold_scan.png`.

### B.3 Singles & Doubles, good statistics (50 min beam-on, 2136 doubles)
- **Singles (M4.A) = 49.40 ± 0.13 Hz**
- **Doubles (M4.B) = 0.712 ± 0.015 Hz**
- Doubles/Singles = 0.0144

### B.4 Beam-OFF baseline (`beam_off_rates.py`, `snapshots/beam_off_rates_off1.json`)
| | Beam-off | Beam-on | Beam-induced |
|---|---|---|---|
| Walls (Hz) | 65 / 92 / 65 / 79 | 652 / 907 / 667 / 723 | ~587 / 815 / 602 / 644 |
| Scints (Hz) | 7.6 / 6.4 / 8.7 / 5.0 | 73 / 51 / 147 / 28 | 65 / 45 / 138 / 23 |
| Singles (Hz) | 11.8 | 49.4 | 37.6 |
| Doubles (Hz) | 0.112 | 0.712 | 0.60 |

**Interpretation:** (i) **W2's excess is intrinsic** (W2/W1 = 1.42× off, 1.39× on → SiPM
dark/cosmic property, not beam). (ii) **S3's excess is real beam flux** (uniform beam-off, 6×
beam-induced → equalizing it at the discriminator would cut real signal, not noise). (iii) ~16%
of beam-on Doubles is the cosmic/accidental floor.

---

## C. Artifact index (all under `n1081b/`)

**Drivers/libs:** `m3_timing_lib.py` (shared: M3 G&D + M5 rate reads), `gd_verify_m3.py`,
`timing_task2_gate20.py`, `timing_task3_scan.py`, `timing_task6.py`, `rate_campaign.py`,
`beam_off_rates.py`. **Analyzers:** `analyze_timing_scan.py`, `analyze_walls_tt.py`,
`analyze_rate_campaign.py`. **Pre-existing used:** `mod5_timetag_logger.py`, `dump_module_info.py`.
**Reports (`snapshots/`):** `trigger_summary_2026-07-11.pdf`, `rate_report_2026-07-11.pdf`.
**Plots:** `timing_scan_run2.png`, `walls_tt_v1.png`, `rate_ratios.png`, `threshold_scan.png`.
**Data:** `timing_scan_run2.json`, `walls_tt_v1.csv`, `rate_campaign_run1.json`,
`beam_off_rates_off1.json`. Run all on mx17-daq via `.venv/bin/python`; SDK `n1081b_sdk`.

---

## D. Open items / TODO
1. **M1 (.240) is back** — decide whether to keep the window at M3 G&D (works, validated) or
   restore M1/M2 output-mono thinning now that M1 is configurable. No urgency; M3 gating is fine.
2. **DREAM readout latency** still un-tuned — see `HANDOFF_2026-07-11_dream_latency_tuning.md`.
   Now unblocked (FEUs back).
3. **Scint S3 (5.3× hot) and wall W2 (1.4× hot)** — S3 is beam flux (leave), W2 is intrinsic;
   W2 could be equalized at the M1 discriminator now that M1 is back, if desired.
4. **Trigger fix is volatile**: re-apply the M4.D OR(lemo1) config if .243 power-cycles (A.5).
5. Re-run the good-stats Singles/Doubles at dedicated beam for a cleaner absolute number.
