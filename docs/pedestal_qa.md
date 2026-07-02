# Pedestal QA — automatic dead-strip checking after each pedestal run

Added 2026-07-02. After every pedestal run, the pedestal QA watcher decodes the
`_pedthr_` FDFs, computes per-channel noise statistics, classifies
dead/noisy/disconnected strips and cables, and publishes the result to the
**Pedestals** tab of the DAQ GUI — no manual steps.

## Components

| Piece | Role |
|---|---|
| `pedestal_qa_config.py` | Edit-and-run config generator → `config/pedestal_qa_config.json` |
| `pedestal_watcher.py` | Daemon (tmux `pedestal_watcher`) that detects finished pedestal runs and launches the analysis |
| `scripts/pedestal_strip_check.py` | The analysis itself: decode → stats → classify → PNGs + `summary.json` + PDF (see `scripts/pedestal_strip_check.md`) |
| `flask_app/app.py` | Routes: `/start_ped_qa`, `/stop_ped_qa`, `/list_ped_runs`, `/ped_qa_data` |
| `flask_app/daq_status.py` | `get_pedestal_watcher_status()` — compact ("small") status card on the DAQ Overview |
| Pedestals tab (`index.html` / `base.html`) | Run selector, per-FEU summary table, PNG gallery, PDF link, watcher Start/Stop toggle |

## Data flow

```
Take Pedestals (GUI button)
  └─ run_pedestals.sh → daq_control → dream_daq
       └─ writes  <BASE_DATA_DIR>pedestals/pedestals_<MM-DD-YY_HH-MM-SS>/pedestals/*_pedthr_*.fdf

pedestal_watcher (poll every 10 s)
  └─ run dir found, pedthr set ≠ state, dir quiet for 60 s
       └─ <nTof_x17 venv python> scripts/pedestal_strip_check.py <fdf_dir> <run_dir>/ped_qa --decode-exe <decode>
            └─ writes  ped_qa/{00_channel_map,01_summary_table,feu_XX_*}.png
                       ped_qa/summary.json   (written atomically — GUI polls it)
                       ped_qa/pedestal_strip_check.pdf

Pedestals tab (auto-refresh every 10 s while open)
  └─ /list_ped_runs → newest-first dropdown ("(no QA yet)" until analyzed)
  └─ /ped_qa_data?run=... → verdict badge + per-FEU table + gallery + PDF
```

Decoded `.root` files land next to the FDFs and are reused on re-analysis.

## Operating it

- **Start/Stop:** toggle button in the Pedestals tab (top right), or
  `POST /start_ped_qa` / `/stop_ped_qa`. Start regenerates the config JSON from
  `pedestal_qa_config.py` and (re)creates the tmux session.
- **Timing:** a run appears in the tab roughly **4 min after the pedestal run
  ends** — 60 s quiet period (making sure dream_daq finished moving files)
  plus ~3 min of decode + analysis for a normal 32-sample run.
- **Status card:** compact chip on the DAQ Overview. `Analyzing` (green) /
  `IDLE`/`RUNNING` (blue) / `Failed` (yellow, backing off) / `STOPPED` (grey).
- **Logs:** `logs/pedestal_watcher.log` (launch/done/killed/failed events) and
  the tmux session `pedestal_watcher` for live output.

## Configuration (`pedestal_qa_config.py`)

| Key | Default | Meaning |
|---|---|---|
| `pedestals_dir` | `<BASE_DATA_DIR>pedestals/` | Where pedestal run dirs appear (derived from `run_config_beam.py`) |
| `analysis_python` | `nTof_x17/.venv/bin/python` | Interpreter with uproot/numpy/matplotlib (same venv qa_watcher uses) |
| `decode_exe` | `mm_strip_reconstruction/build/decoder/decode` | C++ FDF decoder |
| `output_inner_dir` | `ped_qa` | Output subdir inside each run dir |
| `poll_interval` | 10 s | Scan frequency |
| `quiet_sec` | 60 s | A run is "finished" when no file in its subrun dir changed for this long |
| `memory_kill_pct` | 80 % | Kill the analysis if **system** RAM crosses this (the machine idles at ~70 % of 16 GB) |

Edit the file, then Stop/Start the watcher (Start regenerates the JSON).

## State, retries, re-analysis

- `config/pedestal_qa_state.json` maps each run name to the list of pedthr
  FDFs last analyzed. A run is (re)analyzed whenever its FDF set differs from
  the state — so new files trigger a rerun automatically.
- **Force a re-analysis:** delete the run's entry from the state file (or the
  whole file to redo everything). Deleting a run's `ped_qa/summary.json` alone
  only blanks the tab; the state file is what the watcher checks.
- **Skip a run without analyzing:** add/keep its entry in the state file with
  its current FDF list (this is how the 12 GB `pedestals_07-01-26_19-27-05`
  run — taken 2026-07-01 with 400-sample waveforms before the 32-sample fix —
  was excluded; it shows "(no QA yet)" in the tab and is never retried).
- **Failure handling:** if the analysis exits nonzero or is memory-killed, the
  watcher deletes the (possibly truncated) decoded ROOTs and retries with
  exponential backoff — 60 s, 2 min, 4 min, … capped at 1 h — instead of
  hammering a broken run every poll. The memory kill signals the whole process
  group so the C++ decode children die with the python parent.

## Reading the results

Verdict levels (per FEU and overall): **good** ≤ 10 % bad channels < **warn**
≤ 50 % < **bad**. The summary table shows, per FEU: events, OK / noisy-floating
/ dead-stuck / other channel counts, median CNS RMS, and problem DREAMs
(`suspect` = ≥ 50 % bad channels, `disconnected` = whole-cable noise ≥ 2.5× the
global reference). Classification thresholds and their physical interpretation
(floating cable vs. stuck strip vs. common-mode pickup) are documented in
`scripts/pedestal_strip_check.md`.

`summary.json` (consumed by the tab, also handy for scripts):

```json
{
  "generated": "2026-07-02 11:19:33",
  "pedestal_ts": "2026-07-02 11:00",
  "ref_cns_rms": 14.47,
  "thresholds": {"noisy_float_mult": 3.0, "dead_stuck_frac": 0.1, "dream_suspect_mult": 2.5},
  "n_feus": 8, "total_bad": 334, "total_ch": 4096,
  "overall": "warn",
  "feus": {"1": {"n_events": 1033, "n_ok": 367, "n_noisy": 145, "n_dead": 0,
                  "n_other": 0, "med_cns_rms": 27.19,
                  "bad_dreams": {"0": "disconnected_cable"}, "level": "warn"}}
}
```

## Troubleshooting

- **Run stuck at "(no QA yet)":** watcher stopped (check the toggle / status
  chip), run still inside its 60 s quiet window, backing off after failures
  (`logs/pedestal_watcher.log`), or deliberately skipped via the state file.
- **Analysis keeps getting memory-killed:** check what else is eating RAM
  (`free -m`); as a last resort raise `memory_kill_pct`. The analysis itself is
  memory-flat (chunked ROOT reading, one figure open at a time) — kills at 80 %
  mean the *system* is under pressure.
- **Old 400-sample pedestal runs** (before commit `9d2c426`, 2026-07-01) have
  ~480 MB pedthr FDFs (12× normal) and aren't worth analyzing — skip them via
  the state file as above.
