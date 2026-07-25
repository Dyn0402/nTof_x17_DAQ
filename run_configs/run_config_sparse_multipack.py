#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_sparse_multipack.py — RE-TEST of the two comb-study nulls that never actually ran
(2026-07-22, beam-off window).

WHY: `dream_daq_control.py` is a long-lived server and, on 2026-07-19, was running code that
predated the `sparse_rd` / `multipack_*` plumbing. Every sub-run of `zs_sparserd` and
`zs_comb_study` therefore used the TEMPLATE DEFAULT — the archived cfgs show
`Main_Conf_SparseRd 0` and `UdpChan_MultiPackThr 4888` at *every* point of both sweeps. So
"SparseRd does nothing" and "MultiPackThr does nothing" are unsupported: the knobs were never
applied. Full account: docs/FEU_WATERMARKS_2026-07-22.md §3.

The server was restarted 2026-07-22 10:52, so the plumbing is now live (verified: the
watermark override reached both the cfg and the hardware).

WHY SparseRd MATTERS: it is potentially a BETTER comb lever than NbOfSamples.
`Main_Conf_SparseRd` (0x100004 bits 19:17) = 0 read all samples, n=[1..7] skip n samples. It
shortens the SCA readout — and hence BUSY, and hence the post-flash veto — while KEEPING the
readout window SPAN, just at coarser time resolution. NbOfSamples buys the same dead-time
reduction only by throwing the window depth away (n8 = 0.48 us window). If SparseRd works,
it is the lever the comb study wanted and concluded it did not have.

METHOD: fixed 4 kHz pulser (saturating — the FEU accepts ~3079 Hz at these settings, so the
DAQ, not the input, is the limit). Any shortening of the per-event cycle shows up directly as
a HIGHER accepted rate (`IntRate` in the dream_daq pane). No offline analysis needed.

Bracketed with SparseRd=0 controls (a/b/c) so drift cannot masquerade as an effect — this is
the same discipline that caught the n28 "anomaly" in the 2026-07-19 study.

VERIFY THE KNOB LANDED, both places, before believing any result:
  cfg :  grep Main_Conf_SparseRd ~/july_dream/dream_run/sparse_mp/<subrun>/*.cfg
  hw  :  peek 0x100004 -> bits 19:17 (see dream_scripts/feu_trig_counters.py for the UDP
         peek helper; note RunCtrl rewrites some registers, e.g. it clamps RdClk_Div and the
         trigger-FIFO watermarks, so the cfg is NOT proof)

TRIGGER: M4.C <- M6.D pulser. Beam off => `set_veto_open.py` is REQUIRED first, otherwise
M4.C stays FN_OR_VETO, the beam-derived veto never opens and the run records 0 events at
IntRate 0.00 Hz.
RESTORE AFTER: `trigger_mode.py scint --singles --ps-pickup` + `set_pulser.py`.
"""
# --- repo-root shim (run_config_beam/base live one dir up) ---
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from run_config_beam import Config as BeamConfig

ZS_PED_SET = 'zs_k8_tracer_from_07-18-26_14-06-43'
LATENCY, N_SAMPLES, IPD_FIXED, SUBRUN_MIN = 35, 32, 10, 1.0

# (label, sparse_rd, multipack_thr, multipack_enb). None = leave at template default.
# Template defaults: SparseRd 0, MultiPackEnb 1, MultiPackThr 4888.
POINTS = [
    ('base_a',   0,    None, None),   # control
    ('sparse1',  1,    None, None),   # skip 1 -> ~half the samples read
    ('sparse3',  3,    None, None),
    ('sparse7',  7,    None, None),   # max
    ('base_b',   0,    None, None),   # bracket: must reproduce base_a
    ('mp_off',   0,    None, 0),      # MultiPackEnb 0
    ('mp8188',   0,    8188, 1),      # toward the 8192 FEU frame cap
    ('base_c',   0,    None, None),   # final bracket
]


class Config(BeamConfig):
    def _set_defaults(self, config_path=None):
        super()._set_defaults(config_path)
        self.run_name = 'sparse_mp'
        self.n1081b_scan = 'off'
        self.run_out_dir = f'{self.data_out_dir}{self.run_name}/'
        self.dream_daq_info['run_directory'] = f'/home/mx17/july_dream/dream_run/{self.run_name}/'
        self.dream_daq_info['data_out_dir'] = f'{self.run_out_dir}'
        self.processor_info['run_dir'] = f'{self.run_out_dir}'
        self.hv_info['run_out_dir'] = self.run_out_dir
        self.resume = False
        self.trigger = ('SparseRd + MultiPack re-test (both 2026-07-19 nulls never reached the '
                        'cfg -- stale dream_daq_control server). Fixed 4 kHz pulser, saturating; '
                        'metric = accepted rate. SparseRd 0/1/3/7 bracketed with 0. HV left as-is.')
        self.dream_daq_info.update({
            'daq_config_template_path': f'{self.base_out_dir}dream_config/Tcm_Mx17_July_ZS.cfg',
            'zero_suppress': True, 'common_noise_subtraction': True, 'pedestal_subtraction': False,
            'zs_type': 'tpc', 'zs_check_sample': 4, 'inter_packet_delay': IPD_FIXED,
            'pedestals_dir': f'{self.base_out_dir}pedestals/', 'pedestals': ZS_PED_SET,
            'latency': LATENCY, 'n_samples_per_waveform': N_SAMPLES,
        })
        self.sub_runs = []
        for label, sr_v, mpt, mpe in POINTS:
            sr = {'sub_run_name': label, 'run_time': SUBRUN_MIN, 'post_pause_s': 0,
                  'inter_packet_delay': IPD_FIXED, 'pedestals': ZS_PED_SET, 'hvs': {}}
            if sr_v is not None:
                sr['sparse_rd'] = sr_v
            if mpt is not None:
                sr['multipack_thr'] = mpt
            if mpe is not None:
                sr['multipack_enb'] = mpe
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
    c = Config(); c.write_to_file('config/json_run_configs/run_config_sparse_multipack.json')
    print('=== SparseRd + MultiPack re-test (pulser, saturating) ===')
    print(f'n={N_SAMPLES} lat={LATENCY} IPD={IPD_FIXED}; reference accepted rate ~3079 Hz')
    for sr in c.sub_runs:
        print(f"  {sr['sub_run_name']:9s} sparse_rd={str(sr.get('sparse_rd')):>4}  "
              f"mp_thr={str(sr.get('multipack_thr')):>5}  mp_enb={str(sr.get('multipack_enb')):>5}")
    print(f'\n{len(c.sub_runs)} sub-runs x {SUBRUN_MIN} min')
    print('Pre-launch (beam off): .venv/bin/python n1081b/set_veto_open.py')
    print('Launch: .venv/bin/python daq_control.py run_config_sparse_multipack.json')
