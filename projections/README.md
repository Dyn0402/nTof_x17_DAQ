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

| | measured | what it is |
|---|---|---|
| events per pulse | **~104** | detector + trigger performance. Flat to ~1% across all 16 hours of run_79 — the trustworthy half |
| pulses per hour | **~1054** | what the machine delivers. Moves with supercycle and destination sharing — the half to watch |
| → events per beam hour | **~109k** | the product |

`pulses/hour` is a median over sub-runs that were *fully* beam-on. A sub-run that
caught the start of a beam stop has a real event count but only partial beam;
averaging it in would understate the rate we can expect while beam is up, and the
schedule already accounts for downtime — counting it twice would double-penalise
the projection.

Sanity check: `events_per_beam_hour` (109.4k) lands within 1% of the directly
observed `events_per_hour` (110.5k), which is the sign the decomposition is sound.

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
