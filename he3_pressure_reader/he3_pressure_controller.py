#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on July 07 2026
Created in PyCharm
Created as nTof_x17_DAQ/he3_pressure_reader/he3_pressure_controller.py

@author: Dylan Neff, dylan

Monitoring layer for the 3He target pressure gauge read through a Keithley 2000
multimeter over GPIB (NI GPIB-USB-HS + linux-gpib). Mirrors the gas mixer's
flow_controller split, but read-only: there is nothing to command, so there is no
command file — just a published state file and a per-day CSV.

The Keithley measures the gauge's DC output voltage V; the pressure is a linear
map of that voltage:

    pressure = (V - PRESS_OFFSET_V) * PRESS_SLOPE          [bar]

A single background thread owns all GPIB access: it opens the instrument, polls the
voltage at a fixed interval, caches the converted reading (so the web UI reads are
instant and never collide on the bus), publishes it to HE3_PRESSURE_STATE_PATH, and
appends the pressure to a per-day CSV.

Because a GPIB instrument has one owner, the Flask app must NOT open the bus itself
— it reads the published state file. See he3_pressure_reader/KEITHLEY2000_GPIB_SETUP.md.
"""

import os
import csv
import glob
import json
import time
import signal
import threading
from datetime import datetime

try:
    import Gpib
    import gpib  # low-level, for GpibError / timeout constants
except ImportError:  # keep import-safe so the Flask app still boots without the binding
    Gpib = None
    gpib = None

# Shared file paths for the watcher/Flask split. The he3_pressure_watcher process is
# the sole owner of the GPIB bus: it writes HE3_PRESSURE_STATE_PATH (readback the Flask
# app serves). HE3_PRESSURE_LOG_DIR holds the per-day CSVs. Paths are resolved relative
# to the repo so watcher + Flask agree.
_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_DIR = os.path.dirname(_MODULE_DIR)
HE3_PRESSURE_LOG_DIR = os.path.join(_MODULE_DIR, "logs")
HE3_PRESSURE_STATE_PATH = os.path.join(_REPO_DIR, "config", "he3_pressure_state.json")
# The Flask app writes the desired sample period here; the watcher reads it each loop
# and applies it within one cycle (read-only monitor, so a plain config file — no
# per-command ack machinery like the gas watcher needs).
HE3_PRESSURE_CONFIG_PATH = os.path.join(_REPO_DIR, "config", "he3_pressure_config.json")

# --- voltage -> pressure conversion:  pressure = (V - offset) * slope, in bar ---
PRESS_OFFSET_V = 1.0
PRESS_SLOPE = 400.0
PRESS_UNIT = "bar"

# --- sample-rate limits ---
# One GPIB :READ? round-trip on a Keithley 2000 (NPLC=1 integration ~17-20 ms plus
# transport) is tens of ms, but this box also runs the DAQ, HV, gas, backup and
# processor watchers, so we deliberately keep the fastest rate modest: pressure
# monitoring gains nothing from sub-second sampling, and slow polling keeps GPIB +
# CPU load negligible. 2 Hz (0.5 s) is the hard ceiling; 1 sample/min the floor.
MIN_SAMPLE_PERIOD_S = 0.5    # -> 2.0 Hz max
MAX_SAMPLE_PERIOD_S = 60.0   # -> ~0.0167 Hz min
DEFAULT_SAMPLE_PERIOD_S = 2.0


def volts_to_pressure(v):
    """Convert the gauge's DC output voltage to pressure in bar."""
    return (v - PRESS_OFFSET_V) * PRESS_SLOPE


def clamp_period(p):
    """Clamp a requested sample period (seconds) into the safe [MIN, MAX] range.
    Non-numeric input falls back to the default period."""
    try:
        p = float(p)
    except (TypeError, ValueError):
        return DEFAULT_SAMPLE_PERIOD_S
    return max(MIN_SAMPLE_PERIOD_S, min(MAX_SAMPLE_PERIOD_S, p))


