#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on July 27 2026
Created in PyCharm
Created as nTof_x17_DAQ/stats_page_watcher.py

@author: Dylan Neff, dylan

Standalone watcher that publishes DAQ statistics to the public webpage — a small
JSON summary (run, events, trigger rate, sub-run progress, beam) copied every
~60 s into the EOS directory behind https://dylan-neff.web.cern.ch/x17/.

Push-only and read-only: it opens no port, owns no hardware and commands nothing,
so it is safe to start/stop at any time. All of its numbers come from the Flask
GUI's own /status endpoint and the beam/SPS watcher state files.

See stats_page/README.md for the deploy steps and stats_page/stats_collector.py
for the payload.
"""

import os
import sys
import traceback

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from stats_page.stats_collector import (load_config, run_blocking,
                                        STATS_PAGE_EVENT_LOG)
from common_functions import log_event


def main():
    try:
        run_blocking(load_config())
    except Exception as e:  # noqa: BLE001
        # Durable second copy only — re-raised so the tmux pane still shows the live
        # traceback and the process still exits non-zero.
        log_event(STATS_PAGE_EVENT_LOG, 'CRASH', 'stats_page',
                  error=repr(e), traceback=traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
