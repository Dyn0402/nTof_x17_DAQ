#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_window_sparse_test.py -- does "sample fast + sparse" win at a FIXED time window?
(2026-07-23, beam off, saturating pulser)

THE HYPOTHESIS UNDER TEST (operator's, worth proving not asserting). We must always read out the
same physics TIME WINDOW -- currently 32 samples x 60 ns = 1.92 us. Raising both clocks and taking
more samples to cover it is a wash (faster readout x more samples = same rate). The proposal: raise
the sample clock, then go SPARSE (skip samples) so you read FEWER samples while the window stays
1.92 us -- hoping the readout (and hence rate) shrinks.

WHAT MUST STAY FIXED: the FULL TIME WINDOW = 1920 ns, at every point. RdClk is pinned at 4.0
(25 MHz, the hardware maximum -- Drm_RdClk_Div_Min 4.0 in FeuUdpControl/DrmClkConfig.c; below it
FeuCtrl_Open fails, proven by the 07-23 rd3wr4 point). So the ONLY thing varying is HOW the 1920 ns
window is built and read: coarse-sampled, fine-sampled-plain, or fine-sampled-sparse.

  point      sample clk       N samp  sparse   window          samples READ   expectation
  ---------  ---------------  ------  ------   -------------   ------------   ------------------------
  coarse_a   60 ns (WrClk6)     32      0      32x60 = 1920    32             fast (ref, no sparse)
  fine_nosp  20 ns (WrClk2)     96      0      96x20 = 1920    96             ~3x SLOWER (honest cost
                                                                              of fine sampling, no sparse)
  fine_sp2   20 ns (WrClk2)     32      2      32x(2+1)x20     32 (if sparse   fast IF sparse both
                                              = 1920 *          works)         reduces reads AND keeps span
  coarse_b   = coarse_a bracket

  * fine_sp2's 1920 ns span is REALIZED ONLY IF SparseRd actually skips-and-widens. If SparseRd is
    inert (as two prior register-verified nulls indicate), fine_sp2 is really 32x20 = 640 ns -- a
    window a THIRD of the target, masquerading as fast. That is the trap this test exposes.

READS OUT (the metric is readout COST, measured directly):
  1. IntRate (TCM pane) per point -- the sustained rate.
  2. samples/event from decoded data (max 'sample' index) -- how many columns were actually READ.
     coarse_a -> 32, fine_nosp -> 96, fine_sp2 -> 32. This is the readout-depth, the thing that
     sets the rate. It does NOT reveal the achieved time SPAN (beam-off there is no timed signal in
     the waveform), which is exactly why the fine_sp2 window claim rests on SparseRd working.
  3. SparseRd peeked off the hardware DURING fine_sp2 (feu_main_conf.py --expect 2) -- so any null is
     "set but inert", not "never applied".

HOW TO READ THE RESULT.
  - If coarse_a == fine_sp2 (fast) and fine_nosp is ~3x slower: readout cost is set by samples-READ,
    period. Then the ONLY way to hold the full 1920 ns window at the fast rate is coarse sampling
    (coarse_a). Fine sampling either costs 3x (fine_nosp, full window) or silently collapses the
    window to 640 ns (fine_sp2, if SparseRd inert). Sparse buys nothing. -> hypothesis disproven.
  - If fine_sp2 is fast AND (separately confirmed) spans the full 1920 ns while fine_nosp is slow:
    SparseRd genuinely reduces reads while keeping span -> hypothesis SUPPORTED, and it merely MATCHES
    coarse_a (never beats it). Confirming the span then needs a timed injected pulse (FEU Dream
    pulser, reg 0x200014) -- a beam-off follow-up, out of scope for this minimal run.

Either way the ceiling is the same: readout_time ~ (columns spanning the window) / RCk, RCk maxed at
25 MHz. See docs/CLOCK_RATE_SCAN_2026-07-23.md and [[dream-clock-firmware-limits]].

TRIGGER (beam off): M4.C <- M6.D pulser. set_veto_open.py REQUIRED first. Saturating 20 kHz.
RESTORE AFTER: trigger_mode.py scint --singles --ps-pickup + set_pulser.py.
NOTE: the dream_daq server must already carry the wrclk_div plumbing (restarted 2026-07-23 ~10:40).
"""
from run_config_beam import Config as BeamConfig

ZS_PED_SET = 'zs_k8_tracer_from_07-18-26_14-06-43'
LATENCY, IPD_FIXED, SUBRUN_MIN = 35, 2, 0.75
RDCLK = 4.0  # 25 MHz -- hardware maximum, pinned for every point

# (label, wrclk_div, n_samples, sparse_rd) -- all at RdClk 4.0, target window 1920 ns
POINTS = [
    ('coarse_a',  6.0, 32, 0),   # 60 ns x 32 = 1920 ns, no sparse (reference)
    ('fine_nosp', 2.0, 96, 0),   # 20 ns x 96 = 1920 ns, no sparse (honest fine-sampling cost)
    ('fine_sp2',  2.0, 32, 2),   # 20 ns, skip 2 -> 1920 ns IF sparse works, 32 reads (the hypothesis)
    ('coarse_b',  6.0, 32, 0),   # bracket
]
WINDOW_NS = 1920


class Config(BeamConfig):
    def _set_defaults(self, config_path=None):
        super()._set_defaults(config_path)
        self.run_name = 'win_sparse'
        self.n1081b_scan = 'off'
        self.run_out_dir = f'{self.data_out_dir}{self.run_name}/'
        self.dream_daq_info['run_directory'] = f'/home/mx17/july_dream/dream_run/{self.run_name}/'
        self.dream_daq_info['data_out_dir'] = f'{self.run_out_dir}'
        self.processor_info['run_dir'] = f'{self.run_out_dir}'
        self.hv_info['run_out_dir'] = self.run_out_dir
        self.resume = False
        self.trigger = ('Window-fixed sparse test: does fast-sample+sparse beat coarse-sample at a '
                        'FIXED 1920 ns readout window? RdClk 4.0 (25 MHz) pinned. coarse32 vs fine96 '
                        'vs fine32+sparse2, all 1920 ns. Saturating pulser, ZS k8, IPD 2. Metric = '
                        'IntRate + samples/event. HV left as-is.')
        self.dream_daq_info.update({
            'daq_config_template_path': f'{self.base_out_dir}dream_config/Tcm_Mx17_July_ZS.cfg',
            'zero_suppress': True, 'common_noise_subtraction': True, 'pedestal_subtraction': False,
            'zs_type': 'tpc', 'zs_check_sample': 4, 'inter_packet_delay': IPD_FIXED,
            'pedestals_dir': f'{self.base_out_dir}pedestals/', 'pedestals': ZS_PED_SET,
            'latency': LATENCY, 'n_samples_per_waveform': 32,
        })
        self.sub_runs = []
        for label, wr, nsamp, sr_v in POINTS:
            self.sub_runs.append({
                'sub_run_name': label, 'run_time': SUBRUN_MIN, 'post_pause_s': 0,
                'inter_packet_delay': IPD_FIXED, 'pedestals': ZS_PED_SET,
                'rdclk_div': RDCLK, 'wrclk_div': wr,
                'n_samples_per_waveform': nsamp, 'sparse_rd': sr_v, 'hvs': {}})

        scint_hvs = {}
        for det in self.detectors:
            if det['name'] not in self.included_detectors: continue
            if not str(det.get('det_type', '')).startswith('scintillator'): continue
            hc, sp_v = det.get('hv_channels'), det.get('hv_setpoint')
            if not isinstance(hc, dict) or sp_v is None: continue
            for slot, ch in hc.values(): scint_hvs.setdefault(str(slot), {})[str(ch)] = sp_v
        for sr in self.sub_runs:
            for slot, chans in scint_hvs.items(): sr['hvs'].setdefault(slot, {}).update(chans)


if __name__ == '__main__':
    c = Config(); c.write_to_file('config/json_run_configs/run_config_window_sparse_test.json')
    print('=== Window-fixed sparse test (RdClk 4.0 = 25 MHz pinned, window = 1920 ns) ===')
    print(f"{'point':>10} {'sample':>9} {'N':>4} {'sparse':>7} {'window':>10} {'reads':>6}")
    for (label, wr, nsamp, sr_v) in POINTS:
        smp_ns = wr * 10
        win = nsamp * smp_ns * (sr_v + 1)
        print(f'{label:>10} {smp_ns:>6.0f}ns {nsamp:>4} {sr_v:>7} {win:>8.0f}ns {nsamp:>6}'
              + ('  <- 1920 only if sparse works' if sr_v else ''))
    print(f'\n{len(c.sub_runs)} sub-runs x {SUBRUN_MIN} min')
    print('\nPre-launch (beam off):')
    print('  .venv/bin/python n1081b/set_pulser.py --fixed --period 50000 --width 100')
    print('  .venv/bin/python n1081b/set_veto_open.py --lemos 4')
    print('Launch:  .venv/bin/python daq_control.py run_config_window_sparse_test.json')
    print('During fine_sp2: .venv/bin/python dream_scripts/feu_main_conf.py --expect 2 --watch 5')
    print('Per point:       .venv/bin/python dream_scripts/feu_trig_counters.py --latch')
    print('Restore: trigger_mode.py scint --singles --ps-pickup ; set_pulser.py')
