#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Read-only per-sub-run snapshot of the N1081B trigger modules.

Spawned as a background (daemon) thread from daq_control at the start of each
sub-run's data-taking. Read-only: it goes through the mandatory session gateway
(`n1081b/n1081b_session.py`) — one locked, quarantine-aware, clean-closing
connection per board — and only ever issues get_* commands. It never writes to a
board and never raises into the caller (a busy / quarantined / wedged /
unreachable board just records an error entry and the run continues untouched).

Session hygiene for this best-effort telemetry (see n1081b/CLAUDE.md):
  * `board_session(min_gap_s=0.0)` — reads are cheap (the web GUI polls ~4 Hz for
    days on one connection); this preserves the ~0.1 s bulk snapshot.
  * `auto_quarantine=False` — a snapshot that trips the breaker still stops
    touching THAT board (its own breaker latches + we skip it), but a mere
    telemetry read must never impose a 6 h shared quarantine on a LIVE trigger
    board. It still RESPECTS an existing quarantine (skips the board).
  * `require_login=False` — old-firmware boards (.245) serve get/* with login
    returning False; we record the real login result and read them anyway.
  * The interprocess lock is the point: it now serialises this snapshot against
    the scan controller and any ad-hoc board_session, so two processes can never
    hit one board at once. A board another process holds -> BoardBusyError -> we
    skip it this cycle (best-effort telemetry, not worth forcing).

Why a background thread: run_daq_controller() blocks for the whole sub-run, so we
fire the poll just before it and let the ~0.1 s read run in parallel while the
DAQ starts / the run proceeds. Nothing here talks to the DAQ, HV, or DREAM — the
only shared resource is the board net, and the read is independent per websocket.

Not clashing with n1081b_scan_watcher: the watcher changes board config at
sub-run BOUNDARIES and gates daq_control with `.pause_run` (set before it writes,
cleared after). We poll at DAQ-start, which daq_control only reaches once the
pause has cleared -> the watcher's write for this sub-run is already done. As a
belt-and-suspenders guard, poll_to_file() will also wait (bounded) if `.pause_run`
is present when it fires, so it never reads a board mid-apply. When the watcher
isn't running at all, config is static and we simply record whatever is live.

Scope is all six trigger boards (.240-.245), controlled by POLL_IPS. Do NOT poll a
board while mod5_timetag_logger is streaming it -- the board broadcasts send_data
to every client and would interleave/desync the reads (see the n1081b-sdk-gotchas
note); drop .244 from POLL_IPS if a time-tag run is planned concurrently.

Also runnable standalone for a one-off dump:
    .venv/bin/python n1081b/poll_modules.py out.json
"""
import json
import os
import subprocess
import threading
import time
from datetime import datetime

# The N1081B time-tag watcher owns .244 exclusively while it streams (the board
# broadcasts send_data to every websocket client, so a concurrent read desyncs).
# When its tmux session is alive we must NOT poll .244.
TT_WATCHER_SESSION = "n1081b_timetag_watcher"
TT_WATCHER_IP = "192.168.10.244"

# Modules to poll (all six trigger boards, .240-.245). This single list is the
# whole scope control. NOTE: do NOT poll .244 (Module 5) while
# mod5_timetag_logger.py is streaming it -- a streaming board broadcasts send_data
# to every websocket client and would interleave/desync the reads.
#
# TEMPORARY 2026-07-15: .244 is REMOVED because its command interface is WEDGED
# (websocket accepts connections but never answers login -- see
# n1081b/HANDOFF_2026-07-15_timetag_watcher_board_wedge.md). Polling it would waste
# ~6 s/sub-run on a dead login AND keep opening sessions to a board we want left
# alone to self-recover. RESTORE .244 to this tuple once it has been physically
# rebooted and verified reachable.
POLL_IPS = [f"192.168.10.{n}" for n in (240, 241, 242, 243, 244, 245)]  # .244 back 2026-07-16: touchscreen-rebooted, quarantine cleared, sections restored to counter + verified counting
PASSWORD = "password"
RECV_TIMEOUT = 6      # s, per-board socket timeout so one hung board can't stall the rest
# The N1081B has SIX LEMO inputs (0-5) but only FOUR outputs (0-3) per section
# (n1081b_module_map.py: `_in` up to range(1,6), `_out` only range(4)). Reading an
# out-of-range OUTPUT does not error — the board returns uninitialised junk, e.g.
# M6.C out4 mono_value 16843009 == 0x01010101. Using 6 for both (until 2026-07-22)
# put that junk in every archived snapshot and made it look like real config.
INPUT_RANGE = 6       # LEMO inputs 0-5
OUTPUT_RANGE = 4      # LEMO outputs 0-3


def _get(errs, label, s, method, *args):
    """Run one read-only getter through the session, capturing result or error.

    Ordinary getter failures (some getters misbehave on certain sections) are
    recorded and we continue. A BoardWedgedError from the session's breaker is a
    board-level signal, not a per-getter blip: let it propagate so _dump_board
    stops reading this board instead of hammering it getter after getter."""
    from n1081b_session import BoardWedgedError
    try:
        return s.call(method, *args)
    except BoardWedgedError:
        raise
    except Exception as e:  # noqa: BLE001 - record every failure, never raise
        errs[label] = repr(e)
        return None


def _dump_board(ip):
    """Read-only readback of one board through the mandatory session gateway.
    Mirrors dump_module_info.py's coverage. Never raises: a busy / quarantined /
    wedged / unreachable board just records an error entry and returns."""
    from n1081b_sdk import N1081B  # lazy: enum only; keeps import safe if SDK absent
    from n1081b_session import (board_session, BoardBusyError, BoardWedgedError,
                                BoardQuarantinedError)
    board = {"ip": ip, "errors": {}}
    errs = board["errors"]
    try:
        # See the module docstring for why these session knobs: min_gap_s=0.0 keeps
        # the fast bulk read, auto_quarantine=False protects the live trigger from a
        # telemetry-induced lockout, require_login=False snapshots old-fw boards.
        with board_session(ip, purpose="per-subrun snapshot", password=PASSWORD,
                           timeout_s=RECV_TIMEOUT, min_gap_s=0.0, retry_rest_s=10.0,
                           require_login=False, auto_quarantine=False) as s:
            board["login"] = s.login_ok
            board["version"] = _get(errs, "version", s, "get_version")
            board["ethernet"] = _get(errs, "ethernet", s, "get_ethernet_configuration")
            board["clock"] = _get(errs, "clock", s, "get_clock_status")
            board["sections_function"] = _get(errs, "sections_function", s, "get_sections_function")
            # Saved on-board config files (provenance) + LA trigger config. Both are
            # passive one-shot reads. NOT captured: get_logic_analyzer_data /
            # get_time_tag_data (live acquisition, not config; get_time_tag_data is a
            # bare recv() that blocks), get_function_file_list (blocks its recv() for
            # non-LUT/pattern/ToF sections), get_search_device_status (locate-blink alarm).
            board["config_file_list"] = _get(errs, "config_file_list", s, "get_configuration_file_list")
            board["logic_analyzer_trigger"] = _get(errs, "la_trigger", s, "get_logic_analyzer_trigger")

            board["sections"] = {}
            for sec in N1081B.Section:
                sd = {}
                sd["function_configuration"] = _get(errs, f"{sec.name}.fn_config", s, "get_function_configuration", sec)
                sd["function_results"] = _get(errs, f"{sec.name}.fn_results", s, "get_function_results", sec)
                sd["input_configuration"] = _get(errs, f"{sec.name}.input_config", s, "get_input_configuration", sec)
                sd["output_configuration"] = _get(errs, f"{sec.name}.output_config", s, "get_output_configuration", sec)
                sd["input_channels"] = {
                    ch: _get(errs, f"{sec.name}.in_ch{ch}", s, "get_input_channel_configuration", sec, ch)
                    for ch in range(INPUT_RANGE)
                }
                sd["output_channels"] = {
                    ch: _get(errs, f"{sec.name}.out_ch{ch}", s, "get_output_channel_configuration", sec, ch)
                    for ch in range(OUTPUT_RANGE)
                }
                board["sections"][sec.name] = sd
    except BoardBusyError as e:
        errs["busy"] = repr(e)          # another process holds it this cycle; skip
    except BoardQuarantinedError as e:
        errs["quarantined"] = repr(e)   # board resting; leave it alone
    except BoardWedgedError as e:
        errs["wedged"] = repr(e)        # breaker tripped mid-read; do NOT retry
    except Exception as e:  # noqa: BLE001
        errs["fatal"] = repr(e)
    return board


def _tt_watcher_running():
    """True if the time-tag watcher is actually STREAMING .244 (so we must not poll it).

    Both conditions are required: the tmux session must exist AND a supervisor process
    must be alive inside it. The session alone is not enough -- when the supervisor
    self-stops it leaves its pane sitting at an idle bash, and keying off the session
    name alone then silently dropped .244 from every per-sub-run snapshot for as long as
    that pane lived, so there was no wall-rate record at all and nothing said why
    (2026-07-30, HANDOFF_2026-07-30_tt_reboot_race.md §4.1).

    Fails SAFE: if the process check itself errors we assume it IS streaming and skip
    the board, because polling a board that is mid-stream is the harmful direction.
    """
    try:
        r = subprocess.run(["tmux", "has-session", "-t", TT_WATCHER_SESSION],
                           capture_output=True)
        if r.returncode != 0:
            return False
    except Exception:
        return False
    try:
        p = subprocess.run(["pgrep", "-f", "tt_stream_supervisor.py"],
                           capture_output=True)
        return bool(p.stdout.strip())
    except Exception:
        return True  # fail safe: assume streaming, skip the board


def _exclude_tt_watcher_board(ips, logger=None):
    """Drop .244 from the poll list while the time-tag watcher is streaming it."""
    if TT_WATCHER_IP in ips and _tt_watcher_running():
        if logger:
            logger(f"[n1081b] {TT_WATCHER_IP} owned by a live {TT_WATCHER_SESSION} "
                   f"supervisor; skipping it this poll")
        return [ip for ip in ips if ip != TT_WATCHER_IP]
    return ips


def poll_to_file(out_path, ips=None, label=None, logger=print,
                 settle_s=2.0, wait_flag=None, wait_flag_timeout_s=180.0):
    """Poll `ips` read-only and write the snapshot JSON to `out_path`.

    settle_s: brief pause before polling so config has settled into steady state.
    wait_flag: path to `.pause_run`; if present when we fire, wait (up to
        wait_flag_timeout_s) for it to clear so we never read a board while the
        scan watcher is mid-apply. Returns the path written, or None on failure.
    """
    ips = ips or POLL_IPS
    ips = _exclude_tt_watcher_board(ips, logger)
    if settle_s and settle_s > 0:
        time.sleep(settle_s)
    if wait_flag:
        waited = 0.0
        while os.path.exists(wait_flag) and waited < wait_flag_timeout_s:
            time.sleep(1.0)
            waited += 1.0
        if os.path.exists(wait_flag) and logger:
            logger(f"[n1081b] .pause_run still set after {wait_flag_timeout_s:.0f}s; polling anyway")

    result = {
        "polled_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "label": label,
        "boards": {ip: _dump_board(ip) for ip in ips},
    }
    try:
        with open(out_path, "w") as f:
            json.dump(result, f, indent=2, default=str)
    except Exception as e:  # noqa: BLE001
        if logger:
            logger(f"[n1081b] failed to write {out_path}: {e!r}")
        return None

    nerr = sum(len(b.get("errors", {})) for b in result["boards"].values())
    if logger:
        logger(f"[n1081b] wrote {out_path} ({len(ips)} board(s), {nerr} board-call error(s))")
    return out_path


def poll_in_background(out_path, **kwargs):
    """Fire-and-forget snapshot: returns immediately, never blocks or raises into
    the caller. All poll_to_file kwargs (ips, label, logger, settle_s, wait_flag)
    pass through."""
    logger = kwargs.get("logger", print)

    def _run():
        try:
            poll_to_file(out_path, **kwargs)
        except Exception as e:  # noqa: BLE001
            if logger:
                logger(f"[n1081b] background poll failed: {e!r}")

    t = threading.Thread(target=_run, name="n1081b-poll", daemon=True)
    t.start()
    return t


if __name__ == "__main__":
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else "n1081b_config.json"
    # standalone: poll now, no settle delay, no flag gating
    poll_to_file(out, settle_s=0.0)
