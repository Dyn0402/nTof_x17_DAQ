# DREAM run modes & trigger IO — canonical reference (2026-07, July beam)

Everything needed to run the three trigger/readout configurations, plus the
as-built IO layout of all six N1081B modules. Measured 2026-07-11 (parasitic
beam, gas Ar/iso 80/20). Detailed measurement narrative:
`HANDOFF_2026-07-11_latency_tuning.md`.

Switch modes with **`n1081b/trigger_mode.py`** (`status | flash |
flash_random | scint [--singles|--doubles|--both]`). These are volatile
live-board settings on .243 — **re-apply after any power cycle** (log of every
switch: `snapshots/trigger_mode_log.jsonl`).

---

## 1. The three run modes — WHAT TO SET

Placement philosophy: a short flat baseline before the signal (enough to see
the arrival cleanly), then maximize the window after it.

### Mode 1 — Gamma flash only (`trigger_mode.py flash`)

| item | value |
|---|---|
| Trigger | M4.D = OR(lemo0) — PS/flash line only |
| Readout | **400 samples × 20 ns** (8 µs window) |
| **Latency (Dream reg 12)** | **60** (`0x003C`) |
| run_config_beam.py | `n_samples_per_waveform=400, sample_period=20, latency=60` |
| Trigger rate | = beam pulse rate, ~0.29 Hz parasitic (~17/min) |

Window anatomy at latency 60 (everything shifts 1:1 with latency, 1 unit = 1
sample = 20 ns), **after the 2026-07-11 injection retiming** (M6.B in0 G&D
delay 500 → 1260 ns — injection now merged with the flash):
```
smp   0–65    flat baseline (the old separate injection pulse at ~40–55 is gone)
     ~68      composite rise (injection arrives ~180 ns before the flash rise)
     ~78–110  flash + injection;  peak ≈ latency + 25–48 (detector-dependent)
    ~150–200  back to baseline; rest of window = 6 µs neutron-TOF tail
```
Injection timing data (det C flash peak, mean-over-channels):
| M6.B in0 delay | injection vs flash rise | det C peak |
|---|---|---|
| 500 ns (old) | 760 ns early, separate pulse | ~3400 (no cancellation) |
| **1260 ns (set)** | ~180 ns before rise, overlapping | **~2100 — 38 % suppressed** |
| 1440 ns | at/after the rise | ~3600 (no suppression) |

Slightly-before beats exactly-at: the mesh needs pre-loading before the flash
charge arrives. A finer scan (1100–1350 ns) could squeeze more suppression.
**Full study with waveform figures: `snapshots/mesh_injection_report_2026-07-11.pdf`**
(source `snapshots/mesh_injection_report.tex`, figures via
`~/beam_july/test/latency_singles/make_mesh_report_figs.py`).
Flash response by detector (pre-retiming reference): A rails 4095 · C ~3400 ·
D ~2300 (at 640 V) · B ~1200 weak/wiggly.

### Mode 2 — Gamma flash + random pulser (`trigger_mode.py flash_random`)

| item | value |
|---|---|
| Trigger | M4.C = or_veto(lemo4 = pulser) → M4.D = OR(lemo0 flash, lemo1 C-out) |
| Readout | **32 samples × 60 ns** (1.92 µs window) |
| **Latency** | **5** (`0x0005`) |
| run_config_beam.py | `n_samples_per_waveform=32, sample_period=60, latency=5` |
| Trigger rate | flash 0.29 Hz + pulser (667 Hz Poisson design rate, through the 30 ms gate) |

Window anatomy at latency 5 (1 sample = 60 ns; **flash peak ≈ latency + 13**):
```
smp   0–5    baseline (short)
     ~5–10   undershoot dip
     ~10     flash rise;  peak 16–20 (det C ~18; det A rails from ~11)
     ~25+    decayed;  ~12 samples of tail
```
Pulser (random) events = flat pedestal, verified (avg-profile max dev < 8 ADC).
Latency is irrelevant for the random events (uncorrelated) — it is set to
frame the flash events.

Data rate: the 30 ms gate (duty ~1%) turns the 667 Hz pulser into a
comfortable ~5–6 Hz average of DREAM triggers. **Gate verified working
2026-07-11 PM** (see §Veto below) — but ONLY when the or_veto function type
is properly selected; `trigger_mode.py` handles this. For gate-less tests,
throttle the pulser to period 15 ms (67 Hz) — **do NOT set period 150 ms: it
silently kills the output** (generator range limit); design value is 1.5 ms
Poisson, width 100.

