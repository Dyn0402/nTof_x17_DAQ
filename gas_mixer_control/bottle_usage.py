"""Estimate gas-bottle usage from the logged mixer flow.

The gas_watcher logs the measured flow of each controller (columns
``argon_flow_lnh`` / ``iso_flow_lnh``) to per-day CSVs. "ln/h" is *normal*
litres per hour — Bronkhorst's normal reference is 0 degC, 1.013 bar, i.e.
22.414 normal litres per mole. Integrating the flow over the whole logging
history therefore gives the total normal litres (~moles) drawn from each
bottle.

Two gas types, two very different models:

  * Argon is a compressed gas. We track the remaining mole count exactly
    (moles are conserved regardless of the bottle's equation of state) and
    convert moles -> pressure with a real-gas model (below), calibrated
    against the hand-logged gauge photos in
    ``calibrations/gas_bottle/argon_bottle_pressure.csv``.

  * Isobutane is a *liquefied* gas. While any liquid remains the head
    pressure sits at its vapour pressure (~3 bar abs near 20 degC) and
    barely moves; it only collapses once the liquid is gone. So the
    meaningful gauge for iso is the *liquid remaining*, not a dropping
    pressure. We track litres of liquid left and report the ~constant
    vapour pressure alongside it. (No photo calibration exists for iso.)

--- Logging gaps: assume steady flow ------------------------------------
The watcher occasionally drops a few seconds to a few hours of logging
(process restarts etc.). Checked against every gap >60s across 22 days of
logs: in every case the flow immediately before and after the gap was
non-zero and essentially unchanged, i.e. the mixer kept running and only
the *logger* hiccuped. We therefore integrate straight through gaps at
their true duration (ordinary trapezoid) instead of capping them — capping
silently deletes real consumption. If a gap is ever found where flow
before/after genuinely disagrees, that is a sign the mixer itself may have
stopped and this assumption should be revisited for that gap.

--- Real-gas correction (why the linear P = drawn/volume gauge is wrong) --
The old model divided remaining normal-litres directly by the bottle
volume, which is only correct if the bottle sits at 0 degC AND argon is an
ideal gas at 220 bar (neither holds):

  1. Normal litres are defined at 0 degC (273.15 K); the bottle actually
     sits at ambient (~20 degC / 293 K). At fixed volume and mole count,
     ideal-gas pressure scales with T, so this alone under-states pressure
     by ~T_actual/273.15 (~9% at 20 degC).
  2. Argon at 100-230 bar is not ideal: attractive intermolecular forces
     make it *more* compressible than an ideal gas at this temperature, so
     a given mole count exerts LESS pressure than ideal-gas-at-T predicts
     (compressibility factor Z < 1, e.g. Z~0.94-0.96 across our operating
     range per Redlich-Kwong below).

  Both effects are computed from argon's actual critical constants via the
  Redlich-Kwong equation of state (``_rk_pressure``) using the flow log's
  own ``argon_temp_c`` reading — no fitted fudge factor. In our operating
  range (130-230 bar, ~20 degC) the two effects roughly cancel to a net
  +3-4% (real pressure a bit ABOVE the naive linear model for the same
  mole count) — i.e. compressibility does not explain why the bottle empties
  faster than logged flow predicts. That gap is real leak/unlogged draw,
  captured separately by the empirical calibration below.

--- Empirical calibration (effective start pressure + effective leak) -----
Argon was drawn (for drying the liquid-scintillator detectors) before
``DRYING_EVENT_END`` that never went through the mixer's flow meter, so
gauge photos from that window aren't explained by logged flow at all and
are excluded from calibration. Using only photos from ``DRYING_EVENT_END``
onward, we least-squares fit

    bar_measured = intercept + scale * P_physical(t)

where ``P_physical(t)`` is the Redlich-Kwong prediction above. ``intercept``
is the model's calibrated pressure at logging start (the "effective start
pressure" — corrects for the fact that the assumed ``ARGON_START_BAR`` was
just a rough analog-gauge reading of unknown bottle history). ``scale``
captures any further steady draw beyond both logged flow and the physics
correction (leak, a slow regulator bleed, etc.) as a single multiplicative
"effective leak": scale=1 would mean logged flow + real-gas physics fully
explain the measured points; scale>1 means the bottle is emptying faster
than that.

--- One-off untracked draws (``UNTRACKED_DRAWS``) -------------------------
``scale`` is a *rate* correction: it spreads unexplained consumption evenly
over time. A one-off draw that bypassed the flow meter (a purge, a line
fill) is not spread out at all — it is a step. Modelling a step as a rate
smears it backwards over every earlier photo and biases the leak term, so
each such event is instead declared in ``UNTRACKED_DRAWS`` and subtracted
from the mole count only for times *after* it happened.

The drying event before ``DRYING_EVENT_END`` is not on that list: it
predates the flow log entirely, so its photos are simply excluded from the
fit instead (there is no "before" to anchor against).

An event's size may be given (``nL``) or left to the fit (``nL: None``),
in which case it becomes a third fitted parameter: photos before the event
pin ``intercept``/``scale``, photos after it pin the step. Only one event
can be fitted at a time — any others must carry a known ``nL``. With no
photo yet after an event, the fit has nothing to bite on and the event's
``nL_guess`` is used unchanged.

A fitted step is only meaningful if it is big compared with the ~5 bar
scatter of the gauge photos: 1 bar in the 50 L bottle is ~50 normal litres,
so a draw of a few tens of litres is invisible here. ``resolvable`` in the
returned calibration says whether the fitted step cleared that bar, and
``nL_interval`` gives the range of step sizes the photos cannot distinguish
between (1-sigma profile). Do not quote a fitted step that is not
``resolvable`` as a measurement — it is the fit absorbing noise.
"""
import glob
import json
import os

