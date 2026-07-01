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
server.  Typical use from the dev laptop:

    ssh daq_lxplus '~/PycharmProjects/nTof_x17_DAQ/.venv/bin/python -' \
        < n1081b/dump_module_info.py > n1081b/snapshots/dump.json

Then pretty-print / summarize locally.
"""
import json
import sys
from n1081b_sdk import N1081B

IPS = [f"192.168.10.{n}" for n in (240, 241, 242, 243, 244)]
PASSWORD = "password"
SECTIONS = list(N1081B.Section)          # SEC_A..SEC_D
CHANNELS = range(6)                       # LEMO inputs / outputs 0-5
RECV_TIMEOUT = 6                          # seconds


def _call(out_errors, label, fn, *args):
    """Run a get_* call, capturing either its result or the error string."""
    try:
        return fn(*args)
    except Exception as e:  # noqa: BLE001 - we want every failure recorded, not raised
        out_errors[label] = repr(e)
        return None


def dump_board(ip):
    board = {"ip": ip, "errors": {}}
    dev = N1081B(ip)
    try:
        if not dev.connect():
            board["errors"]["connect"] = "connect() returned False"
            return board
        dev.ws.settimeout(RECV_TIMEOUT)
        board["login"] = bool(dev.login(PASSWORD))

        # ---- board-level ----
        board["version"] = _call(board["errors"], "version", dev.get_version)
        board["ethernet"] = _call(board["errors"], "ethernet", dev.get_ethernet_configuration)
        board["clock"] = _call(board["errors"], "clock", dev.get_clock_status)
        board["sections_function"] = _call(board["errors"], "sections_function", dev.get_sections_function)
        board["config_file_list"] = _call(board["errors"], "config_file_list", dev.get_configuration_file_list)

        # ---- per section (A-D) ----
        board["sections"] = {}
        for sec in SECTIONS:
            s = {}
            s["function_configuration"] = _call(board["errors"], f"{sec.name}.fn_config", dev.get_function_configuration, sec)
            s["function_results"] = _call(board["errors"], f"{sec.name}.fn_results", dev.get_function_results, sec)
            s["input_configuration"] = _call(board["errors"], f"{sec.name}.input_config", dev.get_input_configuration, sec)
            s["output_configuration"] = _call(board["errors"], f"{sec.name}.output_config", dev.get_output_configuration, sec)
            s["input_channels"] = {
                ch: _call(board["errors"], f"{sec.name}.in_ch{ch}", dev.get_input_channel_configuration, sec, ch)
                for ch in CHANNELS
            }
            s["output_channels"] = {
                ch: _call(board["errors"], f"{sec.name}.out_ch{ch}", dev.get_output_channel_configuration, sec, ch)
                for ch in CHANNELS
            }
            board["sections"][sec.name] = s
    except Exception as e:  # noqa: BLE001
        board["errors"]["fatal"] = repr(e)
    finally:
        try:
            dev.disconnect()
        except Exception:
            pass
    return board


def main():
    result = {ip: dump_board(ip) for ip in IPS}
    json.dump(result, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
