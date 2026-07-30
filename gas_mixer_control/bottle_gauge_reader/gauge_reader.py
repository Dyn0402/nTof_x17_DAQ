"""Read the argon bottle pressure off a photo of the WIKA panel gauge.

The argon supply panel (Air Liquide GAS PANEL ML2 200-10-10, ``EN-MEF LP 1230``)
has no electrical readout of the *bottle* pressure — the only indication is the
analogue WIKA gauge on the high-pressure inlet, so bottle level is tracked by
photographing the dial. This module turns those photos back into numbers.

The dial (WIKA 63 mm, dual scale):

    black outer scale   0 .. 315 bar    (labels every 50, minor ticks every 10)
    red inner scale     0 .. 4500 psi
    span                270 deg — 0 bar at 7:30, 315 bar at 4:30

Detection is geometric — no OCR, no ML — and leans on one deliberate choice:
**everything is measured off the red psi scale.** Red ink is the only thing on
the panel that is unambiguously *part of the dial*. The chrome bezel, the black
scale print, the pipework and the knobs are all dark or grey, and a threshold
that catches the tick marks catches them too; the tarnished bezel in particular
is a solid dark ring that drowns the tick pattern. Red has none of that
competition, so the red arc alone fixes the dial's centre and rotation.

  1. **Find the dial.** ``cv2.HoughCircles`` proposes circles; each is scored on
     how much of the frame's red ink it encloses, whether that red forms an
     *annulus* rather than a blob, and how much of a turn it covers. That rejects
     the two decoys which fool a plain circle detector: the blank panel plate
     (round and white, but no red) and the "AIR LIQUIDE" sticker (red, but a
     compact blob). Hough only has to be roughly right — it just selects which
     red pixels belong to the dial.

  2. **Fix the centre.** A least-squares circle fit to those red pixels, iterated
     with outlier rejection, locates the centre far more precisely than Hough's
     accumulator grid. Every angle downstream depends on this.

  3. **Fix the rotation.** The red arc spans 0..4500 psi and then stops, leaving a
     blank wedge at the bottom of the dial. The *centre* of that wedge is 6
     o'clock on the dial — and being a midpoint between two edges, it is far
     steadier than either edge alone (which drift with the ink threshold). That
     pins the scale no matter how the phone was held. The angular span is known
     from the dial's specification, so it is not measured.

  4. **Find the needle.** The needle is the one dark feature that is radially
     *continuous* — an unbroken run of dark pixels from near the hub outward.
     Scale numerals ("150", "3500") are just as black but are short, isolated
     blobs, so scoring each angle by its longest contiguous dark radial run
     separates needle from print. The stubby counterweight tail scores too, so
     only runs reaching past ``TIP_MIN_R`` count — that is the pointing end.

Accuracy is a few bar on a clean, square-on photo (1 deg of dial ~ 1.2 bar).
A steep viewing angle projects the circular dial to an ellipse and skews the
reading; heavy glare can swallow the needle. Both tend to show up as a failed
sanity check or low confidence rather than a plausible-looking wrong number, but
**do eyeball the ``--debug`` overlays before trusting a batch** — the overlay
draws the fitted scale over the printed one, so a bad fit is obvious at a glance.

Usage
-----
    python gauge_reader.py PHOTO [PHOTO ...]           # print readings
    python gauge_reader.py DIR --csv out.csv           # batch a directory
    python gauge_reader.py DIR --debug overlays/       # + annotated images

Timestamps come from EXIF ``DateTimeOriginal`` (local wall-clock at the panel),
falling back to the ``PXL_YYYYMMDD_HHMMSS`` name Pixel phones use — which is
**UTC**, so it gets converted — and finally to the file mtime.
"""
import argparse
import csv
import datetime as dt
import math
import os
import re
import sys

import cv2
import numpy as np

# --- Dial geometry (WIKA 63 mm, 0-315 bar / 0-4500 psi) ----------------------
BAR_FULL_SCALE = 315.0        # bar at the clockwise end of the black scale
SPAN_DEG = 270.0              # angular sweep of the black scale
PSI_PER_BAR = 14.5037738
PSI_FULL_SCALE = 4500.0

