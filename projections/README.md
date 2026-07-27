# Statistics projections

Cumulative trigger counts against a frozen projection, so we can tell whether the
run is on track — and, later, how well the projection actually predicted things.

```bash
python run_stats.py           # what we have so far
python schedule.py            # beam availability from the schedule
python make_projection.py     # freeze a projection (saved/projection_<date>.json)
python plot_progress.py       # plots/progress_<date>.png
```

Nothing here touches hardware or the DAQ. It only reads finished run directories
and the beam watcher's CSVs, so it is safe to run at any time.

## The idea

Make a prediction today, then find out how well it held. `make_projection.py`
writes a **frozen** JSON — it is never recomputed. Re-run it weekly (or whenever
the schedule changes) to add another projection; `plot_progress.py` draws them all
against the real curve, so a projection that was too optimistic stays visible
rather than being quietly corrected.

The frozen file records the rate parameters, the schedule and the anchor it used,
so a projection that missed can be diagnosed rather than just noted.

## The ledger — statistics that outlive the disk

`stats_ledger.csv` is one committed row per sub-run: run, sub-run, beam type, start
and end (both as local timestamps **and** stored epochs), live hours, event count and
where the timestamp came from.

It exists because **runs get deleted**. Once a run is safely on EOS the space manager
frees it, and run_1 through run_66 are already gone — only run_67+ are still on disk.
Without a ledger those event counts would vanish and the cumulative total would
silently shrink.

So every scan is merged in and **rows are never dropped for being absent from disk**.
Disk wins on conflict, since a re-processed sub-run can legitimately change its count.
`run_stats.py --no-sync` reads the ledger alone and reproduces the same totals, which
is the check that it really is self-sufficient.

Two subtleties worth knowing:

- **Timestamps survive cleanup via the RunCtrl log.** The `datrun_...` filenames go
  with the `.fdf` files, but `RunCtrl_YYMMDD_HHhMM.log` stays behind and carries the
  same stamp (verified identical on run_79). Without that fallback every cleaned run
  dates to the moment it was *cleaned* — which stacked runs 67–75 onto one instant and
  turned the cumulative curve into a cliff. `t_source` records which was used.
- **Epochs are stored, not re-derived.** Recomputing them from the naive local
  timestamp strings would shift every historical row when the machine leaves CEST for
  CET in October, and pandas reads naive strings as UTC anyway.

## Two subsets, two questions

| | set | why |
|---|---|---|
| **Cumulative total** | every recorded run | "how much have we recorded" — all of it counts |
| **Forward rate** | `rate_first_run` only | "what will we record from here" — must be the current production point |

Leaving the rate fit on the whole history would dilute it with the old HV, threshold
and latency scans, which ran at deliberately bad settings: the full history averages
**79 events/pulse against production's 88**. Set `rate_first_run` in `schedule.json`
and bump it when the production configuration changes.

## What counts

- **Beam data only.** Beam vs cosmics comes from `beam_type` in each run's
  `run_config.json` (`neutrons` vs `cosmics`), not from sub-run names — those change
  between runs, `beam_type` doesn't. Cosmics get their own little counter on the
  bottom panel and are never mixed into the beam total.
- **From run_79 onward** by default (`--first-run`).
- **Completed sub-runs only.** Event counts come from the per-FEU number in the
  RunCtrl log each sub-run leaves behind. A sub-run in progress has no log yet and
  is skipped — not counted as zero, which would otherwise make the rate sag every
  time you looked mid-sub-run.
- Sub-runs inside a beam run count even if beam happened to drop during them: they
  are real recorded data. Beam-on time is handled separately, in the rate model.

## The rate model

Deliberately split into two factors, because they fail differently:

| | measured | source | what it is |
|---|---|---|---|
| events per pulse | **~88** | `rate_first_run` only | detector + trigger performance — the trustworthy half |
| pulses per hour | **~972** | every beam sub-run in the pulse window | what the machine delivers — the half to watch |
| → events per beam hour | **~85k** | | the product |

**The two factors come from different sets, on purpose.** Events/pulse is a property
of the detector, so it must come from the current configuration. Pulses/hour is a
property of the machine and is noisy hour to hour, so it must come from a wide
window: fitting it on run_86's first hour alone — which happened to catch poor beam,
605 pulses/h against a normal ~1050 — halved the projection for a reason that had
nothing to do with the detector.

`pulses/hour` is a median over sub-runs that were *fully* beam-on, and sub-runs with
zero pulses are dropped. A sub-run that caught the start of a beam stop has a real
event count but only partial beam; averaging it in would understate the rate we can
expect while beam is up, and the schedule already accounts for downtime — counting it
twice would double-penalise the projection. Dropping zero-pulse sub-runs also excludes
those from before the beam CSVs begin, which would otherwise contribute events with no
pulses and inflate events/pulse without bound.

