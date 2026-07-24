#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_zs_vetolen.py — post-trigger dead time (TrigVetoLen) sweep (2026-07-19).

Hypothesis: the γ-flash trips ~11 triggers in 57 µs (~5 µs spacing) — if that is trigger
"ringing" (re-fires of one physical flash), a per-trigger holdoff (Trig_Conf_TrigVetoLen) would
reject the close ones, collapse the burst, and free the FEU to keep the physics. Sweep the veto
0 / 250 / 500 / 1000 trigger-clock periods (~0 / 2.5 / 5 / 10 µs), bracketed with veto 0.
Watch (a) the FLASH-BURST size (events in tooth-1 per flash — should shrink if it's ringing) and
(b) the physics yield (tail teeth — should rise if deadtime frees up). Cost if it's real
multiplicity, not ringing: genuine close physics triggers are also lost.

Board: doubles+PS (unchanged). k8, IPD10, n32, HV 550/700.
"""
# --- repo-root shim (run_config_beam/base live one dir up) ---
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from run_config_beam import Config as BeamConfig

ZS_PED_SET = 'zs_k8_tracer_from_07-18-26_14-06-43'
LATENCY, IPD_FIXED, SUBRUN_MIN = 34, 10, 2
RESIST_ABC, DRIFT = 550, 700
VETO_ORDER = [('v0_a', 0), ('v250', 250), ('v500', 500), ('v1000', 1000), ('v0_b', 0)]


class Config(BeamConfig):
    def _set_defaults(self, config_path=None):
        super()._set_defaults(config_path)
        self.run_name = 'zs_vetolen'
        self.run_out_dir = f'{self.data_out_dir}{self.run_name}/'
        self.dream_daq_info['run_directory'] = f'/home/mx17/july_dream/dream_run/{self.run_name}/'
        self.dream_daq_info['data_out_dir'] = f'{self.run_out_dir}'
        self.processor_info['run_dir'] = f'{self.run_out_dir}'
        self.hv_info['run_out_dir'] = self.run_out_dir
        self.resume = False
        self.trigger = ('ZS comb study TrigVetoLen sweep: doubles+PS, k8, IPD10, n32. per-trigger '
                        'holdoff 0/250/500/1000 periods (~0/2.5/5/10 us) to test flash-ringing '
                        'rejection. HV 550/700.')
        self.dream_daq_info.update({
            'daq_config_template_path': f'{self.base_out_dir}dream_config/Tcm_Mx17_July_ZS.cfg',
            'zero_suppress': True, 'common_noise_subtraction': True, 'pedestal_subtraction': False,
            'zs_type': 'tpc', 'zs_check_sample': 4, 'inter_packet_delay': IPD_FIXED,
            'pedestals_dir': f'{self.base_out_dir}pedestals/', 'pedestals': ZS_PED_SET,
            'latency': LATENCY, 'n_samples_per_waveform': 32, 'trig_veto_len': 0,
        })
        hv = {'5': {'1': RESIST_ABC, '2': RESIST_ABC, '3': RESIST_ABC, '4': RESIST_ABC},
              '9': {'0': DRIFT, '1': DRIFT, '2': DRIFT, '3': DRIFT}}
        self.sub_runs = []
        for label, v in VETO_ORDER:
            sr = {'sub_run_name': f'veto_{label}', 'run_time': SUBRUN_MIN, 'post_pause_s': 0,
                  'inter_packet_delay': IPD_FIXED, 'pedestals': ZS_PED_SET, 'trig_veto_len': v,
                  'hvs': {k: dict(v2) for k, v2 in hv.items()}}
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
    c = Config(); c.write_to_file('config/json_run_configs/run_config_zs_vetolen.json')
    print('=== ZS TrigVetoLen sweep (doubles) ===')
    for sr in c.sub_runs:
        print(f"  {sr['sub_run_name']:11s} TrigVetoLen={sr['trig_veto_len']}")
    print('Launch: .venv/bin/python daq_control.py run_config_zs_vetolen.json')