# The red psi scale stops at 4500 psi, a little short of the black scale's end,
# so its arc is slightly narrower than 270 deg and its blank wedge correspondingly
# wider than 90 deg. Both follow from the dial spec — they are never measured.
RED_END_BAR = PSI_FULL_SCALE / PSI_PER_BAR              # ~310.3 bar
RED_SPAN_DEG = RED_END_BAR / BAR_FULL_SCALE * SPAN_DEG  # ~265.9 deg
RED_GAP_DEG = 360.0 - RED_SPAN_DEG                      # ~94.1 deg

# --- Detection tuning --------------------------------------------------------
WORK_PX = 1400                # long edge the image is resampled to before analysis
NBIN = 720                    # angular bins (0.5 deg) for all profiles
GAP_MIN_DEG, GAP_MAX_DEG = 60.0, 110.0   # acceptable width of the measured wedge
UPRIGHT_TOLERANCE_DEG = 45.0  # how far the wedge may sit from straight down
HUB_R = 0.46                  # inner limit of the needle search, in red-arc radii
OUTER_R = 1.38                # outer limit — just past the black tick ring
TIP_MIN_R = 1.00              # a needle run must reach this radius to count
MIN_RED_R_PX = 90.0           # upscale the photo if the red arc is smaller than this

# Fallback when the wedge cannot be measured: the gauge is bolted to the panel
# the right way up and the photos are roughly level, so the dial's 6 o'clock is
# near image-down. Across the photos that fit cleanly, the 0 bar tick lands
# within about +/-6 deg of this, i.e. the fallback costs roughly +/-7 bar.
UPRIGHT_ZERO_DEG = 225.0

_PXL_NAME = re.compile(r"PXL_(\d{8})_(\d{6})")
_IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff")


class GaugeReadError(RuntimeError):
    """Raised when the dial, the scale, or the needle could not be located."""


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _wrap180(a):
    return (a + 180.0) % 360.0 - 180.0


def _longest_run(mask):
    """Length of the longest contiguous True run in a 1-D boolean array."""
    if not mask.any():
        return 0
    e = np.flatnonzero(np.diff(np.concatenate(([0], mask.view(np.int8), [0]))))
    return int((e[1::2] - e[::2]).max())


def _longest_circular_run(mask):
    """(start, length) of the longest wrap-around True run."""
    n = len(mask)
    if mask.all():
        return 0, n
    dbl = np.concatenate([mask, mask])
    best_len = best_start = 0
    i = 0
    while i < 2 * n:
        if dbl[i]:
            j = i
            while j < 2 * n and dbl[j]:
                j += 1
            if j - i > best_len and i < n:
                best_len, best_start = j - i, i
            i = j
        else:
            i += 1
    return best_start, best_len


def _fit_circle(x, y):
    """Kasa algebraic circle fit -> (cx, cy, r)."""
    A = np.column_stack([2 * x, 2 * y, np.ones_like(x)])
    sol, *_ = np.linalg.lstsq(A, x * x + y * y, rcond=None)
    cx, cy = float(sol[0]), float(sol[1])
    return cx, cy, math.sqrt(max(sol[2] + cx * cx + cy * cy, 1e-9))


# --------------------------------------------------------------------------- #
# Image preparation
# --------------------------------------------------------------------------- #
def _load(path):
    bgr = cv2.imread(path, cv2.IMREAD_COLOR)
    if bgr is None:
        raise GaugeReadError("not a readable image")
    bgr = _apply_exif_rotation(path, bgr)
    h, w = bgr.shape[:2]
    s = WORK_PX / max(h, w)
    if s < 1.0:
        bgr = cv2.resize(bgr, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)
    return bgr


def _apply_exif_rotation(path, bgr):
    """cv2.imread ignores EXIF orientation. The dial is round and the rotation is
    measured from the dial itself, so this only affects how the debug overlays
    look — but an upright overlay is much easier to check."""
    rot = {3: cv2.ROTATE_180, 6: cv2.ROTATE_90_CLOCKWISE, 8: cv2.ROTATE_90_COUNTERCLOCKWISE}
    try:
        from PIL import Image
        o = Image.open(path).getexif().get(274, 1)
    except Exception:
        return bgr
    return cv2.rotate(bgr, rot[o]) if o in rot else bgr