import numpy as np
import pandas as pd

# --- Starting assumptions (EDIT THESE to match the real bottles) -------------
ARGON_BOTTLE_L = 50.0        # water volume of the argon bottle [L]
ARGON_START_BAR = 220.0      # rough analog-gauge reading when logging started;
                              # only used as a reference point for the physical
                              # model's shape -- the calibration fit (below)
                              # supplies the actual effective start pressure.

ISO_BOTTLE_L = 50.0          # water volume of the isobutane bottle [L]
ISO_START_LIQ_FRAC = 0.80    # fraction of the bottle that was liquid at start
ISO_LIQ_DENSITY = 0.551      # liquid isobutane density near 20 degC [kg/L]
ISO_MOLAR_MASS = 58.12       # isobutane molar mass [g/mol]
ISO_VAPOR_PRESSURE = 3.05    # ~vapour pressure (absolute) near 20 degC [bar]

NORMAL_MOLAR_VOL_L = 22.414  # normal litres per mole (0 degC, 1.013 bar)

# Redlich-Kwong constants for argon, from its critical point (NIST):
R_GAS = 0.0831446            # L*bar/(mol*K)
ARGON_TC_K = 150.687         # K
ARGON_PC_BAR = 48.63         # bar
_RK_A = 0.42748 * R_GAS**2 * ARGON_TC_K**2.5 / ARGON_PC_BAR
_RK_B = 0.08664 * R_GAS * ARGON_TC_K / ARGON_PC_BAR

# Argon lost drying the liquid-scintillator detectors, before this date, never
# passed through the flow meter -- gauge photos before it are excluded from
# the calibration fit (see module docstring).
DRYING_EVENT_END = pd.Timestamp("2026-07-13")

# One-off draws that bypassed the mixer's flow meter, subtracted from the mole
# count for all times after `t` (see module docstring). `nL` is the amount in
# normal litres, or None to fit it from the gauge photos -- at most one event
# may be fitted. `nL_guess` is the operator's estimate: it seeds the fit range
# and is used as-is when the fit cannot run (no photo after the event yet).
# The time only has to fall between the bracketing photos -- shifting it within
# a day with no readings in it changes nothing.
UNTRACKED_DRAWS = [
    {
        # Fitting this one was tried and abandoned. The three readings taken after it
        # (08-03/04/06) sit +1.2/-1.5/+0.8 bar from the model, i.e. exactly on it, and
        # the fit converged to 0.0 nL -- the bottom of its search range, meaning "no
        # information", not "measured zero". The readings do bound it: anything above
        # ~145 nL (~3 bar) would have shown. The operator's estimate is well inside
        # that bound and is better information than a boundary-pinned fit, so it is
        # used as known. Set nL back to None to re-fit if a big draw ever happens.
        "t": pd.Timestamp("2026-07-31 12:00"),
        "nL": 20.0,
        "nL_guess": 20.0,   # reported as 1-3 h at ~10 ln/h
        "note": "untracked draw off the panel, bypassed the mixer",
    },
]

# Range searched when fitting an untracked draw [normal litres]. 3000 nL is
# ~60 bar of this bottle -- far beyond any plausible one-off draw, so the bound
# only stops the search running away, it does not shape the answer.
UNTRACKED_FIT_MAX_NL = 3000.0

# Window used to extrapolate a steady draw rate for the time-to-empty estimate.
RECENT_WINDOW_H = 0.5        # hours

# Last-resort argon temperature [degC] if the sensor read nothing usable anywhere in
# the record. Temperature is only a correction term in the RK pressure, so a nominal
# hall value is far better than NaN, which propagates into the calibration fit.
FALLBACK_TEMP_C = 20.0

BOTTLE_PHOTO_CSV = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "calibrations", "gas_bottle", "argon_bottle_pressure.csv")

_FLOW_COLS = ["timestamp", "argon_flow_lnh", "iso_flow_lnh", "argon_temp_c"]

# Per-file cache so immutable past-day CSVs are re-parsed only once. Keyed by
# path -> (size, mtime, series dict). Only the current day's file is re-read
# as it grows.
_file_cache = {}


