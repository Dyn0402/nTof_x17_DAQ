# n_TOF stream monitor (SiPM-wall dropouts)

Online version of the offline study in `~/beam_july/analysis/sipm_wall_filesize/`
(`FINDINGS.md`), in two layers:

| layer | cadence | cost | answers |
|---|---|---|---|
| **size** | every poll (120 s) | ~20 ms listing | did the stream volume drop? |
| **waveform** | sampled (≥ 5 min apart) | ~2 s, ~200 MB read | *which detector*, by how much? |

When a SiPM wall drops out its channels stop contributing zero-suppressed hits, so the
raw files roughly halve (~1.9 → ~0.92 GiB on 2026-07-21/22). Cheap to watch, but it
only says "something stopped contributing". Decoding one event says which detector: the
gamma flash is in **every** proton pulse, so its amplitude is an absolute gain
reference. During the dropout the four walls sat at 1.7–2.1 % of nominal (RMPA/RMPC at
0.3 %) while PSS/LIQ/PKUP were untouched at 100 %, with baselines and noise RMS
unchanged — a gain collapse, not a dead digitiser and not a beam or DAQ effect.

## Pieces

| file | role |
|---|---|
| `stream1_size_controller.py` | poll/grade/log loop (`Stream1SizeMonitor`), both layers |
| `ntof_raw.py` | vendored raw-file reader (format: `docs/NTOF_RAW_FORMAT.md`) |
| `../stream1_watcher.py` | thin entry point run in the `stream1_watcher` tmux session |
| `~/beam_july/slow_control/stream1_filesize/stream1_filesize_<day>.csv` | per-file size log |
| `~/beam_july/slow_control/stream1_filesize/stream1_waveform_<day>.csv` | per-detector waveform log |
| `config/stream1_filesize_state.json` | published state (Flask + Telegram monitor read it) |
| `config/stream1_waveform_nominal.json` | frozen per-detector reference |
| `config/stream1_filesize_config.json` | optional tuning overrides (absent = defaults) |
| `config/stream1_command.json` | GUI → watcher commands (only `set_nominal`) |

GUI: **Stream QA** tab (status card, per-detector gain table, flash-ratio trend,
colour-coded size-vs-time plot), start/stop button in Run Control, and a compact card
in the session-status row. Routes: `/stream1/status`, `/stream1/history?hours=`,
`/stream1/waveform_history?hours=`, `/stream1/nominal`, `/stream1/set_nominal` (POST).

Telegram rules (`flask_app/monitor.py`): `rule_stream1_watcher_dead`,
`rule_stream1_files_reduced` (warning at 5 consecutive bad files ≈ 6 min, alert at 20)
and `rule_stream1_detector_gain` (alert when any detector is below 50 % of nominal,
warning below 85 %). Tune via `rule_options` in `config/monitor_config.json`.

## Beam is part of both layers

Without protons on target there is no gamma flash and almost no triggers, so a beam
gap looks exactly like every detector dying at once. Measured on 2026-07-22:

| | file size | PKUP / PSS / LIQ (beam witnesses) | walls |
|---|---|---|---|
| healthy | 1.9–2.5 GiB | 100 % | 100 % |
| **wall dropout** (11:50, 224533_13) | 1.05 GiB | **100 %** | **30 %**, 1–7 ZS blocks |
| **beam gap** (14:25, 224533_137) | 0.41 GiB | **0.0–0.2 %** | 15 % |

So the two are cleanly separable, and both layers use it:

* **size** — files are graded **per proton pulse** (a file holds one event per pulse,
  so this is the beam-independent quantity), counting pulses ≥ 50 e10 from the beam
  watcher's CSV over the file's own writing window. A file with fewer than 3 pulses is
  graded `no_beam`: kept out of the baseline, out of dropout episodes and out of
  alerting. Without any beam data the layer falls back to raw sizes and says so.
* **waveform** — a sample is `no_beam` when the in-event witnesses (PKUP, PSS, LIQ) are
  *all* below 50 % of nominal, or when the beam log shows no pulses. That check is
  self-contained, so it still works if the beam watcher is down. `PKUP` is a witness
  only and is never graded: its amplitude is proportional to beam intensity (38 637 in
  run 224533 vs 14 411 in 224534) rather than railing like the other detectors.

Two other artefacts that used to read as dropouts, both fixed:

* files appear in the EOS listing **before their content lands** (224534_8 was listed at
  0 bytes and was 2.0 GiB minutes later), so a file is only graded once its size has
  settled — either a newer file exists for its run, or the size is unchanged since the
  previous listing;
* EOS mtimes are **UTC** (see below).

## How the size layer works

Every 120 s it runs `xrdfs root://eospublic.cern.ch ls -l <EOS_BASE>/<run>/stream1` for
the two newest run dirs — **sizes and mtimes only, no file contents are read** (a
listing is ~20 ms). New files are appended to the per-day CSV and graded against a
trailing-window baseline:

    per_pulse = file size / proton pulses in its writing window   (beam removed)

    good          per_pulse >= 0.85 * benchmark
    questionable  0.70 * benchmark <= per_pulse < 0.85 * benchmark
    bad           per_pulse <  0.70 * benchmark                  (wall-dropout level)
    no_beam       fewer than 3 pulses — not graded at all

