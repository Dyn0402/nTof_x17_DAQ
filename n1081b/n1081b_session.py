"""Mandatory session-hygiene gateway for ALL N1081B websocket control.

READ THIS BEFORE WRITING ANY SCRIPT THAT TALKS TO A BOARD.

Why this file exists (2026-07-15): the boards' embedded websocket server is an
abandoned C library (libwebsock/1.0.7) with a documented deadlock triggered by
clients that disconnect without a clean Close handshake. It has no server-side
keepalive, idle timeout, or dead-client reaping. Consequences, all reproduced on
hardware:

  * A handful of DIRTY mid-command disconnects (send a command, drop the socket
    without reading the reply / without a Close frame) wedges a board in SECONDS:
    logins start timing out, then the websocket upgrade itself stops answering.
    Recovery then takes HOURS of total isolation, or a physical reboot.
  * The vendor SDK's connect()/recv() have NO timeout -- a wedged board hangs the
    caller forever. This wrapper always sets one.
  * There is NO reliable remote reboot (apply_int_clk does not reboot; the GUI
    reboot needs the wedged websocket). Prevention is the only real defense.

This module is the ONE supported way to reach a board. It enforces:
  1. one persistent, timeout-bounded connection with a GUARANTEED clean close;
  2. an INTERPROCESS LOCK so two processes/agents can never talk to the same
     board at once (the top cause of accidental wedges);
  3. a QUARANTINE gate so a board that was just wedged is left alone to heal
     instead of being re-hammered by the next script/agent;
  4. command pacing, a single rested retry on timeout, and a circuit breaker
     that trips to BoardWedgedError (and quarantines the board) after repeated
     failures.

Usage (this is the whole API you need):

    from n1081b.n1081b_session import board_session, BoardWedgedError, BoardBusyError

    try:
        with board_session("192.168.10.242", purpose="restore SEC_A") as s:
            fns = s.call("get_sections_function")
            s.call("set_section_function", N1081B.Section.SEC_A,
                   N1081B.FunctionType.FN_AND)
            s.call("configure_and", N1081B.Section.SEC_A,
                   True, True, False, False, False, False, False, 0)
    except BoardBusyError as e:
        print("another process holds the board:", e)   # do NOT force past this
    except BoardWedgedError as e:
        print("board wedged -- leave it alone for hours:", e)

DO NOT `from n1081b_sdk import N1081B` and open your own connection in a control
script. DO NOT SIGKILL a process mid-session (the lock self-clears, but a killed
connection is a DIRTY disconnect that damages the board). Use s.call() for every
command so the breaker and pacing see it; s.dev is exposed only for read-only
exotica.
"""

import atexit
import errno
import fcntl
import json
import os
import socket
import time

from websocket import (
    create_connection,
    WebSocketTimeoutException,
    WebSocketException,
)

from n1081b_sdk import N1081B

# Shared runtime dir (absolute, cwd-independent) so every process/agent uses the
# SAME locks and quarantine regardless of where it runs from.
_ACCESS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "config", "n1081b_access")
_ACCESS_DIR = os.path.abspath(_ACCESS_DIR)
os.makedirs(_ACCESS_DIR, exist_ok=True)

# Default quarantine window after a wedge: leave the board fully alone this long.
QUARANTINE_S = 6 * 3600


class BoardBusyError(RuntimeError):
    """Another process already holds this board's lock. Do not force past it."""


class BoardWedgedError(RuntimeError):
    """Circuit breaker tripped / board unresponsive. Stop ALL contact for hours."""


class BoardQuarantinedError(RuntimeError):
    """Board is in its post-wedge rest window. Do not connect until it elapses."""


def _ip_tag(ip):
    return ip.replace(".", "_")


def _lock_path(ip):
    return os.path.join(_ACCESS_DIR, f"{_ip_tag(ip)}.lock")


def _holder_path(ip):
    return os.path.join(_ACCESS_DIR, f"{_ip_tag(ip)}.holder.json")


def _quarantine_path(ip):
    return os.path.join(_ACCESS_DIR, f"{_ip_tag(ip)}.quarantine.json")


