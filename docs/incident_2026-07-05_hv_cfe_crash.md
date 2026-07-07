# Incident Report — HV CFE server crash hangs run_12 scan

**Date:** 2026-07-05
**Author:** Dylan Neff (with Claude Code)
**Severity:** Medium — one run lost (run_12), no hardware damage, no data corruption
**Status:** Resolved. Stack restarted, HV backend recovered, run_12 scrapped.

---

## Summary

During `run_12` (a synchronized anode HV scan), the CAEN HV mainframe's CFE
(front-end) server crashed mid-ramp. `hv_control` retried the failed read
indefinitely and `daq_control` blocked forever waiting for the HV to reach
setpoint, wedging the entire scan on subrun `dr800_A470_07`. The run sat hung
for ~2 h before it was noticed. The crate itself never lost network
connectivity — only the mainframe's front-end software died. Restarting the
run-control stack re-established the HV session cleanly.

---

## Timeline (2026-07-05, local time)

| Time | Event |
|------|-------|
| 19:49–19:51 | `run_12` launched — synchronized HV scan (`dr800_A540_00` stepping the anode voltage down, drift fixed at 800 V). |
| ~19:51–20:30 | Subruns `_00` through `_06` acquired normally. |
| 20:29:55 | `dr800_A480_06` finished cleanly (Dream DAQ: *"Subrun finished: dr800_A480_06"*). |
| 20:25 / 20:55 | Post-sub-run pause engaged, then resumed (`PAUSE_RUN` → `RESUME_RUN` in `daq_events.log`). |
| ~20:56 | Scan advanced to `dr800_A470_07`, sent HV setpoints, began ramping. **CFE server crashed here** — `hv_control` began reporting `Power read bad: CFE server down (0x5)`, all channels reading `-1.00 V`. |
| 20:56 → 23:09 | `daq_control` blocked at *"Waiting for HV to ramp..."*; `hv_control` looped on the failed read. Run hung (~2 h 15 m). Dream DAQ side idle and healthy throughout. |
| 23:09 | Weird state noticed; investigation started. |
| 23:12 | Run-control stack stopped and restarted; HV backend confirmed recovered; `run_12` scrapped. |

---

## Root cause

The CAEN HV crate at `128.141.177.244` runs a CFE (front-end) server that the
`CAENHVController` session talks to. That server process **crashed on the
mainframe** partway through the `_07` HV ramp. Error `0x5` ("CFE server down")
is a loss of the front-end session, not a network fault — the crate continued to
answer ICMP pings throughout.

**Why it hung the whole run (the real failure mode):** the run-control loop has
no timeout on HV ramp. In `hv_control.py`, `set_hvs()` / the ramp-wait logic
keeps polling `get_ch_vmon` / `get_ch_power` until the channels reach setpoint.
With the CFE server down, every read fails and the voltage never changes, so the
loop never exits and `daq_control` never advances or aborts. A transient crate
fault therefore escalates into an indefinite full-scan hang.

The pause/resume immediately before the crash is almost certainly coincidental —
the crash coincided with the `_07` ramp, which happened to be the first crate
interaction after resume. No evidence pause/resume caused it.

---

## Impact

- **`run_12` lost.** Subruns `_00`–`_06` acquired but the scan was incomplete
  and scrapped rather than salvaged.
- **No data on EOS** — backup had not reached run_12 (EOS held only run_1–10;
  live `backup_state.json` had zero run_12 entries).
- **No hardware damage.** HV channels remained powered at valid physics voltages
  the whole time (drift 800 V, anode ~540 V).
- **~2 h of beam time wasted** while the run sat hung unnoticed.

---

## Resolution

1. Stopped the wedged stack — killed the `daq_control`, `dream_daq`,
   `hv_control`, `flask_server` tmux sessions (also killing the hung run loop).
   No orphan processes remained.
2. Restarted via `start_servers.sh`; all four sessions came back listening
   (hv:1100, dream:1101, flask:5001, empty `daq_control` ready).
3. Verified HV backend recovery with a read-only `CAENHVController` connection
   test: `CONNECT OK`, drift slot 9 ch0 = 800 V (on), anode slot 5 ch1 =
   539.75 V (on). CFE server healthy again.
4. Cleared `run_12` (~11 GB) from all three locations it existed:
   - `/home/mx17/july_dream/dream_run/run_12`
   - `/mnt/data/x17/beam_july/runs/run_12`
   - `/mnt/data/x17/beam_july/analysis/run_12`
   Not present on EOS; no live state files referenced it.
5. Left `run_name = 'run_12'` in `run_config_beam.py` so the next run reuses the
   number. Watcher services (pedestal / qa / processor / backup) left untouched —
   independent and healthy.

---

## Follow-up / recommendation

**Add a ramp timeout + CFE-down abort to `hv_control` / `daq_control`.** The core
defect is that a dead crate hangs the run instead of failing it. Suggested:

- Bound the HV ramp wait with a wall-clock timeout; on expiry, abort the subrun
  and surface an error to `daq_control` rather than looping forever.
- Treat repeated `CFE server down (0x5)` reads as a hard error (fail fast) rather
  than a retryable transient.
- On such a failure during an overnight/unattended scan, stop the run cleanly and
  flag it, so a transient crate hiccup costs one subrun, not the whole night.

**Operational note:** if `CFE server down (0x5)` recurs, the crate's front-end
(not the network) is the thing to restart. `ping` will still succeed — it is not
a useful health check for this failure.

---

## Update — fix deployed (2026-07-05, later)

The follow-up recommendation above is now implemented, in two layers, and
hardware-tested against the crate (read-only, slots 5 & 9 only, no run active).

**1. Resilient CAEN session (`caen_hv_py` v2.0.0 + `hv_control.py`).**
`CAENHVController` now supports `keepalive_s` (a background ping that keeps the
session warm) and `auto_reconnect` (transparently re-logs-in + retries on a dead
handle). `hv_control` opens the session with `keepalive_s=10, auto_reconnect=True`
and its monitor thread tolerates `CAENHVError` (logs + NaN, no longer dies).
Hardware finding: the real idle-session drop is at **~75–90 s of no activity**,
not the ~15 s previously assumed — a bare session survived 30/45/60 s idle and
dropped only by 90 s, at which point `auto_reconnect` healed it transparently
(read returned the correct 800.0 V). `keepalive_s=10` pings well inside that
window, so the scan-boundary-pause drop should no longer occur; auto-reconnect
is the backstop if it does.

**2. Bounded ramp + clean stop (the real fix for THIS incident).**
`set_hvs` now bounds the ramp wait with a wall-clock timeout (`ramp_timeout_s`,
default 180 s) and catches `CAENHVError`. On a hard failure (CFE server crash →
reconnect exhausted, or voltage never reaching setpoint) it raises `HVRampError`;
`hv_control` then stops the monitor, creates the `.stop_run` flag so `daq_control`
ends the run cleanly at the next boundary, and replies without `'HV Set'` so the
sub-run is skipped and left un-marked (a `resume` re-runs it). A crate hiccup now
costs one sub-run + a clean, flagged stop — not an all-night hang. No
`daq_control` change was needed (it already breaks on `.stop_run`).

Deployed: `caen_hv_py` 2.0.0 installed in the nTof `.venv`; `hv_control.py`
updated and the `hv_control` tmux server restarted (idle, no session held until a
run starts). A one-off `connect()`-without-context-manager bug in v2 was found and
fixed while smoke-testing. Overnight `caen_watchdog` left off — the resilient code
is now the primary defence; re-enable it only as an unattended backstop.
