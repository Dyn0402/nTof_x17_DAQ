# Light event-logging for every standing DAQ process — 2026-07-29

Prepared 2026-07-29 for another model to implement. This is the write-up of the audit +
design discussed in chat; implement against this doc rather than re-deriving it.

## STATUS: IMPLEMENTED 2026-07-29 (all three tiers)

Code is in; **no watcher was restarted** — a beam run was live — so every new log file
starts empty and fills on the next natural restart of each process. See "What still
needs doing" at the bottom.

Corrections to this plan, found while implementing:

* **The line format was NOT already consistent**, so "byte-for-byte identical to the
  existing three" was not achievable as written. `qa_watcher`/`pedestal_watcher` use
  `{event:<16}` with a hardcoded 13-char source field; `flask_app/app.py` uses
  `{event:<14} | {source:<12}` — and the snippet in this doc copied app.py's widths.
  The shared helper uses **`{event:<16} | {source:<12}`**, which reproduces the watcher
  logs byte-for-byte (verified by field-width diff against `logs/qa_watcher.log`), and
  the checklist's "grep-diff against qa_watcher.log" is the argument that settles it.
  The three existing call sites were left alone — retrofitting a working watcher for
  cosmetics is risk without benefit.
* **`log_event` lands in two namespaces via `from common_functions import *`**
  (`daq_control.py`, `dream_daq_control.py`). No shadowing today, and those two get the
  function for free. `flask_app/app.py` keeps its own same-named, differently-signatured
  `log_event` — it does not import `common_functions`.
* **Details are flattened to one line.** A traceback as a `**details` value would
  otherwise break the one-line-per-event convention the whole grep habit rests on;
  newlines become a literal `\n`.
* **"Wrap `main()` in try/except" does not work for `hv_control` or
  `dream_daq_control`.** Both have an internal `while True: try/except` designed never
  to exit, so an outer wrapper can only ever catch a `KeyboardInterrupt`. Their durable
  crash record goes inside the existing handler instead.
