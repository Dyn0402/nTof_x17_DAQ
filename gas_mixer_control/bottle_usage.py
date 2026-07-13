"""Estimate gas-bottle usage from the logged mixer flow.

The gas_watcher logs the measured flow of each controller (columns
``argon_flow_lnh`` / ``iso_flow_lnh``) to per-day CSVs. "ln/h" is *normal*
litres per hour — Bronkhorst's normal reference is 0 degC, 1.013 bar, i.e.
22.414 normal litres per mole. Integrating the flow over the whole logging
history therefore gives the total normal litres drawn from each bottle, which we
convert to a remaining bottle pressure (argon) or remaining liquid (isobutane)
and an extrapolated time-to-empty.

    normal litres drawn  =  integral of flow[ln/h] dt[h]

Two gas types, two very different pressure models:

  * Argon is a compressed gas. A 50 L bottle at 220 bar holds ~220*50 = 11000
    normal litres; the pressure falls ~linearly as gas is removed:
        P_now = P_start - drawn_nL / bottle_volume_L
    (ideal-gas / linear-gauge approximation; real-gas compressibility at 220 bar
    adds a ~10 % level error but the *shape* — and the time-to-empty — hold).

  * Isobutane is a *liquefied* gas. While any liquid remains the head pressure
    sits at its vapour pressure (~3 bar abs near 20 degC) and barely moves; it
    only collapses once the liquid is gone. So the meaningful gauge for iso is
    the *liquid remaining*, not a dropping pressure. We track litres of liquid
    left and report the ~constant vapour pressure alongside it.

ALL the starting assumptions live in the block below — edit them to match the
actual bottles / regulators.
"""
import glob
import os

import numpy as np
import pandas as pd

# --- Starting assumptions (EDIT THESE to match the real bottles) -------------
ARGON_BOTTLE_L = 50.0        # water volume of the argon bottle [L]
ARGON_START_BAR = 220.0      # bottle pressure when logging started [bar]

ISO_BOTTLE_L = 50.0          # water volume of the isobutane bottle [L]
ISO_START_LIQ_FRAC = 0.80    # fraction of the bottle that was liquid at start
ISO_LIQ_DENSITY = 0.551      # liquid isobutane density near 20 degC [kg/L]
ISO_MOLAR_MASS = 58.12       # isobutane molar mass [g/mol]
ISO_VAPOR_PRESSURE = 3.05    # ~vapour pressure (absolute) near 20 degC [bar]

NORMAL_MOLAR_VOL_L = 22.414  # normal litres per mole (0 degC, 1.013 bar)

# Robustness: if the log has a gap (watcher restart etc.) longer than this, the
# trapezoid step is capped so a long unlogged stretch can't blow up the integral.
MAX_GAP_H = 0.5              # hours

# Window used to extrapolate a steady draw rate for the time-to-empty estimate.
RECENT_WINDOW_H = 0.5        # hours

_FLOW_COLS = ["timestamp", "argon_flow_lnh", "iso_flow_lnh"]

# Per-file cache so immutable past-day CSVs are integrated only once. Keyed by
# path -> (size, mtime, partial dict). Only the current day's file is re-read as
# it grows.
_file_cache = {}


def _integrate_file(path):
    """Trapezoidal integral of argon/iso flow over one CSV. Returns a dict with
    the normal-litres drawn, the logged hours, and the first/last timestamps."""
    df = pd.read_csv(path, usecols=_FLOW_COLS)
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"]).sort_values("timestamp")
    if len(df) < 2:
        return {"argon_nL": 0.0, "iso_nL": 0.0, "logged_h": 0.0,
                "first": df["timestamp"].min() if len(df) else None,
                "last": df["timestamp"].max() if len(df) else None}
    t = df["timestamp"].values.astype("datetime64[s]").astype("int64")
    dt_h = np.diff(t) / 3600.0
    dt_h = np.clip(dt_h, 0.0, MAX_GAP_H)            # drop negatives, cap gaps
    a = df["argon_flow_lnh"].to_numpy(dtype=float)
    i = df["iso_flow_lnh"].to_numpy(dtype=float)
    argon_nL = float((0.5 * (a[1:] + a[:-1]) * dt_h).sum())
    iso_nL = float((0.5 * (i[1:] + i[:-1]) * dt_h).sum())
    return {"argon_nL": argon_nL, "iso_nL": iso_nL, "logged_h": float(dt_h.sum()),
            "first": df["timestamp"].iloc[0], "last": df["timestamp"].iloc[-1]}