def _masks(bgr):
    """Red scale ink, and the white dial face."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    H, S, V = hsv[..., 0].astype(int), hsv[..., 1].astype(int), hsv[..., 2].astype(int)
    red = ((H < 10) | (H > 170)) & (S > 90) & (V > 80)
    face = (V > 110) & (S < 60)
    return red, face


# --------------------------------------------------------------------------- #
# Stage 1 — which circle is the dial
# --------------------------------------------------------------------------- #
def _hough_candidates(bgr):
    gray = cv2.medianBlur(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY), 5)
    h, w = gray.shape
    lo, hi = int(0.06 * min(h, w)), int(0.58 * min(h, w))
    found = []
    for param2 in (90, 70, 55, 45, 35, 28):
        c = cv2.HoughCircles(gray, cv2.HOUGH_GRADIENT, dp=1,
                             minDist=int(0.05 * min(h, w)), param1=130,
                             param2=param2, minRadius=lo, maxRadius=hi)
        if c is not None:
            found.extend(c[0].tolist())
        if len(found) > 90:
            break
    return found


def _score_candidate(red, face, rx, ry, x, y, r):
    """How dial-like is this circle? Returns (score, diagnostics)."""
    h, w = red.shape
    if x - r < -0.4 * r or x + r > w + 0.4 * r or y - r < -0.4 * r or y + r > h + 0.4 * r:
        return -1e9, {}
    d = np.hypot(rx - x, ry - y) / r
    inside = d < 0.92
    if inside.sum() < 150:
        return -1e9, {}

    red_frac = inside.sum() / len(rx)              # share of the frame's red ink
    rad = d[inside]
    annular = float(np.mean((rad > 0.30) & (rad < 0.88)))
    ang = np.degrees(np.arctan2(rx[inside] - x, -(ry[inside] - y))) % 360.0
    cover = len(np.unique((ang / 10).astype(int))) / 36.0

    yy, xx = np.mgrid[0:h, 0:w]
    inner = ((xx - x) ** 2 + (yy - y) ** 2) < (0.80 * r) ** 2
    face_frac = float(face[inner].mean())

    diag = dict(red=red_frac, annular=annular, cover=cover, face=face_frac)
    if red_frac < 0.35 or annular < 0.65 or cover < 0.50:
        return -1e9, diag
    return 2.0 * red_frac + 1.5 * annular + 1.5 * cover + 0.5 * face_frac, diag


def _select_dial(bgr, red, face):
    ry, rx = np.nonzero(red)
    if len(rx) < 200:
        raise GaugeReadError("no red scale markings in frame - is the dial visible?")
    best, best_score = None, -1e9
    for (x, y, r) in _hough_candidates(bgr):
        s, _ = _score_candidate(red, face, rx, ry, x, y, r)
        if s > best_score:
            best_score, best = s, (float(x), float(y), float(r))
    if best is None:
        # No proposed circle looked like a dial (a second gauge in shot, an odd
        # crop). Fall back to the densest cluster of red ink, which is the psi
        # scale itself; the circle fit that follows only needs a rough envelope.
        cx, cy = np.median(rx), np.median(ry)
        for _ in range(15):
            d = np.hypot(rx - cx, ry - cy)
            keep = d < max(np.percentile(d, 60), 25.0)
            if keep.sum() < 100:
                break
            cx, cy = rx[keep].mean(), ry[keep].mean()
        spread = np.percentile(np.hypot(rx[keep] - cx, ry[keep] - cy), 95)
        best = (float(cx), float(cy), float(spread * 1.5))
    return best


# --------------------------------------------------------------------------- #
# Stage 2/3 — centre and rotation, both from the red arc
# --------------------------------------------------------------------------- #
def _fit_red_arc(red, seed):
    """Refine the centre on the dial's red ink. Returns (cx, cy, r_red)."""
    cx, cy, r_outer = seed
    ry, rx = np.nonzero(red)
    keep = np.hypot(rx - cx, ry - cy) < r_outer * 0.95
    if keep.sum() < 200:
        raise GaugeReadError("too little red scale ink on the detected dial")
    X, Y = rx[keep].astype(float), ry[keep].astype(float)
    for _ in range(6):
        fx, fy, fr = _fit_circle(X, Y)
        d = np.hypot(X - fx, Y - fy)
        band = (d > fr * 0.6) & (d < fr * 1.4)     # drop the hub symbol and strays
        if band.sum() < 150:
            break
        X, Y = X[band], Y[band]
    fx, fy, fr = _fit_circle(X, Y)

    # A circle fit only pins a centre if the points wrap most of the way round.
    # When glare or a small dial leaves just an arc fragment, the fit slides off
    # along it and settles somewhere inside the face — plausible-looking, and
    # wrong by tens of bar once angles are measured from it. The detected dial is
    # the sanity check: the psi arc is concentric with it, so a centre that walks
    # away from it means the fit found a fragment, not the arc.
    if math.hypot(fx - cx, fy - cy) > 0.25 * r_outer:
        raise GaugeReadError(
            "red arc fit drifted off the dial centre - only part of the scale is "
            "usable (glare, or the dial is very small in frame)")
    return fx, fy, fr