* **`hv_control`'s restart loop has no `sleep`.** A permanently-failing `Server()` (port
  already bound) spins at full speed, so an unconditional `_log()` there would rebuild
  the 83 MB fossil this exercise exists to prevent. It — and the per-channel monitor
  read failure, which a dead crate trips on every channel every poll — go through a
  60 s per-key throttle that reports `suppressed_since_last=N` on the next line through.
  `beam_watcher`'s NXCALS/Kerberos failures use the same idea at 900 s (a Kerberos
  outage can run for days). Elsewhere, noise is avoided by logging **transitions only**
  (gas/he3 bus up-down, stream1 fault state, backup's Kerberos OK).
* **Flask's start line goes at end-of-module-import, not in `if __name__ == "__main__"`.**
  `flask_app/start_flask.sh` runs `flask run`, which imports the module and never
  executes `__main__` — a start line there would never be written in production. It
  reuses the existing `log_event`/`daq_events.log` rather than opening a new file: no
  new machinery in the process that serves every button, and `daq_events.log` is
  already that process's own log.
* **`dream_daq_control`'s file is `logs/dream_daq_control.log`, not `dream_daq.log`** —
  that name already belongs to the per-run `FileHandler` inside each run's data dir.
* **The `mode_watcher` docstring's `nohup ... > logs/mode_watcher.log`** was worse than
  a dead comment: it would have interleaved full stdout into the new event log. Repointed
  to `logs/mode_watcher.stdout.log`.
* Housekeeping renames were verified safe first — nothing in the repo (`.py`, `.sh`,
  templates, `daq_status.py`, `monitor.py`) reads either fossil.

### Rollout checklist (state as of 2026-07-29 11:35)

A process only picks this up when it **restarts** — every one below was already running
on the old code when the edit landed. `logs/<name>.log` staying absent is expected until
then, and is not a fault.

Verify with: `tail -1 logs/<name>.log` → a `START` line dated after the restart.

**✅ Upgraded + verified end-to-end**

| Process | Log | Evidence |
|---|---|---|
| `flask_server` | `logs/daq_events.log` | restarted 11:29:31 on the new code; real `START` line present. ⚠ Written by the pre-11:33 version — restart again to pick up the import-gate fix (next line will carry `launched_by=flask-cli`) |

**⏳ Upgraded, awaiting a restart to verify — safe to restart any time**

These own no hardware in the run path, or hold state in hardware across a restart.

| Process | Log | Restart |
|---|---|---|
| `stats_page_watcher` | `logs/stats_page_watcher.log` | `tmux kill-session -t stats_page_watcher && bash_scripts/start_servers.sh` |
| `system_stats_watcher` | `logs/system_stats_watcher.log` | same pattern |
| `stream1_watcher` | `logs/stream1_watcher.log` | same pattern |
| `he3_pressure_watcher` | `logs/he3_pressure_watcher.log` | same pattern (read-only GPIB) |
| `gas_watcher` | `logs/gas_watcher.log` | same pattern — does NOT zero setpoints on exit, gas keeps flowing |
| `space_watcher` | `logs/space_watcher.log` | same pattern |
| `beam_watcher` | `logs/beam_watcher.log` | same pattern; ~1 min Spark spin-up gap, `beam_on: null` during it means UNKNOWN and every consumer treats that as "do nothing" |
| `backup_watcher` | `logs/backup_watcher.log` | same pattern; an in-flight xrdcp is retried next poll |
| `processor_watcher` | `logs/processor_watcher.log` | same pattern; a killed decode re-runs next pass |
| `mode_watcher` | `logs/mode_watcher.log` | GUI Run Mode card → Stop, then Start. The disarm flag is a file and survives |

`start_servers.sh` leaves any session that is already up alone, so re-running it only
fills the gap left by the `kill-session`.

**⛔ Upgraded, but do NOT restart during a run — wait for a boundary**

| Process | Log | Why |
|---|---|---|
| `daq_control` | `logs/daq_control.log` | relaunched per run by `start_run.sh`; **verifies itself on the next run start**, no action needed |
| `hv_control` | `logs/hv_control.log` | holds the CAEN session and serves daq_control on port 1100 — killing it mid-run breaks the run |
| `dream_daq` | `logs/dream_daq_control.log` | the DAQ server itself |

**🚫 Instrumented but now off the live path**

| Process | Log | Why |
|---|---|---|
| `n1081b/timetag_watcher_controller.py` | `logs/n1081b_timetag_watcher.log` | instrumented, but **retired the same day** by commit `9ba3244`: the M5 stream was re-enabled 2026-07-29 pointing at `n1081b/tt_stream_supervisor.py --section C` instead (the rotation watcher is what wedged .244; the supervisor is what ran clean for 6 h on 07-18). Its lines only appear if that path is ever run again |

**⚠ Gap opened after this plan was written**

`n1081b/tt_stream_supervisor.py` is now a standing process — it runs 24/7 in the
`n1081b_timetag_watcher` tmux session — and it is **not instrumented**. This plan's
audit predates the repoint, so the supervisor never appears in the table above. It is
the one board-touching standing process left with no durable log, and per
`n1081b/CLAUDE.md` nothing in its board path should be edited casually. Worth a small
follow-up pass: `START` / `STOP` / segment-boundary restore outcome / alarm, at the
points where it already prints.

**Not touched** (already had working event logs): `qa_watcher`, `pedestal_watcher`.

⚠ Unrelated finding while checking this: **`qa_watcher` has been down since
2026-07-23 11:36** — no tmux session, no process. Its last line is a `QA_LAUNCH` at
`mem_pct=80.7%`, i.e. right at `memory_kill_pct`. Nothing restarted it in 6 days.

## Why

Today's trigger: `processor_watcher` looked like it had been down for weeks, because
`logs/processor_watcher.log` last wrote on Jul 4. That file is a fossil from before the
tmux launcher existed (`start_tmux.sh`, added 2026-07-22) — it never gets written to
anymore, because the current launch mechanism runs the watcher's command inside a tmux
pane and nothing redirects that pane's stdout to a file. The real outage (confirmed from
`combined_hits_root` mtimes on disk) was ~11 hours, not weeks — but finding that out took
reverse-engineering file timestamps across `/mnt/data/x17/beam_july/runs/`, because there
was no log to just read.

