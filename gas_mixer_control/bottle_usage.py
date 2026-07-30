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
"""
import glob
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

# Window used to extrapolate a steady draw rate for the time-to-empty estimate.
RECENT_WINDOW_H = 0.5        # hours

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
    if len(df) < 2:
        return {"t": df["timestamp"].to_numpy(), "cum_argon_nL": np.zeros(len(df)),
                "cum_iso_nL": np.zeros(len(df)), "temp_c": df["argon_temp_c"].to_numpy(),
                "argon_total": 0.0, "iso_total": 0.0, "logged_h": 0.0,
                "first": df["timestamp"].min() if len(df) else None,
                "last": df["timestamp"].max() if len(df) else None}
    t = df["timestamp"].values.astype("datetime64[s]").astype("int64")
    dt_h = np.diff(t) / 3600.0
    dt_h = np.clip(dt_h, 0.0, None)  # only guard against out-of-order rows
    a = df["argon_flow_lnh"].to_numpy(dtype=float)
    i = df["iso_flow_lnh"].to_numpy(dtype=float)
    argon_step = 0.5 * (a[1:] + a[:-1]) * dt_h
    iso_step = 0.5 * (i[1:] + i[:-1]) * dt_h
    cum_argon = np.concatenate([[0.0], np.cumsum(argon_step)])
    cum_iso = np.concatenate([[0.0], np.cumsum(iso_step)])
    # sensor glitch guard: argon_temp_c occasionally drops to a spurious 0.0
    temp = df["argon_temp_c"].to_numpy(dtype=float)
    temp = np.where((temp < 10.0) | (temp > 35.0), np.nan, temp)
    temp_series = pd.Series(temp).ffill().bfill().to_numpy()
    return {"t": t, "cum_argon_nL": cum_argon, "cum_iso_nL": cum_iso, "temp_c": temp_series,
            "argon_total": float(cum_argon[-1]), "iso_total": float(cum_iso[-1]),
            "logged_h": float(dt_h.sum()),
            "first": df["timestamp"].iloc[0], "last": df["timestamp"].iloc[-1]}


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


def _physical_pressure(history, ts, n0_mol):
    """Redlich-Kwong-predicted argon pressure at time ts, given n0_mol moles
    present at logging start and the flow drawn (from history) since then."""
    drawn_mol = _at(history, "cum_argon_nL", ts) / NORMAL_MOLAR_VOL_L
    T_K = _at(history, "temp_c", ts) + 273.15
    n_mol = max(n0_mol - drawn_mol, 1e-6)
    return _rk_pressure(n_mol, ARGON_BOTTLE_L, T_K)


def _fit_calibration(history, start_ts, n0_mol):
    """Least-squares fit of measured gauge photos (after the drying-event
    cutoff) against the physical model: bar = intercept + scale * P_phys.
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

    phys = np.array([_physical_pressure(history, ts, n0_mol) for ts in fit_pts["timestamp"]])
    bars = fit_pts["bar"].to_numpy(dtype=float)
    scale, intercept = np.polyfit(phys, bars, 1)
    resid = bars - (intercept + scale * phys)

    start_phys = _physical_pressure(history, start_ts, n0_mol)
    return {
        "intercept": float(intercept),
        "scale": float(scale),
        "effective_start_bar": round(float(intercept + scale * start_phys), 1),
        "effective_leak_pct": round(float((scale - 1.0) * 100), 1),
        "n_fit_points": int(len(fit_pts)),
        "rms_residual_bar": round(float(np.sqrt(np.mean(resid**2))), 2),
        "drying_event_end": str(DRYING_EVENT_END.date()),
        "excluded_early_photos": int((photos["timestamp"] < DRYING_EVENT_END).sum()),
    }


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

    now_ts = end
    current_phys = _physical_pressure(history, now_ts, n0_mol)
    current_bar = intercept + scale * current_phys

    prev_ts = now_ts - pd.Timedelta(hours=RECENT_WINDOW_H)
    raw_rate_lnh = float((history["cum_argon_nL"][-1] - _at(history, "cum_argon_nL", prev_ts))
                          / RECENT_WINDOW_H) if len(history["t"]) else 0.0

    # Depletion-only slope: hold temperature fixed at its current value and
    # only vary the mole count, so a short-window temperature wiggle (the
    # RK pressure also depends on T) can't swamp or reverse the tiny amount
    # of real depletion in a RECENT_WINDOW_H-sized step.
    now_T_K = _at(history, "temp_c", now_ts) + 273.15
    now_drawn_mol = _at(history, "cum_argon_nL", now_ts) / NORMAL_MOLAR_VOL_L
    prev_drawn_mol = _at(history, "cum_argon_nL", prev_ts) / NORMAL_MOLAR_VOL_L
    n_now = max(n0_mol - now_drawn_mol, 1e-6)
    n_prev = max(n0_mol - prev_drawn_mol, 1e-6)
    phys_now_fixedT = _rk_pressure(n_now, ARGON_BOTTLE_L, now_T_K)
    phys_prev_fixedT = _rk_pressure(n_prev, ARGON_BOTTLE_L, now_T_K)
    raw_decline_bar_per_h = (phys_prev_fixedT - phys_now_fixedT) / RECENT_WINDOW_H
    decline_bar_per_day = max(scale * raw_decline_bar_per_h, 0.0) * 24.0

    argon_content0_L = ARGON_START_BAR * ARGON_BOTTLE_L
    argon_remaining_L = max(argon_content0_L - argon_nL, 0.0)
    # Progress bar reflects the CALIBRATED pressure (so it shows the ~13% leak
    # too), not just logged draw vs. the nominal start pressure.
    eff_start_bar = calib["effective_start_bar"] if calib else ARGON_START_BAR
    argon_frac_remaining = float(np.clip(current_bar / eff_start_bar, 0.0, 1.0)) if eff_start_bar else 0.0

    argon_days_to_empty = _days_to_empty(current_bar, decline_bar_per_day)
    argon = {
        "drawn_L": round(argon_nL, 1),
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
