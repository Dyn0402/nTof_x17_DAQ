# HANDOFF 2026-07-17 — .244 power-cycle result: the TT "per-section wedge" diagnosis is unsound

**Status:** `.244` (M5) is **healthy and idle**, all four sections on `counter`, thresholds
untouched, config verified identical to the pre-wedge snapshot. Nothing is running against it.
The time-tag watcher is **stopped** and its auto-start in `start_servers.sh` remains commented out.

**Bottom line:** the power-cycle cleared the full-board wedge (that part worked). But the
follow-up probing showed that **`n1081b/tt_section_probe.py`'s single-tap verdict is not
reproducible**, and the "per-section TT wedge" model built on it — in `TIMETAG_WATCHER.md`
§"Per-section TT wedge", `n1081b/CLAUDE.md` §Current board state, and
`POST_REBOOT_244_CHECKLIST.md` §ROUND 2 — is **not supported by the data**. Do not act on
those three passages, and **do not send the CAEN "nonvolatile wedge" question**, until the
measurement is fixed. Those docs have NOT been edited yet — they still assert the old story.

---

## 1. Timeline of what happened

| Time (07-17) | Event |
|---|---|
| 01:15–02:35 | v2 watcher soak, `--sections CD`. Ran clean: **71,571 edges** (C 51,348 / D 20,223), 80 min. |
| ~02:35 | Board dies abruptly. Per-minute edge rate was **normal right up to the last minute** (02:35 = 797 edges) — no gradual degradation. |
| 02:56 | Watcher self-stops: `alarm: "5 stream-error reconnects within an hour (budget 4) — stopping"`, `last_error: WebSocketTimeoutException`. **v2's stop-don't-hammer policy worked as designed.** |
| ~09:5x | Verified wedged: `.244` login hangs the full 8.01 s; other five boards reply in 0.02 s (rules out network/switch). No quarantine marker was written (the watcher exited via its own alarm path, not `BoardWedgedError`). |
| ~10:1x | **Physical NIM power-cycle** (Dylan, at the crate). |
| after | `ConnectionRefused` → then login **0.0 s**. Board back. |

## 2. Post-power-cycle recovery — verified good

- Login 0.0 s, clean reply.
- `restore_244_counters.py` → all four sections `counter`.
- Counting confirmed over 5 s, **beam OFF** (last pulse 08:00), background only:
  - `SEC_A` ch0 **1348 Hz**, ch1 117, ch2 84, ch3 117
  - `SEC_B` ch0 2507, ch1 785, ch2 132, ch3 **6755 Hz**
  - `SEC_C` ch0–3 ≈ **9–15 Hz**
  - `SEC_D` ch0 **48 Hz**, ch1 ~1 Hz, ch2 0, ch3 0
- Config compared field-by-field against `n1081b/snapshots/dump_2026-07-16_pre_run47.json`:
  **byte-identical** — clock `2`, `config_file_list` intact (incl. `asbuilt_20260715`), every
  section's `input_configuration` and every channel's `input_channel_configuration` unchanged.
  → The board restored its saved config on boot. It did **not** come up on defaults.

## 3. The probe results that break the model

### 3a. `tt_section_probe.py --sections ABCD --drain 5` (all 6 lemos armed, `reset_channel` hardcoded to **channel 1**)

```
A: 0 tags   SILENT
B: 0 tags   SILENT
C: 176 tags (t_board span 51.9 s incl. backlog)
D: 0 tags   SILENT      <-- D streamed 20,223 edges 8 h earlier
```
Isolated single-section re-probes (own session each): `D` alone → 0. `A` alone → 0.

**First red flag:** a power-cycle cannot *create* a persistent per-section wedge on D, which
was healthy hours before. So "silent ⇒ wedged" was already suspect here.

### 3b. Channel-mask test (scratchpad `tt_chan_test.py`) — CONFOUNDED, see note

```
D.ch1 (~1 Hz)   reset=1  -> 292 tags  STREAMS
D.ch0 (~48 Hz)  reset=0  ->   0 tags  SILENT
C.ch0 (~9 Hz)   reset=0  ->   0 tags  SILENT
A.ch2 (~84 Hz)  reset=0  ->   0 tags  SILENT
```
**`D.ch1` streaming 292 tags proves D's TT path is alive** — minutes after the probe called D
"wedged". But this script varied `reset_channel` *together with* the lemo mask (it reset the
lemo it enabled, whereas `tt_section_probe.py` always resets channel 1), so rate and
reset-channel are entangled. Test 3c fixes that.

### 3c. Matrix with `reset_channel` held at 1, one lemo at a time (scratchpad `tt_matrix.py`)

```
D.ch1  (1 Hz)   reset=1  ->   0 tags  SILENT   <-- SAME PARAMS as the 292-tag run above
D.ch1  (1 Hz)   reset=0  ->   0 tags  SILENT
D.ch0  (48 Hz)  reset=1  -> 213 tags  STREAMS  <-- was SILENT in 3b
C.ch0  (9 Hz)   reset=1  ->   0 tags  SILENT
C.ch1  (15 Hz)  reset=1  -> 202 tags  STREAMS
A.ch2  (84 Hz)  reset=1  ->   0 tags  SILENT
A.ch1  (117 Hz) reset=1  ->   0 tags  SILENT
```

