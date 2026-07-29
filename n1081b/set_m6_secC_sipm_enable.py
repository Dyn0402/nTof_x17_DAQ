#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""set_m6_secC_sipm_enable.py — put M6/.245 SEC_C (SiPM enable / blank fan-out) back to
its DESIGNED input state: exactly ONE enabled input (in0 = flash-window / PS trigger),
every other input disabled, all four outputs ON.

WHY (2026-07-22). n1081b_module_map.py documents SEC_C as:
    c_in  = [_in(0, src="flash-window source", NIM)] + [_unused_in(i) for i in 1..5]
    c_out = [out0 -> "SiPM enable / blank" mono 1000 invert, out1 -> same, out2/3 unused]
i.e. ONE input. The board was found on 2026-07-22 with **in1 and in3 also enabled**
(both gd=False, gate=0, delay=0 — the fingerprint of stray enables, not configured
signals). SEC_C is a FANOUT, so its output is the OR of every enabled input: those two
strays let the SiPM enable/blank fire on whatever they pick up.

Operator spec (2026-07-22): "Sec C should have only one input, which is the PS trigger.
The outputs should all be on." Note the module map says only out0/out1 are cabled
(SiPM enable x2) and out2/out3 are unused — all four are left ON per the operator, which
is harmless for uncabled legs.

WHY IT MATTERS: SEC_C drives the SiPM enable. Anything that makes it assert at the wrong
time reduces effective wall gain — the same observable as the mesh-coupled wall collapse
in docs/HANDOFF_2026-07-22_sipm_wall_dropouts.md. Whether the strays explain part of that
is an open question; see the prediction in that handoff.

As-found state is snapshotted to n1081b/snapshots/m6_secC_asfound_<stamp>.json and can be
put back with --restore. All writes are read-back verified.

.245 runs old firmware (2022.3.0.0) which serves get/set WITHOUT a login.

Usage:
    python n1081b/set_m6_secC_sipm_enable.py --show
    python n1081b/set_m6_secC_sipm_enable.py --apply
    python n1081b/set_m6_secC_sipm_enable.py --restore n1081b/snapshots/m6_secC_asfound_*.json
"""
import argparse
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from n1081b_session import board_session, BoardBusyError, BoardWedgedError  # noqa: E402
from n1081b_sdk import N1081B  # noqa: E402

IP = '192.168.10.245'
SECTION = N1081B.Section.SEC_C
# SIX inputs (0-5) but only FOUR outputs (0-3) per section — see n1081b_module_map.py
# (`_in` runs to range(1,6), `_out` only range(4)). Out-of-range OUTPUT reads do NOT
# error; the board returns uninitialised junk (M6.C out4 mono_value 0x01010101), so
# ch4/ch5 must never be treated as outputs. Inputs 4/5 ARE real.
IN_CHS = (0, 1, 2, 3, 4, 5)
OUT_CHS = (0, 1, 2, 3)
KEEP_IN = 0                 # in0 = flash-window / PS trigger — the ONLY designed input
SNAP_DIR = os.path.join(_HERE, 'snapshots')


def read_state(s):
    ins = {ch: s.call('get_input_channel_configuration', SECTION, ch)['data'] for ch in IN_CHS}
    outs = {ch: s.call('get_output_channel_configuration', SECTION, ch)['data'] for ch in OUT_CHS}
    return ins, outs


def show(ins, outs, title):
    print(f'\n{title}')
    for ch in IN_CHS:
        c = ins[ch]
        mark = '  <- KEEP (PS / flash window)' if ch == KEEP_IN else ''
        print(f'  in{ch} : status={c["status"]!s:5s} gd={c["enable_gd"]!s:5s} '
              f'gate={c["gate"]:>6} delay={c["delay"]:>6} invert={c["invert"]}{mark}')
    for ch in OUT_CHS:
        c = outs[ch]
        print(f'  out{ch}: status={c["status"]!s:5s} mono={c["enable_mono"]!s:5s} '
              f'mono_value={c["mono_value"]:>6} invert={c["invert"]}')


def main():
    ap = argparse.ArgumentParser(description='M6.C SiPM-enable fan-out: designed state')
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--show', action='store_true')
    g.add_argument('--apply', action='store_true')
    g.add_argument('--outputs-off', action='store_true',
                   help='DIAGNOSTIC: disable ALL SEC_C outputs, leaving inputs as found. '
                        'SEC_C drives the SiPM enable/blank, so this is expected to '
                        'collapse the walls — it is the single-variable test of whether '
                        'SEC_C couples to wall gain the way M6.B does. Reversible via '
                        '--restore using the snapshot this writes.')
    g.add_argument('--restore', metavar='SNAPSHOT_JSON')
    args = ap.parse_args()

    try:
        with board_session(IP, purpose='M6.C SiPM-enable input cleanup',
                           require_login=False) as s:
            ins, outs = read_state(s)
            show(ins, outs, 'AS FOUND:')
            if args.show:
                return 0

            os.makedirs(SNAP_DIR, exist_ok=True)
            stamp = time.strftime('%Y-%m-%d_%H-%M-%S')
            snap = os.path.join(SNAP_DIR, f'm6_secC_asfound_{stamp}.json')
            with open(snap, 'w') as fh:
                json.dump({'ip': IP, 'section': 'SEC_C', 'saved': stamp,
                           'inputs': {str(k): v for k, v in ins.items()},
                           'outputs': {str(k): v for k, v in outs.items()}}, fh, indent=2)
            print(f'\nas-found snapshot -> {snap}')

            if args.restore:
                tgt = json.load(open(args.restore))
                want_in = {int(k): v for k, v in tgt['inputs'].items()}
                want_out = {int(k): v for k, v in tgt['outputs'].items()}
            elif args.outputs_off:
                want_in = {ch: dict(ins[ch]) for ch in IN_CHS}          # inputs untouched
                want_out = {ch: dict(outs[ch], status=False) for ch in OUT_CHS}
            else:
                want_in = {ch: dict(ins[ch], status=(ch == KEEP_IN)) for ch in IN_CHS}
                want_out = {ch: dict(outs[ch], status=True) for ch in OUT_CHS}

            for ch in IN_CHS:
                w, c = want_in[ch], ins[ch]
                if c['status'] != w['status']:
                    s.call('set_input_channel_configuration', SECTION, ch,
                           w['status'], w['enable_gd'], w['gate'], w['delay'], w['invert'])
            for ch in OUT_CHS:
                w, c = want_out[ch], outs[ch]
                if c['status'] != w['status']:
                    s.call('set_output_channel_configuration', SECTION, ch,
                           w['status'], w['enable_mono'], w['mono_value'], w['invert'])

            ins2, outs2 = read_state(s)
            show(ins2, outs2, 'AFTER:')
            bad = [f'in{ch}' for ch in IN_CHS if ins2[ch]['status'] != want_in[ch]['status']]
            bad += [f'out{ch}' for ch in OUT_CHS if outs2[ch]['status'] != want_out[ch]['status']]
            if bad:
                print('\nVERIFY FAILED: ' + ', '.join(bad))
                return 2
            n_in = sum(1 for ch in IN_CHS if ins2[ch]['status'])
            print(f'\nverified: {n_in} input(s) enabled, '
                  f'{sum(1 for ch in OUT_CHS if outs2[ch]["status"])} output(s) on.')
    except (BoardBusyError, BoardWedgedError) as e:
        print(f'BOARD UNAVAILABLE: {e}')
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
