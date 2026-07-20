#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_zs_readout.py — comb study, readout-limit tests (2026-07-19).

The comb = FEU SCA-readout deadtime draining the flash burst (dream-flash-comb-mechanism);
only NbOfSamples shrank it; ZS/IPD/MultiPack/SparseRd were null. Three follow-ups:
  A) Faster read clock: RdClk_Div 6.0 (ref, ~16.7-20.8 MHz) -> 5.5 -> 5.0 (~+20% readout).
     RISK (manual "delicate"): phases tuned for nominal divisor -> VERIFY data sanity.
  B) Fine NbOfSamples: 32/31/30/28 — is the readout linear in n (each sample counts) or
     quantized (must be a divisor of 512)?
  C) ZS vs Raw: if we're readout-limited, does ZS buy anything, or should we read Raw?
     Raw events (~2.4 ms/FEU on 1 GbE) may be network-limited > the 0.87 ms readout.

Fixed: doubles+PS, k8 (ZS runs), IPD 10, latency 34, HV 550/700, 60 ns sampling. Each sub-run
overrides one knob; measure busy-gap + events/spill (+ data sanity) per condition.
"""
from run_config_beam import Config as BeamConfig

ZS_PED_SET = 'zs_k8_tracer_from_07-18-26_14-06-43'
LATENCY, IPD_FIXED, SUBRUN_MIN = 34, 10, 3
RESIST_ABC, DRIFT = 550, 700

# (label, overrides vs the ZS/n32/rdclk-default reference)
CONDITIONS = [
    ('ref_zs_n32',   {}),                                   # ZS n32 rdclk 6.0
    ('rdclk5p5',     {'rdclk_div': 5.5}),
    ('rdclk5p0',     {'rdclk_div': 5.0}),                   # ~+20% readout
    ('n31',          {'n_samples_per_waveform': 31}),
    ('n30',          {'n_samples_per_waveform': 30}),
    ('n28',          {'n_samples_per_waveform': 28}),
    ('raw_n32',      {'zero_suppress': False}),             # ZS OFF -> full readout
]


class Config(BeamConfig):
    def _set_defaults(self, config_path=None):
        super()._set_defaults(config_path)
        self.run_name = 'zs_readout'
        self.run_out_dir = f'{self.data_out_dir}{self.run_name}/'
        self.dream_daq_info['run_directory'] = f'/home/mx17/july_dream/dream_run/{self.run_name}/'
        self.dream_daq_info['data_out_dir'] = f'{self.run_out_dir}'
        self.processor_info['run_dir'] = f'{self.run_out_dir}'
        self.hv_info['run_out_dir'] = self.run_out_dir
        self.resume = False
        self.trigger = ('ZS comb study readout tests: doubles+PS, k8, IPD10, n32. '
                        'RdClk 6/5.5/5.0 + n_samples 32/31/30/28 + ZS-vs-Raw. HV 550/700.')
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
            sr = {'sub_run_name': f'rdo_{label}', 'run_time': SUBRUN_MIN, 'post_pause_s': 0,
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
    c = Config(); c.write_to_file('config/json_run_configs/run_config_zs_readout.json')
    print('=== ZS readout study (doubles+PS, k8, IPD10) ===')
    for sr in c.sub_runs:
        print(f"  {sr['sub_run_name']:16s} n={sr.get('n_samples_per_waveform',32)} "
              f"rdclk={sr.get('rdclk_div','6.0(def)')} ZS={sr.get('zero_suppress',True)}")
    print('Launch: .venv/bin/python daq_control.py run_config_zs_readout.json')
