#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_hwm_beam.py — BEAM test: maximize recorded events in the 4-10 ms
time-since-flash band by sweeping the FEU trigger-FIFO high water mark (2026-07-22).

THE PHYSICS TARGET
------------------
The in-gate IPC production spectrum peaks at **t = 5.3 ms** since the gamma flash
(thermal peak, E ~ 71 meV) -- see analysis/flash_comb/ipc_vs_runs/. In the current
production config that peak falls in a DEAD gap: the flash latches a full burst of
triggers at t=0, the FEU reads them out as one long block, and the TCM vetoes
everything until the block finishes. run_61 recovered only 1.7% of the in-gate IPC.

THE LEVER (new, 2026-07-22)
---------------------------
`Main_Trig_OvrWrnHwm` caps how many triggers one burst can seize. Measured beam-off:
`maxFIFOocc == Hwm` exactly, and sustained throughput is FLAT from Hwm 11 down to
Hwm 3 (3079 Hz, identical), falling only at Hwm 1 (2001 Hz). See
docs/FEU_WATERMARKS_2026-07-22.md.

So a lower Hwm should truncate the post-flash blackout at ~Hwm x t_readout and let the
DAQ come back live EARLIER -- ideally before 4 ms, so the 5.3 ms thermal peak lands in
a live window instead of a dead gap. Rough expectation at these settings
(per-event cycle ~0.3-0.8 ms depending on ZS/IPD):

    Hwm 11 -> first live ~3.6-8.8 ms   (peak likely still dead)
    Hwm  6 -> ~2.0-4.8 ms
    Hwm  3 -> ~1.0-2.4 ms              (expected sweet spot: live well before 4 ms,
                                        then short teeth cadence across 4-10 ms)
    Hwm  1 -> ~0.3-0.8 ms              (earliest, but -35% sustained throughput)

The detector is flash-blind for the first ~4 ms anyway (run_61), so coming live much
before ~4 ms buys nothing -- which is why Hwm 2-3 is the predicted optimum rather
than Hwm 1. THAT TRADE-OFF IS THE POINT OF THIS SCAN.

CRITICALLY: this is an internally controlled scan. Every point is identical except
Hwm, and the Hwm=11 control is run FIRST and LAST to bracket drift (beam intensity,
gas, HV all move on ~hour timescales).

TRIGGER: PS + singles, co-framed (M4.D in0 delay 1800 ns) -- the standard run_56/58/61
recipe, so the flash marks t=0 in the same way the ipc_vs_runs reference plots assume.
No board changes needed if trigger_mode.py scint --singles --ps-pickup is already set.

HV: set EXPLICITLY here. Do not inherit -- a pedestal run leaves drift/resist at 200 V.

ANALYSIS: feed the sub-run dirs to
  ~/beam_july/analysis/flash_comb/tools/ipc_spectrum_vs_runs.py
(one plot per point: IPC spectrum + live windows + recorded events + integral), then
compare the 4-10 ms integrated fraction across Hwm.
"""
# --- repo-root shim (run_config_beam/base live one dir up) ---
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from run_config_beam import Config as BeamConfig

ZS_PED_SET = 'zs_k8_tracer_from_07-18-26_14-06-43'
LATENCY, N_SAMPLES, IPD_FIXED = 35, 32, 10
SUBRUN_MIN = 6.0          # ~52 beam pulses/point at the observed ~8.7 pulses/min
DRIFT, RESIST = 600, 530  # run_66 nominal for PS+singles

# (label, ovr_wrn_hwm, ovr_wrn_lwm); None = no override -> RunCtrl cap (11 at lat35/n32)
POINTS = [
    ('hwm11_a', None, None),   # control == current production behaviour
    ('hwm6',    6,    3),
    ('hwm3',    3,    1),
    ('hwm1',    1,    0),
    ('hwm11_b', None, None),   # bracket: must reproduce hwm11_a
]


class Config(BeamConfig):
    def _set_defaults(self, config_path=None):
        super()._set_defaults(config_path)
        self.run_name = 'hwm_beam'
        self.n1081b_scan = 'off'   # static PS+singles trigger; no inline modulation
        self.run_out_dir = f'{self.data_out_dir}{self.run_name}/'
        self.dream_daq_info['run_directory'] = f'/home/mx17/july_dream/dream_run/{self.run_name}/'
        self.dream_daq_info['data_out_dir'] = f'{self.run_out_dir}'
        self.processor_info['run_dir'] = f'{self.run_out_dir}'
        self.hv_info['run_out_dir'] = self.run_out_dir
        self.resume = False
        self.trigger = ('BEAM: OvrWrnHwm scan (11/6/3/1, bracketed by 11) to pull the '
                        'post-flash live window into the 4-10 ms IPC band. PS+singles '
                        'co-framed (1800 ns), n32, lat35, ZS k8, IPD10. '
                        f'HV drift {DRIFT} / resist {RESIST}.')
        self.dream_daq_info.update({
            'daq_config_template_path': f'{self.base_out_dir}dream_config/Tcm_Mx17_July_ZS.cfg',
            'zero_suppress': True, 'common_noise_subtraction': True, 'pedestal_subtraction': False,
            'zs_type': 'tpc', 'zs_check_sample': 4, 'inter_packet_delay': IPD_FIXED,
            'pedestals_dir': f'{self.base_out_dir}pedestals/', 'pedestals': ZS_PED_SET,
            'latency': LATENCY, 'n_samples_per_waveform': N_SAMPLES,
        })
        hv = {'5': {'1': RESIST, '2': RESIST, '3': RESIST, '4': RESIST},
              '9': {'0': DRIFT, '1': DRIFT, '2': DRIFT, '3': DRIFT}}
        self.sub_runs = []
        for label, hwm, lwm in POINTS:
            sr = {'sub_run_name': label, 'run_time': SUBRUN_MIN, 'post_pause_s': 0,
                  'inter_packet_delay': IPD_FIXED, 'pedestals': ZS_PED_SET,
                  'hvs': {k: dict(v) for k, v in hv.items()}}
            if hwm is not None:
                sr['ovr_wrn_hwm'], sr['ovr_wrn_lwm'] = hwm, lwm
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
    c = Config(); c.write_to_file('config/json_run_configs/run_config_hwm_beam.json')
    print('=== BEAM OvrWrnHwm scan: pull the live window into 4-10 ms ===')
    print(f'n={N_SAMPLES} lat={LATENCY} IPD={IPD_FIXED} ZS-k8; HV drift {DRIFT} / resist {RESIST}')
    print('trigger: PS + singles, co-framed 1800 ns (no board change if already set)')
    for sr in c.sub_runs:
        print(f"  {sr['sub_run_name']:9s} cfg_hwm={str(sr.get('ovr_wrn_hwm')):>5}  "
              f"cfg_lwm={str(sr.get('ovr_wrn_lwm')):>5}")
    tot = len(c.sub_runs) * SUBRUN_MIN
    print(f'\n{len(c.sub_runs)} sub-runs x {SUBRUN_MIN} min = {tot:.0f} min (+ ~1 min/pt overhead)')
    print('Launch: .venv/bin/python daq_control.py run_config_hwm_beam.json')
