#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on July 14 2026; v2 (long-dwell rotation) and v3 (rate-gated) July 17 2026
Created as nTof_x17_DAQ/n1081b/timetag_watcher_controller.py

@author: Dylan Neff, dylan

Always-on telemetry watcher for N1081B Module 5 (.244, the scintillator-wall
monitor board). Mirrors the gas / 3He / beam watcher split: one background
process owns the board, logs to per-day CSVs, and atomically publishes a state
file the Flask app reads. Read-only to the physics: it only reconfigures its own
board (.244), never the DAQ / HV / DREAM.

=== v3 REDESIGN (2026-07-17 evening) — the TT rate ceiling ==================

HANDOFF_2026-07-17_tt_rate_ceiling.md changed the model this watcher lives in:

- A section emits ZERO time-tags whenever its input rate is above a live
  ceiling (bracketed ~50 Hz streams / ~800 Hz silent, at stream-start time) and
  streams fine below it. Nothing "wedges" per-section; reboots are irrelevant.
- The walls (A, ~3-6 kHz beam-off) and liq (B, ~19-27 kHz) are therefore
  UNSTREAMABLE at current rates; C and D (trigger taps, tens of Hz) stream fine
  — except when their own rates spike above the ceiling, which happens
  minute-to-minute beam-off.
- Backlog dumps can NOT be assumed to cover un-tapped gaps (07-14 measured a
  real ~30 s backlog; 07-17 measured essentially none; regime selector unknown).
- The v2 watcher's 02:35 death is now understood: C/D rates spiked over the
  ceiling -> taps went silent -> "stream errors" -> 5 reconnect+re-arm cycles
  onto a HEALTHY board -> full-board wedge from the reconnect churn itself.

v3 therefore does, per gate cycle (default every 5 min):

1. COUNTERS FIRST (fresh session, s.call phase). All managed sections are put
   back to counter mode (streamed sections auto-revert to 'wire' when their
   stream socket drops), counters are read twice a few seconds apart, and a row
   per section (absolute counts, delta since last cycle, aggregate Hz) is
   appended to the counters CSV. This is the A/B "running total" record — pure
   FPGA counting, works at any rate — and doubles as the rate measurement for
   the gate.
2. RATE GATE. Only TT-candidate sections (default C and D) whose aggregate rate
   is at or below gate_hz (default 40 Hz, safely under the ~50 Hz
   proven-streaming point) are armed to Time-Tag. Sections above the gate stay
   counters this cycle and are re-tested next cycle. A gated-in section whose
   rate spikes mid-cycle simply goes silent — that is EXPECTED and harmless
   (recv timeouts are idle waits, not errors).
3. TT STREAMING (raw one-way phase). Eligible sections are visited round-robin,
   one stream held open dwell_s (default 12 s) at a time, edges deduped and
   appended to the edges CSV. NO completeness assumption: while a section is
   un-tapped its edges may be lost (see backlog caveat above) — this is
   telemetry for offline timestamp matching, not a complete edge record.