def _rk_pressure(n_mol, V_L, T_K):
    """Redlich-Kwong pressure [bar] for n_mol of argon in a fixed volume V_L
    [L] at temperature T_K [K]. Direct (non-iterative): the molar volume
    Vm = V/n is known exactly from the mole count, so P follows in one shot."""
    Vm = V_L / n_mol
    return R_GAS * T_K / (Vm - _RK_B) - _RK_A / (np.sqrt(T_K) * Vm * (Vm + _RK_B))


def _load_file_series(path):
    """Parse one day's CSV and trapezoid-integrate argon/iso flow with NO gap
    cap (steady flow assumed through logger gaps -- see module docstring).
    Returns per-row cumulative normal-litres drawn (local to this file) plus
    the temperature trace, so a caller can look up "cumulative drawn as of
    time t" at arbitrary points, not just a per-file total."""
    df = pd.read_csv(path, usecols=_FLOW_COLS)
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    # The timestamp and temperature traces are built identically whatever the row
    # count. They used to be built twice, and the <2-row branch returned datetime64
    # timestamps and raw temperatures where every other file returned int64 epochs
    # and sanitised ones -- np.concatenate in _build_history then raised
    # DTypePromotionError and took the whole /gas/bottle_usage endpoint down. A
    # one-row day is exactly what a watcher restart just before midnight leaves.
    t = df["timestamp"].values.astype("datetime64[s]").astype("int64")
    # sensor glitch guard: argon_temp_c occasionally drops to a spurious 0.0
    temp = df["argon_temp_c"].to_numpy(dtype=float)
    temp = np.where((temp < 10.0) | (temp > 35.0), np.nan, temp)
    # Left NaN if a whole file is bad; _build_history fills across file boundaries,
    # which a per-file ffill/bfill cannot do.
    temp_series = pd.Series(temp).ffill().bfill().to_numpy()
    first = df["timestamp"].iloc[0] if len(df) else None
    last = df["timestamp"].iloc[-1] if len(df) else None

    if len(df) < 2:
        return {"t": t, "cum_argon_nL": np.zeros(len(df)), "cum_iso_nL": np.zeros(len(df)),
                "temp_c": temp_series, "argon_total": 0.0, "iso_total": 0.0,
                "logged_h": 0.0, "first": first, "last": last}
    dt_h = np.diff(t) / 3600.0
    dt_h = np.clip(dt_h, 0.0, None)  # only guard against out-of-order rows
    a = df["argon_flow_lnh"].to_numpy(dtype=float)
    i = df["iso_flow_lnh"].to_numpy(dtype=float)
    argon_step = 0.5 * (a[1:] + a[:-1]) * dt_h
    iso_step = 0.5 * (i[1:] + i[:-1]) * dt_h
    cum_argon = np.concatenate([[0.0], np.cumsum(argon_step)])
    cum_iso = np.concatenate([[0.0], np.cumsum(iso_step)])
    return {"t": t, "cum_argon_nL": cum_argon, "cum_iso_nL": cum_iso, "temp_c": temp_series,
            "argon_total": float(cum_argon[-1]), "iso_total": float(cum_iso[-1]),
            "logged_h": float(dt_h.sum()), "first": first, "last": last}


def _cached_file_series(path):
    st = os.stat(path)
    sig = (st.st_size, st.st_mtime)
    hit = _file_cache.get(path)
    if hit and hit[0] == sig:
        return hit[1]
    series = _load_file_series(path)
    _file_cache[path] = (sig, series)
    return series


def _build_history(files):
    """Chain per-file series into one global (timestamp, cumulative-nL,
    temperature) history, offsetting each file's local cumulative sum by the
    running total of everything before it."""
    t_parts, argon_parts, iso_parts, temp_parts = [], [], [], []
    argon_offset = iso_offset = 0.0
    argon_total = iso_total = logged_h = 0.0
    start = end = None
    for f in files:
        s = _cached_file_series(f)
        if len(s["t"]):
            t_parts.append(s["t"])
            argon_parts.append(s["cum_argon_nL"] + argon_offset)
            iso_parts.append(s["cum_iso_nL"] + iso_offset)
            temp_parts.append(s["temp_c"])
        argon_offset += s["argon_total"]
        iso_offset += s["iso_total"]
        argon_total += s["argon_total"]
        iso_total += s["iso_total"]
        logged_h += s["logged_h"]
        if s["first"] is not None and (start is None or s["first"] < start):
            start = s["first"]
        if s["last"] is not None and (end is None or s["last"] > end):
            end = s["last"]
    history = {
        "t": np.concatenate(t_parts) if t_parts else np.array([], dtype="int64"),
        "cum_argon_nL": np.concatenate(argon_parts) if argon_parts else np.array([]),
        "cum_iso_nL": np.concatenate(iso_parts) if iso_parts else np.array([]),
        "temp_c": np.concatenate(temp_parts) if temp_parts else np.array([]),
    }
    # A day whose argon_temp_c is out of range for every single row (stuck sensor)
    # comes back all-NaN: the per-file fill had no good sample to reach for. Fill
    # across file boundaries, where the neighbouring days are. If the whole record
    # is bad, fall back to a nominal temperature -- NaN here would reach np.polyfit
    # in _fit_calibration, which RAISES LinAlgError rather than returning NaN, and
    # one bad day of a correction term would take out every argon number.
    if len(history["temp_c"]):
        history["temp_c"] = (pd.Series(history["temp_c"]).ffill().bfill()
                             .fillna(FALLBACK_TEMP_C).to_numpy())
    return history, argon_total, iso_total, logged_h, start, end


