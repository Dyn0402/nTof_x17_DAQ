# N1081B Time-Tag Watcher (Module 5 / .244)

> ⚠️ **DISABLED 2026-07-15 — wedged the board.** A ~1 h run captured **0 edges** (the TT
> engine had already stopped emitting, likely wedged by the volume of rapid `start_tt_data`/
> `stop_tt_data` cycling), and .244's command interface then **hung entirely** — the websocket
> still opens but `login` never replies, i.e. only a **power-cycle** clears it. Auto-start is
> commented out in `start_servers.sh`. **Do NOT run this watcher again** until the cadence is
> made much gentler (poll each section every ~10–30 s, not back-to-back; keep far fewer TT
> start/stop cycles; add a "0-edges-while-beam-on ⇒ reconnect/re-arm/alert" health check) AND a
> multi-hour soak test passes without wedging. Single-section `mod5_timetag_logger.py` has run
> at kHz without wedging — the damage came from the aggressive multi-section start/stop churn.
> Recovery: power-cycle .244, then `python n1081b_timetag_watcher.py --restore` (or verify it
> booted to counters). See §"Failure 2026-07-15" at the end.


Always-on telemetry watcher that reads the four scintillator-wall sections of N1081B
Module 5 (.244) as **per-edge timestamps** and appends them to a per-day CSV — the same
"sole-owner process + daily CSV + published state file" pattern as the gas / ³He / beam
watchers. The point is to have a continuous, independently-acquired timestamp stream of
the walls that can be matched to DREAM runs **after the fact** by aligning timestamps.

- **Controller:** `n1081b/timetag_watcher_controller.py` (`N1081BTimeTagController`)
- **Entry point:** `n1081b_timetag_watcher.py` (repo root); `--restore` recovers .244 to counters
- **Launched by:** `start_servers.sh` (tmux session `n1081b_timetag_watcher`), or the GUI
  Start/Stop buttons (`/start_n1081b_timetag_watcher`, `/stop_...`)
- **CSV:** `n1081b/logs/n1081b_timetag_%Y-%m-%d.csv`
- **State file:** `config/n1081b_timetag_state.json` (served by `/n1081b/status`)
- **Rate history for the GUI:** `/n1081b/history` (bins edges into per-section Hz)

## How it acquires (persistent-FIFO round-robin)

Rests on the persistent-FIFO result (see `TIMETAG_MULTISECTION_2026-07-13.md` §5). In
short: every section is armed to `FN_TIME_TAG` **once**; each then buffers its own edges
concurrently. The watcher reads them **one section at a time** round-robin —
`reset_channel` + `start_tt_data` (dumps that section's buffered backlog) → drain the
broadcast `send_data` packets → `stop_tt_data` — and dedups by `(section, channel,
t_board_ns)` across a rolling horizon so every edge is written exactly once.

Measured on .244: ~380 ms drain/section, ~1.6 s to cover all four → each section polled
every ~1.6 s, far inside the board's ~tens-of-seconds buffer horizon, so no spill is ever
missed. Overlap between successive reads is ~1.0 (essentially only-new tags) and
exactly-once capture was validated (zero late-appearances over a 32 s run). **IO is light**
— a handful of short websocket sends plus one bounded drain per section per ~1.6 s.

**Exclusivity:** while the watcher streams, nothing else may open an SDK connection to
.244 (the board broadcasts its stream to every client). `poll_modules.py` auto-skips .244
whenever the watcher's tmux session is alive. On any clean exit (Ctrl-C / `kill` /
`tmux kill-session` → SIGINT/SIGTERM/SIGHUP) the watcher restores .244 to its counter
steady state and verifies the readback. If it is SIGKILLed it cannot restore, and the
board auto-reverts sections to `wire` passthrough — run `python n1081b_timetag_watcher.py
--restore` (or restart+stop the watcher) to put it back to counters.

## CSV schema

One row per edge (deduped, exactly once):

| column | meaning |
|---|---|
| `host_unix` | wall-clock (epoch seconds) of the poll that captured the edge — **coarse**, good to ~cycle time (~1.6 s); the same value repeats for all edges drained in one section-poll |
| `section` | `A`–`D` (which wall section — attribution is by which section was tapped) |
| `channel` | panel 1–6 (= lemo+1) within that section |
| `t_board_ns` | **precise** free-running board clock in ns (10 ns steps, ~64-bit, common to all sections, does NOT reset) |

`host_unix` is the coarse anchor (bounds the offline slide search); `t_board_ns` carries
the precise relative timing used for coincidence/rate structure.

## Matching to DREAM after the fact (the timestamp slide)

The board clock is **free-running** (relative to board power-on), not wall time, so it must
be related to DREAM's timestamps. Two layers, coarse then fine — exactly analogous to the
TIMBER beam-data matching:

1. **Coarse board→wall anchor (from the CSV itself).** Within any short window the board
   clock is linear in wall time at a known rate (1e8 ticks/s, crystal ~ppm). Each
   section-poll contributes a pair `(host_unix, max t_board_ns)` where the newest edge ≈
   "now" at that `host_unix`. Fitting `wall ≈ host0 + (t_board − board0)/1e8` (NTP-style:
   take the minimum-offset pairs, since `host_unix` lags the true edge) gives wall time
   for every `t_board_ns` to ~second accuracy — enough to place a DREAM run in the stream.

2. **Fine alignment (cross-correlate the shared spill structure).** Beam spills (~30 ms
   every ~1.2 s supercycle, ~24 % duty) imprint the SAME sharp rate structure on both the
   walls and DREAM. Build a rate-vs-time histogram from `t_board_ns` (walls) and from the
   DREAM event times over the same span, then slide one against the other and take the lag
   that maximises the cross-correlation. That lag is the precise board↔DREAM offset for
   that run; apply it and the wall timestamps are on the DREAM time axis. Sub-supercycle
   precision comes straight from the 10 ns `t_board_ns`.

Gotchas to respect in the matcher:
- Tags within a poll are **not strictly time-ordered** — sort by `t_board_ns` before
  histogramming.
- Use `beam_state.json` / `beam_intensity_*.csv` to restrict the correlation to
  beam-on stretches (off-beam is featureless and correlates poorly).
- During an intense spill the board buffer is depth-limited (~4000+ tags/section seen); if
  one wall exceeds that in a single spill the oldest edges of that spill are dropped —
  measure peak edges/spill/section before trusting completeness on the hottest wall.

## Volume & tuning

Aggregate edge rate scales with beam: ~800–900 Hz across the four sections during beam,
near-zero off-beam. That is roughly 10⁷ edges on a heavy beam day (~a few hundred MB
uncompressed) — gzip-rotation of old day-files is the obvious follow-up. The inter-cycle
sleep is GUI-tunable via `config/n1081b_timetag_config.json` (`{"cycle_s": <seconds>}`,
clamped 0–30 s; 0 = poll back-to-back, paced by drain time). A beam-gated mode (only write
when `beam_state.json` says beam-on) would cut ~¾ of the volume if wanted.
