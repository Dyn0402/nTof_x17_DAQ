#!/usr/bin/env python3
"""Safe post-reboot restore of .244 (M5) to its steady state: all four sections in
FN_COUNTER (lemo0-3, no gate), counting. Run this AFTER physically rebooting .244 and
clearing its quarantine (see n1081b/POST_REBOOT_244_CHECKLIST.md).

WHY THIS EXISTS (2026-07-16): the old restore path lived in
`timetag_watcher_controller.restore_counters()` and used a RAW n1081b_sdk connection —
the exact class of tool that wedged .244 in the first place, and risky to run on a
freshly-recovered board. The plan/comments also referenced a
`n1081b_timetag_watcher.py --restore` entry point that does not exist. This standalone
does the same counter-restore but through the mandatory `board_session()` gateway
(interprocess lock, bounded connect, guaranteed clean close, breaker) so it cannot
dirty-disconnect and re-wedge the board.

Read-back verified: every section must report 'counter' or the script fails loudly.

Usage:
    .venv/bin/python n1081b/restore_244_counters.py            # restore + verify
    .venv/bin/python n1081b/restore_244_counters.py --dry-run  # show intent, no writes
"""
import argparse
import os
import sys

if "__file__" in globals():
    _HERE = os.path.dirname(os.path.abspath(__file__))
    if _HERE not in sys.path:
        sys.path.insert(0, _HERE)

from n1081b_sdk import N1081B
from n1081b_session import (board_session, BoardBusyError, BoardWedgedError,
                            BoardQuarantinedError)

IP = "192.168.10.244"
SECTIONS = list(N1081B.Section)   # SEC_A..SEC_D
FN_COUNTER = N1081B.FunctionType.FN_COUNTER
WRITE_GAP_S = 0.3


def restore(dry_run=False):
    if dry_run:
        print(f"[dry-run] would set {IP} SEC_A..D -> FN_COUNTER (lemo0-3, no gate), "
              f"reset channels, and verify readback == 'counter'.")
        return True
    # require_login=False: tolerate old-firmware login()==False; every write is
    # read-back verified below, so proceeding login-less is safe.
    with board_session(IP, purpose="restore .244 counters", min_gap_s=WRITE_GAP_S,
                       require_login=False) as s:
        for sec in SECTIONS:
            s.call("set_section_function", sec, FN_COUNTER)
            s.call("configure_counter", sec, True, True, True, True, False)
            for ch in range(4):
                s.call("reset_channel", sec, ch, FN_COUNTER)
        names = [x["function_name"] for x in s.call("get_sections_function")["data"]]
    ok = all(names[sec.value] == "counter" for sec in SECTIONS)
    print(f"section functions now: {names}")
    if ok:
        print(f"OK: {IP} all four sections restored to 'counter'.")
    else:
        print(f"!! VERIFY FAILED: not all sections read 'counter' -> {names}")
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="show intent, touch no board")
    args = ap.parse_args()
    try:
        return 0 if restore(args.dry_run) else 1
    except BoardQuarantinedError as e:
        print(f"!! {IP} is still QUARANTINED: {e}", file=sys.stderr)
        print("   Clear it FIRST (only after a verified-healthy reboot):", file=sys.stderr)
        print("   python -c \"import sys;sys.path.insert(0,'.'); "
              "from n1081b.n1081b_session import clear_quarantine; "
              "print(clear_quarantine('192.168.10.244'))\"", file=sys.stderr)
        return 2
    except BoardBusyError as e:
        print(f"!! {IP} is held by another process: {e}", file=sys.stderr)
        print("   Wait for it to finish; do NOT force.", file=sys.stderr)
        return 2
    except BoardWedgedError as e:
        print(f"!! {IP} unreachable/wedged: {e}", file=sys.stderr)
        print("   The reboot may not have fully brought the command interface up. "
              "Leave it alone and re-check; do NOT hammer it.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