Cross-check on run_79 (Hwm 2), where statistics are ample: 103.8 events/pulse × 1054
pulses/h = 109.4k/beam-hour against a directly observed 110.5k, within 1%. run_86's
87.6 events/pulse is 0.84 of that — matching the ~16% fewer triggers per spill measured
independently from the flash-anchored distribution.

## The schedule

Edit `schedule.json`. Known beam stops and accesses go in `downtimes`; the
`generic_daily_downtime` block budgets a nominal access per day past the last thing
we actually know about. Overlapping entries are merged before subtracting, so a
budgeted access that collides with a real one is not removed twice.

`efficiency` (0.85) is the pessimism knob — overall data-taking efficiency applied
to beam-available time, covering setup, re-takes, DAQ hiccups and sub-run
turnaround. The plot also draws the same rate at 100% as a dotted line, so the
spread between "what we assume" and "if nothing goes wrong" is visible.

As of the 2026-07-27 schedule: **332.5 h elapsed → 301.5 h beam available → 256.3 h
of data taking at 85%.**

## The three panels

1. **Cumulative beam triggers** against every frozen projection.
2. **Trigger rate** — the expected instantaneous rate, flat at **30.4 Hz** while
   beam is up and zero through each scheduled stop, with the expected **cosmic**
   rate (24.2 Hz) filling those notches. Recorded sub-runs are drawn as one flat
   bar each, because a sub-run's rate is an average over it, not a sample.
3. **Cosmics**, recorded and projected.

## IPC yield vs time since flash

```bash
/usr/bin/python3 ipc_yield.py            # plot from cache
/usr/bin/python3 ipc_yield.py --refresh  # re-extract from the ROOT files (~6 min)
```

**Note the interpreter.** This one needs `uproot`, which lives in the system python,
while the rest of `projections/` needs `pandas`, which lives in `.venv`. The two
dependency sets are disjoint and deliberately kept that way — `ipc_yield.py` reads
the frozen projection straight from its JSON rather than importing `live.py`, so
neither environment has to grow the other's dependencies.

A true overlay on twin y axes, matching the house format of
`analysis/flash_comb/tools/ipc_spectrum_vs_runs.py`. Two scales in one frame is
normally the wrong call — the crossing points mean nothing — so each axis, its label
and its ticks are colour-matched to its curve, and the regions are what is actually
being compared. IPC is the light-blue field behind; the measured triggers are the
darker red line in front.

Log x: the regions come out roughly even width so their labels fit inline, and the
thermal peak stays legible, which a linear axis out to 80 ms would not manage.
(`make_figure("linear", ...)` still works if you want the true spacing back — it
switches to a range-only band tag plus one aligned table, since on a linear axis
1–3 ms is 2.5% of the frame.)

**Expected IPC** — `MX17_Full_Geant/analysis/reweight/ipc_ingate_spectrum.npz`, the
in-gate production spectrum: the sub-keV thermal campaign reweighted by ENDF/B-VIII.0
σ_nγ/σ_np, 4.1e8 effective counts. Taken **verbatim** — `t_ms` and
`dNdt_ipc_per_pulse_per_ms` straight out of the file — so this plot cannot drift away
from the `flash_comb` ones. Integral 6.58e-05 IPC/pulse = 1.27 IPC/day.

⚠ Do **not** rebuild this curve by integrating the raw six-decade table
(`thermal_captures_subkev_full.json`) by hand. That gives a monotonic 1/t staircase
and **loses the thermal peak entirely** — an earlier draft of this script did exactly
that and the resulting curve was qualitatively wrong.

The peak search starts above `THERMAL_SEARCH_MS = 3.5`, as the house script does: the
global maximum sits on the epithermal shoulder at the 1 ms gate edge, and labelling
that "the thermal peak" names the wrong feature at the wrong energy (1988 meV instead
of 71 meV).

The spectrum **stops at 31.6 ms** (2 meV, its lowest energy). Past that the plot is
greyed — absence of simulation, not absence of IPC — so the 25–80 ms region is only
partly covered, flagged with `*`.

**Measured yield** — set by `RUN` / `SUBRUNS` at the top of the script, currently
**run_86 / stat090_0000**: the production point after run_82 moved the watermark to
Hwm 1 / Lwm 0, so it is what the detector actually delivers now. FEU 01 only (every
FEU reads every event, so one FEU is the whole event list at ⅛ the I/O). Anchored on
the **gamma flash itself**, tagged by ADC saturation, not on the first recorded
event: when the flash is missed the first recorded event is the 1 ms gate opening
instead, and anchoring on it would smear the axis by a millisecond. Spills with no
captured flash are dropped — run_86 keeps 606 of 606.

Caches are keyed on (run, sub-run selection), so pointing this at a new run cannot
silently reuse the previous histogram.

Region totals scale each region's measured share to the frozen projection, so
**re-run this after freezing a new projection** or the `→ xM` figures will still
refer to the old one.

