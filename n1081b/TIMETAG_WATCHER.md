# N1081B Time-Tag Watcher (Module 5 / .244) — v2

> **History:** v1 (persistent-FIFO round-robin, back-to-back ~1.6 s cycles) **wedged .244 on
> 2026-07-15** — ~11 TT start/stop commands/s for an hour stalled the TT engine and then the
> whole command processor; recovery took a **physical reboot** (2026-07-16). Incident:
> `HANDOFF_2026-07-15_timetag_watcher_board_wedge.md`. v2 (2026-07-17) is the redesign that
> handoff §4 required: gentle long-dwell cadence, session-hygiene gateway, beam-aware health
> check, periodic re-arm, stop-don't-hammer failure policy. **Do not re-enable auto-start in
> `start_servers.sh` until the multi-hour soak has passed** (see §Rollout status at the end).

Always-on telemetry watcher that reads the four scintillator-wall sections of N1081B
Module 5 (.244) as **per-edge timestamps** and appends them to a per-day CSV — the same
"sole-owner process + daily CSV + published state file" pattern as the gas / ³He / beam
watchers. The point is a continuous, independently-acquired timestamp stream of the walls
that can be matched to DREAM runs **after the fact** by aligning timestamps.

- **Controller:** `n1081b/timetag_watcher_controller.py` (`N1081BTimeTagController`)
- **Entry point:** `n1081b_timetag_watcher.py` (repo root); flags: `--restore` (recover .244
  to counters), `--duration S` (bounded soak run), `--dwell S`, `--sections ABCD`
- **Launched by:** `start_servers.sh` (tmux session `n1081b_timetag_watcher`, currently
  commented out) or the GUI Start/Stop buttons (`/start_n1081b_timetag_watcher`, `/stop_...`)
- **CSV:** `~/beam_july/slow_control/n1081b_timetag/n1081b_timetag_%Y-%m-%d.csv`
  (moved out of the repo per the 07-15 slow-control migration)
- **State file:** `config/n1081b_timetag_state.json` (served by `/n1081b/status`)
- **Rate history for the GUI:** `/n1081b/history` (bins edges into per-section Hz)
- **Tunables:** `config/n1081b_timetag_config.json` — `{"dwell_s": 5–60, "rearm_period_s":
  600–7200}`, re-read every rotation

## How v2 acquires (long-dwell rotation)

All four sections are armed to `FN_TIME_TAG` **once per session** (each buffers its own
edges concurrently — the persistent-FIFO fact from `TIMETAG_MULTISECTION_2026-07-13.md`).
The watcher then visits sections round-robin, but instead of v1's ~0.35 s taps it **holds one
section's stream open for `dwell_s` (default 12 s)**: `reset_channel` + `start_tt_data`
(dumps that section's buffered backlog) → drain live for the dwell → `stop_tt_data` → next
section. That is ~3 stream commands per 12 s (~0.25/s) versus v1's ~11/s — the cadence of
`mod5_timetag_logger.py --section cycle`, which has streamed at kHz for long stretches
without ever wedging. Dedup by `(section, channel, t_board_ns)` over a rolling board-clock
horizon (≥ 120 s) keeps every edge exactly once despite the backlog overlap.

**Completeness caveat (accepted trade-off):** a section is untapped for ~3 dwells (~40 s).
The board buffer holds ~4000+ tags/section, so at beam-on wall rates (~200 Hz/section) a hot
wall can overflow the buffer inside the gap, dropping that gap's **oldest** edges. This
telemetry exists for offline timestamp *matching* (rate structure + coincidences on live
windows), not as a complete edge record. Lower `dwell_s` (min 5 s) narrows the gap if
completeness on hot walls ever matters more than command-rate gentleness.

## Safety architecture (why this can run unattended)

1. **Session-hygiene gateway everywhere.** Config commands go through
   `n1081b_session.N1081BSession` (`s.call`): interprocess flock, quarantine gate, bounded
   connect/recv timeouts, pacing, breaker, guaranteed clean websocket Close. Raw sends are
   used *only* for the tap/untap stream commands, strictly **after** arming — from the first
   raw send the session is one-way (replies drained and ignored) and no `s.call` runs on it
   again; every re-arm builds a fresh session.
2. **Beam-aware health check.** If `beam_state.json` is fresh + beam-on and **zero new edges**
   arrive on any section for 5 min (exactly how the 07-15 wedge began — silently), the watcher
   does ONE clean re-arm. A second silent window ⇒ **stop + alarm** (`alarm` field in the
   state file, non-zero exit). Beam-off or a stale beam file can never trip it.
3. **Periodic re-arm.** Every `rearm_period_s` (default 30 min) the session is closed
   cleanly, rested ~5 s, and rebuilt, bounding board-side session state age.
4. **Stop, don't hammer.** `BoardBusyError` / `BoardQuarantinedError` / `BoardWedgedError` /
   login failure ⇒ publish alarm and exit. Stream-error reconnects are budgeted
   (4/rolling-hour); exceeding the budget stops the watcher. There are no unbounded retry
   loops anywhere.

**Exclusivity:** while the watcher streams, nothing else may connect to .244 (the board
broadcasts `send_data` to every client). Two independent guards: the session **flock** (any
`board_session` caller gets `BoardBusyError`), and `poll_modules.py` auto-skips .244 whenever
the watcher's tmux session is alive. On clean exit (SIGINT/SIGTERM/SIGHUP — including
`tmux kill-session`) the watcher restores .244 to counters and verifies the readback. If it
is SIGKILLed it cannot, and the board auto-reverts streaming sections to `wire` — run
`python n1081b_timetag_watcher.py --restore`.

