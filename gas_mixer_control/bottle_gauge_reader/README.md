# Argon bottle gauge reader

Turns **photos of the argon supply panel's WIKA gauge** into numbers.

The Bronkhorst MFCs measure *flow*, not pressure, and the supply panel
(`EN-MEF LP 1230`, Air Liquide GAS PANEL ML2 200-10-10) has no electrical readout
of the **bottle** pressure — the only indication is the analogue WIKA gauge on the
high-pressure inlet. So the bottle level gets logged by photographing the dial,
and this reads those photos back.

That pressure is the input to `../bottle_usage.py`, whose `ARGON_START_BAR` is
otherwise a guess: these readings measure the real starting pressure and the real
draw-down rate, rather than inferring them from integrated flow.

> Related but different gauges: the **0–16 bar** gauge on the same panel is the
> *regulated outlet* (P2, the ~0.7 bar feeding the MFCs — see the argon cap note
> in `../README.md`). This tool reads the **0–315 bar inlet** gauge only.

## The dial

WIKA 63 mm, dual scale, 270° sweep:

| | range | position |
|---|---|---|
| black outer | 0 – 315 bar | 0 bar at 7:30, 315 bar at 4:30 |
| red inner | 0 – 4500 psi | ends at 4500 psi ≈ 310.3 bar, just short of the black end |

Minor ticks every 10 bar, labels every 50. **1° of dial ≈ 1.2 bar**, which sets
the accuracy budget for everything below.

## Where things live

| | |
|---|---|
| photos | `~/x17/gas/bottle_pressure_photos/` (plus `guesses.csv` + `guesses/` overlays) |
| the log | `calibrations/gas_bottle/argon_bottle_pressure.csv` |
| add new readings | `calibrations/gas_bottle/update.sh` |

## Usage — review by hand (the normal way)

Automatic reading is not reliable enough to trust unsupervised, so the working
setup is: **the machine guesses, you decide.** Normally you just run
`calibrations/gas_bottle/update.sh`, which wraps the two steps below with the
standard paths. Spelled out, they are — two steps because reading a dial takes
~10 s, fine as an unattended batch, unbearable between key presses:

```bash
cd ~/x17/gas/bottle_pressure_photos
V=~/PycharmProjects/nTof_x17_DAQ/.venv/bin/python
R=~/PycharmProjects/nTof_x17_DAQ/gas_mixer_control/bottle_gauge_reader

# 1. pre-compute the guesses (slow, ~10 s/photo, walk away)
$V $R/gauge_reader.py . --csv guesses.csv --debug guesses/ --assume-upright

# 2. review them (instant)
$V $R/review_gauge.py . --csv bottle_pressure.csv
```

Step 2 opens a window per photo showing the dial, the capture time, and the
guess pre-filled in a box:

| key | |
|---|---|
| **Enter** | accept the value in the box |
| *type a number*, Enter | correct it |
| **Esc** | reject the photo |
| **F2** | toggle between the zoomed dial and the full frame (to read the `LP 12xx` label) |
| **Alt+←** | go back one |

`bottle_pressure.csv` is rewritten after every decision, so an interrupted
session loses nothing and re-running picks up where you left off (`--redo` to
revisit decided photos). The pressure-vs-time table prints when you finish.

Photos the guesser refused still appear — just with an empty box. Nothing is
skipped silently.

## Usage — batch, no review

```bash
$V $R/gauge_reader.py PHOTOS/ --csv bottle_pressure.csv --debug overlays/
```

| flag | what it does |
|---|---|
| `--csv FILE` | write `timestamp,bar,psi,confidence,file` |
| `--debug DIR` | write an annotated overlay per photo — **always look at these** |
| `--assume-upright` | if the dial's rotation can't be measured, assume the gauge is mounted upright (costs ~±7 bar) |
| `--hints JSON` | `{"PXL_….jpg": 205, …}` — rough eyeball readings that only steer *which way* the needle points |

Requires `opencv-python-headless` (plus numpy and Pillow, already in the venv).
`review_gauge.py` also needs `tkinter` (stdlib) and a display.

## Files

| | |
|---|---|
| `gauge_reader.py` | the automatic reader — library + batch CLI |
| `review_gauge.py` | the review GUI; the one you normally run |
| `hints.example.json` | example `--hints` file |

### Reading the overlay

The overlay draws the **fitted scale in magenta** on top of the dial's own printed
scale, and the **detected needle in red**. This is the check that matters:

- magenta ticks sitting on the printed ticks → the calibration is right;
- red line lying along the black needle → the right feature was found.

If the magenta scale is visibly rotated off the printed one, the number is wrong
by the same amount, whatever the confidence says.

## How it works

