#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on July 23 2026
Created in PyCharm
Created as nTof_x17_DAQ/sps_monitor/sps_spill_controller.py

@author: Dylan Neff, dylan

TEST/TEMPORARY — SPS slow-extraction spill monitor (companion to the n_TOF
beam monitor). Built 2026-07-23 to check whether the SPS "pause / long spill /
pause" structure is visible in NXCALS the same way the n_TOF pulse train is.
Slated for removal once that question is answered.

Unlike n_TOF (one point per PS cycle = one proton pulse), the SPS delivers a
SLOW EXTRACTION: a multi-second spill in which the stored beam is bled out of
the ring continuously. So there is no "pulse intensity" to plot — the useful
signal is the intra-cycle ring-intensity ramp, whose NEGATIVE DERIVATIVE is the
instantaneous extraction rate, i.e. the flux hitting the target (and hence the
muon rate) as a function of time.

Data source (all measured 2026-07-23, see the header notes in each constant):
  * SPS.BCTDC24.51454:Acquisition:{measStamp,totalIntensity} — per-cycle ARRAYS
    giving ring intensity vs time-in-cycle. measStamp is ms from cycle start,
    5 ms/sample; totalIntensity is in 1e10 protons (unitExponent = 10, the same
    unit the n_TOF monitor uses). This is the device SPSQC itself quotes in
    SPSQC:BCT_NAME, so it is the canonical extraction BCT.
  * SPSQC:* — one scalar per SPS cycle: destination, extracted intensity,
    effective spill length, spill duty factor, extraction/beam-out times.