The benchmark is **frozen**, like the waveform nominal — `config/stream1_size_nominal.json`,
in bytes per pulse. A trailing average is the obvious choice and the wrong one: it
absorbs a long dropout and stops calling it a dropout (2026-07-22 11:50, the size layer
read *good* straight through a real one while the walls were at 30 % of nominal). It is
auto-seeded once from a healthy stretch and then only changes on request.

The trailing level is still computed and published as the **suggestion**, from

    anchor     = median of the largest 25 % of the last 400 gradeable files
    suggestion = median of the files within 70 % of the anchor   (typical level NOW)

shown on the card next to the frozen value with the drift between them. That drift is
the run-config signal: absolute level is not comparable across configurations (the
offline study saw 1.9 vs 2.4 GiB in adjacent runs; 2026-07-22 saw the per-pulse level
double at a config change), so when it moves and the waveform layer says the detectors
are healthy, re-freeze with **Set benchmark** (blank = take the suggestion, or type a
value in MiB/pulse). The watcher refuses to freeze while the waveform layer reports a
detector below nominal gain, or while most recent files are graded bad — the point is
that a benchmark can never be measured through a fault.

The cuts are looser than they would be for raw sizes (0.90/0.75) because counting
pulses in an approximate window adds ~±1 pulse (±8 %) of noise: measured over
2026-07-22, healthy files span 0.83-1.08 of baseline while the real 11:50 dropout sat
at 0.63-0.71, so 0.85/0.70 separates them with nothing spurious in "bad".

The two-pass form matters for the suggestion: taking the anchor itself would put it at
the top of normal ±10 % scatter. The grade of every file, and both cuts as they stood
when it arrived, are recorded in the CSV — so the plot's colours are what the watcher
actually decided, not a re-derivation.

When the stream goes quiet for 20 min the watcher checks whether the run has been
closed out — n_TOF writes `stream0/` (the run index + a `mark.dto` marker) only at the
end of a run — and reports `run_ended` ("Run Ended") if so, or `stale` ("No Files") if
the run is still open and files simply stopped arriving. Neither is a size anomaly.

**`xrdfs ls -l` prints mtimes in UTC**, so they are converted to local time on the way
in (`_eos_time`). Reading them as local made every file look 2 h old under CEST and
parked the monitor permanently in its "no new files" state; the same offset explains
the apparent write gap at the end of the offline study's window.

## How the waveform layer works

At most one file per `waveform_min_interval_s` (default 300 s), and always the *newest*
one, so a backlog of 100 files does not trigger 100 decodes. For that file it runs
`xrdfs cat` and feeds the stream straight into the bank reader, **stopping at the second
event header and killing the transfer there** — only the ~150–250 MB one event needs
ever crosses the network (~2 s at the rates seen from the DAQ PC).

Per channel, from the always-kept first block (the 30 µs around the flash, present
whether or not the channel still produces zero-suppressed pulses):

    baseline  median of the first 2000 samples
    rms       std of the same
    flash     max |sample − baseline|          <- the absolute gain reference
    zs_blocks number of zero-suppressed blocks <- how much physics it is still writing

Channels are averaged per detector and graded against `stream1_waveform_nominal.json`:
**bad** below 50 % of nominal flash, **questionable** below 85 %. Baseline shifts
(> 500 counts) and RMS changes (> 3×) are reported separately as notes, because those
mean a digitiser/DC-path fault rather than a gain loss.

### The nominal

Frozen, never rolling — the whole point of the file-size baseline's weakness (a long
dropout eventually becomes "normal") is avoided here. It is auto-seeded from the first
sampled file the size layer independently calls *good*, and thereafter changes only
when someone presses **Set nominal** in the GUI. Either way `adopt_nominal` refuses
while any wall is below 10 000 counts (alive ≈ 34 000, dead ≈ 600), so a re-baseline
during a dropout cannot bless the fault as normal.

Re-baseline after anything that legitimately changes gain: a wall HV change, a
digitiser reconfiguration, a cabling change.

## Operating notes

* Read-only — owns no hardware, commands nothing; safe to start/stop at any time. On
  restart it re-seeds its history from the CSVs, so it neither re-logs nor loses its
  baseline, and it backfills whatever arrived while it was down.
* Needs a valid Kerberos ticket for EOS (the keytab-seeded one the backup watcher
  uses). Without it the card reads **No EOS** and `rule_stream1_watcher_dead` fires.
* File size is the summed hit multiplicity of *everything* in stream1, so it says
  "something stopped contributing hits", not which wall. Confirm per-channel
  occupancy by decoding one reduced and one full file.
* Started at boot by `start_servers.sh`; also start/stop from the GUI.