**`logs/backup_watcher.log` is the same fossil pattern** (83 MB, dead since Jul 7) — this
has already bitten twice.

## Current state (audit)

17 processes. Only 2 have real, currently-working event logs. Everything else lives only
in a tmux pane's scrollback: capped (`history-limit`, 500–20000 lines depending on the
session — see `start_servers.sh`), and gone entirely on session kill or reboot.

| Process | Launched by | Logging today |
|---|---|---|
| `qa_watcher.py` | `start_servers.sh` | ✅ `logs/qa_watcher.log` — `_log()` at line 51, START/QA_LAUNCH/QA_DONE/QA_KILLED |
| `pedestal_watcher.py` | GUI (`/start_processor`-style button) | ✅ `logs/pedestal_watcher.log` — same `_log()` pattern |
| `space_watcher.py` | `start_servers.sh` | ⚠ partial — the poll loop itself logs nothing; deletions it triggers go through `space_manager.py`'s `DELETE_LOG` → `logs/space_manager.log` |
| `dream_daq_control.py` | `start_servers.sh` (`dream_daq` session) | ⚠ partial — `logging.basicConfig` has only a `StreamHandler` (console/tmux, ephemeral); `common_functions.setup_logging()` additionally attaches a per-run `FileHandler` to `<data_out_dir>/dream_daq.log`, i.e. inside that run's own data folder — good for per-run debugging, useless for "is the watcher itself alive" |
| `flask_app/app.py` (`flask_server`) | `start_servers.sh` | ⚠ partial — `log_event()` at app.py:123 writes `logs/daq_events.log`, but that's a GUI-button audit trail (`STOP_RUN`, `GAS_SET`, ...), not Flask-process lifecycle/crash logging |
| `hv_control.py` | `start_servers.sh` | ❌ print-only. HV trip/deviation alerts go to Telegram (see `send_telegram` calls) but nowhere durable; a failed Telegram send (`HV alert {method} failed`) is silently swallowed |
| `gas_watcher.py` → `gas_mixer_control/flow_controller.py` | `start_servers.sh` | ❌ print-only (the per-day CSV is flow **data**, not an event/error log) |
| `he3_pressure_watcher.py` → `he3_pressure_reader/he3_pressure_controller.py` | `start_servers.sh` | ❌ print-only |
| `system_stats_watcher.py` → `system_monitor/system_stats_controller.py` | `start_servers.sh` | ❌ print-only |
| `beam_watcher.py` → `beam_monitor/beam_intensity_controller.py` | `start_servers.sh` | ❌ print-only |
| `stream1_watcher.py` → `stream1_monitor/stream1_size_controller.py` | `start_servers.sh` | ❌ print-only |
| `stats_page_watcher.py` → `stats_page/stats_collector.py` | `start_servers.sh` | ❌ print-only (to stderr) |
| `backup_watcher.py` | `start_servers.sh` | ❌ print-only. `logs/backup_watcher.log` is the **83 MB fossil**, dead since Jul 7 |
| `processor_watcher.py` | `start_servers.sh` | ❌ print-only. `logs/processor_watcher.log` is the **fossil that triggered this doc**, dead since Jul 4 |
| `mode_watcher.py` | GUI (`flask_app/app.py:3386`, tmux new-session) | ❌ print-only. (Its own top-of-file comment mentions `nohup ... > logs/mode_watcher.log`, but that's not how it's actually launched today — dead comment, not a real path) |
| `n1081b_timetag_watcher.py` → `n1081b/timetag_watcher_controller.py` | GUI, disabled by default in `start_servers.sh` | ❌ print-only |
| `daq_control.py` | per-run, via `bash_scripts/start_run.sh`, inside the `daq_control` tmux pane | ❌ print-only. This is the orchestrator that decides pre-flight pass/fail, n1081b scan-control verify, refuse-to-start — exactly the kind of decision that's cost time before (see memory: "daq_control silent on RunCtrl fail") |

