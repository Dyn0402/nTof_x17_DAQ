# N1081B Trigger Modules

Tooling and notes for the six CAEN **N1081B** programmable-logic units that make
up the DAQ trigger. This directory is for exploring, snapshotting, homogenizing,
and (eventually) run-control integration of those units.

> The per-trigger **time-tag logger** (`trigger_logger.py` / `trigger_config.py`)
> currently lives at the repo root and is documented in
> `docs/n1081b_trigger_logger.md`. It may get folded in here later.

## The units

All six sit on the **private DAQ network**, reachable only from the DAQ server
(`ssh daq_lxplus`, host `mx17-daq`) — *not* from a dev laptop. WebSocket API on
`ws://<ip>:8080/`, default login password `password`.

**As of 2026-07-09 the trigger logic is racked:** trigger "Module N" =
`192.168.10.(239+N)` — the mapping is consecutive. Full as-built state + fix
list: `~/Documents/ntof_trigger_logic/TRIGGER_SETUP_2026-07.md` §0.5, snapshot
`snapshots/dump_2026-07-09_six_modules.json`.

| IP | Module | Serial | SW version | Role (A/B/C/D) as of 2026-07-09 |
|----|--------|--------|-----------|----------------------------------|
| 192.168.10.240 | 1 | 49323 | 2025.3.27.0 | SiPM wall OR ×4 (DISCR, th A/C +15, B/D +16 mV since 2026-07-17) |
| 192.168.10.241 | 2 | 22428 | 2025.3.27.0 | plastic-pair OR ×4 via linear fan-in/fan-out (DISCR, th A/B/C −30, D −38 mV since 2026-07-17; D1 input broken — wall D dead ≤ −24 mV) |
| 192.168.10.242 | 3 | 49325 | 2025.3.27.0 | sector AND ×4 (wall_i × scint_i) |
| 192.168.10.243 | 4 | 49326 | 2025.3.27.0 | Singles `or` / Doubles `majority` / `or_veto` / final `or` |
| 192.168.10.244 | 5 | 32429 | 2025.3.27.0 | scalers: counter ×4 |
| 192.168.10.245 | 6 | 23011 | 2025.3.27.0 | fanout (PS/T0) / fanout → **mesh charge-injection** (4 outs) / fanout → **SiPM enable** (TTL inv., 2 outs) / **pulse_gen** Poisson 667 Hz → Module 4.C p5 |

**Coincidence timing as-built 2026-07-17 night** (post-FIFO recalibration —
`HANDOFF_2026-07-17_night_trigger_scans.md`; trigger rates are **beam-driven**,
tracked in `config/beam_state.json`): the 20 ns sector-AND window is imposed at
**M3 (.242) input Gate&Delay** — both legs gate=20 ns, **wall leg (ch0) delay
+20 ns / scint leg (ch1) delay 0**, all sectors, compensating the FIFO+cable
plastic lateness (plateau centers B +17.8 / C +22.3 / D +23.6 ns, FWHM 34–45,
`timing_scan_night_v2run1.*`; scan tool `timing_delay_scan_v2.py`, which
supersedes `timing_task3_scan.py`). M1/M2 leg monos 15 ns (thinned 2026-07-14).
**M3 output monos 30 ns**, **M4.B Doubles window 50 ns**. Canonical dump
`snapshots/dump_2026-07-18_postfifo_canonical.json` — **do NOT restore any
older dump onto M1/M2/M3** (stale thresholds + delay=0). Historical (2026-07-11,
delay=0 era): `snapshots/dump_2026-07-11_timing_final.json`, delay scans
`timing_scan_run2.*`, walls aligned ≤11 ns (`walls_tt_v1.*`).

Board layout: 4 **sections** A–D (enum 0–3), each with 6 LEMO inputs (0–5). One
*function* is assigned per section.

All six boards run firmware `2025.3.27.0` (242 upgraded 2026-07-08; **245
upgraded 2026-07-11** during the switch-outage recovery — the old-fw SDK
quirks no longer apply anywhere). 245 is the ex veto-test rig (see
`VETO_TEST_RESULTS_2026-07-02.md`), moved from the CERN net onto the DAQ net.

