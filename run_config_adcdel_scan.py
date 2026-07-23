#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_adcdel_scan.py -- is Rd2AdcDataDel=8 still right at a 25 MHz read clock? (2026-07-23)

WHY THIS MATTERS: it de-risks a change already IN PRODUCTION. Today we made RdClk_Div 4.0 (25 MHz)
the default read clock (docs/CLOCK_RATE_SCAN_2026-07-23.md). `Feu_RunCtrl_AdcDatRdyDel`
(RunControl 0x200008 bits 20:16, "Rd2AdcDataDel") is the number of READ-CLOCK CYCLES the logic waits
between the Dream Read strobe and latching valid ADC data. Our templates set it to **8**
(`Tcm_Mx17_July_ZS.cfg:202`, Raw:197) and the hardware holds 8 (peeked, all 8 FEUs).

The FEU manual specifies 8 **for the 20.8(3) MHz read clock**. We no longer run at 20.8 MHz.

PREDICTION (sharper than "is 8 still ok"). The delay is counted in read-clock CYCLES, but the thing
being waited out -- analogue settling of the Dream multiplexed output into the ADC -- is a fixed
PHYSICAL time. So the required cycle count should scale WITH the clock frequency:

    8 cycles @ 20.8 MHz = 8 x 48.0 ns = 384 ns  of settling
    to buy 384 ns @ 25 MHz (40 ns period) needs  384/40 = 9.6  ->  ~10 cycles

So if the manual's 8 was tuned as a physical settling time, the optimum at 25 MHz should sit near
**9-10, not 8** -- i.e. we may currently be latching the ADC ~64-160 ns EARLY, part-way up the
settling edge. That would cost S/N (higher baseline RMS), not rate.

THIS IS NOT A RATE TEST. Rd2AdcDataDel does not change readout duration -- do not judge it on
IntRate (which should be flat across the whole scan; if IntRate DOES move, something else changed and
the run is suspect). Judge on DATA QUALITY, from the decoded data:
  * chan-0 baseline RMS   -- the sensitive metric. Clock scan reference: median 263, rms 81-92.
  * baseline MEDIAN       -- should stay ~263 (CmnPedOffset=256).
  * ZS tracer channels 0/224/511 present ~100 % of events -- integrity watermark.
  * hits/event            -- 96.0 at both clock points previously.
A U-shaped RMS vs delay with its minimum away from 8 => free S/N available at the new clock.
A FLAT curve => 8 is fine and today's 25 MHz default is confirmed sound (also a valuable result --
it retires the main open worry about a change already shipped).
If NOTHING changes at all across 6->12, suspect the write never landed and go back to the peek.

VERIFY EVERY POINT ON HARDWARE:
    .venv/bin/python dream_scripts/feu_runctrl_reg.py --expect-adcdel <N>
(bits 20:16 of 0x200008). The cfg is not proof.

NOTE this test needs the processor to decode before it can be read -- queue the decode, do not block.

TRIGGER (beam off): M4.C <- M6.D pulser saturating 20 kHz; set_veto_open.py REQUIRED first.
RESTORE AFTER: trigger_mode.py scint --singles --ps-pickup + set_pulser.py.
"""
from run_config_beam import Config as BeamConfig

ZS_PED_SET = 'zs_k8_tracer_from_07-18-26_14-06-43'
LATENCY, N_SAMPLES, IPD_FIXED, SUBRUN_MIN = 35, 32, 2, 0.75

# (label, adc_dat_rdy_del). 8 = template/production value. Bracketed by 8 at both ends.
POINTS = [
    ('adc08_a', 8),    # bracket / production reference
    ('adc06',   6),    # early -- expect degradation if we are already early
    ('adc07',   7),
    ('adc09',   9),    # predicted region of the optimum at 25 MHz
    ('adc10',   10),   # 10 cycles x 40 ns = 400 ns ~= the 384 ns the manual buys at 20.8 MHz
    ('adc08_b', 8),    # bracket -- must reproduce adc08_a
    ('adc12',   12),   # far side; expect degradation once past the settling point
]


class Config(BeamConfig):
    def _set_defaults(self, config_path=None):
        super()._set_defaults(config_path)
        self.run_name = 'adcdel'
        self.n1081b_scan = 'off'
        self.run_out_dir = f'{self.data_out_dir}{self.run_name}/'
        self.dream_daq_info['run_directory'] = f'/home/mx17/july_dream/dream_run/{self.run_name}/'
        self.dream_daq_info['data_out_dir'] = f'{self.run_out_dir}'
        self.processor_info['run_dir'] = f'{self.run_out_dir}'
        self.hv_info['run_out_dir'] = self.run_out_dir
        self.resume = False
        self.trigger = ('Rd2AdcDataDel scan at the new 25 MHz read clock. Template value 8 was '
                        'specified by the manual for 20.8 MHz; delay is in read-clock cycles so the '
                        'optimum should shift up to ~9-10 if it encodes a fixed settling time. '
                        'DATA-QUALITY test (baseline RMS / tracers), NOT a rate test. '
                        '8/6/7/9/10/8/12, saturating pulser, ZS k8, n32, IPD 2. HV as-is.')
        self.dream_daq_info.update({
            'daq_config_template_path': f'{self.base_out_dir}dream_config/Tcm_Mx17_July_ZS.cfg',
            'zero_suppress': True, 'common_noise_subtraction': True, 'pedestal_subtraction': False,
            'zs_type': 'tpc', 'zs_check_sample': 4, 'inter_packet_delay': IPD_FIXED,
            'pedestals_dir': f'{self.base_out_dir}pedestals/', 'pedestals': ZS_PED_SET,
            'latency': LATENCY, 'n_samples_per_waveform': N_SAMPLES,
        })
        self.sub_runs = []
        for label, ad in POINTS:
            self.sub_runs.append({
                'sub_run_name': label, 'run_time': SUBRUN_MIN, 'post_pause_s': 0,
                'inter_packet_delay': IPD_FIXED, 'pedestals': ZS_PED_SET,
                'adc_dat_rdy_del': ad, 'hvs': {}})

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
    c = Config(); c.write_to_file('config/json_run_configs/run_config_adcdel_scan.json')
    print('=== Rd2AdcDataDel scan @ 25 MHz read clock (data-quality test) ===')
    print('settling bought: N cycles x 40 ns @25MHz   (manual: 8 x 48 ns = 384 ns @20.8MHz)')
    for label, ad in POINTS:
        print(f'  {label:8s} adc_dat_rdy_del={ad:>2}  -> {ad*40:>4} ns'
              + ('   <- production value' if ad == 8 else '')
              + ('   <- ~matches 384 ns' if ad == 10 else ''))
    print(f'\n{len(c.sub_runs)} sub-runs x {SUBRUN_MIN} min')
    print('\nPer point VERIFY: .venv/bin/python dream_scripts/feu_runctrl_reg.py --expect-adcdel <N>')
    print('Launch: .venv/bin/python daq_control.py run_config_adcdel_scan.json')
    print('Metric = decoded baseline RMS / tracers (NOT IntRate, which should be flat).')
