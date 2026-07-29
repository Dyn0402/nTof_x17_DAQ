#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_clock_window_test.py -- does the 25 MHz read clock pack MORE events into the
                                    ~10 ms pulse window? (2026-07-24, BEAM OFF)

THE QUESTION (operator's, 2026-07-24). The 07-23 scan proved RdClk 6.0->4.0 buys 1.5x
sustained readout rate (docs/CLOCK_RATE_SCAN_2026-07-23.md) via a saturating-pulser IntRate
measurement. What that did NOT show directly is the TIME-DOMAIN picture the operator cares
about: that with a faster read clock, events actually land DENSER and EARLIER inside the fixed
~10 ms window where the IPC physics is (39.8% of in-gate IPC arrives < 4.46 ms). This run proves
it two independent ways -- a clock CURVE of the sustained ceiling, and a Poisson event-SPACING
measurement of the readout comb period -- and folds in a latency control the operator asked for.

WHY ZS AND NOT RAW (important, and it is deliberate). This is a DIAGNOSTIC, not physics data --
we read event COUNTS and TIMESTAMPS, never amplitudes. We run ZS on purpose: RAW n32 is
~311 kB/event, so a saturating pulser at ~10 kHz = ~3 GB/s > 10 GbE -> the NETWORK would be the
ceiling and would MASK the read clock, which is exactly the thing we are trying to isolate. ZS
events are ~30 kB, so the readout clock is the true limit (this is why the 07-23 scan that
established the 1.5x also used ZS). Because we only count/timestamp events, the ZS-forced-PedSub
double-subtract concern (feu-runctrl-register-findings, CLOSED 07-24) is irrelevant here -- this
run produces NO physics amplitudes and is NOT a return to ZS data-taking.

CLOCKS ARE SET EXPLICITLY, NOT VIA sample_period. The 07-23 default change made
SAMPLE_PERIOD_CLOCK_DIVS[60] = ('4.0','6.0'), i.e. `sample_period:60` now means RdClk 4.0
(25 MHz) already. To avoid that ambiguity every point sets rdclk_div AND wrclk_div directly.
WrClk 6.0 everywhere -> the 60 ns / 1.92 us sample window is held FIXED at every clock (matches
production), so only the read-out speed varies. Phasing:
  - RdClk 6.0 / WrClk 6.0  = vendor 60 ns pair, phased, clean          (the OLD 16.7 MHz baseline)
  - RdClk 4.0 / WrClk 6.0  = TODAY'S PRODUCTION DEFAULT, proven clean  (25 MHz)
  - RdClk 5.0 / 4.5 / WrClk 6.0 = UN-PHASED intermediates. Their ADC content may be off, but
    IntRate and event timestamps are valid regardless (the observable here). Flag, do not trust
    their amplitudes -- we do not read them anyway.

THE FOUR BLOCKS (the "4 at the same time" -- 4 read-clock points in one curve, plus controls):

  A. CLOCK CURVE  (SATURATING 20 kHz fixed pulser). RdClk 6.0/5.0/4.5/4.0, bracketed with 6.0.
     Metric = IntRate (feu_trig_counters.py --latch): the sustained ceiling vs clock. Under
     saturation, events-in-10 ms = ceiling x 10 ms, so a 1.5x ceiling IS 1.5x events in the
     window. Prediction: ~7 k -> ~10.8 kHz, monotone with clock.

  B. EVENT SPACING (POISSON ~1 kHz). RdClk 6.0 then 4.0. This is the operator's literal proposal
     -- a fast Poisson pulser, event spacing read out after the fact. Metric = inter-event-dt
     histogram + comb autocorrelation (interevent_dt.py). The 07-20 flash-off study measured the
     readout comb at 16.7 MHz (period ~9.6 ms, hump ~8.6 ms). Prediction at 25 MHz: the comb
     period shrinks ~1.5x to ~6.4 ms -> the block-readout gap moves OUT of the 10 ms window, i.e.
     the window drains faster. This is the direct "denser/earlier in the window" plot.

  C. LATENCY CONTROL (SATURATING, RdClk 4.0). latency 3 / 35(current) / 100, bracketed at 35.
     Confirms latency does NOT affect the readout ceiling (readout_time ~ NbOfSamples, independent
     of latency; latency only moves N_buf=(512-lat)/n, the rested-dump depth, not the rate).
     Expect IntRate FLAT across all three -> "latency doesn't affect anything", as asked.

  D. RESTED-DUMP A/B (rest_toggle.py, 200 kHz idle-then-burst). RdClk 6.0 then 4.0, longer
     sub-runs. The flash-analog: a rested SCA (~15 cells) dumps into the window on each restart.
     Metric = events landing in the first 10 ms after each rested burst (clock_window_analysis.py
     --dump). The most beam-faithful transient. OPTIONAL -- A+B+C already answer the question.

PULSER CHANGES BETWEEN BLOCKS. The pulser is reconfigured at each block boundary; a settle_time
pause on the first sub-run of B/C/D gives the operator time to switch it. EXACT sequence and
commands are in docs/PLAN_2026-07-24_clock_window_tests.md -- follow that, not memory.

TRIGGER (beam off): M4.C <- M6.D pulser. set_veto_open.py --lemos 4 is REQUIRED first or M4.C
stays FN_OR_VETO and the run records 0 events at IntRate 0. RESTORE after (mandatory, and the
instant beam returns): trigger_mode.py scint --singles --ps-pickup ; set_pulser.py ;
set_veto_open.py --show ; set_ps_trigger_delay.py --show (expect delay 1800).

BEFORE LAUNCHING: the dream_daq server already carries rdclk_div/wrclk_div/rd_del/adc_dat_rdy_del
(restarted 07-23). No new knob is added here, so NO restart is needed -- but grep one emitted cfg
for the RdClk/WrClk/Dream*12 lines before trusting a point (stale-server trap).
"""
# --- repo-root shim (run_config_beam/base live one dir up) ---
import os
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from run_config_beam import Config as BeamConfig

# Phase selection (avoids mid-run pulser-switch timing): run one pulser regime per launch.
#   CLK_BLOCKS=A,C  -> saturating clock curve + latency control (fixed 20 kHz pulser)  [phase 1]
#   CLK_BLOCKS=B    -> Poisson event-spacing                    (Poisson ~1 kHz)       [phase 2]
#   CLK_BLOCKS=D    -> rested-dump A/B                          (rest_toggle 200 kHz)  [phase 3]
# Default = all four (the reviewed single-run form). run_name carries the phase so dirs don't collide.
_BLK_SEL = [b.strip().upper() for b in os.environ.get('CLK_BLOCKS', 'A,B,C,D').split(',') if b.strip()]
_RUN_NAME = os.environ.get('CLK_RUN_NAME',
                           'clk_' + ''.join(_BLK_SEL).lower() if _BLK_SEL != ['A', 'B', 'C', 'D']
                           else 'clk_window')

ZS_PED_SET = 'zs_k8_tracer_from_07-18-26_14-06-43'   # same set the 07-23 1.5x scan used
LATENCY, N_SAMPLES, IPD_FIXED = 35, 32, 2            # IPD 2 = max clock sensitivity (ZS op point)
SAT_MIN, POIS_MIN, DUMP_MIN = 0.75, 1.5, 2.0
SETTLE = 15                                          # s pause to switch the pulser at a boundary

# (label, block, rdclk, wrclk, latency, run_time, settle)
#   block 'A' saturating clock curve | 'B' Poisson spacing | 'C' latency control | 'D' rested dump
#   latency None = inherit LATENCY (35). settle>0 marks a pulser switch (see the PLAN doc).
POINTS = [
    # A -- SATURATING 20 kHz fixed pulser: ceiling vs read clock -------------------------------
    ('satA_rd6_a',  'A', 6.0, 6.0, None, SAT_MIN,  0),   # 16.7 MHz baseline (bracket open)
    ('satA_rd5',    'A', 5.0, 6.0, None, SAT_MIN,  0),   # 20.0 MHz (un-phased content)
    ('satA_rd4p5',  'A', 4.5, 6.0, None, SAT_MIN,  0),   # 22.2 MHz (un-phased content)
    ('satA_rd4',    'A', 4.0, 6.0, None, SAT_MIN,  0),   # 25.0 MHz -- production default
    ('satA_rd6_b',  'A', 6.0, 6.0, None, SAT_MIN,  0),   # 16.7 MHz (bracket close, must == rd6_a)
    # B -- POISSON ~1 kHz: readout comb period vs read clock (SWITCH PULSER to Poisson) ---------
    ('poisB_rd6',   'B', 6.0, 6.0, None, POIS_MIN, SETTLE),  # comb at 16.7 MHz (~9.6 ms expected)
    ('poisB_rd4',   'B', 4.0, 6.0, None, POIS_MIN, 0),       # comb at 25 MHz   (~6.4 ms predicted)
    # C -- SATURATING again: latency control at 25 MHz (SWITCH PULSER back to fixed 20 kHz) -----
    ('satC_lat3',   'C', 4.0, 6.0, 3,    SAT_MIN,  SETTLE),
    ('satC_lat35',  'C', 4.0, 6.0, 35,   SAT_MIN,  0),   # current
    ('satC_lat100', 'C', 4.0, 6.0, 100,  SAT_MIN,  0),
    ('satC_lat35b', 'C', 4.0, 6.0, 35,   SAT_MIN,  0),   # bracket, must == satC_lat35
    # D -- RESTED DUMP A/B: events in first 10 ms after a rested burst (run rest_toggle.py) -----
    ('dumpD_rd6',   'D', 6.0, 6.0, None, DUMP_MIN, SETTLE),  # run rest_toggle.py DURING this
    ('dumpD_rd4',   'D', 4.0, 6.0, None, DUMP_MIN, 0),       # run rest_toggle.py DURING this
]


class Config(BeamConfig):
    def _set_defaults(self, config_path=None):
        super()._set_defaults(config_path)
        self.run_name = _RUN_NAME
        self.n1081b_scan = 'off'
        self.run_out_dir = f'{self.data_out_dir}{self.run_name}/'
        self.dream_daq_info['run_directory'] = f'/home/mx17/july_dream/dream_run/{self.run_name}/'
        self.dream_daq_info['data_out_dir'] = f'{self.run_out_dir}'
        self.processor_info['run_dir'] = f'{self.run_out_dir}'
        self.hv_info['run_out_dir'] = self.run_out_dir
        self.resume = False
        self.trigger = ('DREAM clock-vs-window test (beam off): does 25 MHz pack more events into '
                        'the ~10 ms window? A=saturating clock curve RdClk 6/5/4.5/4 (ceiling vs '
                        'clock), B=Poisson ~1 kHz spacing at RdClk 6 vs 4 (comb period), '
                        'C=latency control 3/35/100 at RdClk 4 (expect flat), D=rested-dump A/B. '
                        'ZS diagnostic (counts/timestamps only, NOT physics), n32/lat35/IPD2, '
                        'WrClk 6.0 fixed (60 ns window held). HV left as-is.')
        self.dream_daq_info.update({
            'daq_config_template_path': f'{self.base_out_dir}dream_config/Tcm_Mx17_July_ZS.cfg',
            'zero_suppress': True, 'common_noise_subtraction': True, 'pedestal_subtraction': False,
            'zs_type': 'tpc', 'zs_check_sample': 4, 'inter_packet_delay': IPD_FIXED,
            'pedestals_dir': f'{self.base_out_dir}pedestals/', 'pedestals': ZS_PED_SET,
            'latency': LATENCY, 'n_samples_per_waveform': N_SAMPLES,
        })

        full_run = _BLK_SEL == ['A', 'B', 'C', 'D']
        self.sub_runs = []
        for label, blk, rd, wr, lat, rt, settle in POINTS:
            if blk not in _BLK_SEL:
                continue
            sr = {'sub_run_name': label, 'run_time': rt, 'post_pause_s': 0,
                  'inter_packet_delay': IPD_FIXED, 'pedestals': ZS_PED_SET,
                  'rdclk_div': rd, 'wrclk_div': wr, 'hvs': {}}
            if lat is not None:
                sr['latency'] = lat
            # settle_time only matters at a pulser SWITCH; single-regime phase launches don't switch
            if settle and full_run:
                sr['settle_time'] = settle
            self.sub_runs.append(sr)
        if not self.sub_runs:
            raise SystemExit(f'no sub-runs for CLK_BLOCKS={_BLK_SEL}')

        # keep scintillator HV pinned at standing setpoints (HV axis unused here)
        scint_hvs = {}
        for det in self.detectors:
            if det['name'] not in self.included_detectors:
                continue
            if not str(det.get('det_type', '')).startswith('scintillator'):
                continue
            hc, sp_v = det.get('hv_channels'), det.get('hv_setpoint')
            if not isinstance(hc, dict) or sp_v is None:
                continue
            for slot, ch in hc.values():
                scint_hvs.setdefault(str(slot), {})[str(ch)] = sp_v
        for sr in self.sub_runs:
            for slot, chans in scint_hvs.items():
                sr['hvs'].setdefault(slot, {}).update(chans)


if __name__ == '__main__':
    c = Config()
    out_json = f'config/json_run_configs/run_config_{_RUN_NAME}.json'
    c.write_to_file(out_json)
    print(f'=== DREAM clock-vs-window test  (run_name={_RUN_NAME}, blocks={",".join(_BLK_SEL)}) ===')
    print(f'n={N_SAMPLES} lat={LATENCY} IPD={IPD_FIXED}   WrClk 6.0 fixed (60 ns window held)\n')
    hdr = f"{'sub-run':>12} {'blk':>3} {'RdClk':>6} {'readout':>9} {'lat':>4} {'min':>5}"
    print(hdr)
    total = 0.0
    for (label, blk, rd, wr, lat, rt, settle) in POINTS:
        if blk not in _BLK_SEL:
            continue
        mhz = f'{100.0 / rd:.1f} MHz'
        latv = '-' if lat is None else str(lat)
        print(f'{label:>12} {blk:>3} {rd:>6.1f} {mhz:>9} {latv:>4} {rt:>5}')
        total += rt
    print(f'\n{len(c.sub_runs)} sub-runs, ~{total:.1f} min beam-off  ->  {out_json}')
    print('Follow docs/PLAN_2026-07-24_clock_window_tests.md for the pulser regime + restore.')