def _at(history, key, ts):
    """Nearest-sample lookup of history[key] at timestamp ts (pandas Timestamp)."""
    if len(history["t"]) == 0:
        return None
    tq = np.int64(ts.value // 10**9)
    idx = np.searchsorted(history["t"], tq)
    idx = np.clip(idx, 0, len(history["t"]) - 1)
    if idx > 0 and abs(history["t"][idx - 1] - tq) < abs(history["t"][idx] - tq):
        idx -= 1
    return float(history[key][idx])


def _fitted_event_index():
    """Index of the single UNTRACKED_DRAWS entry whose size is fitted, or None.
    Later entries with nL=None fall back to their guess (see docstring)."""
    for i, ev in enumerate(UNTRACKED_DRAWS):
        if ev.get("nL") is None:
            return i
    return None


def _untracked_nL(ts, step_nL=None):
    """Normal litres of untracked draw that had already happened by time ts.
    `step_nL` overrides the size of the fitted event (None -> use its guess)."""
    fitted = _fitted_event_index()
    total = 0.0
    for i, ev in enumerate(UNTRACKED_DRAWS):
        if ts < ev["t"]:
            continue
        amount = ev.get("nL")
        if amount is None:
            amount = ev["nL_guess"] if (i != fitted or step_nL is None) else step_nL
        total += float(amount)
    return total


def _physical_pressure(history, ts, n0_mol, step_nL=None):
    """Redlich-Kwong-predicted argon pressure at time ts, given n0_mol moles
    present at logging start, and everything drawn since then: the logged flow
    (from history) plus any UNTRACKED_DRAWS that had already happened by ts."""
    drawn_nL = _at(history, "cum_argon_nL", ts) + _untracked_nL(ts, step_nL)
    drawn_mol = drawn_nL / NORMAL_MOLAR_VOL_L
    T_K = _at(history, "temp_c", ts) + 273.15
    n_mol = max(n0_mol - drawn_mol, 1e-6)
    return _rk_pressure(n_mol, ARGON_BOTTLE_L, T_K)


def _linear_fit(phys, bars):
    """bar = intercept + scale * P_phys. Returns (scale, intercept, SSR)."""
    scale, intercept = np.polyfit(phys, bars, 1)
    resid = bars - (intercept + scale * phys)
    return float(scale), float(intercept), float(np.sum(resid**2))


def _refine_step(ssr_of_step, lo, hi, rounds=4, samples=41):
    """Minimise ssr_of_step over [lo, hi] by repeated grid refinement. The
    inner intercept/scale fit is linear and exact, so only this one parameter
    is searched, and SSR along it is smooth and single-minimum -- a bracketing
    grid is enough and keeps scipy out of the dependency list."""
    grid = np.linspace(lo, hi, samples)
    for _ in range(rounds):
        ssr = np.array([ssr_of_step(s) for s in grid])
        j = int(np.argmin(ssr))
        lo, hi = grid[max(j - 1, 0)], grid[min(j + 1, len(grid) - 1)]
        grid = np.linspace(lo, hi, samples)
    return float(grid[int(np.argmin(np.array([ssr_of_step(s) for s in grid])))])


def _step_interval(ssr_of_step, best_ssr, n_pts, n_params, max_nL):
    """Step sizes the photos cannot tell apart from the best one: the standard
    1-sigma profile band, SSR <= SSR_min * (1 + 1/(n - p)). A band covering
    most of the search range means the data does not constrain the step."""
    dof = n_pts - n_params
    if dof < 1 or best_ssr <= 0:
        return None
    limit = best_ssr * (1.0 + 1.0 / dof)
    grid = np.linspace(0.0, max_nL, 601)
    inside = grid[np.array([ssr_of_step(s) for s in grid]) <= limit]
    if not len(inside):
        return None
    return [round(float(inside.min()), 1), round(float(inside.max()), 1)]


def _fit_calibration(history, start_ts, n0_mol):
    """Least-squares fit of measured gauge photos (after the drying-event
    cutoff) against the physical model: bar = intercept + scale * P_phys,
    plus the size of one untracked draw if UNTRACKED_DRAWS leaves it open.
    Returns None if there aren't enough post-cutoff photos to fit."""
    if not os.path.exists(BOTTLE_PHOTO_CSV):
        return None
    photos = pd.read_csv(BOTTLE_PHOTO_CSV)
    photos["timestamp"] = pd.to_datetime(photos["timestamp"], errors="coerce")
    photos = photos.dropna(subset=["timestamp", "bar"])
    photos = photos[photos["status"] == "ok"] if "status" in photos else photos
    fit_pts = photos[photos["timestamp"] >= DRYING_EVENT_END].sort_values("timestamp")
    if len(fit_pts) < 2:
        return None

    fitted = _fitted_event_index()
    guess_nL = UNTRACKED_DRAWS[fitted]["nL_guess"] if fitted is not None else None
    phys = np.array([_physical_pressure(history, ts, n0_mol, guess_nL)
                     for ts in fit_pts["timestamp"]])
    bars = fit_pts["bar"].to_numpy(dtype=float)
    # np.polyfit RAISES on a non-finite point (LinAlgError: SVD did not converge), so
    # drop them rather than let one unusable photo kill the whole argon block. The
    # mask comes from the history lookup, not from the step size, so it is stable
    # across the search below and is applied once, here.
    good = np.isfinite(phys) & np.isfinite(bars)
    if int(good.sum()) < 2:
        return None
    bars, fit_pts = bars[good], fit_pts[good]
    ts_pts = list(fit_pts["timestamp"])

    def phys_at(step_nL):
        return np.array([_physical_pressure(history, ts, n0_mol, step_nL) for ts in ts_pts])

    def ssr_of_step(step_nL):
        return _linear_fit(phys_at(step_nL), bars)[2]

    # The step is only identifiable from photos taken AFTER the event -- with none
    # yet, every candidate step shifts the post-event points nowhere and the search
    # would wander over a flat SSR. Fall back to the operator's estimate instead.
    n_after = int(sum(ts > UNTRACKED_DRAWS[fitted]["t"] for ts in ts_pts)) if fitted is not None else 0
    step_nL, step_source, step_interval = None, None, None
    if fitted is not None:
        if n_after:
            step_nL = _refine_step(ssr_of_step, 0.0, UNTRACKED_FIT_MAX_NL)
            step_source = "fit"
        else:
            step_nL, step_source = float(guess_nL), "guess"

    phys = phys_at(step_nL)
    scale, intercept, ssr = _linear_fit(phys, bars)
    resid = bars - (intercept + scale * phys)
    rms = float(np.sqrt(np.mean(resid**2)))
    if step_source == "fit":
        step_interval = _step_interval(ssr_of_step, ssr, len(bars), 3, UNTRACKED_FIT_MAX_NL)

    start_phys = _physical_pressure(history, start_ts, n0_mol, step_nL)
    calib = {
        "intercept": float(intercept),
        "scale": float(scale),
        "effective_start_bar": round(float(intercept + scale * start_phys), 1),
        "effective_leak_pct": round(float((scale - 1.0) * 100), 1),
        "n_fit_points": int(len(fit_pts)),
        "rms_residual_bar": round(rms, 2),
        "drying_event_end": str(DRYING_EVENT_END.date()),
        "excluded_early_photos": int((photos["timestamp"] < DRYING_EVENT_END).sum()),
        "untracked_draws": _describe_untracked(history, n0_mol, scale, step_nL),
    }
    if fitted is not None:
        ev = calib["untracked_draws"][fitted]
        ev["source"] = step_source
        ev["n_photos_after"] = n_after
        ev["nL_interval"] = step_interval
        # "Did the photos actually see this step?" -- a step smaller than the fit's
        # own scatter is the fit absorbing noise, not a measurement.
        ev["resolvable"] = bool(step_source == "fit" and ev["bar_equiv"] >= rms)
    return calib


def _describe_untracked(history, n0_mol, scale, step_nL):
    """Report each untracked draw in calibrated bar as well as normal litres:
    the pressure the bottle lost to it, which is what the gauge would have
    shown, and the only form in which its size can be judged against the
    photos' scatter."""
    fitted = _fitted_event_index()
    out = []
    for i, ev in enumerate(UNTRACKED_DRAWS):
        amount = ev.get("nL")
        if amount is None:
            amount = step_nL if (i == fitted and step_nL is not None) else ev["nL_guess"]
        # Pressure just before the event, minus pressure with the step removed:
        # evaluated at the same instant, so temperature and logged draw cancel.
        after = _physical_pressure(history, ev["t"], n0_mol, step_nL)
        drawn_nL = _at(history, "cum_argon_nL", ev["t"]) + _untracked_nL(ev["t"], step_nL) - amount
        T_K = _at(history, "temp_c", ev["t"]) + 273.15
        before = _rk_pressure(max(n0_mol - drawn_nL / NORMAL_MOLAR_VOL_L, 1e-6),
                              ARGON_BOTTLE_L, T_K)
        out.append({
            "when": ev["t"].strftime("%Y-%m-%d %H:%M"),
            "note": ev.get("note", ""),
            "nL": round(float(amount), 1),
            "bar_equiv": round(float(scale * (before - after)), 2),
            "source": "known" if ev.get("nL") is not None else None,
        })
    return out


def _days_to_empty(remaining, decline_per_day):
    """Generic linear time-to-empty: remaining and decline_per_day just need
    matching units (bar & bar/day, or normal-litres & normal-litres/day)."""
    if decline_per_day is None or decline_per_day <= 1e-9 or remaining <= 0:
        return None
    return remaining / decline_per_day


def compute_bottle_usage(log_dir):
    """Integrate the whole flow history and return the bottle-usage estimate.

    Returns a JSON-friendly dict: total litres drawn, remaining pressure/liquid,
    fraction left, recent draw rate and extrapolated days-to-empty for each gas.
    """
    files = sorted(glob.glob(os.path.join(log_dir, "gas_flow_*.csv")))
    if not files:
        return {"success": False, "message": "no gas flow logs found"}

    history, argon_nL, iso_nL, logged_h, start, end = _build_history(files)
    if start is None:
        return {"success": False, "message": "no gas flow logs found"}

    n0_mol = (ARGON_START_BAR * ARGON_BOTTLE_L) / NORMAL_MOLAR_VOL_L
    calib = _fit_calibration(history, start, n0_mol)
    intercept, scale = (calib["intercept"], calib["scale"]) if calib else (0.0, 1.0)
    # Size the fit settled on for the open untracked draw, so every pressure
    # below is evaluated on the same mole count the calibration was fitted to.
    fitted = _fitted_event_index()
    step_nL = (calib["untracked_draws"][fitted]["nL"]
               if (calib and fitted is not None) else None)

    now_ts = end
    current_phys = _physical_pressure(history, now_ts, n0_mol, step_nL)
    current_bar = intercept + scale * current_phys

    prev_ts = now_ts - pd.Timedelta(hours=RECENT_WINDOW_H)
    raw_rate_lnh = float((history["cum_argon_nL"][-1] - _at(history, "cum_argon_nL", prev_ts))
                          / RECENT_WINDOW_H) if len(history["t"]) else 0.0

    # Depletion-only slope: hold temperature fixed at its current value and
    # only vary the mole count, so a short-window temperature wiggle (the
    # RK pressure also depends on T) can't swamp or reverse the tiny amount
    # of real depletion in a RECENT_WINDOW_H-sized step.
    now_T_K = _at(history, "temp_c", now_ts) + 273.15
    now_drawn_mol = ((_at(history, "cum_argon_nL", now_ts) + _untracked_nL(now_ts, step_nL))
                     / NORMAL_MOLAR_VOL_L)
    prev_drawn_mol = ((_at(history, "cum_argon_nL", prev_ts) + _untracked_nL(prev_ts, step_nL))
                      / NORMAL_MOLAR_VOL_L)
    n_now = max(n0_mol - now_drawn_mol, 1e-6)
    n_prev = max(n0_mol - prev_drawn_mol, 1e-6)
    phys_now_fixedT = _rk_pressure(n_now, ARGON_BOTTLE_L, now_T_K)
    phys_prev_fixedT = _rk_pressure(n_prev, ARGON_BOTTLE_L, now_T_K)
    raw_decline_bar_per_h = (phys_prev_fixedT - phys_now_fixedT) / RECENT_WINDOW_H
    decline_bar_per_day = max(scale * raw_decline_bar_per_h, 0.0) * 24.0

    argon_content0_L = ARGON_START_BAR * ARGON_BOTTLE_L
    # Progress bar reflects the CALIBRATED pressure (so it shows the ~13% leak
    # too), not just logged draw vs. the nominal start pressure.
    eff_start_bar = calib["effective_start_bar"] if calib else ARGON_START_BAR
    argon_frac_remaining = float(np.clip(current_bar / eff_start_bar, 0.0, 1.0)) if eff_start_bar else 0.0
    # Litres left come from that SAME calibrated fraction. They used to be
    # content0 - logged draw, i.e. the old linear model, so the card printed a
    # litre count and a percentage that disagreed by the whole leak term
    # ("6432 L / 48.9 %", where 6432 of 11000 L is 58.5 %). Note drawn_L +
    # remaining_L therefore no longer sums to the start content: the gap IS the
    # leak, which the calibration line spells out.
    argon_remaining_L = argon_frac_remaining * argon_content0_L

    argon_days_to_empty = _days_to_empty(current_bar, decline_bar_per_day)
    argon = {
        "drawn_L": round(argon_nL, 1),
        "untracked_drawn_L": round(_untracked_nL(now_ts, step_nL), 1),
        "content_start_L": round(argon_content0_L, 1),
        "remaining_L": round(argon_remaining_L, 1),
        "frac_remaining": round(argon_frac_remaining, 4),
        "pressure_bar": round(current_bar, 1),
        "pressure_physical_uncalibrated_bar": round(current_phys, 1),
        "pressure_start_bar": ARGON_START_BAR,
        "rate_lnh": round(raw_rate_lnh, 4),
        "effective_rate_lnh": round(raw_rate_lnh * scale, 4),
        "days_to_empty": round(argon_days_to_empty, 1) if argon_days_to_empty else None,
        "empty_date": ((now_ts + pd.Timedelta(days=argon_days_to_empty)).strftime("%Y-%m-%d")
                       if argon_days_to_empty else None),
        "calibration": calib,
    }

    # --- Isobutane: liquefied gas, track liquid remaining (unchanged model) ---
    iso_liq0_L = ISO_BOTTLE_L * ISO_START_LIQ_FRAC
    iso_content0_nL = (iso_liq0_L * ISO_LIQ_DENSITY * 1000.0 / ISO_MOLAR_MASS
                       * NORMAL_MOLAR_VOL_L)
    iso_remaining_nL = max(iso_content0_nL - iso_nL, 0.0)
    iso_frac = iso_remaining_nL / iso_content0_nL if iso_content0_nL else 0.0
    iso_rate = float((history["cum_iso_nL"][-1] - _at(history, "cum_iso_nL", prev_ts))
                      / RECENT_WINDOW_H) if len(history["t"]) else 0.0
    iso_days_to_empty = _days_to_empty(iso_remaining_nL, iso_rate * 24.0)
    iso = {
        "drawn_L": round(iso_nL, 1),
        "content_start_L": round(iso_content0_nL, 1),
        "remaining_L": round(iso_remaining_nL, 1),
        "frac_remaining": round(iso_frac, 4),
        "liquid_start_L": round(iso_liq0_L, 1),
        "liquid_remaining_L": round(iso_liq0_L * iso_frac, 2),
        "vapor_pressure_bar": ISO_VAPOR_PRESSURE if iso_remaining_nL > 0 else 0.0,
        "rate_lnh": round(iso_rate, 4),
        "days_to_empty": round(iso_days_to_empty, 1) if iso_days_to_empty else None,
        "empty_date": ((end + pd.Timedelta(days=iso_days_to_empty)).strftime("%Y-%m-%d")
                       if iso_days_to_empty else None),
    }

    span_h = (end - start).total_seconds() / 3600.0 if start is not None else 0.0
    return {
        "success": True,
        "start": start.strftime("%Y-%m-%d %H:%M:%S") if start is not None else None,
        "end": end.strftime("%Y-%m-%d %H:%M:%S") if end is not None else None,
        "span_hours": round(span_h, 2),
        "logged_hours": round(logged_h, 2),
        "argon": argon,
        "iso": iso,
        "assumptions": {
            "argon_bottle_L": ARGON_BOTTLE_L, "argon_start_bar": ARGON_START_BAR,
            "iso_bottle_L": ISO_BOTTLE_L, "iso_start_liq_frac": ISO_START_LIQ_FRAC,
            "iso_liq_density": ISO_LIQ_DENSITY, "iso_vapor_pressure_bar": ISO_VAPOR_PRESSURE,
            "rk_argon_tc_K": ARGON_TC_K, "rk_argon_pc_bar": ARGON_PC_BAR,
        },
    }


PLANNED_STOP_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "projections", "schedule.json")


