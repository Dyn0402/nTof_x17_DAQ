#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_zs_latency.py — does trigger LATENCY affect the comb? (2026-07-19)

Claim under test: latency (TrigLat, Dream reg 12) only picks WHICH frozen SCA columns are read
(shifts the window) and adds NO deadtime, so it should NOT change the yield/comb. Sweep
latency 3 / 34 / 60, BRACKETED with latency 34 (beam control). Check both:
  (1) latency actually took effect -> the signal sits at a different sample per latency,
  (2) the yield (events/spill) is unchanged across latency (the real claim).
Everything else fixed: doubles+PS, k8, IPD10, n32, HV 550/700.
"""
# --- repo-root shim (run_config_beam/base live one dir up) ---
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from run_config_beam import Config as BeamConfig

ZS_PED_SET = 'zs_k8_tracer_from_07-18-26_14-06-43'
IPD_FIXED, SUBRUN_MIN = 10, 2
RESIST_ABC, DRIFT = 550, 700
# bracketed: lat34 at 1/3/5 (beam tracker), lat3 at 2, lat60 at 4
LAT_ORDER = [('lat34_1', 34), ('lat3', 3), ('lat34_2', 34), ('lat60', 60), ('lat34_3', 34)]


class Config(BeamConfig):
    def _set_defaults(self, config_path=None):
        super()._set_defaults(config_path)
        self.run_name = 'zs_latency'
        self.run_out_dir = f'{self.data_out_dir}{self.run_name}/'
        self.dream_daq_info['run_directory'] = f'/home/mx17/july_dream/dream_run/{self.run_name}/'
        self.dream_daq_info['data_out_dir'] = f'{self.run_out_dir}'
        self.processor_info['run_dir'] = f'{self.run_out_dir}'
        self.hv_info['run_out_dir'] = self.run_out_dir
        self.resume = False
        self.trigger = ('ZS comb study LATENCY sweep: doubles+PS, k8, IPD10, n32. latency 3/34/60 '
                        'bracketed by 34 (beam control) — does latency touch the comb? HV 550/700.')
        self.dream_daq_info.update({
            'daq_config_template_path': f'{self.base_out_dir}dream_config/Tcm_Mx17_July_ZS.cfg',
            'zero_suppress': True, 'common_noise_subtraction': True, 'pedestal_subtraction': False,
            'zs_type': 'tpc', 'zs_check_sample': 4, 'inter_packet_delay': IPD_FIXED,
            'pedestals_dir': f'{self.base_out_dir}pedestals/', 'pedestals': ZS_PED_SET,
            'latency': 34, 'n_samples_per_waveform': 32,
        })
        hv = {'5': {'1': RESIST_ABC, '2': RESIST_ABC, '3': RESIST_ABC, '4': RESIST_ABC},
              '9': {'0': DRIFT, '1': DRIFT, '2': DRIFT, '3': DRIFT}}
        self.sub_runs = []
        for label, lat in LAT_ORDER:
            sr = {'sub_run_name': f'lat_{label}', 'run_time': SUBRUN_MIN, 'post_pause_s': 0,
                  'inter_packet_delay': IPD_FIXED, 'pedestals': ZS_PED_SET, 'latency': lat,
                  'hvs': {k: dict(v) for k, v in hv.items()}}
            self.sub_runs.append(sr)
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
    c = Config(); c.write_to_file('config/json_run_configs/run_config_zs_latency.json')
    print('=== ZS latency sweep (bracketed) ===')
    for sr in c.sub_runs:
        print(f"  {sr['sub_run_name']:12s} latency={sr['latency']}")
    print('Launch: .venv/bin/python daq_control.py run_config_zs_latency.json')
