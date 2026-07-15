#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on July 07 2026
Created in PyCharm
Created as nTof_x17_DAQ/gas_mixer_control/flow_controller.py

@author: Dylan Neff, dylan

Control + monitoring layer for the two Bronkhorst mass-flow controllers used to
mix isobutane into argon.

The two controllers are daisy-chained on one FLOW-BUS serial port and identify
themselves via their calibrated "Fluid Name" parameter:

    node with Fluid Name "Ar"      -> argon controller
    node with Fluid Name "C4H10.." -> isobutane controller

so no manual "plug one in" test is needed. Serial numbers are used as a hard
backup mapping in case a controller is ever swapped/re-flashed.

Two independent knobs are exposed:
  * total argon flow rate  (set directly, in ln/h)
  * isobutane percentage of the *total* mixture:  iso = argon * p / (100 - p)

A single background thread owns all serial access: it polls both controllers at
a fixed interval, caches the latest readback (so the web UI reads are instant and
never collide on the bus), and appends the readings to a per-day CSV. Commanded
setpoint writes from the web request threads take the same lock.

All flows are expressed in ln/h (litres-normal per hour). The two devices report
native units differently (argon in ln/h, isobutane in ln/min); each device's
readings are converted to ln/h from its reported "Capacity Unit".
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
    import propar
except ImportError:  # keep import-safe so the Flask app still boots without the lib
    propar = None

# Shared file paths for the watcher/Flask split. The gas_watcher process is the sole
# owner of the serial bus: it writes GAS_STATE_PATH (readback the Flask app serves) and
# applies setpoint commands the Flask app drops in GAS_COMMAND_PATH. GAS_LOG_DIR holds
# the per-day CSVs. Paths are resolved relative to the repo so watcher + Flask agree.
_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_DIR = os.path.dirname(_MODULE_DIR)
# Per-day flow CSVs live with the other slow-control logs (3He pressure, beam) under
# ~/beam_july/slow_control/ on the data disk, not in the repo.
GAS_LOG_DIR = os.path.expanduser("~/beam_july/slow_control/gas_flow")
GAS_STATE_PATH = os.path.join(_REPO_DIR, "config", "gas_state.json")
GAS_COMMAND_PATH = os.path.join(_REPO_DIR, "config", "gas_command.json")

# --- propar parameter numbers (DDE) we read/write ---
DDE_SETPOINT   = 9    # int 0..32000  (0..100% of capacity)
DDE_CONTROL    = 12   # control mode; 0 = digital/RS232 setpoint
DDE_FMEASURE   = 205  # measured flow, float, in the device's capacity unit
DDE_FSETPOINT  = 206  # setpoint, float, in the device's capacity unit
DDE_VALVE_OUT  = 55   # valve output (0..100%)
DDE_TEMPERATURE = 142 # device temperature, deg C
DDE_CAPACITY   = 21   # 100% capacity, float, in the capacity unit
DDE_CAP_UNIT   = 129  # capacity unit string, e.g. "ln/min"
DDE_FLUID_NAME = 25   # calibrated fluid, e.g. "Ar" / "C4H10 #2"
DDE_SERIAL     = 92   # serial number string
DDE_MODEL      = 91   # BHT model number string
DDE_P_INLET    = 178  # inlet pressure the device was CALIBRATED for (bar-a); not live
DDE_P_OUTLET   = 179  # outlet pressure the device was CALIBRATED for (bar-a); not live

SETPOINT_MAX = 32000  # full-scale raw setpoint (== 100% of capacity)
VALVE_RAW_FULL = 2 ** 24  # valve-output register full-scale (raw 16,777,216 == 100% open)

# Optional software cap on commandable argon flow (ln/h). None = full device scale
# (7.591 ln/h). Set to a float to re-cap, e.g. if the argon inlet pressure drops
# below its ~3 bar-a calibration and the controller can't reach full scale.
ARGON_MAX_LNH = 7.2
ARGON_LIMIT_NOTE = "argon flow capped at 7.2 ln/h (configured limit)"

# Roles keyed by serial number: authoritative backup if a controller's Fluid Name
# is ever ambiguous or reflashed. Update here if a unit is replaced.
SERIAL_ROLE = {
    "M14210735A": "argon",
    "M10211716B": "isobutane",
}

