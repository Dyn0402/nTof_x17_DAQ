#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on July 14 2026
Created as nTof_x17_DAQ/n1081b/timetag_watcher_controller.py

@author: Dylan Neff, dylan

Always-on Time-Tag telemetry watcher for N1081B Module 5 (.244), which reads the
four scintillator-wall sections (A-D) as per-edge timestamps. Mirrors the gas /
3He / beam watcher split: one background process owns the board's websocket, polls
continuously, appends per-edge rows to a per-day CSV, and atomically publishes a
state file the Flask app reads. Read-only to the physics: it only reconfigures its
own board (.244), never the DAQ / HV / DREAM.

WHY THIS WORKS (the persistent-FIFO scheme, validated 2026-07-14 on .244, fw
2025.3.27.0 -- full writeup in n1081b/TIMETAG_MULTISECTION_2026-07-13.md):

- Set every section to FN_TIME_TAG ONCE. Each section then accumulates edge
  timestamps into its OWN buffer, concurrently, even while it is not the actively
  tapped stream. So we do NOT cycle the section FUNCTION (the ~91 ms FPGA switch);
  we arm once and read the buffers round-robin.
- To read a section: `reset_channel` + `start_tt_data` DUMPS that section's buffered
  backlog (reset does NOT clear it -- it is the trigger to flush), drain the
  broadcast `send_data` packets, then `stop_tt_data`. `start_tt_data` ALONE returns
  nothing -- the reset is mandatory.
- The board buffer holds ~tens of seconds of history, so polling each section every
  ~1-2 s cannot miss a spill. In steady polling `reset+start` returns essentially
  only-new tags (measured overlap ~1.0), but reads can return an overlapping window,
  so we DEDUP by (section, channel, t_board_ns) across a rolling horizon -> every
  edge is written exactly once (validated: zero late-appearances over a 32 s run).
- ATTRIBUTION is by which section we tapped: tap ONE section at a time (never two --
  simultaneous taps merge into one indistinguishable stream). The `send_data` packet
  carries no section id; the panel number (1-6) is the same on every section, so it
  does NOT identify the section.

COEXISTENCE: while this watcher streams .244, NOTHING else may open an SDK
connection to .244 (the board broadcasts send_data to every client and would desync
their reads). poll_modules.py must drop .244 from POLL_IPS while this runs (its
docstring already says so). This watcher is .244's sole owner, exactly like the gas
watcher owns the FLOW-BUS.

