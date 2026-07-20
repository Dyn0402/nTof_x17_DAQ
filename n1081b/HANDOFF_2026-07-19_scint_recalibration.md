# HANDOFF 2026-07-19 — scintillator trigger recalibration (HV + walls + plastics)

One-line: re-equalized the plastic PMTs to the **Y-88** set, then set the SiPM walls
to **half-MIP** and the plastic discriminators to **0.5-MIP**. All three are now the
standing config. Artifact (figures): claude.ai/code/artifact/3da100a7-4637-45ce-8f20-21f1e06412a3.

## Final standing config (verified on the boards)
| Stage | Old | **New (adopted 2026-07-19)** | Source |
|---|---|---|---|
| M1 walls (mV) | 15/16/15/16 | **25 / 35 / 34 / 36** | `daq/calibrations/wal_trigger/thresholds_halfMIP_run224503.json` (half-MIP) |
| M2 plastics (mV) | −30/−30/−30/−38 | **−65 / −78 / −86 / −83** | `daq/calibrations/pss/mip_thresholds_y88.json` (0.5-MIP) |
| Plastic PMT HV (V) | run224466 set | **Y88 set** (A_L1237/A_R1177/B_L1440/B_R1248/C_L1214/C_R1312/D_L1331/D_R1448) | `daq/calibrations/pss/hv_equalization_y88_fifo.json` |

M3 wall-leg G&D delay +20 ns unchanged. Trigger mode flash_random. Boards free, no quarantine.

## How each was set (and why the method differs)
1. **Plastic HV** — the plastics are now **DAQ-owned** (each is a `run_config_beam.py`
   detector with `hv_channels {'bias': (7,N)}` + `hv_setpoint`). Consequences:
   - The standalone `scint_hv_*` path REFUSES (card 7 no longer disjoint from the DAQ).
   - `daq_control` loads the config **once at run start** and re-asserts `hv_setpoint`
     every sub-run; there is **no live set-HV path** (only Flask `/run/hv_off`).
   - So HV can only be set when the **DAQ run is stopped** (then `hv_control` releases
     the CAEN session). Applied with the new tool
     `scintillator_hv/apply_plastic_hv.py <file> --daq-stopped` (ramp+verify; the
     `--daq-stopped` guard refuses if `daq_control.py` is actually running — it
     excludes the `dream_daq_control.py` false match). Persisted by editing the 8
     `hv_setpoint` in `run_config_beam.py` (+ `scint_hv_config.py` nominal_v) so a
     restarted run keeps it.
2. **Walls + plastics (M1/M2 thresholds)** — board-resident; **nothing re-applies them
   at run start** (verified: no threshold writes in trigger_mode / daq_control /
   scan_control). Set with `threshold_ladder.py --apply-wall` / `--apply-plastic`
   (set-and-leave), verified by read-back. Adopting = set board + update docs.

## Key derivations / judgment calls
- **Plastics are per-arm one threshold:** each M2 section OR's its two bars (lemo0+lemo1)
  under one discriminator (`setup_plastic_pairs.py`). The 0.5-MIP file is per-bar, so
  each section = **per-arm average** of the two bars' half-MIP. **D = D_R only** (−83)
  because D_L / PSSD1 is the broken input (dead ≤ −24 mV); −83 is safely deeper than the
  −36 retrigger guard.
- **Predicted MIP:** `mip_thresholds_y88.json` MIP peak (152 mV) is PREDICTED from the
  Y88 699-keVee absolute scale, **not measured** (no clean plastic triple-MIP yet). A
  prompt-beam / low-rate MIP run should confirm and replace `mip_peak_mV_fleet`; the
  0.5-MIP thresholds scale linearly with global gain g.
- **Digitizer→hardware mV** is treated ~1:1 (same as the walls); sanity-checked against
  the measured Y88 turn-on (thresholds land at 370–1600 Hz, live and non-saturated).

## Wall-rate cost (why half-MIP is safe)
Back-to-back plastic-normalized compare (`wall_rate_compare.py`): raising walls costs a
smooth **16–24%** of wall singles (A −16 / B −20 / C −24 / D −23 %), scaling with how far
each moved — a low-amplitude-tail trim, not a plateau collapse.

## Datasets (`~/beam_july/`)
- `threshold_ladder/2026-07-19_15-02-37_wide_plastic_to200` — 1st single-pass (old HV/walls).
- `…_wide_{AC,BD}_ref` — reference-normalized two-pass (old HV/walls).
- `…_wide_{AC,BD}_newwalls` — old HV, new walls (coincidence vs walls).
- `wall_rate_compare/2026-07-19_16-40-38_newcal_compare` — walls 15/16 vs 25/35/34/36.
- `…_wide_{AC,BD}_y88hv_newwalls` — **final config** coarse ladder (Fig 1 source).

## New tools
- `scintillator_hv/apply_plastic_hv.py` — parse calib (`pmts.<PMT>.{caen,v_suggested}` or
  flat), [1000,1550] V guard, ramp+verify, `--daq-stopped`, `--update-nominal`.
- `n1081b/wall_rate_compare.py` — plastic-normalized wall-singles compare (non-destructive).

## ⚠ Follow-ups / caveats
- **Canonical dump `snapshots/dump_2026-07-18_postfifo_canonical.json` is STALE** for M1
  walls, M2 plastics, and plastic HV — do NOT blindly restore it. Regenerate a
  2026-07-19 canonical dump (not done automatically).
- All config edits are git-revertible. Memory: `plastic-hv-y88-equalization`,
  `n1081b-postfifo-trigger-calibration`.
