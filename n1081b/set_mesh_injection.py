#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""set_mesh_injection.py — read / set the Micromegas mesh charge-injection fan-out
(M6 / .245, SEC_B) as a standing, whole-run state.

WHY THIS EXISTS: mesh injection has only ever been driven per-sub-run by the scan
schedule (`mesh_b` target, `n1081b_scan_schedule.json`) via the scan watcher /
`scan_control`. A run that wants the mesh simply ON for its whole duration
(n1081b_scan='off') has no way to set it — hence this one-shot tool.

WHAT IT TOUCHES (nothing else on the board):
  * SEC_B input ch0  — the mesh trigger source. `on` enables it and (optionally,
    with --delay/--gate) sets its Gate&Delay injection timing.
  * SEC_B outputs 0-3 — the four mesh-injection fan-out legs. `on` enables them,
    `off` disables them. Use --outputs to enable only a subset (the mesh circuit
    is currently cabled to detectors A and C only); legs not listed are disabled.
  Mono width, invert, and every untouched field are read and preserved. All writes
  are read-back verified.

.245 runs old firmware (2022.3.0.0) which serves get/set WITHOUT a login, so the
session is opened with require_login=False (same as the scan watcher does for it).

Usage:
    python n1081b/set_mesh_injection.py status
    python n1081b/set_mesh_injection.py on                    # in0 + all 4 outputs
    python n1081b/set_mesh_injection.py on --outputs 0 1      # only legs 0,1
    python n1081b/set_mesh_injection.py on --delay 1260 --gate 100
    python n1081b/set_mesh_injection.py off
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from n1081b_session import board_session, BoardBusyError, BoardWedgedError  # noqa: E402
from n1081b_sdk import N1081B  # noqa: E402

MESH_IP = '192.168.10.245'
SECTION = N1081B.Section.SEC_B
TRIG_IN = 0                 # SEC_B in0 = mesh trigger source
OUT_CHS = (0, 1, 2, 3)      # SEC_B out0-3 = mesh charge-injection legs


def read_state(s):
    cin = s.call('get_input_channel_configuration', SECTION, TRIG_IN)['data']
    outs = {ch: s.call('get_output_channel_configuration', SECTION, ch)['data']
            for ch in OUT_CHS}
    return cin, outs


def fmt_state(cin, outs):
    lines = ['M6 (.245) SEC_B — mesh charge-injection',
             f'  in{TRIG_IN} (mesh trigger source): status={cin["status"]} '
             f'gd={cin["enable_gd"]} gate={cin["gate"]} ns delay={cin["delay"]} ns '
             f'invert={cin["invert"]}']
    for ch, c in outs.items():
        lines.append(f'  out{ch}: status={c["status"]} mono={c["enable_mono"]}/'
                     f'{c["mono_value"]} ns invert={c["invert"]}')
    on = [ch for ch, c in outs.items() if c['status']]
    lines.append(f'  => injection {"ON" if (cin["status"] and on) else "OFF"} '
                 f'(enabled legs: {on if on else "none"})')
    return '\n'.join(lines)


def main():
    ap = argparse.ArgumentParser(description='Read/set M6.B mesh charge-injection')
    ap.add_argument('action', choices=['status', 'on', 'off'])
    ap.add_argument('--outputs', type=int, nargs='+', default=list(OUT_CHS),
                    choices=list(OUT_CHS),
                    help='which fan-out legs to enable on "on" (default all 4). '
                         'Legs not listed are DISABLED.')
    ap.add_argument('--delay', type=int, default=None,
                    help='SEC_B in0 Gate&Delay injection delay (ns); enables G&D')
    ap.add_argument('--gate', type=int, default=None,
                    help='SEC_B in0 gate width (ns), used with --delay')
    args = ap.parse_args()

    try:
        with board_session(MESH_IP, require_login=False,
                           purpose=f'mesh injection {args.action}') as s:
            cin, outs = read_state(s)
            print('BEFORE:')
            print(fmt_state(cin, outs))

            if args.action == 'status':
                return 0

            want_in = (args.action == 'on')
            want_out = {ch: (args.action == 'on' and ch in args.outputs)
                        for ch in OUT_CHS}

            # --- input ch0 (trigger source + injection timing) ---
            new_gd = cin['enable_gd']
            new_gate = cin['gate']
            new_delay = cin['delay']
            if args.delay is not None:
                new_gd, new_delay = True, args.delay
            if args.gate is not None:
                new_gate = args.gate
            if (cin['status'] != want_in or new_gd != cin['enable_gd']
                    or new_gate != cin['gate'] or new_delay != cin['delay']):
                s.call('set_input_channel_configuration', SECTION, TRIG_IN,
                       want_in, new_gd, new_gate, new_delay, cin['invert'])

            # --- outputs 0-3 ---
            for ch in OUT_CHS:
                c = outs[ch]
                if c['status'] != want_out[ch]:
                    s.call('set_output_channel_configuration', SECTION, ch,
                           want_out[ch], c['enable_mono'], c['mono_value'], c['invert'])

            # --- read-back verify ---
            cin2, outs2 = read_state(s)
            print('\nAFTER:')
            print(fmt_state(cin2, outs2))

            bad = []
            if cin2['status'] != want_in:
                bad.append(f'in{TRIG_IN} status {cin2["status"]} != {want_in}')
            if args.delay is not None and cin2['delay'] != args.delay:
                bad.append(f'in{TRIG_IN} delay {cin2["delay"]} != {args.delay}')
            for ch in OUT_CHS:
                if outs2[ch]['status'] != want_out[ch]:
                    bad.append(f'out{ch} status {outs2[ch]["status"]} != {want_out[ch]}')
            if bad:
                print('\nVERIFY FAILED: ' + '; '.join(bad))
                return 2
            print('\nVERIFY OK')
            return 0
    except BoardBusyError as e:
        print(f'board busy (another process holds .245): {e}')
        return 3
    except BoardWedgedError as e:
        print(f'BOARD WEDGED — leave .245 alone for hours: {e}')
        return 4


if __name__ == '__main__':
    sys.exit(main())
