#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on July 10 2026
Created in PyCharm
Created as nTof_x17_DAQ/beam_monitor/beam_intensity_controller.py

@author: Dylan Neff, dylan

Live n_TOF beam-intensity monitor, fed from CERN NXCALS (the database behind
Timber). Mirrors the he3_pressure watcher split: a standalone process (the
beam_watcher tmux session) owns the NXCALS/Spark session, polls the proton
intensity delivered to the n_TOF target, appends every logged pulse to a
per-day CSV and publishes a summary to BEAM_STATE_PATH for the Flask app.

The variable is F16.BCT372.TOF:INTENSITY — the beam-current transformer in the
FTN transfer line (last BCT before the n_TOF target), filtered to cycles whose
destination is TOF. Units are 1e10 protons per pulse (a dedicated pulse is
~800, i.e. 8e12 protons; parasitic pulses ~400). The PS writes one point per
TOF cycle, including ~0 points when a TOF cycle plays with no beam, so a fresh
point with low intensity means "beam off", while no points at all means TOF is
out of the supercycle (or the NXCALS pipeline is down) — the state reports
both timestamps so the GUI can tell the difference.

NXCALS practicalities (why this is its own process + venv):
  * pytimber >=4 drags in a full local Spark session — ~1 GB of JVM + PySpark
    that must NOT live in the DAQ venv. It is installed in NXCALS_PYTHON
    (built from acc-py-repo.cern.ch, which this machine can reach directly).
  * Authentication is the user's Kerberos ticket (same /tmp/krb5cc_1000 the
    EOS backup uses). The watcher renews it with `kinit -R` while it is
    renewable; once past the renewable life (~5 days) a manual
    `kinit dneff@CERN.CH` reseed is needed — the state file exposes the
    expiry so the GUI can warn before that happens.
  * The first query after startup takes ~30-60 s (Spark spin-up); after that
    each poll is ~1-2 s. NXCALS data latency is a few tens of seconds, which
    the beam-off gap threshold must (and does) exceed.
