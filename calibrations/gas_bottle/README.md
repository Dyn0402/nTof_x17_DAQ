# Argon bottle pressure log

Bottle pressure of the argon supply, read off photographs of the panel gauge.

Unlike the rest of `calibrations/`, this is not a detector constant — it is an
**operational log that grows over time**, one row per photo. It lives here
because it is committed reference data that other code reads, and it follows the
same shape as its neighbours (machine-readable file + this README).

## Why photographs

The Bronkhorst MFCs are thermal — they measure *flow*, not pressure — and the
supply panel (`EN-MEF LP 1230`, Air Liquide GAS PANEL ML2 200-10-10) has no
electrical readout of the bottle. The only indication is the analogue WIKA
0–315 bar gauge on the high-pressure inlet, so it gets photographed.

This is the measured input to `gas_mixer_control/bottle_usage.py`, whose
`ARGON_START_BAR` is otherwise an assumption.

## `argon_bottle_pressure.csv`

| column | |
|---|---|
| `timestamp` | capture time from EXIF, local wall-clock at the panel |
| `bar`, `psi` | gauge reading; blank when `status=rejected` |
| `status` | `ok`, or `rejected` for a photo deliberately excluded |
| `source` | `auto` = the automatic guess was accepted unchanged; `manual` = typed by hand |
| `file` | photo filename, in `~/x17/gas/bottle_pressure_photos/` |

Rejected rows are kept **on purpose** — they record that a photo was looked at
and dismissed, so the reviewer does not ask about it again and so the gap in the
series is explained rather than mysterious.

Every reading is human-confirmed. The automatic reader is a starting guess only
(see *Accuracy* below).

### July 2026 series

Ten readings, 2026-07-07 to 2026-07-29, all panel **LP 1230**:
**227 → 131 bar**, a clean **−4.4 bar/day** (4.8 bar rms about a straight line).

Two caveats on this batch:

- The **2026-07-07 09:46** row (214 bar) is the only one accepted from the
  automatic guess unchanged, and it is also the only point that breaks the
  monotonic fall — 214, then 225 and 227 later the same day. It is probably
  ~10 bar low. Re-review that photo before leaning on it.
- **2026-07-07 19:03 is panel LP 1232**, a *different* bottle (~87 bar at the
  time), hence `rejected`. See *Two panels* below.

## Updating

Drop new photos into `~/x17/gas/bottle_pressure_photos/` and run:

```bash
calibrations/gas_bottle/update.sh
```

It pre-computes guesses for photos it has not seen (slow, ~10 s each,
unattended), then opens the review window: **Enter** accepts the guess, typing a
number corrects it, **Esc** rejects the photo, **F2** shows the full frame.
Photos already in the CSV are skipped, so re-running is safe and an interrupted
session loses nothing — the CSV is rewritten after every decision.

Then commit the CSV. Set `PHOTOS=/some/other/dir` to point it elsewhere.

Tooling and the full method: `gas_mixer_control/bottle_gauge_reader/`.

### Readings taken without a photo

When the gauge was read at the panel and written down rather than photographed,
there is nothing to review — add the row directly:

```bash
python3 calibrations/gas_bottle/add_reading.py                          # interactive
python3 calibrations/gas_bottle/add_reading.py "2026-08-03 14:20" 122   # or inline
```

These land as `source=manual-entry` (distinct from `manual`, which means a photo
*was* on screen and its guess was typed over) with a `manual:<timestamp>`
placeholder in `file`. The placeholder is required, not cosmetic: `review_gauge.py`
keys rows by filename and drops any row without one. Both paths write the same
CSV and can be used in any order.

## Untracked draws

Argon taken off the panel without passing the mixer's flow meter is invisible to
`bottle_usage.py`'s integration, so the readings after it drop below the model
for a reason the model has no term for. Each such event is declared in that
file's `UNTRACKED_DRAWS`, which subtracts it as a **step** from the mole count —
not as a faster leak, which would smear it backwards over every earlier reading.
Its size can be fitted from the readings that bracket it.

Note the scale before reading anything into a fitted step: 1 bar in the 50 L
bottle is ~50 normal litres, and readings are only good to ~±5 bar. A draw of a
few tens of litres is therefore **not measurable here at all** — the model
reports it as unresolved rather than pretending otherwise.

## Two panels — check the label

There is more than one of these panels in the area (`LP 1230` and `LP 1232` at
least). They are **identical** and their bottles sit at different pressures. The
reader cannot tell them apart; it reads whatever dial is in the picture.

On 2026-07-07 two photos a minute apart read 227 bar and 87 bar. That is not a
leak — it is two different panels. **Get the `LP 12xx` label in frame**, and
reject (or log separately) anything that is not the panel you are tracking.

## Accuracy

Readings are human, from a dial with 10 bar minor ticks, so roughly ±5 bar.

The automatic guess is worse than that and should not be trusted unreviewed: on
the July batch it was off by **+10 bar on average, sd 13, worst 23**. It is
useful as a default that saves typing, not as a measurement. The whole point of
the review step is that a person confirms every number.
