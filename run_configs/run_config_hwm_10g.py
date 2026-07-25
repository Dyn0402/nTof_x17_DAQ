#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_hwm_10g.py — OvrWrnHwm scan in the NEW regime: 10 GbE, RAW, 1 ms window start.

WHY RE-RUN A SCAN THAT ALREADY CAME BACK NEGATIVE
-------------------------------------------------
The 2026-07-22 morning scan (`hwm_beam`) found lowering OvrWrnHwm HURT: 23.4 -> 13.4
events/spill in the 4-10 ms band. **That result does not transfer, because the regime
changed underneath it.** Measured with tools/flash_anchored_band.py:

    morning runs : gap from flash to next event = 5.02 ms   (window opened at 5 ms)
    now          : gap from flash to next event = 1.00 ms   (window opens at 1 ms)

At a 5 ms window start the ~11-event flash burst drains at 5-10 ms, i.e. INSIDE the band --
so throttling it deleted the very events being counted, and of course it hurt. At a 1 ms
start the same burst drains at 1.0 -> 1.0 + Hwm x t_cycle, i.e. almost entirely OUTSIDE the
band, where the detector is still flash-blind and the events are useless. Truncating it
should now cost ~nothing and free the FEU earlier. Opposite sign expected.

THE MECHANISM BEING TESTED
--------------------------
`maxFIFOocc == Hwm` exactly (prior study), and the hardware currently reads **Hwm 11** --
which is precisely the observed 11-event flash burst. So Hwm IS the burst length. At
IPD 5 (cycle 314 us) the burst spans 1.00 -> 1.00 + 11 x 0.314 = **4.45 ms**, so only its
last ~1.4 events land in the 4-10 ms band; the other ~9.6 are spent in the flash-blind
region. Lowering Hwm should end the burst sooner and, if the post-burst dead-cycle scales
with burst length, bring the FEU back live earlier and more often inside the band.

    Hwm 11 -> burst ends 4.45 ms      Hwm 6 -> 2.88 ms
    Hwm  8 -> 3.51 ms                 Hwm 4 -> 2.26 ms
    Hwm  3 -> 1.94 ms                 Hwm 2 -> 1.63 ms

PREDICTION, WRITTEN BEFORE THE MEASUREMENT
------------------------------------------
Band capacity at IPD 5 is 6 ms / 0.314 ms = ~19 events; we currently record **12.00**. So
there IS headroom -- the band is not readout-capacity-limited at IPD 5, which is exactly why
this point was chosen (at IPD 10 capacity is 12.7 vs 11.6 recorded, i.e. saturated, and any
watermark gain would be invisible).
  * If the post-burst dead-cycle scales with Hwm -> band rises toward ~19 as Hwm falls,
    with a turnover once Hwm gets so small that sustained throughput drops (prior beam-off
    data: throughput FLAT from Hwm 11 down to 3, falling only at Hwm 1).
  * If it does not -> band stays ~12 and the burst was never the constraint; the limit is
    the trigger rate inside the band, and no DAQ knob will fix it.
Either outcome is decisive. Expected optimum Hwm 3-4 if the first branch holds.

CONFIG: RAW n32, lat 35, IPD 5, PS+singles co-framed beam trigger, HV drift 600/resist 530.
Bracketed by the Hwm-11 default at BOTH ends so drift cannot fake a trend.

⚠ VERIFY THE KNOB ACTUALLY MOVED. A null here is only believable if the watermark reached
the hardware. RunCtrl clamps these one-directionally (lowering works, raising does not), and
a stale long-lived dream_daq server silently drops overrides. During/after the run:
    .venv/bin/python dream_scripts/feu_trig_counters.py     # Hwm column + maxOcc
and check the archived per-subrun cfg for `Main_Trig_OvrWrnHwm`.

Analysis: ~/beam_july/analysis/flash_comb/tools/flash_anchored_band.py --run hwm_10g
  -> report band_FLASH (events 4-10 ms AFTER THE FLASH) and gapAfter, never band_1stEv alone.