TIMESTAMP MATCHING TO DREAM (offline): each row carries host_unix (the wall time of
the poll that captured it -- coarse, good to ~cycle time) and t_board_ns (the precise
10 ns free-running board clock, common to all sections). The (host_unix, max
t_board_ns) pairs give a coarse board->wall anchor; the precise alignment to a DREAM
run comes from sliding/cross-correlating the shared beam-spill rate structure (as
with the TIMBER beam matching). See n1081b/TIMETAG_WATCHER.md.
"""

import os
import csv
import json
import time
import signal
import socket
import threading
import collections
from datetime import datetime

try:
    from n1081b_sdk import N1081B
except Exception:  # keep import-safe so the Flask app still boots without the SDK
    N1081B = None

try:
    from websocket import WebSocketTimeoutException
except Exception:
    class WebSocketTimeoutException(Exception):
        pass

_IDLE = (socket.timeout, WebSocketTimeoutException)

# Shared file paths for the watcher/Flask split (resolved relative to the repo so
# the watcher process and the Flask app always agree).
_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_DIR = os.path.dirname(_MODULE_DIR)
N1081B_TT_LOG_DIR = os.path.join(_MODULE_DIR, "logs")
N1081B_TT_STATE_PATH = os.path.join(_REPO_DIR, "config", "n1081b_timetag_state.json")
N1081B_TT_CONFIG_PATH = os.path.join(_REPO_DIR, "config", "n1081b_timetag_config.json")

NS_PER_S = 1e8               # board clock: 10 ns steps -> 1e8 ticks / second
DEFAULT_IP = "192.168.10.244"
DEFAULT_SECTIONS = "ABCD"

# --- cadence limits (per-section drain + full-cycle pacing) ---
# One full cycle taps each armed section once. Measured on .244: ~380 ms drain per
# section during beam, ~1.6 s for all four -> each section polled every ~1.6 s, far
# inside the ~tens-of-seconds board buffer horizon. These are the safe knobs.
MIN_CYCLE_PERIOD_S = 0.0     # 0 = poll back-to-back (drain time paces it)
MAX_CYCLE_PERIOD_S = 30.0    # never let a section go longer than this between polls
DEFAULT_CYCLE_PERIOD_S = 0.0
DRAIN_MAX_S = 0.35           # hard cap on one section's drain
DRAIN_QUIET_S = 0.12         # stop early if the buffer goes silent this long
HORIZON_S = 40.0             # dedup memory depth (must exceed board buffer horizon)


def clamp_cycle(p):
    """Clamp a requested inter-cycle sleep (seconds) into the safe range."""
    try:
        p = float(p)
    except (TypeError, ValueError):
        return DEFAULT_CYCLE_PERIOD_S
    return max(MIN_CYCLE_PERIOD_S, min(MAX_CYCLE_PERIOD_S, p))


class N1081BTimeTagController:
    """Owns the .244 websocket plus the persistent-FIFO poll/log thread."""

    def __init__(self, ip=DEFAULT_IP, password="password", sections=DEFAULT_SECTIONS,
                 cycle_s=DEFAULT_CYCLE_PERIOD_S, state_path=N1081B_TT_STATE_PATH,
                 config_path=N1081B_TT_CONFIG_PATH, log_dir=None):
        self.ip = ip
        self.password = password
        # 'A'->SEC_A ...; keep only the requested letters, in A..D order.
        self.letters = [c for c in "ABCD" if c in sections.upper()]
        self.cycle_s = clamp_cycle(cycle_s)
        self.state_path = state_path
        self.config_path = config_path
        self.log_dir = log_dir or N1081B_TT_LOG_DIR

        self.dev = None
        self.connected = False
        self.last_error = None

        # dedup memory: per-section OrderedDict {(channel, t_board): None}, insertion
        # order ~ arrival, pruned by board-clock horizon.
        self._seen = {c: collections.OrderedDict() for c in self.letters}
        self._last_max_t = {c: 0 for c in self.letters}
        # rolling stats for the state file
        self._rate_hz = {c: 0.0 for c in self.letters}
        self._total_today = {c: 0 for c in self.letters}
        self._today = None
        self._last_cycle_t = None
        self._cycles = 0

        self._state = {}
        self._state_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    # ---------------- SDK enum handles (lazy; SDK may be absent at import) ----------------

    @property
    def _SEC(self):
        return {s.name[-1]: s for s in N1081B.Section}

    @property
    def _TT(self):
        return N1081B.FunctionType.FN_TIME_TAG

    @property
    def _CNT(self):
        return N1081B.FunctionType.FN_COUNTER

    # ---------------- connection / arming ----------------

    def _open(self):
        dev = N1081B(self.ip)
        if not dev.connect():
            raise ConnectionError(f"connect() failed for {self.ip}")
        dev.ws.settimeout(6)
        if not dev.login(self.password):
            raise ConnectionError(f"login failed for {self.ip}")
        return dev

    def _connect(self):
        """Open the websocket, arm every section to Time-Tag, and flush the stale
        power-on backlog so the first real cycle starts clean. Sets self.connected."""
        if N1081B is None:
            self.last_error = "n1081b_sdk not installed"
            return False
        try:
            dev = self._open()
            for c in self.letters:
                sec = self._SEC[c]
                dev.set_section_function(sec, self._TT)
                dev.configure_time_tagging(sec, True, True, True, True, True, True)
            # one flush per section drops the (possibly huge) accumulated backlog;
            # interruptible so SIGTERM during startup shuts down promptly.
            for c in self.letters:
                if self._stop.is_set():
                    break
                self._tap(dev, c)
                self._drain(dev, 1.2, 0.4)
                self._untap(dev, c)
                time.sleep(0.05)
        except Exception as e:
            self.last_error = f"connect/arm failed: {e}"
            self._safe_disconnect(dev if 'dev' in dir() else None)
            self.dev = None
            self.connected = False
            return False
        self.dev = dev
        self.connected = True
        self.last_error = None
        return True

    def _safe_disconnect(self, dev):
        try:
            if dev is not None:
                dev.disconnect()
        except Exception:
            pass

    # ---------------- raw stream primitives ----------------

    def _raw(self, dev, obj):
        dev.ws.send(json.dumps(obj))

    def _tap(self, dev, letter):
        v = self._SEC[letter].value
        self._raw(dev, {"command": "reset_channel", "callback": "s",
                        "params": {"section": v, "channel": 1}})
        self._raw(dev, {"command": "start_tt_data", "callback": "s",
                        "params": {"section": v}})

    def _untap(self, dev, letter):
        v = self._SEC[letter].value
        self._raw(dev, {"command": "stop_tt_data", "callback": "s", "params": {"section": v}})

    def _drain(self, dev, max_s, quiet_s):
        """Collect [channel, t_board_ns] tags until the buffer goes quiet or max_s."""
        dev.ws.settimeout(0.1)
        t0 = time.time()
        last = t0
        tags = []
        while time.time() - t0 < max_s:
            try:
                raw = dev.ws.recv()
            except _IDLE:
                if tags and (time.time() - last) > quiet_s:
                    break
                continue
            except Exception:
                break
            try:
                pkt = json.loads(raw.replace(",]", "]"))
            except Exception:
                continue
            if pkt.get("command") == "send_data":
                got = [[e[0], e[1]] for e in pkt.get("timetag_data", [])
                       if isinstance(e, list) and len(e) >= 2]
                if got:
                    tags += got
                    last = time.time()
        return tags

    # ---------------- one poll cycle ----------------

    def _poll_cycle(self):
        """Tap each section once, dedup, and return the list of NEW rows
        (host_unix, section, channel, t_board_ns) plus per-section new counts."""
        rows = []
        new_counts = {c: 0 for c in self.letters}
        for c in self.letters:
            if self._stop.is_set():
                break
            self._tap(self.dev, c)
            tags = self._drain(self.dev, DRAIN_MAX_S, DRAIN_QUIET_S)
            self._untap(self.dev, c)
            now = time.time()
            seen = self._seen[c]
            if tags:
                tmax = 0
                for ch, t in tags:
                    key = (ch, t)
                    if key not in seen:
                        seen[key] = None
                        rows.append((f"{now:.3f}", c, ch, t))
                        new_counts[c] += 1
                    if t > tmax:
                        tmax = t
                self._last_max_t[c] = max(self._last_max_t[c], tmax)
                # prune dedup memory older than the horizon
                cut = self._last_max_t[c] - int(HORIZON_S * NS_PER_S)
                while seen:
                    k = next(iter(seen))
                    if k[1] < cut:
                        seen.popitem(last=False)
                    else:
                        break
        return rows, new_counts

    # ---------------- CSV logging ----------------

    def _csv_path(self):
        day = datetime.now().strftime("%Y-%m-%d")
        return os.path.join(self.log_dir, f"n1081b_timetag_{day}.csv")

    _CSV_FIELDS = ["host_unix", "section", "channel", "t_board_ns"]

    def _log_rows(self, rows):
        if not rows:
            return
        try:
            os.makedirs(self.log_dir, exist_ok=True)
            path = self._csv_path()
            new_file = not os.path.exists(path)
            with open(path, "a", newline="") as f:
                w = csv.writer(f)
                if new_file:
                    w.writerow(self._CSV_FIELDS)
                w.writerows(rows)
        except Exception as e:
            self.log(f"CSV log failed: {e}")

    # ---------------- state file ----------------

    def log(self, msg):
        # Best-effort: a killed tmux pane makes stdout raise (EIO/BrokenPipe); swallow it
        # so a shutdown log line can never abort the counter-restore in the finally block.
        try:
            print(f"{datetime.now().strftime('%H:%M:%S')} [n1081b_timetag_watcher] {msg}", flush=True)
        except Exception:
            pass

    def _roll_day(self):
        """Reset per-day totals when the date changes."""
        day = datetime.now().strftime("%Y-%m-%d")
        if day != self._today:
            self._today = day
            self._total_today = {c: 0 for c in self.letters}

    def get_state(self):
        with self._state_lock:
            state = dict(self._state)
        state.setdefault("connected", self.connected)
        state["last_error"] = self.last_error
        state["csv_path"] = self._csv_path()
        state["ip"] = self.ip
        state["sections"] = "".join(self.letters)
        state["cycle_s"] = self.cycle_s
        return state

    def _publish(self):
        state = {
            "connected": self.connected,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "ip": self.ip,
            "sections": "".join(self.letters),
            "rate_hz": {c: round(self._rate_hz[c], 1) for c in self.letters},
            "rate_hz_total": round(sum(self._rate_hz.values()), 1),
            "total_today": dict(self._total_today),
            "total_today_all": sum(self._total_today.values()),
            "last_t_board_ns": dict(self._last_max_t),
            "cycles": self._cycles,
            "cycle_s": self.cycle_s,
            "last_error": self.last_error,
        }
        with self._state_lock:
            self._state = state
        try:
            os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
            tmp = self.state_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.get_state(), f)
            os.replace(tmp, self.state_path)
        except Exception as e:
            self.log(f"state write failed: {e}")

    def _apply_config(self):
        """Pick up an inter-cycle sleep change from the config file (GUI-tunable)."""
        try:
            with open(self.config_path) as f:
                cfg = json.load(f)
        except (FileNotFoundError, ValueError, OSError):
            return
        p = cfg.get("cycle_s")
        if p is None:
            return
        p = clamp_cycle(p)
        if p != self.cycle_s:
            self.log(f"cycle period -> {p:.3f}s")
            self.cycle_s = p

    # ---------------- poll loop ----------------

    def _run(self):
        self._apply_config()
        while not self._stop.is_set():
            self._apply_config()
            self._roll_day()
            if not self.connected:
                if self._connect():
                    self.log(f"connected + armed sections {self.letters} on {self.ip}")
                else:
                    self._publish()
                    self.log(f"connect failed ({self.last_error}); retry in 5 s")
                    self._stop.wait(5.0)
                    continue
            try:
                t_start = time.time()
                rows, new_counts = self._poll_cycle()
                dt = time.time() - t_start
                self._log_rows(rows)
                self._cycles += 1
                for c in self.letters:
                    self._total_today[c] += new_counts[c]
                    inst = new_counts[c] / dt if dt > 0 else 0.0
                    # light EWMA so the GUI number is not too jumpy
                    self._rate_hz[c] = 0.5 * self._rate_hz[c] + 0.5 * inst
                self._publish()
            except Exception as e:
                self.last_error = f"stream error: {e}"
                self.log(f"{self.last_error}; reconnecting")
                self.connected = False
                self._safe_disconnect(self.dev)
                self.dev = None
                self._publish()
                self._stop.wait(2.0)
                continue
            if self.cycle_s > 0:
                self._stop.wait(self.cycle_s)

    # ---------------- restore ----------------

    def restore_counters(self, attempts=3):
        """Return .244 to its steady state: all sections FN_COUNTER (lemo0-3, no gate)
        and counting. Fresh connection (the streaming socket may be desynced), and uses
        the SDK methods (which recv their replies -> no desync) rather than raw sends.
        Verifies every section reads back 'counter' and retries if not.

        NOTE: the board auto-reverts sections to 'wire' passthrough when a TT stream
        socket is closed abruptly, so this MUST run on every clean exit; if the process
        is SIGKILLed it cannot, and .244 is left in 'wire' until this is rerun (see the
        standalone `--restore` entry point in n1081b_timetag_watcher.py)."""
        if N1081B is None:
            return False
        for attempt in range(1, attempts + 1):
            dev = None
            try:
                dev = self._open()
                for c in self.letters:
                    sec = self._SEC[c]
                    dev.set_section_function(sec, self._CNT)
                    dev.configure_counter(sec, True, True, True, True, False)
                    for ch in range(4):
                        dev.reset_channel(sec, ch, self._CNT)   # SDK: sends + recvs reply
                names = [x["function_name"] for x in dev.get_sections_function()["data"]]
                dev.disconnect()
                target = [self._SEC[c].value for c in self.letters]
                ok = all(names[v] == "counter" for v in target)
                if ok:
                    self.log(f"restored sections to counter: {names}")
                    return True
                self.log(f"restore attempt {attempt}: readback {names} not all counter; retrying")
            except Exception as e:
                self._safe_disconnect(dev)
                self.log(f"restore attempt {attempt} failed ({e!r})")
            time.sleep(1.0)
        self.log("RESTORE FAILED after retries -- .244 may be left in wire/time_tag mode; "
                 "run `python n1081b_timetag_watcher.py --restore` or reboot the board")
        return False

    # ---------------- lifecycle ----------------

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="n1081b-tt-poll", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def run_blocking(self):
        # Catch SIGHUP too: `tmux kill-session` (the GUI stop button + start_servers path)
        # delivers SIGHUP, and we MUST run the finally-block counter-restore on it, else
        # .244 is left in 'wire'. SIGINT/SIGTERM cover Ctrl-C and `kill`.
        for _sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            try:
                signal.signal(_sig, lambda *a: self._stop.set())
            except (ValueError, OSError):
                pass  # SIGHUP absent on non-POSIX; ignore
        self.log(f"N1081B time-tag watcher starting ({self.ip}, sections {self.letters}, "
                 f"persist-all round-robin, cycle_s={self.cycle_s})")
        try:
            self._run()
        finally:
            self._safe_disconnect(self.dev)
            self.dev = None
            self.connected = False
            self.restore_counters()
            self._publish()
            self.log("N1081B time-tag watcher stopped")


def _demo():
    """Standalone check: run for ~12 s, print state, restore."""
    c = N1081BTimeTagController()
    c.start()
    time.sleep(12)
    print(json.dumps(c.get_state(), indent=2, default=str))
    c.stop()
    time.sleep(1)
    c.restore_counters()


if __name__ == "__main__":
    _demo()
