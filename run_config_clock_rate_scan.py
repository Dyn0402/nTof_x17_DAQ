#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_clock_rate_scan.py -- can a faster DREAM clock buy readout rate? (2026-07-23, beam off)

THE QUESTION. On 10 GbE the readout stopped being the constraint at beam rates -- the beam
TRIGGER is now the ceiling (~95 ev/spill, see docs/network_upgrade_10g/results_2026-07-22...).
But the per-event cycle itself is still ~474 us at IPD 10, and it is set by

    cycle = n_samples x (4.83 + 0.998 x IPD) us          [fitted, deadtime_db.csv]
                         ^^^^
                         SCA readout -- the term the READ CLOCK sets

Nothing has ever tried moving that 4.83 us. The knob is `DrmClk RdClk_Div` (TrigClock/RdClk):
6.0 = 16.7 MHz today, 4.0 = 25 MHz. Faster read clock -> shorter SCA drain -> shorter deadtime
-> higher sustainable rate, for free, at every IPD.

DESIGN NOTE -- WHY IPD 2, NOT THE USUAL 10. The clock only touches the 4.83 us term, so the
lower the IPD the larger the fraction of the cycle it can move:

    IPD 10 : 4.83 / 14.81 = 33% of the cycle is clock-driven   (weak sensitivity)
    IPD  2 : 4.83 /  6.83 = 71%                                <-- used here
    IPD  1 : 4.83 /  5.83 = 83%

IPD 2 is the standing ZS operating point (proven since 07-19) and puts most of the cycle under
the knob. Predicted at RdClk 4.0 (25 MHz): 4.83 -> ~3.2 us, cycle 219 -> 166 us, ~1.3x rate.
A null well inside that is the interesting outcome -- it would mean the 4.83 us is not the SCA
readout at all.

CLOCKS MOVE AS A PAIR. `WrClk_Phase 2 / AdcClk_Phase 5` in the cfg are tuned for the two VENDOR
divisor pairs -- 6.0/6.0 (60 ns sampling) and 4.0/2.0 (20 ns sampling). So the ladder walks the
vendor 20 ns preset FIRST (fully supported, phases valid), and only then the unpaired
RdClk 4.0 / WrClk 4.0 (40 ns sampling, 25 MHz readout), which keeps the readout fast while
recovering half the sampling window the 20 ns preset gives up. That point is un-phased territory
-> its data sanity must be checked before its rate number means anything.

SPARSERD IS A PROBE HERE, NOT A SWEEP. It has been "swept" twice and been null twice, and
neither null is trustworthy (07-19: never reached the cfg, stale server; 07-22 `sparse_mp`:
reached the cfg but data volume flat to 0.3% and the register was NEVER read off the hardware).
Sub-run `sp3_probe` exists purely so the register can be peeked while it is live:

    .venv/bin/python dream_scripts/feu_main_conf.py --expect 3 --watch 5

  PASS (all 8 hold 3) -> SparseRd is live and genuinely inert; `clk20_sp3` then tests whether it
                         pays off once the read clock is fast. Believe a null.
  FAIL (reads 0)      -> RunCtrl clamps it exactly as it clamps the watermarks; SparseRd has
                         never actually been tested and both nulls are void. `clk20_sp3` is
                         meaningless -- ignore it and report the clamp.

METRIC. Fixed 20 kHz pulser = saturating (the FEU accepts ~3079 Hz at n32/lat35/IPD10, and the
ceiling at IPD 2 is still far under 20 kHz), so the DAQ is the limit at every point and any
shortening of the cycle shows up DIRECTLY as a higher accepted rate. Read it per sub-run:

    .venv/bin/python dream_scripts/feu_trig_counters.py --latch

No offline analysis needed for the rate answer. Bracketed nominal_a/b/c so drift cannot
masquerade as an effect -- the discipline that caught the n28 "anomaly" on 07-19.

DATA SANITY IS NOT OPTIONAL. A faster read clock is the manual's "delicate" region. For every
non-nominal point verify BEFORE quoting its rate:
  - ZS tracer channels 0/224/511 present in ~100% of events (the integrity watermark -- clock
    corruption breaks this first)
  - decoded baseline ~256, amplitudes physical, no decode errors from processor_watcher
