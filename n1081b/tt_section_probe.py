#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on July 17 2026
Created as nTof_x17_DAQ/n1081b/tt_section_probe.py

@author: Dylan Neff, dylan

Gentle per-section Time-Tag health probe for N1081B .244 (Module 5).

Context: .244 has a known PER-SECTION TT-wedge failure mode — a TT stream that
dies mid-operation leaves that section returning ZERO time tags forever (its
counters still work), and only a board reboot/power-cycle clears it (see
TIMETAG_MULTISECTION_2026-07-13.md §3 and TIMETAG_WATCHER.md). Found live on
2026-07-17: after the 07-16 touchscreen reboot, sections C/D streamed but A/B
stayed TT-wedged. This probe answers "which sections can stream?" with the
minimum possible board contact: one session, one short tap per section
(reset_channel + start_tt_data -> ~4 s drain -> stop_tt_data), then restores
every probed section to its counter steady state and verifies the readback.

Run it after any .244 reboot/power-cycle, and before re-enabling the time-tag
watcher on a section set:

    .venv/bin/python n1081b/tt_section_probe.py            # probe A B C D
    .venv/bin/python n1081b/tt_section_probe.py --sections AB --drain 6

Interpreting: tags>0 = section streams (healthy). tags=0 = either TT-wedged or
genuinely edge-free — check the section's counter deltas (printed at the end);
counting-but-silent = the per-section TT wedge.
"""
import argparse
import json
import socket
import sys
import time

sys.path.insert(0, __file__.rsplit("/", 2)[0])

from n1081b_sdk import N1081B
from n1081b.n1081b_session import board_session

try:
    from websocket import WebSocketTimeoutException
except Exception:
    class WebSocketTimeoutException(Exception):
        pass

IDLE = (socket.timeout, WebSocketTimeoutException)
SEC = {s.name[-1]: s for s in N1081B.Section}
TT = N1081B.FunctionType.FN_TIME_TAG
CNT = N1081B.FunctionType.FN_COUNTER
DEFAULT_IP = "192.168.10.244"


def raw(dev, obj):
    dev.ws.send(json.dumps(obj))
    time.sleep(0.12)


def drain(dev, seconds):
    dev.ws.settimeout(0.5)
    t_end = time.time() + seconds
    tags = 0
    t_min = t_max = None
    while time.time() < t_end:
        try:
            m = dev.ws.recv()
        except IDLE:
            continue
        try:
            pkt = json.loads(m.replace(",]", "]"))
        except Exception:
            continue
        if pkt.get("command") != "send_data":
            continue
        for el in pkt.get("timetag_data", []):
            if isinstance(el, list) and len(el) >= 2:
                tags += 1
                t = el[1]
                t_min = t if t_min is None or t < t_min else t_min
                t_max = t if t_max is None or t > t_max else t_max
    span = round((t_max - t_min) / 1e8, 1) if tags else None
    return tags, span


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ip", default=DEFAULT_IP)
    ap.add_argument("--password", default="password")
    ap.add_argument("--sections", default="ABCD", help="sections to probe (subset of ABCD)")
    ap.add_argument("--drain", type=float, default=4.0, help="seconds to listen per tap")
    args = ap.parse_args()
    letters = [c for c in "ABCD" if c in args.sections.upper()]

    results = {}
    with board_session(args.ip, password=args.password,
                       purpose=f"TT section probe {''.join(letters)}") as s:
        for c in letters:
            s.call("set_section_function", SEC[c], TT)
            # Result:False on this firmware is cosmetic — the config applies.
            s.call("configure_time_tagging", SEC[c], True, True, True, True, True, True)
        dev = s.dev
        # From the first raw send this session is one-way (no more s.call).
        for c in letters:
            v = SEC[c].value
            raw(dev, {"command": "reset_channel", "callback": "s",
                      "params": {"section": v, "channel": 1}})
            raw(dev, {"command": "start_tt_data", "callback": "s", "params": {"section": v}})
            tags, span = drain(dev, args.drain)
            raw(dev, {"command": "stop_tt_data", "callback": "s", "params": {"section": v}})
            drain(dev, 0.8)  # flush stragglers so they can't bleed into the next tap
            results[c] = (tags, span)
            print(f"section {c}: {tags} tags in {args.drain:.0f}s"
                  + (f" (t_board span {span}s incl. backlog)" if tags else "  <- SILENT"))

    print("restoring probed sections to counter...")
    with board_session(args.ip, password=args.password,
                       purpose="TT probe restore counters") as s:
        for c in letters:
            s.call("set_section_function", SEC[c], CNT)
            s.call("configure_counter", SEC[c], True, True, True, True, False)
            for ch in range(4):
                s.call("reset_channel", SEC[c], ch, CNT)
        names = [x["function_name"] for x in s.call("get_sections_function")["data"]]
        print("sections now:", names)
        time.sleep(3)
        deltas = {}
        for c in letters:
            vals = [x["value"] for x in
                    s.call("get_function_results", SEC[c])["data"]["counters"]]
            deltas[c] = vals
        print("counter values (3 s after reset; non-zero = counting):", deltas)

    silent = [c for c in letters if results[c][0] == 0]
    if silent:
        print(f"\nSILENT sections {silent}: if their counters count, their TT path is "
              f"wedged -> only a board reboot/power-cycle clears it "
              f"(TIMETAG_MULTISECTION_2026-07-13.md §3).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