**Run modes + full trigger IO layout (canonical): `RUN_MODES_2026-07.md`** —
the three DREAM run configurations (flash / flash+random / scint) with tuned
latencies, switched via `trigger_mode.py`.

**Homogenized 2026-07-01:** boards **240, 241, 242, 243** were reset to a uniform
state — all 4 sections = `wire`, input NIM / 50 Ω / threshold 0, all 6 input
channels enabled, output NIM. Saved on each board as `homogeneous_wire.json`.
**244 was left untouched (in use).** The original per-board configs (the roles in
the table above) are backed up — see Restore below.

### Key specs (from the datasheet)

- **Timing functions:** resolution **10 ns**, min detectable time 13 ns,
  reconstruction `t = 13 ns + bin_size × bin_number`; bin size 10 ns…1 s, ≤1024
  bins. (This is the time-tag tick resolution the logger needs.)
- Inputs: 6/section, 50 Ω / 1 kΩ, NIM/TTL/DISCR, min width 2 ns, min level ±10 mV.
- Discriminator threshold −800 mV…+2.5 V, 1 mV step.
- Max rate 80/100 MHz async, 40 MHz sync, scaler 130 MHz. Gate&Delay 5 ns step.

## The SDK

`n1081b-sdk` (PyPI), WebSocket transport, depends on `websocket-client`. Installed
in both venvs (dev laptop + server).

**Packaging gotcha:** the 1.0.4 wheel installs its package dir as `n1081b-sdk`
(hyphen — not importable) instead of `n1081b_sdk` (underscore, which its own
`__init__.py` expects). After any `pip install`/upgrade you must:

```bash
SP=$(.venv/bin/python -c "import site; print(site.getsitepackages()[0])")
mv "$SP/n1081b-sdk" "$SP/n1081b_sdk"
```

## Scripts