### Mode 3 — Scintillator trigger (`trigger_mode.py scint --singles|--both`)

| item | value |
|---|---|
| Trigger | M4.C = or_veto(lemo0 Singles [, lemo1 Doubles]) → M4.D = OR(lemo1) |
| Readout | **32 samples × 60 ns** |
| **Latency** | **35** (`0x0023`) |
| run_config_beam.py | `n_samples_per_waveform=32, sample_period=60, latency=35` |
| Trigger rate | Singles ~49 Hz beam-on / ~12 Hz off; Doubles ~0.7 Hz |

**Key physics: the MM pulse arrives ~1.4 µs BEFORE the trigger reaches the
TCM** (scint→rack logic→TCM cable loop), so **pulse peak = latency − 24**
(span latency−26 … latency−11). run_19-style latency 3 would put the pulse 24
samples before the window — empty waveforms.

Window anatomy at latency 35:
```
smp   0–8    baseline (first ~5 samples carry the window-start artifact — see §Gotchas)
      9      pulse rise
     11–13   PEAK  (det A sharp; B 11–14; C 10–14 + tail hump 22–27)
     ~25–31  tail
```
Confirmed at 32 smp on all three live detectors (7.5 k singles, det A n=497
x/y-coincident tracks). Only ~2–7 % of singles triggers have an MM track —
the rest are pedestal; that is expected (trigger particle usually misses the
MMs).

---

## 2. DREAM config mapping (dream_daq_control.py / run_test.py)

- latency → `Feu * Dream * 12  0x{latency:04X} 0x0000 0x0000 0x0000`
- 20 ns → `Feu * DrmClk RdClk_Div 4.0`, `WrClk_Div 2.0` (template default)
- 60 ns → `RdClk_Div 6.0`, `WrClk_Div 6.0` — **verified working** (the July-11
  early-AM `DrmClkConfig WrRd_Missmatch` was the FEU network outage, nothing else)
- samples → `Sys NbOfSamples`
- Test harness: `~/beam_july/test/latency_singles/run_test.py <variant>
  --minutes N --set "KEY=VALUE" ...` (refuses to run if another RunCtrl is up)

Latency ↔ signal position is exactly 1:1 in samples at both clock speeds
(verified over lat 40→80 at 20 ns and 3→35 at 60 ns). Offsets differ per
trigger path AND per sample period — never transfer an offset, re-measure.

---

## 3. Trigger IO layout — all six modules (as of 2026-07-11 evening)

Module N = 192.168.10.(239+N). All fw 2025.3.27.0 (M6 was upgraded from
2022.3.0.0 during the July-11 switch recovery). "pN" = front-panel LEMO N
(1-based) = SDK lemo N−1.

### M1 (.240) — 4× wall OR   [DISCR +30 mV, 50 Ω]
| Sec | function | inputs | outputs |
|---|---|---|---|
| A–D = walls 1–4 | `or` (lemos 0–3) | 428F pair-sums of SiPM wall N | out ch0 → M3 secN in-p1 (mono 50 ns); ch1 → M5.A scaler; **SEC_A ch3 = stray inverted RAW copy, unknown cable** |

### M2 (.241) — 4× scint OR   [DISCR −15 mV]  (2 plastics/wall, all four sections)
| Sec | function | inputs | thr | outputs |
|---|---|---|---|---|
| A–D = scint 1–4 | `or` (lemos 0–1) | 2 plastic scints | **−15 mV** (calibrated) | out ch0 → M3 secN in-p2 (mono 50); ch1 → M5.B (mono 100) |

**2026-07-14: liquid-scint plan REVERSED — back to 2 plastics/wall on all four
sections**, at a new calibrated threshold of **−15 mV** (replaces both the
−80 mV plastic level and the −50 mV liquid-scint level used 2026-07-13/14).
Applied by `setup_plastic_pairs.py` (read-back verified; snapshots
`dump_2026-07-14_{pre,post}_revert_plastic_pairs.json`). Walls A, D had briefly
been converted to a single liquid-scint input (Input 1 only, −50 mV,
`setup_liqscint_walls.py`, 2026-07-13) — Input 2 has been re-enabled and both
inputs OR'd again.

Legacy notes (superseded): the earlier equalizing thresholds (S1 −88, S2 −75,
S3 −122, S4 −60 mV) and the −80 mV nominal are both replaced by the uniform
−15 mV. `setup_liqscint_walls.py` remains in the repo in case the liquid-scint
swap is revisited later.