class He3PressureController:
    """Owns the Keithley 2000 GPIB handle plus the polling/logging thread."""

    def __init__(self, board=0, pad=15, func="VOLT:DC", poll_s=DEFAULT_SAMPLE_PERIOD_S,
                 log_s=None, state_path=HE3_PRESSURE_STATE_PATH,
                 config_path=HE3_PRESSURE_CONFIG_PATH, log_dir=None):
        self.board = board
        # NOTE: the Keithley's GPIB primary address is NOT guaranteed to be this — it
        # was 15 on the first bench machine. Rescan the bus (see the setup guide) and
        # update this default once the real address is known.
        self.pad = pad
        self.func = func
        # Poll and log run at the same cadence (one logged row per sample); log_s tracks
        # poll_s unless explicitly overridden.
        self.poll_s = clamp_period(poll_s)
        self.log_s = clamp_period(poll_s if log_s is None else log_s)
        self.state_path = state_path
        self.config_path = config_path
        self.log_dir = log_dir or HE3_PRESSURE_LOG_DIR

        self._lock = threading.Lock()          # guards ALL GPIB access
        self.inst = None
        self.idn = None
        self.connected = False
        self.last_error = None

        self._state = {}                       # latest reading (served to the UI)
        self._state_lock = threading.Lock()    # guards _state only (not GPIB)
        self._last_log_t = 0.0
        self._stop = threading.Event()
        self._thread = None

    # ---------------- connection ----------------

    def _connect(self):
        """Open + configure the Keithley. Runs inside the poll thread holding the GPIB
        lock. Sets self.connected + self.inst + self.idn."""
        if Gpib is None:
            self.last_error = "linux-gpib python binding (Gpib) not installed"
            return False
        try:
            inst = Gpib.Gpib(self.board, self.pad, 0, gpib.T3s)
            inst.clear()
            inst.write("*IDN?".encode())
            idn = inst.read(256).decode(errors="replace").strip()
            # Configure the measurement function once; return the reading only.
            inst.write(f":CONFigure:{self.func}".encode())
            inst.write(b":FORMat:ELEMents READing")
        except Exception as e:
            self.last_error = (f"could not open/configure Keithley on board {self.board} "
                               f"@ pad {self.pad}: {e}")
            self.inst = None
            self.connected = False
            return False
        self.inst = inst
        self.idn = idn
        self.connected = True
        self.last_error = None
        return True

    # ---------------- reading ----------------

    def _read_pressure(self):
        """Poll one voltage and convert. GPIB lock held by caller. Returns (volts,
        pressure) or raises."""
        self.inst.write(":READ?".encode())
        raw = self.inst.read(256).decode(errors="replace").strip()
        volts = float(raw.split(",")[0])
        return volts, volts_to_pressure(volts)

    def _poll_once(self):
        """Read the gauge under the GPIB lock and build the shared state dict."""
        with self._lock:
            volts, pressure = self._read_pressure()
        state = {
            "connected": True,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "pressure": pressure,      # bar
            "voltage": volts,          # raw Keithley DC volts (diagnostic; not logged)
            "unit": PRESS_UNIT,
            "idn": self.idn,
            "pad": self.pad,
            "poll_s": self.poll_s,
            "log_s": self.log_s,
        }
        with self._state_lock:
            self._state = state
        return state

    def get_state(self):
        """Latest cached reading for the web UI (never touches the bus directly)."""
        with self._state_lock:
            state = dict(self._state)
        if not state:
            state = {"connected": False}
        state.setdefault("connected", self.connected)
        state["last_error"] = self.last_error
        state["unit"] = PRESS_UNIT
        state["csv_path"] = self._csv_path()
        # Current sample rate + the allowed range, so the GUI can render the input.
        state["poll_s"] = self.poll_s
        state["log_s"] = self.log_s
        state["sample_hz"] = round(1.0 / self.poll_s, 4) if self.poll_s else None
        state["sample_limits"] = {
            "min_hz": round(1.0 / MAX_SAMPLE_PERIOD_S, 4),
            "max_hz": round(1.0 / MIN_SAMPLE_PERIOD_S, 4),
            "min_period_s": MIN_SAMPLE_PERIOD_S,
            "max_period_s": MAX_SAMPLE_PERIOD_S,
        }
        return state

    # ---------------- CSV logging ----------------

    def _csv_path(self):
        day = datetime.now().strftime("%Y-%m-%d")
        return os.path.join(self.log_dir, f"he3_pressure_{day}.csv")

    _CSV_FIELDS = ["timestamp", "pressure_bar"]

    def _log_row(self, state):
        try:
            os.makedirs(self.log_dir, exist_ok=True)
            path = self._csv_path()
            new_file = not os.path.exists(path)
            row = {
                "timestamp": state["timestamp"],
                "pressure_bar": round(state["pressure"], 5),
            }
            with open(path, "a", newline="") as f:
                w = csv.DictWriter(f, fieldnames=self._CSV_FIELDS)
                if new_file:
                    w.writeheader()
                w.writerow(row)
        except Exception as e:
            print(f"[he3_pressure_controller] CSV log failed: {e}")

    # ---------------- watcher IPC (state file) ----------------

    def log(self, msg):
        """Timestamped, prefixed line for the he3_pressure_watcher tmux pane (and logs)."""
        print(f"{datetime.now().strftime('%H:%M:%S')} [he3_pressure_watcher] {msg}", flush=True)

    def _write_state(self, state):
        """Atomically publish the current state for the Flask app to read."""
        try:
            os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
            tmp = self.state_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(state, f)
            os.replace(tmp, self.state_path)   # atomic: readers never see a partial file
        except Exception as e:
            print(f"[he3_pressure_controller] state write failed: {e}")

    def _apply_config(self):
        """Pick up a sample-period change the Flask app wrote to the config file and
        apply it (clamped) to the poll/log cadence. Called once per loop iteration, so a
        GUI change takes effect within one cycle. Missing/garbage file -> keep current."""
        try:
            with open(self.config_path) as f:
                cfg = json.load(f)
        except (FileNotFoundError, ValueError, OSError):
            return
        p = cfg.get("poll_s")
        if p is None:
            return
        p = clamp_period(p)
        if p != self.poll_s or p != self.log_s:
            self.log(f"sample period -> {p:.3f}s ({1.0 / p:.3f} Hz)")
            self.poll_s = p
            self.log_s = p

    # ---------------- poll loop ----------------

    def _run(self):
        self._apply_config()   # honor any saved rate before the first sample
        while not self._stop.is_set():
            self._apply_config()
            if not self.connected:
                with self._lock:
                    self._connect()
                if self.connected:
                    self.log(f"connected: {self.idn} (pad {self.pad})")
                else:
                    with self._state_lock:
                        self._state = {"connected": False, "last_error": self.last_error}
                    self._write_state(self.get_state())
                    self._stop.wait(min(5.0, max(self.poll_s, 2.0)))
                    continue
            try:
                state = self._poll_once()
                self._write_state(self.get_state())
                now = time.time()
                # Half-poll tolerance so log_s == poll_s reliably logs every sample
                # (timer jitter would otherwise make an exact >= comparison skip one).
                if now - self._last_log_t >= self.log_s - self.poll_s * 0.5:
                    self._log_row(state)
                    self._last_log_t = now
            except Exception as e:
                # Lost the instrument mid-run: mark disconnected and let the loop reopen.
                self.last_error = f"read failed: {e}"
                self.connected = False
                self.inst = None
            self._stop.wait(self.poll_s)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="pressure-poll", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def run_blocking(self):
        """Run the poll/log loop in the current thread until SIGINT/SIGTERM. Used by
        he3_pressure_watcher.py."""
        signal.signal(signal.SIGINT, lambda *a: self._stop.set())
        signal.signal(signal.SIGTERM, lambda *a: self._stop.set())
        self.log(f"3He pressure watcher starting (pad {self.pad}, poll {self.poll_s}s, "
                 f"log {self.log_s}s, P=(V-{PRESS_OFFSET_V})*{PRESS_SLOPE} {PRESS_UNIT})")
        self._run()
        self.log("3He pressure watcher stopped")


# Module-level singleton so the Flask app could share one controller if ever needed.
_controller = None
_controller_lock = threading.Lock()


def get_controller(**kwargs):
    global _controller
    with _controller_lock:
        if _controller is None:
            _controller = He3PressureController(**kwargs)
            _controller.start()
    return _controller


def _demo():
    """Quick standalone check: connect, print one reading, exit."""
    pc = He3PressureController()
    pc.start()
    time.sleep(3)
    print(json.dumps(pc.get_state(), indent=2, default=str))
    pc.stop()


if __name__ == "__main__":
    _demo()