| Script | What it does | Where to run |
|--------|--------------|--------------|
| `dump_module_info.py` | Read-only: dumps *everything* readable from all 5 boards to one JSON on stdout. Doubles as a pre-change backup. | server (board net) |
| `summarize_dump.py` | Local: turns a `snapshots/*.json` dump into a human-readable per-board / per-section comparison. | anywhere |
| `homogenize.py` | Backs up each board (on-board `backup_pre_homog.json`), then sets all sections to wire + NIM/50Ω/th0. **Skips 244** unless `--include-244`. Re-run safe (won't clobber the backup). | server (board net) |
| `mod5_timetag_logger.py` | Streams per-edge timestamps from Module 5 (.244) to CSV via the Time Tag function — one row per input edge (host time, section, panel channel, board ns). `--section A..D` for one section, `--section cycle --dwell N` to rotate through all four (only one section can stream at a time — see Time-tag facts below). Restores the counter config on exit. | server (board net) |
| `poll_modules.py` | Read-only per-**sub-run** config snapshot, wired into `daq_control.py`: a background thread dumps each module's full state to `<run>/<sub_run>/n1081b_config.json` at DAQ-start, in parallel with data-taking. Fired right after the inline trigger apply so it records the as-built state; still waits on `.pause_run` (now only set by a manual pause). **Scope: all six** (`POLL_IPS`); drop `.244` if a time-tag run is concurrent. Full write-up: `SUBRUN_CONFIG_SNAPSHOT.md`. | server (board net) |
| `scan_control.py` | **In-process** per-sub-run trigger/mesh modulation, imported by `daq_control.py` (`N1081BScanControl`). For each sub-run it maps the leading name tag → `config/n1081b_scan_schedule.json` `scans[tag]` and applies it, verified by read-back, BEFORE taking data; snapshots the boards at run start and restores on exit. `mode='auto'` (run-config `n1081b_scan`) enables it whenever a sub-run tag matches a scan entry, else it's a no-op. **This replaced the standalone watcher process** — the modulation is now part of the run and can't be forgotten (the run_30/run_33 corruption). Also publishes to `config/n1081b_scan_active.json` on each apply (and marks it inactive on restore/exit), same as the old watcher, so the DAQ-GUI Trigger tab diagram band tracks the active scan for inline runs too. Does NOT set function types / pulser — that's `setup_run30_trigger.py`, still a one-time pre-run step. | server (board net) |
| `setup_liqscint_walls.py` | One-time per-wall conversion of Module 2 (.241) from the 2-plastic OR (Input 1+2) to a single **liquid-scint** input (Input 1 only). `--walls`, `--threshold`, `--dry-run`. Applied to walls A,D 2026-07-13; **reversed 2026-07-14** (see `setup_plastic_pairs.py`) — kept in the repo in case the swap is revisited. | server (board net) |
| `setup_plastic_pairs.py` | Sets Module 2 (.241) walls to the **2-plastic-scintillator OR** (Input 1+2, both enabled) at a given threshold. **Standing thresholds since 2026-07-17 (post-FIFO): A/B/C −30 mV, D −38 mV** (default is −30; wall D refuses values shallower than −36 — its D1 input is broken, dead ≤ −24). `--walls`, `--threshold`, `--dry-run`. Read-back verified, logs to `snapshots/plastic_pairs_log.jsonl`. | server (board net) |
| `n1081b_scan_watcher.py` | **Legacy / manual only, CLI-only (no GUI button).** The old standalone watcher that synced the board via `.subrun_complete` + `.pause_run`. Superseded by `scan_control.py` for data runs — do NOT run it during a daq_control run (both would drive the board). Still useful standalone for `--restore-baseline`, `--dry-run`, and as the home of the shared board primitives `scan_control` imports. | server (board net) |

Typical flow (from the dev laptop):

```bash
ssh daq_lxplus '~/PycharmProjects/nTof_x17_DAQ/.venv/bin/python -' \
    < n1081b/dump_module_info.py > n1081b/snapshots/dump.json
python n1081b/summarize_dump.py n1081b/snapshots/dump.json
```

`snapshots/` holds captured board state (JSON). Keep at least one pre-change dump
as the restore reference.

## Time-tag streaming facts (measured 2026-07-10 on .244, fw 2025.3.27.0)

- `configure_time_tagging` returns **`Result: False` but the config applies
  anyway** — cosmetic firmware bug, ignore the return value.
- Tag element = `[channel, timestamp]`: channel = **panel number 1–6**
  (= SDK lemo + 1), timestamp in **ns** (10 ns granularity) from a
  **free-running board clock** (does not reset on `reset_channel`).
- Time Tag sees **all 6 lemos** of a section; counter/scaler only cover lemo 0–3.
- `send_data` packets carry **no section id**, and the board **broadcasts every
  packet to every connected websocket client**. Consequences:
  - only **one section can stream at a time** per board (two at once → merged,
    unattributable tags); cover all 24 inputs by cycling sections;
  - **don't run any other SDK connection against a board while it streams** —
    the broadcasts interleave with that client's replies and desync it.
- The SDK's `start_acquisition`/`stop_acquisition` for TT send two commands but
  read one reply → desync. Send `reset_channel` + `start_tt_data` /
  `stop_tt_data` raw and consume replies tolerantly (see `mod5_timetag_logger.py`).
- Throughput: ~1 kHz aggregate verified live; 10 kHz verified in the 2026-07-02
  veto test. Higher rates untested (websocket + JSON per packet).
- **Push model, not polling:** after `start_tt_data` the board emits `send_data`
  packets on its own; the client just `recv()`s. Measured at ~800 Hz: the board
  flushes every ~10–100 ms (median 12 ms), each packet batching whatever
  accumulated (median 8 tags; up to 1028 seen in one packet — the internal FIFO
  is likely 1024 tags deep).
- **Stalled reader is safe for a while:** with the stream running at ~750 Hz and
  the client deliberately not reading for 20 s, **zero tags were lost** — TCP
  backpressure buffered ~15 k tags (~300 kB) end-to-end and they arrived in a
  catch-up burst (≥4.5 k tags/s drain rate). So there is no required poll
  frequency; the client only needs to keep up *on average*. Behavior once the
  TCP buffers actually fill (board blocks vs. drops) is untested.

## Password

Default login password is `password` on all five — **kept as-is** (decided
2026-07-01). All our SDK tooling logs in automatically, so the password is
already transparent for scripted access; nothing to re-enable later.

The **web GUI has no login/password at all** (zero mentions in the 90-page manual —
access over Ethernet is open on the DAQ net), so there's nothing to disable there.
The only password is the **WebSocket API** `login`. An **empty password cannot
suppress it**: `change_password("")` returns `Result:false` and `login("")` fails —
the firmware requires a non-empty password. The round-trip *is* reversible and
verified (temp password set and restored on 240), so a shared temp password is
possible if ever needed, but we're not using one.

## Documentation (`docs/`)

Downloaded to `docs/`:

- `DS8138_x1081B_datasheet.pdf` — official CAEN datasheet (specs above come from here).
- `N1081B-CERN.pdf` — 41-slide CERN talk on the module.
- `SDK_README.md`, `SDK_CHANGELOG.md` — from the SDK repo.

**Behind a login wall** (need a free CAEN account — grab these and drop into `docs/`):

- **User Manual** and **Firmware**: <https://www.caen.it/download/> → search "N1081B".
- SDK source (moved from GitHub): <https://gitlab.nuclearinstruments.eu/public-repo/n1081/n1081b_sdk_python> (default branch `master`).

## Firmware

All five boards run `2025.3.27.0`. 242 was upgraded 2026-07-08 (previously
`2023.12.4.0`); the old-firmware quirks — `configure_or`/`__config_logic6` timing
out — no longer apply to it.

**We cannot upgrade from our Python tooling** — the SDK exposes no firmware
method. Upgrade paths are the board's **web GUI** (`http://<ip>/`, port 80 open on
the DAQ net) System/Firmware section, or the **2.8" touchscreen + USB stick**. Both
need the correct N1081B firmware package, which lives behind the CAEN download
login above. Firmware flashing is delicate and may wipe the active config — do it
deliberately (not mid-run), with a config backup in hand.

