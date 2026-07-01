#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Summarize n1081b/snapshots/dump.json into a human-readable comparison.

Runs locally (no board access needed) against the JSON produced by
dump_module_info.py.  Usage:  python n1081b/summarize_dump.py [dump.json]
"""
import json
import sys

STD = {0: "NIM", 1: "TTL", 2: "DISCR"}


def d(node):
    """Unwrap the SDK envelope -> its .data payload (or {} on error)."""
    return (node or {}).get("data", {}) if isinstance(node, dict) else {}


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "n1081b/snapshots/dump.json"
    boards = json.load(open(path))

    for ip, b in boards.items():
        v = d(b.get("version"))
        eth = d(b.get("ethernet"))
        print("=" * 78)
        print(f"{ip}   sn={v.get('serial_number')}   sw={v.get('software_version')}   "
              f"fpga={v.get('fpga_version')}   mac={v.get('mac_address')}")
        clk = b.get("clock", {})
        print(f"   clock: data={clk.get('data')}  clk_out_enable={clk.get('clk_out_enable')}   "
              f"eth: ip={eth.get('ip')} nm={eth.get('nm')} gw={eth.get('gw')} dhcp={eth.get('dhcp')}")
        print(f"   config_file_list: {d(b.get('config_file_list')) or (b.get('config_file_list') or {}).get('data')}")

        fnmap = {r["section"]: r["function_name"] for r in d(b.get("sections_function"))} if b.get("sections_function") else {}
        for si, sec in enumerate(("SEC_A", "SEC_B", "SEC_C", "SEC_D")):
            s = b["sections"][sec]
            inp = d(s.get("input_configuration"))
            outp = d(s.get("output_configuration"))
            fn = fnmap.get(si, "?")
            in_std = STD.get(inp.get("standard"), inp.get("standard"))
            imp = "50" if inp.get("imp") in (True, "true") else "HI"
            out_std = STD.get(outp.get("standard"), outp.get("standard"))
            # enabled input channels
            en = []
            for ch in range(6):
                cc = d(s.get("input_channels", {}).get(str(ch)))
                if cc.get("status"):
                    tag = str(ch)
                    if cc.get("invert"):
                        tag += "!"
                    en.append(tag)
            fc = d(s.get("function_configuration"))
            # compact function-config view
            fc_bits = {k: fc[k] for k in fc if k != "lemo_enables"}
            lemo_en = [l["lemo"] for l in fc.get("lemo_enables", []) if l.get("enable")]
            print(f"   {sec[-1]}: fn={fn:<16} in={in_std}/{imp}Ω th={inp.get('threshold')} "
                  f"out={out_std}  in_ch_on={','.join(en) or '-'}  "
                  f"lemo_en={lemo_en}  cfg={fc_bits}")
    print("=" * 78)


if __name__ == "__main__":
    main()