Not in scope (already fine): `backup_progress` (tmux session dedicated to tailing backup
progress, already redirected to `logs/backup_progress.log` by
`bash_scripts/backup_progress_log.sh`); `claude_daq` (interactive session, not a watcher).

## Design

### One shared helper, generalizing the pattern that already exists three times

`qa_watcher.py` (`_log`, line 51), `pedestal_watcher.py` (`_log`, line ~44), and
`flask_app/app.py` (`log_event`, line 123) independently reimplement the same six lines:
timestamped, pipe-delimited, one line per event, appended to a per-process file under
`logs/`, wrapped in try/except so a logging failure can never take the process down.
Extract it once into `common_functions.py` (which already holds `setup_logging` /
`teardown_logging` — a different, heavier mechanism for per-run `logging.FileHandler`
attachment; this is a separate, lighter function, not a replacement):

```python
# common_functions.py

def log_event(log_path, event, source, **details):
    """Append one line to a lightweight event log. Never raises — a logging
    failure must not take down the process it's instrumenting."""
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        detail_str = ' | '.join(f'{k}={v}' for k, v in details.items())
        line = f"{ts} | {event:<14} | {source:<12} | {detail_str}\n"
        with open(log_path, 'a') as f:
            f.write(line)
    except Exception as e:
        print(f"Warning: could not write to {log_path}: {e}")
```

Keep the line format byte-for-byte identical to the existing three (`ts | event | source |
k=v | k=v`) so grep habits and any future log-parsing tooling work across every process
without a special case.

Each watcher then adds, near its existing imports:

```python
from pathlib import Path
from common_functions import log_event

_LOG_FILE = Path(__file__).parent / 'logs' / '<watcher_name>.log'

def _log(event, **details):
    log_event(str(_LOG_FILE), event, '<watcher_name>', **details)
```

For the `*_controller.py` modules behind the thin `*_watcher.py` entrypoints (gas, he3,
system_stats, beam, stream1, n1081b timetag), put `_log`/`_LOG_FILE` in the controller
module itself (that's where `run_blocking()` and the existing `print(...)` calls already
live), not in the thin wrapper script.

### What "light" means — do not mirror stdout

The 83 MB `backup_watcher.log` fossil got that big because it used to mirror full stdout.
Don't repeat that. Two tiers per process:

**Baseline — every process, ~10 min each:**
- `START` — one line, key config values (mirror what `qa_watcher.py:114` already does)
- `STOP` — clean-exit path, if one exists (signal handler / end of `run_blocking`)
- `CRASH` — wrap the body of `main()` (or `run_blocking()`) in `try/except Exception`,
  log the exception **and** `traceback.format_exc()` as a detail, then re-raise / let the
  process still exit non-zero. Do not swallow it — the tmux pane must keep showing the
  live traceback to anyone watching; the log is a second, durable copy, not a replacement.

**Domain events — reuse what's already printed, just also log it:**

