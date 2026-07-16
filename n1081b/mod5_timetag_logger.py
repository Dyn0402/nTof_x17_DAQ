#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stream N1081B time-tag timestamps from Module 5 (.244) to CSV.

Turns Module-5 sections into Time Tag mode and streams per-edge timestamps
(one CSV row per input edge: host receive time, section, panel channel 1-6,
board timestamp in ns). On exit (Ctrl-C / SIGTERM / --duration) the sections
are restored to the plain counter config (lemo 0-3, no gate).

Hardware facts this design rests on (measured 2026-07-10 on .244, fw 2025.3.27.0):

- ``configure_time_tagging`` returns ``Result: False`` on this firmware but the
  config APPLIES anyway — the return value is cosmetic. Ignore it.
- Time Tag covers all 6 lemos of a section (counters only cover lemo 0-3).
- Tag element = ``[channel, timestamp]`` with channel = PANEL number 1-6
  (= SDK lemo + 1) and timestamp in ns (10 ns granularity), from a
  free-running board clock that does NOT reset on reset_channel.
- ``send_data`` packets carry NO section id, and the board BROADCASTS every
  packet to every connected websocket client. Therefore:
    * only ONE section may stream at a time (else tags are unattributable) —
      "all 24 inputs" is only possible by cycling sections (``--section cycle``);
    * do NOT run any other SDK connection against the board while streaming
      (its replies will desync with broadcast packets queued in between).
- Sustained ~1 kHz aggregate verified fine; 10 kHz verified in the 2026-07-02
  veto test. Beyond that the websocket/JSON path is untested.

Usage (from mx17-daq, boards on the private net):

    .venv/bin/python n1081b/mod5_timetag_logger.py --section A -o walls.csv
    .venv/bin/python n1081b/mod5_timetag_logger.py --section cycle --dwell 10 \
        --duration 120 -o all_inputs.csv

