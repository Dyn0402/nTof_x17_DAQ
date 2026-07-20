#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_zs_singles.py — do the comb findings hold on SINGLES (high rate)? (2026-07-19)

Everything so far was doubles-triggered (~6 Hz). Singles is ~100x higher rate (rate-scan:
~1700 singles / 30 ms window), so the FEU trigger FIFO is near-continuously saturated — the
comb may wash into uniform dropping rather than flash-burst teeth. Confirm: (1) is there still a
readout-limited loss, (2) does n_samples still recover yield.

Board: scint --singles --ps-pickup (set before this run). k8, IPD10, HV 550/700. Bracketed
n32/n8/n32 for beam control. NOTE: singles is a much higher DAQ load — watch for stalls; ZS keeps
events small and we write to the SSD path.
"""
from run_config_beam import Config as BeamConfig

ZS_PED_SET = 'zs_k8_tracer_from_07-18-26_14-06-43'
LATENCY, IPD_FIXED, SUBRUN_MIN = 34, 10, 2
RESIST_ABC, DRIFT = 550, 700
ORDER = [('n32_1', 32), ('n8', 8), ('n32_2', 32)]


class Config(BeamConfig):
    def _set_defaults(self, config_path=None):
        super()._set_defaults(config_path)
        self.run_name = 'zs_singles'
        self.run_out_dir = f'{self.data_out_dir}{self.run_name}/'
        self.dream_daq_info['run_directory'] = f'/home/mx17/july_dream/dream_run/{self.run_name}/'
        self.dream_daq_info['data_out_dir'] = f'{self.run_out_dir}'
        self.processor_info['run_dir'] = f'{self.run_out_dir}'
        self.hv_info['run_out_dir'] = self.run_out_dir
        self.resume = False
        self.trigger = ('ZS comb study on SINGLES: scint-singles+PS (high rate), k8, IPD10. '
                        'n32/n8/n32 bracketed — do the readout-limited comb findings hold? HV 550/700.')
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
        for label, n in ORDER:
            sr = {'sub_run_name': f'sng_{label}', 'run_time': SUBRUN_MIN, 'post_pause_s': 0,
                  'inter_packet_delay': IPD_FIXED, 'pedestals': ZS_PED_SET, 'n_samples_per_waveform': n,
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
    c = Config(); c.write_to_file('config/json_run_configs/run_config_zs_singles.json')
    print('=== ZS on SINGLES (bracketed n32/n8/n32) ===')
    for sr in c.sub_runs: print(f"  {sr['sub_run_name']:10s} n={sr['n_samples_per_waveform']}")
    print('PRE-RUN board: .venv/bin/python n1081b/trigger_mode.py scint --singles --ps-pickup')
    print('Launch: .venv/bin/python daq_control.py run_config_zs_singles.json')
