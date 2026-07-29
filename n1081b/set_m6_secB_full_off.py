#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""set_m6_secB_full_off.py — take the mesh charge-injection circuit (M6/.245 SEC_B)
FULLY dead: every input channel 0-5 AND every output channel 0-3 disabled.

WHY THIS EXISTS (2026-07-22). `set_mesh_injection.py off` only clears SEC_B **in0** and
outs 0-3; it leaves in1/in2/in3 as found. On 2026-07-22 SEC_B was found with in1 (gd,
gate 50, delay 40) and in3 (gate 50) still ENABLED, so "mesh off" was really "mesh
half-off".

That matters because of the SiPM wall-gain coupling
(docs/HANDOFF_2026-07-22_sipm_wall_dropouts.md): with M6.B outputs disabled but the input
still being pulsed, ALL FOUR walls collapse to ~1/40 gain, which kills the wall leg of the
wall AND plastic Singles coincidence and leaves ~1 event per beam pulse (MEASURED on
run_67: mesh-ON 174 MB/min vs mesh-OFF 6.2 MB/min = 28x). The standing hypothesis is that
wall SiPM bias depends on the injection rail being actively pumped, so a HALF-active
circuit is the pathological state and a FULLY dead one may not be.

RESULT 2026-07-22: the walls stayed collapsed (5.9 MB/min, vs 6.2 half-off and 427 ON),
so a dead circuit is no better than a half-dead one. CAVEAT: that run cleared inputs 0-3
only, and the board has SIX inputs — SEC_B in4 stayed ENABLED, so it was not a true full
shutdown. (The four OUTPUTS 0-3 are all there are, and they were off, so the wall-collapse
observation stands; "fully off == half off" was never actually tested.) Inputs are now
cleared 0-5; re-run if that comparison matters.

The forced-toggle test (mesh_toggle_test, 2026-07-22 21:00) showed the coupling is a
PUMPED RAIL: collapse takes 5-21 s after mesh-off, recovery is <1 s after mesh-on.

*** After running this, TAKE A SHORT TEST SUB-RUN AND CHECK THE RATE before committing
hours of beam: if the walls stay collapsed you get ~1 event/pulse. Do not skip it. ***

Writes are read-back verified, and the as-found state is saved to
n1081b/snapshots/m6_secB_asfound_<stamp>.json so this is reversible
(--restore <file> puts it back).

.245 runs old firmware (2022.3.0.0) which serves get/set WITHOUT a login, so the session
is opened with require_login=False (same as the scan watcher and set_mesh_injection).

Usage:
    python n1081b/set_m6_secB_full_off.py --show
    python n1081b/set_m6_secB_full_off.py --off
    python n1081b/set_m6_secB_full_off.py --restore n1081b/snapshots/m6_secB_asfound_*.json
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
SECTION = N1081B.Section.SEC_B
# SIX channels per section on this board (0-5), NOT four. Using (0,1,2,3) on
# 2026-07-22 silently left ch4/ch5 untouched — SEC_B in4 stayed enabled during a
# supposed "full off", and SEC_C in5 survived a supposed "one input only" cleanup.
# SIX inputs (0-5) but only FOUR outputs (0-3) per section — n1081b_module_map.py
# (`_in` to range(1,6), `_out` only range(4)). Out-of-range OUTPUT reads do NOT error;
# the board returns uninitialised junk (M6.C out4 mono_value 0x01010101). Inputs 4/5 real.
IN_CHS = (0, 1, 2, 3, 4, 5)
OUT_CHS = (0, 1, 2, 3)
SNAP_DIR = os.path.join(_HERE, 'snapshots')


def read_state(s):
    ins = {ch: s.call('get_input_channel_configuration', SECTION, ch)['data'] for ch in IN_CHS}
    outs = {ch: s.call('get_output_channel_configuration', SECTION, ch)['data'] for ch in OUT_CHS}
    return ins, outs


def show(ins, outs, title):
    print(f'\n{title}')
    for ch in IN_CHS:
        c = ins[ch]
        print(f'  in{ch} : status={c["status"]!s:5s} gd={c["enable_gd"]!s:5s} '
              f'gate={c["gate"]:>6} delay={c["delay"]:>6} invert={c["invert"]}')
    for ch in OUT_CHS:
        c = outs[ch]
        print(f'  out{ch}: status={c["status"]!s:5s} mono={c["enable_mono"]!s:5s} '
              f'mono_value={c["mono_value"]:>6} invert={c["invert"]}')


def main():
    ap = argparse.ArgumentParser(description='M6.B mesh circuit: full off / restore')
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--show', action='store_true')
    g.add_argument('--off', action='store_true')
    g.add_argument('--restore', metavar='SNAPSHOT_JSON')
    args = ap.parse_args()

    try:
        with board_session(IP, purpose='M6.B mesh circuit full off/restore',
                           require_login=False) as s:
            ins, outs = read_state(s)
            show(ins, outs, 'AS FOUND:')
            if args.show:
                return 0

            os.makedirs(SNAP_DIR, exist_ok=True)
            stamp = time.strftime('%Y-%m-%d_%H-%M-%S')
            snap = os.path.join(SNAP_DIR, f'm6_secB_asfound_{stamp}.json')
            with open(snap, 'w') as fh:
                json.dump({'ip': IP, 'section': 'SEC_B', 'saved': stamp,
                           'inputs': {str(k): v for k, v in ins.items()},
                           'outputs': {str(k): v for k, v in outs.items()}}, fh, indent=2)
            print(f'\nas-found snapshot -> {snap}')

            if args.restore:
                tgt = json.load(open(args.restore))
                want_in = {int(k): v for k, v in tgt['inputs'].items()}
                want_out = {int(k): v for k, v in tgt['outputs'].items()}
            else:
                want_in = {ch: dict(ins[ch], status=False) for ch in IN_CHS}
                want_out = {ch: dict(outs[ch], status=False) for ch in OUT_CHS}

            for ch in IN_CHS:                    # inputs first: stop the source
                w, c = want_in[ch], ins[ch]
                if (c['status'] != w['status'] or c['enable_gd'] != w['enable_gd']
                        or c['gate'] != w['gate'] or c['delay'] != w['delay']):
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
            print('\nverified: every SEC_B input and output is at the requested state.')
    except (BoardBusyError, BoardWedgedError) as e:
        print(f'BOARD UNAVAILABLE: {e}')
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
