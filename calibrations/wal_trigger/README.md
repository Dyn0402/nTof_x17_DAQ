# SiPM-wall trigger thresholds (top+bottom group sums)

**Source: run224460 (2026-07-16), analysis `nTof_x17/mx_july_beam_qa/18_*`.**
The wall trigger fires on the analog sum of each 4-bar group's top+bottom
SiPMs; **one threshold per wall** (4 groups share it). Thresholds below are
digitizer-equivalent mV of `amp(top)+amp(bottom)` — calibrate the mapping to
the hardware discriminator (N1081B) scale before setting.

## Recommended operating point (weakest group ≥95% MIP efficiency)

| wall | **threshold** | MIP eff g1–g4 | purity g1–g4 | rel. rate [pairs/bunch] | if duplication unfixed |
|---|---|---|---|---|---|
| WALA | **12 mV** | 0.96 / 0.99 / 0.98 / 0.98 | 0.92–0.94 | 2454 | 2878 (+17%) |
| WALB | **14 mV** | 0.97 / 0.96 / 0.96 / 1.00 | 0.88–0.91 | 2916 | 2981 (+2%) |
| WALC | **12 mV** | 0.96 / 0.96 / 0.97 / 0.98 | 0.84–0.88 | 3184 | 3293 (+3%) |
| WALD | **14 mV** | 0.99 / 0.96 / 0.97 / 0.97 | 0.85–0.94 | 2526 | 3168 (+25%) |

MIP-sum peaks per group [mV] — the scale everything is relative to:

| | g1 | g2 | g3 | g4 |
|---|---|---|---|---|
| WALA | 46 | 48 | 48 | 44 |
| WALB | 62 | 66 | 68 | 68 |
| WALC | 66 | 66 | 64 | 70 |
| WALD | 72 | 66 | 72 | **44** (weak ch7/8) |

## Dropping the rate: landmarks

Full 1-mV-step table: `threshold_scan_<run>.csv`
(columns: per-group eff, min eff, per-group purity, rate, rate-if-dup-unfixed).
Approximate landmarks from the scan figures:

- **~20 mV**: all walls keep min-group eff ≈ 0.90–0.93; ~25–30% less rate than
  recommended.
- **~25 mV** (junk valley edge): strong groups ≥0.90, but WALA / WALD-g4 fall
  to ~0.85; roughly **40% less rate**.
- **~35 mV**: half the MIP-sum peak of the weak groups — WALA/WALD-g4 eff
  ~0.65–0.75, only for desperate rate reductions.

Use the CSV to pick exact values; scale your *observed* trigger rate by the
ratio of table rates (table rates are late-TOF relative numbers, not in-spill
absolutes).

## Caveats (serious ones)

1. **Signal duplication** (WALA 5↔7, WALD 2↔4, 5↔7; equal-amplitude analog
   copies): cannot be vetoed in hardware. Until the cabling/summing fix, WALD
   triggers ~+25% and WALA ~+17% above table rates (column
   `pairs_per_bunch_dup_unfixed`). Fix before freezing rate budgets.
2. **HV state**: constants assume the run224460 operating point (WALA ~30% low
   gain overall, WALD g4 weak). After the planned HV trims, re-derive — walls
   should converge to a common ~14–16 mV threshold.
3. Purity is tag-and-probe (plastic coincidence) and conservatively low near
   threshold; efficiency numbers are ε-independent.
4. Method + figures: `nTof_x17/mx_july_beam_qa/TRIGGER_THRESHOLDS.md` and
   `slides/run224460_slides.pdf`.

## Files

- `thresholds_<run>.json` — recommended values + per-group constants, with
  provenance (values are 1-mV bin centers, e.g. 12.5/13.5 — the tables above
  round them; the JSON is authoritative).
- `threshold_scan_<run>.csv` — the full trade-off table (1 mV steps, 0–100 mV).
- `figures/threshold_scan_<run>.png` — eff/purity/rate vs threshold per wall.
- `figures/trigsum_spectra_linear_<run>.png` — sum spectra with the cut drawn.
- `figures/purity_vs_eff_<run>.png` — trade-off curves per group.