## Restore (undo the homogenization)

Two independent restore paths for 240/241/242/243:

1. **On-board:** each board holds `backup_pre_homog.json` (saved *before* any
   change). `dev.load_configuration_file("backup_pre_homog.json")` reverts it.
2. **Authoritative:** `snapshots/dump.json` is a full readback of every board's
   original state — re-apply via the SDK setters if the on-board file is ever lost.

(`download_configuration_file` returns `Result:true` but no inline content on this
firmware, so the config JSON can't be pulled to disk that way — rely on the two
paths above.)

## Next steps

See **`PLAN_2026-07-02.md`** for the detailed walk-through: (1) firmware-upgrade 242
via the web GUI, (2) password conclusion, (3) first trigger-recording test with the
time-tag logger (incl. the CH 1,2,4,5 / T0 caveats).

## Status / open decisions

- [x] Connectivity + SDK proof-of-concept (all 5 reachable, login OK).
- [x] Full read-only info dump of all boards (`snapshots/dump.json`).
- [x] Password policy decided: keep default `password` (empty is rejected by fw).
- [x] Homogenized 240/241/242/243 to all-wire NIM/50Ω/th0 (244 left in use).
- [x] Docs downloaded; time-tag resolution confirmed (10 ns).
- [ ] **244**: homogenize it too once it's free (`homogenize.py --include-244`).
- [x] **242 firmware** upgraded to `2025.3.27.0` (2026-07-08).
- [x] Run-control integration, phase 1: per-sub-run read-only config snapshot of
  all six boards (`poll_modules.py` → `daq_control.py`). See
  `SUBRUN_CONFIG_SNAPSHOT.md`.
