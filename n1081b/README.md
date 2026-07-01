# N1081B Trigger Modules

Tooling and notes for the five CAEN **N1081B** programmable-logic units that make
up the DAQ trigger. This directory is for exploring, snapshotting, homogenizing,
and (eventually) run-control integration of those units.

> The per-trigger **time-tag logger** (`trigger_logger.py` / `trigger_config.py`)
> currently lives at the repo root and is documented in
> `docs/n1081b_trigger_logger.md`. It may get folded in here later.

## The units

All five sit on the **private DAQ network**, reachable only from the DAQ server
(`ssh daq_lxplus`, host `mx17-daq`) — *not* from a dev laptop. WebSocket API on
`ws://<ip>:8080/`, default login password `password`.

| IP | Serial | SW version | Clock-out | Current role (A/B/C/D) |
|----|--------|-----------|-----------|------------------------|
| 192.168.10.240 | 49323 | 2025.3.27.0 | on | pulse_gen / counter / counter / counter |
| 192.168.10.241 | 22428 | 2025.3.27.0 | on | pulse_gen / counter / counter / counter |
| 192.168.10.242 | 49325 | **2023.12.4.0** | on | scaler / wire / wire / wire (TTL, th=300) |
| 192.168.10.243 | 49326 | 2025.3.27.0 | on | or / or / counter / or (mixed DISCR/NIM) |
| 192.168.10.244 | 32429 | 2025.3.27.0 | **off** | majority / counter / or_veto / counter |

Board layout: 4 **sections** A–D (enum 0–3), each with 6 LEMO inputs (0–5). One
*function* is assigned per section.

⚠️ **242 is on older firmware** than the rest (see Firmware below).

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

Typical flow (from the dev laptop):

```bash
ssh daq_lxplus '~/PycharmProjects/nTof_x17_DAQ/.venv/bin/python -' \
    < n1081b/dump_module_info.py > n1081b/snapshots/dump.json
python n1081b/summarize_dump.py n1081b/snapshots/dump.json
```

`snapshots/` holds captured board state (JSON). Keep at least one pre-change dump
as the restore reference.

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

242 runs `2023.12.4.0`; the others run `2025.3.27.0`.

**We cannot upgrade it from our Python tooling** — the SDK exposes no firmware
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
- [ ] **242 firmware** upgrade to `2025.3.27.0` — needs firmware pkg + web GUI/USB.
- [ ] Run-control integration (watcher-style, like `qa_watcher.py`).