"""
# --- repo-root shim (run_config_beam/base live one dir up) ---
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from run_config_beam import Config as BeamConfig

LATENCY, N_SAMPLES, IPD_FIXED, SUBRUN_MIN = 35, 32, 5, 3.0
DRIFT, RESIST = 600, 530

# (label, ovr_wrn_hwm, ovr_wrn_lwm); None = no override -> RunCtrl cap, currently 11.
# LWM is the hysteresis partner and MUST stay < HWM.
POINTS = [
    ('hwm11_a', None, None),   # control = current production behaviour
    ('hwm8',    8,    4),
    ('hwm6',    6,    3),
    ('hwm4',    4,    2),
    ('hwm3',    3,    1),
    ('hwm2',    2,    1),
    ('hwm11_b', None, None),   # bracket: must reproduce hwm11_a
]


class Config(BeamConfig):
    def _set_defaults(self, config_path=None):
        super()._set_defaults(config_path)
        self.run_name = 'hwm_10g'
        self.n1081b_scan = 'off'
        self.run_out_dir = f'{self.data_out_dir}{self.run_name}/'
        self.dream_daq_info['run_directory'] = f'/home/mx17/july_dream/dream_run/{self.run_name}/'
        self.dream_daq_info['data_out_dir'] = f'{self.run_out_dir}'
        self.processor_info['run_dir'] = f'{self.run_out_dir}'
        self.hv_info['run_out_dir'] = self.run_out_dir
        self.resume = False
        self.trigger = ('10 GbE / 1 ms-window OvrWrnHwm scan (11/8/6/4/3/2, bracketed by 11) '
                        'under PS+singles co-framed beam trigger, RAW n32 lat35 IPD5. Tests '
                        'whether truncating the flash burst - which at a 1 ms window start '
                        'is spent in the flash-blind 1-4 ms region - frees the FEU to record '
                        f'more in 4-10 ms. HV drift {DRIFT} / resist {RESIST}.')
        self.dream_daq_info.update({
            'latency': LATENCY, 'n_samples_per_waveform': N_SAMPLES,
            'zero_suppress': False, 'inter_packet_delay': IPD_FIXED,
        })
        hv = {'5': {'1': RESIST, '2': RESIST, '3': RESIST, '4': RESIST},
              '9': {'0': DRIFT, '1': DRIFT, '2': DRIFT, '3': DRIFT}}
        self.sub_runs = []
        for label, hwm, lwm in POINTS:
            sr = {'sub_run_name': label, 'run_time': SUBRUN_MIN, 'post_pause_s': 0,
                  'inter_packet_delay': IPD_FIXED,
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
    c = Config(); c.write_to_file('config/json_run_configs/run_config_hwm_10g.json')
    dd = c.dream_daq_info
    print('=== OvrWrnHwm scan, 10 GbE / 1 ms window (RAW) ===')
    print(f"zero_suppress={dd['zero_suppress']}  n={dd['n_samples_per_waveform']}  "
          f"lat={dd['latency']}  IPD={dd['inter_packet_delay']}  pedestals={dd['pedestals']}")
    cyc = N_SAMPLES * (4.83 + 0.998 * IPD_FIXED) / 1000
    print(f"cycle {cyc:.3f} ms/event -> band capacity {6.0/cyc:.1f} events in 4-10 ms")
    for sr in c.sub_runs:
        h = sr.get('ovr_wrn_hwm')
        n = h if h is not None else 11
        print(f"  {sr['sub_run_name']:9s} cfg_hwm={str(h):>5} cfg_lwm={str(sr.get('ovr_wrn_lwm')):>5}"
              f"   burst 1.00 -> {1.0 + n*cyc:5.2f} ms")
    print(f'\n{len(c.sub_runs)} sub-runs x {SUBRUN_MIN} min = {len(c.sub_runs)*SUBRUN_MIN:.0f} min')
    print('Launch: bash bash_scripts/start_run.sh run_config_hwm_10g.json')