# ---------------------------------------------------------------------------
# Quarantine helpers (module-level so tooling / the GUI can query & clear them)
# ---------------------------------------------------------------------------
def quarantine_status(ip):
    """Return the quarantine record dict if the board is still resting, else None."""
    path = _quarantine_path(ip)
    try:
        with open(path) as f:
            rec = json.load(f)
    except (OSError, ValueError):
        return None
    if time.time() >= rec.get("until", 0):
        return None
    return rec


def set_quarantine(ip, reason, window_s=QUARANTINE_S):
    now = time.time()
    rec = {"ip": ip, "reason": str(reason), "since": now, "until": now + window_s,
           "pid": os.getpid()}
    tmp = _quarantine_path(ip) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(rec, f, indent=2)
    os.replace(tmp, _quarantine_path(ip))
    return rec


def clear_quarantine(ip):
    """Manual override -- only after a board is verified healthy (e.g. post-reboot)."""
    try:
        os.remove(_quarantine_path(ip))
        return True
    except OSError:
        return False


class N1081BSession:
    """Context manager owning one locked, quarantine-aware, clean-closing connection."""

    def __init__(self, ip, password="password", timeout_s=10.0, connect_timeout_s=8.0,
                 min_gap_s=0.15, max_consecutive_timeouts=2, retry_rest_s=45.0,
                 require_login=True, purpose="", ignore_quarantine=False,
                 auto_quarantine=True):
        self.ip = ip
        self.password = password
        self.timeout_s = timeout_s
        self.connect_timeout_s = connect_timeout_s
        self.min_gap_s = min_gap_s
        self.max_consecutive_timeouts = max_consecutive_timeouts
        self.retry_rest_s = retry_rest_s
        self.require_login = require_login
        self.purpose = purpose
        self.ignore_quarantine = ignore_quarantine
        # auto_quarantine: on a breaker trip, WRITE a shared quarantine marker that
        # locks every process out of this board for QUARANTINE_S. Control/write
        # scripts want this (a wedge must isolate the board). A read-only, best-effort
        # telemetry caller (poll_modules) should set it False: it still gets
        # BoardWedgedError and its own breaker still trips (so IT stops touching the
        # board), but a mere snapshot read won't impose a 6 h lockout on a LIVE
        # trigger board from a transient blip. It always RESPECTS an existing
        # quarantine (that gate is separate; see ignore_quarantine).
        self.auto_quarantine = auto_quarantine
        self.dev = None
        self.login_ok = None   # set on connect: login() result (old-fw boards return False)
        self._lock_fd = None
        self._last_cmd_t = 0.0
        self._consecutive_timeouts = 0

    # -- lifecycle -----------------------------------------------------------
    def __enter__(self):
        # 1) quarantine gate
        if not self.ignore_quarantine:
            q = quarantine_status(self.ip)
            if q:
                mins = (q["until"] - time.time()) / 60.0
                raise BoardQuarantinedError(
                    f"{self.ip}: quarantined ({q['reason']}); "
                    f"{mins:.0f} min of rest left. Leave it alone or, if verified "
                    f"healthy, clear_quarantine('{self.ip}').")
        # 2) interprocess lock (non-blocking exclusive)
        self._acquire_lock()
        try:
            # 3) connect (with a real timeout on the handshake)
            self._connect()
        except Exception:
            self._release_lock()
            raise
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        self._release_lock()
        return False

    def _acquire_lock(self):
        fd = os.open(_lock_path(self.ip), os.O_RDWR | os.O_CREAT, 0o664)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            os.close(fd)
            if e.errno in (errno.EACCES, errno.EAGAIN):
                holder = self._read_holder()
                raise BoardBusyError(
                    f"{self.ip}: in use by {holder}. Only one process may talk to a "
                    f"board at a time -- wait for it to finish. (Do NOT bypass.)")
            raise
        self._lock_fd = fd
        self._write_holder()
        atexit.register(self._release_lock)

    def _release_lock(self):
        if self._lock_fd is not None:
            try:
                self._clear_holder()
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                os.close(self._lock_fd)
            except OSError:
                pass
            self._lock_fd = None

    def _read_holder(self):
        try:
            with open(_holder_path(self.ip)) as f:
                h = json.load(f)
            return f"pid {h.get('pid')} ({h.get('purpose') or '?'}) since {h.get('since_str')}"
        except (OSError, ValueError):
            return "another process"

    def _write_holder(self):
        rec = {"ip": self.ip, "pid": os.getpid(), "purpose": self.purpose,
               "since": time.time(), "since_str": time.strftime("%Y-%m-%d %H:%M:%S")}
        try:
            with open(_holder_path(self.ip), "w") as f:
                json.dump(rec, f, indent=2)
        except OSError:
            pass

    def _clear_holder(self):
        try:
            os.remove(_holder_path(self.ip))
        except OSError:
            pass

    def _connect(self):
        # Bounded websocket upgrade -- the vendor SDK's own connect() has NO timeout,
        # so a wedged board would hang us forever. Build the socket ourselves.
        url = f"ws://{self.ip}:8080/"
        try:
            ws = create_connection(url, timeout=self.connect_timeout_s)
        except (WebSocketException, OSError, socket.timeout) as e:
            self._consecutive_timeouts += 1
            self._maybe_quarantine(e)
            raise BoardWedgedError(
                f"{self.ip}: websocket upgrade failed/timed out ({e!r}). Board is "
                f"likely wedged -- stop all contact and let it rest.") from e
        self.dev = N1081B(self.ip)
        self.dev.ws = ws
        self.dev.ws.settimeout(self.timeout_s)
        ok = self.dev.login(self.password)
        self.login_ok = bool(ok)
        if not ok and self.require_login:
            self.close()
            raise ConnectionError(f"{self.ip}: login failed")

    def close(self):
        """Clean websocket close (sends the Close frame). Safe to call twice."""
        if self.dev is not None:
            try:
                self.dev.ws.close()
            except Exception:
                pass
            self.dev = None

    # -- command execution ---------------------------------------------------
    def call(self, method_name, *args, **kwargs):
        """Run an SDK method with pacing, one rested retry, and the breaker."""
        if self._consecutive_timeouts >= self.max_consecutive_timeouts:
            raise BoardWedgedError(f"{self.ip}: breaker tripped -- leave the board alone")

        gap = time.time() - self._last_cmd_t
        if gap < self.min_gap_s:
            time.sleep(self.min_gap_s - gap)

        try:
            result = self._invoke(method_name, *args, **kwargs)
            self._consecutive_timeouts = 0
            return result
        except (WebSocketTimeoutException, WebSocketException, ConnectionError,
                OSError) as first_err:
            self._consecutive_timeouts += 1
            self.close()
            if self._consecutive_timeouts >= self.max_consecutive_timeouts:
                self._maybe_quarantine(first_err)
                raise BoardWedgedError(
                    f"{self.ip}: {self._consecutive_timeouts} consecutive failures "
                    f"({first_err!r}) -- STOP all contact; board needs hours of rest."
                ) from first_err
            time.sleep(self.retry_rest_s)
            try:
                self._connect()
                result = self._invoke(method_name, *args, **kwargs)
                self._consecutive_timeouts = 0
                return result
            except (WebSocketTimeoutException, WebSocketException, ConnectionError,
                    OSError) as second_err:
                self._consecutive_timeouts = self.max_consecutive_timeouts
                self.close()
                self._maybe_quarantine(second_err)
                raise BoardWedgedError(
                    f"{self.ip}: retry after {self.retry_rest_s:.0f}s rest also failed "
                    f"({second_err!r}) -- STOP all contact; board needs hours of rest."
                ) from second_err

    def _invoke(self, method_name, *args, **kwargs):
        if self.dev is None:
            self._connect()
        self._last_cmd_t = time.time()
        result = getattr(self.dev, method_name)(*args, **kwargs)
        self._last_cmd_t = time.time()
        return result

    def _maybe_quarantine(self, err):
        if not self.auto_quarantine:
            return  # best-effort read-only caller: trip the breaker but don't lock out others
        try:
            set_quarantine(self.ip, f"wedge during '{self.purpose or 'session'}': {err!r}")
        except OSError:
            pass


def board_session(ip, **kwargs):
    """Preferred entry point. See module docstring."""
    return N1081BSession(ip, **kwargs)