### M3 (.242) — 4× sector AND
| Sec | function | inputs | outputs |
|---|---|---|---|
| A–D = sectors 1–4 | `and` (lemos 0–1) | p1 = wall N (M1), p2 = scint N (M2); **both G&D gate=20 ns delay=0** (the 20 ns coincidence window lives HERE) | mono 30 ns → M4.A, M4.B, M5.C |

### M4 (.243) — trigger builder  ← `trigger_mode.py` touches ONLY C/D lemo enables
| Sec | function | inputs | outputs |
|---|---|---|---|
| A | Singles `or` (lemos 0,1,3,4) | p1,2,4,5 = sectors 1–4 | out → C p1; → M5.D p1 |
| B | Doubles `coincidence_gate` (lemos 0,1,3,4; width 50 ns, FIRST) | p1,2,4,5 = sectors 1–4 | out p1 (per-coinc) → C p2; out p2 (window copy) → M5.D p2 [should move to p3]; counters [TOTAL,CH1,CH2,CH4,CH5] |
| C | `or_veto` (reports as `or`) | p1 = Singles, p2 = Doubles, **p5 = M6.D pulser**, p6 = N93B timer line (implicit veto input) | out p1 → D p2 |
| D | final `or` | **p1 = PS/γ-flash trigger line** (0.29 Hz, PS-cycle-quantized, ~100 ns), p2 = C out | **out p1 = DREAM trigger cable → TCM** |

### M5 (.244) — scalers (counter ×4, lemos 0–3 only)
| Sec | inputs |
|---|---|
| A | walls 1–4 (M1 ch1 taps) |
| B | scints 1–4 (M2 ch1 taps) |
| C | sectors 1–4 (M3 taps) |
| D | p1 = Singles (M4.A), p2 = M4.B window copy (≈ Doubles), p3–4 = n/c(?) |

### M6 (.245) — γ-flash countermeasures + pulser   [fw 2025.3.27.0 since 2026-07-11]
| Sec | function | in-ch G&D (dump 2026-07-11) | role |
|---|---|---|---|
| A | `fanout` (TTL in) | — | PS/T0 fan-out |
| B | `fanout` | **in0: gate 50, delay 1260 ns** (retimed 2026-07-11, was 500; in1: delay 40) | → **mesh charge-injection** ×4 outs (mono 500) — the in0 delay is the injection-timing knob; 1260 ns = injection ~180 ns before flash rise, −38 % flash peak on det C |
| C | `fanout` (TTL out inv.) | in0: gate 100, delay 200 | → SiPM enable ×2 (mono 1000), blanks SiPMs during flash |
| D | `pulse_generator` | (in0 shows gd 9600 — unused for pulse gen) | **Poisson 1.5 ms / width 100** ≈ 667 Hz → M4.C p5 |

### Veto gate (M4.C p6) — N93B 30 ms timer — **VERIFIED WORKING 2026-07-11 PM**
Line behavior (operator-confirmed + measured): inverted-NIM, arrives at
PS-pickup time, **LOW for 30 ms = enable window**, HIGH otherwise (veto).
The 30 ms LOW is invisible to 20 µs LA frames (~1 % duty) — never conclude
"stuck high" from LA sampling. or_veto semantics (manual + behavioral):
**output = OR(enabled CH1,2,4,5) AND veto-line LOW**.

**⚠ THE SDK GOTCHA THAT HID THIS FOR DAYS**: `configure_or_veto()` does NOT
set the function type — it only writes lemo enables into whatever function
is loaded (the fw ignores the callback name; even `"banana"` returns
`Result:true`). You MUST first call
`set_section_function(SEC_C, FunctionType.FN_OR_VETO)`. Without it M4.C ran
as a plain OR and the veto input was inert (flipping the in5 invert flag
changed nothing). With FN_OR_VETO selected: pulser pass rate collapsed
6.9 → 0.38 Hz ≈ beam-pulse rate, and C.in5 read LOW in every passing frame.
`trigger_mode.py` now selects the type automatically.

Consequence for mode 3: with the veto genuinely gating, Singles/Doubles
rates DROP vs the numbers measured while it was inert (uniform-in-time
cosmics are blocked outside the ~1 % duty window; beam-correlated triggers
survive). Latencies are unaffected.

---

## 4. Gotchas (cost hours — read before touching)

