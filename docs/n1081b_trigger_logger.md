# N1081B Trigger Logger — Handoff

Logs a per-trigger timestamp from one or more CAEN **N1081B** programmable-logic
units to CSV, using the official Nuclear Instruments Python SDK. Built to mirror the
existing `qa_watcher.py` / `qa_config.py` pattern so it can later be launched from the
flask UI as part of run control.

**Status:** code complete, compiles, config generation verified. **Not yet run against
hardware** — see [Tomorrow's hardware test](#tomorrows-hardware-test) and
[Things to tailor first](#things-to-tailor-to-our-system).

---

## Files

| File | Role |
|------|------|
| `trigger_config.py` | Edit constants → run → emits `config/trigger_config.json`. |
| `trigger_logger.py` | Reads that JSON, streams time tags to CSV (one thread/connection per unit). |
| `config/trigger_config.json` | Generated config consumed by the logger. |
| `logs/trigger_logger.log` | Lifecycle events (`START`, `STREAM_START`, `ERROR`, `STOP`, …). |

Outputs, per unit, written under `output_dir`:
- `<name>_triggers.csv` — one row per trigger.
- `<name>_triggers.meta.json` — host wall-clock `t0` at acquisition start + unit info.

---

## The SDK

- Package: `n1081b-sdk` (PyPI). Repo: `https://gitlab.nuclearinstruments.eu/public-repo/n1081/n1081b_sdk_python`
- Install into our venv: `.venv/bin/pip install n1081b-sdk` (also added to `requirements.txt`).
- Transport: **WebSocket**, `ws://<board-ip>:8080/`. Depends on `websocket-client`.
- Connect flow: `dev = N1081B(ip); dev.connect(); dev.login("password")`.
- Board layout: 4 **sections** A–D, each with 6 LEMO inputs. You assign one *function*
  per section, configure it, then `start_acquisition`.

### Why Time Tag (not Counter/RateMeter)

We want a timestamp per individual trigger, so we use the **Time Tag** function:

```python
dev.set_input_configuration(SEC_A, NIM, NIM, threshold, IMPEDANCE_50)
dev.set_section_function(SEC_A, FN_TIME_TAG)
dev.configure_time_tagging(SEC_A, en0, en1, en2, en3, en4, en5)  # per-LEMO enables
dev.stop_acquisition(SEC_A, FN_TIME_TAG)   # reset
dev.start_acquisition(SEC_A, FN_TIME_TAG)  # opens the data stream
while running:
    packet = dev.get_time_tag_data()       # list of per-trigger time tags
```

`get_time_tag_data()` is a **streaming push**: the board emits `send_data` packets as
triggers arrive and the SDK blocks on `recv()` for each. It is *not* a poll on a fixed
interval. (Counter / RateMeter sections, by contrast, are polled via
`get_function_results()` and give counts/rates but no individual trigger times — cheaper
at very high rates if we ever need that instead.)

---

## How `trigger_logger.py` works

- One **thread per unit**, each with its **own connection** — the SDK is explicitly not
  thread safe.
- Each thread: connect → login → configure input + section → `configure_time_tagging`
  with the enabled channels → reset → record host `t0` → `start_acquisition` → drain
  `get_time_tag_data()` into the CSV.
- The websocket `recv` timeout (`recv_timeout`, default 2 s) bounds how fast a unit
  notices a stop request when no triggers are arriving.
- Auto-reconnect: any error logs to `logs/trigger_logger.log`, waits `reconnect_interval`,
  and retries. The CSV is reopened in append mode so data already written is preserved.
- Clean shutdown on `Ctrl-C` / `SIGTERM`: each unit `stop_acquisition`s and disconnects.

### CSV schema

```
recv_iso, recv_unix, unit, section, tt_<...>
```

`recv_iso`/`recv_unix` are the **host** receive time of the packet (coarse — packets can
batch multiple triggers). The `tt_*` columns are the raw time-tag fields straight from
the board. **The element schema of `timetag_data` is firmware-specific and not documented
in the SDK source**, so the header is inferred from the first packet:
- dict element → one `tt_<key>` column per key,
- list/tuple element → `tt_0, tt_1, …`,
- scalar → `tt_value`.

> First real packet tomorrow tells us the actual fields. Once known, pin the columns
> explicitly and add the tick→seconds conversion.

---

## Running it standalone

```bash
# 1. one-time: install SDK into the venv
.venv/bin/pip install n1081b-sdk

# 2. edit unit IPs / passwords / sections / channels
$EDITOR trigger_config.py
.venv/bin/python trigger_config.py        # writes config/trigger_config.json

# 3. run (Ctrl-C to stop cleanly)
.venv/bin/python trigger_logger.py config/trigger_config.json
```

---

## Things to tailor to our system

- [ ] **Real IPs / passwords / sections / channels** in `trigger_config.py` (currently the
      SDK demo placeholder `192.168.50.153`, password `"password"`). One entry per N1081B.
- [ ] **`output_dir`** — currently `/mnt/data/x17/beam_may/triggers/`. Decide per-run vs
      flat layout.
- [ ] **Input standard / impedance / threshold** per unit — set to NIM / 50 Ω / 0 by
      default. If our trigger logic outputs are NIM into 50 Ω this is fine.
- [ ] **Which LEMO channels** carry trigger signals on each unit.
- [ ] Confirm the boards are reachable: `ping <ip>`, and that port 8080 is open.

## Open questions / known caveats

1. **Tick resolution (ns/tick) unknown.** Time tags are clock ticks from acquisition
   start, not wall clock. Get the resolution from `docs/SDK-n1081b.pdf` in the SDK repo.
   We write raw fields verbatim, so no precision is lost meanwhile.
2. **Cross-unit / cross-DAQ alignment.** Per-unit host `t0` is recorded, but host clocks
   drift. For real alignment between units (or against the DREAM DAQ) feed a common
   start/sync pulse and/or correlate on a shared reference channel.
3. **High-rate throughput.** Time Tag is websocket-push + JSON decode per packet. At very
   high rates this can bottleneck; fall back to a polled Counter/RateMeter section if we
   only need rates.
4. **Run-control integration** not done yet. Plan: launch from the flask UI like
   `qa_watcher`, pointing `output_dir` at the per-run directory; stop with the run.

## Tomorrow's hardware test

1. Install SDK; fill in one real unit in `trigger_config.py`; regenerate JSON.
2. `ping` the board; run the logger; send a few known pulses into the configured LEMO.
3. Inspect `<name>_triggers.csv` — capture a sample packet's fields and paste them back so
   we can lock the column schema and the tick→time conversion.
4. Verify clean `Ctrl-C` shutdown and auto-reconnect (pull/replug the cable).
5. Then add the second+ units and confirm independent threads/files.
