#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_zs_sparserd.py — comb study, SparseRd sweep (2026-07-19).

The comb is FEU SCA-readout drain of the flash trigger burst (see dream-flash-comb-mechanism).
SparseRd (Main_Conf_SparseRd, skip n-of-every samples) shortens the analog readout WITHOUT
shrinking the drift-window SPAN (just coarser time resolution) — the most promising
"keep the window" lever. Sweep SparseRd = 0 (ref), 1 (read 1/2), 3 (read 1/4) at n32.

Everything else fixed: doubles+PS, k8, IPD 10, latency 34, HV 550/700. 3 min sub-runs so the
FEU trigger counters (TrigStat/TrigAcpt/TrigDrop, read live via peek) accumulate per condition.
"""
from run_config_beam import Config as BeamConfig

ZS_PED_SET = 'zs_k8_tracer_from_07-18-26_14-06-43'
LATENCY, IPD_FIXED, SUBRUN_MIN = 34, 10, 3
RESIST_ABC, DRIFT = 550, 700
SPARSE_LADDER = [0, 1, 3]


class Config(BeamConfig):
    def _set_defaults(self, config_path=None):
        super()._set_defaults(config_path)
        self.run_name = 'zs_sparserd'
        self.run_out_dir = f'{self.data_out_dir}{self.run_name}/'
        self.dream_daq_info['run_directory'] = f'/home/mx17/july_dream/dream_run/{self.run_name}/'
        self.dream_daq_info['data_out_dir'] = f'{self.run_out_dir}'
        self.processor_info['run_dir'] = f'{self.run_out_dir}'
        self.hv_info['run_out_dir'] = self.run_out_dir
        self.resume = False
        self.trigger = ('ZS comb study SparseRd sweep: doubles+PS, k8, IPD 10, n32. '
                        'SparseRd 0/1/3 (skip-n samples) to shorten SCA readout / shrink comb '
                        'keeping window span. HV 550/700.')
        self.dream_daq_info.update({
            'daq_config_template_path': f'{self.base_out_dir}dream_config/Tcm_Mx17_July_ZS.cfg',
            'zero_suppress': True, 'common_noise_subtraction': True, 'pedestal_subtraction': False,
            'zs_type': 'tpc', 'zs_check_sample': 4, 'inter_packet_delay': IPD_FIXED,
            'pedestals_dir': f'{self.base_out_dir}pedestals/', 'pedestals': ZS_PED_SET,
            'latency': LATENCY, 'n_samples_per_waveform': 32, 'sparse_rd': 0,
        })
        hv = {'5': {'1': RESIST_ABC, '2': RESIST_ABC, '3': RESIST_ABC, '4': RESIST_ABC},
              '9': {'0': DRIFT, '1': DRIFT, '2': DRIFT, '3': DRIFT}}
        self.sub_runs = []
        for s in SPARSE_LADDER:
            self.sub_runs.append({
                'sub_run_name': f'sparse{s}_n32', 'run_time': SUBRUN_MIN, 'post_pause_s': 0,
                'inter_packet_delay': IPD_FIXED, 'pedestals': ZS_PED_SET, 'sparse_rd': s,
                'hvs': {k: dict(v) for k, v in hv.items()}})
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
    c = Config(); c.write_to_file('config/json_run_configs/run_config_zs_sparserd.json')
    print('=== ZS SparseRd sweep (n32, doubles+PS, k8, IPD10) ===')
    for sr in c.sub_runs:
        print(f"  {sr['sub_run_name']:14s} sparse_rd={sr['sparse_rd']}")
    print('Launch: .venv/bin/python daq_control.py run_config_zs_sparserd.json')
