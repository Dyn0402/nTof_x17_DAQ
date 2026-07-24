#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_zs_control.py — controlled re-run to kill the beam-intensity confound (2026-07-19).

The busy-gap metric = (events per tooth) x (readout per event), so it picks up beam intensity
via the tooth size — that's why n28 read HIGH earlier. Here we BRACKET each n28 between n32s
(controls beam drift) and bracket the Raw sub-run between ZS-n32s (controls ZS-vs-Raw for beam).
Analysis normalizes per-event: readout/event = drain-gap / events-in-preceding-tooth (beam-indep).

Fixed: doubles+PS, k8 (ZS), IPD 10, latency 34, HV 550/700. Order interleaves the variables.
"""
# --- repo-root shim (run_config_beam/base live one dir up) ---
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from run_config_beam import Config as BeamConfig

ZS_PED_SET = 'zs_k8_tracer_from_07-18-26_14-06-43'
LATENCY, IPD_FIXED, SUBRUN_MIN = 34, 10, 2
RESIST_ABC, DRIFT = 550, 700

# bracketed order: n32 appears at 1/3/5 (beam tracker + ZS ref); n28 at 2/6; raw at 4 (between n32s)
CONDITIONS = [
    ('n32_1', {'n_samples_per_waveform': 32}),
    ('n28_1', {'n_samples_per_waveform': 28}),
    ('n32_2', {'n_samples_per_waveform': 32}),
    ('raw_1', {'n_samples_per_waveform': 32, 'zero_suppress': False}),
    ('n32_3', {'n_samples_per_waveform': 32}),
    ('n28_2', {'n_samples_per_waveform': 28}),
]


class Config(BeamConfig):
    def _set_defaults(self, config_path=None):
        super()._set_defaults(config_path)
        self.run_name = 'zs_control'
        self.run_out_dir = f'{self.data_out_dir}{self.run_name}/'
        self.dream_daq_info['run_directory'] = f'/home/mx17/july_dream/dream_run/{self.run_name}/'
        self.dream_daq_info['data_out_dir'] = f'{self.run_out_dir}'
        self.processor_info['run_dir'] = f'{self.run_out_dir}'
        self.hv_info['run_out_dir'] = self.run_out_dir
        self.resume = False
        self.trigger = ('ZS comb study CONTROLLED re-run: bracketed n32/n28 + bracketed ZS/Raw '
                        '(beam-confound control), doubles+PS, k8, IPD10, latency 34, HV 550/700.')
        self.dream_daq_info.update({
            'daq_config_template_path': f'{self.base_out_dir}dream_config/Tcm_Mx17_July_ZS.cfg',
            'zero_suppress': True, 'common_noise_subtraction': True, 'pedestal_subtraction': False,
            'zs_type': 'tpc', 'zs_check_sample': 4, 'inter_packet_delay': IPD_FIXED,
            'pedestals_dir': f'{self.base_out_dir}pedestals/', 'pedestals': ZS_PED_SET,
            'latency': LATENCY, 'n_samples_per_waveform': 32,
        })
        hv = {'5': {'1': RESIST_ABC, '2': RESIST_ABC, '3': RESIST_ABC, '4': RESIST_ABC},
              '9': {'0': DRIFT, '1': DRIFT, '2': DRIFT, '3': DRIFT}}
        self.sub_runs = []
        for label, ov in CONDITIONS:
            sr = {'sub_run_name': f'ctl_{label}', 'run_time': SUBRUN_MIN, 'post_pause_s': 0,
                  'inter_packet_delay': IPD_FIXED, 'pedestals': ZS_PED_SET,
                  'hvs': {k: dict(v) for k, v in hv.items()}}
            sr.update(ov)
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
    c = Config(); c.write_to_file('config/json_run_configs/run_config_zs_control.json')
    print('=== ZS controlled re-run (bracketed) ===')
    for sr in c.sub_runs:
        print(f"  {sr['sub_run_name']:10s} n={sr.get('n_samples_per_waveform')} ZS={sr.get('zero_suppress',True)}")
    print('Launch: .venv/bin/python daq_control.py run_config_zs_control.json')
