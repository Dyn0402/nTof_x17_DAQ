# N1081B Module-5 Watcher (.244) — v3: rate-gated TT + counter totals

> **History:** v1 (fast round-robin) **wedged .244 on 2026-07-15** — ~11 TT start/stop
> commands/s stalled the TT engine, then the whole command processor; physical reboot
> needed (`HANDOFF_2026-07-15_timetag_watcher_board_wedge.md`). v2 (long-dwell rotation
> over all four sections) was qualified on 07-17 but died at 02:35 the same night: C/D
> beam-off rates spiked over the **TT rate ceiling** → taps went silent → 5 reconnect+
> re-arm cycles onto a *healthy* board → full-board wedge from the reconnect churn.
> v3 (2026-07-17 evening) is the redesign the rate-ceiling handoff
> (`HANDOFF_2026-07-17_tt_rate_ceiling.md`) requires: rate gate + counter logging +
> **zero reconnects**. **Auto-start in `start_servers.sh` stays commented out until a
> multi-hour soak passes** (see §Rollout status).

## The physics constraint (measured 2026-07-17)

A section emits **zero TT tags whenever its input rate is above a live ceiling**
(bracketed: ~50 Hz streams, ~800 Hz silent, at stream-start time) and streams fine
below it. Counters are pure FPGA and count at any rate. Consequences:

- **Walls (A, ~3–6 kHz beam-off) and liq (B, ~19–27 kHz) cannot be TT-streamed** —
  they are logged as **counter totals** instead.
- **C and D (trigger taps, tens of Hz) stream fine** — except when their own rates
  spike over the ceiling (happens minute-to-minute beam-off), during which they are
  simply silent. Silence on an over-ceiling section is *expected*, not an error.
- **Backlog dumps cannot be assumed to cover un-tapped gaps** (07-14 measured a real
  ~30 s backlog; 07-17 measured essentially none; the regime selector is unknown).
  Edges of an un-tapped section may be lost — this is telemetry for offline timestamp
  *matching*, not a complete edge record. **A faithful per-trigger record (DREAM
  event ↔ trigger matching) needs continuous single-section streaming instead — see
  `TT_STREAM_QUALIFY_PLAN_2026-07-17.md` + `tt_stream_qualify.py`.**

## What v3 does (one gate cycle, default every 5 min)

1. **Counters first.** Fresh `board_session`; all managed sections (default ABCD)
   restored to counter mode (streamed sections auto-revert to `wire` when their stream
   socket drops); counters read twice ~4 s apart; one row per section appended to the
   **counters CSV**: absolute counts `c0–c3`, delta since last cycle `d0–d3`
   (reset-tolerant), aggregate Hz. This is the A/B running-total record and doubles as
   the rate measurement for the gate.
2. **Rate gate.** TT candidates (default CD) with aggregate rate ≤ `gate_hz`
   (default 40 Hz, under the ~50 Hz proven-streaming point) are armed to Time-Tag;
   the rest stay counters this cycle and are re-tested next cycle.
3. **Stream.** Eligible sections visited round-robin, one stream held open `dwell_s`
   (default 12 s) at a time, edges deduped by `(section, channel, t_board_ns)` and
   appended to the **edges CSV**. All config via `s.call` strictly before the first
   raw send; the raw phase is one-way (same hygiene as v2/tt_probe_v2).
4. **Clean close + idle.** Session closes at the end of the period; the board is
   untouched until the next cycle (state-file heartbeat continues).

### Failure policy: reconnect budget ZERO

**Any** error — login, counter read, arming, or a mid-stream socket error — publishes
an `alarm` in the state file and **stops the watcher**. There are no retries and no
reconnects anywhere (reconnect churn is what actually wedged .244 on 07-17; recv
*timeouts* during a dwell are idle waits, not errors). `BoardBusy` / `BoardQuarantined`
/ `BoardWedged` likewise stop it. The per-cycle fresh session is the only reconnection.

