# TT continuous-stream qualification plan — can .244 log every trigger?

**Goal:** decide whether Module 5 can produce a **faithful (gap-free) per-trigger
timestamp record** good enough to match each DREAM event to the trigger that fired it.
The v3 watcher's rotation is NOT good enough for that (edges of the un-tapped section
may be lost — no reliable backlog); a faithful record needs **one section streamed
continuously**. This plan qualifies that mode. Harness: `n1081b/tt_stream_qualify.py`
(offline-smoke-tested 07-17; not yet run on hardware).

## Which section? (check this before testing — mapping is easy to get backwards)

Per the VERIFIED map (`n1081b_module_map.py`, 2026-07-16):

| M5 section | inputs (counter idx 0-3 = panels 1,2,4,5) | what it gives you |
|---|---|---|
| **D** | **Singles, Doubles, gated pulser (M4.C out), MASTER TRIGGER (M4.D out)** | per-event trigger identity: match master-trigger edges to DREAM events, attribute type by which of Singles/Doubles/pulser coincides |
| C | sector1-4 coincidences (M3) | which sector fired (no trigger identity) |

**Preference (Dylan, 07-17): C first, D as fallback.** The per-sector timestamps are
the richest record — the trigger logic (Singles/Doubles) can be *reconstructed* from
sector timestamps offline, while D only gives the already-collapsed trigger outputs.
If C can't be streamed faithfully, D alone is still acceptable. Test order is still
**D first** (agreed 07-17) — its master-trigger channel gives the fastest direct
completeness check against DREAM events, and both sections need the same sustain
qualification anyway.

Caveat that decides everything: **per-channel TT masking is impossible**
(`TIMETAG_MULTISECTION_2026-07-13.md` §1c — the enable mask is ignored, and disabling
the input doesn't suppress emission either). A section always streams all its cabled
channels, so the **aggregate rate** is what must survive — and **C and D face
essentially the same rate** (Dylan, 07-17): Singles = OR(sector1..4), so C's
aggregate (Σ sector rates) is mathematically ≥ the Singles rate, and D's aggregate ≈
Singles + Doubles + ~6-10 Hz of pulser/trigger. Confirmed in the 07-17 morning data:
D's Singles spike ~900 Hz coincided with C aggregate ~940 Hz. Neither section gets a
rate advantage — the C preference rests purely on information content, and the
sustain test (T2) is equally decisive for both.

## The two physics unknowns (from `HANDOFF_2026-07-17_tt_rate_ceiling.md` §2)

1. **Start ceiling vs sustain ceiling.** Stream-STARTS above ~50-800 Hz yield silence,
   but Jul-11 sustained 2.5 kHz on an already-running stream. If a stream opened
   during a quiet moment rides through kHz excursions, D is loggable. If the ceiling
   also kills running streams, only quiet stretches are covered → not faithful.
2. **Loss vs late.** When delivery pauses, is data dropped or buffered-then-dumped?
   07-14 saw a real ~30 s backlog; 07-17 saw none. Regime selector unknown.

## Test sequence (no physical access needed; .244 only; between/after rate-scan use)

Preconditions for every step: rate_scan_2d finished and nothing holds .244 (Board
Access card / `config/n1081b_access/`); board healthy (it is, post 07-17 closeout).
The harness is single-session, 3 stream commands per run, zero reconnects, restores
counters + verifies on every exit path — the proven-gentle cadence.

- **T1 — mechanics + quiet-section soak (beam-on or off), ~15 min:**
  `tt_stream_qualify.py --section D --duration 900`. Expect: continuous delivery,
  streamed/expected ratio ≈ 1 per channel, gaps only where tboard_gap ≈ host_gap
  (input actually quiet). If D is over-ceiling at start time, retry when its counter
  rate dips (watch the GUI card / rerun --pre only), or run C first for mechanics.
- **T2 — sustain test (THE decider), 1-2 h spanning natural rate excursions:**
  `--section D --duration 7200 --label secD_sustain`. Beam-off spikes (~1 kHz swings
  minute-to-minute) and/or beam spills provide the over-ceiling excursions free.
  Read-out: does delivery continue *during* excursions (edge rate tracks counter-rate
  structure), or do gaps open? For each gap: tboard_gap ≈ host_gap + counters say
  input was live ⇒ **tags dropped** (bad); burst after gap with contiguous t_board ⇒
  buffered (late but complete).
- **T3 — completeness vs DREAM (the real criterion), during a normal run:**
  stream D for a full run/subrun; offline, count master-trigger edges (panel 5)
  against DREAM's event count for the same window (`current-run-from-daq-log`
  events per FEU; beam CSVs for spill windows). Faithful = every DREAM event has
  exactly one matching master-trigger edge within clock-anchor tolerance.
