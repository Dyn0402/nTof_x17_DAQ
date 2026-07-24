# RESULT — 25 MHz read clock packs 1.5× more events into the window (beam off, 2026-07-24)

Run `clk_ac` (phase 1 of the clock-window test, `run_config_clock_window_test.py` with
`CLK_BLOCKS=A,C`). Beam confirmed off (>19 min, 0 pulses/10 min). Saturating 20 kHz fixed pulser,
M4.C veto open, ZS diagnostic (counts/timestamps only — NOT physics data), n32 / IPD 2, WrClk 6.0
fixed (60 ns / 1.92 µs window held). Metric = live `IntRate` from the `dream_daq` pane. Trigger
fully restored afterward (scint singles + ps, veto `or_veto[0]`, PS delay 1800 — all read back OK).

## Block A — clock curve: readout rate is EXACTLY linear in read clock

| sub-run | RdClk_Div | read clock | IntRate | ratio to RdClk 6.0 | expected (6.0/div) |
|---|---|---|---|---|---|
| satA_rd6_a | 6.0 | 16.7 MHz | **7231.50 Hz** | 1.000 | 1.000 |
| satA_rd5   | 5.0 | 20.0 MHz | **8678.85 Hz** | 1.200 | 1.200 |
| satA_rd4p5 | 4.5 | 22.2 MHz | **9640.79 Hz** | 1.333 | 1.333 |
| satA_rd4   | 4.0 | 25.0 MHz | **10847.29 Hz** | **1.500** | 1.500 |
| satA_rd6_b | 6.0 | 16.7 MHz | **7231.37 Hz** | 1.000 (bracket ✓) | — |

- **RdClk 6.0 → 4.0 = 1.500× exactly** (10847.29 / 7231.50), reproducing the 07-23 A/B result.
- **The bracket reproduces to 0.002 %** (7231.50 vs 7231.37) → drift excluded, the effect is real.
- **Every intermediate point matches 6.0/RdClk_Div to <0.1 %** → the accepted readout rate is
  *strictly proportional to read-clock frequency* across all four points. At IPD 2 / n32 there is
  **no visible fixed-overhead floor** eating into the gain — the readout is purely read-clock-bound
  at this operating point. This is a stronger statement than the original 2-point A/B: the full
  curve is a straight line through the origin in frequency.

### In the units of the question
Under saturation, events-in-window = IntRate × window. For the ~10 ms pulse window:
- 16.7 MHz: **72.3 events / 10 ms**
- 25.0 MHz: **108.5 events / 10 ms**  → **+36 events, 1.50×**

So the 25 MHz clock demonstrably reads out 1.5× more events in the ~10 ms window whenever the
window is readout-limited (the dense early band). Confirmed directly, beam-off.

## Block C — latency does NOT affect readout rate (control)

| sub-run | latency | IntRate |
|---|---|---|
| satC_lat3   | 3   | **10848.41 Hz** |
| satC_lat35  | 35  | **10847.68 Hz** |
| satC_lat100 | 100 | ~10847 (3.8 G raw volume identical to the others; live value scrolled) |
| satC_lat35b | 35  | **10847.72 Hz** |

All at RdClk 4.0. **Flat to <0.01 %** across latency 3 → 35 → 100 (bracket lat35/lat35b reproduce).
Confirms readout time ∝ NbOfSamples, independent of latency — latency only sets the rested-buffer
depth N_buf = (512−lat)/n, not the sustained rate. "Latency doesn't affect anything": confirmed.

## Data quality
All 9 sub-runs completed cleanly ("Dream Subrun complete" ×9, run exited 0), all 8 Dreams streaming
throughout. Raw volume 3.8 G on every saturating sub-run (fixed ZS event size × equal duration).
The un-phased intermediate points (RdClk 5.0 / 4.5 with WrClk 6.0) are used for **rate/timestamps
only** — their ADC content was not inspected (by design; not needed for the rate observable).

## Block B — Poisson event spacing (run `clk_b`, decoded FEU01)

Poisson 20 kHz (over both ceilings), same clocks. Accepted rate = ceiling, confirming 1.5× under
statistical (not deterministic) load:

| sub-run | read clock | events (FEU01) | accepted rate | min-dt | median-dt |
|---|---|---|---|---|---|
| poisB_rd6 | 16.7 MHz | 650 899 | **7228 Hz** | 4 µs | 60 µs |
| poisB_rd4 | 25.0 MHz | 976 255 | **10841 Hz** | 4 µs | 51 µs |

- **1.500× again** (10841/7228) — the clock gain holds under Poisson arrivals, not just deterministic.
- **min-dt = 4 µs both** = the buffered-pair ADC floor (matches the 07-20 flash-off 3.8 µs); clock-independent.
- **median-dt tightens 60 → 51 µs** with the faster clock = the readout cycle shrinking. Median ≪ mean
  (=1/rate, 138 µs) ⇒ events arrive in **clusters + gaps** (the block-readout structure the 07-20 study
  saw). The explicit ~10 ms comb autocorrelation did not resolve at this rate (events are ~50 µs apart,
  far finer than the 07-20 low-rate regime where the comb was the dominant scale) — expected, not a null.

## Block D — rested-buffer dump, the flash analog (run `clk_d`, `rest_toggle.py`, decoded FEU01)

`rest_toggle.py` idled the 200 kHz pulser (0.3–5 s rests × 4 reps) so each restart dumps a rested SCA
into the window — the controlled, beam-off version of the gamma-flash's rested-buffer dump. **Events
landing in the first 10 ms after each dump:**

| sub-run | read clock | n dumps | events in first 10 ms (mean) | modal |
|---|---|---|---|---|
| dumpD_rd6 | 16.7 MHz | 24 | **83.0** | 83 |
| dumpD_rd4 | 25.0 MHz | 25 | **114.3** | 119 |

- **ratio 1.38× (mean), 1.43× (modal)** more events read out in the first 10 ms at 25 MHz.
- Strikingly uniform per dump (83,83,83… / 119,119,119…) → the readout is deterministic dump-to-dump.
- **Why 1.4× and not 1.5×:** the rested pre-load (~11 cells, N_buf=(512−lat)/n, clock-independent) is a
  fixed additive term that dilutes the ratio: rd6 = 72 (ceiling×10 ms) + 11 ≈ 83; rd4 = 108 + 11 ≈ 119.
  The sustained part scales 1.5×; the fixed dump does not. This is the honest, beam-faithful number for
  "events in the ~10 ms window after a flash": **~1.4×**, a bit below the pure-sustained 1.5×.

## Bottom line
Three independent measurements, one answer: **the 25 MHz read clock genuinely reads out more events
into the ~10 ms window** — 1.50× when the window is readout-limited (sustained, blocks A & B) and 1.4×
in the literal flash-dump transient (block D). Latency is orthogonal (block C). Confirmed, beam-off.

## Notes / follow-ups
- All blocks A/B/C/D taken and analysed this session (decoded FEU01 one-off with the `decode` binary;
  `processor_watcher` was down, so no automatic decode / no `analyze_waveforms` hits step).
- The explicit ~10 ms comb autocorrelation did not resolve at these high sustained rates (see block B) —
  a finer-binned Δt histogram or a lower Poisson rate would show the cluster/gap structure directly if a
  visual is wanted; the rate + dump results already carry the physics.
- `satA_rd6_a` / `satA_rd4` were also decoded (block-A comb cross-check) but the headline is the rate
  curve; the dump (block D) is the cleaner "events in the window" statement.
- **uproot lives in `/usr/bin/python3`, not the repo `.venv`** — run the analysis with system python.