def _fit_rotation(red, cx, cy, r_red, allow_upright=False):
    """Image angle (deg cw from up) of the 0 bar tick.

    The red arc's blank wedge sits at the bottom of the dial; its midpoint is 6
    o'clock, from which the 0 bar end follows by a fixed offset. A midpoint is
    used rather than either edge because the two edges drift in opposite
    directions as the ink threshold moves, so the error largely cancels.

    Returns (zero_deg, gap_deg, how) where ``how`` is "wedge" or "assumed".
    """
    ry, rx = np.nonzero(red)
    X, Y = rx.astype(float) - cx, ry.astype(float) - cy
    rad = np.hypot(X, Y) / r_red
    sel = (rad > 0.55) & (rad < 1.5)
    if sel.sum() < 200:
        raise GaugeReadError("red arc too sparse to fix the dial rotation")

    ang = np.degrees(np.arctan2(X[sel], -Y[sel])) % 360.0
    cnt = np.bincount((ang * (NBIN / 360.0)).astype(int) % NBIN, minlength=NBIN).astype(float)
    cnt = np.convolve(np.tile(cnt, 3), np.ones(7) / 7, "same")[NBIN:2 * NBIN]

    start, length = _longest_circular_run(cnt < 0.15 * cnt.max())
    gap_deg = length * 360.0 / NBIN
    bottom = ((start + length / 2.0) % NBIN) * 360.0 / NBIN

    bad = None
    if not (GAP_MIN_DEG <= gap_deg <= GAP_MAX_DEG):
        bad = (f"blank wedge in the red scale is {gap_deg:.0f} deg, expected "
               f"~{RED_GAP_DEG:.0f}")
    elif abs(_wrap180(bottom - 180.0)) > UPRIGHT_TOLERANCE_DEG:
        # The wedge is the right size but nowhere near the bottom of the frame,
        # which for a panel-mounted gauge means we found the wrong blank.
        bad = f"blank wedge sits {abs(_wrap180(bottom - 180.0)):.0f} deg off dial-bottom"

    if bad is None:
        return (bottom + 180.0 - RED_SPAN_DEG / 2.0) % 360.0, gap_deg, "wedge"
    if allow_upright:
        return UPRIGHT_ZERO_DEG, gap_deg, "assumed"
    raise GaugeReadError(bad + " - dial cropped, obscured, or glare on the face? "
                               "(--assume-upright to read it anyway)")


# --------------------------------------------------------------------------- #
# Stage 4 — the needle
# --------------------------------------------------------------------------- #
def _hub_run(row, slack=2):
    """Radial index out to which ink extends *continuously from the hub*.

    Anchoring at the hub is what separates the needle from the printed text: the
    needle is physically attached to the centre boss, whereas "0 bar psi" or
    "1 bar = 100 kPa" are free-floating blobs that happen to lie along some
    radius. ``slack`` tolerates a few empty bins where the needle is thin or
    crosses a printed tick.
    """
    end, gap = 0, 0
    for i, on in enumerate(row):
        if on:
            end, gap = i, 0
        else:
            gap += 1
            if gap > slack:
                break
    return end


def _reach(occ, k, halfwidth=6):
    """How far ink reaches from the hub around direction bin ``k``.

    Averaged over neighbouring angles so one ragged column cannot decide it.
    """
    n = occ.shape[0]
    ends = [_hub_run(occ[j % n]) for j in range(k - halfwidth, k + halfwidth + 1)]
    return float(np.median(ends)) if ends else 0.0


