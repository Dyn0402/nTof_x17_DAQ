#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_zs_comb_study.py — systematic comb study, CONFIG-ONLY conditions (2026-07-19).

Goal: find what closes the flash-driven ~10 ms "DAQ comb" (events pile at 0/~10/~20 ms since
flash, dead in between). Established so far: comb is IPD-INDEPENDENT (2-100) and ksigma-
INDEPENDENT (k5-25) -> not the small-event readout; it's the readout of the flash-triggered
BURST (11 triggers in 57 us, several full-size, wire-limited on 1 GbE). This run sweeps the
DAQ-side knobs that could shorten that burst readout, one variable at a time, everything else
fixed (k8, IPD 10, flash ON, HV as-is). PS-veto (kill the flash burst) is a SEPARATE run
(needs a board change).

Each sub-run overrides one knob; measure time-since-flash comb per sub-run (analysis script).
n_samples is dropped "arbitrarily for testing" (framing irrelevant to the comb, which is about
event TIMING). MultiPackThr pushed toward/over the FEU 8192 cap (operator OK losing last packet).
"""
from run_config_beam import Config as BeamConfig

ZS_PED_SET = 'zs_k8_tracer_from_07-18-26_14-06-43'
LATENCY, IPD_FIXED, SUBRUN_MIN = 34, 10, 2
RESIST_ABC, DRIFT = 550, 700   # HV held (as left by the IPD scan)

# (label, overrides) — each isolates one knob vs the reference
CONDITIONS = [
    ('ref_n32_mpt4888',  {'n_samples_per_waveform': 32, 'multipack_thr': 4888, 'multipack_enb': True}),
    ('mpt6000',          {'n_samples_per_waveform': 32, 'multipack_thr': 6000, 'multipack_enb': True}),
    ('mpt8188',          {'n_samples_per_waveform': 32, 'multipack_thr': 8188, 'multipack_enb': True}),  # register max (11-bit x4), past-safe -> drops last packet
    ('multipack_off',    {'n_samples_per_waveform': 32, 'multipack_enb': False}),
    ('n16',              {'n_samples_per_waveform': 16, 'multipack_thr': 4888, 'multipack_enb': True}),
    ('n8',               {'n_samples_per_waveform': 8,  'multipack_thr': 4888, 'multipack_enb': True}),
]


class Config(BeamConfig):
    def _set_defaults(self, config_path=None):
        super()._set_defaults(config_path)
        self.run_name = 'zs_comb_study'
        self.run_out_dir = f'{self.data_out_dir}{self.run_name}/'
        self.dream_daq_info['run_directory'] = f'/home/mx17/july_dream/dream_run/{self.run_name}/'
        self.dream_daq_info['data_out_dir'] = f'{self.run_out_dir}'
        self.processor_info['run_dir'] = f'{self.run_out_dir}'
        self.hv_info['run_out_dir'] = self.run_out_dir
        self.resume = False
        self.trigger = ('ZS comb study (config-only): scint-DOUBLES + PS-pickup, k8, IPD 10, '
                        'flash ON. Sweeps MultiPackThr/Enb + n_samples to find what shortens the '
                        'flash-burst readout / closes the comb. HV 550/700 fixed.')
        self.dream_daq_info.update({
            'daq_config_template_path': f'{self.base_out_dir}dream_config/Tcm_Mx17_July_ZS.cfg',
            'zero_suppress': True, 'common_noise_subtraction': True, 'pedestal_subtraction': False,
            'zs_type': 'tpc', 'zs_check_sample': 4, 'inter_packet_delay': IPD_FIXED,
            'pedestals_dir': f'{self.base_out_dir}pedestals/', 'pedestals': ZS_PED_SET,
            'latency': LATENCY, 'n_samples_per_waveform': 32,
            'multipack_thr': 4888, 'multipack_enb': True,
        })
        hv = {'5': {'1': RESIST_ABC, '2': RESIST_ABC, '3': RESIST_ABC, '4': RESIST_ABC},
              '9': {'0': DRIFT, '1': DRIFT, '2': DRIFT, '3': DRIFT}}
        self.sub_runs = []
        for label, ov in CONDITIONS:
            sr = {'sub_run_name': f'comb_{label}', 'run_time': SUBRUN_MIN, 'post_pause_s': 0,
                  'inter_packet_delay': IPD_FIXED, 'pedestals': ZS_PED_SET,
                  'hvs': {k: dict(v) for k, v in hv.items()}}
            sr.update(ov)
            self.sub_runs.append(sr)
        # merge scint PMT holds
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
    c = Config(); c.write_to_file('config/json_run_configs/run_config_zs_comb_study.json')
    print('=== ZS comb study (config-only), k8/IPD10/flash-on ===')
    for sr in c.sub_runs:
        print(f"  {sr['sub_run_name']:22s} n_samp={sr.get('n_samples_per_waveform')} "
              f"MPT={sr.get('multipack_thr')} MPEnb={sr.get('multipack_enb')}")
    print('Launch: .venv/bin/python daq_control.py run_config_zs_comb_study.json')