4. ZERO RECONNECTS (stop, don't hammer). ANY error — login, s.call, or
   mid-stream socket error — publishes an alarm and STOPS the watcher. There is
   no reconnect budget in v3 (it was reconnect churn that wedged the board on
   07-17). The planned per-cycle session rebuild is the only reconnection.
5. SILENT-DESPITE-GATE health check (replaces v2's beam-on check). If a section
   passes the gate (measured >= MIN_EXPECT_HZ) yet streams zero edges for
   MAX_SILENT_STRIKES consecutive cycles, the ceiling model says something is
   actually wrong -> alarm + stop. Silence on an over-ceiling section means
   nothing and never strikes.

Acquisition facts this rests on (measured on .244, fw 2025.3.27.0 — see
TIMETAG_MULTISECTION_2026-07-13.md + HANDOFF_2026-07-17_tt_rate_ceiling.md):
- Counters are pure FPGA and count at any input rate; TT tags must cross the
  Zynq daemon and are rate-limited as above.
- Arm a section FN_TIME_TAG once; reset_channel + start_tt_data starts its
  stream (and dumps whatever backlog regime the board is in).
- send_data packets carry NO section id and are broadcast to every client, so:
  tap ONE section at a time, and nothing else may hold a connection to .244
  while streaming (the session flock enforces this; poll_modules also skips
  .244 whenever the watcher's tmux session is alive).
- From the first raw send a session is one-way (replies drained and ignored);
  no s.call may run on it again. All s.call work happens strictly before.
- Reads can overlap between taps -> dedup by (section, channel, t_board_ns).
- configure_time_tagging returns Result:False on this firmware but applies.

TIMESTAMP MATCHING TO DREAM (offline): each edge row carries host_unix (packet
receive wall time) and t_board_ns (10 ns free-running board clock, common to
all sections). Coarse board->wall anchor from minimum-offset pairs; fine
alignment by cross-correlating beam-spill rate structure. See TIMETAG_WATCHER.md.

v2 history (long-dwell rotation over all four sections, reconnect budget 4/h,
beam-on zero-edge health check) is preserved in git; v1 (fast round-robin)
wedged the board on 07-15 — HANDOFF_2026-07-15_timetag_watcher_board_wedge.md.
"""

import csv
import json
import os
import signal
import socket
import threading
import time
from collections import OrderedDict
from datetime import datetime

# Import-safe: the Flask app imports this module for the path constants below and
# must boot even where the SDK / websocket stack is absent.
try:
    from n1081b_sdk import N1081B
except Exception:
    N1081B = None

try:
    from websocket import WebSocketTimeoutException
except Exception:
    class WebSocketTimeoutException(Exception):
        pass

try:
    from n1081b.n1081b_session import (board_session, BoardBusyError,
                                       BoardWedgedError, BoardQuarantinedError)
except Exception:
    try:
        from n1081b_session import (board_session, BoardBusyError,
                                    BoardWedgedError, BoardQuarantinedError)
    except Exception:
        board_session = None

        class BoardBusyError(RuntimeError):
            pass

        class BoardWedgedError(RuntimeError):
            pass

        class BoardQuarantinedError(RuntimeError):
            pass

_IDLE = (socket.timeout, WebSocketTimeoutException)

# Shared file paths for the watcher/Flask split (resolved relative to the repo so
# the watcher process and the Flask app always agree).
_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_DIR = os.path.dirname(_MODULE_DIR)
# Per the slow-control migration (2026-07-15): telemetry CSVs live OUT of the repo.
N1081B_TT_LOG_DIR = os.path.expanduser("~/beam_july/slow_control/n1081b_timetag")
N1081B_TT_STATE_PATH = os.path.join(_REPO_DIR, "config", "n1081b_timetag_state.json")
N1081B_TT_CONFIG_PATH = os.path.join(_REPO_DIR, "config", "n1081b_timetag_config.json")
BEAM_STATE_PATH = os.path.join(_REPO_DIR, "config", "beam_state.json")

NS_PER_S = 1e9               # board TT clock: 1 ns ticks (MEASURED 2026-07-18 vs
                             # host over 6 h, exactly 1000.0 MHz; the older docs'
                             # "10 ns steps" was wrong — t_board_ns really is ns)
DEFAULT_IP = "192.168.10.244"
DEFAULT_SECTIONS = "ABCD"    # sections managed as counters (running totals)
DEFAULT_TT_SECTIONS = "CD"   # TT-streaming candidates (subset; gated per cycle)

# --- rate gate (the v3 safety knob; see module docstring §2) ---
DEFAULT_GATE_HZ = 40.0       # stream only sections at/below this aggregate rate
MIN_GATE_HZ = 5.0
MAX_GATE_HZ = 300.0          # ceiling bracket is 50-800 Hz; stay well under it
DEFAULT_GATE_PERIOD_S = 300.0   # one full cycle: counters+gate, then stream
MIN_GATE_PERIOD_S = 120.0
MAX_GATE_PERIOD_S = 1800.0
RATE_SAMPLE_S = 4.0          # spacing of the two counter reads behind each rate

# --- cadence (see module docstring §3) ---
DEFAULT_DWELL_S = 12.0       # one section's stream held open this long per tap
MIN_DWELL_S = 5.0
MAX_DWELL_S = 60.0
SETTLE_S = 0.4               # pause between stopping one section and tapping the next
CMD_GAP_S = 0.12             # pacing between raw stream-command sends
DRAIN_RECV_TIMEOUT_S = 0.5   # ws recv timeout inside a dwell (idle wait, no board load)

# --- health (silent-despite-gate; see module docstring §5) ---
MIN_EXPECT_HZ = 5.0          # only strike if the gate-time rate was at least this
MAX_SILENT_STRIKES = 3       # consecutive gated-in-but-silent cycles -> stop + alarm
BEAM_STATE_FRESH_S = 120.0   # beam_state.json older than this counts as beam-unknown

PUBLISH_EVERY_S = 5.0        # state-file heartbeat, incl. mid-dwell and idle wait


def clamp(v, lo, hi, default):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


class N1081BTimeTagController:
    """Owns .244 (through the session gateway): per-cycle counter logging + rate
    gate + TT streaming of the under-ceiling sections."""

    def __init__(self, ip=DEFAULT_IP, password="password", sections=DEFAULT_SECTIONS,
                 tt_sections=DEFAULT_TT_SECTIONS, dwell_s=DEFAULT_DWELL_S,
                 gate_hz=DEFAULT_GATE_HZ, gate_period_s=DEFAULT_GATE_PERIOD_S,
                 state_path=N1081B_TT_STATE_PATH, config_path=N1081B_TT_CONFIG_PATH,
                 log_dir=None):
        self.ip = ip
        self.password = password
        # 'A'->SEC_A ...; keep only the requested letters, in A..D order.
        self.letters = [c for c in "ABCD" if c in sections.upper()]
        self.tt_letters = [c for c in self.letters if c in tt_sections.upper()]
        self.dwell_s = clamp(dwell_s, MIN_DWELL_S, MAX_DWELL_S, DEFAULT_DWELL_S)
        self.gate_hz = clamp(gate_hz, MIN_GATE_HZ, MAX_GATE_HZ, DEFAULT_GATE_HZ)
        self.gate_period_s = clamp(gate_period_s, MIN_GATE_PERIOD_S,
                                   MAX_GATE_PERIOD_S, DEFAULT_GATE_PERIOD_S)
        self.state_path = state_path
        self.config_path = config_path
        self.log_dir = log_dir or N1081B_TT_LOG_DIR

        self.connected = False
        self.last_error = None
        self.alarm = None            # set -> the watcher stopped itself; human needed
        self.deadline = None         # optional wall-clock stop (--duration)

        # counter bookkeeping (all managed sections)
        self._prev_counts = {c: None for c in self.letters}   # last cycle's absolutes
        self._counter_rates = {c: None for c in self.letters}  # gate-time agg Hz
        self._counts_today = {c: 0 for c in self.letters}

        # TT bookkeeping (candidate sections only)
        # dedup memory: per-section OrderedDict {(channel, t_board): None}, insertion
        # order ~ arrival, pruned by a board-clock horizon.
        self._seen = {c: OrderedDict() for c in self.tt_letters}
        self._last_max_t = {c: 0 for c in self.tt_letters}
        self._last_tap_t = {c: None for c in self.tt_letters}
        self._tt_rate_hz = {c: 0.0 for c in self.tt_letters}
        self._edges_today = {c: 0 for c in self.tt_letters}
        self._tt_status = {c: "pending" for c in self.tt_letters}
        self._silent_strikes = {c: 0 for c in self.tt_letters}

        self._today = None
        self._cycles = 0             # completed gate cycles
        self._session_started = None
        self._last_publish = 0.0
        self._last_edge_t = None     # wall time of the last NEW edge on any section

        self._state = {}
        self._state_lock = threading.Lock()
        self._stop = threading.Event()

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

    # ---------------- beam state (display only in v3) ----------------

    def _beam_on(self):
        try:
            with open(BEAM_STATE_PATH) as f:
                st = json.load(f)
            age = (datetime.now()
                   - datetime.fromisoformat(st["timestamp"])).total_seconds()
        except Exception:
            return False
        return bool(st.get("beam_on")) and 0 <= age < BEAM_STATE_FRESH_S

    # ---------------- counter phase (s.call, before any raw send) ----------------

    def _ensure_counters(self, s):
        """Put every managed section that is not already a counter back to counter
        mode (sections auto-revert to 'wire' when their TT stream socket drops)."""
        names = [x["function_name"] for x in s.call("get_sections_function")["data"]]
        for c in self.letters:
            if names[self._SEC[c].value] != "counter":
                s.call("set_section_function", self._SEC[c], self._CNT)
                s.call("configure_counter", self._SEC[c], True, True, True, True, False)

    def _read_counters(self, s, letter):
        vals = [x["value"] for x in
                s.call("get_function_results", self._SEC[letter])["data"]["counters"]]
        return time.time(), vals

    def _measure_counters(self, s):
        """Two paced counter reads per section -> aggregate Hz for the gate, plus a
        counters-CSV row per section: absolute counts, delta since last cycle
        (reset-tolerant), aggregate rate. This is the A/B running-total record."""
        r1 = {c: self._read_counters(s, c) for c in self.letters}
        self._stop.wait(RATE_SAMPLE_S)
        r2 = {c: self._read_counters(s, c) for c in self.letters}
        rows = []
        for c in self.letters:
            (t1, v1), (t2, v2) = r1[c], r2[c]
            span = max(t2 - t1, 0.1)
            agg = sum(max(0, b - a) for a, b in zip(v1, v2)) / span
            self._counter_rates[c] = agg
            prev = self._prev_counts[c]
            if prev is None:
                delta = [0] * len(v2)   # first cycle: no baseline, don't inherit history
            else:
                # b < prev means the counter was reset (function change / another
                # tool / rollover): count only what accumulated since the reset.
                delta = [b - p if b >= p else b for b, p in zip(v2, prev)]
            self._prev_counts[c] = list(v2)
            self._counts_today[c] += sum(delta)
            rows.append([f"{t2:.3f}", c] + list(v2) + delta + [round(agg, 1)])
        self._log_counter_rows(rows)
        return {c: self._counter_rates[c] for c in self.letters}

    # ---------------- raw stream primitives ----------------
    # Used ONLY after arming is complete: from the first raw send onward this session
    # is a one-way stream (command replies are drained and ignored), so no s.call()
    # may run on it again — request/reply would desync. Config goes through s.call()
    # strictly BEFORE the first tap; every cycle builds a fresh session.

    def _raw(self, dev, obj):
        dev.ws.send(json.dumps(obj))
        time.sleep(CMD_GAP_S)

    def _tap(self, dev, letter):
        v = self._SEC[letter].value
        self._raw(dev, {"command": "reset_channel", "callback": "s",
                        "params": {"section": v, "channel": 1}})
        self._raw(dev, {"command": "start_tt_data", "callback": "s",
                        "params": {"section": v}})

    def _untap(self, dev, letter):
        v = self._SEC[letter].value
        self._raw(dev, {"command": "stop_tt_data", "callback": "s",
                        "params": {"section": v}})

    def _drain(self, dev, letter, dwell_s):
        """Hold `letter`'s stream open for dwell_s, collecting (host_unix, channel,
        t_board_ns) tags. Publishes a state heartbeat every PUBLISH_EVERY_S so the
        GUI card never reads stale mid-dwell. Raises on socket/websocket errors
        (which v3 treats as fatal — no reconnects). recv timeouts are idle waits,
        NOT errors: a section whose rate spiked over the ceiling is simply silent."""
        dev.ws.settimeout(DRAIN_RECV_TIMEOUT_S)
        t_end = time.time() + dwell_s
        tags = []
        while time.time() < t_end and not self._stop.is_set():
            if time.time() - self._last_publish > PUBLISH_EVERY_S:
                self._publish()
            try:
                raw = dev.ws.recv()
            except _IDLE:
                continue
            now = time.time()
            try:
                pkt = json.loads(raw.replace(",]", "]"))
            except Exception:
                continue
            if pkt.get("command") != "send_data":
                continue  # replies to our own reset/start/stop sends
            for el in pkt.get("timetag_data", []):
                if isinstance(el, list) and len(el) >= 2:
                    tags.append((now, el[0], el[1]))
        return tags

    # ---------------- arming ----------------

    def _arm(self, s, eligible):
        """Set the gate-passing sections to Time-Tag through the session gateway
        (paced, breaker-guarded). MUST fully precede the first raw tap."""
        for c in eligible:
            sec = self._SEC[c]
            s.call("set_section_function", sec, self._TT)
            # Result:False on this firmware is cosmetic — the config applies.
            s.call("configure_time_tagging", sec, True, True, True, True, True, True)
        now = time.time()
        for c in eligible:
            self._last_tap_t[c] = now

    # ---------------- one section visit ----------------

    def _visit(self, dev, letter):
        """Tap one section, dwell on its stream, untap; dedup and return new rows."""
        self._tap(dev, letter)
        tags = self._drain(dev, letter, self.dwell_s)
        self._untap(dev, letter)

        now = time.time()
        rows = []
        seen = self._seen[letter]
        tmax = self._last_max_t[letter]
        for host_t, ch, t in tags:
            key = (ch, t)
            if key not in seen:
                seen[key] = None
                rows.append((f"{host_t:.3f}", letter, ch, t))
            if t > tmax:
                tmax = t
        self._last_max_t[letter] = tmax
        # prune dedup memory older than the horizon (safely > the inter-tap gap)
        horizon_s = max(120.0, 8.0 * self.dwell_s)
        cut = tmax - int(horizon_s * NS_PER_S)
        while seen:
            k = next(iter(seen))
            if k[1] < cut:
                seen.popitem(last=False)
            else:
                break

        # live edge rate over this dwell (EMA); no backlog assumption in v3
        inst = len(rows) / self.dwell_s if self.dwell_s > 0 else 0.0
        self._tt_rate_hz[letter] = 0.5 * self._tt_rate_hz[letter] + 0.5 * inst
        self._last_tap_t[letter] = now
        self._edges_today[letter] += len(rows)
        if rows:
            self._last_edge_t = now
        return rows

    # ---------------- CSV logging ----------------

    def _csv_path(self):
        day = datetime.now().strftime("%Y-%m-%d")
        return os.path.join(self.log_dir, f"n1081b_timetag_{day}.csv")

    def _counters_csv_path(self):
        day = datetime.now().strftime("%Y-%m-%d")
        return os.path.join(self.log_dir, f"n1081b_counters_{day}.csv")

    _CSV_FIELDS = ["host_unix", "section", "channel", "t_board_ns"]
    _COUNTER_CSV_FIELDS = ["host_unix", "section", "c0", "c1", "c2", "c3",
                           "d0", "d1", "d2", "d3", "agg_hz"]

    def _append_rows(self, path, fields, rows):
        try:
            os.makedirs(self.log_dir, exist_ok=True)
            new_file = not os.path.exists(path)
            with open(path, "a", newline="") as f:
                w = csv.writer(f)
                if new_file:
                    w.writerow(fields)
                w.writerows(rows)
        except Exception as e:
            self.log(f"CSV log failed ({os.path.basename(path)}): {e}")

    def _log_rows(self, rows):
        if rows:
            self._append_rows(self._csv_path(), self._CSV_FIELDS, rows)

    def _log_counter_rows(self, rows):
        if rows:
            self._append_rows(self._counters_csv_path(), self._COUNTER_CSV_FIELDS, rows)

    # ---------------- state file ----------------

    def log(self, msg):
        # Best-effort: a killed tmux pane makes stdout raise (EIO/BrokenPipe); swallow it
        # so a shutdown log line can never abort the counter-restore in the finally block.
        try:
            print(f"{datetime.now().strftime('%H:%M:%S')} [n1081b_timetag_watcher] {msg}",
                  flush=True)
        except Exception:
            pass

    def _roll_day(self):
        day = datetime.now().strftime("%Y-%m-%d")
        if day != self._today:
            self._today = day
            self._counts_today = {c: 0 for c in self.letters}
            self._edges_today = {c: 0 for c in self.tt_letters}

    def get_state(self):
        with self._state_lock:
            state = dict(self._state)
        state.setdefault("connected", self.connected)
        state["last_error"] = self.last_error
        state["alarm"] = self.alarm
        state["csv_path"] = self._csv_path()
        state["counters_csv_path"] = self._counters_csv_path()
        state["ip"] = self.ip
        state["sections"] = "".join(self.letters)
        state["tt_sections"] = "".join(self.tt_letters)
        state["dwell_s"] = self.dwell_s
        return state

    def _publish(self):
        now = time.time()
        state = {
            "connected": self.connected,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "ip": self.ip,
            "mode": "rate-gated TT + counter totals (v3)",
            "sections": "".join(self.letters),
            "tt_sections": "".join(self.tt_letters),
            # rate_hz = counter-measured input rates (works at any rate; this is
            # what the GUI card shows per section — the walls finally show numbers)
            "rate_hz": {c: (round(self._counter_rates[c], 1)
                            if self._counter_rates[c] is not None else None)
                        for c in self.letters},
            "rate_hz_total": round(sum(r for r in self._counter_rates.values()
                                       if r is not None), 1),
            # tt_rate_hz = live TT edge rates for the streamed sections only
            "tt_rate_hz": {c: round(self._tt_rate_hz[c], 1) for c in self.tt_letters},
            "tt_status": dict(self._tt_status),
            "silent_strikes": dict(self._silent_strikes),
            # total_today = counter deltas (A/B complete; C/D pause while streaming)
            "total_today": dict(self._counts_today),
            "total_today_all": sum(self._counts_today.values()),
            "edges_today": dict(self._edges_today),
            "edges_today_all": sum(self._edges_today.values()),
            "last_t_board_ns": dict(self._last_max_t),
            "cycles": self._cycles,
            "dwell_s": self.dwell_s,
            "gate_hz": self.gate_hz,
            "gate_period_s": self.gate_period_s,
            "beam_on": self._beam_on(),
            "last_edge_ago_s": (round(now - self._last_edge_t, 1)
                                if self._last_edge_t else None),
            "session_started": (datetime.fromtimestamp(self._session_started)
                                .isoformat(timespec="seconds")
                                if self._session_started else None),
            "alarm": self.alarm,
            "last_error": self.last_error,
        }
        with self._state_lock:
            self._state = state
        self._last_publish = now
        try:
            os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
            tmp = self.state_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.get_state(), f)
            os.replace(tmp, self.state_path)
        except Exception as e:
            self.log(f"state write failed: {e}")

    def _apply_config(self):
        """Pick up tunable changes from the config file (GUI-tunable)."""
        try:
            with open(self.config_path) as f:
                cfg = json.load(f)
        except (FileNotFoundError, ValueError, OSError):
            return
        d = clamp(cfg.get("dwell_s"), MIN_DWELL_S, MAX_DWELL_S, self.dwell_s)
        if d != self.dwell_s:
            self.log(f"dwell -> {d:.1f}s")
            self.dwell_s = d
        g = clamp(cfg.get("gate_hz"), MIN_GATE_HZ, MAX_GATE_HZ, self.gate_hz)
        if g != self.gate_hz:
            self.log(f"rate gate -> {g:.0f} Hz")
            self.gate_hz = g
        p = clamp(cfg.get("gate_period_s"), MIN_GATE_PERIOD_S, MAX_GATE_PERIOD_S,
                  self.gate_period_s)
        if p != self.gate_period_s:
            self.log(f"gate period -> {p:.0f}s")
            self.gate_period_s = p

    # ---------------- stream loop ----------------

    def _expired(self):
        return self.deadline is not None and time.time() >= self.deadline

    def _stream_until(self, s, eligible, t_end):
        """Rotate the gate-passing sections with dwells on an armed session until
        t_end. Returns 'stop' or 'period'; per-cycle edge counts land in
        edges_cycle. Socket/websocket errors propagate (fatal in v3)."""
        self._session_started = time.time()
        self._edges_cycle = {c: 0 for c in eligible}
        while time.time() < t_end:
            for c in eligible:
                if self._stop.is_set() or self._expired():
                    return "stop"
                rows = self._visit(s.dev, c)
                self._log_rows(rows)
                self._edges_cycle[c] += len(rows)
                self._publish()
                if time.time() >= t_end:
                    break
                self._stop.wait(SETTLE_S)
        return "period"

    def _run(self):
        """Cycle lifecycle: fresh session -> restore counters -> measure+log
        counters -> rate gate -> arm+stream the under-ceiling sections -> clean
        close -> idle out the period. ANY error stops the watcher (v3: reconnect
        budget zero — reconnect churn is what wedges boards)."""
        if N1081B is None or board_session is None:
            self.alarm = "n1081b_sdk / n1081b_session not importable"
            self._publish()
            return
        while not self._stop.is_set() and not self._expired():
            self._apply_config()
            self._roll_day()
            cycle_start = time.time()
            verdict = None
            eligible = []
            rates = {}
            phase = "connect"
            try:
                with board_session(self.ip, password=self.password,
                                   purpose="timetag watcher v3 (counters+gated TT .244)",
                                   timeout_s=8.0, connect_timeout_s=8.0) as s:
                    phase = "counter"
                    self._ensure_counters(s)
                    rates = self._measure_counters(s)
                    self.connected = True
                    self.last_error = None
                    eligible = [c for c in self.tt_letters
                                if rates.get(c) is not None and rates[c] <= self.gate_hz]
                    for c in self.tt_letters:
                        self._tt_status[c] = ("streaming" if c in eligible else
                                              f"gated-out ({rates.get(c, 0):.0f} Hz)")
                    self._publish()
                    self.log("cycle {}: rates {} -> streaming {}".format(
                        self._cycles + 1,
                        {c: round(r, 1) for c, r in rates.items()},
                        eligible or "none (all gated out)"))
                    if eligible:
                        phase = "arm"
                        self._arm(s, eligible)
                        phase = "stream"
                        verdict = self._stream_until(
                            s, eligible, cycle_start + self.gate_period_s)
            except BoardBusyError as e:
                self.alarm = f"board busy (another process holds .244): {e}"
            except BoardQuarantinedError as e:
                self.alarm = f"board quarantined: {e}"
            except BoardWedgedError as e:
                self.alarm = f"BOARD WEDGED — all contact stopped: {e}"
            except Exception as e:
                # v3 no-retry policy: any failure — login, counter s.call, arming,
                # or mid-stream socket error — stops the watcher. The 07-17 wedge
                # came from reconnecting onto a board whose sections had merely
                # gone over the TT rate ceiling; we never reconnect on error.
                self.alarm = f"{phase} error (no-retry policy, v3): {e!r}"

            if self.alarm:
                self.connected = False
                self.log(f"ALARM: {self.alarm}")
                self._publish()
                return
            if verdict == "stop" or self._stop.is_set() or self._expired():
                return

            # silent-despite-gate health check (module docstring §5)
            for c in eligible:
                if (getattr(self, "_edges_cycle", {}).get(c, 0) == 0
                        and rates.get(c, 0) >= MIN_EXPECT_HZ):
                    self._silent_strikes[c] += 1
                    self.log(f"section {c}: passed gate at {rates[c]:.0f} Hz but "
                             f"streamed 0 edges (strike {self._silent_strikes[c]}"
                             f"/{MAX_SILENT_STRIKES})")
                else:
                    self._silent_strikes[c] = 0
            bad = [c for c in self.tt_letters
                   if self._silent_strikes[c] >= MAX_SILENT_STRIKES]
            if bad:
                self.alarm = (f"section(s) {bad} under the rate gate yet silent for "
                              f"{MAX_SILENT_STRIKES} consecutive cycles — TT path "
                              "presumed unhealthy; stopping (do NOT restart blindly; "
                              "probe gently with tt_probe_v2.py)")
                self.connected = False
                self.log(f"ALARM: {self.alarm}")
                self._publish()
                return

            self._cycles += 1
            # idle out the rest of the period with the board untouched (session is
            # closed); keep the state-file heartbeat alive for the GUI.
            while (time.time() < cycle_start + self.gate_period_s
                   and not self._stop.is_set() and not self._expired()):
                if time.time() - self._last_publish > PUBLISH_EVERY_S:
                    self._publish()
                self._stop.wait(1.0)

    # ---------------- restore ----------------

    def restore_counters(self, attempts=2):
        """Return .244 to its steady state: all sections FN_COUNTER (lemo0-3, no gate)
        and counting, via a FRESH gateway session (the streaming socket is one-way by
        then). Verifies every section reads back 'counter'.

        NOTE: the board auto-reverts sections to 'wire' passthrough when a TT stream
        socket drops, so this MUST run on every clean exit; if the process is
        SIGKILLed it cannot, and .244 is left in 'wire' until `--restore` is rerun."""
        if N1081B is None or board_session is None:
            return False
        for attempt in range(1, attempts + 1):
            try:
                with board_session(self.ip, password=self.password,
                                   purpose="timetag watcher restore -> counters",
                                   timeout_s=8.0, connect_timeout_s=8.0) as s:
                    for c in self.letters:
                        sec = self._SEC[c]
                        s.call("set_section_function", sec, self._CNT)
                        s.call("configure_counter", sec, True, True, True, True, False)
                        for ch in range(4):
                            s.call("reset_channel", sec, ch, self._CNT)
                    names = [x["function_name"]
                             for x in s.call("get_sections_function")["data"]]
                ok = all(names[self._SEC[c].value] == "counter" for c in self.letters)
                if ok:
                    self.log(f"restored sections to counter: {names}")
                    return True
                self.log(f"restore attempt {attempt}: readback {names} "
                         f"not all counter; retrying")
            except (BoardQuarantinedError, BoardWedgedError) as e:
                self.log(f"restore blocked — board must rest ({e}); "
                         f"run --restore after it recovers")
                return False
            except Exception as e:
                self.log(f"restore attempt {attempt} failed ({e!r})")
            time.sleep(2.0)
        self.log("RESTORE FAILED after retries -- .244 may be left in wire/time_tag "
                 "mode; run `python n1081b_timetag_watcher.py --restore` when the "
                 "board is reachable")
        return False

    # ---------------- lifecycle ----------------

    def stop(self):
        self._stop.set()

    def run_blocking(self, duration_s=0):
        # Catch SIGHUP too: `tmux kill-session` (the GUI stop button + start_servers
        # path) delivers SIGHUP, and we MUST run the finally-block counter-restore on
        # it, else .244 is left in 'wire'. SIGINT/SIGTERM cover Ctrl-C and `kill`.
        for _sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            try:
                signal.signal(_sig, lambda *a: self._stop.set())
            except (ValueError, OSError):
                pass  # SIGHUP absent on non-POSIX; ignore
        if duration_s and duration_s > 0:
            self.deadline = time.time() + duration_s
        self.log(f"N1081B time-tag watcher v3 starting ({self.ip}: counters "
                 f"{self.letters}, TT candidates {self.tt_letters}, gate "
                 f"{self.gate_hz:.0f} Hz, cycle {self.gate_period_s / 60:.0f} min, "
                 f"dwell {self.dwell_s:.0f}s"
                 + (f", duration {duration_s:.0f}s" if self.deadline else "") + ")")
        try:
            self._run()
        finally:
            self.connected = False
            self.restore_counters()
            self._publish()
            self.log("N1081B time-tag watcher stopped"
                     + (f" -- ALARM: {self.alarm}" if self.alarm else ""))