**`D.ch1 reset=1` gave 292 tags in 3b and 0 tags in 3c, ~3 minutes apart, identical parameters.**
The measurement is non-deterministic. Every hypothesis we tried — rate-ordering,
reset-channel, mask composition, probe ordering — is falsified by one row or another of this
table. See §4 for the one model that does fit.

## 4. The model that fits: taps dump backlog, they don't stream live

Every tap that produced anything produced **176 / 202 / 213 / 292** tags — a tight cluster,
**independent of the channel's rate** (9 Hz and 48 Hz both yield ~200). And the first probe
reported a **`t_board` span of 51.9 s inside a 5 s drain window**.

A 9 Hz channel cannot emit ~200 edges in 8 s of live acquisition. It can if `start_tt_data` is
flushing a buffer that has been filling for ~a minute. So:

> **A tap dumps whatever that section has buffered. If the buffer was recently drained, the tap
> returns 0 — which the probe prints as `SILENT` and the docs read as "TT-wedged".**

This explains, with no extra assumptions: C/D flipping between runs; order-dependence; the
rate-independent ~200-tag ceiling; the 51.9 s span; and why back-to-back re-probes of a section
that *just* streamed come back silent. **`SILENT` is largely a buffer-state artifact, not a
health signal.** It follows that the v2 watcher's "live drain during a 12 s dwell" mental model
in `TIMETAG_WATCHER.md` §"How v2 acquires" may also be wrong — the CSV may be backlog dumps
throughout, which would matter for the `host_unix` precision claim in the CSV-schema table.

## 5. What survives: A and B are still anomalous

**A and B never streamed once** — ~6 independent taps today, multiple channels, both mask
configurations, before and after the power-cycle. And the backlog model makes this *worse*, not
better: A counts at **1348 Hz**, so its buffer (~4000 tags per `TIMETAG_MULTISECTION_2026-07-13.md`)
should refill in ~3 s and **every** tap should dump a full buffer. A clean zero from A is a real
anomaly; C/D's flickering is not.

**Leading hypothesis (untested): it is rate-driven, not wedge-driven.** A/B are the kHz walls;
C/D are the ~10 Hz sections. A TT engine that silently drops out on buffer overflow reproduces
the whole picture. Supporting evidence: `n1081b/snapshots/walls_tt_v1.csv` (10 MB, Jul 11) is a
**successful section-A wall capture** — A demonstrably streamed historically, which makes
"A is permanently broken" a harder sell than "A's present rate breaks it". Worth checking what
A's rate was on Jul 11 vs the 1348 Hz now.

## 6. Recommended next steps

1. **Rewrite `tt_section_probe.py` before trusting any verdict.** It needs: multiple taps per
   section; a deliberate buffer-accumulation delay between taps; and reporting of the `t_board`
   span + first/last tag times so a backlog dump is distinguishable from live streaming.
   Its current single-tap `SILENT`/`STREAMS` verdict is what generated this entire false story.
2. **Test the rate hypothesis on A** (needs Dylan's OK — it is a *write* to `.244`): raise
   SEC_A's threshold to pull its rate under ~15 Hz, re-probe, then restore the threshold.
   `.244` is walls-monitoring only (zero trigger impact), but this changes physics monitoring,
   so agree a restore plan first. Pre-change values are in the snapshot (§2) — currently
   `threshold: 0, th_unit: 0` on every section.
3. **Then, and only then**, correct the three doc passages listed at the top and decide whether
   any CAEN question remains.
4. **Separately — the watcher's own bug is still real and unfixed.** The 02:35 wedge stands on
   its own: 5 stream-error reconnects in an hour, each rebuilding a session on a socket that had
   just errored out, and the board died on the last one. Reconnect-after-stream-error is the
   prime suspect for the wedge mechanism (a dirty disconnect is exactly the documented poison —
   `HANDOFF_2026-07-15_wedge_root_cause.md`). Auto-start stays commented out. Consider whether
   the reconnect budget should be **0** — i.e. stream error ⇒ stop, never re-dial.

## 7. Cautions for whoever picks this up

- All board contact today went through `board_session()` except two **bounded login probes**
  (the sanctioned `POST_REBOOT_244_CHECKLIST.md` step-1 pattern) — `n1081b/CLAUDE.md` rule 1.
- **Disclosure:** early in the session I ran `dump_module_info.py --help`, which that script
  does not support — it therefore ran a **real read-only dump against all six boards**, and a
  `head -30` truncated it mid-print. This did **not** cause the `.244` wedge (the board had
  been unreachable for ~7 h already, per §1) and the other five boards verified healthy at
  0.02 s afterwards. Python raises `BrokenPipeError` rather than dying on a signal, so the
  session wrapper unwound cleanly. Noted so nobody re-derives it as a cause. Don't pipe that
  script into `head`; redirect to a file.
- `.244` is currently **free** — no quarantine marker, no holder, no process attached.
- Scratchpad scripts (`tt_chan_test.py`, `tt_matrix.py`, `dump_244_after_powercycle.json`,
  `now_244.json`) are in
  `/tmp/claude-1000/-home-mx17-PycharmProjects-nTof-x17-DAQ/f30b4063-b54f-41b3-bdee-2fb3dc71a7ba/scratchpad`
  — **ephemeral**; copy them into the repo if they're worth keeping.
- Beam was **OFF** for all measurements above (last pulse 08:00). Any re-test under beam-on
  will have very different section rates — which, if §5 is right, is itself a variable.