CSV columns: host_unix (packet receive time — tags batch, so it repeats),
section (A-D), channel (panel 1-6), t_board_ns.
"""
import argparse
import csv
import json
import signal
import socket
import sys
import time

from n1081b_sdk import N1081B

try:
    from websocket import WebSocketTimeoutException
except Exception:  # pragma: no cover - websocket-client always present in venv
    class WebSocketTimeoutException(Exception):
        pass

IDLE = (socket.timeout, WebSocketTimeoutException)
SECTIONS = {s.name[-1]: s for s in N1081B.Section}   # 'A'->SEC_A ...
TT = N1081B.FunctionType.FN_TIME_TAG


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def connect(ip, password):
    dev = N1081B(ip)
    if not dev.connect():
        raise ConnectionError(f"connect() failed for {ip}")
    dev.ws.settimeout(6)
    if not dev.login(password):
        raise ConnectionError(f"login failed for {ip}")
    return dev


def start_stream(dev, sec):
    """Put one section in TT mode and start its stream (raw sends: the SDK's
    start_acquisition sends two commands but reads one reply, desyncing)."""
    dev.set_section_function(sec, TT)
    dev.configure_time_tagging(sec, True, True, True, True, True, True)  # Result:False is cosmetic
    dev.ws.send('{"command":"reset_channel", "callback":"start", "params":{"section":%d, "channel":1}}' % sec.value)
    dev.ws.send('{"command":"start_tt_data", "callback":"start", "params":{"section":%d}}' % sec.value)


def stop_stream(dev, sec):
    dev.ws.send('{"command":"stop_tt_data", "callback":"stop", "params":{"section":%d}}' % sec.value)


def drain(dev, writer, sec_letter, seconds, stats, stop_flag):
    """Read broadcast packets for `seconds`, writing tag rows."""
    dev.ws.settimeout(0.5)
    t_end = time.time() + seconds
    while time.time() < t_end and not stop_flag["stop"]:
        try:
            raw = dev.ws.recv()
        except IDLE:
            continue
        now = time.time()
        try:
            pkt = json.loads(raw.replace(",]", "]"))
        except Exception:
            continue
        if pkt.get("command") != "send_data":
            continue  # command replies from our own start/stop sends
        for el in pkt.get("timetag_data", []):
            writer.writerow((f"{now:.3f}", sec_letter, el[0], el[1]))
            stats[sec_letter] = stats.get(sec_letter, 0) + 1


def restore_counters(ip, password, letters):
    """Fresh connection (post-stream socket may be desynced) -> counter config."""
    dev = connect(ip, password)
    for letter in letters:
        sec = SECTIONS[letter]
        dev.set_section_function(sec, N1081B.FunctionType.FN_COUNTER)
        dev.configure_counter(sec, True, True, True, True, False)
    names = [x["function_name"] for x in dev.get_sections_function()["data"]]
    dev.disconnect()
    log(f"restored sections to counter: {names}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ip", default="192.168.10.244", help="board IP (default Module 5)")
    ap.add_argument("--password", default="password")
    ap.add_argument("--section", default="cycle", choices=[*"ABCD", "cycle"],
                    help="section to stream, or 'cycle' through all four")
    ap.add_argument("--dwell", type=float, default=10.0,
                    help="seconds per section in cycle mode (default 10)")
    ap.add_argument("--duration", type=float, default=0,
                    help="total seconds to run (0 = until Ctrl-C)")
    ap.add_argument("-o", "--output", default="-",
                    help="CSV path (append; '-' = stdout)")
    ap.add_argument("--no-restore", action="store_true",
                    help="leave sections in time_tag mode on exit")
    args = ap.parse_args()

    stop_flag = {"stop": False}
    signal.signal(signal.SIGTERM, lambda *_: stop_flag.update(stop=True))

    out = sys.stdout if args.output == "-" else open(args.output, "a", buffering=1)
    writer = csv.writer(out)
    if out is sys.stdout or out.tell() == 0:
        writer.writerow(("host_unix", "section", "channel", "t_board_ns"))

    letters = list("ABCD") if args.section == "cycle" else [args.section]
    t0 = time.time()
    t_stop = t0 + args.duration if args.duration > 0 else float("inf")
    stats = {}
    dev = connect(args.ip, args.password)
    log(f"connected to {args.ip}; streaming section(s) {letters}"
        + (f", dwell {args.dwell}s" if len(letters) > 1 else ""))
    try:
        while not stop_flag["stop"] and time.time() < t_stop:
            for letter in letters:
                if stop_flag["stop"] or time.time() >= t_stop:
                    break
                sec = SECTIONS[letter]
                dwell = args.dwell if len(letters) > 1 else 60.0
                dwell = max(0.0, min(dwell, t_stop - time.time()))
                try:
                    start_stream(dev, sec)
                    drain(dev, writer, letter, dwell, stats, stop_flag)
                    stop_stream(dev, sec)
                    time.sleep(0.2)   # let the last broadcasts arrive
                    drain(dev, writer, letter, 0.5, stats, stop_flag)
                except Exception as e:
                    if stop_flag["stop"]:
                        break
                    log(f"stream error on {letter}: {e!r}; reconnecting in 2 s")
                    try:
                        dev.disconnect()
                    except Exception:
                        pass
                    time.sleep(2)
                    dev = connect(args.ip, args.password)
                log(f"tags so far: { {k: stats.get(k, 0) for k in letters} }")
    except KeyboardInterrupt:
        pass
    finally:
        try:
            dev.disconnect()
        except Exception:
            pass
        if not args.no_restore:
            try:
                restore_counters(args.ip, args.password, letters)
            except Exception as e:
                log(f"RESTORE FAILED ({e!r}) — sections left in time_tag mode; "
                    f"rerun restore manually")
        if out is not sys.stdout:
            out.close()
        log(f"done; total tags {stats}")


if __name__ == "__main__":
    main()
