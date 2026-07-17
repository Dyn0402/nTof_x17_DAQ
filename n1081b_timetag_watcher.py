#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on July 14 2026, redesigned July 17 2026 (v2)
Created as nTof_x17_DAQ/n1081b_timetag_watcher.py

@author: Dylan Neff, dylan

Standalone N1081B time-tag watcher -- the SOLE owner of Module 5 (.244), streaming
its four scintillator-wall sections (A-D) as per-edge timestamps.

Runs in its own tmux session (started by start_servers.sh at boot, or via the GUI
"Start N1081B Time-Tag Watcher" button). v2 (gentle long-dwell rotation; see
n1081b/timetag_watcher_controller.py for the full design + the v1 wedge history):
  * arms every section to Time-Tag once per session, then holds ONE section's
    stream open ~12 s at a time, rotating A->B->C->D (backlog is dumped at each
    tap), through the mandatory session gateway (n1081b/n1081b_session.py),
  * dedups and appends per-edge rows (host_unix, section, channel, t_board_ns) to
    the per-day CSV ~/beam_july/slow_control/n1081b_timetag/,
  * publishes health + per-section rates to config/n1081b_timetag_state.json
    (served by /n1081b/status),
  * health-checks itself (beam-on + zero edges -> one re-arm, then stop + alarm),
  * on exit RESTORES .244 to its counter steady state.

While it runs it owns .244 exclusively (session flock; poll_modules also skips
.244 whenever this watcher's tmux session is alive).
"""

import argparse
import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from n1081b.timetag_watcher_controller import (N1081BTimeTagController,
                                               DEFAULT_DWELL_S, DEFAULT_SECTIONS)


def main():
    ap = argparse.ArgumentParser(
        description="N1081B Module-5 (.244) time-tag watcher (v2, long-dwell rotation)")
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
                    help="subset of ABCD to stream (default all four)")
    args = ap.parse_args()

    if args.restore:
        ok = N1081BTimeTagController(sections=args.sections).restore_counters()
        sys.exit(0 if ok else 1)
    ctl = N1081BTimeTagController(sections=args.sections, dwell_s=args.dwell)
    ctl.run_blocking(duration_s=args.duration)
    sys.exit(1 if ctl.alarm else 0)


if __name__ == "__main__":
    main()
