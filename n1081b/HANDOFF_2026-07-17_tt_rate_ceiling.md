# HANDOFF 2026-07-17 (midday) — "per-section TT wedge" SOLVED: it is a live rate ceiling

**One-line result:** N1081B .244's Time-Tag sections were never wedged. A section emits
**zero TT tags whenever its input rate is above a ceiling** (bracketed today: **~50 Hz
streams, ~800 Hz is silent**, at stream-start time) and streams perfectly below it —
regardless of section identity, history, reboots, or pulse width. Proven by cable swap:
**section A streamed a ~10–20 Hz signal flawlessly** minutes after being "diagnosed
wedged" for the fourth day running, and C and D — the two "healthy" sections — went
**silent within the hour as their own beam-off rates spiked to ~900 Hz**.

Board state at end of session: `.244` power-cycled (closeout), standard cabling
restored, all four sections `counter` and verified counting (login 0.06 s). No wedge
occurred today; no quarantine exists. The area is closed; next physical access TBD.

---

## 1. What was tested (all via `board_session`; writes read-back-verified + restored)

Instrument first: `n1081b/tt_probe_v2.py` (new) replaces the single-tap SILENT/STREAMS
verdict of `tt_section_probe.py`. Per section it takes four taps that differ only in
buffer age (IMMEDIATE ~0.3 s after arming / DELAYED after a gap / DOUBLE back-to-back /
CYCLE after a counter→TT function cycle), and classifies backlog-burst vs live tags by
arrival time and `t_board` span. `--fast` gets arm→tap down to ~0.1 s.

| # | Hypothesis | Test | Result |
|---|---|---|---|
| 1 | Probe-timing overflow latch (A/B buffers overflow before every historical tap) | IMMEDIATE tap 0.3 s after arm (A buffer <1k) + CYCLE tap | **Falsified** — A/B silent on all taps incl. immediate/cycle |
| 2 | B-specific: beat B's 0.2 s overflow | `--fast` (~0.1 s arm→tap) | **Falsified** — B silent from the first packet flush |
| 3 | .244 config difference A/B vs C/D | field-by-field diff of all four sections (pre_run47 dump) | **Falsified** — byte-identical |
| 4 | Pulse width (M1 tap mono thinned 50→15 ns after Jul-11) | M1 SEC_A out1 mono 15→50 ns (read-back verified), probe, restore | **Falsified** — still zero tags at 50 ns |
| 5 | **Live rate ceiling** | Cable swap at crate: C-sector tap (~10–20 Hz) into A input 4 | **CONFIRMED** — A streams 81–115 tags/tap, ~20 Hz live, every tap |
| 6 | (corollary) ceiling applies to any section | C at ~800 Hz agg, D at ~900 Hz agg (beam-off spikes) | Both **silent**; both had streamed at ~40–50 Hz hours earlier |
| 7 | Deep ~30 s backlog buffer (TIMETAG_MULTISECTION §5c) | A at ~20 Hz, 25 s armed-untapped, then tap | **Not reproduced** — burst = 14 tags, not the ~500 expected; taps today deliver essentially live data only |

Supporting history that now fits: Jul-11 `walls_tt_v1.csv` streamed section A at 2.5 kHz
*continuously* (single long-running stream); rate_scan_2d streamed ~230 Hz in 87.8 s
dwells; the 07-14 persistent-FIFO tests ran B/C at 148/9 Hz. Every documented TT success
was low-rate at stream-start; every "wedge" was a section sitting at kHz.

## 2. The model (and one open discrepancy)

- **Counters are pure FPGA and count anything; TT tags must cross the Zynq daemon.**
  When a section's rate is above the ceiling at `reset_channel`+`start_tt_data` time,
  the tap yields **nothing at all** — not truncated data, zero, silently.
- Ceiling bracket today: **between ~50 Hz (streams) and ~800 Hz (silent)** aggregate.
  Jul-11's sustained 2.5 kHz suggests the choke is at **stream-start/dump priming**, and
  an already-running stream may sustain much higher rates — **untested**.
- **Open discrepancy:** 07-14 measured real ~30 s backlog accumulation (2097 tags after
  2 s untapped); today there was none (14 tags after 25 s). Unknown what selects the
  regime. Until resolved, assume **rotation gaps are NOT covered by backlog dumps**.

## 3. Consequences — doc/plan corrections

1. **No CAEN "nonvolatile per-section wedge" question.** Nothing is wedged and nothing
   is nonvolatile. The right CAEN question is now: *what is the TT streaming path's rate
   limit, and why does an over-rate `start_tt_data` yield silence instead of truncation?*
2. **`TIMETAG_MULTISECTION_2026-07-13.md` §3 / `TIMETAG_WATCHER.md` "Per-section TT
   wedge" / `POST_REBOOT_244_CHECKLIST.md` ROUND 2 are obsolete** (the morning handoff
   `HANDOFF_2026-07-17_tt_probe_unreliable.md` already flagged them unsound; this
   handoff supplies the replacement model). Reboots/power-cycles never fixed or broke
   TT sections — the sections' *rates* drifted across the ceiling between observations.
3. **The walls (A) and liq (B) cannot be TT-streamed at current beam-off rates**
   (A ~3–6 kHz swinging, B ~19–27 kHz — the plastics-HV/threshold noise). Options:
   raise upstream thresholds, or test whether a stream *started* below the ceiling
   sustains kHz (Jul-11 precedent says maybe).