"""

import os
import csv
import json
import re
import signal
import subprocess
import sys
import time
import traceback
from datetime import datetime, timedelta

# Shared file paths for the watcher/Flask split (resolved relative to the repo so
# watcher + Flask agree). The beam_watcher process is the sole owner of the NXCALS
# session; Flask only reads BEAM_STATE_PATH and the CSVs.
_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_DIR = os.path.dirname(_MODULE_DIR)

# common_functions is stdlib-only, so it imports fine under this watcher's SEPARATE
# NXCALS venv (~/venvs/nxcals/bin/python) as well as under the DAQ venv that Flask uses.
sys.path.insert(0, _REPO_DIR)
from common_functions import log_event

# Durable event log for the beam_watcher process. Events only (session up/down,
# Kerberos, query failures) — the per-day CSVs below are intensity DATA.
BEAM_EVENT_LOG = os.path.join(_REPO_DIR, "logs", "beam_watcher.log")


def _log(event, **details):
    log_event(BEAM_EVENT_LOG, event, 'beam_watcher', **details)


# The failure paths here repeat on every poll (12 s) for as long as the outage lasts,
# and a Kerberos outage can run for days. Throttle them so this stays an event log:
# the first line of an episode always gets through, then at most one per interval,
# carrying how many were suppressed.
_LOG_THROTTLE_S = 900
_last_logged = {}      # key -> (last write time, suppressed count)


def _log_throttled(key, event, **details):
    now = time.time()
    last, suppressed = _last_logged.get(key, (0.0, 0))
    if now - last < _LOG_THROTTLE_S:
        _last_logged[key] = (last, suppressed + 1)
        return
    _last_logged[key] = (now, 0)
    if suppressed:
        details['suppressed_since_last'] = suppressed
    _log(event, **details)
# Per-day intensity CSVs live with the other slow-control logs (gas, 3He pressure)
# under ~/beam_july/slow_control/ on the data disk, not in the repo.
BEAM_LOG_DIR = os.path.expanduser("~/beam_july/slow_control/beam_intensity")
BEAM_STATE_PATH = os.path.join(_REPO_DIR, "config", "beam_state.json")

# Interpreter the watcher must run under (Flask's venv does not have pytimber).
NXCALS_PYTHON = os.path.expanduser("~/venvs/nxcals/bin/python")

# NXCALS variable + interpretation.
# FTN.BCT477 is the LAST beam-current transformer before the n_TOF target — the
# ground truth for protons on target. Do NOT use F16.BCT372.TOF (TT2, upstream):
# on 2026-07-10 17:20-18:20 the target got zero beam for an hour while BCT372
# kept logging ~6.9e12 "TOF-destination" pulses that were stopped before the
# target. BCT477 logged ~0 for exactly that hour. (Until the switch at ~18:45
# that day, the CSVs contain BCT372 data.)
BEAM_VARIABLE = "FTN.BCT477:AcquisitionLatest:totalIntensity"
BEAM_UNIT = "1e10 protons"
# Points below this are empty/parasitic-noise cycles, not real beam pulses.
PULSE_THRESHOLD_E10 = 50.0

POLL_S = 12.0            # NXCALS query cadence. The query itself is ~0.3 s, so
                         # this only sets how stale the published state can be;
                         # NXCALS's own ingestion latency (11-23 s measured) is the
                         # real floor, and it also sets how fast beam-BACK-ON shows.
LOOKBACK_S = 600.0       # stats window (pulses / protons in the last 10 min)
RATE_WINDOW_S = 120.0    # SHORT window for the live protons/min figure. Measured
                         # 2026-07-27 on a 3 h beam-on stretch: the window-to-window
                         # scatter of the rate is 7.1 % at 600 s, 8.4 % at 120 s,
                         # 11 % at 60 s and 24 % at 15 s (~19 pulses/min, so a 15 s
                         # window can hold zero pulses). 120 s is therefore 5x faster
                         # than the old 10 min at essentially the same steadiness.
BEAM_OFF_GAP_S = 80.0    # no pulse for this long -> beam considered OFF.
                         # MEASURED 2026-07-27 over 134 h / 117472 pulses (07-22..27):
                         # the pulse-gap distribution has a CLEAN EMPTY BAND between
                         # 38.4 s and 86.4 s. Longest gap with the beam demonstrably
                         # running (70-105 pulses in the 5 min either side) = 38.4 s
                         # (36 such gaps, 0.27/h); shortest genuine stop = 86.4 s
                         # (29 events); NOTHING in 45-75 s. This value is on the
                         # WALL-CLOCK axis, so it also has to cover the delay before
                         # a pulse is visible to us (NXCALS ingestion + poll phase,
                         # measured over 58 polls: 11.3 s min / 16.4 s median /
                         # 23.5 s max): 38.4 + 23.5 = 61.9 s is the worst reading a
                         # HEALTHY beam can produce, so 80 s clears it by 18 s and
                         # still flags every real stop. Do not go below ~65 s.
                         # Validated on a real stop 07-27 13:55: OFF at 86 s, no
                         # false OFF (max reading while correctly ON = 73.6 s).
SPS_POLL_EVERY = 2       # run the (heavier) SPS spill poll every Nth beam poll, so
                         # speeding up the beam loop does not multiply SPS load.
                         # 2 x 12 s + its own ~6-10 s query lands the SPS tab back on
                         # the ~30-40 s cadence it had when the loop ran at 30 s.
KRB_RENEW_S = 4 * 3600.0  # try `kinit -R` this often to keep the ticket alive


class BeamIntensityMonitor:
    """Owns the pytimber/NXCALS session plus the poll/log loop."""

    def __init__(self, poll_s=POLL_S, state_path=BEAM_STATE_PATH, log_dir=BEAM_LOG_DIR):
        self.poll_s = poll_s
        self.state_path = state_path
        self.log_dir = log_dir
        self.db = None
        self.connected = False
        self.last_error = None
        self._stop = False
        # Unix ts of the newest CSV row — dedups across polls AND across watcher
        # restarts (each poll re-queries the whole lookback window, so without
        # seeding from the CSV a restart would re-log up to LOOKBACK_S of rows).
        self._last_logged_ts = self._newest_logged_ts()
        self._last_point = None      # (unix_ts, value) newest point of any size
        self._last_pulse = None      # (unix_ts, value) newest point >= threshold
        self._last_krb_renew = 0.0
        # TEMPORARY (2026-07-23): SPS slow-extraction spill monitor. It has no
        # Spark session of its own — pytimber is a ~1.3 GB JVM and one is
        # already running right here, so it borrows self.db. Every call into it
        # is wrapped: an SPS failure must never disturb n_TOF beam logging.
        # To remove: delete this attribute, the _poll_sps() call in
        # run_blocking(), and the sps_monitor package.
        self._sps = None
        self._sps_tick = 0
        try:
            from sps_monitor.sps_spill_controller import SpsSpillMonitor
            self._sps = SpsSpillMonitor(logger=self.log)
        except Exception as e:
            self.log(f"SPS spill monitor unavailable (n_TOF unaffected): {e}")

    # ---------------- NXCALS session ----------------

    def _connect(self):
        """Create the pytimber session (slow: local Spark spin-up)."""
        try:
            import pytimber
            self.log("starting NXCALS session (Spark spin-up, ~1 min)...")
            # The NXCALS bundle's spark-defaults.conf pins spark.driver.port to 5001
            # — the Flask GUI's port. If this watcher starts while Flask is down,
            # Spark steals 5001 and Flask can never come back. Pin the driver away
            # from it (and drop the unused Spark web UI).
            self.db = pytimber.LoggingDB(source="nxcals", sparkprops={
                "spark.driver.port": "5011",
                "spark.ui.enabled": "false",
            })
            self.connected = True
            self.last_error = None
            self.log("NXCALS session up")
            _log('NXCALS_UP')
        except Exception as e:
            self.db = None
            self.connected = False
            self.last_error = f"NXCALS connect failed: {e}"
            self.log(self.last_error)
            _log_throttled('connect_failed', 'NXCALS_CONNECT_FAILED',
                           error=str(e)[:300])
        return self.connected

    # ---------------- Kerberos upkeep ----------------

    @staticmethod
    def _krb_expiry():
        """Expiry datetime of the krbtgt in the default cache, or None."""
        try:
            out = subprocess.run(["klist"], capture_output=True, text=True,
                                 timeout=10).stdout
        except Exception:
            return None
        for line in out.splitlines():
            if "krbtgt/CERN.CH" in line:
                m = re.match(r"\s*\S+\s+\S+\s+(\d+/\d+/\d+)\s+(\d+:\d+:\d+)", line)
                if m:
                    try:
                        return datetime.strptime(f"{m.group(1)} {m.group(2)}",
                                                 "%m/%d/%Y %H:%M:%S")
                    except ValueError:
                        pass
        return None

    def _renew_kerberos(self, force=False):
        """`kinit -R` at most every KRB_RENEW_S (or now, if force). Renewal only
        works within the ticket's renewable life; past that a manual kinit reseed
        is required (same story as the EOS backup)."""
        now = time.time()
        if not force and now - self._last_krb_renew < KRB_RENEW_S:
            return
        self._last_krb_renew = now
        try:
            r = subprocess.run(["kinit", "-R"], capture_output=True, text=True, timeout=15)
            if r.returncode == 0:
                self.log("kerberos ticket renewed (kinit -R)")
            else:
                self.log(f"kinit -R failed: {r.stderr.strip()} — "
                         f"manual `kinit dneff@CERN.CH` needed before expiry")
                _log_throttled('krb_renew', 'KERBEROS_RENEW_FAILED',
                               stderr=r.stderr.strip()[:200],
                               note='manual kinit needed before expiry')
        except Exception as e:
            self.log(f"kinit -R error: {e}")
            _log_throttled('krb_renew', 'KERBEROS_RENEW_ERROR', error=repr(e))

    # ---------------- query + state ----------------

    def _poll_once(self):
        """One NXCALS query: fetch new points, log them, rebuild the state dict."""
        t2 = datetime.now()
        t1 = t2 - timedelta(seconds=LOOKBACK_S)
        q0 = time.time()
        res = self.db.get(BEAM_VARIABLE, t1, t2)
        query_s = time.time() - q0

        ts, vals = res.get(BEAM_VARIABLE, ([], []))
        # Cast numpy scalars to native floats here, so every downstream value
        # (state JSON, CSV) is json-serializable without further care.
        points = sorted((float(t), float(v)) for t, v in zip(ts, vals))
        for t, v in points:
            if t > (self._last_point[0] if self._last_point else 0):
                self._last_point = (t, v)
            if v >= PULSE_THRESHOLD_E10 and t > (self._last_pulse[0] if self._last_pulse else 0):
                self._last_pulse = (t, v)
        self._log_rows([(t, v) for t, v in points if t > self._last_logged_ts])

        pulses = [(t, v) for t, v in points if v >= PULSE_THRESHOLD_E10]
        now = time.time()
        # Fast delivery rate: protons over the SHORT trailing window, expressed
        # per minute. Summed over ALL cycles in the window (empty ones count as
        # zero) so it is duty-cycle aware and collapses within RATE_WINDOW_S of a
        # beam stop, unlike the 10-min figures kept below for continuity.
        rate_cut = now - RATE_WINDOW_S
        rate_pts = [(t, v) for t, v in points if t >= rate_cut]
        rate_pulses = [(t, v) for t, v in rate_pts if v >= PULSE_THRESHOLD_E10]
        protons_per_min = (sum(v for _, v in rate_pts) / (RATE_WINDOW_S / 60.0)
                           if rate_pts else None)
        since_pulse = now - self._last_pulse[0] if self._last_pulse else None
        since_point = now - self._last_point[0] if self._last_point else None
        # The gap as the DATA sees it: last logged cycle minus last real pulse, with
        # no wall-clock term, so it carries none of the ingestion delay. Diagnostic
        # only — beam_on stays on the wall-clock gap, because during a genuine stop
        # the point stream can also dry up (TOF out of the supercycle), which would
        # freeze this figure. Published so BEAM_OFF_GAP_S can be re-derived from the
        # live state instead of re-measured from the CSVs.
        observed_gap = (max(0.0, self._last_point[0] - self._last_pulse[0])
                        if (self._last_point and self._last_pulse) else None)
        krb_exp = self._krb_expiry()

        state = {
            "connected": True,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "variable": BEAM_VARIABLE,
            "unit": BEAM_UNIT,
            "beam_on": since_pulse is not None and since_pulse <= BEAM_OFF_GAP_S,
            "last_pulse_time": (datetime.fromtimestamp(self._last_pulse[0])
                                .isoformat(timespec="seconds") if self._last_pulse else None),
            "last_pulse_e10": round(self._last_pulse[1], 2) if self._last_pulse else None,
            "seconds_since_pulse": round(since_pulse, 1) if since_pulse is not None else None,
            "seconds_since_point": round(since_point, 1) if since_point is not None else None,
            "observed_gap_s": round(observed_gap, 1) if observed_gap is not None else None,
            "pulses_10min": len(pulses),
            "protons_10min_e10": round(sum(v for _, v in pulses), 1),
            "avg_pulse_e10": round(sum(v for _, v in pulses) / len(pulses), 1) if pulses else None,
            # Short-window live rate (see RATE_WINDOW_S). The *_10min fields above
            # stay as they are: scan scripts and the public stats page key off them.
            "rate_window_s": RATE_WINDOW_S,
            "rate_window_pulses": len(rate_pulses),
            "protons_per_min_e10": round(protons_per_min, 1) if protons_per_min is not None else None,
            "pulse_threshold_e10": PULSE_THRESHOLD_E10,
            "beam_off_gap_s": BEAM_OFF_GAP_S,
            "poll_s": self.poll_s,
            "query_s": round(query_s, 2),
            "krb_valid_until": krb_exp.isoformat(timespec="seconds") if krb_exp else None,
            "csv_path": self._csv_path(),
            "last_error": None,
        }
        return state

    # ---------------- CSV logging ----------------

    def _csv_path(self, day=None):
        day = day or datetime.now().strftime("%Y-%m-%d")
        return os.path.join(self.log_dir, f"beam_intensity_{day}.csv")

    def _newest_logged_ts(self):
        """Largest unix_ts already in the newest per-day CSV (0.0 if none)."""
        try:
            files = sorted(f for f in os.listdir(self.log_dir)
                           if f.startswith("beam_intensity_") and f.endswith(".csv"))
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

    _CSV_FIELDS = ["timestamp", "unix_ts", "intensity_e10"]

    def _log_rows(self, points):
        """Append new (unix_ts, value) points — every TOF cycle, zeros included, so
        the CSV is the same record Timber would give you. Rows go to the CSV of the
        day they occurred (matters just after midnight)."""
        if not points:
            return
        try:
            os.makedirs(self.log_dir, exist_ok=True)
            by_day = {}
            for t, v in points:
                dt = datetime.fromtimestamp(t)
                by_day.setdefault(dt.strftime("%Y-%m-%d"), []).append((dt, t, v))
            for day, rows in by_day.items():
                path = self._csv_path(day)
                new_file = not os.path.exists(path)
                with open(path, "a", newline="") as f:
                    w = csv.DictWriter(f, fieldnames=self._CSV_FIELDS)
                    if new_file:
                        w.writeheader()
                    for dt, t, v in rows:
                        w.writerow({"timestamp": dt.isoformat(timespec="milliseconds"),
                                    "unix_ts": round(t, 3),
                                    "intensity_e10": round(float(v), 3)})
            self._last_logged_ts = max(t for t, _ in points)
        except Exception as e:
            self.log(f"CSV log failed: {e}")

    # ---------------- watcher IPC (state file) ----------------

    def log(self, msg):
        """Timestamped, prefixed line for the beam_watcher tmux pane."""
        print(f"{datetime.now().strftime('%H:%M:%S')} [beam_watcher] {msg}", flush=True)

    def _write_state(self, state):
        """Atomically publish the current state for the Flask app to read."""
        try:
            os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
            tmp = self.state_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(state, f)
            os.replace(tmp, self.state_path)   # atomic: readers never see a partial file
        except Exception as e:
            self.log(f"state write failed: {e}")

    def _write_error_state(self):
        self._write_state({
            "connected": False,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "variable": BEAM_VARIABLE,
            "unit": BEAM_UNIT,
            "beam_on": None,
            "last_error": self.last_error,
        })

    # ---------------- SPS spill monitor (TEMPORARY, borrows self.db) ----------

    def _poll_sps(self):
        """Run one SPS spill poll on our Spark session. Swallows everything: this
        is a bolted-on test monitor and must not be able to break n_TOF logging
        or the reconnect logic. Delete along with self._sps to remove."""
        if self._sps is None:
            return
        # Decimated: the SPS pass pulls intra-cycle arrays (~1800 floats/cycle) and
        # is far heavier than the n_TOF query, so it keeps its own ~30 s cadence
        # (about one SPS supercycle) no matter how fast the beam loop runs.
        self._sps_tick += 1
        if self._sps_tick % SPS_POLL_EVERY:
            return
        try:
            self._sps.poll(self.db)
        except Exception as e:
            self.log(f"SPS poll failed (n_TOF unaffected): {e}")
            try:
                self._sps.write_error(e)
            except Exception:
                pass

    # ---------------- poll loop ----------------

    def run_blocking(self):
        """Poll/log loop in the current thread until SIGINT/SIGTERM. Used by
        beam_watcher.py."""
        signal.signal(signal.SIGINT, lambda *a: setattr(self, "_stop", True))
        signal.signal(signal.SIGTERM, lambda *a: setattr(self, "_stop", True))
        self.log(f"beam watcher starting ({BEAM_VARIABLE}, poll {self.poll_s}s, "
                 f"beam-off gap {BEAM_OFF_GAP_S}s)")
        _log('START', variable=BEAM_VARIABLE, poll=f'{self.poll_s}s',
             beam_off_gap=f'{BEAM_OFF_GAP_S}s', csv_dir=self.log_dir)
        while not self._stop:
            if not self.connected and not self._connect():
                self._write_error_state()
                self._sleep(60.0)
                continue
            try:
                self._renew_kerberos()
                state = self._poll_once()
                self._write_state(state)
                # n_TOF state is published FIRST, so the SPS add-on can only ever
                # delay the next poll, never withhold a beam update.
                self._poll_sps()
            except Exception as e:
                # Query died (kerberos expiry, network blip, Spark session loss).
                # Renew the ticket now and rebuild the session on the next pass.
                self.last_error = f"query failed: {e}"
                self.log(self.last_error)
                _log_throttled('query_failed', 'QUERY_FAILED', error=str(e)[:300],
                               action='renewing ticket, rebuilding session next pass')
                self._write_error_state()
                self._renew_kerberos(force=True)
                self.connected = False
                self.db = None
            self._sleep(self.poll_s)
        self.log("beam watcher stopped")
        _log('STOP')

    def _sleep(self, seconds):
        end = time.time() + seconds
        while not self._stop and time.time() < end:
            time.sleep(0.5)
