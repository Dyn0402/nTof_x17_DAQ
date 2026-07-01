#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Homogenize the N1081B boards: set every section to the SAME electrical config and
the SAME function (wire).

Target per section (A-D):
  * input : standard = NIM, standard_sub = NIM, threshold = 0, impedance = 50 ohm
  * input channels 0-5 : enabled, gate/delay 0, not inverted
  * function : wire, all 4 logic LEMO enables on
  * output : standard = NIM

Before touching a board it saves the current config to an on-board file
`backup_pre_homog.json` AND downloads that file's JSON content to snapshots/, so
the pre-change state is recoverable (load_configuration_file / upload).

SAFETY: board 244 is IN USE and is skipped unless explicitly included.

Run on the DAQ server (board net):
    ssh daq_lxplus '~/PycharmProjects/nTof_x17_DAQ/.venv/bin/python -' < n1081b/homogenize.py
"""
import json
import sys
from n1081b_sdk import N1081B

Section = N1081B.Section
Std = N1081B.SignalStandard
Imp = N1081B.SignalImpedance
Fn = N1081B.FunctionType

# 244 deliberately excluded (in use). Pass "--include-244" to add it.
IPS = [f"192.168.10.{n}" for n in (240, 241, 242, 243)]
if "--include-244" in sys.argv:
    IPS.append("192.168.10.244")
PASSWORD = "password"
BACKUP_NAME = "backup_pre_homog.json"
NEW_CONFIG_NAME = "homogeneous_wire.json"


def _res(r):
    return r.get("Result") if isinstance(r, dict) else "None(no-return)"


def apply_section(dev, sec, log):
    # electrical
    r = dev.set_input_configuration(sec, Std.STANDARD_NIM, Std.STANDARD_NIM, 0, Imp.IMPEDANCE_50)
    log.append(f"    {sec.name} set_input NIM/50/th0 -> {_res(r)}")
    # function = wire, all logic enables on.  NB: configure_wire() returns None
    # (SDK wrapper has no `return`); the command is still sent — verify via readback.
    r = dev.set_section_function(sec, Fn.FN_WIRE)
    log.append(f"    {sec.name} set_function wire -> {_res(r)}")
    dev.configure_wire(sec, True, True, True, True)
    # input channels 0-5 enabled, no gate/delay/invert
    for ch in range(6):
        dev.set_input_channel_configuration(sec, ch, True, True, 0, 0, False)
    log.append(f"    {sec.name} configure_wire 0-3 + input ch0-5 enabled")
    # output standard NIM
    r = dev.set_output_configuration(sec, Std.STANDARD_NIM)
    log.append(f"    {sec.name} set_output NIM -> {_res(r)}")


def do_board(ip):
    log = [f"=== {ip} ==="]
    dev = N1081B(ip)
    try:
        if not dev.connect():
            log.append("  connect FAILED"); return log, None
        dev.ws.settimeout(8)
        if not dev.login(PASSWORD):
            log.append("  login FAILED"); return log, None

        # --- backup current state (ONLY if not already backed up, so a re-run
        #     never clobbers the original-state backup with a partial config) ---
        existing = (dev.get_configuration_file_list() or {}).get("data", "") or ""
        if BACKUP_NAME in existing:
            log.append(f"  backup {BACKUP_NAME} already exists on board — NOT overwriting")
        else:
            r = dev.save_configuration_file(BACKUP_NAME)
            log.append(f"  save_configuration_file({BACKUP_NAME}) -> {_res(r)}")
        dl = dev.download_configuration_file(BACKUP_NAME)
        backup_content = dl if isinstance(dl, dict) else {"raw": str(dl)}
        log.append(f"  download_configuration_file raw keys: {list(backup_content.keys())}")

        # --- apply homogeneous config to all four sections ---
        for sec in Section:
            apply_section(dev, sec, log)

        # --- persist the new state as a named config too ---
        r = dev.save_configuration_file(NEW_CONFIG_NAME)
        log.append(f"  save_configuration_file({NEW_CONFIG_NAME}) -> {_res(r)}")

        # --- verify ---
        fns = dev.get_sections_function().get("data")
        log.append(f"  VERIFY sections_function: {[f['function_name'] for f in fns]}")
        for sec in Section:
            inp = dev.get_input_configuration(sec).get("data", {})
            log.append(f"  VERIFY {sec.name} input: std={inp.get('standard')} "
                       f"imp={inp.get('imp')} th={inp.get('threshold')}")
        return log, backup_content
    except Exception as e:  # noqa: BLE001
        log.append(f"  ERROR: {e!r}")
        return log, None
    finally:
        try:
            dev.disconnect()
        except Exception:
            pass


def main():
    backups = {}
    for ip in IPS:
        log, backup = do_board(ip)
        print("\n".join(log))
        if backup is not None:
            backups[ip] = backup
    # Emit downloaded backups as a JSON blob on the LAST lines, delimited, so the
    # caller can split them out to files.
    print("\n===BACKUPS_JSON_BEGIN===")
    print(json.dumps(backups))
    print("===BACKUPS_JSON_END===")


if __name__ == "__main__":
    main()