## CSV schema

One row per edge (deduped, exactly once):

| column | meaning |
|---|---|
| `host_unix` | wall-clock (epoch s) when the packet carrying the edge arrived — **precise (~ms)** for edges captured live during a dwell; ≈ tap time for edges recovered from the backlog dump |
| `section` | `A`–`D` (attribution is by which section was tapped) |
| `channel` | panel 1–6 (= lemo+1) within that section |
| `t_board_ns` | **precise** free-running board clock in ns (10 ns steps, common to all sections, does NOT reset) |

## Matching to DREAM after the fact (unchanged from v1)

1. **Coarse board→wall anchor:** fit `wall ≈ host0 + (t_board − board0)/1e8` on the
   minimum-offset `(host_unix, t_board_ns)` pairs (newest edge of a tap ≈ "now").
2. **Fine alignment:** cross-correlate the beam-spill rate structure (walls vs DREAM event
   times), take the best lag — as with the TIMBER beam matching. Restrict to beam-on
   stretches via `beam_state.json` / the beam-intensity CSVs.

Gotchas: sort by `t_board_ns` before histogramming (arrival order ≠ time order); the
backlog-overflow caveat above applies to the hottest wall during intense spills.

## Per-section TT wedge (found 2026-07-17) — **OBSOLETE, see below**

> **SUPERSEDED 2026-07-17 midday:** the per-section TT wedge does not exist. Sections
> emit zero TT tags whenever their input rate is above a live ceiling (~50 Hz streams,
> ~800 Hz silent) and stream fine below it — proven by cable-swap on section A. Reboots
> never mattered. See `HANDOFF_2026-07-17_tt_rate_ceiling.md`; probe with
> `tt_probe_v2.py`, not `tt_section_probe.py`. The section below is kept for history.

.244 has a **per-section** failure mode on top of the whole-board wedge: a TT stream that
dies mid-operation leaves THAT section returning zero tags forever (counters unaffected);
only a reboot/power-cycle clears it, and counter→TT function cycling does **not**
(TIMETAG_MULTISECTION_2026-07-13.md §3). As of 2026-07-17 night: **A and B are TT-wedged**
(left over from the 07-15 kill; the 07-16 touchscreen reboot cleared only C/D), C streams
reliably, D streams but can miss the first tap after a re-arm (the backlog dump covers the
gap at the next tap). Probe any time with `n1081b/tt_section_probe.py` (gentle, single tap
per section, auto-restores counters). Until a power-cycle clears A/B, run the watcher with
`--sections CD` — D carries the trigger taps (Singles/Doubles/**PS anchor**), which is the
most useful stream for DREAM timestamp matching anyway; A (the walls) needs the power-cycle.

## Rollout status (update as stages pass)

- **2026-07-17 ~01:15:** stage 1 (3 min, ABCD) PASSED mechanically — connect/arm 1 s,
  3 rotations, 2262 edges on C+D, zero dupes, clean restore, board healthy after. A/B
  silent → diagnosed as pre-existing per-section TT wedges (see above), not a v2 defect.
- **2026-07-17 ~01:27:** stage 2 (25 min, `--sections CD`) started; overnight soak next.
  Auto-start in `start_servers.sh` stays **commented out** until the soak passes and the
  board verifies healthy afterwards.