| process | events worth persisting (already exist as `print()` calls — find and dual-write them) |
|---|---|
| `hv_control.py` | HV trip / sustained-deviation alert fired; Telegram send failed (currently silently swallowed at the `HV alert {method} failed` print) |
| `backup_watcher.py` | xrdcp wedge + retry outcome (memory: 5-8% of calls wedge, retry clears ~100% — worth a count over time) |
| `processor_watcher.py` | `DecodeTimeout`/HANG, TRUNCATED re-decode, incomplete-FEU warning (`only {n}/{expected} FEUs after {waited} min`) — these already print, just also `_log()` them |
| `daq_control.py` | pre-flight FEU crate failure, n1081b scan-control refuse-to-start / verify-fail, stop-run requested |
| `mode_watcher.py` | beam↔cosmics mode transition (which direction, why) |
| `gas_watcher.py` | setpoint applied, zero, FLOW-BUS read/write error |
| `he3_pressure_watcher.py` | read error |
| `beam_watcher.py` | NXCALS/Spark auth error and recovery (Kerberos ticket expiry is a recurring failure mode per memory) |
| `stream1_watcher.py` | fault detected (the size-drop classifier already prints this) |
| `n1081b_timetag_watcher.py` | board alarm, restore-on-exit outcome |
| `stats_page_watcher.py` | publish failure |
| `system_stats_watcher.py` | none needed beyond baseline — pure logger, low risk |
| `space_watcher.py` | none needed beyond baseline — deletions already land in `space_manager.log` |
| `flask_app/app.py` | process-level START only; button-click auditing already covered by existing `log_event` |
| `dream_daq_control.py` | process-level START/CRASH only; per-run detail already covered by the per-run `dream_daq.log` |

### Rollout order

1. **Now** — `processor_watcher.py`, `backup_watcher.py` (both have already caused a
   real "is this actually dead?" investigation), `hv_control.py` (safety-adjacent),
   `daq_control.py` (known silent-failure history per memory)
2. **Next** — `gas_watcher`, `he3_pressure_watcher`, `mode_watcher`, `beam_watcher`,
   `n1081b_timetag_watcher` (touches a wedge-prone board — logging changes only add a
   `_log()` call, must not touch the board-session logic itself; see `n1081b/CLAUDE.md`
   before editing anything under `n1081b/`)
3. **Low priority** — `system_stats_watcher`, `stats_page_watcher`, `stream1_watcher`
   (already partial via `space_manager.log`, `daq_events.log`, per-run `dream_daq.log`)

### Housekeeping — do this before wiring up new logging to the same filenames

Rename or move aside the two fossils so the new writes don't inherit a history that looks
continuous but isn't (exactly the confusion that had to be reverse-engineered today):

```bash
mv logs/processor_watcher.log logs/processor_watcher.log.stale_pre_tmux_20260704
mv logs/backup_watcher.log    logs/backup_watcher.log.stale_pre_tmux_20260707
```

### Validation checklist per process

- [ ] Restart the watcher (GUI button or `tmux kill-session -t <name> && bash_scripts/start_servers.sh` for the ones it manages) and confirm a `START` line appears in `logs/<name>.log` within a few seconds
- [ ] Confirm the log file is append-mode across restarts (don't truncate on open)
- [ ] Force or find a natural error path once and confirm a `CRASH` or domain-event line appears with a useful, specific message (not just "Exception")
- [ ] Confirm the log write failing (e.g. `logs/` briefly unwritable) does not crash the watcher — the `try/except` in `log_event` is the thing under test here
- [ ] Grep-diff the new log's line format against `logs/qa_watcher.log` to confirm the convention actually matches

## Explicitly out of scope for this pass

Wiring these events into `flask_app/monitor.py`'s Telegram rules. That's a complementary,
separate layer — a log tells you *why/when* after the fact; a monitor rule tells you *now*.
`monitor.py` already has `rule_*_dead` checks for `dream_daq`, `daq_control`, `gas_watcher`,
`beam_watcher`, `stream1_watcher`, `backup_watcher`, `processor_watcher`, `qa_watcher`,
`space_watcher` — missing `he3_pressure_watcher`, `system_stats_watcher`,
`stats_page_watcher`, `mode_watcher`, `hv_control`, `n1081b_timetag_watcher`,
`flask_server` itself, `pedestal_watcher`. Worth its own pass, but not this one.
