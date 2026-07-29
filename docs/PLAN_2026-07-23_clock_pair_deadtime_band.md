# Deferred test — read-clock A/B at 0.50 MIP (the deadtime-shadow band)

**2026-07-23.** Built and ready; NOT yet run (beam went to another run). Launch when a
~30-min beam-on slot is free.

- Run config: `run_config_clock_pair_050mip.py` → `run_71`
- Analysis: `~/beam_july/analysis/flash_timing_threshold/`
- Prereqs: same board setup as run_69/70 (see the config docstring).

---

## The finding that motivates it (run_69 + run_70, 2026-07-23)

We ran two quick beam tests of the new 25 MHz DREAM read clock:

- **run_69** — read-clock A/B (25 vs 16.7 MHz) at a fixed 0.90 MIP plastic threshold.
- **run_70** — plastic-threshold plateau scan (2.00 → 0.50 MIP, all at 25 MHz).

**Totals said the clock does nothing.** run_69 beam-normalised: 15.77 (25 MHz) vs 15.79
(16.7 MHz) accepted per 1e12 protons — identical. run_70: total per-flash yield rises
monotonically as threshold drops (37 → 103) with **zero FEU drops** throughout. Both are
the textbook trigger-limited result — the clock is headroom, not yield, when the offered
rate is far below the readout ceiling.

**But the flash-anchored time structure told a different story.** Anchoring every DREAM
event to its gamma flash (t0 = burst start; validated: flash rails all detectors at ~29900
saturated hits vs ~88 for singles, and the first single lands at 1.01 ms = the N93B gate),
the **4–8 ms band yield FALLS as the threshold drops** while the total rises:

| MIP | 1–4 ms | **4–8 ms** | 8–20 ms | 20–81 ms | total |
|----:|-------:|-----------:|--------:|---------:|------:|
| 2.00 | 8.2 | **10.9** | 9.8 | 8.4 | 37 |
| 1.41 | 15.5 | **8.3** | 21.0 | 29.7 | 74 |
| 1.13 | 16.4 | **6.7** | 23.6 | 40.5 | 87 |
| 0.90 | 16.6 | **6.3** | 24.4 | 46.5 | 94 |
| 0.70 | 16.4 | **6.3** | 24.7 | 53.8 | 101 |
| 0.50 | 16.5 | **6.1** | 25.2 | 55.5 | 103 |

(events per flash; the 1.41 closing bracket reproduced the opening to 0.6%, so this is real,
not beam drift.)

**Why the 4–8 ms drop can only be deadtime.** Lowering a discriminator can *only add*
triggers — a 4–8 ms hit above −262 mV (2.0 MIP) is also above −66 mV (0.5 MIP). So the ~4.8
events/flash that vanish from 4–8 ms between 2.0 and 0.5 MIP were **recorded then lost →
vetoed → deadtime**. Mechanism: the **1–4 ms window saturates at ~16.5/flash** (flat below
1.13 MIP — a DAQ cap, not physics), and its per-event readout **spills past 4 ms**,
shadowing the 4–8 ms band. The late window (20–81 ms) is sparse enough to escape the shadow
and dominates the total — which is exactly why the *totals* looked clock-independent.

## The prediction

Per-event readout ≈ **262 µs at 25 MHz** vs **314 µs at 16.7 MHz** (n32, IPD 5). The 1–4 ms
readout demand is ~144% of the window at 25 MHz but ~173% at 16.7 MHz, so the slower clock's
shadow reaches further into 4–8 ms.

> **At 0.50 MIP, the 4–8 ms band should hold measurably MORE events at 25 MHz than at
> 16.7 MHz — a first-order (tens-of-%) effect, not the sub-noise wash-out the totals show.**

This is the one place the 25 MHz upgrade converts to yield, and the one falsifiable test of
the deadtime-shadow model. A null result says even the dense early band is not
readout-limited at these rates (and the clock is pure headroom everywhere).

## How to read run_71 out

**The FEU trigger counters are BLIND to this.** BUSY-vetoed triggers never arrive, so both
clocks show ~0 drops and similar totals — the loss is in *where* events land, not *how many*.
Do **not** conclude "no effect" from `feu_trig_counters.py`.

1. Data sanity first on every 25 MHz point (above the ASIC's rated 20 MHz RCk): baseline
   ~256, no processor decode errors, eventId gaps ~0.
2. Run the flash-anchored band analysis grouped by **clock** instead of threshold:
   `~/beam_july/analysis/flash_timing_threshold/` — add an `analyze_run71.py` that loads the
   six sub-runs, splits new (25 MHz) vs old (16.7 MHz), and compares 4–8 ms events/flash. The
   new/old brackets give the error bar.

## Follow-ons if it confirms

- Repeat at 0.70 MIP (partial shadow) to map the effect vs threshold.
- The operational lever if we want early-window yield without the clock: a shallower FEU
  watermark spreads the 1–4 ms burst (run_67's Hwm 2), at a total-yield cost — the trade
  the deadtime-shadow model now lets us reason about quantitatively.