# Native capacity unit -> ln/h multiplier.  Device unit strings come back space
# padded (e.g. "ln/min "); we strip + lower-case before lookup.
UNIT_TO_LNH = {
    "ln/h":    1.0,
    "ln/hr":   1.0,
    "ls/h":    1.0,
    "ln/min":  60.0,
    "sln/min": 60.0,
    "ls/min":  60.0,
    "mln/min": 0.06,
    "mls/min": 0.06,
    "ml/min":  0.06,
    "sccm":    0.06,
    "mln/h":   0.001,
}


def to_lnh(value, unit):
    """Convert a flow `value` given in `unit` to ln/h. Unknown unit -> assume it is
    already ln/h (with a printed warning)."""
    key = str(unit).strip().lower()
    mult = UNIT_TO_LNH.get(key)
    if mult is None:
        print(f"[flow_controller] Unknown flow unit {unit!r}; treating as ln/h")
        mult = 1.0
    return value * mult


class _Device:
    """One Bronkhorst controller: its propar handle plus cached identity."""

    def __init__(self, inst, node, serial, fluid, model, cap_native, cap_unit,
                 cal_inlet=None, cal_outlet=None):
        self.inst = inst
        self.node = node
        # Coerce identity strings so a dropped read can never crash the poll loop.
        self.serial = str(serial or "").strip()
        self.fluid = str(fluid or "").strip()
        self.model = str(model or "").strip()
        self.cap_native = cap_native           # 100% capacity in native unit
        self.cap_unit = str(cap_unit).strip()  # e.g. "ln/h"
        self.full_scale_lnh = to_lnh(cap_native, cap_unit) if cap_native is not None else 0.0
        # Static calibration reference pressures (bar-a) the unit was calibrated for;
        # NOT a live measurement (these thermal MFCs have no pressure sensor).
        self.cal_inlet = cal_inlet
        self.cal_outlet = cal_outlet

    def native_to_lnh(self, value):
        return to_lnh(value, self.cap_unit)

    def lnh_to_setpoint(self, flow_lnh):
        """ln/h -> raw 0..32000 setpoint, clamped to the device's range."""
        if self.full_scale_lnh <= 0:
            return 0
        frac = flow_lnh / self.full_scale_lnh
        frac = max(0.0, min(1.0, frac))
        return int(round(frac * SETPOINT_MAX))