No OCR, no ML. One deliberate choice drives the design: **everything is measured
off the red psi scale.** Red ink is the only thing in frame that is unambiguously
*part of the dial*. The bezel, the black scale print, the pipework and the knobs
are all dark or grey, and any threshold that catches the tick marks catches them
too — the tarnished chrome bezel in particular is a solid dark ring that drowns
out the tick pattern entirely. Red has none of that competition.

1. **Find the dial** — `cv2.HoughCircles` proposes circles; each is scored on how
   much of the frame's red ink it encloses, whether that red forms an *annulus*
   rather than a blob, and how much of a turn it covers. That rejects the two
   decoys which fool a plain circle detector: the blank panel plate (round and
   white, no red) and the "AIR LIQUIDE" sticker (red, but a compact blob). Hough
   only has to be roughly right — it just selects which red pixels are the dial's.
2. **Fix the centre** — least-squares circle fit to those red pixels, iterated
   with outlier rejection. Every angle downstream depends on this.
3. **Fix the rotation** — the red arc stops at 4500 psi, leaving a blank wedge at
   the bottom of the dial. The *centre* of that wedge is 6 o'clock; being a
   midpoint between two edges, it is far steadier than either edge alone, which
   drift in opposite directions as the ink threshold moves. The angular span is
   known from the dial spec, so it is never measured.
4. **Find the needle** — the needle is the one dark feature that runs
   *continuously outward from the hub*. Printed numerals are just as black but
   are free-floating blobs, so scoring each angle by its longest hub-anchored
   dark run separates needle from print. The stubby counterweight tail is then
   rejected by comparing how far each end reaches.

## Accuracy, and what it can't do

**Measured against the July 2026 batch, after human review: mean error +10 bar,
sd 13, worst 23.** Treat the automatic number as a default that saves typing,
not as a measurement — which is why the review step exists. It was close to a
few bar only on the one clean, square-on dial in that set; the rest were
hand-held wide shots, and they were not.

Two failure modes dominate, and both are properties of the photo, not the code:

- **Oblique angle.** A circular dial photographed off-axis projects to an
  ellipse, and the scale no longer maps linearly onto image angle. The reading
  skews; the overlay shows it as magenta ticks that drift away from the printed
  ones around the rim.
- **Glare.** Reflections on the glass wash out the needle or break the red arc.

Stage 4 is the brittle one. On a heterogeneous set of hand-held photos it finds
the needle unaided on the clean, large, square-on dials and misses on the small,
oblique or glared ones. That's what `--hints` is for: a rough number (±40 bar is
plenty) confines the search to one wedge of the dial and removes the ambiguity,
while the precise angle — and therefore the actual reading — still comes from the
image. One glance per problem photo, and it is auditable, because the hint cannot
move the answer more than the window allows.

`confidence` measures only how dominant the winning needle direction was against
the rest of the dial. It says nothing about whether the *scale* fit is right —
that's what the overlay is for. A hinted read reports low confidence by
construction (the search was deliberately restricted), which is not the same as a
bad read.

**For a better shot:** square-on, dial filling most of the frame, no flash, and
avoid the ceiling lights reflecting off the glass. Those read unaided.

## When it refuses

The reader would rather fail than emit a confident-looking wrong number, so two
checks can reject a photo outright:

- **"red arc fit drifted off the dial centre"** — a circle fit only pins a centre
  if the points wrap most of the way round. Where glare or a very small dial
  leaves only an arc fragment, the fit slides along it and settles somewhere
  inside the face. That centre looks reasonable but is wrong by tens of bar once
  angles are measured from it, so it is rejected instead.
- **"blank wedge … expected ~94 deg"** — the rotation could not be established.
  `--assume-upright` reads it anyway using the mounting orientation.

Both mean *re-photograph if you can*. Failing that, read the dial by eye — the
printed scale has 10 bar minor ticks, so ±5 bar by hand is easy, and that is
better than a number the tool cannot stand behind.

## Results so far

The log lives in `calibrations/gas_bottle/argon_bottle_pressure.csv` — see that
directory's README for the series so far and for how to add to it.

## Taking the photos

Any camera; EXIF `DateTimeOriginal` is used for the timestamp (local wall-clock at
the panel). Pixel `PXL_YYYYMMDD_HHMMSS` filenames are a fallback — note those are
**UTC** and are converted; file mtime is the last resort.

**Get the panel label in frame.** There is more than one of these panels in the
area (`LP 1230` and `LP 1232` at least), they are identical, and their bottles are
at different pressures. The reader cannot tell them apart — it reads whatever dial
is in the picture. Two photos a minute apart showing 210 bar and 87 bar are not a
leak; they are two different panels.
