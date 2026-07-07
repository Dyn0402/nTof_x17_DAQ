#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generate the per-detector, per-DAC self-trigger .cfg files for the July self-trigger scan.

Each output is an offshoot of Tcm_Mx17_July.cfg with:
  * the self-trigger deltas (Trig Slf, loose multiplicity window, self-trig hit output on the Dreams),
  * the triggering detector's two FEUs (X & Y) set to Trg in Sys Topo, all others Dat,
  * the discriminator channel mask ENABLED: Dream registers 8 & 9 = 0x0000 (0xFFFF = channels masked
    OFF, which gave zero triggers -- see manual test 2026-07-06),
  * the global discriminator threshold set in Dream register 1 (bits 14-20 ThDAC, bit 21 sign=0 for our
    negative-polarity signals). THIS is the self-trigger threshold knob (NOT the _thr.prg / ZS files).

Hardware sample period / NbOfSamples / Dream latency are NOT set here -- the run pipeline
(make_config_from_template) drives those from run_config (sample_period=60, n_samples=32, latency=2).

Detector -> trigger FEUs (from run_config_beam.py dream_feus):
    A -> 3 (X), 4 (Y)    B -> 5, 6    C -> 7, 8    D -> 1, 2

DAC magnitudes (translated from the old 1000/700/500 _thr scan; 17.5% discriminator window,
~5.6 ADC counts/LSB, ceiling at 127): high/med/low = 127 / 105 / 89.
"""

import re
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from dream_register_reader import decode_register1, encode_register1

TEMPLATE = '/mnt/data/x17/beam_july/dream_config/Tcm_Mx17_July.cfg'
OUT_DIR = '/mnt/data/x17/beam_july/dream_config/'
OUT_FMT = 'SelfTrig_Mx17_July_{det}_dac{dac}.cfg'

DET_TRIG_FEUS = {'A': {3, 4}, 'B': {5, 6}, 'C': {7, 8}, 'D': {1, 2}}
DAC_MAGS = [127, 105, 89]                 # high / med / low discriminator threshold
BASE_DREAM1 = 0x891FD023                  # nominal MM self-trig register 1 (17.5% range, neg polarity)

# Single-value replacements (token-key -> new value). Spacing/comments preserved.
# X/Y coincidence self-trigger (X strips on one FEU, Y on the other, per detector):
#   - each connector (DREAM) asserts a HIT when >= HIT_multi channels fire (Dream*2, set below);
#   - a FEU asserts its self-trig primitive when "active connector hits > SelfTrig_Mult" -> 0 means
#     >=1 connector is enough (do NOT require more than one Dream per FEU);
#   - the TCM fires when "FEU primitives > Sys Trg MultMoreThan" -> 1 means >=2 FEUs = BOTH the X and
#     Y FEUs of the detector must fire in coincidence.
SCALAR = {
    ('Sys', 'DaqRun', 'Trig'): 'Slf',
    ('Sys', 'Trg', 'MultMoreThan'): '1',   # >=2 FEU primitives => X AND Y coincidence
    ('Sys', 'Trg', 'MultLessThan'): '17',
    ('Feu', '*', 'Feu_RunCtrl_CM'): '1',
    ('Feu', '*', 'SelfTrig_Mult'): '0',    # >=1 connector per FEU (one Dream hit is enough)
    ('Feu', '*', 'SelfTrig_DrmHitWid'): '10',
    ('Feu', '*', 'SelfTrig_CmbHitWid'): '10',
    ('Feu', '*', 'SelfTrig_Veto'): '10',
}
DREAM2_KEY = ('Feu', '*', 'Dream', '*', '2')
# Dream register 2, high word. 0x0400 = HIT_multi level 4 (bit26): >=4 channels per connector to assert
# a HIT (nearest available level to the requested 3; DREAM offers only 1/2/4/8, see manual Table 3).
# Also enables the self-trig hit output.
DREAM2_VAL = '0x0400'

TOPO_PAT = re.compile(r'^(\s*)(Sys\s+Topo\s+Feu\s+(\d+)\s+Dream\s+)(.*)$')
HW_PAT = re.compile(r'^\s*Feu\s+(?P<num>\d+)\s+(?:Feu_RunCtrl_Id|NetChan_Ip)\b')  # per-FEU hardware line


def replace_scalar(line, key, value):
    if line.lstrip().startswith('#') or not line.strip():
        return line, False
    pat = r'^(\s*' + r'\s+'.join(re.escape(t) for t in key) + r'\s+)(\S+)'
    m = re.match(pat, line)
    if not m:
        return line, False
    return line[:m.start(2)] + value + line[m.end(2):], True


def set_topo_roles(line, trig_feus):
    if line.lstrip().startswith('#'):
        return line
    m = TOPO_PAT.match(line)
    if not m:
        return line
    indent, head, num, dreams = m.group(1), m.group(2), int(m.group(3)), m.group(4)
    role = 'Trg' if num in trig_feus else 'Dat'
    dreams = re.sub(r'(\d+)(\s+)(?:Trg|Dat|Msk)',
                    lambda mm: f'{mm.group(1)}{mm.group(2)}{role}', dreams)
    return f'{indent}{head}{dreams}\n'


def dream1_words(dac_mag):
    """Dream register 1 (0x891FD023 base) with the ThDAC magnitude set, negative sign -> 'hi lo' words."""
    p = decode_register1(BASE_DREAM1)
    p['Threshold_DAC'] = f'-{dac_mag}'
    v = int(encode_register1(p), 16)
    return f'0x{(v >> 16) & 0xFFFF:04X} 0x{v & 0xFFFF:04X}'


def build_cfg(det, dac_mag, template_lines):
    trig_feus = DET_TRIG_FEUS[det]
    d1 = dream1_words(dac_mag)
    out = []
    for line in template_lines:
        for key, val in SCALAR.items():
            line, changed = replace_scalar(line, key, val)
            if changed:
                break
        line, _ = replace_scalar(line, DREAM2_KEY, DREAM2_VAL)
        # enable discriminator channels: Dream 8 & 9 -> all zero (0xFFFF = masked off)
        if re.match(r'^Feu \* Dream \*\s+8\s', line):
            line = 'Feu * Dream *  8 0x0000 0x0000 0x0000 0x0000\n'
        elif re.match(r'^Feu \* Dream \*\s+9\s', line):
            line = 'Feu * Dream *  9 0x0000 0x0000 0x0000 0x0000\n'
        # global discriminator threshold DAC in Dream register 1 (keep trailing two words)
        line = re.sub(r'^(Feu \* Dream \*\s+1\s+)0x[0-9A-Fa-f]+ 0x[0-9A-Fa-f]+( 0x0000 0x0000)',
                      rf'\g<1>{d1}\g<2>', line)
        # Read out ONLY the triggering detector's two FEUs: its Sys Topo lines become Trg,
        # every other FEU's Sys Topo + hardware (Id/IP) lines are commented out.
        mt = TOPO_PAT.match(line)
        if mt and not line.lstrip().startswith('#'):
            if int(mt.group(3)) in trig_feus:
                line = set_topo_roles(line, trig_feus)          # -> Trg
            else:
                line = '#' + line
        else:
            mh = HW_PAT.match(line)
            if mh and not line.lstrip().startswith('#') and int(mh.group('num')) not in trig_feus:
                line = '#' + line
        out.append(line)
    return out


def main():
    with open(TEMPLATE) as f:
        template_lines = f.readlines()
    for det, trig_feus in DET_TRIG_FEUS.items():
        for dac in DAC_MAGS:
            out_path = f'{OUT_DIR}{OUT_FMT.format(det=det, dac=dac)}'
            with open(out_path, 'w') as f:
                f.writelines(build_cfg(det, dac, template_lines))
            print(f'Wrote {os.path.basename(out_path)}  (Trg FEUs {sorted(trig_feus)}, ThDAC -{dac} -> Dream1 {dream1_words(dac)})')
    print('donzo')


if __name__ == '__main__':
    main()
