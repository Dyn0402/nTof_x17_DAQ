#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Disk-space fill-rate forecasting for the nTof DAQ box.

"How long until this disk is full?" for the two acquisition disks — the SSD
staging filesystem (/) and the data HDD (/mnt/data). It answers from the record
the system_stats_watcher already keeps: one `ssd_percent` / `hdd_percent` sample
every ~0.5 s in per-day CSVs under system_stats/, so nothing new has to be logged.

Method — GROSS INFLOW, not a raw slope. Both disks are periodically CLEARED (the
SSD by space_watcher's auto-remove, the HDD by backup cleanup / manual pruning),
so `used` is a sawtooth: a steady climb at the data rate, chopped by sudden drops.
A plain least-squares slope over any window that straddles a drop is meaningless
(we measured it swing from +35 to -635 GB/hr on the same data). So instead, over a
trailing window (default 6 h) we bin `used` into 10-minute steps, take the
per-bin change, ZERO OUT the drops (a cleanup is not data leaving the experiment),
and sum the rises. That gross accumulation over the window's elapsed time is the
data-inflow rate — and it comes out stable (~15-33 GB/hr here) across every window
length and bin size, because it no longer cares where the cleanups fell.

Time-to-full is then free / inflow, i.e. "how long until this disk fills IF it
stops being cleared." That framing is deliberate and differs per disk:

  * HDD — NOT auto-managed. It is only ever pruned by hand / by backup cleanup, so
    "full in ~2.5 days at the current rate unless pruned" is a real planning number.

  * SSD — auto-managed. space_watcher holds free space near its floor, so it will
    not actually reach full in normal operation; the ETA is the SAFETY figure "if
    auto-clear stops" (backups stall, watcher dies). `managed` is set so a caller
    can label it that way rather than crying wolf.

`duty` (fraction of bins that were rising) reports how busy the window was, so a
caller can tell a genuine steady rate from one averaged over a mostly-idle stretch.
An inflow within `stable_gb_per_hr` of zero is reported as "idle", never an ETA.
"""

import os
import glob
import shutil
from datetime import datetime, timedelta

import pandas as pd

from system_monitor.system_stats_controller import SYSTEM_STATS_LOG_DIR, DISK_MOUNTS

GB = 1024 ** 3

# Human labels + which disks the auto-remover manages (drives the "auto-managed"
# annotation and the interpretation of a positive SSD slope as a backup problem).
DISK_LABELS  = {"ssd": "SSD (/)", "hdd": "HDD (/mnt/data)"}
DISK_MANAGED = {"ssd": True, "hdd": False}


def _now(mount):
    """Live total/used/free for a mount, or None if it is briefly unavailable."""
    try:
        u = shutil.disk_usage(mount)
        return u.total, u.used, u.free
    except Exception:
        return None


def _gross_inflow(percent_series, total_bytes, bin_min):
    """Data-inflow rate (bytes/hour) from a used-percent time series, robust to the
    periodic cleanup drops that make a raw slope useless here.

    Bin to `bin_min` minutes (coarse enough to clear the HDD's 0.1%-quantized log),
    take each bin's change in used bytes, drop the negatives (cleanups aren't data
    leaving), and divide the summed rises by the window's elapsed time.

    Returns (rate_bytes_per_hr, duty, n_bins) where duty is the fraction of bins
    that rose — the window's "busyness" — or (None, None, n) if too short to fit."""
    s = (percent_series.set_index("timestamp").iloc[:, 0]
         .resample(f"{int(bin_min)}min").mean().dropna())
    n = int(len(s))
    if n < 3:
        return None, None, n
    used = s / 100.0 * total_bytes
    delta = used.diff().dropna()
    if delta.empty:
        return None, None, n
    elapsed_h = (s.index[-1] - s.index[0]).total_seconds() / 3600.0
    if elapsed_h <= 0:
        return None, None, n
    rises = delta.clip(lower=0)
    rate = float(rises.sum() / elapsed_h)
    duty = float((delta > 0).mean())
    return rate, duty, n


def _load_window(hours):
    """The last `hours` of (timestamp, ssd_percent, hdd_percent), read from the one
    or two day-files the window can touch. Empty frame if there is no usable data."""
    files = sorted(glob.glob(os.path.join(SYSTEM_STATS_LOG_DIR, "system_stats_*.csv")))
    if not files:
        return pd.DataFrame()
    cols = ["timestamp", "ssd_percent", "hdd_percent"]
    frames = []
    for f in files[-2:]:                       # two files so a window spanning midnight works
        try:
            frames.append(pd.read_csv(f, usecols=lambda c: c in cols))
        except Exception:
            continue
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"])
    if hours and hours > 0:
        df = df[df["timestamp"] >= datetime.now() - timedelta(hours=hours)]
    return df


def human_duration(hours):
    """A compact ETA: minutes under 1 h, hours under 2 days, else days."""
    if hours is None:
        return None
    if hours < 1:
        return f"{hours * 60:.0f} min"
    if hours < 48:
        return f"{hours:.1f} h"
    return f"{hours / 24:.1f} d"


# How to phrase the "full in X" figure, since neither disk fills without its clearing
# being absent. Keyed by `managed`.
_ETA_BASIS = {True:  "if not cleared",
              False: "if not pruned"}


def _forecast_disk(key, df, hours, bin_min, stable_gb_per_hr):
    """Forecast one disk from the window `df`. Always returns the live usage; the rate
    fields are None when there is not enough history to fit (a fresh log, say)."""
    mount = DISK_MOUNTS[key]
    label = DISK_LABELS.get(key, key.upper())
    managed = DISK_MANAGED.get(key, False)

    out = {"disk": key, "label": label, "mount": mount, "managed": managed,
           "total": None, "used": None, "free": None, "percent": None,
           "inflow_gb_per_hr": None, "duty": None,
           "trend": "unknown", "time_to_full_h": None, "eta_full": None,
           "eta_basis": _ETA_BASIS[managed],
           "window_hours": hours, "n_bins": 0}

    live = _now(mount)
    if live:
        total, used, free = live
        out.update(total=total, used=used, free=free,
                   percent=round(used / total * 100, 1) if total else None)
    else:
        total = free = None

    col = f"{key}_percent"
    if df.empty or col not in df.columns or total is None:
        return out
    s = df[["timestamp", col]].copy()
    s[col] = pd.to_numeric(s[col], errors="coerce")
    s = s.dropna()
    if len(s) < 3:
        return out

    rate_bytes_per_hr, duty, n_bins = _gross_inflow(s, total, bin_min)
    out["n_bins"] = n_bins
    if rate_bytes_per_hr is None:
        return out
    out["inflow_gb_per_hr"] = round(rate_bytes_per_hr / GB, 1)
    out["duty"] = round(duty, 2) if duty is not None else None

    if rate_bytes_per_hr <= stable_gb_per_hr * GB:
        # No meaningful accumulation over the window — beam off / idle / fully held.
        out["trend"] = "idle"
        return out

    out["trend"] = "filling"
    if free is not None and rate_bytes_per_hr > 0:
        ttf_h = free / rate_bytes_per_hr
        out["time_to_full_h"] = round(ttf_h, 2)
        out["eta_full"] = human_duration(ttf_h)
    return out


def forecast(hours=6.0, bin_min=10, stable_gb_per_hr=0.3):
    """Time-to-full forecast for the SSD (/) and HDD (/mnt/data).

    Args:
      hours             trailing window to measure the inflow rate over (default 6 h).
      bin_min           bin width for the gross-inflow estimate (default 10 min; must
                        be coarse enough to clear the HDD log's 0.1% quantization).
      stable_gb_per_hr  inflow below this reads as "idle", not an ETA, so sampling
                        jitter or a beam-off window never manufactures one (default 0.3).

    Returns a dict:
      {success, window_hours, bin_min, generated,
       disks: {ssd: {...}, hdd: {...}}}
    each disk carrying live total/used/free/percent, the gross inflow_gb_per_hr and
    its duty (fraction of the window spent filling), a trend in {filling, idle,
    unknown}, and, when filling, time_to_full_h + a human eta_full with the eta_basis
    that qualifies it ("if not cleared" for the managed SSD, "if not pruned" for the
    HDD).
    """
    try:
        df = _load_window(hours)
    except Exception as e:
        return {"success": False, "message": f"could not read system_stats history: {e}"}

    disks = {key: _forecast_disk(key, df, hours, bin_min, stable_gb_per_hr)
             for key in ("ssd", "hdd")}
    return {"success": True,
            "window_hours": hours,
            "bin_min": bin_min,
            "generated": datetime.now().isoformat(timespec="seconds"),
            "disks": disks}


if __name__ == "__main__":
    import json
    import argparse
    ap = argparse.ArgumentParser(description="Disk-space time-to-full forecast")
    ap.add_argument("--hours", type=float, default=6.0, help="trailing window (h)")
    ap.add_argument("--bin-min", type=int, default=10, help="inflow bin width (min)")
    ap.add_argument("--stable-gb-per-hr", type=float, default=0.3,
                    help="inflow below this reads as idle, not an ETA")
    args = ap.parse_args()
    print(json.dumps(forecast(args.hours, args.bin_min, args.stable_gb_per_hr), indent=2))