4. **v2 watcher revisions needed before any re-enable** (auto-start stays commented):
   - Its "backlog dump covers the rotation gap" completeness claim is wrong (§2).
   - Its 02:35 death now has a cleaner story: C/D beam-off rates spike above the
     ceiling → taps go silent → recv timeouts ("stream errors") → **5 reconnect+re-arm
     cycles onto a board that was never sick** → full-board wedge from the reconnect
     churn itself. The reconnect budget should be **0** (stream error ⇒ stop). A
     rate-aware gate (read counters first; skip sections above ~50 Hz) would prevent
     the whole cascade.
5. **The whole-board libwebsock wedge story is untouched** by today — still real,
   still the reason for `board_session` hygiene. The planned dose-response test
   (N dirty disconnects ⇒ stage-1/2/3) did NOT run today (time); it remains the top
   item for the next physical-access window, .244 only, power-cycle standing by.

## 4. Appendix — raw measurements (session ~10:30–12:10, beam OFF throughout)

Probe output format: per tap, total tags | tags in first 1 s of drain (backlog burst)
| tags after 1 s (≈live) | estimated live Hz | per-channel counts. Taps per section:
IMMEDIATE (~0.3 s after arm) / DELAYED (after gap) / DOUBLE (~0.8 s after DELAYED) /
CYCLE (counter→TT cycle then instant tap).

**Baseline, standard cabling** (`tt_probe_v2.py --sections CABD --gap 15 --drain 6`):

| Sec | IMMEDIATE | DELAYED | DOUBLE | CYCLE | counters Δ/3 s after restore |
|---|---|---|---|---|---|
| C | 209 (30 burst / 179 live ≈36 Hz) | 226 | 228 | 255 | 40, 66, 65, 57 (~76 Hz agg) |
| A | 0 | 0 | 0 | 0 | 6877, 687, 515, 587 (~2.9 kHz) |
| B | 0 | 0 | 0 | 0 | 14062, 3873, 616, 39270 (~19 kHz) |
| D | 240 (39/201 ≈40 Hz) | 243 | 213 | 242 | 138, 5, 8, 7 (~53 Hz) |

All C/D taps: burst spans ~58–61 s of board clock but only ~20–40 tags — the "dump"
is sparse, not a full-rate backlog (see §2 discrepancy).

**B fast-tap** (`--sections B --gap 3 --drain 6 --fast`, arm→tap ~0.1 s): all four
taps 0 tags; zero from the first packet flush (~12 ms). B counters that session:
2372, 75171, 2042, 1848 /3 s (~27 kHz — ch1 swings 1–25 kHz between readings).

**M1 width test:** `.240` SEC_A out1 (M5.A panel-1 tap) read {status:True,
enable_mono:True, mono_value:15, invert:False} → written 50, read-back verified →
probe A: 0 tags on all four taps (panel-1 counter ~1.7 kHz during test) → restored
15, read-back verified.

**Cable pulls (Dylan at crate):** A inputs 1,2,5 unplugged, input 4 kept: counters
confirmed [0, 0, 8751, 0]/3 s — the one remaining wall spiked to ~2.9 kHz on its own
(SiPM beam-off rates swing by ~10× minute-to-minute all session). A still silent on
all taps.

**Cable swap (C input-2 cable → A input 4, A otherwise empty):**

| Sec | IMMEDIATE | DELAYED | DOUBLE | CYCLE | counters Δ/3 s |
|---|---|---|---|---|---|
| A | 98 (20/78 ≈20 Hz, all ch4) | 91 | 113 | 109 | 0, 0, 83, 0 (~28 Hz) |
| C | 0 | 0 | 0 | 0 | 57, 0, 75, 2279 (~800 Hz — ch3 spiked) |

**Backlog-accumulation test** (A at ~20 Hz, `--gap 25`): DELAYED tap burst = **14
tags** (expected ~500 if 25 s accumulated); all taps 81–115 tags ≈ live-only.

**C/D re-probe minutes later:** both 0 tags on all taps; counters C = 78, 0, 94,
2649 (~940 Hz), D = 2688, 4, 173, 156 (~1.0 kHz — Singles ch0 spiked beam-off).
Both had streamed at ~40–50 Hz agg earlier the same morning → within-section,
within-hour rate↔silence correlation on two sections.

**Closeout:** standard cabling restored, NIM power-cycle ~11:5x, boot poll:
ConnectionRefused → login OK 0.06 s on attempt 2 (+15 s). `restore_244_counters.py`
OK; counting verified: A Δ/3s = 4234, 1116, 1234, 10181; B = 10359, 3355, 1913, 284;
C = 51, 68, 59, 164; D = 270, 2, 11, 10.

## 5. Session hygiene notes

- All contact via `board_session` (probes use the sanctioned raw-one-way pattern after
  arming); two bounded login probes (checklist step-1 pattern) during boot polling.
- Writes performed (all read-back-verified, all restored + re-verified):
  M1 (.240) SEC_A out1 mono 15→50→15 ns. `.244` sections cycled TT↔counter by the
  probes, restored to counter each run.
- Cable moves (by Dylan, at crate): M5.A inputs 1,2,5 unplugged → A empty except a
  C-sector cable in A4 for the swap test → **standard cabling restored** at closeout.
- Power-cycle at closeout (area closing), post-boot verify clean: login 0.06 s,
  `restore_244_counters.py` OK, all sections counting (A ≈5.2 kHz agg, B ≈5.3 kHz,
  C ≈114 Hz, D ≈98 Hz at 12:0x beam-off).
