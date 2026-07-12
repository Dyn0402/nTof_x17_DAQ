#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Read-only per-sub-run snapshot of the N1081B trigger modules.

Spawned as a background (daemon) thread from daq_control at the start of each
sub-run's data-taking. Read-only: connect -> login -> get_* -> disconnect. It
never writes to a board and never raises into the caller (a busy or unreachable
board just records an error entry and the run continues untouched).

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
import threading
import time
from datetime import datetime

# Modules to poll (all six trigger boards, .240-.245). This single list is the
# whole scope control. NOTE: do NOT poll .244 (Module 5) while
# mod5_timetag_logger.py is streaming it -- a streaming board broadcasts send_data
# to every websocket client and would interleave/desync the reads.
POLL_IPS = [f"192.168.10.{n}" for n in (240, 241, 242, 243, 244, 245)]
PASSWORD = "password"
RECV_TIMEOUT = 6      # s, per-board socket timeout so one hung board can't stall the rest
SECTIONS_RANGE = 6    # LEMO inputs / outputs 0-5 per section


def _call(errs, label, fn, *args):
    """Run a get_* call, capturing either its result or the error string."""
    try:
        return fn(*args)
    except Exception as e:  # noqa: BLE001 - record every failure, never raise
        errs[label] = repr(e)
        return None


def _dump_board(ip):
    """Read-only readback of one board. Mirrors dump_module_info.py's coverage."""
    from n1081b_sdk import N1081B  # lazy: keeps import safe if the SDK is absent
    board = {"ip": ip, "errors": {}}
    dev = N1081B(ip)
    try:
        if not dev.connect():
            board["errors"]["connect"] = "connect() returned False"
            return board
        dev.ws.settimeout(RECV_TIMEOUT)
        board["login"] = bool(dev.login(PASSWORD))

        board["version"] = _call(board["errors"], "version", dev.get_version)
        board["ethernet"] = _call(board["errors"], "ethernet", dev.get_ethernet_configuration)
        board["clock"] = _call(board["errors"], "clock", dev.get_clock_status)
        board["sections_function"] = _call(board["errors"], "sections_function", dev.get_sections_function)
        # Saved on-board config files (provenance) + LA trigger config. Both are
        # passive one-shot reads. NOT captured: get_logic_analyzer_data /
        # get_time_tag_data (live acquisition, not config; get_time_tag_data is a
        # bare recv() that blocks), get_function_file_list (blocks its recv() for
        # non-LUT/pattern/ToF sections), get_search_device_status (locate-blink alarm).
        board["config_file_list"] = _call(board["errors"], "config_file_list", dev.get_configuration_file_list)
        board["logic_analyzer_trigger"] = _call(board["errors"], "la_trigger", dev.get_logic_analyzer_trigger)

        board["sections"] = {}
        for sec in N1081B.Section:
            s = {}
            s["function_configuration"] = _call(board["errors"], f"{sec.name}.fn_config", dev.get_function_configuration, sec)
            s["function_results"] = _call(board["errors"], f"{sec.name}.fn_results", dev.get_function_results, sec)
            s["input_configuration"] = _call(board["errors"], f"{sec.name}.input_config", dev.get_input_configuration, sec)
            s["output_configuration"] = _call(board["errors"], f"{sec.name}.output_config", dev.get_output_configuration, sec)
            s["input_channels"] = {
                ch: _call(board["errors"], f"{sec.name}.in_ch{ch}", dev.get_input_channel_configuration, sec, ch)
                for ch in range(SECTIONS_RANGE)
            }
            s["output_channels"] = {
                ch: _call(board["errors"], f"{sec.name}.out_ch{ch}", dev.get_output_channel_configuration, sec, ch)
                for ch in range(SECTIONS_RANGE)
            }
            board["sections"][sec.name] = s
    except Exception as e:  # noqa: BLE001
        board["errors"]["fatal"] = repr(e)
    finally:
        try:
            dev.disconnect()
        except Exception:
            pass
    return board


def poll_to_file(out_path, ips=None, label=None, logger=print,
                 settle_s=2.0, wait_flag=None, wait_flag_timeout_s=180.0):
    """Poll `ips` read-only and write the snapshot JSON to `out_path`.

    settle_s: brief pause before polling so config has settled into steady state.
    wait_flag: path to `.pause_run`; if present when we fire, wait (up to
        wait_flag_timeout_s) for it to clear so we never read a board while the
        scan watcher is mid-apply. Returns the path written, or None on failure.
    """
    ips = ips or POLL_IPS
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
