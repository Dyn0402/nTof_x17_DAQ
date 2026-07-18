#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on July 17 2026
Created as nTof_x17_DAQ/n1081b/tt_stream_qualify.py

@author: Dylan Neff, dylan

Single-section CONTINUOUS-stream qualification for N1081B .244 (Module 5) —
answers the questions that decide whether we can log every trigger faithfully
for DREAM event <-> trigger matching (HANDOFF_2026-07-17_tt_rate_ceiling.md §2):

  Q1  Does one long-held stream deliver continuously (no rotation, no gaps)?
  Q2  Does an ALREADY-RUNNING stream survive input-rate excursions above the
      stream-START ceiling (~50-800 Hz)?  Jul-11 precedent: section A sustained
      2.5 kHz continuously — but stream-starts above the ceiling yield silence.
  Q3  When delivery pauses, is the data lost or buffered-then-dumped?

Design: ONE session, THREE stream commands total for the whole run (the proven
mod5_timetag_logger cadence — no start/stop churn):

  1. s.call phase: verify/restore the target section to counter, sample its
     per-channel rates for --pre seconds (the "input truth" baseline).
  2. Arm to FN_TIME_TAG (s.call), then go raw one-way: reset_channel +
     start_tt_data ONCE, drain continuously for --duration, stop_tt_data ONCE.
     Edges are flushed to CSV as they arrive; a status line + stats JSON update
     every 10 s. Delivery gaps > --gap-thresh s are recorded with both the host
     gap and the board-clock gap across them (see interpretation below).
  3. Fresh session: restore the section to counter (verified), sample rates for
     --post seconds. Final report compares streamed edges/channel to the
     counter-implied expectation.

Interpreting a recorded gap (host_gap vs tboard_gap of the surrounding edges):
  - tboard_gap ~= host_gap      -> the INPUT was quiet (no loss proven).
  - tboard_gap << host_gap and edges arrive in a burst afterwards -> delivery
    stalled but the board buffered (backlog regime) — data late, not lost.
  - tboard_gap ~= host_gap but pre/post counters say the input was live at
    rate >> observed -> tags were DROPPED (the disqualifying outcome).
Rate comparison caveat: beam structure makes counter-implied expectations
rough; judge per-channel ratios over long windows, not short ones.

Section map (VERIFIED 2026-07-16, n1081b_module_map.py): C = sector1-4
coincidences (M3); D = Singles, Doubles, gated pulser (M4.C), MASTER TRIGGER
(M4.D). Counter index i corresponds to front-panel input [1,2,4,5][i], and TT
'channel' is the true panel number — so counter[i] <-> TT channel [1,2,4,5][i].
NOTE: per-channel TT masking is IMPOSSIBLE (TIMETAG_MULTISECTION §1c — the
enable mask is ignored); a section always streams every cabled channel, so the
section's AGGREGATE rate is what matters.

Session hygiene: everything via board_session (flock, quarantine gate, clean
close); zero reconnects — ANY error ends the run (clean close + restore
attempt) and is recorded in the stats JSON. Check the Trigger tab's Board
Access card (or config/n1081b_access/) before launching; do NOT run while
rate_scan_2d or the timetag watcher holds .244.

    .venv/bin/python n1081b/tt_stream_qualify.py --section D --duration 900
    .venv/bin/python n1081b/tt_stream_qualify.py --section C --duration 3600 \\
        --label secC_1h_beamon
