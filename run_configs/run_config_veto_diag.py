#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_veto_diag.py — use the DREAM event rate as a TRIGGER INDICATOR to isolate
why the singles leg produces no events (2026-07-22).

Symptom: PS+singles runs record exactly 1.00 events/spill -- the flash only, zero physics.
run_63/64 (07-21) got 14.9-15.1 ev/spill on the same trigger, so this broke between
run_64 (07-21 ~21:56) and run_66 (07-22 09:12). Thresholds, HV, M4.A and M4.C all read
nominal / canonical, so the remaining split is:

    (a) the M4.C veto gate never opens  -> singles are generated but blocked
    (b) the Singles OR has no input     -> nothing to block

PS reaches the FEUs via M4.D lemo0, which BYPASSES section C. Singles are the only leg
routed through C's or_veto. So bypassing the veto separates (a) from (b) cleanly:

  PHASE 1  veto_on   C = or_veto, lemo0 (singles)   -> expect ~PS rate only (the flash)
  PHASE 2  veto_off  C = plain OR, lemo0 (singles)  -> veto line IGNORED
                     rate JUMPS  => (a) the veto gate was blocking singles
                     rate FLAT   => (b) no singles reaching M4.C at all

Run it as:
  RUN_TAG=veto_on   python run_config_veto_diag.py && \
      .venv/bin/python daq_control.py run_config_veto_diag.json
  .venv/bin/python n1081b/set_veto_open.py --lemos 0      # C -> plain OR, singles only
  RUN_TAG=veto_off  python run_config_veto_diag.py && \
      .venv/bin/python daq_control.py run_config_veto_diag.json

RESTORE AFTERWARDS (mandatory -- re-arms the veto):
  .venv/bin/python n1081b/trigger_mode.py scint --singles --ps-pickup

HV: sub-run carries no resist/drift setpoints, so HV stays where it is (no ramp, no
settle). The trigger is scintillator-derived, so detector HV is irrelevant to this test.
"""
import os
# --- repo-root shim (run_config_beam/base live one dir up) ---
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from run_config_beam import Config as BeamConfig

ZS_PED_SET = 'zs_k8_tracer_from_07-18-26_14-06-43'
LATENCY, N_SAMPLES, IPD_FIXED, SUBRUN_MIN = 35, 32, 10, 2.0
RUN_TAG = os.environ.get('RUN_TAG', 'veto_on')


class Config(BeamConfig):
    def _set_defaults(self, config_path=None):
        super()._set_defaults(config_path)
        self.run_name = f'vetodiag_{RUN_TAG}'
        self.n1081b_scan = 'off'
        self.run_out_dir = f'{self.data_out_dir}{self.run_name}/'
        self.dream_daq_info['run_directory'] = f'/home/mx17/july_dream/dream_run/{self.run_name}/'
        self.dream_daq_info['data_out_dir'] = f'{self.run_out_dir}'
        self.processor_info['run_dir'] = f'{self.run_out_dir}'
        self.hv_info['run_out_dir'] = self.run_out_dir
        self.resume = False
        self.trigger = (f'VETO DIAGNOSTIC ({RUN_TAG}): DREAM rate as trigger indicator. '
                        'PS+singles; phase 2 bypasses the M4.C veto (plain OR) to test '
                        'whether singles are blocked by the gate or absent entirely.')
        self.dream_daq_info.update({
            'daq_config_template_path': f'{self.base_out_dir}dream_config/Tcm_Mx17_July_ZS.cfg',
            'zero_suppress': True, 'common_noise_subtraction': True, 'pedestal_subtraction': False,
            'zs_type': 'tpc', 'zs_check_sample': 4, 'inter_packet_delay': IPD_FIXED,
            'pedestals_dir': f'{self.base_out_dir}pedestals/', 'pedestals': ZS_PED_SET,
            'latency': LATENCY, 'n_samples_per_waveform': N_SAMPLES,
        })
        self.sub_runs = [{
            'sub_run_name': RUN_TAG, 'run_time': SUBRUN_MIN, 'post_pause_s': 0,
            'inter_packet_delay': IPD_FIXED, 'pedestals': ZS_PED_SET, 'hvs': {},
        }]
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
    c = Config(); c.write_to_file('config/json_run_configs/run_config_veto_diag.json')
    print(f'=== veto diagnostic: {RUN_TAG} ===  1 sub-run x {SUBRUN_MIN} min, HV untouched')
