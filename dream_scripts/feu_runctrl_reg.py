#!/usr/bin/env python3
"""Read the FEU RunControl register (0x200008) back off every FEU.

WHY: this register carries two settings that are NOT in our cfg template and are therefore inherited
from whatever ran last -- so the cfg is not proof of what the hardware is doing (the standing rule,
docs/FEU_WATERMARKS_2026-07-22.md sec.3):

  * DreamRdDel (bit 22)      -- FEU manual 3.2.3: "intended for tests", DEFAULT 0. When 1 the FIRST
                               Dream Read of the train is delayed by a hardcoded 1536 core-clock
                               cycles (12.3 us @125 MHz core, 15.4 us @100 MHz). RunCtrl sets it on
                               its Constant/low-rate PEDESTAL branches; the Tg_Src_ExtSyn data branch
                               never clears it, and an unset cfg param is never written -> production
                               data runs can silently inherit 1.
  * Rd2AdcDataDel (20:16)    -- read-clock cycles the logic waits between the Dream Read strobe and
                               valid ADC data. Manual: "for the 20.8(3) MHz read clock this value is
                               usually set to 8". We now read at 25 MHz (RdClk_Div 4.0).

Register map (FEU User's Manual sec.3.2.3, RunControl @ 0x200008):
    bit    0    PedSub          pedestal subtraction
    bit    1    ComModSub       common-mode subtraction
    bit    2    ZS              zero suppression
    bit    3    ZsTyp           0 tracker / 1 TPC
    bits 6:4    ZsChkSmp
    bit    7    DrRawOvh
    bits 15:8   FeuId
    bits 20:16  Rd2AdcDataDel
    bit   21    EvTstExt
    bit   22    DreamRdDel
    bits 31:23  CmnPedOffset

Read-only slow-control UDP peek (port 1300 + FeuId), same transport as feu_trig_counters.py /
feu_main_conf.py. Safe at any time, including mid-run -- use it as the per-sub-run verifier.

Usage:
  feu_runctrl_reg.py                        # all 8 FEUs, decoded
  feu_runctrl_reg.py --expect-rddel 0       # PASS/FAIL on DreamRdDel
  feu_runctrl_reg.py --expect-adcdel 8      # PASS/FAIL on Rd2AdcDataDel
  feu_runctrl_reg.py --watch 5
"""
import argparse
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from feu_trig_counters import FEUS, peek

REG_RUNCTRL = 0x200008


def decode(v):
    return {
        'raw': v,
        'PedSub': v & 0x1,
        'CM': (v >> 1) & 0x1,
        'ZS': (v >> 2) & 0x1,
        'ZsTyp': (v >> 3) & 0x1,
        'ZsChkSmp': (v >> 4) & 0x7,
        'DrRawOvh': (v >> 7) & 0x1,
        'FeuId': (v >> 8) & 0xFF,
        'Rd2AdcDataDel': (v >> 16) & 0x1F,
        'EvTstExt': (v >> 21) & 0x1,
        'DreamRdDel': (v >> 22) & 0x1,
        'CmnPedOffset': (v >> 23) & 0x1FF,
    }


def read_feu(slot):
    fid, ip = FEUS[slot]
    row = {'feu': slot, 'error': None}
    try:
        v = peek(ip, 1300 + fid, REG_RUNCTRL)
        if v is None:
            row['error'] = 'unparseable reply'
        else:
            row.update(decode(v))
    except Exception as e:
        row['error'] = str(e)
    return row


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--feus', type=int, nargs='+', choices=sorted(FEUS))
    ap.add_argument('--expect-rddel', type=int, default=None, metavar='N',
                    help='PASS/FAIL verdict on DreamRdDel (bit 22)')
    ap.add_argument('--expect-adcdel', type=int, default=None, metavar='N',
                    help='PASS/FAIL verdict on Rd2AdcDataDel (bits 20:16)')
    ap.add_argument('--watch', type=float, default=None, metavar='SEC')
    args = ap.parse_args()

    slots = args.feus or sorted(FEUS)
    while True:
        rows = [read_feu(s) for s in slots]
        print(f"--- RunControl 0x{REG_RUNCTRL:06X}   {time.strftime('%H:%M:%S')} ---")
        print(f"{'FEU':>4} {'raw':>12} {'RdDel':>6} {'AdcDel':>7} {'Ped':>4} {'CM':>3} "
              f"{'ZS':>3} {'ZsTyp':>6} {'ChkSmp':>7} {'CmnPedOff':>10}")
        for r in rows:
            if r['error']:
                print(f"{r['feu']:>4} {'ERROR':>12}  {r['error']}")
            else:
                print(f"{r['feu']:>4} 0x{r['raw']:08X} {r['DreamRdDel']:>6} "
                      f"{r['Rd2AdcDataDel']:>7} {r['PedSub']:>4} {r['CM']:>3} {r['ZS']:>3} "
                      f"{r['ZsTyp']:>6} {r['ZsChkSmp']:>7} {r['CmnPedOffset']:>10}")

        for field, want, label in (('DreamRdDel', args.expect_rddel, 'DreamRdDel'),
                                   ('Rd2AdcDataDel', args.expect_adcdel, 'Rd2AdcDataDel')):
            if want is None:
                continue
            ok = [r for r in rows if not r['error'] and r[field] == want]
            bad = [r for r in rows if not r['error'] and r[field] != want]
            err = [r for r in rows if r['error']]
            if bad or err or not ok:
                seen = sorted({r[field] for r in bad}) if bad else []
                print(f"\nFAIL: {len(ok)}/{len(rows)} FEUs hold {label}={want}"
                      + (f"; {len(bad)} read {seen} instead -> value did NOT reach the hardware"
                         if bad else '')
                      + (f"; {len(err)} unreachable" if err else ''))
            else:
                print(f"\nPASS: all {len(ok)} FEUs hold {label}={want}")

        if args.watch is None:
            break
        time.sleep(args.watch)


if __name__ == '__main__':
    main()
