# METHOD — setting the beam→cosmics threshold from historic beam data

**Question.** `mode_watcher.py` swaps the DAQ to cosmics after the beam has been away
`BEAM_DOWN_MIN`. That was initially a guess (5 min). How long are n_TOF beam stops
*actually*, and what does that say the threshold should be?

**Answer, in one line.** 5 minutes is right, and the data says why: the self-resolving
hiccups die out at ~4 min, and the conditional-survival curve has its knee in the same
place and plateaus immediately after.

Reproduce: `.venv/bin/python beam_monitor/analyze_stop_durations.py`

---

## Data and method

27 daily CSVs from `~/beam_july/slow_control/beam_intensity/`, **2026-06-30 → 07-27**:
642 h, 1,114,433 rows, 511,358 pulses above the 50 e10 threshold.

A *pulse* is a logged point above `pulse_threshold_e10`. A *stop* is a gap between pulses
longer than **80 s** — the boundary from `beam-off-threshold-empty-band`, which found the
gap distribution to be cleanly bimodal: longest gap with the beam demonstrably running
38.4 s, shortest genuine stop 86.4 s, nothing at all in 45–75 s. Nothing below is
sensitive to where in that empty band the cut sits.

### ⚠ Two traps, both of which would have corrupted the answer

1. **A low intensity reading does not mean beam off.** Near-zero points keep being logged
   every ~1.2–2.4 s both inside structural gaps and during real stops. Only the gap
   between *pulses* carries the information.
2. **A gap in the pulse series can mean our own logger died**, which is not beam behaviour.
   Every candidate gap is therefore required to have CSV rows arriving throughout it (no
   row-to-row gap over 120 s). This rejected **3 of 133** candidates — including one of
   **9.8 h**, which on its own would have badly skewed the tail and the mean.

**130 real stops**, 4.9/day, totalling **115.5 h = 18.0% of wall-clock** (4.3 h/day).

---

## Result 1 — the duration distribution is not unimodal

```
   1-2 min  23  #######################
   2-3 min  18  ##################
   3-4 min  13  #############
   4-5 min   4  ####
   5-6 min   2  ##
   6-7 min   1  #
   7-8 min   2  ##
   8-9 min   3  ###
  9-10 min   3  ###
```

percentiles: p10 1.7 · p25 2.3 · **p50 8.6** · p75 47.4 · p90 114 · p95 251 min
(min 1.4, max 726, mean 53.3)

There is a dense population of short stops that **falls off a cliff between 3 and 5
minutes** (13 → 4 → 2 per minute-bin), and then a flat, very long tail. "Typical" is a
misleading word here: the median stop is 8.6 min but the mean is 53 min, because the tail
runs to 12 hours.

## Result 2 — conditional survival, which is the actual decision quantity

The question at decision time is not "how long are stops" but *"it has already been down
T minutes — what happens next?"*

| T (min) | still down | P(down ≥15 min more) | median remaining | mean remaining |
|--------:|-----------:|---------------------:|-----------------:|---------------:|
| 2 | 107 | 51% | 16.3 min | 62 min |
| 3 |  89 | 61% | 26.3 min | 74 min |
| 4 |  76 | **70%** | 37.2 min | 86 min |
| **5** | **72** | **72%** | **39.7 min** | **89 min** |
| 6 |  70 | 73% | 39.0 min | 91 min |
| 7 |  69 | 72% | 38.3 min | 91 min |
| 8 |  67 | 72% | 38.2 min | 93 min |
| 10 | 61 | 79% | 46.5 min | 100 min |
| 20 | 52 | 81% | 48.6 min | 106 min |

The information is nearly all gained by T = 4–5, and the curve then **plateaus** (72% at
5, 6, 7 and 8 alike). Waiting past 5 min buys almost nothing.

At T = 2 it is a coin flip — you would be tearing down a good beam run on a hiccup that
would have fixed itself, half the time.

## Result 3 — the trade-off, and what it costs to be wrong

A switch costs ~70 s of beam at the *return* changeover (≈8 s detection + ≈60 s of `--go`).
Switching to cosmics itself costs no beam, because the beam is already gone.

| T | switches | /day | cosmic h recovered | beam h lost | ≥1 full 15-min sub-run |
|--:|---------:|-----:|-------------------:|------------:|-----------------------:|
| 2 | 107 | 4.0 | 111.3 | 2.08 | 51% |
| 3 |  89 | 3.3 | 109.7 | 1.73 | 61% |
| 4 |  76 | 2.8 | 108.3 | 1.48 | 70% |
| **5** | **72** | **2.7** | **107.0** | **1.40** | **72%** |
| 7 |  69 | 2.6 | 104.7 | 1.34 | 72% |
| 10 | 61 | 2.3 | 101.4 | 1.19 | 79% |
| 20 | 52 | 1.9 |  92.0 | 1.01 | 81% |

Recovered cosmic time is remarkably flat across the whole range (111 h → 92 h) because the
total is dominated by the long tail, not by the threshold. So the threshold is **not** a
"how much cosmic data do we get" decision — it is purely a "how often do we churn the DAQ
for nothing" decision. That is what the survival curve answers, and it says 5.

## Result 4 — sub-run length is the other, independent lever

Whether a cosmic stint is *useful* depends on completing at least one sub-run: a
manually-stopped partial sub-run gets no `.subrun_complete` and pins the run against
space_watcher cleanup (see `switch-mode-and-manual-stop-marker`).

Fraction of switches yielding ≥1 **complete** cosmic sub-run:

| T \ sub-run | 5 min | 10 min | 15 min | 20 min |
|------------:|------:|-------:|-------:|-------:|
| 3  | 75% | 65% | 61% | 54% |
| **5** | **85%** | **78%** | **72%** | 67% |
| 7  | 86% | 80% | 72% | 65% |
| 10 | 92% | 85% | 79% | 72% |

At the current 15-min cosmic sub-run and T = 5, **72%** of switches produce at least one
complete sub-run. Dropping the cosmic sub-run to **10 min raises that to 78%, and 5 min to
85%**, without touching the threshold or losing any beam. That is the cheaper lever if the
28% "switched but got only a partial" rate turns out to be annoying in practice —
`switch_mode.MODES['cosmics']['gen']` sets `SUBRUN_MIN`.

## Conclusion

Keep `BEAM_DOWN_MIN = 5`. It sits immediately past the end of the self-resolving-hiccup
population and at the knee of the conditional-survival curve, and everything from 5 to 8
minutes is statistically indistinguishable. Do not drop below ~4 min. Going above ~10 min
just adds idle time for no gain in certainty.

⚠ Re-run `analyze_stop_durations.py` after any machine schedule change — this is a
property of the accelerator, not of our DAQ.
