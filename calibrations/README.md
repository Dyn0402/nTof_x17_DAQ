# Detector calibrations

One subdirectory per detector system / calibration type. Each holds:
machine-readable constants (`*.json`, `*.csv`) tagged by source run, the
operating figures, and a `README.md` that answers the operational questions
without needing the analysis repo. Analyses live in `nTof_x17/mx_july_beam_qa/`,
`nTof_x17/ntof_processing/` and `nTof_x17/ntof_dream_merge/`; files here are
exported snapshots with provenance — **regenerate rather than hand-edit**.

## ⚠ Two kinds of number live here, and confusing them is expensive

| | **DAQ settings** | **offline constants** |
|---|---|---|
| what it is | a value dialled into hardware | a value applied in software after the fact |
| who acts on it | the shifter, at the crate | the analysis |
| when it changes | when someone turns a knob | when the *processing* or the *run pair* changes |
| getting it wrong | wrong data is taken, irreversibly | wrong answer from good data, fixable |

Every directory below is labelled. A file that records *what was set* is not the
same as a file that recommends *what to set* — `dream_ntof/n1081b_thresholds_*`
is the former, `wal_trigger/` and `pss_trigger/` are the latter.

## Contents

### DAQ settings — what to dial in

| dir | what | status |
|---|---|---|
| `wal_trigger/` | SiPM-wall trigger thresholds (top+bottom group sums, one per wall): recommended values + full threshold→efficiency/purity/rate scan | **filled** (run224460) |
| `pss_trigger/` | Plastic trigger thresholds vs the 10 mV discriminator floor: efficiencies + HV-recovery options | **filled** (run224466) |
| `pss/` | Plastic PMT gain curves G∝V^n, HV equalization + global gain-slide table | **filled** (run224466 HV scan) |
| `mm/` | Micromegas tracker HV operating points: statistics-run setpoint + per-detector resist-HV fine-scan ranges (drift/resist) | **filled** (run_67) |

### Offline constants — what the analysis applies

| dir | what | scope / lifetime | status |
|---|---|---|---|
| `flash_timing/` | γ-flash arrival time per channel vs the beam pickup: `t_flash = tof_PKUP + C`. 32 wall constants + LIQ time-base monitor | per hardware epoch; **PSS per run** | **filled** (divert-off runs 224356–360, 224464/466) |
| `dream_ntof/` | the DREAM→n_TOF time map (`K`, `T0`, per-arm, per-bunch), the ±25 ns accept window, and the in-situ n_TOF internal alignment | **per (DREAM run, n_TOF processing) pair** | **filled** (run_79 ↔ 224572 / v12_liqpileup) |
| `wal_mip/` | Per-channel SiPM-wall MIP amplitude constants (ADC & mV) | per wall HV state | **filled** (run224460) |
| `liq/` | Liquid scintillators (LS): MIP / PSD calibration | — | placeholder — awaiting LIQ readout |

`dream_ntof/` also carries `n1081b_thresholds_run_79.json`, which is DAQ state
recorded *as operated* because the offline trigger emulation needs it as an
input. It is the one file here that crosses the line, and it says so.

### Neither

| dir | what | status |
|---|---|---|
| `gas_bottle/` | Argon bottle pressure vs time, read off photos of the panel gauge. An operational log that grows; see its README to add rows | **running** (from 2026-07-07) |

Anticipated later: `sili/` (Si monitor), MM gain-vs-HV curves + per-strip
threshold maps.

## Operational quick answer: "we need to drop the trigger rate"

1. Open `wal_trigger/threshold_scan_<run>.csv` (or the figures in
   `wal_trigger/figures/`).
2. Pick the new threshold per wall from the scan row that gives the rate you
   need; the same row tells you exactly the per-group MIP efficiency and purity
   you are giving up. Landmarks are in `wal_trigger/README.md`.
3. Rates in the table are *relative* (late-TOF sample, per bunch) — scale your
   currently observed rate by the ratio of table rates between old and new
   thresholds.

## Operational quick answer: "set up a Micromegas statistics run"

`mm/statistics_run_config_run67.json`: drift **700 V** on all four, plastic
discriminator **0.90 MIP**, resist **A 540 / B 540 / C 525 / D 520 V**. Bands,
evidence and caveats (notably: efficiency only — no spark data; and Det B only
works at drift 700) in `mm/README.md`.

## Operational quick answer: "we want more/less plastic gain globally"

Open `pss/global_gain_slide_<run>.csv`, pick the column with the desired gain
factor g, set the 8 listed voltages (CAEN card 07). Each PMT moves by its own
amount (`V × g^(1/n)`, n per PMT) so the fleet stays equalized. Details and
caveats in `pss/README.md`.

## Operational quick answer: "what time did the γ-flash hit this channel?"

`t_flash = tof_PKUP(bunch) + C_ns[channel]`, with the 32 constants in
`flash_timing/flash_time_constants.json` (2026-07-16 epoch for runs ≥224400).
**Per channel, not per wall** — a per-detector constant is no better than a
single global one. Never use the PSA's stored `tflash` from the *official*
files; the wall flash finder is bistable within a run. Details in
`flash_timing/README.md`.

## Operational quick answer: "which n_TOF coincidence fired this DREAM trigger?"

Apply the time map in `dream_ntof/time_map_*.json` and accept the nearest
sector-SINGLES candidate within **±25 ns**: 95.8 % of triggers get one, with a
measured 0.049 % chance of it being an accident. **Re-fit `K` and `T0` for any
new run pair** — they do not transfer, not even between two processings of the
same run. `dream_ntof/README.md` says what else does not transfer.

## Provenance / regeneration

Every JSON carries a `provenance` block (run, date, selection, HV state,
caveats). Regenerate after any HV or hardware change:

```
cd ~/PycharmProjects/nTof_x17/mx_july_beam_qa
python 18_trigger_threshold.py <run_file>    # trigger sums cache
python 18c_export_daq_calib.py <run_stem>    # export here

cd ~/PycharmProjects/nTof_x17
.venv/bin/python ntof_processing/flash_timing/scripts/export_daq_calib.py
cd ntof_dream_merge/match_study/scripts && ../../../.venv/bin/python export_daq_calib.py
```