This module does NOT own a Spark session. pytimber/Spark is a ~1.3 GB JVM and
the machine already runs exactly one, inside the beam_watcher process, so this
monitor BORROWS that handle (see BeamIntensityMonitor.run_blocking). Everything
here is wrapped so that an SPS failure can never disturb n_TOF beam logging.
"""

import os
import csv
import json
from datetime import datetime, timedelta

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_DIR = os.path.dirname(_MODULE_DIR)

# Per-day CSVs live with the other slow-control logs, like the n_TOF beam CSVs.
SPS_LOG_DIR = os.path.expanduser("~/beam_july/slow_control/sps_spill")
SPS_STATE_PATH = os.path.join(_REPO_DIR, "config", "sps_state.json")

# --- NXCALS variables -------------------------------------------------------
# Intra-cycle ring intensity vs time-in-cycle. Both are per-cycle arrays and MUST
# be read as a pair: measStamp is the x-axis (ms since cycle start) for the
# totalIntensity y-axis. Do NOT assume the array spans the whole cycle — on the
# 2026-07-23 FTARGET cycle the acquisition is 1815 samples = 9070 ms of a
# 10800 ms cycle (it stops shortly after beam-out at 9040 ms).
SPS_STAMP_VAR = "SPS.BCTDC24.51454:Acquisition:measStamp"
SPS_INT_VAR = "SPS.BCTDC24.51454:Acquisition:totalIntensity"

# Per-cycle scalars (one point per SPS cycle).
SPSQC_VARS = {
    "destination": "SPSQC:DESTINATION",
    "extracted_e10": "SPSQC:EXTRACTED_INTENSITY",
    "spill_len_ms": "SPSQC:EFF_SPILL_LENGHT",       # sic — misspelled in NXCALS
    "duty_factor": "SPSQC:SPILL_DUTY_FACTOR",
    "extraction_time_ms": "SPSQC:EXTRACTION_TIME",
    "beam_out_time_ms": "SPSQC:BEAM_OUT_TIME",
    "cycle_len_ms": "SPSQC:DURATION_IN_MS",
}

SPS_UNIT = "1e10 protons"
# Destination of the slow-extracted fixed-target beam (the North Area). Other
# destinations seen in the same supercycle (e.g. SPS_DUMP) carry no spill.
EXTRACTED_DEST = "FTARGET"
# SPSQC:EXTRACTED_INTENSITY is in PROTONS (raw, ~1.3e13), not 1e10 units like
# the BCT arrays — divide to put both on the n_TOF monitor's 1e10 scale.
EXTRACTED_SCALE_E10 = 1e-10
# Below this a "FTARGET" cycle carried no real beam.
SPILL_THRESHOLD_E10 = 50.0

POLL_S = 30.0             # matches the beam watcher's cadence (it drives us)
SCALAR_LOOKBACK_S = 900.0  # window for the per-cycle scalars / CSV
PROFILE_LOOKBACK_S = 330.0  # window for the (heavy) intra-cycle arrays
SPILL_OFF_GAP_S = 120.0    # no extracted cycle for this long -> SPS spill OFF
# Downsample the stitched timeline to this step. 50 ms is >> fine enough to
# resolve a ~4.5 s spill and keeps the published JSON around 100 kB.
TIMELINE_STEP_MS = 50.0
# Downsample the single-cycle profile to at most this many points.
PROFILE_MAX_POINTS = 500


def _to_list(a):
    """numpy array / scalar -> plain python list of floats (json-safe)."""
    try:
        return [float(x) for x in a]
    except TypeError:
        return [float(a)]


def _nearest(times, values, t, tol=1.0):
    """Value of a per-cycle scalar series nearest to time t (None past tol).

    The tolerance MUST stay well under the shortest cycle (3.6 s here): several
    SPSQC variables (EFF_SPILL_LENGHT, SPILL_DUTY_FACTOR) are published only for
    extracting cycles, and a loose tolerance silently attributes the neighbouring
    FTARGET cycle's spill length to the SPS_DUMP cycle next to it.
    """
    best, best_d = None, tol
    for tt, vv in zip(times, values):
        d = abs(tt - t)
        if d < best_d:
            best, best_d = vv, d
    return best


def _derive_rate(stamp_ms, intensity_e10, t_start_ms=None, t_end_ms=None):
    """Extraction rate (1e10 protons/s) vs time from the ring-intensity ramp.

    The ring loses beam only through extraction (plus small losses), so
    -dI/dt IS the spill. Uses a centred difference over a +/-`half` window to
    beat down BCT sample noise; the window is in samples, so it scales with
    whatever sampling the acquisition used.

    `t_start_ms`/`t_end_ms` are SPSQC's EXTRACTION_TIME and BEAM_OUT_TIME, and
    the rate is forced to zero outside them. That is not cosmetic: at beam-out
    the residual stored beam (~7% of the cycle here) is DUMPED INTERNALLY in a
    few ms, which is a genuine -dI/dt spike ~5x the spill plateau but is NOT
    beam on the target. Left in, it dominates the peak and the colour scale
    while representing no flux at all downstream.
    """
    n = min(len(stamp_ms), len(intensity_e10))
    if n < 8:
        return []
    half = 4
    rate = [0.0] * n
    for i in range(n):
        ms = stamp_ms[i]
        if t_start_ms is not None and t_start_ms >= 0 and ms < t_start_ms:
            continue
        if t_end_ms is not None and t_end_ms >= 0 and ms > t_end_ms:
            continue
        a, b = max(0, i - half), min(n - 1, i + half)
        dt_s = (stamp_ms[b] - stamp_ms[a]) / 1000.0
        if dt_s <= 0:
            continue
        # negative slope -> positive extraction rate; clamp the noise floor so
        # the flat top and the inter-spill pause read as a clean zero.
        r = -(intensity_e10[b] - intensity_e10[a]) / dt_s
        rate[i] = r if r > 0 else 0.0
    return rate


class SpsSpillMonitor:
    """Polls the SPS spill variables using a pytimber handle owned by someone
    else (the beam watcher). Publishes SPS_STATE_PATH + a per-cycle CSV."""

    def __init__(self, state_path=SPS_STATE_PATH, log_dir=SPS_LOG_DIR, logger=None):
        self.state_path = state_path
        self.log_dir = log_dir
        self._log = logger or (lambda m: None)
        self._last_logged_ts = self._newest_logged_ts()

    def log(self, msg):
        self._log(f"[sps] {msg}")

    # ---------------- poll ----------------

    def poll(self, db):
        """One NXCALS pass. Returns the state dict it published."""
        now = datetime.now()

        # 1) per-cycle scalars over the long window (cheap; drives the CSV)
        scal_res = db.get(list(SPSQC_VARS.values()),
                          now - timedelta(seconds=SCALAR_LOOKBACK_S), now)
        scal = {}
        for key, var in SPSQC_VARS.items():
            ts, vals = scal_res.get(var, ([], []))
            scal[key] = ([float(t) for t in ts], list(vals))

        dest_t, dest_v = scal["destination"]
        cycles = []
        for t, dest in zip(dest_t, dest_v):
            row = {"unix_ts": t, "destination": str(dest)}
            for key in SPSQC_VARS:
                if key == "destination":
                    continue
                v = _nearest(*scal[key], t)
                row[key] = float(v) if v is not None else None
            if row.get("extracted_e10") is not None:
                row["extracted_e10"] *= EXTRACTED_SCALE_E10
            cycles.append(row)
        cycles.sort(key=lambda r: r["unix_ts"])

        self._log_rows([c for c in cycles if c["unix_ts"] > self._last_logged_ts])

        # 2) intra-cycle arrays over the short window (heavy: ~1800 floats/cycle)
        prof_res = db.get([SPS_STAMP_VAR, SPS_INT_VAR],
                          now - timedelta(seconds=PROFILE_LOOKBACK_S), now)
        st_t, st_v = prof_res.get(SPS_STAMP_VAR, ([], []))
        in_t, in_v = prof_res.get(SPS_INT_VAR, ([], []))
        stamps = {float(t): _to_list(v) for t, v in zip(st_t, st_v)}

        profiles = []   # (cycle_start_unix, dest, stamp_ms[], intensity[], rate[])
        for t, iv in zip(in_t, in_v):
            t = float(t)
            stamp = stamps.get(t)
            if stamp is None:      # pair them by identical cycle stamp
                continue
            inten = _to_list(iv)
            n = min(len(stamp), len(inten))
            if n < 8:
                continue
            stamp, inten = stamp[:n], inten[:n]
            dest = _nearest(dest_t, dest_v, t)
            # Gate the rate to the machine's own declared spill window so the
            # end-of-cycle internal dump is not mistaken for extracted flux.
            t_start = _nearest(*scal["extraction_time_ms"], t)
            t_end = _nearest(*scal["beam_out_time_ms"], t)
            # The array is logged when the acquisition completes, so the cycle
            # started ~(last measStamp) earlier. Constant offset -> only shifts
            # the timeline, never distorts the spill shape.
            start = t - stamp[-1] / 1000.0
            profiles.append((start, str(dest) if dest else None, stamp, inten,
                             _derive_rate(stamp, inten, t_start, t_end)))
        profiles.sort(key=lambda p: p[0])

        # 3) stitch every cycle's extraction rate onto absolute wall-clock time.
        #    THIS is the pause / spill / pause trace.
        tl_t, tl_r = [], []
        for start, _dest, stamp, _inten, rate in profiles:
            if not rate:
                continue
            last_kept = None
            for ms, r in zip(stamp, rate):
                if last_kept is not None and ms - last_kept < TIMELINE_STEP_MS:
                    continue
                last_kept = ms
                tl_t.append(round(start + ms / 1000.0, 3))
                tl_r.append(round(r, 3))

        # 4) newest cycle that actually extracted -> the featured spill profile
        featured = None
        for start, dest, stamp, inten, rate in reversed(profiles):
            if dest != EXTRACTED_DEST or not rate or max(rate) <= 0:
                continue
            step = max(1, len(stamp) // PROFILE_MAX_POINTS)
            featured = {
                "cycle_start": datetime.fromtimestamp(start).isoformat(timespec="milliseconds"),
                "cycle_start_unix": round(start, 3),
                "destination": dest,
                "t_ms": [round(x, 1) for x in stamp[::step]],
                "intensity_e10": [round(x, 2) for x in inten[::step]],
                "rate_e10_per_s": [round(x, 2) for x in rate[::step]],
                "peak_rate_e10_per_s": round(max(rate), 2),
                # Mean over the spilling samples only. This, not the peak, is the
                # number to quote: the peak is a short transient at extraction
                # start and runs ~3x the plateau the detector actually sees.
                "mean_rate_e10_per_s": round(
                    sum(r for r in rate if r > 0) / max(1, sum(1 for r in rate if r > 0)), 2),
            }
            break

        # 5) summary
        extracted = [c for c in cycles
                     if c["destination"] == EXTRACTED_DEST
                     and (c.get("extracted_e10") or 0) >= SPILL_THRESHOLD_E10]
        last = extracted[-1] if extracted else None
        since = (now.timestamp() - last["unix_ts"]) if last else None
        recent = [c for c in cycles if c["unix_ts"] >= now.timestamp() - 600]
        recent_ex = [c for c in extracted if c["unix_ts"] >= now.timestamp() - 600]
        # Supercycle period: median gap between successive extracted cycles.
        gaps = sorted(b["unix_ts"] - a["unix_ts"]
                      for a, b in zip(extracted, extracted[1:]))
        period = round(gaps[len(gaps) // 2], 2) if gaps else None

        state = {
            "connected": True,
            "timestamp": now.isoformat(timespec="seconds"),
            "unit": SPS_UNIT,
            "intensity_var": SPS_INT_VAR,
            "spill_on": since is not None and since <= SPILL_OFF_GAP_S,
            "last_spill_time": (datetime.fromtimestamp(last["unix_ts"])
                                .isoformat(timespec="seconds") if last else None),
            "seconds_since_spill": round(since, 1) if since is not None else None,
            "last_extracted_e10": round(last["extracted_e10"], 1) if last else None,
            "last_spill_len_ms": (round(last["spill_len_ms"], 0)
                                  if last and last["spill_len_ms"] else None),
            "last_duty_factor": (round(last["duty_factor"], 3)
                                 if last and last["duty_factor"] else None),
            "last_cycle_len_ms": (round(last["cycle_len_ms"], 0)
                                  if last and last["cycle_len_ms"] else None),
            "supercycle_period_s": period,
            "spills_10min": len(recent_ex),
            "protons_10min_e10": round(sum(c["extracted_e10"] for c in recent_ex), 1),
            "destinations_10min": sorted({c["destination"] for c in recent}),
            "spill_off_gap_s": SPILL_OFF_GAP_S,
            "timeline": {"t_unix": tl_t, "rate_e10_per_s": tl_r,
                         "span_s": PROFILE_LOOKBACK_S},
            "profile": featured,
            "csv_path": self._csv_path(),
            "last_error": None,
        }
        self._write_state(state)
        return state

    def write_error(self, msg):
        self._write_state({
            "connected": False,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "unit": SPS_UNIT,
            "spill_on": None,
            "last_error": str(msg),
        })

    # ---------------- CSV ----------------

    _CSV_FIELDS = ["timestamp", "unix_ts", "destination", "extracted_e10",
                   "spill_len_ms", "duty_factor", "extraction_time_ms",
                   "beam_out_time_ms", "cycle_len_ms"]

    def _csv_path(self, day=None):
        day = day or datetime.now().strftime("%Y-%m-%d")
        return os.path.join(self.log_dir, f"sps_spill_{day}.csv")

    def _newest_logged_ts(self):
        """Largest unix_ts already logged, so a restart does not re-log the
        lookback window (same trick as the n_TOF beam watcher)."""
        try:
            files = sorted(f for f in os.listdir(self.log_dir)
                           if f.startswith("sps_spill_") and f.endswith(".csv"))
        except OSError:
            return 0.0
        if not files:
            return 0.0
        newest = 0.0
        try:
            with open(os.path.join(self.log_dir, files[-1]), newline="") as f:
                for row in csv.DictReader(f):
                    try:
                        newest = max(newest, float(row["unix_ts"]))
                    except (KeyError, TypeError, ValueError):
                        pass
        except OSError:
            return 0.0
        return newest

    def _log_rows(self, rows):
        """Append new per-cycle rows — every SPS cycle, dump cycles included, so
        the supercycle structure is reconstructable from the CSV alone."""
        if not rows:
            return
        try:
            os.makedirs(self.log_dir, exist_ok=True)
            by_day = {}
            for r in rows:
                dt = datetime.fromtimestamp(r["unix_ts"])
                by_day.setdefault(dt.strftime("%Y-%m-%d"), []).append((dt, r))
            for day, day_rows in by_day.items():
                path = self._csv_path(day)
                new_file = not os.path.exists(path)
                with open(path, "a", newline="") as f:
                    w = csv.DictWriter(f, fieldnames=self._CSV_FIELDS)
                    if new_file:
                        w.writeheader()
                    for dt, r in day_rows:
                        out = {"timestamp": dt.isoformat(timespec="milliseconds"),
                               "unix_ts": round(r["unix_ts"], 3)}
                        for k in self._CSV_FIELDS[2:]:
                            v = r.get(k)
                            out[k] = round(v, 4) if isinstance(v, float) else v
                        w.writerow(out)
            self._last_logged_ts = max(r["unix_ts"] for r in rows)
        except Exception as e:
            self.log(f"CSV log failed: {e}")

    # ---------------- state file ----------------

    def _write_state(self, state):
        try:
            os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
            tmp = self.state_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(state, f)
            os.replace(tmp, self.state_path)   # atomic
        except Exception as e:
            self.log(f"state write failed: {e}")
