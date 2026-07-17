#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on July 14 2026, redesigned July 17 2026 (v2)
Created as nTof_x17_DAQ/n1081b/timetag_watcher_controller.py

@author: Dylan Neff, dylan

Always-on Time-Tag telemetry watcher for N1081B Module 5 (.244), which reads the
four scintillator-wall sections (A-D) as per-edge timestamps. Mirrors the gas /
3He / beam watcher split: one background process owns the board, polls
continuously, appends per-edge rows to a per-day CSV, and atomically publishes a
state file the Flask app reads. Read-only to the physics: it only reconfigures its
own board (.244), never the DAQ / HV / DREAM.

=== v2 REDESIGN (2026-07-17) — why v1 was retired =========================

v1 (persistent-FIFO round-robin, cycle_s=0) tapped every section every ~1.6 s:
reset+start_tt_data -> ~0.35 s drain -> stop_tt_data, back-to-back, ~11 TT
commands/s. After ~1 h (3257 cycles, mostly beam-off on empty buffers) the
board's TT engine stopped emitting and .244's command processor then wedged
entirely; recovery took a PHYSICAL reboot (2026-07-16). Full incident:
HANDOFF_2026-07-15_timetag_watcher_board_wedge.md. The single-section streamer
mod5_timetag_logger.py (long-held streams, few start/stops) has run at kHz for
long stretches without ever wedging — the damage came from the START/STOP CHURN,
not from TT streaming itself.

v2 therefore does everything the wedge handoff (§4) requires:

1. GENTLE CADENCE — long-dwell rotation. All sections are still armed to
   FN_TIME_TAG once (each buffers its own edges concurrently), but the watcher
   holds ONE section's stream open for dwell_s (default 12 s, clamp 5-60),
   draining continuously, before stopping it and moving to the next. That is
   ~3 stream commands per 12 s (~0.25 cmd/s) instead of ~11/s — the proven
   mod5_timetag_logger cycle cadence. Tapping a section dumps its buffered
   backlog first (reset_channel + start_tt_data), so the ~3-dwell gap while a
   section is untapped is recovered from the buffer.
   COMPLETENESS CAVEAT: the board buffer holds ~4000+ tags/section. At beam-on
   wall rates (~200 Hz/section) a ~40 s inter-tap gap can exceed that on a hot
   wall, dropping that gap's OLDEST edges. Accepted trade-off: this is telemetry
   for offline timestamp matching (rate structure), not a complete edge record.
2. HEALTH CHECK — if beam_state.json says beam-on and NO new edge arrives on any
   section for NO_EDGE_BEAMON_ALARM_S (5 min), the TT engine is presumed stalled
   (exactly how the 07-15 wedge began, silently): the watcher does ONE clean
   re-arm (close, rest, reconnect, re-arm). If a second full window passes with
   beam on and still zero edges, it STOPS with an alarm instead of hammering a
   sick board.
3. PERIODIC RE-ARM — every rearm_period_s (default 30 min) the session is closed
   CLEANLY, rested a few seconds, and rebuilt, bounding any board-side session
   state age.
4. STOP, DON'T HAMMER — session-hygiene gateway everywhere (n1081b_session:
   interprocess flock, quarantine gate, bounded timeouts, guaranteed clean
   close). BoardBusy / BoardQuarantined / BoardWedged / login failure -> publish
   an alarm in the state file and EXIT. Stream-error reconnects are budgeted
   (MAX_RECONNECTS_PER_HOUR); exceeding the budget also stops the watcher.

Acquisition facts this rests on (measured on .244, fw 2025.3.27.0 — see
TIMETAG_MULTISECTION_2026-07-13.md):
- Arm each section FN_TIME_TAG once; each accumulates edges into its own buffer.
- reset_channel + start_tt_data dumps a section's buffered backlog (reset does
  NOT clear it — it is the flush trigger); start_tt_data alone returns nothing.
