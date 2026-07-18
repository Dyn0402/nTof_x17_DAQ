#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on July 14 2026; v2 July 17 2026; v3 (rate-gated) later 2026-07-17
Created as nTof_x17_DAQ/n1081b_timetag_watcher.py

@author: Dylan Neff, dylan

Standalone N1081B Module-5 (.244) watcher -- the SOLE owner of the board.

v3 (rate-gated; see n1081b/timetag_watcher_controller.py for the full design and
the TT rate-ceiling story in HANDOFF_2026-07-17_tt_rate_ceiling.md): every gate
cycle (default 5 min) it
  * restores all managed sections to counter mode and logs absolute counts +
    deltas + rates to a per-day counters CSV -- the running-total record for the
    walls (A) and liq (B), which count at any rate but CANNOT be TT-streamed,
  * rate-gates the TT candidates (default C and D): only sections at/below
    --gate-hz (default 40 Hz, under the ~50 Hz proven-streaming ceiling) are
    armed to Time-Tag and streamed, one dwell at a time, edges appended to the
    per-day time-tag CSV in ~/beam_july/slow_control/n1081b_timetag/,
  * publishes health + rates to config/n1081b_timetag_state.json (/n1081b/status),
  * stops with an alarm on ANY error -- v3 has a reconnect budget of ZERO
    (reconnect churn onto a healthy board is what wedged .244 on 07-17),
  * on exit RESTORES .244 to its counter steady state.

Runs in its own tmux session (start_servers.sh or the GUI Start/Stop buttons).
While it runs it owns .244 exclusively (session flock; poll_modules also skips
.244 whenever this watcher's tmux session is alive).
"""

import argparse
import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from n1081b.timetag_watcher_controller import (N1081BTimeTagController,
                                               DEFAULT_DWELL_S, DEFAULT_SECTIONS,
                                               DEFAULT_TT_SECTIONS, DEFAULT_GATE_HZ,
                                               DEFAULT_GATE_PERIOD_S)


def main():
    ap = argparse.ArgumentParser(
        description="N1081B Module-5 (.244) watcher (v3: rate-gated TT + counter totals)")
    ap.add_argument("--restore", action="store_true",
                    help="recovery path: put .244's sections back to counters and exit "
                         "(needed if the watcher was SIGKILLed and left the board in "
                         "'wire'/time_tag)")
    ap.add_argument("--duration", type=float, default=0,
                    help="run this many seconds then restore + exit (0 = until signal); "
                         "use for bounded soak tests")
    ap.add_argument("--dwell", type=float, default=DEFAULT_DWELL_S,
                    help=f"seconds one section's stream is held open per tap "
                         f"(default {DEFAULT_DWELL_S:.0f}, clamped 5-60)")
    ap.add_argument("--sections", default=DEFAULT_SECTIONS,
                    help="sections managed as counters / running totals "
                         f"(default {DEFAULT_SECTIONS})")
    ap.add_argument("--tt-sections", default=DEFAULT_TT_SECTIONS,
                    help="TT-streaming candidates, gated per cycle by --gate-hz "
                         f"(default {DEFAULT_TT_SECTIONS}; walls A / liq B are "
                         "over the ceiling at current rates — leave them out)")
    ap.add_argument("--gate-hz", type=float, default=DEFAULT_GATE_HZ,
                    help="stream a candidate only if its aggregate counter rate is "
                         f"at/below this (default {DEFAULT_GATE_HZ:.0f} Hz; the board "
                         "goes TT-silent somewhere between ~50 and ~800 Hz)")
    ap.add_argument("--gate-period", type=float, default=DEFAULT_GATE_PERIOD_S,
                    help="seconds per gate cycle: counter log + re-gate + stream "
                         f"(default {DEFAULT_GATE_PERIOD_S:.0f}, clamped 120-1800)")
    args = ap.parse_args()

    if args.restore:
        ok = N1081BTimeTagController(sections=args.sections).restore_counters()
        sys.exit(0 if ok else 1)
    ctl = N1081BTimeTagController(sections=args.sections, tt_sections=args.tt_sections,
                                  dwell_s=args.dwell, gate_hz=args.gate_hz,
                                  gate_period_s=args.gate_period)
    ctl.run_blocking(duration_s=args.duration)
    sys.exit(1 if ctl.alarm else 0)


if __name__ == "__main__":
    main()
