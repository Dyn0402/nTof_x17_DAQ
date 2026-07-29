#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_wm_pulser.py — FEU trigger-FIFO WATERMARK characterization, pulser-driven
(2026-07-22, beam-off window).

WHY THIS EXISTS (supersedes the 2026-07-19 "raise the HWM" null result)
----------------------------------------------------------------------
The 2026-07-19 comb study swept `Main_Trig_OvrWrnHwm` 20 -> 48 and got a null, concluding
"the FIFO never reaches the HWM (occ 11)". That conclusion was an artifact: RunCtrl
OVERWRITES the cfg watermarks. RunCtrl.c:998-1051 computes its own cap from the DREAM
derandomiser geometry

    drm_derand_buf = 512 - TRIGLAT              (TRIGLAT = cfg latency, dream_reg[12])
    drm_evt_buf    = drm_derand_buf / NbOfSamples
    buf>16 -> Hwm=buf-4, Lwm=buf-8 ;  buf>8 -> Hwm=buf-3, Lwm=buf-6 ;  ...

and then clamps: `if (cfg_Hwm > cap) cfg_Hwm = cap`. Both 20 and 48 exceed the cap, so BOTH
were silently forced to the same value -- nothing was ever varied. Confirmed 2026-07-22 by
reading 0x100008 live: cfg says Hwm 20 / Lwm 16, hardware holds Hwm 12 / Lwm 9 (and the
formula reproduces all ten drm_evt_buf values recorded in deadtime_db.csv exactly).

The clamp is ONE-DIRECTIONAL, so values BELOW the cap pass through untouched. That is the
untested direction and the only one that can work. At latency 35 / n32 the cap is Hwm=11.

WHAT THIS MEASURES
------------------
Fixed 1 kHz pulser (saturating: the flash-off study showed fixed >=500 Hz pins the FEU at
its ~312 Hz readout ceiling, so the FIFO genuinely fills). One sub-run per watermark. Read
per sub-run with `dream_scripts/feu_trig_counters.py`:

    maxFIFOocc  -- prediction: pins at Hwm-1 for every setting
    fifoDrop / closeDrop
    accepted    -- does throttling earlier cost or gain throughput?

Sub-run 1 is the un-overridden control (cfg 20/16 -> clamped to the cap) and MUST reproduce
the cap, proving the clamp; the rest probe below it.

Counters reset per RunCtrl run, so one watermark per sub-run -- maxFIFOocc is a max-hold and
cannot be swept inside a single run.

HV: sub-runs carry NO resist/drift setpoints -> HV is left exactly where the previous run put
it (beam-off characterization, no HV ramp, no settle cost). Scint PMT holds are merged below.

TRIGGER: M4.C -> M6.D pulser (`trigger_mode.py flash_random`, `set_pulser.py --fixed
--period 1000000`). RESTORE AFTER: `trigger_mode.py scint --singles --ps-pickup` and
`set_pulser.py` (back to Poisson 1.5 ms).
"""
# --- repo-root shim (run_config_beam/base live one dir up) ---
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from run_config_beam import Config as BeamConfig

ZS_PED_SET = 'zs_k8_tracer_from_07-18-26_14-06-43'
LATENCY, N_SAMPLES, IPD_FIXED, SUBRUN_MIN = 35, 32, 10, 1.0

# (label, ovr_wrn_hwm, ovr_wrn_lwm). None = no override -> RunCtrl clamps cfg 20/16 to the cap.
# At latency 35 / n32: drm_evt_buf = (512-35)//32 = 14 -> cap Hwm=11, Lwm=8.
WM_ORDER = [
    ('base', None, None),   # control: expect hardware Hwm=11, Lwm=8, maxFIFOocc=10
    ('hwm6', 6, 3),         # expect maxFIFOocc=5
    ('hwm3', 3, 1),         # expect maxFIFOocc=2
    ('hwm1', 1, 0),         # extreme: expect maxFIFOocc=0, near-single-event pacing
]


class Config(BeamConfig):
    def _set_defaults(self, config_path=None):
        super()._set_defaults(config_path)
        self.run_name = 'wm_pulser2'
        self.n1081b_scan = 'off'   # static pulser trigger; no scan watcher running
        self.run_out_dir = f'{self.data_out_dir}{self.run_name}/'
        self.dream_daq_info['run_directory'] = f'/home/mx17/july_dream/dream_run/{self.run_name}/'
        self.dream_daq_info['data_out_dir'] = f'{self.run_out_dir}'
        self.processor_info['run_dir'] = f'{self.run_out_dir}'
        self.hv_info['run_out_dir'] = self.run_out_dir
        self.resume = False
        self.trigger = ('FEU trigger-FIFO watermark characterization, fixed 1 kHz pulser '
                        '(M4.C <- M6.D). OvrWrnHwm swept BELOW the RunCtrl cap (11 at '
                        'lat35/n32) -- the direction the 2026-07-19 sweep could not reach '
                        'because RunCtrl clamps values above the cap. HV left as-is.')
        self.dream_daq_info.update({
            'daq_config_template_path': f'{self.base_out_dir}dream_config/Tcm_Mx17_July_ZS.cfg',
            'zero_suppress': True, 'common_noise_subtraction': True, 'pedestal_subtraction': False,
            'zs_type': 'tpc', 'zs_check_sample': 4, 'inter_packet_delay': IPD_FIXED,
            'pedestals_dir': f'{self.base_out_dir}pedestals/', 'pedestals': ZS_PED_SET,
            'latency': LATENCY, 'n_samples_per_waveform': N_SAMPLES,
        })
        self.sub_runs = []
        for label, hwm, lwm in WM_ORDER:
            sr = {'sub_run_name': f'wm_{label}', 'run_time': SUBRUN_MIN, 'post_pause_s': 0,
                  'inter_packet_delay': IPD_FIXED, 'pedestals': ZS_PED_SET, 'hvs': {}}
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
    c = Config(); c.write_to_file('config/json_run_configs/run_config_wm_pulser.json')
    buf = (512 - LATENCY) // N_SAMPLES
    cap_h, cap_l = (buf - 3, buf - 6) if buf > 8 else (buf - 2, buf - 4)
    print('=== FEU watermark characterization (pulser) ===')
    print(f'latency={LATENCY} n={N_SAMPLES} -> drm_evt_buf={buf}  RunCtrl cap: Hwm={cap_h} Lwm={cap_l}')
    for sr in c.sub_runs:
        h = sr.get('ovr_wrn_hwm')
        eff = cap_h if h is None else min(h, cap_h)
        print(f"  {sr['sub_run_name']:9s} cfg_hwm={str(h):>4}  -> effective Hwm={eff:>2}  "
              f"predicted maxFIFOocc={eff - 1}")
    print(f'\n{len(c.sub_runs)} sub-runs x {SUBRUN_MIN} min')
    print('Launch: ./start_run.sh run_config_wm_pulser.json')
