# HANDOFF — 3-mode trigger setup + DREAM latency tuning (2026-07-11 afternoon)

Extends `HANDOFF_2026-07-11_dream_latency_tuning.md` to three run configurations.
All work done 2026-07-11 ~12:00–14:30 with parasitic beam (~0.28 pulses/s,
600–850e10), gas Ar/iso 80/20, HV resist 690 V (A/B/C; D lowered → parked, see
§Detector D), drift 800 V.

**Mode switching: `n1081b/trigger_mode.py`** (`status | flash | flash_random |
scint [--singles|--doubles|--both]`). Volatile live-board settings — re-apply
after any .243 power cycle. Log: `snapshots/trigger_mode_log.jsonl`.

---

## The three modes (all drive M4.D out0 = DREAM trigger cable)

| # | Mode | M4.C (or_veto, veto=lemo5) | M4.D (OR) | DREAM readout | Latency (Dream reg 12) |
|---|------|---------------------------|-----------|---------------|------------------------|
| 1 | `flash` | untouched | lemo0 only | 400 smp × 20 ns | **60** (see §Mode 1) |
| 2 | `flash_random` | lemo4 = pulser only | lemo0 + lemo1 | 32 smp × 60 ns | **5** |
| 3 | `scint` | lemo0 Singles / lemo1 Doubles | lemo1 only | 32 smp × 60 ns | **see §Mode 3** |

DREAM config mapping (`dream_daq_control.py`): latency → `Feu * Dream * 12 =
0x{lat:04X}`; 60 ns → `DrmClk RdClk_Div=6.0, WrClk_Div=6.0` (20 ns = 4.0/2.0,
template default). **60 ns DrmClk config VERIFIED working** (2-min sanity run,
all 8 FEUs, clean 512 ch × 32 smp events) — the July-11-early-AM
`DrmClkConfig WrRd_Missmatch` failures were purely the FEU network outage.

## Trigger cabling (LA-verified, `verify_trigger_paths.py`)

- **M4.D lemo0 = gamma-flash trigger line**: 0.288 Hz, intervals quantized in
  ~1.2 s PS-cycle multiples, ~100 ns pulses. Positively identified.
- **M4.C lemo4 (panel 5) = M6.D pulser** — NOT panel 3 as first thought.
  Pulser is **Poisson** (`frequency_type 1`), design period 1.5 ms ≈ 667 Hz,
  width 100 (reads ~200 ns on the LA).
- **Veto gate: RESOLVED 2026-07-11 PM — it works, the software never engaged
  it.** The N93B line is fine (LOW 30 ms after PS = enable; HIGH = veto; the
  1 %-duty LOW is invisible to LA frames). Root cause of the "not windowing"
  observations: `configure_or_veto()` does NOT set the function type (fw
  ignores the callback name) → M4.C had been a plain OR all along; the veto
  input was inert (invert-flag flip: no effect). Fix =
  `set_section_function(SEC_C, FN_OR_VETO)` first — now in
  `trigger_mode.py`. Verified: pulser pass rate 6.9 → 0.38 Hz ≈ beam pulse
  rate, C.in5 LOW in every passing frame (manual semantics: pass = OR ∧
  veto-LOW). Mode-2 data rate at design 667 Hz pulser ≈ 5–6 Hz average —
  fine. (Pulser test throttle: 15 ms OK; **150 ms silently kills output**.)
  NOTE: mode-3 Singles/Doubles rates measured earlier were with the veto
  inert — expect lower rates now that out-of-window cosmics are blocked.
- **Singles and Doubles are NOT flash-correlated** (0/60 and 0/40 LA frames
  with a flash pulse in ±20 µs) — consistent with the N93B window design
  (opens 9.6 µs after flash). Mode-3 tuning cannot use the flash.

## Mode 1 — flash only, 400 smp × 20 ns

Scan at latency {40, 60, 80}, 4 min each (~79 events @ 0.33 Hz), variants
`m1_lat40/60/80` in `~/beam_july/test/latency_singles/`.

- **Flash peak = latency + 36–45** (detector-dependent shaping); 1:1 slope
  confirmed on every FEU. (July-2 PS-pickup trigger gave +27 — new path
  arrives ~260 ns earlier.)
- Window anatomy at lat 60 (shifts 1:1): 0–25 flat baseline ✓; ~40–55
  **second railed pulse on det A** (+ blip on D) ≈ 0.9 µs before the flash —
  consistent with the **M6.B mesh charge-injection countermeasure** (500 ns
  mono); ~25–75 coherent undershoot dip to ADC 0; flash rise ~78, peak
  ~96–108, baseline again by ~150–200.
- **Chosen: latency 60** — flash peak ~sample 100 of 400, ~6 µs TOF tail.
- Per-detector flash response: A (FEU03/04) rails 4095; C (07/08) ~3400;
  D (01/02) ~2300 (at 640 V); B (05/06) weak ~1200 with wiggly tail.

## Mode 2 — flash + random, 32 smp × 60 ns

Locate `m2_locate128_60ns` (128 smp, lat 3): flash peak sample 9–14 →
**peak = latency + ~9–13 at 60 ns**. Confirm `m2_confirm32_lat5` (32 smp,
lat 5, pulser throttled to 15 ms): flash peak 16–20 (B/C), A rail onset
11–13; **pulser events are flat pedestal** (avg-profile max dev < 8 ADC,
~5.2 k random events). **Chosen: latency 5.**

Note: run_19's latency 3 (inherited from run_15) would also have worked —
flash at ~12–15 of 32 — but 5 centers it better with the leading dip resolved.

