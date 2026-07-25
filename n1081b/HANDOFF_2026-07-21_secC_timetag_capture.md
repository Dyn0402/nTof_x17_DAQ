# HANDOFF 2026-07-21 — M5 section C time-tag capture (sector coincidences), beam-on

**One-line result:** N1081B `.244` (Module 5) section **C** streamed **300 s continuously
at ~270 Hz aggregate with zero loss and zero delivery gaps**, beam-on, inside a single
run_64 sub-run. The data is per-edge timestamps of the four **sector coincidences**
(`sectorN = wallN ∧ liqN`) and is ready to plot as counts vs time.

## 1. The data

```
~/beam_july/test/tt_stream_qualify/secC_run64_20260721_175230/
    edges.csv        80576 rows — one row per input edge   <- THE DATA
    counters.csv     free-running counter samples, pre/post baselines
    stats.json       run metadata + per-channel totals + gap list
```

### edges.csv
| column | meaning |
|---|---|
| `host_unix` | host receive time of the **packet** (s). Tags arrive batched → this value **repeats** across many rows. Use only for wall-clock anchoring, never for spacing. |
| `channel` | front-panel number **1, 2, 4, 5** (3 and 6 are empty, rack-wide convention) |
| `t_board_ns` | board timestamp, ns, **10 ns granularity**, free-running board clock |

**Channel → detector map** (`n1081b/n1081b_module_map.py::_module5`, verified 2026-07-16):

| panel channel | 1 | 2 | 4 | 5 |
|---|---|---|---|---|
| sector | 1 | 2 | 3 | 4 |
| counter index in `counters.csv` | c0 | c1 | c2 | c3 |
| measured rate this capture | 58.2 Hz | 75.5 Hz | 73.8 Hz | 61.1 Hz |

Each sector is M3's AND of the SiPM wall leg and the liquid-scint leg, 20 ns coincidence
window on the M3 input gate&delays, output mono 30 ns → M5.C scaler tap.

### counters.csv
`host_unix, phase, c0..c3` where `phase` ∈ {`pre`, `post`}, sampled ~every 2 s.
Counters are cumulative and free-running. **The `post` block restarts near zero** —
flipping the section to time-tag mode and back resets the counters. That is expected.

## 2. Gotchas before plotting

1. **Sort globally by `t_board_ns` first.** Each packet carries per-channel tag blocks
   concatenated, so ~6 % of consecutive rows step backwards in the file as written.
   Within any single channel the timestamps are **strictly monotonic** (verified: zero
   violations on all four channels).
2. **`t_board_ns` has no absolute meaning** — free-running board clock, does not reset on
   `reset_channel`, no relation to the DAQ clock. For elapsed time use
   `(t_board_ns - t_board_ns.min()) / 1e9`. Capture spans 300.058 s.
3. **Do not derive rate from `host_unix`** (batched, 0.1–0.3 s quantized). All timing
   comes from `t_board_ns`; `host_unix` only maps the capture onto wall-clock/beam data.
4. Anchor: first edge `host_unix` 1784649181.663 ≈ **2026-07-21 17:53:01 CEST**.
5. Minimum per-channel spacing 40–50 ns; median inter-arrival 13–30 µs per channel.
6. There is **no trigger reference in this file.** Only one section can stream at a time
   (packets are broadcast with no section id), so M5.D — Singles / Doubles / gated pulser
   / **master trigger** — was *not* captured. Matching these edges to DREAM events is an
   open problem, not something this dataset solves.

## 3. Run context for the same 300 s

Captured inside sub-run **`sngPSmesh_dr700_r550_031`** of **run_64** (started 17:52:05,
stream 17:53:01→17:58:01):

```
~/beam_july/runs/run_64/sngPSmesh_dr700_r550_031/    decoded_root, hits_root,
                                                     hv_monitor.csv, n1081b_config.json
~/july_dream/dream_run/run_64/sngPSmesh_dr700_r550_031/     raw FDFs
~/beam_july/slow_control/beam_intensity/beam_intensity_2026-07-21.csv
```

HV point: drift **700 V**, resist **550 V** (det D = −10 V). Mesh charge-injection is ON
for detectors **A and C only** all run (B, D = same-beam no-mesh control). Trigger:
`scint --singles --ps-pickup`, RAW readout, 64 smp × 60 ns, IPD 90, latency 33. Full
description: `run_config_mesh_ac_scan.py` docstring.

Overlaying the beam CSV is worth it — counts vs time should show n_TOF spill structure
(~1.16 s grid, see memory `run62-noPS-firsttooth`), not a flat Poisson rate.

## 4. Documentation to read

| file | why |
|---|---|
| `n1081b/tt_stream_qualify.py` (module docstring) | how the capture was taken; gap-interpretation rules |
| `n1081b/HANDOFF_2026-07-17_tt_rate_ceiling.md` | the rate ceiling; why over-rate sections return silence |
| `n1081b/TT_STREAM_QUALIFY_PLAN_2026-07-17.md` | what the qualification was meant to answer |
| `n1081b/n1081b_module_map.py` | `_module5` (taps), `_module3` (sector AND), `_module1` (wall OR) |
| `n1081b/TIMETAG_MULTISECTION_2026-07-13.md` | broadcast/no-section-id, per-channel masking impossible |
| `n1081b/CLAUDE.md` | **mandatory** before any new board contact |

## 5. If another capture is needed

```
cd ~/PycharmProjects/nTof_x17_DAQ
.venv/bin/python n1081b/tt_stream_qualify.py --section C --duration 300 --pre 30 --post 30
```

Rules: check `config/n1081b_access/` first (no `*.holder.json` / `*.quarantine.json`);
run it **inside** a sub-run, not across a boundary — run_64 boundaries are ~635 s apart
(median of 30, min 631) and `poll_modules` reads all six boards at each DAQ-start. Budget
≤ ~400 s total (pre + stream + post) starting ~25 s after the boundary. An overrun is not
dangerous — `poll_modules` just skips `.244` for that sub-run — but it costs a snapshot.
`.244` is monitoring-only and not in the trigger path; never SIGKILL the stream.

## 6. Verification record (2026-07-21)

| check | result |
|---|---|
| completeness | streamed/expected **1.04–1.07** per channel (pre/post baselines bracket a rising rate → no loss) |
| delivery gaps | max packet gap **0.3 s**; gaps > 2 s: **0** |
| per-channel monotonicity | **0** violations on all four channels |
| timestamp granularity | 100 % of positive steps are multiples of 10 ns |
| section restored | `restore C -> counter: OK`, `stats.json: "restored": true, "finished": "clean"` |
| ceiling model | 270 Hz aggregate but only 52–76 Hz **per channel** → confirms the per-channel ceiling (~220 Hz/ch), not an aggregate one |
