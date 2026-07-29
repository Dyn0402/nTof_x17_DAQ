#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_raw_ipd_ladder.py — RAW readout IPD ladder under the real beam trigger, to find
how low IPD can go on 1 GbE and extrapolate the 10 GbE headroom (2026-07-22).

WHY
---
2026-07-22 established that the DOMINANT deadtime lever is Feu_InterPacket_Delay, not
NbOfSamples: per-event cycle = n x (4.83 + 0.998 x IPD) us. Going run_61 -> today
(n64/IPD100/Raw -> n32/IPD10/ZS) cut 6.64 ms/event to 0.504 ms/event, of which IPD
contributed 7.1x and NbOfSamples only 2x. That turned a comb (teeth at 4/13.3/26.6 ms) into
one continuous live window 4.5 -> 51 ms, and 4.00 -> 23.45 recorded events/spill in 4-10 ms.

But that win required ZS, which throws away sub-threshold hits. We want RAW at the same
speed. Raw is ~25x more data per event (measured: 94 kB/FEU/event at n64 raw vs 1.6 kB at
n32 ZS), and all 8 FEUs share ONE 1 GbE host link (eno1, confirmed 1000 Mb/s). That shared
link -- not the per-FEU link, not the FEU readout -- is the binding constraint:
deadtime_db.csv shows n32 Raw at IPD 16 is CLEAN with 1-2 FEUs and CORRUPT with 4-8.

PRIOR (deadtime_study 2026-07-07, n32 Raw, 8 FEUs, SATURATING internal trigger):
    IPD 4..50 -> CORRUPT ;  IPD 75..400 -> OK      => breaks between 50 and 75
That was a saturated generator. Under the real PS+singles beam trigger the duty cycle is
different (bursty: ~93 events per spill inside ~46 ms, then idle), so the true threshold
must be measured, not inherited.

WHAT THIS MEASURES
------------------
Per IPD point, with the SAME corruption criteria as the deadtime_study harness
(lib_deadtime.py): eventId gap fraction > 0.1%, or cross-FEU event-count spread > 2%.
Plus the physics figure of merit: recorded events in the 4-10 ms band per spill.

Bracketed with IPD 100 at both ends so a beam-intensity drift cannot fake a threshold.
NOTE the SiPM walls dropped out for ~7 h today; a point reading ~1.00 events/spill is a
SiPM dropout, NOT an IPD effect -- check before interpreting.

EXTRAPOLATION TO 10 GbE
-----------------------
The host link is the constraint, so the usable IPD scales ~inversely with available
aggregate bandwidth: 10 GbE should permit IPD_min(1G)/~8-10. The hard floor is the
IPD-independent term, n x 4.83 us = 155 us/event at n32 -- if that term is FEU-internal
packet formation rather than wire time, no network upgrade goes below it. The ladder
measures where the cycle stops improving, which bounds that floor directly.

TRIGGER: PS + singles co-framed (1800 ns), unchanged -- no board writes needed.
HV: explicit drift 600 / resist 530 (do NOT inherit; a pedestal run leaves them at 200 V).
"""
# --- repo-root shim (run_config_beam/base live one dir up) ---
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from run_config_beam import Config as BeamConfig

LATENCY, N_SAMPLES, SUBRUN_MIN = 35, 32, 3.0
DRIFT, RESIST = 600, 530

# IPD ladder. 100 = the safe Raw default (and the run_61 value); brackets at both ends.
IPD_ORDER = [
    ('ipd100_a', 100),   # reference / control
    ('ipd075',   75),    # last CLEAN point in the saturated 2026-07-07 ladder
    ('ipd050',   50),    # first CORRUPT point there
    ('ipd030',   30),
    ('ipd015',   15),
    ('ipd100_b', 100),   # bracket: must reproduce ipd100_a
]


class Config(BeamConfig):
    def _set_defaults(self, config_path=None):
        super()._set_defaults(config_path)
        self.run_name = 'raw_ipd_ladder'
        self.n1081b_scan = 'off'
        self.run_out_dir = f'{self.data_out_dir}{self.run_name}/'
        self.dream_daq_info['run_directory'] = f'/home/mx17/july_dream/dream_run/{self.run_name}/'
        self.dream_daq_info['data_out_dir'] = f'{self.run_out_dir}'
        self.processor_info['run_dir'] = f'{self.run_out_dir}'
        self.hv_info['run_out_dir'] = self.run_out_dir
        self.resume = False
        self.trigger = ('RAW IPD ladder (100/75/50/30/15, bracketed by 100) under PS+singles '
                        'co-framed beam trigger, n32 lat35. Finds the 1 GbE corruption '
                        'threshold for full readout and bounds the 10 GbE headroom. '
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
    c = Config(); c.write_to_file('config/json_run_configs/run_config_raw_ipd_ladder.json')
    dd = c.dream_daq_info
    print('=== RAW IPD ladder (beam trigger) ===')
    print(f"template  : {dd['daq_config_template_path']}")
    print(f"zero_suppress={dd['zero_suppress']}  n={dd['n_samples_per_waveform']}  "
          f"lat={dd['latency']}  sample_period={dd['sample_period']}  pedestals={dd['pedestals']}")
    for sr in c.sub_runs:
        ipd = sr['inter_packet_delay']
        print(f"  {sr['sub_run_name']:10s} IPD={ipd:>4}  -> model cycle "
              f"{N_SAMPLES * (4.83 + 0.998 * ipd) / 1000:6.3f} ms/event "
              f"({1000 / (N_SAMPLES * (4.83 + 0.998 * ipd) / 1000):6.0f} Hz ceiling)")
    print(f'\n{len(c.sub_runs)} sub-runs x {SUBRUN_MIN} min = {len(c.sub_runs)*SUBRUN_MIN:.0f} min')
    print('Launch: .venv/bin/python daq_control.py run_config_raw_ipd_ladder.json')
