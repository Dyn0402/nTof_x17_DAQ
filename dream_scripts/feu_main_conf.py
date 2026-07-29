#!/usr/bin/env python3
"""Read Main_Conf (0x100004) back off every FEU -- the SparseRd hardware proof.

WHY THIS EXISTS: `Main_Conf_SparseRd` has been "swept" twice and been null twice, and
neither null is trustworthy.

  * 2026-07-19 (`zs_sparserd`) -- the value never reached the cfg at all (stale
    dream_daq_control server). See docs/FEU_WATERMARKS_2026-07-22.md sec.3.
  * 2026-07-22 (`sparse_mp`)  -- the value DID reach the cfg (archived cfgs read
    SparseRd 0/1/3/7 correctly), yet the recorded data volume was flat to 0.3%
    (1.523 / 1.526 / 1.527 / 1.528 GB). Nobody ever read the register off the hardware.

Rule 3 of that document: for FEU registers the cfg is NOT the last word, because RunCtrl
rewrites some values at configure time (it demonstrably does this to the trigger-FIFO
watermarks, Main_Conf_Samples and RdClk_Div). So "SparseRd does nothing" is STILL an open
question: either the FEU ignores the setting, or RunCtrl is stamping it back to 0.

This script settles it. Run it WHILE a sub-run with a non-zero sparse_rd is live.

  * register holds the requested n -> SparseRd is real and genuinely has no rate effect.
  * register reads 0        -> RunCtrl clamped it; the knob has never actually been tested.

Register map (FEU User's Manual sec.3.1.2, Main_Conf @ 0x100004):
    bits 19:17   SparseRd     0 = read all samples, n=[1..7] = skip n samples
    bits 15:8    Samples      Main_Conf_Samples (RunCtrl copies Sys NbOfSamples here)

Protocol is the same read-only slow-control UDP peek used by feu_trig_counters.py
(port 1300 + FeuId). Peeks are cheap and safe at any time, including mid-run.

Usage:
  feu_main_conf.py                     # all 8 FEUs, one shot
  feu_main_conf.py --expect 3          # PASS/FAIL against a requested SparseRd
  feu_main_conf.py --watch 5           # re-read every 5 s
  feu_main_conf.py --feus 1 2
"""
import argparse
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from feu_trig_counters import FEUS, peek

REG_MAIN_CONF = 0x100004


def decode_main_conf(v):
    """Unpack the Main_Conf fields we care about."""
    return {
        'raw': v,
        'SparseRd': (v >> 17) & 0x7,
        'Samples': (v >> 8) & 0xFF,
    }


def read_feu(slot):
    fid, ip = FEUS[slot]
    port = 1300 + fid
    row = {'feu': slot, 'error': None}
    try:
        v = peek(ip, port, REG_MAIN_CONF)
        if v is None:
            row['error'] = 'unparseable reply'
        else:
            row.update(decode_main_conf(v))
    except Exception as e:
        row['error'] = str(e)
    return row


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--feus', type=int, nargs='+', choices=sorted(FEUS),
                    help='FEU slots to read (default: all)')
    ap.add_argument('--expect', type=int, default=None, metavar='N',
                    help='requested SparseRd; print a PASS/FAIL verdict against it')
    ap.add_argument('--watch', type=float, default=None, metavar='SEC',
                    help='re-read every SEC seconds until Ctrl-C')
    args = ap.parse_args()

    slots = args.feus or sorted(FEUS)

    while True:
        rows = [read_feu(s) for s in slots]
        print(f"--- Main_Conf 0x{REG_MAIN_CONF:06X}   {time.strftime('%H:%M:%S')} ---")
        print(f"{'FEU':>4} {'raw':>12} {'SparseRd':>9} {'Samples':>8}")
        for r in rows:
            if r['error']:
                print(f"{r['feu']:>4} {'ERROR':>12}  {r['error']}")
            else:
                print(f"{r['feu']:>4} 0x{r['raw']:08X}   {r['SparseRd']:>8} {r['Samples']:>8}")

        if args.expect is not None:
            good = [r for r in rows if not r['error'] and r['SparseRd'] == args.expect]
            bad = [r for r in rows if not r['error'] and r['SparseRd'] != args.expect]
            err = [r for r in rows if r['error']]
            if bad or err or not good:
                print(f"\nFAIL: {len(good)}/{len(rows)} FEUs hold SparseRd={args.expect}.")
                if bad:
                    seen = sorted({r['SparseRd'] for r in bad})
                    print(f"  {len(bad)} FEU(s) read {seen} instead -> the knob did NOT reach "
                          f"the hardware (RunCtrl clamp?). Any SparseRd null is UNSUPPORTED.")
                if err:
                    print(f"  {len(err)} FEU(s) unreachable.")
            else:
                print(f"\nPASS: all {len(good)} FEUs hold SparseRd={args.expect} "
                      f"-> the knob IS live; a flat rate is a real null.")

        if args.watch is None:
            break
        time.sleep(args.watch)


if __name__ == '__main__':
    main()