- send_data packets carry NO section id and are broadcast to every client, so:
  tap ONE section at a time, and nothing else may hold a connection to .244
  while streaming (the session flock now enforces this; poll_modules also skips
  .244 whenever the watcher's tmux session is alive).
- Reads can overlap between taps -> dedup by (section, channel, t_board_ns) over
  a rolling board-clock horizon; every edge is written exactly once.
- configure_time_tagging returns Result:False on this firmware but applies.

TIMESTAMP MATCHING TO DREAM (offline): each row carries host_unix (packet
receive wall time — precise for live-streamed edges, ~tap-time for backlog
dumps) and t_board_ns (10 ns free-running board clock, common to all sections).
Coarse board->wall anchor from minimum-offset (host_unix, t_board_ns) pairs;
fine alignment by cross-correlating beam-spill rate structure. See
n1081b/TIMETAG_WATCHER.md.
"""

import csv
import json
import os
import signal
import socket
import threading
import time
from collections import OrderedDict, deque
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

NS_PER_S = 1e8               # board clock: 10 ns steps -> 1e8 ticks / second
DEFAULT_IP = "192.168.10.244"
DEFAULT_SECTIONS = "ABCD"

# --- cadence (the safety-critical knobs; see module docstring §1) ---
DEFAULT_DWELL_S = 12.0       # one section's stream held open this long per tap
MIN_DWELL_S = 5.0
MAX_DWELL_S = 60.0
SETTLE_S = 0.4               # pause between stopping one section and tapping the next
CMD_GAP_S = 0.12             # pacing between raw stream-command sends
DRAIN_RECV_TIMEOUT_S = 0.5   # ws recv timeout inside a dwell (idle wait, no board load)

# --- periodic re-arm (§3) ---
DEFAULT_REARM_PERIOD_S = 1800.0
MIN_REARM_PERIOD_S = 600.0
MAX_REARM_PERIOD_S = 7200.0
REARM_REST_S = 5.0           # rest between clean close and reconnect

# --- health / stop-don't-hammer (§2, §4) ---
NO_EDGE_BEAMON_ALARM_S = 300.0   # beam on + zero edges this long -> strike
MAX_HEALTH_STRIKES = 2           # strike 1 = one clean re-arm; strike 2 = stop + alarm
BEAM_STATE_FRESH_S = 120.0       # beam_state.json older than this counts as beam-unknown
RECONNECT_REST_S = 30.0          # rest before a stream-error reconnect
MAX_RECONNECTS_PER_HOUR = 4      # rolling budget; exceeded -> stop + alarm

PUBLISH_EVERY_S = 5.0        # state-file heartbeat, incl. mid-dwell


def clamp(v, lo, hi, default):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


class N1081BTimeTagController:
    """Owns .244 (through the session gateway) plus the long-dwell poll/log loop."""

    def __init__(self, ip=DEFAULT_IP, password="password", sections=DEFAULT_SECTIONS,
                 dwell_s=DEFAULT_DWELL_S, rearm_period_s=DEFAULT_REARM_PERIOD_S,
                 state_path=N1081B_TT_STATE_PATH, config_path=N1081B_TT_CONFIG_PATH,
                 log_dir=None):
        self.ip = ip
        self.password = password
        # 'A'->SEC_A ...; keep only the requested letters, in A..D order.
        self.letters = [c for c in "ABCD" if c in sections.upper()]
        self.dwell_s = clamp(dwell_s, MIN_DWELL_S, MAX_DWELL_S, DEFAULT_DWELL_S)
        self.rearm_period_s = clamp(rearm_period_s, MIN_REARM_PERIOD_S,
                                    MAX_REARM_PERIOD_S, DEFAULT_REARM_PERIOD_S)
        self.state_path = state_path
        self.config_path = config_path
        self.log_dir = log_dir or N1081B_TT_LOG_DIR

        self.connected = False
        self.last_error = None
        self.alarm = None            # set -> the watcher stopped itself; human needed
        self.deadline = None         # optional wall-clock stop (--duration)

        # dedup memory: per-section OrderedDict {(channel, t_board): None}, insertion
        # order ~ arrival, pruned by board-clock horizon (> the longest inter-tap gap).
        self._seen = {c: OrderedDict() for c in self.letters}
        self._last_max_t = {c: 0 for c in self.letters}
        self._last_tap_t = {c: None for c in self.letters}
        # rolling stats for the state file
        self._rate_hz = {c: 0.0 for c in self.letters}
        self._total_today = {c: 0 for c in self.letters}
        self._today = None
        self._cycles = 0             # completed full rotations over all sections
        self._session_started = None
        self._last_publish = 0.0
        self._last_edge_t = None     # wall time of the last NEW edge on any section
        self._beamon_silent_since = None
        self._health_strikes = 0
        self._reconnects = deque()   # wall times of stream-error reconnects (1 h window)

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

    # ---------------- beam state ----------------

    def _beam_on(self):
        """True only if beam_state.json says beam-on AND the file is fresh. Anything
        else (missing, stale, malformed) counts as beam-unknown -> False, so the
        zero-edge health alarm can never fire on a dead beam watcher."""
        try:
            with open(BEAM_STATE_PATH) as f:
                st = json.load(f)
            age = (datetime.now()
                   - datetime.fromisoformat(st["timestamp"])).total_seconds()
        except Exception:
            return False
        return bool(st.get("beam_on")) and 0 <= age < BEAM_STATE_FRESH_S

    # ---------------- raw stream primitives ----------------
    # Used ONLY after arming is complete: from the first raw send onward this session
    # is a one-way stream (command replies are drained and ignored), so no s.call()
    # may run on it again — request/reply would desync. Config goes through s.call()
    # strictly BEFORE the first tap; a fresh session is built for every re-arm.

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
        GUI card never reads stale mid-dwell. Raises on socket/websocket errors."""
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

    def _arm(self, s):
        """Set every requested section to Time-Tag through the session gateway
        (paced, breaker-guarded). MUST fully precede the first raw tap."""
        for c in self.letters:
            sec = self._SEC[c]
            s.call("set_section_function", sec, self._TT)
            # Result:False on this firmware is cosmetic — the config applies.
            s.call("configure_time_tagging", sec, True, True, True, True, True, True)
        now = time.time()
        for c in self.letters:
            if self._last_tap_t[c] is None:
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

        # rate over the full inter-tap period (backlog dump + live stream)
        span = now - (self._last_tap_t[letter] or now)
        inst = len(rows) / span if span > 0 else 0.0
        self._rate_hz[letter] = 0.5 * self._rate_hz[letter] + 0.5 * inst
        self._last_tap_t[letter] = now
        self._total_today[letter] += len(rows)
        if rows:
            self._last_edge_t = now
        return rows

    # ---------------- health (§2) ----------------

    def _health_check(self):
        """Return 'health' if the beam-on zero-edge alarm window elapsed, else None.
        Strike bookkeeping: edges anywhere reset the strikes."""
        now = time.time()
        if self._last_edge_t and now - self._last_edge_t < NO_EDGE_BEAMON_ALARM_S:
            self._health_strikes = 0
        if not self._beam_on():
            self._beamon_silent_since = None
            return None
        if self._last_edge_t and now - self._last_edge_t < NO_EDGE_BEAMON_ALARM_S:
            self._beamon_silent_since = None
            return None
        if self._beamon_silent_since is None:
            self._beamon_silent_since = now
            return None
        if now - self._beamon_silent_since >= NO_EDGE_BEAMON_ALARM_S:
            self._beamon_silent_since = None
            self._health_strikes += 1
            self.log(f"HEALTH: beam on but zero edges for "
                     f"{NO_EDGE_BEAMON_ALARM_S:.0f}s (strike {self._health_strikes}"
                     f"/{MAX_HEALTH_STRIKES})")
            return "health"
        return None

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
            print(f"{datetime.now().strftime('%H:%M:%S')} [n1081b_timetag_watcher] {msg}",
                  flush=True)
        except Exception:
            pass

    def _roll_day(self):
        day = datetime.now().strftime("%Y-%m-%d")
        if day != self._today:
            self._today = day
            self._total_today = {c: 0 for c in self.letters}

    def get_state(self):
        with self._state_lock:
            state = dict(self._state)
        state.setdefault("connected", self.connected)
        state["last_error"] = self.last_error
        state["alarm"] = self.alarm
        state["csv_path"] = self._csv_path()
        state["ip"] = self.ip
        state["sections"] = "".join(self.letters)
        state["dwell_s"] = self.dwell_s
        return state

    def _publish(self):
        now = time.time()
        state = {
            "connected": self.connected,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "ip": self.ip,
            "mode": "long-dwell rotation (v2)",
            "sections": "".join(self.letters),
            "rate_hz": {c: round(self._rate_hz[c], 1) for c in self.letters},
            "rate_hz_total": round(sum(self._rate_hz.values()), 1),
            "total_today": dict(self._total_today),
            "total_today_all": sum(self._total_today.values()),
            "last_t_board_ns": dict(self._last_max_t),
            "cycles": self._cycles,
            "dwell_s": self.dwell_s,
            "rearm_period_s": self.rearm_period_s,
            "beam_on": self._beam_on(),
            "last_edge_ago_s": (round(now - self._last_edge_t, 1)
                                if self._last_edge_t else None),
            "session_started": (datetime.fromtimestamp(self._session_started)
                                .isoformat(timespec="seconds")
                                if self._session_started else None),
            "reconnects_last_hour": len(self._reconnects),
            "health_strikes": self._health_strikes,
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
        """Pick up dwell / re-arm period changes from the config file (GUI-tunable)."""
        try:
            with open(self.config_path) as f:
                cfg = json.load(f)
        except (FileNotFoundError, ValueError, OSError):
            return
        d = clamp(cfg.get("dwell_s"), MIN_DWELL_S, MAX_DWELL_S, self.dwell_s)
        if d != self.dwell_s:
            self.log(f"dwell -> {d:.1f}s")
            self.dwell_s = d
        r = clamp(cfg.get("rearm_period_s"), MIN_REARM_PERIOD_S, MAX_REARM_PERIOD_S,
                  self.rearm_period_s)
        if r != self.rearm_period_s:
            self.log(f"re-arm period -> {r:.0f}s")
            self.rearm_period_s = r

    # ---------------- reconnect budget (§4) ----------------

    def _note_reconnect(self):
        now = time.time()
        self._reconnects.append(now)
        while self._reconnects and now - self._reconnects[0] > 3600.0:
            self._reconnects.popleft()
        return len(self._reconnects)

    # ---------------- stream loop ----------------

    def _expired(self):
        return self.deadline is not None and time.time() >= self.deadline

    def _stream_until_rearm(self, s):
        """Rotate sections with long dwells on an armed session. Returns 'stop',
        'rearm', or 'health'. Socket/websocket errors propagate to the caller."""
        self._session_started = time.time()
        while True:
            for c in self.letters:
                if self._stop.is_set() or self._expired():
                    return "stop"
                rows = self._visit(s.dev, c)
                self._log_rows(rows)
                self._publish()
                verdict = self._health_check()
                if verdict:
                    return verdict
                if time.time() - self._session_started >= self.rearm_period_s:
                    return "rearm"
                self._stop.wait(SETTLE_S)
            self._cycles += 1
            self._apply_config()
            self._roll_day()

    def _run(self):
        """Session lifecycle: connect+arm -> stream -> clean close, repeating only for
        the planned re-arm, a budgeted stream-error reconnect, or one health re-arm.
        Everything else stops the watcher (stop, don't hammer)."""
        if N1081B is None or board_session is None:
            self.alarm = "n1081b_sdk / n1081b_session not importable"
            self._publish()
            return
        while not self._stop.is_set() and not self._expired():
            self._apply_config()
            self._roll_day()
            reason = None
            armed_ok = False
            try:
                with board_session(self.ip, password=self.password,
                                   purpose="timetag watcher (streaming .244)",
                                   timeout_s=8.0, connect_timeout_s=8.0) as s:
                    self._arm(s)
                    armed_ok = True
                    self.connected = True
                    self.last_error = None
                    self._publish()
                    self.log(f"connected + armed sections {self.letters} on {self.ip} "
                             f"(dwell {self.dwell_s:.0f}s, re-arm every "
                             f"{self.rearm_period_s / 60:.0f} min)")
                    reason = self._stream_until_rearm(s)
            except BoardBusyError as e:
                self.alarm = f"board busy (another process holds .244): {e}"
            except BoardQuarantinedError as e:
                self.alarm = f"board quarantined: {e}"
            except BoardWedgedError as e:
                self.alarm = f"BOARD WEDGED — all contact stopped: {e}"
            except Exception as e:
                # ConnectionError before arming = the session's login failure ->
                # fatal. (Builtin ConnectionError also covers mid-stream socket
                # resets, so classify by phase, not type.)
                if isinstance(e, ConnectionError) and not armed_ok:
                    self.alarm = f"login/connect failed: {e}"
                    self.connected = False
                    self.log(f"ALARM: {self.alarm}")
                    self._publish()
                    return
                # stream/socket error mid-dwell: the with-block already closed cleanly
                self.connected = False
                self.last_error = f"stream error: {e!r}"
                n = self._note_reconnect()
                if n > MAX_RECONNECTS_PER_HOUR:
                    self.alarm = (f"{n} stream-error reconnects within an hour "
                                  f"(budget {MAX_RECONNECTS_PER_HOUR}) — stopping")
                else:
                    self.log(f"{self.last_error}; clean reconnect "
                             f"{n}/{MAX_RECONNECTS_PER_HOUR} in {RECONNECT_REST_S:.0f}s")
                    self._publish()
                    self._stop.wait(RECONNECT_REST_S)
                    continue

            self.connected = False
            if self.alarm:
                self.log(f"ALARM: {self.alarm}")
                self._publish()
                return
            if reason == "stop":
                return
            if reason == "health":
                if self._health_strikes >= MAX_HEALTH_STRIKES:
                    self.alarm = ("beam on but zero edges through "
                                  f"{MAX_HEALTH_STRIKES} windows incl. one re-arm — "
                                  "TT engine presumed stalled; stopping (do NOT "
                                  "restart blindly; check the board gently)")
                    self.log(f"ALARM: {self.alarm}")
                    self._publish()
                    return
                self.log("health strike: one clean re-arm after rest")
                self._publish()
                self._stop.wait(RECONNECT_REST_S)
                continue
            # reason == 'rearm' (or arm-time stop): planned clean rebuild
            self._publish()
            self._stop.wait(REARM_REST_S)

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
        self.log(f"N1081B time-tag watcher v2 starting ({self.ip}, sections "
                 f"{self.letters}, long-dwell rotation, dwell {self.dwell_s:.0f}s, "
                 f"re-arm {self.rearm_period_s / 60:.0f} min"
                 + (f", duration {duration_s:.0f}s" if self.deadline else "") + ")")
        try:
            self._run()
        finally:
            self.connected = False
            self.restore_counters()
            self._publish()
            self.log("N1081B time-tag watcher stopped"
                     + (f" -- ALARM: {self.alarm}" if self.alarm else ""))