## Mode 3 — scint trigger, 32 smp × 60 ns

Hard mode, as expected: singles are wall+scint coincidences whose track
usually misses the Micromegas, so the per-event signal is rare and sits under
hot-channel noise + a **window-start artifact**. Findings:

- `analyze_m3_locate.py` (best-channel) and `analyze_m3_coinc.py`
  (x/y-plane coincidence + hot-channel mask + edge guard + rising-edge veto)
  in `~/beam_july/test/latency_singles/`.
- **Window-start artifact**: decaying transients in the first ~5–10 samples of
  every event, on all FEUs, at FIXED window position (does NOT shift with
  latency — verified lat 3 vs 15). Plane-coincident, survives naive cuts.
  Any "bump" near the window start is suspect until it moves with latency.
- Hot channel families (adjacent groups): FEU01 307–310, FEU02 460–470,
  FEU04 ~42–56, FEU06 51–54, FEU07 244/500 …; det B (FEU05/06) globally
  noisy.
- det D (best responder, at 640 V in the 12:07 sanity data) showed real
  2.5–3 kADC scint-correlated pulses clipped at window start at lat 3 —
  then its HV went away (§Detector D), removing the easiest detector.
- **Decisive run**: `m3_locate128_lat35` (10 min, ~29 k singles, lat 35) —
  **PHYSICS CONFIRMED on det A**: strong tight bump at samples 9–24 (peak 11,
  n=709, far above floor), det B/C weak echoes at 10–16. On/off latency test
  passed: at lat 15 the window ends before the pulse → det A showed nothing;
  at lat 35 it appears. (det C/D's earlier low-sample "bumps" were artifacts —
  they did NOT move between lat 3 and 15.)
- **MM pulse peak = latency − 24** (span latency − 26 … − 11): the pulse
  precedes the trigger's arrival at the TCM by ~1.4 µs of scint→rack→TCM
  cable + logic delay. This is why lat 3 (run_19 style) would bury the scint
  pulse 24 samples before the window.

**Mode-3 latency: 35 (0x0023)** — pulse span lands at samples 9–24 of 32,
peak ~11, baseline 0–8, tail to ~25. **Confirmed at 32 smp**
(`m3_confirm32_lat35`, 6 min, ~7.5 k singles): det A sharp peak at sample 11
(n=497), det B peak 11–14 (n=196), det C peak 10–14 + tail hump 22–27
(n=537) — all three live detectors in-window.

## Detector D HV timeline (2026-07-11)

640 V (user-lowered, 12:19, imon 8 µA) → contributed cleanly to lat40/60
runs → **dead flat in the lat80 run (12:40)** → found at V0Set=100 V (13:0x,
user-parked, presumably after another trip). resist_C's current also climbed
1.5 → 6.4 µA over ~40 min at 690 V — watch it.

## Files & tools

- `n1081b/trigger_mode.py` — mode switcher (this doc §top).
- `n1081b/verify_trigger_paths.py` — LA verification (script, import-guarded).
- `~/beam_july/test/latency_singles/`: `run_test.py` harness;
  `analyze_m2_confirm.py` (flash/pulser event split);
  `analyze_m3_locate.py`, `analyze_m3_coinc.py` (scint-trigger timing);
  variants `sanity20_32smp`, `sanity60_32smp`, `m1_lat{40,60,80}`,
  `m2_locate128_60ns`, `m2_confirm32_lat{8,5}`, `m3_locate128_lat{15,35}`.
- Snapshots: `dump_2026-07-11_pre_latency_tuning.json` (pre),
  `trigger_mode_log.jsonl` (every mode switch, before/after).
- `check_completeness.py` hardcodes 400-sample expectation — its
  "incomplete" flags on 32/128-smp runs are false positives.

## Late-afternoon follow-ups (same day)

1. **Veto root-caused and FIXED** — see the veto bullet in §Trigger cabling:
   `set_section_function(FN_OR_VETO)` was missing everywhere; now in
   `trigger_mode.py`. Gate verified (pulser 6.9 → 0.38 Hz, passes only while
   C.in5 is LOW). Scint modes now genuinely beam-windowed → expect lower
   Singles/Doubles rates than the inert-veto numbers above.
2. **Mesh injection retimed**: M6.B in0 G&D delay 500 → **1260 ns** —
   injection now arrives ~180 ns before the flash rise and **suppresses the
   det C flash peak 3400 → 2100 ADC (−38 %)**; at 1440 ns (exactly at the
   rise) suppression vanishes → slightly-early is the right timing. Runs
   `m6_inj_aligned` (1260) / `m6_inj_aligned2` (1440). Mode-1 window anatomy
   updated in `RUN_MODES_2026-07.md` (separate pre-pulse gone; baseline now
   flat to ~sample 65 at latency 60).
3. Canonical reference doc created: **`RUN_MODES_2026-07.md`** (modes,
   latencies, full six-module IO map, gotchas).

## Resting state (end of session)

- Trigger: **scint(both)** — C = **real or_veto** (fn readback `or_veto`)
  with Singles+Doubles, veto gating active; D = OR(lemo1).
- Pulser: design 1.5 ms Poisson (667 Hz), gated by the veto in mode 2.
- M6.B injection delay 1260 ns.
- HV: resist A/B 690, C 690 (imon climbing — watch), D parked 100 V (user;
  D tripped), drift 800 ×4.
- DAQ idle; snapshots `dump_2026-07-11_post_latency_tuning.json` (pre-veto-fix)
  and `dump_2026-07-11_veto_injection_final.json` (final).
