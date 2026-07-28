# Inferring H4 accesses from the T2 TAX

Added 2026-07-27 to the (temporary) **SPS** tab, in the *H4 Barrier — T2 TAX*
panel. Remove it with the rest of the `sps_monitor` package.

## The variable

```
XTAX_022_023:POSITION_MEAS      # mm
```

Parks near **−140 mm** with beam going to H4 and drives full stroke to
**+141 mm** to block it. A stroke takes about 6 minutes.

**Watch the name.** H4, H6 and H8 have **no `XB<line>.XTAX` variable at all** —
only H2 (`XBH2.XTAX.021.*`), M2 (`XBM2.XTAX.061.*`) and P42
(`XBP42.XTAX.043.*`) publish a TAX under the line prefix. Building a per-line
panel by globbing `XBH4.%` silently misses H4's barrier, which is the single
most diagnostic signal for that line.

## How it was validated

Found blind: asked which NXCALS variables correlate with a known access on
2026-07-27, ~14:00–15:00.

| Evidence | Observation |
|---|---|
| `XTAX_022_023:POSITION_MEAS` | −140 → +141 mm over **14:07–14:13**, back **15:26–15:29** |
| `XBH4.BEND.022.{053,083,117}:I_MEAS` | ~477 A → **0 A** (the other 27 H4 magnets steady) |
| 26 H4 flux counters | **exactly 0** from 14:10 to 15:25 |
| H2 / H6 / K12 / M2 / P42 | unaffected — only a shared ~0.43 blip at 14:00 that recovers |
| `SPSQC:EXTRACTED_INTENSITY` | FTARGET extraction continued throughout — nothing machine-wide |

## Two analysis traps

1. **Pick the witness counters carefully.** During the confirmed access
   `XBH4.XSCI.022.130:COUNTS` only fell to 0.098 of baseline and
   `XBH4.EXPT.HNA162.005:COUNTS` did not move at all (ratio 1.00) — they see
   more than the H4 line. Counters that genuinely track H4 flux, all reaching
   0.0000–0.0015:
   `XBH4.XSCI.022.453:COUNTS`, `XBH4.XSCI.022.480:COUNTS`,
   `XBH4.XSCI.022.466:COUNTS`, `XBH4.EXPT.GIF.001:COUNTS`,
   `XBH4.EXPT.GIF.003:COUNTS`.
2. **Normalise per counter, then take the median.** Summing a line's raw counts
   lets one big channel dominate and showed only a 30 % dip where the truth was
   a total collapse.

Also: NXCALS returns these counters as `numpy.int32`, which is **not** a python
`int`, so `isinstance(v, (int, float))` drops every one of them. Coerce with a
bare `float(v)` in a `try`.

## What the tab shows

- **Live state** — `open` / `blocked` / `moving`, from `config/sps_state.json`
  (`h4_tax` block), refreshed on the SPS monitor's poll.
- **24 h trace** of the position with the open/blocked thresholds drawn.
- **Blocked-span table** — these are **access _candidates_**. The position says
  the line is blocked; it does not say why. Confirm against the witness counters
  above before calling a span an access.

Data path: `beam_watcher` → `SpsSpillMonitor._poll_tax()` → per-day CSV
`~/beam_july/slow_control/sps_spill/h4_tax_YYYY-MM-DD.csv`
(`timestamp,unix_ts,position_mm,state`, ~20 k rows ≈ 1 MB/day) →
`/sps/tax_history?hours=N` → the panel. Span detection is
`tax_blocked_intervals()` in `sps_monitor/sps_spill_controller.py`: a span opens
when the TAX **leaves** open (not when it arrives at the far end), spans closer
than 10 min are merged, and anything under 5 min is dropped as pure motion.

History was backfilled into those CSVs from NXCALS: 07-20 → 07-27 initially, then
back to **07-14** on 07-28, so the panel opens with two weeks of context. The
backfill tool is `sps_monitor/backfill_tax_nxcals.py` (safe to re-run, dedups on
`unix_ts`, refuses today's file while the watcher is appending to it).

The **P2 sibling DAQ (banco) shows this same panel by proxying this box** — it is
not TN-trusted and cannot reach NXCALS, so it holds no TAX data of its own. See
`H4_BARRIER_ON_BANCO_2026-07-28.md`.

## Inferred spans, 2026-07-20 → 07-27

25 of 28 candidate spans had H4 flux collapse below 2 % of baseline. The cadence
is strikingly regular — roughly **09:00, 14:00 and 21:00 daily**, 60–100 min each:

| Day | Spans (H4 flux → 0) |
|---|---|
| Mon 07-20 | 09:00–10:24, 14:07–15:50, 18:37–18:46, 19:18–20:45, 22:01–22:50 |
| Tue 07-21 | 08:59–10:42, 14:32–15:42, 21:16–22:16 |
| Wed 07-22 | **08:08–19:47 (11.6 h)**, 23:04–23:28 |
| Thu 07-23 | 09:29–11:55, 13:24–14:48, 21:07–22:27 |
| Fri 07-24 | 10:01–11:28, 15:23–16:40, 21:03–21:42, 22:52–23:24 |
| Sat 07-25 | 09:01–10:08, 14:16–15:40, 21:07–22:16 |
| Sun 07-26 | 09:01–10:13, 14:02–15:15, 21:03–22:13 |
| Mon 07-27 | 09:02–10:08, **14:07–15:30** |

Not accesses: 07-20 11:34–11:45 and 07-21 16:55–17:10 (flux only fell to ~3–4 %),
and 07-26 22:34–22:44 (flux *higher* during the span).