| region | triggers | /spill | → by Aug 10 | of the IPC |
|---|---|---|---|---|
| 1–3 ms | 8.5% | 7.3 | 2.53M | 25% |
| 3–8 ms | 19.4% | 16.7 | 5.78M | **56%** |
| 8–15 ms | 22.5% | 19.4 | 6.70M | 17% |
| 15–25 ms | 17.7% | 15.2 | 5.27M | 2% |
| 25–80 ms | 31.9% | 27.5 | 9.50M | ~0%* |

The two columns pull in opposite directions, which is the point of overlaying them:
**56% of the IPC lands in 3–8 ms, where 19% of the triggers are**, while a third of
all triggers sit in 25–80 ms, where the expectation is essentially nothing.

Against run_79 (Hwm 2) the Hwm 1 point trades total rate for placement: 86 triggers
per spill in 1–80 ms against 102, i.e. ~16% fewer, but the 3–8 ms share rises from
16.3% to 19.4% — more of what is left lands where the IPC is.

## run_82 — watermark × inter-packet-delay

```bash
/usr/bin/python3 run82_comb.py            # needs uproot: system python
/usr/bin/python3 run82_comb.py --refresh
```

One panel per (Hwm, IPD) setting, drawn like `ipc_yield.py`. run_82 takes each of the
four points **twice in interleaved order** (h2i5, h1i2, h2i2, h1i5, h1i5, h2i2, h1i2,
h2i5) to cancel beam drift, so the repeats are pooled — and their individual CVs are
printed, because pooling two repeats that disagreed would hide what the interleaving
was there to expose.

The figure of merit is the CV of the trigger yield across **1–10 ms in 100 µs bins**.
The bin width is not a free choice: the same band reads CV 0.42 at 0.5 ms bins and
0.87 at 0.1 ms, so a coarse binning hides the comb entirely.

| setting | spills | trig/spill (1–10 ms) | CV | starved | repeats |
|---|---|---|---|---|---|
| Hwm 2 / IPD 5 | 74 | 31.1 | 0.85 | 27% | 0.86, 0.88 |
| Hwm 2 / IPD 2 | 106 | 33.3 | 0.94 | 41% | 0.96, 0.93 |
| **Hwm 1 / IPD 5** | 105 | 30.4 | **0.41** | **3%** | 0.48, 0.39 |
| Hwm 1 / IPD 2 | 104 | 31.4 | 0.55 | 7% | 0.61, 0.52 |
| run_79 reference | 17,037 | 32.4 | 0.86 | 28% | — |

**Hwm is the lever, not IPD.** Dropping Hwm 2 → 1 halves the CV and takes starved bins
from 27% to 3%, for about 6% fewer triggers in the band. IPD 5 → 2 makes it slightly
worse at both watermarks.

Two things that make the result trustworthy: the h2i5 control reproduces the run_79
production reference (CV 0.85 vs 0.86) on 1/230th the spills, and the two repeats of
each setting agree.

⚠ `stat090_h2i5_0007` is short — 39 spills at **51.3% flash capture** and 1,772 events
against ~5,000 for every other sub-run. It looks truncated. That is why Hwm 2 / IPD 5
pools only 74 spills against ~105 elsewhere. It does not change the conclusion (that
setting agrees with the high-statistics run_79 reference), but it is worth knowing
before quoting its numbers on their own.

## Cosmics during beam-off

Cosmic triggers are projected to accumulate through the scheduled downtime, at the
rate measured from run_80 (**24.2 Hz**, 87k/hour). Over the 31 h of scheduled
beam-off that is **2.40M cosmics**, against 109k recorded so far.

This assumes a beam stop is spent taking a cosmic run, which is what run_80 was. If
a downtime is instead spent with the detectors off — plausible for the long Tuesday
access — the cosmic curve over-predicts. It is a plan, not a measurement. The same
85% efficiency is applied, on the same reasoning as for beam.

## Caveats

- `pulses/hour` is measured over a single overnight stretch of run_79. It is the
  weakest input; if the machine changes its supercycle or destination sharing, the
  projection will drift even though events/pulse stays put. That is exactly what
  the weekly re-projection is for.
- The generic 2 h/day access budget past 2026-08-04 is a guess, and it compounds:
  over the last five days it removes 10 h. If real accesses turn out shorter, we
  beat the projection for a reason that has nothing to do with the detector.
- Sub-run start times come from the `datrun` filenames (minute resolution) and
  durations from the planned `run_time`, not from measured start/stop. Good to a
  minute or two per sub-run — irrelevant at the scale of the projection, but it is
  not a precise live-time accounting.
- Timestamps are local wall-clock throughout. Beware `pandas.Timestamp.timestamp()`,
  which reads a naive value as UTC while `datetime.timestamp()` reads it as local —
  a silent 2 h shift under CEST. `run_stats` carries explicit `t_start_unix` /
  `t_end_unix` columns computed from plain datetimes for exactly this reason.