class FlowController:
    """Owns the two MFCs, the polling/logging thread, and the mixing math."""

    def __init__(self, port=None, log_dir=None, poll_s=2.0, log_s=2.0,
                 argon_limit_lnh=ARGON_MAX_LNH, argon_limit_note=ARGON_LIMIT_NOTE,
                 state_path=GAS_STATE_PATH, command_path=GAS_COMMAND_PATH):
        self.port = port                      # None -> autodiscover
        self.poll_s = poll_s
        self.log_s = log_s
        # Temporary argon flow cap (ln/h); None = no cap (use device full scale).
        self.argon_limit_lnh = argon_limit_lnh
        self.argon_limit_note = argon_limit_note
        self.log_dir = log_dir or GAS_LOG_DIR
        # Watcher IPC: state published here, setpoint commands read from here.
        self.state_path = state_path
        self.command_path = command_path

        self._lock = threading.Lock()         # guards ALL serial access
        self.argon = None                     # _Device or None
        self.iso = None                       # _Device or None
        self.connected = False
        self.last_error = None

        self._state = {}                      # latest readback (served to the UI)
        self._state_lock = threading.Lock()   # guards _state only (not serial)
        self._last_log_t = 0.0
        self._last_command_id = None          # dedupe applied commands by id
        self._last_command_result = None      # ack recorded back into the state file
        self._stop = threading.Event()
        self._thread = None

    # ---------------- discovery / identification ----------------

    def _find_port(self):
        if self.port:
            return self.port
        ports = sorted(glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*"))
        return ports[0] if ports else None

    @staticmethod
    def _classify(fluid, serial):
        """Return 'argon' / 'isobutane' / None from fluid name, serial as backup."""
        f = str(fluid).strip().lower()
        if f.startswith("ar"):
            return "argon"
        if "c4h10" in f or "butan" in f:
            return "isobutane"
        return SERIAL_ROLE.get(str(serial).strip())

    def _connect(self):
        """(Re)discover the two controllers on the bus. Runs inside the poll thread,
        holding the serial lock. Sets self.connected + self.argon/self.iso."""
        if propar is None:
            self.last_error = "bronkhorst-propar not installed"
            return False
        port = self._find_port()
        if not port:
            self.last_error = "no serial port found"
            return False
        try:
            master = propar.instrument(port).master
            nodes = master.get_nodes()
        except Exception as e:
            self.last_error = f"bus scan failed on {port}: {e}"
            return False

        def rd_retry(inst, dde, tries=5):
            """Read a parameter, retrying transient None/errors (the bus occasionally
            drops a single reply, especially when another reader is contending it)."""
            for _ in range(tries):
                try:
                    v = inst.readParameter(dde)
                except Exception:
                    v = None
                if v is not None:
                    return v
                time.sleep(0.05)
            return None

        argon = iso = None
        for raw in nodes:
            addr = raw["address"] if isinstance(raw, dict) else raw
            inst = propar.instrument(port, addr)
            serial = rd_retry(inst, DDE_SERIAL)
            fluid = rd_retry(inst, DDE_FLUID_NAME)
            model = rd_retry(inst, DDE_MODEL)
            cap = rd_retry(inst, DDE_CAPACITY)
            unit = rd_retry(inst, DDE_CAP_UNIT)
            # Capacity + unit are essential for the flow math; if either couldn't be
            # read this round, skip the node and let the next poll cycle retry it
            # rather than constructing a device with missing calibration.
            if cap is None or unit is None:
                self.last_error = f"node {addr}: incomplete ID read (cap={cap}, unit={unit!r})"
                continue
            cal_inlet = rd_retry(inst, DDE_P_INLET)
            cal_outlet = rd_retry(inst, DDE_P_OUTLET)
            dev = _Device(inst, addr, serial, fluid, model, cap, unit, cal_inlet, cal_outlet)
            role = self._classify(fluid, serial)
            if role == "argon":
                argon = dev
            elif role == "isobutane":
                iso = dev
            # Ensure digital setpoint control so our writes are honoured.
            try:
                inst.writeParameter(DDE_CONTROL, 0)
            except Exception:
                pass

        self.port = port
        self.argon, self.iso = argon, iso
        self.connected = argon is not None and iso is not None
        if not self.connected:
            found = [d.fluid for d in (argon, iso) if d]
            self.last_error = f"need argon+isobutane, found: {found or 'none'}"
        else:
            self.last_error = None
        return self.connected

    # ---------------- commanding ----------------

    def _iso_flow_lnh(self, argon_lnh, iso_percent):
        """Isobutane flow so isobutane is `iso_percent` of the *total* mixture."""
        p = max(0.0, min(99.0, float(iso_percent)))  # 100% total-iso is unphysical here
        if p <= 0:
            return 0.0
        return argon_lnh * p / (100.0 - p)

    def effective_argon_max(self):
        """Max commandable argon flow (ln/h): the device full scale, further limited by
        the temporary low-pressure cap if one is set. 0 if not connected."""
        fs = self.argon.full_scale_lnh if self.argon else 0.0
        if self.argon_limit_lnh is None:
            return fs
        return min(fs, self.argon_limit_lnh)

    def apply_mix(self, argon_lnh, iso_percent):
        """Command both controllers for the requested argon flow (ln/h) and isobutane
        fraction of total. Returns a dict describing what was applied, including any
        clamp warnings."""
        if not self.connected:
            return {"success": False, "message": self.last_error or "not connected"}

        argon_lnh = max(0.0, float(argon_lnh))
        warnings = []
        # Apply the temporary low-pressure argon cap before deriving isobutane, so the
        # requested isobutane fraction still holds against the capped argon flow.
        eff_max = self.effective_argon_max()
        if argon_lnh > eff_max + 1e-9:
            warnings.append(
                f"argon {argon_lnh:.3f} capped to {eff_max:.3f} ln/h ({self.argon_limit_note})")
            argon_lnh = eff_max

        iso_lnh = self._iso_flow_lnh(argon_lnh, iso_percent)
        if iso_lnh > self.iso.full_scale_lnh:
            warnings.append(
                f"isobutane {iso_lnh:.3f} exceeds max {self.iso.full_scale_lnh:.3f} ln/h; clamped")

        with self._lock:
            try:
                self.argon.inst.writeParameter(DDE_CONTROL, 0)
                self.argon.inst.setpoint = self.argon.lnh_to_setpoint(argon_lnh)
                self.iso.inst.writeParameter(DDE_CONTROL, 0)
                self.iso.inst.setpoint = self.iso.lnh_to_setpoint(iso_lnh)
            except Exception as e:
                return {"success": False, "message": f"write failed: {e}"}

        return {"success": True, "warnings": warnings,
                "argon_set_lnh": argon_lnh, "iso_set_lnh": iso_lnh,
                "iso_percent": iso_percent}

    def zero_all(self):
        """Emergency stop: drive both setpoints to 0."""
        if not self.connected:
            return {"success": False, "message": self.last_error or "not connected"}
        with self._lock:
            try:
                self.argon.inst.setpoint = 0
                self.iso.inst.setpoint = 0
            except Exception as e:
                return {"success": False, "message": f"write failed: {e}"}
        return {"success": True, "message": "both setpoints -> 0"}

    # ---------------- reading ----------------

    def _read_device(self, dev):
        """Readback dict for one device (all flows in ln/h). Serial lock held by caller."""
        def rd(dde, default=None):
            try:
                return dev.inst.readParameter(dde)
            except Exception:
                return default
        flow_native = rd(DDE_FMEASURE, 0.0) or 0.0
        set_native = rd(DDE_FSETPOINT, 0.0) or 0.0
        # Valve output is a raw 32-bit register (0..2^24 == 0..100% open), not a
        # percentage; scale it and clamp (it can wind past 100% with no gas supply).
        valve_raw = rd(DDE_VALVE_OUT, 0) or 0
        valve_pct = max(0.0, min(100.0, valve_raw / VALVE_RAW_FULL * 100.0))
        return {
            "fluid": dev.fluid.strip(),
            "serial": dev.serial.strip(),
            "model": dev.model.strip(),
            "node": dev.node,
            "full_scale_lnh": dev.full_scale_lnh,
            "flow_lnh": dev.native_to_lnh(flow_native),
            "set_lnh": dev.native_to_lnh(set_native),
            "valve_pct": valve_pct,
            "temp_c": rd(DDE_TEMPERATURE, 0.0),
            "cal_inlet_bar": dev.cal_inlet,    # calibration reference, not live
            "cal_outlet_bar": dev.cal_outlet,  # calibration reference, not live
        }

    def _poll_once(self):
        """Read both devices under the serial lock and build the shared state dict."""
        with self._lock:
            argon = self._read_device(self.argon)
            iso = self._read_device(self.iso)

        total_meas = argon["flow_lnh"] + iso["flow_lnh"]
        total_set = argon["set_lnh"] + iso["set_lnh"]
        state = {
            "connected": True,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "argon": argon,
            "iso": iso,
            "derived": {
                "total_flow_lnh": total_meas,
                "total_set_lnh": total_set,
                "iso_pct_meas": (iso["flow_lnh"] / total_meas * 100.0) if total_meas > 0 else 0.0,
                "iso_pct_set": (iso["set_lnh"] / total_set * 100.0) if total_set > 0 else 0.0,
            },
            "poll_s": self.poll_s,
            "log_s": self.log_s,
        }
        with self._state_lock:
            self._state = state
        return state

    def get_state(self):
        """Latest cached readback for the web UI (never touches the bus directly)."""
        with self._state_lock:
            state = dict(self._state)
        if not state:
            state = {"connected": False}
        state.setdefault("connected", self.connected)
        state["last_error"] = self.last_error
        state["csv_path"] = self._csv_path()
        if self.argon_limit_lnh is not None:
            state["argon_limit"] = {
                "lnh": self.argon_limit_lnh,
                "note": self.argon_limit_note,
                "effective_max_lnh": self.effective_argon_max(),
            }
        if self._last_command_result is not None:
            state["last_command"] = self._last_command_result
        return state

    # ---------------- CSV logging ----------------

    def _csv_path(self):
        day = datetime.now().strftime("%Y-%m-%d")
        return os.path.join(self.log_dir, f"gas_flow_{day}.csv")

    _CSV_FIELDS = [
        "timestamp",
        "argon_set_lnh", "argon_flow_lnh", "argon_valve_pct", "argon_temp_c",
        "iso_set_lnh", "iso_flow_lnh", "iso_valve_pct", "iso_temp_c",
        "iso_pct_set", "iso_pct_meas", "total_flow_lnh",
    ]

    def _log_row(self, state):
        try:
            os.makedirs(self.log_dir, exist_ok=True)
            path = self._csv_path()
            new_file = not os.path.exists(path)
            a, i, d = state["argon"], state["iso"], state["derived"]
            row = {
                "timestamp": state["timestamp"],
                "argon_set_lnh": round(a["set_lnh"], 5),
                "argon_flow_lnh": round(a["flow_lnh"], 5),
                "argon_valve_pct": round(a["valve_pct"] or 0.0, 3),
                "argon_temp_c": round(a["temp_c"] or 0.0, 3),
                "iso_set_lnh": round(i["set_lnh"], 5),
                "iso_flow_lnh": round(i["flow_lnh"], 5),
                "iso_valve_pct": round(i["valve_pct"] or 0.0, 3),
                "iso_temp_c": round(i["temp_c"] or 0.0, 3),
                "iso_pct_set": round(d["iso_pct_set"], 4),
                "iso_pct_meas": round(d["iso_pct_meas"], 4),
                "total_flow_lnh": round(d["total_flow_lnh"], 5),
            }
            with open(path, "a", newline="") as f:
                w = csv.DictWriter(f, fieldnames=self._CSV_FIELDS)
                if new_file:
                    w.writeheader()
                w.writerow(row)
        except Exception as e:
            print(f"[flow_controller] CSV log failed: {e}")

    # ---------------- watcher IPC (state file + command file) ----------------

    def log(self, msg):
        """Timestamped, prefixed line for the gas_watcher tmux pane (and its logs)."""
        print(f"{datetime.now().strftime('%H:%M:%S')} [gas_watcher] {msg}", flush=True)

    def _write_state(self, state):
        """Atomically publish the current state for the Flask app to read."""
        try:
            os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
            tmp = self.state_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(state, f)
            os.replace(tmp, self.state_path)   # atomic: readers never see a partial file
        except Exception as e:
            print(f"[flow_controller] state write failed: {e}")

    def prime_command_id(self):
        """Adopt the id currently in the command file WITHOUT applying it, so a watcher
        restart doesn't re-fire the last command (the setpoint is already in hardware)."""
        try:
            with open(self.command_path) as f:
                self._last_command_id = json.load(f).get("id")
        except Exception:
            self._last_command_id = None

    def _process_command(self):
        """Apply a newly-written setpoint command from the command file (id-deduped so
        each command runs once). Records the result back into the state file as an ack."""
        try:
            with open(self.command_path) as f:
                cmd = json.load(f)
        except (FileNotFoundError, ValueError, OSError):
            return
        cid = cmd.get("id")
        if cid is None or cid == self._last_command_id:
            return
        self._last_command_id = cid
        action = cmd.get("cmd")
        if action == "apply":
            res = self.apply_mix(cmd.get("argon_lnh"), cmd.get("iso_percent"))
        elif action == "zero":
            res = self.zero_all()
        else:
            res = {"success": False, "message": f"unknown command {action!r}"}
        res["id"] = cid
        self._last_command_result = res
        self.log(f"command {action}: success={res.get('success')} "
                 f"{res.get('warnings') or res.get('message') or ''}")
        self._write_state(self.get_state())   # publish ack immediately (fast Flask reply)

    # ---------------- poll loop ----------------

    def _run(self):
        while not self._stop.is_set():
            if not self.connected:
                with self._lock:
                    self._connect()
                if self.connected:
                    self.log(f"connected: argon node {self.argon.node}, iso node {self.iso.node}")
                else:
                    with self._state_lock:
                        self._state = {"connected": False, "last_error": self.last_error}
                    self._write_state(self.get_state())
                    self._process_command()   # records "not connected" for any pending cmd
                    self._stop.wait(min(5.0, max(self.poll_s, 2.0)))
                    continue
            # Apply any queued setpoint command, then read + publish + log.
            self._process_command()
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
                # Lost the bus mid-run: mark disconnected and let the loop rediscover.
                self.last_error = f"poll failed: {e}"
                self.connected = False
            self._stop.wait(self.poll_s)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="gas-mixer-poll", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def run_blocking(self):
        """Run the poll/log/command loop in the current thread until SIGINT/SIGTERM.
        Used by gas_watcher.py. Does NOT zero setpoints on exit — the controllers hold
        their setpoints in hardware, so gas keeps flowing across a watcher restart."""
        signal.signal(signal.SIGINT, lambda *a: self._stop.set())
        signal.signal(signal.SIGTERM, lambda *a: self._stop.set())
        self.prime_command_id()
        self.log(f"gas watcher starting (poll {self.poll_s}s, log {self.log_s}s)")
        self._run()
        self.log("gas watcher stopped (setpoints left as-is in hardware)")


# Module-level singleton so the Flask app shares one controller/logger.
_controller = None
_controller_lock = threading.Lock()


def get_controller(**kwargs):
    global _controller
    with _controller_lock:
        if _controller is None:
            _controller = FlowController(**kwargs)
            _controller.start()
    return _controller


def _demo():
    """Quick standalone check: connect, print one readback, exit (no setpoint change)."""
    fc = FlowController()
    fc.start()
    time.sleep(3)
    import json
    print(json.dumps(fc.get_state(), indent=2, default=str))
    fc.stop()


if __name__ == "__main__":
    _demo()
