#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_zs_kladder.py — short BEAM k-sigma ladder to find the live suppression knee
(2026-07-19). k3.0 flooded (full-Raw) because the pedestals are beam-off and the in-beam
noise floor is ~10-20x higher; this ladder finds the lowest k that actually suppresses in
beam = the "keep most hits" operating point. Then the IPD scan runs at that k.

Fixed: doubles+PS trigger (already on the boards), latency 34, HV (resist 550 / drift 700),
IPD 100. Scans PEDESTALS (k set) per sub-run. Watch event size collapse full-Raw -> suppressed.

Reuses the validated ZS machinery (Option B: Pd=0 + offline pedestal subtraction).
"""
from run_config_beam import Config as BeamConfig

K_LADDER    = [5, 8, 12, 25]        # sigma multipliers to scan (prepped sets exist)
FIXED_IPD   = 100                   # safe IPD while we probe suppression
LATENCY     = 34
SUBRUN_MIN  = 2
PED_SET_FMT = 'zs_k{ktag}_tracer_from_07-18-26_14-06-43'   # ktag: 5,8,12,25 (g-format)

# HV operating point (operator-set): all resists 550, all drifts 700. See A_drift spark note.
RESIST_ABC, DET_D_OFFSET, DRIFT_BCD, DRIFT_A = 550, 0, 700, 700


def _ped_set(k):
    return PED_SET_FMT.format(ktag=f'{k:g}')


class Config(BeamConfig):
    def _set_defaults(self, config_path=None):
        super()._set_defaults(config_path)

        self.run_name = 'zs_kladder'
        self.run_out_dir = f'{self.data_out_dir}{self.run_name}/'
        self.dream_daq_info['run_directory'] = f'/home/mx17/july_dream/dream_run/{self.run_name}/'
        self.dream_daq_info['data_out_dir'] = f'{self.run_out_dir}'
        self.processor_info['run_dir'] = f'{self.run_out_dir}'
        self.hv_info['run_out_dir'] = self.run_out_dir
        self.resume = False
        self.trigger = (f'ZS BEAM k-ladder: scint-DOUBLES + PS-pickup, latency {LATENCY}, IPD '
                        f'{FIXED_IPD}, HV resist {RESIST_ABC}/drift {DRIFT_BCD}. Scans k={K_LADDER} '
                        f'(Pd=0/CM=1, Option B) to find the live suppression knee.')

        self.dream_daq_info.update({
            'daq_config_template_path': f'{self.base_out_dir}dream_config/Tcm_Mx17_July_ZS.cfg',
            'zero_suppress': True,
            'common_noise_subtraction': True,
            'pedestal_subtraction': False,     # Option B (validated)
            'zs_type': 'tpc',
            'zs_check_sample': 4,
            'inter_packet_delay': FIXED_IPD,
            'pedestals_dir': f'{self.base_out_dir}pedestals/',
            'pedestals': _ped_set(K_LADDER[0]),   # default; each sub-run overrides
            'latency': LATENCY,
        })

        def _resist():
            return {'1': RESIST_ABC, '2': RESIST_ABC, '3': RESIST_ABC, '4': RESIST_ABC - DET_D_OFFSET}

        def _drift():
            return {'0': DRIFT_A, '1': DRIFT_BCD, '2': DRIFT_BCD, '3': DRIFT_BCD}

        # one sub-run per k; override pedestals (the k set), fixed IPD/HV
        self.sub_runs = []
        for k in K_LADDER:
            self.sub_runs.append({
                'sub_run_name': f'kladder_k{k:g}_ipd{FIXED_IPD}',
                'run_time': SUBRUN_MIN, 'post_pause_s': 0,
                'pedestals': _ped_set(k),          # per-sub-run pedestal-set override
                'inter_packet_delay': FIXED_IPD,
                'hvs': {'5': _resist(), '9': _drift()},
            })

        # merge scint PMT holds (Y88 set) into every sub-run
        scint_hvs = {}
        for det in self.detectors:
            if det['name'] not in self.included_detectors:
                continue
            if not str(det.get('det_type', '')).startswith('scintillator'):
                continue
            hv_channels = det.get('hv_channels'); setpoint = det.get('hv_setpoint')
            if not isinstance(hv_channels, dict) or setpoint is None:
                continue
            for slot, channel in hv_channels.values():
                scint_hvs.setdefault(str(slot), {})[str(channel)] = setpoint
        for sub_run in self.sub_runs:
            hvs = sub_run.setdefault('hvs', {})
            for slot, chans in scint_hvs.items():
                hvs.setdefault(slot, {}).update(chans)


if __name__ == '__main__':
    config = Config()
    config.write_to_file('config/json_run_configs/run_config_zs_kladder.json')
    dd = config.dream_daq_info
    print('=== ZS beam k-ladder ===')
    print(f"run_name : {config.run_name}   latency {dd['latency']}  IPD {FIXED_IPD}  "
          f"HV resist {RESIST_ABC}/drift {DRIFT_BCD}  (Pd={dd['pedestal_subtraction']} CM={dd['common_noise_subtraction']})")
    for sr in config.sub_runs:
        print(f"   {sr['sub_run_name']:22s} pedestals={sr['pedestals']}")
    print('Launch: .venv/bin/python daq_control.py run_config_zs_kladder.json')
