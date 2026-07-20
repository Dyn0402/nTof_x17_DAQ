#!/usr/bin/env python3
"""Switch the DREAM trigger between the three run-type modes on M4 (.243).

All three modes drive the same DREAM trigger cable = M4.D out0; only the C/D
input enables change. These are LIVE-BOARD settings — they revert if .243
power-cycles, so re-run this script after any power event.

Cabling (verified 2026-07-11, see HANDOFF_2026-07-11_latency_tuning.md):
  M4.C  or_veto : lemo0 = Singles (M4.A out)     lemo1 = Doubles (M4.B out)
                  lemo4 = M6.D pulser (~667 Hz)  lemo5 = implicit VETO
                  (veto line = N93B 30 ms delay-timer window, inverted NIM:
                   HIGH in-window = enable, LOW outside = veto.
                   2026-07-11: line measured CONSTANTLY HIGH -> no windowing;
                   pulser passes ungated at ~667 Hz until the N93B is sorted)
  M4.D  or      : lemo0 = gamma-flash trigger line   lemo1 = M4.C out0
        out0    = DREAM external trigger (cable to TCM)

Modes:
  flash        D = OR(lemo0)          gamma flash only; C output suppressed
  flash_random C = or_veto(pulser)    flash + 30 ms-gated random pulser
               D = OR(lemo0, lemo1)
  scint        C = or_veto(Singles and/or Doubles)   scintillator trigger
               D = OR(lemo1)  [+ lemo0 PS/flash if --ps-pickup]

--ps-pickup ORs the PS/gamma-flash line (M4.D lemo0) into the scint trigger so a
run fires on Doubles OR a beam pickup. The PS leg carries its own G&D delay on
M4.D in0 (set separately via set_ps_trigger_delay.py) so the flash co-frames with
the scint pulse in the same DREAM window — 1800 ns puts the flash at ~smp 13 next
to the doubles MM pulse at ~11 (latency 35, 32 smp; measured 2026-07-19).

Usage:
  trigger_mode.py status
  trigger_mode.py flash
  trigger_mode.py flash_random [--pulser-lemo N]
  trigger_mode.py scint [--singles | --doubles | --both] [--ps-pickup]

Every write is read-back verified; C/D config before+after is appended to
snapshots/trigger_mode_log.jsonl.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

if "__file__" in globals():
    _HERE = os.path.dirname(os.path.abspath(__file__))
    if _HERE not in sys.path:
        sys.path.insert(0, _HERE)

from n1081b_sdk import N1081B
from n1081b_session import (board_session, BoardBusyError, BoardWedgedError,
                            BoardQuarantinedError)

M4_IP = "192.168.10.243"
PULSER_LEMO = 4   # panel 5; LA-verified 2026-07-11 (verify_trigger_paths.py test 2)
LOG_PATH = Path(__file__).resolve().parent / "snapshots" / "trigger_mode_log.jsonl"
# Config writes pace ~0.3-1 s apart per the board-hygiene guardrail.
WRITE_GAP_S = 0.3


class _Board:
    """Session-backed connection adapter. trigger_mode's own apply()/status() pass a
    raw board_session to the helpers (helpers call ``s.call(...)``); external scripts
    that do ``d = trigger_mode.connect(); tm.set_d_or(d, ...); d.some_sdk_method(...)``
    get this adapter, which serves BOTH ``d.method(args)`` (via __getattr__) and
    ``s.call("method", args)`` (explicit) through one locked, clean-closing session.
    Call ``d.close()`` when done (releases the interprocess lock)."""

    def __init__(self, session):
        self._s = session

    def call(self, name, *a, **k):
        return self._s.call(name, *a, **k)

    def __getattr__(self, name):
        return lambda *a, **k: self._s.call(name, *a, **k)

    def close(self):
        self._s.__exit__(None, None, None)


def connect(ip=M4_IP, purpose="trigger_mode helpers"):
    """Open a locked, clean-closing board_session and return a call-forwarding adapter
    compatible with the module's helpers and with raw ``d.method(...)`` call sites.
    Raises BoardBusyError/BoardWedgedError/BoardQuarantinedError — the caller must
    abort, not force. Remember to ``.close()`` the returned object."""
    s = board_session(ip, purpose=purpose, min_gap_s=WRITE_GAP_S)
    s.__enter__()   # acquire lock + connect
    return _Board(s)


def get_cd_state(s):
    """Current function + lemo enables for M4 sections C and D."""
    out = {}
    for name in ("SEC_C", "SEC_D"):
        sec = getattr(N1081B.Section, name)
        fc = (s.call("get_function_configuration", sec) or {}).get("data") or {}
        out[name] = fc
    return out


def lemos_enabled(fc):
    """Set of enabled lemo indices from a get_function_configuration payload."""
    return sorted(l.get("lemo") for l in (fc.get("lemo_enables") or []) if l.get("enable"))


def set_c_or_veto(s, enables):
    """C = or_veto with the given iterable of enabled lemos (0-4); lemo5 = veto.

    CRITICAL (found 2026-07-11): configure_or_veto alone does NOT change the
    section's function type — it only sets lemo enables of whatever function
    is loaded (the firmware ignores the callback name). The type must be
    selected first with set_section_function, else C silently stays a plain
    OR and the veto input is inert."""
    fn = {sec['section']: sec['function_name']
          for sec in (s.call("get_sections_function") or {}).get('data', [])}
    if fn.get(2) != 'or_veto':
        s.call("set_section_function", N1081B.Section.SEC_C, N1081B.FunctionType.FN_OR_VETO)
    en = [i in enables for i in range(5)]
    s.call("configure_or_veto", N1081B.Section.SEC_C, *en, False, 0)


def set_d_or(s, enables):
    """D = plain OR with the given iterable of enabled lemos (0-5)."""
    fn = {sec['section']: sec['function_name']
          for sec in (s.call("get_sections_function") or {}).get('data', [])}
    if fn.get(3) != 'or':
        s.call("set_section_function", N1081B.Section.SEC_D, N1081B.FunctionType.FN_OR)
    en = [i in enables for i in range(6)]
    s.call("configure_or", N1081B.Section.SEC_D, *en, False, 0)


def apply(mode, c_lemos, d_lemos, touch_c=True):
    with board_session(M4_IP, purpose=f"trigger_mode {mode}",
                       min_gap_s=WRITE_GAP_S) as s:
        before = get_cd_state(s)
        if touch_c:
            set_c_or_veto(s, c_lemos)
        set_d_or(s, d_lemos)
        after = get_cd_state(s)

        ok = sorted(lemos_enabled(after["SEC_D"])) == sorted(d_lemos)
        if touch_c:
            ok = ok and sorted(lemos_enabled(after["SEC_C"])) == sorted(c_lemos)

        rec = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "mode": mode,
               "c_lemos": sorted(c_lemos) if touch_c else "untouched",
               "d_lemos": sorted(d_lemos), "verified": ok,
               "before": before, "after": after}
        LOG_PATH.parent.mkdir(exist_ok=True)
        with open(LOG_PATH, "a") as f:
            f.write(json.dumps(rec) + "\n")

        print(f"mode={mode}")
        if touch_c:
            print(f"  C (or_veto) lemos: {lemos_enabled(before['SEC_C'])} -> "
                  f"{lemos_enabled(after['SEC_C'])}  (want {sorted(c_lemos)})")
        else:
            print(f"  C untouched: lemos {lemos_enabled(after['SEC_C'])}")
        print(f"  D (or)      lemos: {lemos_enabled(before['SEC_D'])} -> "
              f"{lemos_enabled(after['SEC_D'])}  (want {sorted(d_lemos)})")
        print("  READBACK OK" if ok else "  !! READBACK MISMATCH — do not start a run")
        return 0 if ok else 1


def status():
    with board_session(M4_IP, purpose="trigger_mode status") as s:
        st = get_cd_state(s)
        fns = {}
        try:
            for sec in (s.call("get_sections_function") or {}).get("data") or []:
                fns[f"SEC_{'ABCD'[sec.get('section')]}"] = sec.get("function_name")
        except BoardWedgedError:
            raise
        except Exception:
            pass
        for name in ("SEC_C", "SEC_D"):
            print(f"  {name}: fn={fns.get(name, '?')} lemos={lemos_enabled(st[name])}")
        if fns.get("SEC_C") != "or_veto":
            print("  !! C function is NOT or_veto — the veto input is INERT "
                  "(run any mode command to fix; needs set_section_function)")
        c, dl = lemos_enabled(st["SEC_C"]), lemos_enabled(st["SEC_D"])
        if dl == [0]:
            guess = "flash"
        elif dl == [0, 1]:
            guess = {"[4]": "flash_random", "[1]": "scint(doubles)+ps",
                     "[0]": "scint(singles)+ps", "[0, 1]": "scint(both)+ps"}.get(
                         str(c), f"flash+C({c})?")
        elif dl == [1]:
            guess = {"[0]": "scint(singles)", "[1]": "scint(doubles)",
                     "[0, 1]": "scint(both)"}.get(str(c), f"scint? C={c}")
        else:
            guess = "unrecognized"
        print(f"  => looks like mode: {guess}")
        return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mode", choices=["status", "flash", "flash_random", "scint"])
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--singles", action="store_true", help="scint: Singles only (lemo0)")
    g.add_argument("--doubles", action="store_true", help="scint: Doubles only (lemo1)")
    g.add_argument("--both", action="store_true", help="scint: Singles|Doubles (default)")
    ap.add_argument("--pulser-lemo", type=int, default=PULSER_LEMO,
                    help=f"flash_random: C lemo of the M6.D pulser (default {PULSER_LEMO})")
    ap.add_argument("--ps-pickup", action="store_true",
                    help="scint: also OR-in the PS/gamma-flash line (M4.D lemo0) -> D=OR([0,1]); "
                         "co-frame via set_ps_trigger_delay.py")
    args = ap.parse_args()

    try:
        if args.mode == "status":
            return status()
        if args.mode == "flash":
            # C untouched (its output into D is simply disabled)
            return apply("flash", [], [0], touch_c=False)
        if args.mode == "flash_random":
            return apply("flash_random", [args.pulser_lemo], [0, 1])
        if args.mode == "scint":
            c = [1] if args.doubles else ([0] if args.singles else [0, 1])
            tag = "doubles" if args.doubles else ("singles" if args.singles else "both")
            d = [0, 1] if args.ps_pickup else [1]
            if args.ps_pickup:
                tag += "+ps"
            return apply(f"scint({tag})", c, d)
    except BoardBusyError as e:
        print(f"!! board in use by another process: {e}", file=sys.stderr)
        print("   aborted — wait for it to finish, do NOT force.", file=sys.stderr)
        return 2
    except (BoardWedgedError, BoardQuarantinedError) as e:
        print(f"!! board unavailable: {e}", file=sys.stderr)
        print("   aborted — leave the board alone to rest; do NOT start a run.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
