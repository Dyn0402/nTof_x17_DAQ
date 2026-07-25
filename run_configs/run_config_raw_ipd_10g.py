#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_raw_ipd_10g.py — post-10GbE-switch RAW readout verification (2026-07-22).

The direct A/B counterpart to run_config_raw_ipd_ladder.py, which measured the 1 GbE
baseline earlier today. Everything is held identical to that run so the comparison is
clean: n32 RAW, latency 35, sample_period 60, PS+singles co-framed beam trigger,
HV drift 600 / resist 530, 3 min/point, brackets at IPD 100 at both ends.

    .venv/bin/python run_config_raw_ipd_10g.py --smoke   # 1 point, IPD 10, 3 min
    .venv/bin/python run_config_raw_ipd_10g.py           # full ladder, ~21 min

RUN THE SMOKE POINT FIRST. It is a single 3-minute RAW subrun at IPD 10 — the value the
bandwidth model says should now be comfortably clean and which was catastrophically
corrupt (99% eventId gaps) on 1 GbE. It answers "did the upgrade work at all?" before you
spend 21 minutes of beam on the ladder, and it is also the run during which you should
capture frame sizes:

    sudo docs/network_upgrade_10g/scripts/jumbo_capture.sh     # while the smoke point runs

WHAT "GOOD READS" MEANS — decided before the measurement (lib_deadtime.py criteria):

    CLEAN   eventId gap fraction  <= 0.1%   AND   cross-FEU count spread <= 2%
    CORRUPT either of those exceeded

1 GbE BASELINE (measured 2026-07-22, same config):

    IPD | ev/spill | ev/spill in 4-10ms | eventId gaps | verdict
    100 |   30.4   |      11.00         |   0.000%     | clean
     75 |   36.0   |      11.00         |   0.000%     | clean  <- threshold on 1 GbE
     50 |    3.8   |       1.39         |  56.3%       | CORRUPT
     30 |    7.5   |       0.68         |  99.0%       | CORRUPT
     15 |    9.4   |       1.09         |  86.8%       | CORRUPT

10 GbE PREDICTION (from the measured 311 kB/event, 04_bandwidth_model.md):

    clean threshold moves to IPD ~4-5; at IPD 10 expect 0.000% gaps and
    ~23-24 events/spill in the 4-10 ms band -- i.e. RAW reaches PARITY with today's ZS
    (23.45 ev/spill) but with FULL WAVEFORMS. That is the number to quote.

    PARTIAL (threshold lands 10-30): real gain, less than 10x. Check in this order --
      PCIe x1, jumbo not enabled, CPU/RSS, or an NBASE-T port. A threshold near 28 points
      straight at a 2.5 GbE negotiation.
    FAIL (threshold still ~75): the switch UPLINK is still 1 Gb. Check the port speed.

TRAPS carried over from the 1 GbE ladder -- both have burned this measurement before:
  * A point reading ~1.0 ev/spill is likely a SiPM WALL DROPOUT, not an IPD effect.
    Check wall gain before interpreting (sipm-wall-dropouts-0722).
  * `live_windows`-derived "cycle us" is MEANINGLESS here -- it returns intra-burst arrival
    spacing (~27 us), not the readout cycle. Trust ev/spill and band/spill only.

DO NOT run production at the measured threshold. The sub-threshold behaviour is a CLIFF,
not a slope (0.000% -> 56% gaps between IPD 75 and 50 on 1 GbE), and below it the in-band
yield COLLAPSES. Back off to IPD 10-15 whatever the ladder says.

Analysis: ~/beam_july/analysis/flash_comb/tools/raw_ipd_analysis.py
Link load: docs/network_upgrade_10g/scripts/analyze_link_load.py --iface enp4s0 \
             --line-mbps 10000     <-- NOTE --iface enp4s0, NOT eno1. The NICs swapped
                                       roles on 2026-07-22 while enp4s0 kept its name;
                                       eno1 is now CERN. See 05_as_built §3.
           The event size must still come back at 311 kB. If it does not, something other
           than the link changed and the A/B is not clean.