- **Discriminator threshold floor: |threshold| ≥ 10 mV** — the N1081B input
  discriminators have a hardware minimum of 10 mV magnitude (+10 mV on
  positive-signal boards like M1/walls, −10 mV on negative like M2/scints).
  Do NOT program values below it (the 2026-07-15 ZS study probed −8 mV, which
  is invalid). Every scan/set script must validate `abs(mv) >= 10`;
  `systematic_threshold_scan_v3.thresholds_grid` now clamps to this floor.
  **Keep a margin above the floor too**: AT the floor a channel can sit inside
  its noise and the discriminator retriggers continuously — the counted edge
  rate then COLLAPSES instead of rising (inverted response). Observed live
  2026-07-16: plastic sector D at −10 mV read 112 Hz vs 601 Hz at −30 mV
  (D_R had just taken +117 V in the HV equalization), while A/B/C behaved.
  Practical rule: operate ≥ ~1.5× the floor where possible, and treat any
  NON-MONOTONIC rate-vs-threshold response as noise saturation, not physics.
  ⚠ The SiPM wall thresholds (M1, calibration-nominal 13–14 mV) sit within
  ~30–40 % of the floor — re-check monotonicity whenever they are lowered.
- **Window-start artifact**: every DREAM event's first ~5–10 samples carry
  decaying transients on all FEUs, at FIXED window position (does not move
  with latency), coincident across planes. Any early-window "signal" is fake
  unless it shifts 1:1 with latency. Analysis countermeasures in
  `~/beam_july/test/latency_singles/analyze_m3_coinc.py` (hot-channel mask,
  edge guard, rising-edge veto, latency-shift test).
- **Hot channel families** (adjacent strips): FEU01 307–310, FEU02 460–470,
  FEU04 42–56, FEU06 51–54, FEU07 244/500. Det B (FEU05/06) globally noisy.
- FEU↔detector map: **A=FEU03/04, B=05/06, C=07/08, D=01/02** (x,y).
- `check_completeness.py` hardcodes 400 samples — false "incomplete" flags on
  32/128-sample runs.
- `verify_trigger_paths.py` is a script (import-guarded — don't import it).
- Only one RunCtrl at a time; stop the main DAQ (`dream_daq` tmux) first.
- M6 pulse generator: period 150 ms silently disables output; 15 ms OK.
- HV context 2026-07-11: det D tripped (640 V → parked 100 V), det C current
  climbing at 690 V (1.5→6.4 µA over ~40 min) — both under observation.

---

## 5. Running an HV scan via `run_config_beam.py` (operational, 2026-07-15)

All three scans (flash / flash_random / scint) are driven by the ONE file
`run_config_beam.py`, which generates `config/json_run_configs/run_config_beam.json`
that daq_control reads. To run a **flash-mode HV scan** (as run_41, 3He target):

1. **Boards**: `.venv/bin/python n1081b/trigger_mode.py flash` then `… status`
   (expect "flash"). These are VOLATILE .243 settings — re-apply after any power
   cycle. For flash_random also confirm the M6.D pulser is ON (Poisson period
   1.5 ms / width 100 ≈ 667 Hz; `configure_pulse_generator`, see
   `setup_run30_trigger.py`) — it is usually left running.
2. **Mode block** in `run_config_beam.py` `dream_daq_info` — set to the mode's
   §1 values:
   - flash → `n_samples_per_waveform=400, sample_period=20, latency=60`
   - flash_random → `32, 60, 5`   ·   scint → `32, 60, 35`
3. **HV scan constants** (top of file): `RESIST_TOP / RESIST_STEP / RESIST_BOTTOM`,
   `SUBRUN_MIN`, `N_PER_POINT`, `SCAN_DRIFT`. run_41 (flash) = 560→400 V, −5 V,
   1×5 min/pt, drift 800. run_42 (flash_random) = 560→490 V, −5 V, 1×5 min/pt.
4. `n1081b_scan='off'` when the mesh is grounded/disconnected (no per-sub-run
   cycling; do NOT start `n1081b_scan_watcher.py`). Also set `resume`, `target_type`,
   `gas`. Sub-run name prefix (`flash_`/`frand_`/`scint_`) is cosmetic.
5. **Pedestals** `'latest'` — take a fresh set after any target/config change.
   Per-channel & sample-independent, so one set covers 32- and 400-smp runs.
6. **Launch**: GUI (iterate → generate → start), or
   `bash bash_scripts/start_run.sh run_config_beam.json`. NOTE: the run number
   auto-iterates ONLY through the GUI (`iterate_run_num.py`, resume-aware — it
   holds the name to resume an incomplete run); for a CLI launch set `run_name`
   explicitly so you don't collide with an existing/running run dir.
7. **Stop**: `bash bash_scripts/stop_run.sh` (cuts the current sub-run + ends the
   run cleanly). Delete a sub-run dir to force a re-take on the next resume run.
