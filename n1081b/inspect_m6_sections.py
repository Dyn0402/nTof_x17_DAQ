#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""inspect_m6_sections.py — READ-ONLY dump of M6 (.245) per-channel input/output state.

Why this exists: poll_modules' archived `n1081b_config.json` records .245's section
functions and the section-level input/output configuration, but leaves the PER-CHANNEL
`input_channels` / `output_channels` as null — so there is no offline way to answer
"which outputs of SEC_C are enabled?". This reads exactly that, and writes nothing.

.245 runs old firmware (2022.3.0.0) which serves get/set WITHOUT a login, so the session
is opened with require_login=False (same as the scan watcher and set_mesh_injection do).

Usage:  python n1081b/inspect_m6_sections.py [--sections B C] [--channels 4]
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from n1081b_session import board_session, BoardBusyError, BoardWedgedError  # noqa: E402
from n1081b_sdk import N1081B  # noqa: E402

IP = '192.168.10.245'


def main():
    ap = argparse.ArgumentParser(description='READ-ONLY dump of M6 (.245) channels')
    ap.add_argument('--sections', nargs='+', default=['A', 'B', 'C', 'D'],
                    choices=['A', 'B', 'C', 'D'])
    # SIX inputs (0-5) but only FOUR outputs (0-3) per section — n1081b_module_map.py
    # (`_in` runs to range(1,6), `_out` only range(4)). Reading an out-of-range OUTPUT
    # does NOT error: the board returns uninitialised junk (M6.C out4 mono_value
    # 0x01010101 == 16843009), which is easy to mistake for real configuration.
    ap.add_argument('--in-channels', type=int, default=6,
                    help='LEMO inputs per section (hardware has 6: 0-5)')
    ap.add_argument('--out-channels', type=int, default=4,
                    help='LEMO outputs per section (hardware has 4: 0-3); reading '
                         'beyond 4 returns junk rather than an error')
    args = ap.parse_args()

    try:
        with board_session(IP, purpose='read-only M6 section/channel dump',
                           require_login=False) as s:
            fns = s.call('get_sections_function')['data']
            fmap = {f['section']: f['function_name'] for f in fns}
            for name in args.sections:
                sec = getattr(N1081B.Section, f'SEC_{name}')
                idx = 'ABCD'.index(name)
                print(f'\n=== M6 SEC_{name}  (function: {fmap.get(idx, "?")}) ===')
                for ch in range(args.in_channels):
                    try:
                        c = s.call('get_input_channel_configuration', sec, ch)['data']
                        print(f'  in{ch} : status={c["status"]!s:5s} gd={c["enable_gd"]!s:5s} '
                              f'gate={c["gate"]:>6} delay={c["delay"]:>6} '
                              f'invert={c["invert"]}')
                    except Exception as e:
                        print(f'  in{ch} : ERROR {e}')
                for ch in range(args.out_channels):
                    try:
                        c = s.call('get_output_channel_configuration', sec, ch)['data']
                        print(f'  out{ch}: status={c["status"]!s:5s} '
                              f'mono={c["enable_mono"]!s:5s} '
                              f'mono_value={c["mono_value"]:>6} invert={c["invert"]}')
                    except Exception as e:
                        print(f'  out{ch}: ERROR {e}')
    except (BoardBusyError, BoardWedgedError) as e:
        print(f'BOARD UNAVAILABLE: {e}')
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