A point that reads FAST but has lost its tracers is reading garbage quickly, not reading fast.

TRIGGER (beam off): M4.C <- M6.D pulser. `set_veto_open.py` is REQUIRED first, otherwise M4.C
stays FN_OR_VETO, the beam-derived veto never opens, and the run records 0 events at
IntRate 0.00 Hz with no error anywhere.
RESTORE AFTER: `trigger_mode.py scint --singles --ps-pickup` then `set_pulser.py`.

BEFORE LAUNCHING: restart the dream_daq server. `wrclk_div` was added to
dream_daq_control.py today and the running server predates it -- an un-restarted server drops
it silently and rd4wr4 becomes a duplicate of clk20 (docs/FEU_WATERMARKS_2026-07-22.md sec.3).
"""
from run_config_beam import Config as BeamConfig

ZS_PED_SET = 'zs_k8_tracer_from_07-18-26_14-06-43'
LATENCY, N_SAMPLES, IPD_FIXED, SUBRUN_MIN = 35, 32, 2, 0.75

# (label, sample_period, rdclk_div, wrclk_div, sparse_rd)
# sample_period 60 -> RdClk 6.0 / WrClk 6.0 ; 20 -> RdClk 4.0 / WrClk 2.0 (vendor pairs).
# rdclk_div / wrclk_div override whatever the preset set. None = leave alone.
POINTS = [
    ('nom60_a',    60,   None, None, None),  # control: today's operating point, 16.7 MHz readout
    ('sp3_probe',  60,   None, None, 3),     # PEEK 0x100004 WHILE THIS RUNS -- see docstring
    ('clk20',      20,   None, None, None),  # vendor 20 ns preset: RdClk 4.0 / WrClk 2.0, 25 MHz
    ('rd4wr4',     None, 4.0,  4.0,  None),  # unpaired: 25 MHz readout, 40 ns sampling
    ('nom60_b',    60,   None, None, None),  # bracket: must reproduce nom60_a
    ('clk20_sp3',  20,   None, None, 3),     # sparse x fast clock -- only if sp3_probe PASSED
    ('rd3wr4',     None, 3.0,  4.0,  None),  # probe past the vendor pairs: 33 MHz readout.
                                             # EXPECT this one to break sanity; that is the point.
    ('nom60_c',    60,   None, None, None),  # closing bracket
]


class Config(BeamConfig):
    def _set_defaults(self, config_path=None):
        super()._set_defaults(config_path)
        self.run_name = 'clk_rate'
        self.n1081b_scan = 'off'
        self.run_out_dir = f'{self.data_out_dir}{self.run_name}/'
        self.dream_daq_info['run_directory'] = f'/home/mx17/july_dream/dream_run/{self.run_name}/'
        self.dream_daq_info['data_out_dir'] = f'{self.run_out_dir}'
        self.processor_info['run_dir'] = f'{self.run_out_dir}'
        self.hv_info['run_out_dir'] = self.run_out_dir
        self.resume = False
        self.trigger = ('DREAM clock rate scan: can a faster RdClk shorten the SCA readout and '
                        'raise the sustained rate? Saturating 20 kHz pulser, ZS k8, n32/lat35, '
                        'IPD 2 (max clock sensitivity). 60 ns nominal -> 20 ns vendor preset -> '
                        'RdClk 4.0/WrClk 4.0 unpaired -> RdClk 3.0 probe, bracketed x3. '
                        'sp3_probe exists to peek Main_Conf 0x100004 and settle whether SparseRd '
                        'ever reaches the hardware. HV left as-is.')
        self.dream_daq_info.update({
            'daq_config_template_path': f'{self.base_out_dir}dream_config/Tcm_Mx17_July_ZS.cfg',
            'zero_suppress': True, 'common_noise_subtraction': True, 'pedestal_subtraction': False,
            'zs_type': 'tpc', 'zs_check_sample': 4, 'inter_packet_delay': IPD_FIXED,
            'pedestals_dir': f'{self.base_out_dir}pedestals/', 'pedestals': ZS_PED_SET,
            'latency': LATENCY, 'n_samples_per_waveform': N_SAMPLES,
        })
        self.sub_runs = []
        for label, sp, rd, wr, sr_v in POINTS:
            sr = {'sub_run_name': label, 'run_time': SUBRUN_MIN, 'post_pause_s': 0,
                  'inter_packet_delay': IPD_FIXED, 'pedestals': ZS_PED_SET, 'hvs': {}}
            if sp is not None:
                sr['sample_period'] = sp
            if rd is not None:
                sr['rdclk_div'] = rd
            if wr is not None:
                sr['wrclk_div'] = wr
            if sr_v is not None:
                sr['sparse_rd'] = sr_v
            self.sub_runs.append(sr)

        scint_hvs = {}
        for det in self.detectors:
            if det['name'] not in self.included_detectors: continue
            if not str(det.get('det_type', '')).startswith('scintillator'): continue
            hc, sp_v = det.get('hv_channels'), det.get('hv_setpoint')
            if not isinstance(hc, dict) or sp_v is None: continue
            for slot, ch in hc.values(): scint_hvs.setdefault(str(slot), {})[str(ch)] = sp_v
        for sr in self.sub_runs:
            for slot, chans in scint_hvs.items(): sr['hvs'].setdefault(slot, {}).update(chans)


# Vendor/derived divisor pairs, for the printout only. TrigClock = 100 MHz.
_PRESET = {60: ('6.0', '6.0'), 20: ('4.0', '2.0')}


def _clocks(sp, rd, wr):
    r, w = _PRESET.get(sp, (None, None))
    if rd is not None: r = f'{rd:.1f}'
    if wr is not None: w = f'{wr:.1f}'
    r = r or '(tmpl 4.0)'
    w = w or '(tmpl 2.0)'
    try:
        rd_mhz = f'{100.0 / float(r):.1f} MHz'
        smp_ns = f'{float(w) * 10:.0f} ns'
    except ValueError:
        rd_mhz, smp_ns = '?', '?'
    return r, w, rd_mhz, smp_ns


if __name__ == '__main__':
    c = Config(); c.write_to_file('config/json_run_configs/run_config_clock_rate_scan.json')
    print('=== DREAM clock rate scan (saturating pulser, ZS k8) ===')
    print(f'n={N_SAMPLES} lat={LATENCY} IPD={IPD_FIXED}   '
          f'nominal cycle = {N_SAMPLES} x (4.83 + 0.998 x {IPD_FIXED}) = '
          f'{N_SAMPLES * (4.83 + 0.998 * IPD_FIXED):.0f} us '
          f'-> {1e6 / (N_SAMPLES * (4.83 + 0.998 * IPD_FIXED)):.0f} Hz expected at nominal')
    print(f"\n{'sub-run':>11} {'RdClk':>9} {'WrClk':>9} {'readout':>9} {'sample':>8} {'SparseRd':>9}")
    for (label, sp, rd, wr, sr_v) in POINTS:
        r, w, rd_mhz, smp_ns = _clocks(sp, rd, wr)
        print(f'{label:>11} {r:>9} {w:>9} {rd_mhz:>9} {smp_ns:>8} '
              f'{("-" if sr_v is None else sr_v):>9}')
    print(f'\n{len(c.sub_runs)} sub-runs x {SUBRUN_MIN} min '
          f'= {len(c.sub_runs) * SUBRUN_MIN:.0f} min of beam-off time')
    print('\nRESTART the dream_daq server first (wrclk_div is new today).')
    print('Pre-launch (beam off): .venv/bin/python n1081b/set_veto_open.py --lemos 4')
    print('                       .venv/bin/python n1081b/set_pulser.py --fixed --period 50000 --width 100')
    print('Launch:  .venv/bin/python daq_control.py run_config_clock_rate_scan.json')
    print('Per point: .venv/bin/python dream_scripts/feu_trig_counters.py --latch')
    print('During sp3_probe: .venv/bin/python dream_scripts/feu_main_conf.py --expect 3 --watch 5')
    print('Restore: .venv/bin/python n1081b/trigger_mode.py scint --singles --ps-pickup')
    print('         .venv/bin/python n1081b/set_pulser.py')