"""
import sys

# --- repo-root shim (run_config_beam/base live one dir up) ---
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from run_config_beam import Config as BeamConfig

LATENCY, N_SAMPLES, SUBRUN_MIN = 35, 32, 3.0
DRIFT, RESIST = 600, 530

SMOKE = '--smoke' in sys.argv
LOW = '--low' in sys.argv

# Full ladder. Brackets at 100 at both ends so a beam-intensity drift cannot fake a
# threshold. Points chosen to straddle the predicted 10 GbE threshold of IPD ~4-5, and to
# re-measure 30/15 where 1 GbE was catastrophically corrupt.
LADDER = [
    ('ipd100_a', 100),   # bracket / control -- must reproduce the 1 GbE ipd100 point
    ('ipd030',    30),   # 99.0% gaps on 1 GbE
    ('ipd015',    15),   # 86.8% gaps on 1 GbE
    ('ipd010',    10),   # the operating point we want
    ('ipd005',     5),   # predicted threshold region
    ('ipd003',     3),   # predicted first corrupt point
    ('ipd100_b', 100),   # bracket: must reproduce ipd100_a
]

# Smoke: one point at the intended operating value.
SMOKE_LADDER = [('ipd010', 10)]

# LOW ladder (2026-07-22, second pass). The first ladder was CLEAN at every point from 100
# down to 5 -- the corruption threshold was never found, so the DREAM rate limit is still
# unmeasured. This walks the only remaining ground, IPD 5 -> 1, where the modelled cycle
# approaches the IPD-INDEPENDENT floor of n x 4.83 us = 155 us/event:
#
#     IPD 5 -> 314 us   IPD 3 -> 250 us   IPD 2 -> 186 us   IPD 1 -> 186 us
#
# Two outcomes, and they mean opposite things:
#   * gaps appear      -> we finally found the LINK/readout threshold. Quote it, back off 2x.
#   * cycle FLATTENS   -> we hit the FEU-internal floor, not the network. Nothing faster than
#                         10 GbE would help, and that is a real, quotable result.
# Also re-takes ipd003, which the first pass lost to a beam dropout, and brackets IPD 100 at
# BOTH ends -- the first ladder's closing bracket was destroyed by that dropout, so it had no
# drift control at all. This one must not repeat that.
LOW_LADDER = [
    ('ipd100_a', 100),   # opening bracket / drift control
    ('ipd005',     5),   # last known-clean point, re-measured for continuity
    ('ipd003',     3),   # first pass VOID (beam dropout) -- re-take
    ('ipd002',     2),
    ('ipd001',     1),   # cycle 186 us vs a 155 us floor: only 1.2x of headroom left
    ('ipd100_b', 100),   # closing bracket: MUST reproduce ipd100_a or the ladder is void
]

if SMOKE:
    IPD_ORDER, RUN_NAME = SMOKE_LADDER, 'raw_ipd_10g_smoke'
elif LOW:
    IPD_ORDER, RUN_NAME = LOW_LADDER, 'raw_ipd_10g_low'
else:
    IPD_ORDER, RUN_NAME = LADDER, 'raw_ipd_10g'


class Config(BeamConfig):
    def _set_defaults(self, config_path=None):
        super()._set_defaults(config_path)
        self.run_name = RUN_NAME
        self.n1081b_scan = 'off'
        self.run_out_dir = f'{self.data_out_dir}{self.run_name}/'
        self.dream_daq_info['run_directory'] = f'/home/mx17/july_dream/dream_run/{self.run_name}/'
        self.dream_daq_info['data_out_dir'] = f'{self.run_out_dir}'
        self.processor_info['run_dir'] = f'{self.run_out_dir}'
        self.hv_info['run_out_dir'] = self.run_out_dir
        self.resume = False
        which = ('SMOKE (single point IPD 10)' if SMOKE else
                 'LOW ladder 5/3/2/1 bracketed by 100 (find the floor)' if LOW else
                 'ladder 100/30/15/10/5/3, bracketed by 100')
        self.trigger = (f'POST-10GbE RAW IPD {which} under PS+singles co-framed beam trigger, '
                        f'n32 lat35. Direct A/B against the 1 GbE raw_ipd_ladder measured '
                        f'2026-07-22 (clean only at IPD>=75). '
                        f'HV drift {DRIFT} / resist {RESIST}.')
        # RAW: inherit BeamConfig's raw template + zero_suppress False + pedestals 'latest'.
        self.dream_daq_info.update({
            'latency': LATENCY, 'n_samples_per_waveform': N_SAMPLES,
            'zero_suppress': False,
        })
        hv = {'5': {'1': RESIST, '2': RESIST, '3': RESIST, '4': RESIST},
              '9': {'0': DRIFT, '1': DRIFT, '2': DRIFT, '3': DRIFT}}
        self.sub_runs = []
        for label, ipd in IPD_ORDER:
            self.sub_runs.append({
                'sub_run_name': label, 'run_time': SUBRUN_MIN, 'post_pause_s': 0,
                'inter_packet_delay': ipd,
                'hvs': {k: dict(v) for k, v in hv.items()},
            })
        scint_hvs = {}
        for det in self.detectors:
            if det['name'] not in self.included_detectors: continue
            if not str(det.get('det_type', '')).startswith('scintillator'): continue
            hc, sp = det.get('hv_channels'), det.get('hv_setpoint')
            if not isinstance(hc, dict) or sp is None: continue
            for slot, ch in hc.values(): scint_hvs.setdefault(str(slot), {})[str(ch)] = sp
        for sr in self.sub_runs:
            for slot, chans in scint_hvs.items(): sr['hvs'].setdefault(slot, {}).update(chans)


if __name__ == '__main__':
    c = Config()
    out = f'config/json_run_configs/run_config_{RUN_NAME}.json'
    c.write_to_file(out)
    dd = c.dream_daq_info
    print(f'=== POST-10GbE RAW IPD {"SMOKE" if SMOKE else "ladder"} (beam trigger) ===')
    print(f"template  : {dd['daq_config_template_path']}")
    print(f"zero_suppress={dd['zero_suppress']}  n={dd['n_samples_per_waveform']}  "
          f"lat={dd['latency']}  sample_period={dd['sample_period']}  pedestals={dd['pedestals']}")
    for sr in c.sub_runs:
        ipd = sr['inter_packet_delay']
        print(f"  {sr['sub_run_name']:10s} IPD={ipd:>4}  -> model cycle "
              f"{N_SAMPLES * (4.83 + 0.998 * ipd) / 1000:6.3f} ms/event "
              f"({1000 / (N_SAMPLES * (4.83 + 0.998 * ipd) / 1000):6.0f} Hz ceiling)")
    print(f'\n{len(c.sub_runs)} sub-run(s) x {SUBRUN_MIN} min = {len(c.sub_runs)*SUBRUN_MIN:.0f} min')
    print(f'Launch: .venv/bin/python daq_control.py run_config_{RUN_NAME}.json')
    if SMOKE:
        print('While it runs: sudo docs/network_upgrade_10g/scripts/jumbo_capture.sh')
