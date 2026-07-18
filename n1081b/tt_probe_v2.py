#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on July 17 2026
Created as nTof_x17_DAQ/n1081b/tt_probe_v2.py

@author: Dylan Neff, dylan

Time-Tag probe v2 for N1081B .244 — replaces the single-tap SILENT/STREAMS verdict
of tt_section_probe.py, which HANDOFF_2026-07-17_tt_probe_unreliable.md showed to be
non-reproducible.

VERDICT (2026-07-17, HANDOFF_2026-07-17_tt_rate_ceiling.md): the overflow-latch model
this probe was written to test was falsified, and the real mechanism found — a LIVE
RATE CEILING: a section yields zero TT tags whenever its input rate is above ~50-800 Hz
(bracketed) at stream-start, and streams fine below it. Section identity, history,
reboots, and pulse width are all irrelevant. INTERPRETATION RULE: read the section's
counter rate first; a silent verdict on a section above ~50 Hz aggregate is expected
and means NOTHING about board health.

Per section, one at a time (others untouched), four taps that differ only in buffer age:
  tap0 IMMEDIATE — arm and tap within ~0.2 s (buffer nearly empty; B marginal)
  tap1 DELAYED   — after --gap s armed+untapped (A/B overflowed, C/D not)
  tap2 DOUBLE    — ~0.8 s after tap1 stops (buffer young again IF tap1 cleared it)
  tap3 CYCLE     — function cycled counter->TT, then instant tap (does cycling reset
                   the buffer/latch?)
Each tap reports: total tags, per-channel counts, tags in the first 1 s (backlog
burst) vs after (live), t_board spans of each, and an estimated live rate — enough to
tell a backlog dump from a live stream from silence.

Session hygiene: one board_session; arming/tap commands are raw one-way sends after
login (same pattern as tt_section_probe.py / the v2 watcher); a FRESH session at the
end restores all probed sections to counter and verifies counting.

    .venv/bin/python n1081b/tt_probe_v2.py                 # sections C A B D
    .venv/bin/python n1081b/tt_probe_v2.py --sections A --gap 15 --drain 6
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


def raw(dev, obj, settle=0.12):
    dev.ws.send(json.dumps(obj))
    if settle:
        time.sleep(settle)


ARM_SETTLE = 0.25   # s after select_section_function (--fast: 0.06, just past the
RESET_SETTLE = 0.05  # ~46 ms FPGA reconfig; beats a 19 kHz section's 0.21 s overflow)


def set_function_raw(dev, letter, fn):
    raw(dev, {"command": "select_section_function", "callback": "set_fn",
              "params": {"section": SEC[letter].value, "function": fn.value}},
        settle=ARM_SETTLE)


def flush(dev, seconds=0.8):
    dev.ws.settimeout(0.3)
    t_end = time.time() + seconds
    while time.time() < t_end:
        try:
            dev.ws.recv()
        except IDLE:
            continue
        except Exception:
            break


def tap(dev, letter, drain_s, label):
    """reset+start -> drain -> stop. Returns a dict of classification stats."""
    v = SEC[letter].value
    raw(dev, {"command": "reset_channel", "callback": "s",
              "params": {"section": v, "channel": 1}}, settle=RESET_SETTLE)
    t_start = time.time()
    raw(dev, {"command": "start_tt_data", "callback": "s",
              "params": {"section": v}}, settle=0.0)
    tags = []  # (host_dt, channel, t_board_ns)
    dev.ws.settimeout(0.4)
    t_end = t_start + drain_s
    while time.time() < t_end:
        try:
            m = dev.ws.recv()
        except IDLE:
            continue
        except Exception:
            break
        try:
            pkt = json.loads(m.replace(",]", "]"))
        except Exception:
            continue
        if pkt.get("command") != "send_data":
            continue
        now_dt = time.time() - t_start
        for el in pkt.get("timetag_data", []):
            if isinstance(el, list) and len(el) >= 2:
                tags.append((now_dt, el[0], el[1]))
    raw(dev, {"command": "stop_tt_data", "callback": "s",
              "params": {"section": v}}, settle=0.0)
    flush(dev, 0.8)

    burst = [t for t in tags if t[0] <= 1.0]
    late = [t for t in tags if t[0] > 1.0]
    chans = {}
    for _, ch, _ in tags:
        chans[ch] = chans.get(ch, 0) + 1

    def span(rows):
        if not rows:
            return None
        tb = [r[2] for r in rows]
        return round((max(tb) - min(tb)) / 1e9, 1)  # board TT clock: 1 ns units (2026-07-18)

    live_hz = round(len(late) / max(drain_s - 1.0, 0.1), 1) if late else 0.0
    r = {"label": label, "n": len(tags), "burst_1s": len(burst), "late": len(late),
         "span_all_s": span(tags), "span_late_s": span(late),
         "live_hz_est": live_hz, "channels": chans}
    print(f"    {label:9s}: {r['n']:6d} tags | first-1s {r['burst_1s']:5d} "
          f"(span {r['span_all_s']}s) | after-1s {r['late']:5d} "
          f"(~{live_hz} Hz live) | ch {chans}")
    return r


