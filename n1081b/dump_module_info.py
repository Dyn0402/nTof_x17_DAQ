#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Read EVERYTHING readable from each CAEN N1081B unit and emit it as one JSON blob
on stdout.  Read-only: connects, logs in, and only calls get_* methods — nothing
on the board is configured or changed.

Doubles as a pre-change backup: the per-section function/input/output
configuration captured here is enough to see (and hand-restore) the current state
of every board before we homogenize them.

Must run where the boards are reachable (the DAQ private net) — i.e. on the daq
server. Board access goes through the mandatory `n1081b/n1081b_session.py` gateway
(lock + quarantine gate + clean close + breaker), never a raw connection — so a
quarantined board (e.g. .244) is recorded and skipped instead of hanging the dump.

Typical use (run from the repo root so n1081b/ is importable):

    .venv/bin/python n1081b/dump_module_info.py > n1081b/snapshots/dump.json

Piped over ssh needs the n1081b dir on PYTHONPATH (so n1081b_session resolves):

    ssh daq_lxplus 'PYTHONPATH=~/PycharmProjects/nTof_x17_DAQ/n1081b \
        ~/PycharmProjects/nTof_x17_DAQ/.venv/bin/python -' \
        < n1081b/dump_module_info.py > n1081b/snapshots/dump.json
"""
import json
import os
import sys

# Make the sibling n1081b_session importable when run as a file (sys.path[0] already
# covers the direct case; this also helps `python path/to/dump_module_info.py`).
if "__file__" in globals():
    _HERE = os.path.dirname(os.path.abspath(__file__))
    if _HERE not in sys.path:
        sys.path.insert(0, _HERE)

from n1081b_sdk import N1081B
from n1081b_session import (board_session, BoardBusyError, BoardWedgedError,
                            BoardQuarantinedError)

IPS = [f"192.168.10.{n}" for n in (240, 241, 242, 243, 244, 245)]
PASSWORD = "password"
SECTIONS = list(N1081B.Section)          # SEC_A..SEC_D
CHANNELS = range(6)                       # LEMO inputs / outputs 0-5
RECV_TIMEOUT = 6                          # seconds


def _get(errs, label, s, method, *args):
    """Run one read-only getter through the session, capturing result or error.
    Ordinary getter failures are recorded and we continue; a breaker BoardWedgedError
    propagates so dump_board stops reading this board instead of hammering it."""
    try:
        return s.call(method, *args)
    except BoardWedgedError:
        raise
    except Exception as e:  # noqa: BLE001 - record every failure, never raise
        errs[label] = repr(e)
        return None


def dump_board(ip):
    board = {"ip": ip, "errors": {}}
    errs = board["errors"]
    try:
        # Read-only diagnostic: require_login=False (dump old-fw boards too),
        # auto_quarantine=False (checking a suspect board must not impose a 6 h
        # lockout), min_gap_s=0.0 (reads are cheap).
        with board_session(ip, purpose="dump_module_info", password=PASSWORD,
                           timeout_s=RECV_TIMEOUT, min_gap_s=0.0, require_login=False,
                           auto_quarantine=False) as s:
            board["login"] = s.login_ok

            # ---- board-level ----
            board["version"] = _get(errs, "version", s, "get_version")
            board["ethernet"] = _get(errs, "ethernet", s, "get_ethernet_configuration")
            board["clock"] = _get(errs, "clock", s, "get_clock_status")
            board["sections_function"] = _get(errs, "sections_function", s, "get_sections_function")
            board["config_file_list"] = _get(errs, "config_file_list", s, "get_configuration_file_list")

            # ---- per section (A-D) ----
            board["sections"] = {}
            for sec in SECTIONS:
                sd = {}
                sd["function_configuration"] = _get(errs, f"{sec.name}.fn_config", s, "get_function_configuration", sec)
                sd["function_results"] = _get(errs, f"{sec.name}.fn_results", s, "get_function_results", sec)
                sd["input_configuration"] = _get(errs, f"{sec.name}.input_config", s, "get_input_configuration", sec)
                sd["output_configuration"] = _get(errs, f"{sec.name}.output_config", s, "get_output_configuration", sec)
                sd["input_channels"] = {
                    ch: _get(errs, f"{sec.name}.in_ch{ch}", s, "get_input_channel_configuration", sec, ch)
                    for ch in CHANNELS
                }
                sd["output_channels"] = {
                    ch: _get(errs, f"{sec.name}.out_ch{ch}", s, "get_output_channel_configuration", sec, ch)
                    for ch in CHANNELS
                }
                board["sections"][sec.name] = sd
    except BoardBusyError as e:
        errs["busy"] = repr(e)          # another process holds it; skip
    except BoardQuarantinedError as e:
        errs["quarantined"] = repr(e)   # board resting; leave it alone
    except BoardWedgedError as e:
        errs["wedged"] = repr(e)        # breaker tripped mid-read; do NOT retry
    except Exception as e:  # noqa: BLE001
        errs["fatal"] = repr(e)
    return board


def main():
    result = {ip: dump_board(ip) for ip in IPS}
    json.dump(result, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