def _planned_stop():
    """When the run is expected to end, from the statistics projection's schedule.
    Read rather than restated so the date has ONE home: the gas panel and the
    stats projection must never disagree about when beam stops. Missing or
    unparseable file just means no marker -- it is decoration, not a dependency."""
    try:
        with open(PLANNED_STOP_PATH) as f:
            return pd.Timestamp(json.load(f)["run_end"])
    except Exception:
        return None


def _forecast(history, n0_mol, step_nL, intercept, scale, end, step_h=6.0, max_days=200):
    """Project the bottle forward at the current draw rate until it reads zero.

    Moles are drawn down at the *logged* rate and the leak enters through the
    calibration's `scale`, exactly as the decline rate in compute_bottle_usage does
    -- inflating the mole draw AND applying scale would count the leak twice.
    Temperature is held at its current value: the bottle's day/night swing is a
    couple of bar either way and would otherwise put a fake ripple on a forecast.
    """
    prev_ts = end - pd.Timedelta(hours=RECENT_WINDOW_H)
    rate_lnh = ((history["cum_argon_nL"][-1] - _at(history, "cum_argon_nL", prev_ts))
                / RECENT_WINDOW_H) if len(history["t"]) else 0.0
    if rate_lnh <= 1e-9:
        return None, None          # nothing flowing: "time to empty" has no meaning
    T_K = _at(history, "temp_c", end) + 273.15
    n_now = max(n0_mol - (_at(history, "cum_argon_nL", end) + _untracked_nL(end, step_nL))
                / NORMAL_MOLAR_VOL_L, 1e-6)
    per_h_mol = rate_lnh / NORMAL_MOLAR_VOL_L

    def bar_at(hours):
        n = max(n_now - per_h_mol * hours, 1e-6)
        return intercept + scale * _rk_pressure(n, ARGON_BOTTLE_L, T_K)

    times, bars = [], []
    for i in range(int(max_days * 24 / step_h) + 1):
        hours = i * step_h
        bar = bar_at(hours)
        times.append(end + pd.Timedelta(hours=hours))
        bars.append(bar)
        if bar <= 0:
            break
    return {"time": [t.strftime("%Y-%m-%d %H:%M:%S") for t in times],
            "bar": [round(float(b), 2) for b in bars],
            "empty_when": times[-1].strftime("%Y-%m-%d %H:%M") if bars[-1] <= 0 else None,
            "rate_lnh": round(float(rate_lnh), 3)}, bar_at


