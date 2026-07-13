# Per-sub-run N1081B config snapshot

Records the full readable N1081B trigger-module configuration once per DAQ
sub-run, so we can later reconstruct the exact as-built trigger state for any run.
Read-only, background, and fault-isolated — it can never disturb data-taking.

## What it does

For each sub-run, `daq_control.py` fires a **background daemon thread** that polls
the trigger modules (read-only: `connect → login → get_* → disconnect`) and writes:

```
<run_out_dir>/<sub_run_name>/n1081b_config.json
```

next to that sub-run's `run_config.json` and raw data. The JSON has a top-level
`polled_at` timestamp, the `label` (sub-run name), and a `boards` map — one entry
per module with `version`, `ethernet`, `clock`, `sections_function`,
`config_file_list` (saved on-board configs, for provenance), `logic_analyzer_trigger`,
and per-section (`A–D`) function / input / output / per-channel config. Any board
call that fails is captured under that board's `errors` map instead of raising.

This is the **complete active configuration + live function results** the SDK
exposes. Deliberately *not* captured (not config, or unsafe to call passively):
`get_logic_analyzer_data` / `get_time_tag_data` (live acquisition data — and
`get_time_tag_data` is a bare `recv()` that blocks until a broadcast packet),
`get_function_file_list` (blocks its `recv()` for non-LUT/pattern/ToF sections),
`get_search_device_status` (the locate-blink alarm).

## Measured cost (2026-07-12, all six boards)

- **~0.05 s per board**, 0 errors, repeatable.
- All six ≈ 0.3 s serial — trivial next to multi-minute sub-runs, and it runs in a
  **background thread** so it adds **~0** to the DAQ path (helper returns in ~0.01 s).
- ~14 KB/board on disk → ~85 KB (all six) per sub-run.

## Design choices

- **Background thread, spawned before `run_daq_controller()`.** That call blocks for
  the whole sub-run, so the poll is fired just before it and reads in parallel while
  the DAQ runs — capturing steady-state, not adding latency.
- **Never breaks the run.** The spawn is wrapped in a guarded helper
  (`_snapshot_n1081b`) that swallows any import/runtime error; the thread itself
  catches everything and only logs. A busy or unreachable board records an error
  entry and the run continues untouched.
- **Records the as-built trigger.** Trigger/mesh modulation is now applied
  in-process by `scan_control.py` (`N1081BScanControl`, called from daq_control)
  BEFORE this snapshot fires — and before the DAQ starts — so the snapshot always
  captures the config the sub-run actually ran with. This replaced the standalone
  `n1081b_scan_watcher.py` process (which switched config at sub-run boundaries and
  gated daq_control via `.pause_run`). The poll still **waits for `.pause_run` to
  clear** (bounded, 180 s) as a belt-and-suspenders guard — now that flag is only
  set by a manual pause or an apply-failure hold, never a routine scan swap. When a
  run needs no modulation, config is static and we just record what's live. (If the
  legacy watcher is ever run standalone, this still coexists — same `.pause_run`
  handshake.)
- **A ~2 s settle delay** before the read lets the config reach steady state.

## Scope

Scope is **all six trigger boards (`.240`–`.245`)**, controlled by the single list in
`n1081b/poll_modules.py`:

```python
POLL_IPS = [f"192.168.10.{n}" for n in (240, 241, 242, 243, 244, 245)]
```

**Drop `.244` from `POLL_IPS` if a `mod5_timetag_logger.py` run is planned
concurrently** — a streaming board broadcasts `send_data` to every websocket client
and would interleave/desync the reads (see the SDK-gotchas note).

## Files

- `n1081b/poll_modules.py` — the read-only poller. Importable
  (`poll_in_background`, `poll_to_file`) and runnable standalone:
  `.venv/bin/python n1081b/poll_modules.py out.json`.
- `daq_control.py` — `_snapshot_n1081b()` helper + the spawn call just before
  `run_daq_controller()`.

Related: `dump_module_info.py` (the original all-6 one-shot dump this reuses the
coverage of), `n1081b_scan_watcher.py` (the boundary config switcher this
coordinates with).
