#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_verify_production_cfg.py -- one short run that RESTORES and VERIFIES the production
FEU register state. Utility, not a physics run.

WHY THIS EXISTS. FEU registers persist: they hold whatever the last run wrote until the next run
reconfigures them. After any register sweep (e.g. `adcdel`, `rddel`) the hardware is left at the LAST
sub-run's value -- on 2026-07-23 that left all 8 FEUs at `Rd2AdcDataDel = 12`, a value MEASURED to
destroy the data (hits/event 96 -> 1053). It self-heals at the next run because both templates carry
explicit `Feu_RunCtrl_AdcDatRdyDel 8` / `Feu_RunCtrl_RdDel 1` lines, but "it will fix itself next
time" is not a state you want to walk away from, and a peek taken in between is misleading.

Run this after any FEU register sweep, and any time you want to confirm the production configuration
actually reaches the hardware.

It sets NO overrides -- that is the point. Everything comes from the template + run_config_beam
defaults, so what lands is exactly what a physics run would land:

    RdClk_Div 4.0 (25 MHz read)      <- from SAMPLE_PERIOD_CLOCK_DIVS[60], the 2026-07-23 default
    WrClk_Div 6.0 (60 ns sampling)   <- full 1.92 us / 32-sample drift window
    Feu_RunCtrl_AdcDatRdyDel 8       <- razor-sharp optimum, +/-1 destroys data
    Feu_RunCtrl_RdDel 1              <- template value; 0 is safe but buys nothing

USE (beam off or on -- the trigger is left alone, no pulser needed; at ~6 Hz beam-off it records
almost nothing, which is fine because the point is the CONFIGURE, not the data):

    .venv/bin/python daq_control.py run_config_verify_production_cfg.json
    # while it runs:
    .venv/bin/python dream_scripts/feu_runctrl_reg.py --expect-adcdel 8
    .venv/bin/python dream_scripts/feu_runctrl_reg.py --expect-rddel 1
    .venv/bin/python dream_scripts/feu_main_conf.py                 # SparseRd should read 0

Expect PASS on all 8 FEUs. Background: docs/DAQ_OPTIMIZATION_SUMMARY_2026-07-23.md.
"""
from run_config_beam import Config as BeamConfig

ZS_PED_SET = 'zs_k8_tracer_from_07-18-26_14-06-43'


class Config(BeamConfig):
    def _set_defaults(self, config_path=None):
        super()._set_defaults(config_path)
        self.run_name = 'verify_cfg'
        self.n1081b_scan = 'off'
        self.run_out_dir = f'{self.data_out_dir}{self.run_name}/'
        self.dream_daq_info['run_directory'] = f'/home/mx17/july_dream/dream_run/{self.run_name}/'
        self.dream_daq_info['data_out_dir'] = f'{self.run_out_dir}'
        self.processor_info['run_dir'] = f'{self.run_out_dir}'
        self.hv_info['run_out_dir'] = self.run_out_dir
        self.resume = False
        self.trigger = ('Utility: restore + verify the production FEU register state after a register '
                        'sweep. NO overrides -- template/run_config_beam defaults only, so what lands '
                        'is exactly what a physics run lands (RdClk 4.0 / WrClk 6.0 / AdcDel 8 / '
                        'RdDel 1). Trigger untouched, HV as-is.')
        self.dream_daq_info.update({
            'daq_config_template_path': f'{self.base_out_dir}dream_config/Tcm_Mx17_July_ZS.cfg',
            'zero_suppress': True, 'common_noise_subtraction': True, 'pedestal_subtraction': False,
            'zs_type': 'tpc', 'zs_check_sample': 4, 'inter_packet_delay': 2,
            'pedestals_dir': f'{self.base_out_dir}pedestals/', 'pedestals': ZS_PED_SET,
            'latency': 35, 'n_samples_per_waveform': 32,
        })
        # Deliberately NO rdclk_div / wrclk_div / rd_del / adc_dat_rdy_del / sparse_rd overrides.
        self.sub_runs = [{
            'sub_run_name': 'verify', 'run_time': 0.5, 'post_pause_s': 0,
            'inter_packet_delay': 2, 'pedestals': ZS_PED_SET, 'hvs': {}}]

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
    c = Config(); c.write_to_file('config/json_run_configs/run_config_verify_production_cfg.json')
    print('=== verify/restore production FEU cfg (no overrides) ===')
    print('expect on hardware: RdClk 4.0 (25 MHz) / WrClk 6.0 (60 ns) / AdcDel 8 / RdDel 1 / SparseRd 0')
    print('Launch: .venv/bin/python daq_control.py run_config_verify_production_cfg.json')
    print('Verify: .venv/bin/python dream_scripts/feu_runctrl_reg.py --expect-adcdel 8')