def _cached_integral(path):
    st = os.stat(path)
    sig = (st.st_size, st.st_mtime)
    hit = _file_cache.get(path)
    if hit and hit[0] == sig:
        return hit[1]
    part = _integrate_file(path)
    _file_cache[path] = (sig, part)
    return part


def _recent_rate(path):
    """Mean argon/iso flow (ln/h) over the last RECENT_WINDOW_H of the newest
    file — a steady draw rate for the time-to-empty extrapolation."""
    df = pd.read_csv(path, usecols=_FLOW_COLS)
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"])
    if df.empty:
        return 0.0, 0.0
    cutoff = df["timestamp"].max() - pd.Timedelta(hours=RECENT_WINDOW_H)
    win = df[df["timestamp"] >= cutoff]
    return float(win["argon_flow_lnh"].mean()), float(win["iso_flow_lnh"].mean())


def _days_to_empty(remaining_nL, rate_lnh):
    if rate_lnh is None or rate_lnh <= 1e-6 or remaining_nL <= 0:
        return None
    return (remaining_nL / rate_lnh) / 24.0


def compute_bottle_usage(log_dir):
    """Integrate the whole flow history and return the bottle-usage estimate.

    Returns a JSON-friendly dict: total litres drawn, remaining pressure/liquid,
    fraction left, recent draw rate and extrapolated days-to-empty for each gas.
    """
    files = sorted(glob.glob(os.path.join(log_dir, "gas_flow_*.csv")))
    if not files:
        return {"success": False, "message": "no gas flow logs found"}

    argon_nL = iso_nL = logged_h = 0.0
    start = end = None
    for f in files:
        p = _cached_integral(f)
        argon_nL += p["argon_nL"]
        iso_nL += p["iso_nL"]
        logged_h += p["logged_h"]
        if p["first"] is not None and (start is None or p["first"] < start):
            start = p["first"]
        if p["last"] is not None and (end is None or p["last"] > end):
            end = p["last"]

    argon_rate, iso_rate = _recent_rate(files[-1])

    # --- Argon: compressed gas, linear pressure gauge ---
    argon_content0_nL = ARGON_START_BAR * ARGON_BOTTLE_L
    argon_remaining_nL = max(argon_content0_nL - argon_nL, 0.0)
    argon_pressure = argon_remaining_nL / ARGON_BOTTLE_L
    argon = {
        "drawn_L": round(argon_nL, 1),
        "content_start_L": round(argon_content0_nL, 1),
        "remaining_L": round(argon_remaining_nL, 1),
        "frac_remaining": round(argon_remaining_nL / argon_content0_nL, 4),
        "pressure_bar": round(argon_pressure, 1),
        "pressure_start_bar": ARGON_START_BAR,
        "rate_lnh": round(argon_rate, 4),
        "days_to_empty": _days_to_empty(argon_remaining_nL, argon_rate),
    }

    # --- Isobutane: liquefied gas, track liquid remaining ---
    iso_liq0_L = ISO_BOTTLE_L * ISO_START_LIQ_FRAC
    iso_content0_nL = (iso_liq0_L * ISO_LIQ_DENSITY * 1000.0 / ISO_MOLAR_MASS
                       * NORMAL_MOLAR_VOL_L)
    iso_remaining_nL = max(iso_content0_nL - iso_nL, 0.0)
    iso_frac = iso_remaining_nL / iso_content0_nL if iso_content0_nL else 0.0
    iso = {
        "drawn_L": round(iso_nL, 1),
        "content_start_L": round(iso_content0_nL, 1),
        "remaining_L": round(iso_remaining_nL, 1),
        "frac_remaining": round(iso_frac, 4),
        "liquid_start_L": round(iso_liq0_L, 1),
        "liquid_remaining_L": round(iso_liq0_L * iso_frac, 2),
        "vapor_pressure_bar": ISO_VAPOR_PRESSURE if iso_remaining_nL > 0 else 0.0,
        "rate_lnh": round(iso_rate, 4),
        "days_to_empty": _days_to_empty(iso_remaining_nL, iso_rate),
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
        },
    }
