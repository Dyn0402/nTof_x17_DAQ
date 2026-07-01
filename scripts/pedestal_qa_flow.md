# Pedestal QA flow

Regular quality check of pedestal (`_pedthr_`) runs: find dead/noisy strips per FEU
and connector, save a per-run status (CSV + PDF), and diff against the previous run.

## TL;DR — the regular check

```bash
scripts/pedestal_qa.sh
```

This one command:
1. **Syncs** new pedestal runs' `_pedthr_` FDFs from the DAQ machine into the local
   pedestals directory.
2. **Runs QA** on every run that doesn't already have results, writing into each run dir:
   - `pedestal_strip_check.pdf` — full visual QA (channel map, per-FEU/connector tables,
     dead/noisy bars, per-FEU RMS scatter).
   - `pedestal_channels.csv` — one row per channel (the machine-readable status record).
   - `pedestal_summary.csv` — one row per (FEU, connector): dead/noisy counts + median RMS.
3. **Compares the two most recent runs**, writing into the pedestals parent dir:
   - `compare_runs.png` — per-FEU dead/noisy bars, one series per run.
   - `compare_summary.csv` — per (FEU, connector) dead/noisy for every compared run.
   - `compare_changes.csv` — **the channels whose status changed** between the two newest
     runs (the fastest "what changed since last time" view).

Re-running is cheap: QA is skipped for runs already done (decoded ROOTs are also cached),
and the comparison reads only the CSVs.

### Options
- `scripts/pedestal_qa.sh --no-sync` — skip the rsync; just (re)analyze/compare local data.
- `scripts/pedestal_qa.sh --force` — re-run QA even if a run already has a CSV.

### Config (env vars, defaults in the script)
```
PED_REMOTE_HOST=daq
PED_REMOTE_DIR=/home/mx17/beam_july/pedestals
PED_LOCAL=/media/dylan/data/x17/beam_july/pedestals
DECODE_EXE=/home/dylan/CLionProjects/mm_strip_reconstruction/cmake-build-debug/decoder/decode
```

## Running the pieces by hand

QA a single run directory (must contain `_pedthr_*.fdf`; decodes if needed):
```bash
DECODE_EXE=/home/dylan/CLionProjects/mm_strip_reconstruction/cmake-build-debug/decoder/decode \
  .venv/bin/python3 scripts/pedestal_strip_check.py <run_dir>
```

Compare runs (CSV-only, instant):
```bash
# two most recent runs under a pedestals parent dir
.venv/bin/python3 scripts/compare_pedestals.py /media/dylan/data/x17/beam_july/pedestals
# or specific run dirs, or --all to compare every run
.venv/bin/python3 scripts/compare_pedestals.py <dir1> <dir2> [...]
.venv/bin/python3 scripts/compare_pedestals.py --all /media/.../pedestals
```

## Classification (see thresholds at the top of `pedestal_strip_check.py`)

Per channel, judged on **raw RMS relative to that FEU's raw-RMS median**:
- `dead_stuck`  — raw RMS `< DEAD_STUCK_FRAC` × FEU median (default 0.40): no signal.
- `noisy_floating` — raw RMS `> NOISY_RAW_MULT` × FEU median (default 2.0): abnormally noisy.
- `ok` otherwise. `missing` if the channel never appears.

Per connector (DREAM cable), on the common-noise-subtracted (CNS) RMS:
- `disconnected_cable` — cable's median CNS RMS `> DREAM_SUSPECT_MULT` × global reference
  (whole 64-ch cable floating).

CNS RMS matches `WaveformAnalyzer::computePedestals()` in `mm_strip_reconstruction`
(per (event, sample) median across each 64-ch DREAM block subtracted, then std-dev).

## CSV schemas

`pedestal_channels.csv`:
`feu, connector, channel, strip, status, raw_rms, cns_rms, mean, n_events, ref_cns_rms`
(channel = 0–511 within the FEU; connector = channel//64 = DREAM D0–D7; strip = channel%64.)

`pedestal_summary.csv`:
`feu, connector, n_dead, n_noisy, n_ok, med_raw_rms, med_cns_rms, cable_status`

`compare_changes.csv`:
`feu, connector, channel, <prev>_status, <cur>_status, <prev>_raw, <cur>_raw, <prev>_cns, <cur>_cns`

## Notes from the first comparison (2026-07-01)

- The **dead-strip map is stable** run-to-run (same physical channels; e.g. FEU01
  D0/D3 and FEU07 D0/D3 are the persistent disconnected cables).
- **Noisy strips are the volatile part** — watch `compare_changes.csv`. FEU04 D0/D7 turned
  persistently noisy in the DAQ pedestals; FEU02 D7 was a one-off transient in a single run.