### Health check: silent-despite-gate

v2's beam-on/zero-edges check is gone (silence is now often expected). Instead: a
section that **passes the gate at ≥ 5 Hz yet streams zero edges** takes a strike;
**3 consecutive strikes** (≈15 min) contradict the ceiling model → alarm + stop.
Probe gently with `tt_probe_v2.py` before restarting.

## Files / knobs

- **Controller:** `n1081b/timetag_watcher_controller.py` (`N1081BTimeTagController`)
- **Entry point:** `n1081b_timetag_watcher.py`; flags: `--restore`, `--duration S`,
  `--dwell S`, `--sections ABCD` (counter-managed), `--tt-sections CD` (stream
  candidates), `--gate-hz 40`, `--gate-period 300`
- **Launched by:** `start_servers.sh` (tmux `n1081b_timetag_watcher`, commented out)
  or the GUI (`/start_n1081b_timetag_watcher` / `/stop_...`)
- **Edges CSV:** `~/beam_july/slow_control/n1081b_timetag/n1081b_timetag_%Y-%m-%d.csv`
  — `host_unix, section, channel, t_board_ns` (unchanged from v2; feeds `/n1081b/history`)
- **Counters CSV (new):** same dir, `n1081b_counters_%Y-%m-%d.csv` —
  `host_unix, section, c0..c3, d0..d3, agg_hz`. Running totals = sum of `d*` (deltas
  are reset-tolerant: a counter that shrank is treated as freshly reset). C/D pause
  counting while they stream; A/B are continuous.
- **State file:** `config/n1081b_timetag_state.json` (served by `/n1081b/status`).
  `rate_hz` per section is now the **counter-measured input rate** (so A/B finally
  show numbers on the card); TT edge rates are in `tt_rate_hz`; per-section gate
  status in `tt_status`; counter totals in `total_today`, TT edge counts in
  `edges_today`.
- **Tunables:** `config/n1081b_timetag_config.json` — `{"dwell_s": 5–60,
  "gate_hz": 5–300, "gate_period_s": 120–1800}`, re-read every cycle.

**Exclusivity:** unchanged from v2 — while the watcher streams, nothing else may
connect to .244 (`send_data` is broadcast to every client). Guards: the session
flock, and `poll_modules.py` auto-skips .244 whenever the watcher's tmux session is
alive. On clean exit (SIGINT/SIGTERM/SIGHUP incl. `tmux kill-session`) it restores
.244 to counters and verifies readback; after a SIGKILL run
`python n1081b_timetag_watcher.py --restore`.

## Matching to DREAM after the fact (unchanged)

1. **Coarse board→wall anchor:** fit `wall ≈ host0 + (t_board − board0)/1e9` on the
   minimum-offset `(host_unix, t_board_ns)` pairs (newest edge of a tap ≈ "now").
2. **Fine alignment:** cross-correlate the beam-spill rate structure (walls vs DREAM
   event times), best lag — as with the TIMBER beam matching. Restrict to beam-on
   stretches via `beam_state.json` / the beam-intensity CSVs.

Gotchas: sort by `t_board_ns` before histogramming (arrival order ≠ time order);
un-tapped gaps in C/D are real losses (no backlog assumption).

## Rollout status (update as stages pass)

- **2026-07-17 ~01:15:** v2 stage 1 (3 min, ABCD) passed mechanically; A/B silence
  later explained by the rate ceiling, not a wedge.
- **2026-07-17 ~02:35:** v2 overnight soak DIED — reconnect churn onto rate-spiked
  C/D wedged the board (see History above). v2 retired.
- **2026-07-17 evening:** v3 written; offline dry-run passed (fake-board harness:
  gate in/out, counter CSV deltas, dedup across cycles, silent-strike alarm,
  GUI state keys). **NOT yet run against hardware.** Next: stage 1 =
  `--duration 900` bounded run while someone is watching the Trigger tab's Board
  Access card; then a multi-hour soak; only then uncomment auto-start.
