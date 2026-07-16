# Scintillator HV scan

Standalone HV control + logging for a scintillator HV scan, run **in parallel
with the DREAM DAQ**. It opens its own CAEN session alongside the DAQ's
`hv_control.py` (supported by the mainframe) and touches **only** the channels
you list in `scint_hv_config.py`.

## Why this is safe to run next to the DAQ
- The CAEN mainframe allows multiple simultaneous login sessions; the repo
  already relies on this (`emergency_hv_off.py` opens a second session).
- This tool uses `CAENHVController(..., keepalive_s=10, auto_reconnect=True)`,
  the same resilient session model as `hv_control.py`, so it survives the
  ~15 s idle session drop.
- **The one hard rule:** the scint channels must be *different* from the ones
  the DAQ controls. Startup cross-checks `SCINT_CHANNELS` against
  `run_config_beam.py` and **refuses to run on any overlap**.
- Beam intensity is read from `config/beam_state.json` (published by
  `beam_watcher.py`) — read-only, no crate involvement.

## Setup
Edit `scint_hv_config.py`:
1. `SCINT_CHANNELS` — the scintillator `(slot, channel)` list (required).
2. `SCAN_VOLTAGES` / `PROTONS_PER_STEP_E10` — only if you use the auto scanner.
3. Optional: `I0SET_UA`, `TRIP_S`, `MONITOR_INTERVAL_S`.

## Run (from the repo root, in the DAQ venv)
Passive logging only — never moves a voltage:
```
python scintillator_hv/scint_hv_logger.py [label]
```

Active scan — steps voltage up as protons accumulate:
```
python scintillator_hv/scint_hv_scan.py [label]
```

`Ctrl-C` to stop. The scanner powers its channels **off** on exit. **Do not
SIGKILL** — let Ctrl-C close the CAEN session cleanly.

## Output
`~/beam_july/scint_hv_scan/<timestamp>_<label>/hv_beam_monitor.csv` — one row
per interval: per-channel power / vmon / imon, the current scan step, and the
beam intensity (on/off, last pulse e10, 10-min protons, cumulative protons at
the step).