- **T2c/T3c — same sustain + completeness on C (the PREFERRED section):**
  `--section C --duration 7200 --label secC_sustain`, then a full-run stream.
  C's completeness check: every master-trigger edge from the D runs (or every DREAM
  event) must have a reconstructing sector-edge pattern within the coincidence
  window; sector rates vs C counters as the in-stream check.

## Decision tree (preference: C > D — sector record reconstructs the trigger logic)

- C sustains + complete → production = **continuous C streamer** (the full-info
  record; D re-derivable offline).
- C drops tags during excursions but D is clean → **continuous D streamer**
  (trigger-identity record only; accepted fallback).
- D clean and C marginal only during spikes (unlikely — their aggregates track each
  other, see above) → stream C but gate *analysis* trust by the counters CSV (know
  when C was over-ceiling), or upstream rate reduction (thresholds feeding M3
  sectors — a physics decision).
- Both drop → TT path unusable for faithful logging at current rates; fall back to
  v3 watcher telemetry + pursue the CAEN question (why does over-rate streaming
  drop instead of throttle?) and upstream rate reduction.
- Whichever section wins: adapt the v3 watcher (drop rotation; stream the winner
  always-on, counter cycles for the other sections only at stream restarts) or a
  mod5-logger-style dedicated per-run process.

## Running in parallel with a DAQ run (safe, and T3 requires it)

.244 is monitoring-only — never in the trigger path — so the harness cannot affect a
run; worst case (harness error / even a board wedge) only costs walls monitoring and
the M5 part of the per-subrun config snapshot. Interlocks verified 2026-07-17:

- `board_session` takes its flock BEFORE connecting, so a mid-run `poll_modules`
  visit to .244 can never open a socket onto the stream — it logs busy and skips.
- Cleaner still: launch the harness inside a tmux session named
  `n1081b_timetag_watcher` — `poll_modules` then skips .244 without even trying:
  `tmux new-session -d -s n1081b_timetag_watcher \
       '.venv/bin/python n1081b/tt_stream_qualify.py --section D --duration 7200 \
        --label secD_run_parallel'`
- While that session name is in use: do NOT press the GUI's Start/Stop time-tag
  watcher buttons (Start does `tmux kill-session` on that name first — it would
  SIGHUP the harness; the harness restores cleanly on SIGHUP, but the test dies)
  and the dashboard TT card will read Starting/Stale (cosmetic — the harness does
  not publish the state file).

**Schedule for a ~1 h gap then a scintillator-triggered run:**
1. Gap (needs ~35 min + margin): T1 on D (`--duration 900`, ~17 min with baselines),
   then T1 on C (~17 min). Mechanics + quiet-stretch behavior for both sections.
2. During the scint run: long parallel stream = T2+T3 in one shot
   (`--section D --duration 7200` via the tmux recipe above; in-spill rates provide
   the over-ceiling excursions, and the run's DREAM events are the completeness
   ground truth). Next run: same on C.

## Results so far (update as tests run)

- **T1 D (2026-07-18 00:08, 15 min): SILENT.** Aggregate ~755 Hz throughout
  (Singles ~711 Hz on panel 1 alone, Doubles ~31, pulser ~6, trigger ~7; pre/post
  stable). Zero edges, stream never caught. At current baseline thresholds D is
  permanently over-ceiling — D cannot start a stream.
- **T1 C (2026-07-18 00:24, 15 min): STREAMS, ESSENTIALLY COMPLETE.** Aggregate
  ~745 Hz (sectors ~150-220 Hz each), 670,808 edges, max packet gap 0.2 s, zero
  gaps > 2 s, **streamed/expected = 0.98 / 1.01 / 1.00 / 1.00** per channel.
- **MODEL REVISION — the ceiling is PER-CHANNEL, not aggregate.** C at 745 Hz agg
  streamed perfectly while D at 755 Hz agg was silent minutes earlier; the only
  structural difference is channel distribution (C max channel ~220 Hz; D has one
  711 Hz channel). Re-reading 07-17 morning: every silent case had a single
  channel ≥ ~700 Hz (C ch3 760 Hz, D ch0 900 Hz, A wall 2.9 kHz, B 25 kHz);
  every streaming case had max channel ≤ ~220 Hz. Bracket: **per-channel streams
  ≤ ~220 Hz, silent ≥ ~700 Hz**. Consequence: C works as long as no single
  sector exceeds the per-channel ceiling; D is hostage to Singles alone.
- T2 C sustain (2 h, launched 00:25 in the `n1081b_timetag_watcher` tmux): pending.

## Bookkeeping

- Results: `~/beam_july/test/tt_stream_qualify/<label>/` (edges.csv, counters.csv,
  stats.json — gaps recorded with host + board-clock spans).
- The v3 watcher and this harness must not run at the same time (both want .244;
  the session flock will refuse the second one — that refusal ends the harness by
  design, so just don't).
- Update `TIMETAG_WATCHER.md` + memory `n1081b-timetag-watcher` with the verdict.