"""
import argparse
import csv
import json
import os
import signal
import socket
import sys
import time
from datetime import datetime

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
PANEL_OF_INPUT = [1, 2, 4, 5]      # counter index i <-> front-panel/TT channel
OUT_BASE = os.path.expanduser("~/beam_july/test/tt_stream_qualify")

CMD_GAP_S = 0.12
DRAIN_RECV_TIMEOUT_S = 0.5
STATUS_EVERY_S = 10.0
CSV_FLUSH_EVERY_S = 5.0
COUNTER_READ_EVERY_S = 2.0

_stop = {"flag": False}


def _sig(*_a):
    _stop["flag"] = True


def log(msg):
    try:
        print(f"{datetime.now().strftime('%H:%M:%S')} [tt_stream_qualify] {msg}",
              flush=True)
    except Exception:
        pass


def raw(dev, obj, settle=CMD_GAP_S):
    dev.ws.send(json.dumps(obj))
    if settle:
        time.sleep(settle)


def read_counters(s, letter):
    vals = [x["value"] for x in
            s.call("get_function_results", SEC[letter])["data"]["counters"]]
    return time.time(), vals


def counter_series(s, letter, seconds, csv_writer, phase):
    """Sample the section's free-running counters every ~2 s for `seconds`;
    returns per-channel mean rates (Hz, by counter index)."""
    t0, v0 = read_counters(s, letter)
    csv_writer.writerow([f"{t0:.3f}", phase] + list(v0))
    first_t, first_v = t0, v0
    t_end = t0 + seconds
    while time.time() < t_end and not _stop["flag"]:
        time.sleep(COUNTER_READ_EVERY_S)
        t1, v1 = read_counters(s, letter)
        csv_writer.writerow([f"{t1:.3f}", phase] + list(v1))
        t0, v0 = t1, v1
    span = max(t0 - first_t, 0.1)
    # free-running counters: a shrink means someone reset them; rate then unknown
    rates = [((b - a) / span if b >= a else float("nan"))
             for a, b in zip(first_v, v0)]
    return rates


def ensure_counter(s, letter):
    names = [x["function_name"] for x in s.call("get_sections_function")["data"]]
    if names[SEC[letter].value] != "counter":
        s.call("set_section_function", SEC[letter], CNT)
        s.call("configure_counter", SEC[letter], True, True, True, True, False)
        for ch in range(4):
            s.call("reset_channel", SEC[letter], ch, CNT)
        time.sleep(0.5)


def restore_counter(ip, password, letter):
    try:
        with board_session(ip, password=password,
                           purpose=f"tt_stream_qualify restore {letter}") as s:
            s.call("set_section_function", SEC[letter], CNT)
            s.call("configure_counter", SEC[letter], True, True, True, True, False)
            for ch in range(4):
                s.call("reset_channel", SEC[letter], ch, CNT)
            names = [x["function_name"]
                     for x in s.call("get_sections_function")["data"]]
        ok = names[SEC[letter].value] == "counter"
        log(f"restore {letter} -> counter: {'OK' if ok else f'FAILED ({names})'}")
        return ok, s
    except Exception as e:
        log(f"RESTORE FAILED ({e!r}) — rerun: python n1081b_timetag_watcher.py --restore")
        return False, None


def main():
    ap = argparse.ArgumentParser(
        description="Continuous single-section TT stream qualification (.244)")
    ap.add_argument("--ip", default=DEFAULT_IP)
    ap.add_argument("--password", default="password")
    ap.add_argument("--section", default="D", choices=list("ABCD"),
                    help="section to stream (D = trigger taps incl. master trigger; "
                         "C = sector coincidences)")
    ap.add_argument("--duration", type=float, default=900.0,
                    help="continuous stream seconds (default 900; hard cap 6 h)")
    ap.add_argument("--pre", type=float, default=30.0,
                    help="seconds of counter-rate baseline before streaming")
    ap.add_argument("--post", type=float, default=30.0,
                    help="seconds of counter-rate baseline after streaming")
    ap.add_argument("--gap-thresh", type=float, default=2.0,
                    help="record delivery gaps longer than this many seconds")
    ap.add_argument("--label", default=None,
                    help="output subdir name (default sec<X>_<YYYYmmdd_HHMMSS>)")
    args = ap.parse_args()
    letter = args.section.upper()
    duration = min(args.duration, 6 * 3600.0)

    for sg in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(sg, _sig)
        except (ValueError, OSError):
            pass

    label = args.label or f"sec{letter}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    out_dir = os.path.join(OUT_BASE, label)
    os.makedirs(out_dir, exist_ok=True)
    edges_path = os.path.join(out_dir, "edges.csv")
    counters_path = os.path.join(out_dir, "counters.csv")
    stats_path = os.path.join(out_dir, "stats.json")
    log(f"section {letter}, stream {duration:.0f}s, output {out_dir}")

    stats = {
        "section": letter, "ip": args.ip, "duration_s": duration,
        "started": datetime.now().isoformat(timespec="seconds"),
        "pre_rates_hz": None, "post_rates_hz": None,
        "panel_of_input": PANEL_OF_INPUT,
        "edges_total": 0, "edges_by_channel": {}, "packets": 0,
        "gaps": [], "max_packet_gap_s": 0.0,
        "alarm": None, "finished": None, "restored": None,
    }

    def save_stats():
        tmp = stats_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(stats, f, indent=1)
        os.replace(tmp, stats_path)

    counters_f = open(counters_path, "w", newline="")
    counters_w = csv.writer(counters_f)
    counters_w.writerow(["host_unix", "phase", "c0", "c1", "c2", "c3"])
    edges_f = open(edges_path, "w", newline="")
    edges_w = csv.writer(edges_f)
    edges_w.writerow(["host_unix", "channel", "t_board_ns"])

    by_ch = {}
    last_edge = {"host": None, "tboard": None}
    t_stream0 = None
    t_stream_end = None

    try:
        with board_session(args.ip, password=args.password,
                           purpose=f"tt_stream_qualify sec {letter}",
                           timeout_s=8.0, connect_timeout_s=8.0) as s:
            # --- phase 1: baseline (s.call) ---
            ensure_counter(s, letter)
            log(f"pre-stream counter baseline ({args.pre:.0f}s)...")
            stats["pre_rates_hz"] = counter_series(s, letter, args.pre,
                                                   counters_w, "pre")
            counters_f.flush()
            log("pre rates (Hz, by counter idx -> panel "
                f"{PANEL_OF_INPUT}): {[round(r, 1) for r in stats['pre_rates_hz']]}")
            save_stats()
            if _stop["flag"]:
                raise KeyboardInterrupt

            # --- phase 2: arm + one continuous stream (raw one-way from here) ---
            s.call("set_section_function", SEC[letter], TT)
            dev = s.dev
            v = SEC[letter].value
            raw(dev, {"command": "reset_channel", "callback": "s",
                      "params": {"section": v, "channel": 1}})
            t_stream0 = time.time()
            raw(dev, {"command": "start_tt_data", "callback": "s",
                      "params": {"section": v}}, settle=0.0)
            log(f"stream OPEN on {letter} — holding {duration:.0f}s "
                f"(3 stream commands total for this run)")
            dev.ws.settimeout(DRAIN_RECV_TIMEOUT_S)

            t_end = t_stream0 + duration
            last_pkt_t = t_stream0
            last_status = last_flush = t_stream0
            while time.time() < t_end and not _stop["flag"]:
                try:
                    m = dev.ws.recv()
                except IDLE:
                    now = time.time()
                    if now - last_status > STATUS_EVERY_S:
                        quiet = now - last_pkt_t
                        log(f"  t+{now - t_stream0:7.0f}s edges={stats['edges_total']:8d} "
                            f"quiet {quiet:5.1f}s  by_ch {by_ch}")
                        stats["max_packet_gap_s"] = max(stats["max_packet_gap_s"],
                                                        round(quiet, 1))
                        save_stats()
                        last_status = now
                    continue
                now = time.time()
                try:
                    pkt = json.loads(m.replace(",]", "]"))
                except Exception:
                    continue
                if pkt.get("command") != "send_data":
                    continue
                stats["packets"] += 1
                gap = now - last_pkt_t
                if gap > args.gap_thresh and stats["packets"] > 1:
                    first = next((el for el in pkt.get("timetag_data", [])
                                  if isinstance(el, list) and len(el) >= 2), None)
                    rec = {"t_wall": datetime.fromtimestamp(last_pkt_t)
                           .isoformat(timespec="seconds"),
                           "host_gap_s": round(gap, 2),
                           "tboard_gap_s": (round((first[1] - last_edge["tboard"]) / 1e9, 2)
                                            if first and last_edge["tboard"] else None)}
                    stats["gaps"].append(rec)
                    log(f"  GAP {rec}")
                stats["max_packet_gap_s"] = max(stats["max_packet_gap_s"],
                                                round(gap, 1))
                last_pkt_t = now
                for el in pkt.get("timetag_data", []):
                    if isinstance(el, list) and len(el) >= 2:
                        ch, tb = el[0], el[1]
                        edges_w.writerow([f"{now:.3f}", ch, tb])
                        by_ch[ch] = by_ch.get(ch, 0) + 1
                        stats["edges_total"] += 1
                        last_edge["host"], last_edge["tboard"] = now, tb
                if now - last_flush > CSV_FLUSH_EVERY_S:
                    edges_f.flush()
                    last_flush = now
                if now - last_status > STATUS_EVERY_S:
                    span = now - t_stream0
                    log(f"  t+{span:7.0f}s edges={stats['edges_total']:8d} "
                        f"(~{stats['edges_total'] / max(span, 1):.1f} Hz avg)  by_ch {by_ch}")
                    stats["edges_by_channel"] = dict(by_ch)
                    save_stats()
                    last_status = now

            t_stream_end = time.time()
            raw(dev, {"command": "stop_tt_data", "callback": "s",
                      "params": {"section": v}}, settle=0.0)
            log(f"stream CLOSED after {t_stream_end - t_stream0:.0f}s, "
                f"{stats['edges_total']} edges")
        stats["finished"] = "clean" if not _stop["flag"] else "signal"
    except KeyboardInterrupt:
        stats["finished"] = "signal"
    except Exception as e:
        stats["alarm"] = f"{e!r} (zero-reconnect policy: run ended)"
        log(f"ALARM: {stats['alarm']}")
    finally:
        if t_stream0 and t_stream_end is None:
            t_stream_end = time.time()   # stream ended by error/signal
        edges_f.flush(); edges_f.close()
        stats["edges_by_channel"] = dict(by_ch)
        stats["stream_seconds"] = (round(t_stream_end - t_stream0, 1)
                                   if t_stream0 else 0.0)
        save_stats()

    # --- phase 3: restore + post baseline (fresh session; stream socket is one-way) ---
    ok, _ = restore_counter(args.ip, args.password, letter)
    stats["restored"] = ok
    if ok and args.post > 0 and not _stop["flag"]:
        try:
            with board_session(args.ip, password=args.password,
                               purpose=f"tt_stream_qualify post-baseline {letter}") as s:
                log(f"post-stream counter baseline ({args.post:.0f}s)...")
                stats["post_rates_hz"] = counter_series(s, letter, args.post,
                                                        counters_w, "post")
        except Exception as e:
            log(f"post baseline failed ({e!r}) — pre rates still usable")
    counters_f.flush(); counters_f.close()
    save_stats()

    # --- report ---
    span = stats.get("stream_seconds") or 0.0
    print("\n================ REPORT ================")
    print(f"section {letter}; streamed {span:.0f}s; "
          f"edges {stats['edges_total']} ({stats['packets']} packets)")
    for i, panel in enumerate(PANEL_OF_INPUT):
        pre = (stats["pre_rates_hz"] or [None] * 4)[i]
        post = (stats["post_rates_hz"] or [None] * 4)[i]
        n = by_ch.get(panel, 0)
        exp = None
        if pre is not None and span:
            base = pre if post is None else 0.5 * (pre + post)
            exp = base * span
        ratio = (f"{n / exp:.2f}" if exp and exp > 0 else "n/a")
        print(f"  panel {panel} (counter idx {i}): {n:8d} edges | "
              f"pre {pre if pre is None else round(pre, 1)} Hz, "
              f"post {post if post is None else round(post, 1)} Hz | "
              f"streamed/expected ~ {ratio}")
    print(f"  max packet gap {stats['max_packet_gap_s']}s; "
          f"gaps>{args.gap_thresh}s recorded: {len(stats['gaps'])}")
    if stats["alarm"]:
        print(f"  ALARM: {stats['alarm']}")
    print(f"  output: {out_dir}")
    print("(interpretation rules in the module docstring / "
          "TT_STREAM_QUALIFY_PLAN_2026-07-17.md)")
    return 1 if stats["alarm"] else 0


if __name__ == "__main__":
    sys.exit(main())