def _find_needle(bgr, red, cx, cy, r_red, zero_deg=0.0, hint_bar=None,
                 hint_window_deg=35.0):
    """Needle angle (deg cw from image-up) and a 0-1 confidence.

    ``hint_bar`` is an optional rough reading (from a human glancing at the
    photo) used only to restrict the angular search window. Picking the needle
    out of a cluttered dial is the brittle step; saying "it's somewhere near 200"
    removes the ambiguity while the sub-degree angle — and therefore the actual
    number — still comes from the image, not from the hint.
    """
    h, w = bgr.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    r = np.hypot(xx - cx, yy - cy) / r_red
    ang = np.degrees(np.arctan2(xx - cx, -(yy - cy))) % 360.0

    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    disc = r < OUTER_R
    if disc.sum() < 2000:
        raise GaugeReadError("dial too small in frame to analyse")
    thr, _ = cv2.threshold(gray[disc].astype(np.uint8), 0, 255,
                           cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    ink = (gray < thr) & ~red & disc

    nrad = 64
    # Red pixels are excluded from the *denominator* as well as the numerator.
    # Where the needle crosses the red danger band most of the bin is red ink, so
    # counting those pixels as "not needle" would dilute the occupancy below the
    # threshold and make the pointer vanish exactly when the bottle is full.
    band = (r >= HUB_R) & (r <= OUTER_R) & ~red
    abin = (ang[band] * (NBIN / 360.0)).astype(int) % NBIN
    rbin = np.clip(((r[band] - HUB_R) / (OUTER_R - HUB_R) * (nrad - 1)).round().astype(int),
                   0, nrad - 1)
    tot = np.zeros((NBIN, nrad))
    hit = np.zeros((NBIN, nrad))
    np.add.at(tot, (abin, rbin), 1.0)
    np.add.at(hit, (abin, rbin), ink[band].astype(float))
    occ = np.divide(hit, tot, out=np.zeros_like(hit), where=tot > 0) > 0.5

    # With a hint the search is confined to one wedge of the dial, so the run
    # does not have to reach as far out, nor beat everything else on the face,
    # to be identifiable. Without one, both gates are what keep printed text
    # from being mistaken for the pointer.
    def _score_for(tip_r):
        t = int(np.clip((tip_r - HUB_R) / (OUTER_R - HUB_R) * (nrad - 1), 0, nrad - 1))
        s = np.array([_hub_run(row) if row[t:].any() else 0 for row in occ], float)
        return np.convolve(np.tile(s, 3), np.ones(5) / 5, "same")[NBIN:2 * NBIN]

    if hint_bar is None:
        score = _score_for(TIP_MIN_R)
        if score.max() < 0.30 * nrad:
            raise GaugeReadError("no needle found - glare across the dial, or obscured")
    else:
        # On a small or soft dial the pointer may not survive as a long clean run,
        # so step the reach requirement down until something shows up inside the
        # hinted wedge. The wedge is narrow enough that there is nothing else
        # there to latch onto.
        want = (zero_deg + hint_bar / BAR_FULL_SCALE * SPAN_DEG) % 360.0
        near = np.abs(_wrap180(np.arange(NBIN) * (360.0 / NBIN) - want)) <= hint_window_deg
        for tip_r in (0.90, 0.70, 0.55, HUB_R):
            score = _score_for(tip_r)
            if (score * near).max() > 0:
                break
        else:
            raise GaugeReadError(f"no needle near the {hint_bar:.0f} bar hint")

    if hint_bar is None:
        # The needle is a two-ended object: a long thin pointer and a short fat
        # counterweight 180 deg opposite. Both score well, so take the strongest
        # direction and explicitly compare it against its opposite, keeping
        # whichever reaches further out.
        peak = int(score.argmax())
        opposite = (peak + NBIN // 2) % NBIN
        if _reach(occ, opposite) > _reach(occ, peak) + 2:
            peak = opposite
    else:
        peak = int((score * near).argmax())

    offs = np.arange(-12, 13)
    wts = np.clip(score[(peak + offs) % NBIN] - 0.5 * score.max(), 0.0, None)
    step = 360.0 / NBIN
    angle = ((peak + (wts * offs).sum() / wts.sum()) * step) % 360.0 if wts.sum() else peak * step

    far = np.abs(_wrap180((np.arange(NBIN) - peak) * step)) > 20.0
    runner = float(score[far].max()) if far.any() else 0.0
    return float(angle), float(np.clip(1.0 - runner / max(score.max(), 1e-6), 0.0, 1.0))


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def read_gauge(path, debug_dir=None, assume_upright=False, hint_bar=None):
    """Read one photo. Returns a dict; raises GaugeReadError if unreadable."""
    bgr = _load(path)
    red, face = _masks(bgr)
    seed = _select_dial(bgr, red, face)
    cx, cy, r_red = _fit_red_arc(red, seed)

    # In a wide shot the dial can be only tens of pixels across, which is finer
    # than the 0.5 deg x 64-step grid the needle search runs on — most cells end
    # up empty and the pointer disappears into quantisation noise. Crop to the
    # dial and enlarge so the grid has something to bite on. This adds no detail,
    # it just stops the sampling from throwing away what is there. Cropping first
    # matters: scaling the whole frame instead is many times the pixels, for a
    # region we are about to discard anyway.
    if r_red < MIN_RED_R_PX:
        pad = r_red * 2.8      # comfortably past the tick ring and bezel
        x0, y0 = int(max(0, cx - pad)), int(max(0, cy - pad))
        x1, y1 = int(min(bgr.shape[1], cx + pad)), int(min(bgr.shape[0], cy + pad))
        k = min(MIN_RED_R_PX / r_red, 6.0)
        bgr = cv2.resize(bgr[y0:y1, x0:x1], None, fx=k, fy=k, interpolation=cv2.INTER_CUBIC)
        red, face = _masks(bgr)
        cx, cy = (cx - x0) * k, (cy - y0) * k
        expect_r, expect_c = r_red * k, (cx, cy)
        cx, cy, r_red = _fit_red_arc(red, (cx, cy, expect_r * 1.6))
        # The enlarged crop is mostly dial, so a re-fit that moves the centre or
        # changes the radius substantially has locked onto something else — a
        # clipped arc, a reflection — rather than the psi scale. Better to refuse
        # than to report a confident-looking number off a scale never found.
        drift = math.hypot(cx - expect_c[0], cy - expect_c[1]) / expect_r
        if not 0.7 < r_red / expect_r < 1.4 or drift > 0.35:
            raise GaugeReadError(
                "dial too small in frame to read reliably - the enlarged re-fit "
                f"moved the centre by {drift * 100:.0f}% of the arc radius")

    zero, gap_deg, how = _fit_rotation(red, cx, cy, r_red, assume_upright)
    needle, conf = _find_needle(bgr, red, cx, cy, r_red, zero, hint_bar)
    if how == "assumed":
        conf = min(conf, 0.45)

    sweep = (needle - zero) % 360.0
    if sweep > SPAN_DEG:
        # In the blank wedge below the scale: pinned at one end or the other.
        bar = 0.0 if sweep > SPAN_DEG + (360.0 - SPAN_DEG) / 2.0 else BAR_FULL_SCALE
        conf = min(conf, 0.4)
    else:
        bar = sweep / SPAN_DEG * BAR_FULL_SCALE

    res = {
        "file": os.path.basename(path),
        "timestamp": photo_timestamp(path),
        "bar": float(bar),
        "psi": float(bar) * PSI_PER_BAR,
        "confidence": float(conf),
        "gap_deg": gap_deg,
        "rotation_from": how,
        "_geom": (cx, cy, r_red, zero, needle),
        "_image": bgr,
    }
    if debug_dir:
        res["debug"] = write_overlay(res, debug_dir)
    return res


def photo_timestamp(path):
    """Best available capture time, as a naive local datetime."""
    try:
        from PIL import Image
        exif = Image.open(path).getexif()
        raw = exif.get(36867) or exif.get(306)      # DateTimeOriginal, DateTime
        if raw:
            return dt.datetime.strptime(str(raw).strip(), "%Y:%m:%d %H:%M:%S")
    except Exception:
        pass
    m = _PXL_NAME.search(os.path.basename(path))
    if m:
        utc = dt.datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
        return utc.replace(tzinfo=dt.timezone.utc).astimezone().replace(tzinfo=None)
    return dt.datetime.fromtimestamp(os.path.getmtime(path))


def write_overlay(res, debug_dir):
    """Annotated copy: the fitted scale drawn over the printed one, plus the
    needle. If the magenta ticks do not sit on the dial's own labels, the fit is
    wrong and the number should not be believed."""
    os.makedirs(debug_dir, exist_ok=True)
    cx, cy, r_red, zero, needle = res["_geom"]
    vis = res["_image"].copy()

    def at(deg, rad):
        a = math.radians(deg)
        return int(round(cx + math.sin(a) * rad)), int(round(cy - math.cos(a) * rad))

    for bar in range(0, int(BAR_FULL_SCALE) + 1, 10):
        major = bar % 50 == 0
        a = zero + bar / BAR_FULL_SCALE * SPAN_DEG
        cv2.line(vis, at(a, r_red * (1.05 if major else 1.20)), at(a, r_red * 1.35),
                 (255, 0, 255), 2 if major else 1)
        if major:
            cv2.putText(vis, str(bar), at(a, r_red * 1.55), cv2.FONT_HERSHEY_SIMPLEX,
                        0.5, (255, 0, 255), 2)
    cv2.line(vis, (int(cx), int(cy)), at(needle, r_red * 1.30), (0, 0, 255), 2)
    cv2.circle(vis, (int(cx), int(cy)), 4, (0, 255, 255), -1)

    txt = f"{res['bar']:.0f} bar / {res['psi']:.0f} psi   conf {res['confidence']:.2f}"
    for col, th in (((0, 0, 0), 5), ((0, 0, 255), 2)):
        cv2.putText(vis, txt, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, col, th)

    out = os.path.join(debug_dir, os.path.splitext(res["file"])[0] + "_overlay.jpg")
    cv2.imwrite(out, vis)
    return out


def iter_images(paths):
    for p in paths:
        if os.path.isdir(p):
            for name in sorted(os.listdir(p)):
                if name.lower().endswith(_IMAGE_EXT):
                    yield os.path.join(p, name)
        else:
            yield p


def main(argv=None):
    ap = argparse.ArgumentParser(description="Read a WIKA 0-315 bar dial from photos.")
    ap.add_argument("paths", nargs="+", help="image files and/or directories")
    ap.add_argument("--csv", help="write the readings to this CSV")
    ap.add_argument("--debug", metavar="DIR", help="write annotated overlays here")
    ap.add_argument("--assume-upright", action="store_true",
                    help="when the dial's rotation cannot be measured, assume the "
                         "gauge is mounted upright (worth ~+/-7 bar; always check "
                         "the overlay)")
    ap.add_argument("--hints", metavar="JSON",
                    help="JSON {filename: approx_bar} of rough eyeball readings, "
                         "used only to disambiguate which way the needle points "
                         "on photos the detector gets wrong; the precise angle "
                         "still comes from the image")
    args = ap.parse_args(argv)

    hints = {}
    if args.hints:
        import json
        with open(args.hints) as fh:
            hints = json.load(fh)

    rows, failed = [], []
    for path in iter_images(args.paths):
        try:
            rows.append(read_gauge(path, debug_dir=args.debug,
                                   assume_upright=args.assume_upright,
                                   hint_bar=hints.get(os.path.basename(path))))
        except (GaugeReadError, cv2.error, OSError) as exc:
            failed.append((os.path.basename(path), str(exc).strip().splitlines()[-1]))

    rows.sort(key=lambda r: r["timestamp"])
    for r in rows:
        flag = "" if r["confidence"] >= 0.5 else "   <-- low confidence, check overlay"
        if r["rotation_from"] == "assumed":
            flag = "   <-- rotation ASSUMED upright, check overlay"
        print(f"{r['timestamp']:%Y-%m-%d %H:%M}  {r['bar']:6.1f} bar  {r['psi']:6.0f} psi  "
              f"conf {r['confidence']:.2f}  {r['file']}{flag}")

    if len(rows) > 1:
        days = (rows[-1]["timestamp"] - rows[0]["timestamp"]).total_seconds() / 86400.0
        if days > 0:
            rate = (rows[-1]["bar"] - rows[0]["bar"]) / days
            print(f"\n{len(rows)} readings over {days:.1f} d: {rows[0]['bar']:.0f} -> "
                  f"{rows[-1]['bar']:.0f} bar ({rate:+.2f} bar/day mean)")

    if args.csv:
        with open(args.csv, "w", newline="") as fh:
            # csv defaults to CRLF; keep it LF so grep/awk/git see clean lines.
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["timestamp", "bar", "psi", "confidence", "file"])
            for r in rows:
                w.writerow([r["timestamp"].isoformat(timespec="seconds"), f"{r['bar']:.1f}",
                            f"{r['psi']:.0f}", f"{r['confidence']:.2f}", r["file"]])
        print(f"\nwrote {args.csv}")

    for name, msg in failed:
        print(f"FAILED  {name}: {msg}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