def probe_section(dev, letter, gap_s, drain_s):
    print(f"  section {letter}: arming TT (immediate tap follows)")
    results = []
    # tap0 IMMEDIATE: arm -> tap with minimum latency (skip configure_time_tagging,
    # a no-op on this firmware per TIMETAG_MULTISECTION_2026-07-13.md §1c)
    set_function_raw(dev, letter, TT)
    results.append(tap(dev, letter, drain_s, "IMMEDIATE"))
    # tap1 DELAYED: sit armed+untapped for gap_s (A/B overflow, C/D don't)
    print(f"    (waiting {gap_s:.0f}s armed+untapped)")
    time.sleep(gap_s)
    results.append(tap(dev, letter, drain_s, "DELAYED"))
    # tap2 DOUBLE: young buffer IF tap1 drained/cleared it
    time.sleep(0.8)
    results.append(tap(dev, letter, drain_s, "DOUBLE"))
    # tap3 CYCLE: function cycle then instant tap
    set_function_raw(dev, letter, CNT)
    set_function_raw(dev, letter, TT)
    results.append(tap(dev, letter, drain_s, "CYCLE"))
    # leave the section back on counter (full config + reset in the restore session)
    set_function_raw(dev, letter, CNT)
    flush(dev, 0.5)
    return results


def main():
    ap = argparse.ArgumentParser(description="TT probe v2 (backlog/live/overflow aware)")
    ap.add_argument("--ip", default=DEFAULT_IP)
    ap.add_argument("--password", default="password")
    ap.add_argument("--sections", default="CABD",
                    help="sections to probe, in order (subset/permutation of ABCD)")
    ap.add_argument("--gap", type=float, default=15.0,
                    help="armed-untapped seconds before the DELAYED tap")
    ap.add_argument("--drain", type=float, default=6.0, help="seconds per tap drain")
    ap.add_argument("--fast", action="store_true",
                    help="minimum arm->tap latency (~0.1 s) to beat a kHz section's "
                         "buffer overflow on the IMMEDIATE tap")
    args = ap.parse_args()
    if args.fast:
        global ARM_SETTLE, RESET_SETTLE
        ARM_SETTLE, RESET_SETTLE = 0.06, 0.02
    letters = [c for c in args.sections.upper() if c in "ABCD"]

    all_results = {}
    with board_session(args.ip, password=args.password,
                       purpose=f"TT probe v2 {''.join(letters)}") as s:
        dev = s.dev  # raw one-way from here on; no more s.call on this session
        for c in letters:
            all_results[c] = probe_section(dev, c, args.gap, args.drain)

    print("restoring probed sections to counter (fresh session)...")
    with board_session(args.ip, password=args.password,
                       purpose="TT probe v2 restore counters") as s:
        for c in letters:
            s.call("set_section_function", SEC[c], CNT)
            s.call("configure_counter", SEC[c], True, True, True, True, False)
            for ch in range(4):
                s.call("reset_channel", SEC[c], ch, CNT)
        names = [x["function_name"] for x in s.call("get_sections_function")["data"]]
        print("sections now:", names)
        time.sleep(3)
        for c in letters:
            vals = [x["value"] for x in
                    s.call("get_function_results", SEC[c])["data"]["counters"]]
            print(f"  {c} counters 3s after reset (non-zero = counting): {vals}")

    print("\nsummary (n tags per tap):")
    for c in letters:
        print(f"  {c}: " + "  ".join(f"{r['label']}={r['n']}" for r in all_results[c]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
