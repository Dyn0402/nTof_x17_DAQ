# N1081B Module-5 Watcher (.244)

> ## ⚠ WHAT ACTUALLY RUNS IS THE SUPERVISOR, NOT THIS WATCHER (2026-07-29)
>
> The standing Module-5 logger is **`n1081b/tt_stream_supervisor.py`** — chained 6 h
> **continuous single-section** streams of **section C**. It is what
> `start_servers.sh` and the GUI Start/Stop buttons launch, in the tmux session still
> named `n1081b_timetag_watcher` (that name is what `poll_modules` keys its .244 skip
> off — do not rename it). See **§The standing configuration** at the bottom.
>
> **`n1081b_timetag_watcher.py` (v3, documented below) has never been run against
> hardware and is not what you want.** Its rotation still costs a TT start/stop per
> section per cycle, and its un-tapped gaps are real data loss. It is kept only for
> `--restore`, which is still the right way to put .244 back to counters by hand:
>
> ```
> .venv/bin/python n1081b_timetag_watcher.py --restore
> ```

## v3 design (rate-gated TT + counter totals) — reference only, never hardware-run

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
  GUI state keys). **NOT yet run against hardware** — and never was: the
  continuous single-section stream below superseded it two days later.

---

# The standing configuration (2026-07-29)

**`n1081b/tt_stream_supervisor.py --section C`**, in the tmux session
`n1081b_timetag_watcher`, auto-started by `start_servers.sh`.

## Why this mode and not a rotation

Every wedge in this board's history came from **command churn**, not from streaming:
v1 did ~11 TT start/stop per second (wedged 07-15), v2 did 5 reconnect+re-arm cycles
onto a healthy board (wedged 07-17), and a Ctrl-C landing inside `rate_scan_2d`'s raw
TT phase closed a socket dirty (wedged 07-22). The continuous single-section stream is
the opposite of all three: **one session and exactly three stream commands per 6 h
segment**, zero reconnects, and a clean `stop_tt_data` + verified restore at the end.

It is also the only mode that produces a *faithful* record — a rotation's un-tapped
gaps are real losses, since the backlog dump cannot be relied on (§The physics
constraint).

The evidence it works: **2026-07-18 03:38–09:38, a full 6 h segment on C, 14,235,386
edges, 678,141 packets, zero recorded gaps, max packet gap 0.4 s, restored clean.**

## Why section C

| | C (sector coincidences, from M3) | D (Singles/Doubles/master trigger) |
|---|---|---|
| information | the four `sectorN = wallN ∧ liqN` — reconstructs the trigger logic offline | collapsed trigger outputs |
| rate 2026-07-29 | 48–76 Hz per channel | up to 235 Hz on Singles |
| ceiling headroom | ~3–4× | none — at the bracket |

The TT ceiling is **per channel (~220 Hz streams / ~700 Hz silent)**, not aggregate
(`HANDOFF_2026-07-17_tt_rate_ceiling.md` + the 07-18 T1 result). D sat at the ceiling
and went silent for 15 min in the T1 test; C streamed complete in the same conditions.
Walls (A, ~3.7 kHz) and liq (B, ~1.5 kHz) are far over it and are counter-logged only.

## What it writes

| what | where |
|---|---|
| per-edge timestamps (the scientific record) | `~/beam_july/slow_control/n1081b_timetag/stream/<label>/edges.csv.gz` — `host_unix, channel, t_board_ns`; **gzipped** at segment end, ~100 MB/day; pruned after 21 days |
| per-segment report | same dir, `stats.json` (pre/post counter rates for **all four** sections, gaps, restore verdict) + `counters.csv` |
| 10 s binned rates (what the GUI plots) | `~/beam_july/slow_control/n1081b_timetag/n1081b_tt_rates_%Y-%m-%d.csv` — `host_unix, section, channel, edges, hz` |
| health for the Module-5 card | `config/n1081b_timetag_state.json` (`/n1081b/status`) |
| durable process log | `logs/n1081b_tt_stream.log` (rotates at 20 MB) |

`t_board_ns` is in **1 ns** ticks (measured 07-18; older docs saying 10 ns are wrong).
Sort globally before use — a packet interleaves per-channel blocks.

## Failure policy

- Reconnect budget **zero**. Any socket/login/call error ends the segment.
- Silent start (0 edges for 90 s while the pre-baseline says the input is live) ⇒
  stall. Strike 1 = one clean retry; strike 2 = **rest 50 min, then ONE
  `tt_probe_v2`** — the 07-18 stall healed with exactly this. 3 failed probe cycles ⇒
  Telegram + stop.
- Two consecutive harness alarms ⇒ Telegram + stop.
- Disk guard: no segment is started below 25 GB free.

### ⚠ Known failure mode: the cold-boot false wedge (open, recurs every reboot)

A **host reboot** autostarts this supervisor ~14 s after boot, before the DREAM link is
up. The first connect to .244 returns `OSError(113, 'No route to host')`, which
`n1081b_session` classifies as `BoardWedgedError` and answers with a **6 h quarantine**.
The one scheduled retry 10 min later is then blocked *by that same marker*, which counts
as harness alarm #2 ⇒ **stop**. So one transient network error at boot reliably kills TT
logging for good, with no self-recovery, and `.244` counter telemetry with it (§Exclusivity
— `poll_modules` skips on the *session* name, not on whether the supervisor is alive).

**The board is fine in this case.** Tell it apart by the previous segment's `stats.json`:
`"finished": "signal"` + `"restored": true` = the last session ended cleanly with a
verified restore, the opposite of a wedge. `"post_rates_all_hz": null` on such a segment is
normal (the post-baseline is skipped when stopping), **not** evidence the restore failed —
and the supervisor's `WARNING: segment did not verify the restore to counters` is
misleading here.

Recovery runbook, evidence and the not-yet-applied fix:
**`HANDOFF_2026-07-30_tt_reboot_race.md`**.

## Operating it

```
# start / stop (also the GUI buttons on the Module-5 tab)
bash_scripts/start_tmux.sh n1081b_timetag_watcher \
    "$PWD/.venv/bin/python $PWD/n1081b/tt_stream_supervisor.py --section C" 5000
touch config/tt_stream_supervisor.stop     # clean stop; restores .244, then exits

tail -f logs/n1081b_tt_stream.log
```

**Never SIGKILL it, and do not `tmux kill-session` it while a stream is open** — both
are dirty disconnects. Use the stop-file (or the GUI Stop button, which writes the
stop-file, waits, and only then reaps the session). If it *was* killed hard, .244 is
left in `wire`/`time_tag`: `python n1081b_timetag_watcher.py --restore` fixes it, and
the next segment's `ensure_counter` would too.

**Exclusivity:** the session name `n1081b_timetag_watcher` is what `poll_modules`
matches to skip .244 — **do not rename it**. The scan watcher never touches .244.
While the stream runs, .244 is absent from the per-sub-run `n1081b_config.json`
snapshots; the 6-hourly `stats.json` baselines are the wall-rate record instead.