def compute_bottle_history(log_dir, step_h=2.0):
    """The argon model as a plottable series, for "model vs measured" on the Gas tab.

    `compute_bottle_usage` answers "how full is it now"; this answers "has the model
    been tracking the gauge", which is the question that says whether to trust the
    first answer. Returns the modelled pressure curve sampled every `step_h` hours,
    every gauge reading with its residual against that curve, and the untracked-draw
    events -- everything needed to redraw the calibration plot.
    """
    files = sorted(glob.glob(os.path.join(log_dir, "gas_flow_*.csv")))
    if not files:
        return {"success": False, "message": "no gas flow logs found"}
    history, argon_nL, _iso, _logged, start, end = _build_history(files)
    if start is None:
        return {"success": False, "message": "no gas flow logs found"}

    n0_mol = (ARGON_START_BAR * ARGON_BOTTLE_L) / NORMAL_MOLAR_VOL_L
    calib = _fit_calibration(history, start, n0_mol)
    intercept, scale = (calib["intercept"], calib["scale"]) if calib else (0.0, 1.0)
    fitted = _fitted_event_index()
    step_nL = (calib["untracked_draws"][fitted]["nL"]
               if (calib and fitted is not None) else None)

    # Sampled coarsely on purpose: the curve is smooth on any scale a person reads,
    # so hourly-or-finer sampling would only make the payload bigger. Two-hour steps
    # over a month is ~360 points.
    times = pd.date_range(start, end, freq=pd.Timedelta(hours=step_h))
    if len(times) == 0 or times[-1] < end:
        times = times.append(pd.DatetimeIndex([end]))
    phys = np.array([_physical_pressure(history, ts, n0_mol, step_nL) for ts in times])

    readings = []
    if os.path.exists(BOTTLE_PHOTO_CSV):
        photos = pd.read_csv(BOTTLE_PHOTO_CSV)
        photos["timestamp"] = pd.to_datetime(photos["timestamp"], errors="coerce")
        photos = photos.dropna(subset=["timestamp", "bar"]).sort_values("timestamp")
        if "status" in photos:
            photos = photos[photos["status"] == "ok"]
        for _, r in photos.iterrows():
            p = _physical_pressure(history, r["timestamp"], n0_mol, step_nL)
            model = intercept + scale * p
            # Points before the drying cutoff are drawn but never fitted, so their
            # residual is not a fit residual -- it is the size of the unlogged
            # drying draw. Flagged, not hidden, so the early gap is explained.
            in_fit = bool(r["timestamp"] >= DRYING_EVENT_END)
            readings.append({
                "t": r["timestamp"].strftime("%Y-%m-%d %H:%M"),
                "bar": round(float(r["bar"]), 1),
                "model_bar": round(float(model), 1) if np.isfinite(model) else None,
                "residual_bar": round(float(r["bar"] - model), 1) if np.isfinite(model) else None,
                "in_fit": in_fit,
                "source": str(r.get("source", "") or ""),
            })

    forecast, bar_at = _forecast(history, n0_mol, step_nL, intercept, scale, end)

    # Pressure at the planned end of the run: the number that actually decides
    # whether a bottle change is needed before then.
    stop = _planned_stop()
    planned = None
    if stop is not None and bar_at is not None:
        hours = (stop - end).total_seconds() / 3600.0
        bar_then = bar_at(hours) if hours >= 0 else (intercept + scale * phys[-1])
        eff_start = (calib or {}).get("effective_start_bar") or ARGON_START_BAR
        planned = {
            "when": stop.strftime("%Y-%m-%d %H:%M"),
            "bar": round(float(max(bar_then, 0.0)), 1),
            "frac_remaining": round(float(np.clip(bar_then / eff_start, 0.0, 1.0)), 4),
            "days_from_now": round(hours / 24.0, 1),
            "reaches_empty_first": bool(bar_then <= 0),
            "source": "projections/schedule.json run_end",
        }

    return {
        "success": True,
        "time": [t.strftime("%Y-%m-%d %H:%M:%S") for t in times],
        "model_bar": [round(float(intercept + scale * p), 2) for p in phys],
        "model_uncalibrated_bar": [round(float(p), 2) for p in phys],
        "readings": readings,
        "events": (calib or {}).get("untracked_draws", []),
        "calibration": calib,
        "drying_event_end": str(DRYING_EVENT_END.date()),
        "forecast": forecast,
        "planned_stop": planned,
    }
