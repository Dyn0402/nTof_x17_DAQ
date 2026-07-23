#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_rddel_test.py -- does DreamRdDel=1 cost us per-event readout time? (2026-07-23, beam off)

FINDING UNDER TEST. `Feu_RunCtrl_RdDel` (RunControl 0x200008 bit 22, "DreamRdDel") is set to **1** on
all 8 FEUs -- verified by live peek 2026-07-23 12:21 (dream_scripts/feu_runctrl_reg.py).

FEU manual 3.2.3 on this bit:
  * DEFAULT is 0, and the bit is "intended for tests".
  * With 0: "the readout of Dream ASICs starts immediately after the trigger. This is default
    operation."
  * With 1: "the very first Dream Read signal in the train can be delayed by a fixed latency. The
    delay is hardcoded and equals 1536 core clock cycles" -- 12.3 us @125 MHz core, 15.4 us @100 MHz.
    Purpose: "At a low trigger rate, this guaranties that the Dream Read signals will never overlap
    with the corresponding Dream Trigger signal."

CORRECTION to PLAN_2026-07-23_beamoff_pulser_hour.md sec.0: the plan states this is *stale state
inherited from the pedestal run* and "not in our cfg". It IS in our cfg -- `Tcm_Mx17_July_ZS.cfg:198`
(and the Raw template line 193) explicitly carry `Feu * Feu_RunCtrl_RdDel 1`. So this is a deliberate
template line, not inheritance. (Likely mis-grepped as the *register* name `Rd2AdcDataDel` instead of
the cfg keyword.) The test is unchanged and still worth doing; only the provenance story changes --
and it is now a one-line cfg fix rather than a state-hygiene problem.

WHY THE VALUE CAN STICK (not clamped like the watermarks): our data runs use `Sys DaqRun Trig Ext`,
and that RunCtrl branch (RunCtrl.c ~1328) sets ONLY `Feu_PreScale_EvtData` -- it never assigns
`Feu_RunCtrl_RdDel`. The pedestal phase's ExtSyn branch likewise leaves it alone (only the
Constant/NegExp branches force it). Verified by reading RunCtrl.c. Peek anyway, every sub-run.

PREDICTIONS (per-event cycle is currently ~92 us = 10847 Hz at RdClk 4.0 / n32 / IPD 2):
  * If the 1536-cycle delay applies ONCE PER EVENT -> rddel0 gives +13 to +20 % rate. Big, obvious.
  * If it applies only to the FIRST event of a queued train -> saturating pulser shows NULL. That is
    NOT a refutation: it means the lever lives in the isolated-trigger regime and the observable is
    per-event LATENCY (flash-comb relevant), needing a paced knee scan or beam.

ACCEPTANCE IS ON DATA SANITY, NOT RATE. This bit exists to stop Dream Read overlapping Dream Trigger.
Our trigger pulse is only 32 WCk = 1.92 us so the protection should not be load-bearing, but a
desynchronised SCA readout is exactly the failure this could cause. Gate on: ZS tracer channels
0/224/511 present ~100 % of events, chan-0 baseline median ~263 (CmnPedOffset=256), hits/event ~96,
amplitudes physical. ANY tracer loss or baseline shift => reject, report, revert (drop the cfg line).

ALSO WATCHING: the idle peek showed `PedSub=1` while every ZS cfg sets Pd=0 (Option B). That is
probably post-pedestal-phase residue, but the per-sub-run peek during a DATA run settles it -- the
tool prints PedSub. If PedSub reads 1 mid-data-run, that is a separate and more serious finding
(firmware pedestal subtraction on top of the offline subtraction = double-subtract).

TRIGGER (beam off): M4.C <- M6.D pulser, saturating 20 kHz. set_veto_open.py REQUIRED first.
RESTORE AFTER: trigger_mode.py scint --singles --ps-pickup + set_pulser.py.
NOTE: dream_daq server must carry the rd_del plumbing (restarted 2026-07-23 12:28).
"""
from run_config_beam import Config as BeamConfig

ZS_PED_SET = 'zs_k8_tracer_from_07-18-26_14-06-43'
LATENCY, N_SAMPLES, IPD_FIXED, SUBRUN_MIN = 35, 32, 2, 0.75

# (label, rd_del)  None = leave the template's value (1) alone -> the drift bracket
POINTS = [
    ('nom_a',    None),  # bracket: template default (RdDel 1)
    ('rddel1',   1),     # explicit 1 -- proves the knob reaches hw AND reproduces nom_a
    ('rddel0',   0),     # THE MEASUREMENT
    ('nom_b',    None),  # bracket
    ('rddel0_b', 0),     # repeat of the measurement
    ('nom_c',    None),  # closing bracket
]


class Config(BeamConfig):
    def _set_defaults(self, config_path=None):
        super()._set_defaults(config_path)
        self.run_name = 'rddel'
        self.n1081b_scan = 'off'
        self.run_out_dir = f'{self.data_out_dir}{self.run_name}/'
        self.dream_daq_info['run_directory'] = f'/home/mx17/july_dream/dream_run/{self.run_name}/'
        self.dream_daq_info['data_out_dir'] = f'{self.run_out_dir}'
        self.processor_info['run_dir'] = f'{self.run_out_dir}'
        self.hv_info['run_out_dir'] = self.run_out_dir
        self.resume = False
        self.trigger = ('DreamRdDel test: template sets Feu_RunCtrl_RdDel=1 (manual default 0, '
                        '"intended for tests", delays first Dream Read by 1536 core clocks ~12-15 us). '
                        'Does 0 buy per-event readout time? RdDel 1/0 bracketed x3, saturating pulser, '
                        'ZS k8, n32, RdClk 4.0, IPD 2. Accept on data sanity not rate. HV as-is.')
        self.dream_daq_info.update({
            'daq_config_template_path': f'{self.base_out_dir}dream_config/Tcm_Mx17_July_ZS.cfg',
            'zero_suppress': True, 'common_noise_subtraction': True, 'pedestal_subtraction': False,
            'zs_type': 'tpc', 'zs_check_sample': 4, 'inter_packet_delay': IPD_FIXED,
            'pedestals_dir': f'{self.base_out_dir}pedestals/', 'pedestals': ZS_PED_SET,
            'latency': LATENCY, 'n_samples_per_waveform': N_SAMPLES,
        })
        self.sub_runs = []
        for label, rd in POINTS:
            sr = {'sub_run_name': label, 'run_time': SUBRUN_MIN, 'post_pause_s': 0,
                  'inter_packet_delay': IPD_FIXED, 'pedestals': ZS_PED_SET, 'hvs': {}}
            if rd is not None:
                sr['rd_del'] = rd
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


if __name__ == '__main__':
    c = Config(); c.write_to_file('config/json_run_configs/run_config_rddel_test.json')
    print('=== DreamRdDel test (saturating pulser, RdClk 4.0, n32, IPD 2) ===')
    print('reference rate at this point: ~10847 Hz (92 us/event); 1536 core clk = 12.3 us @125MHz / 15.4 us @100MHz')
    for label, rd in POINTS:
        print(f"  {label:9s} rd_del={'(tmpl=1)' if rd is None else rd}")
    print(f'\n{len(c.sub_runs)} sub-runs x {SUBRUN_MIN} min')
    print('\nPer sub-run VERIFY: .venv/bin/python dream_scripts/feu_runctrl_reg.py --expect-rddel <N>')
    print('Launch: .venv/bin/python daq_control.py run_config_rddel_test.json')
    print('Restore: trigger_mode.py scint --singles --ps-pickup ; set_pulser.py')
