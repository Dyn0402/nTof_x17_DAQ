#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_clock_pair_050mip.py — run_71 (BUILT 2026-07-23, NOT YET RUN). The clean
25-vs-16.7 MHz read-clock A/B test, pinned at 0.50 MIP — the one regime where the faster
clock is predicted to recover yield.

    .venv/bin/python run_config_clock_pair_050mip.py
    ./start_run.sh run_config_clock_pair_050mip.json          # ~24 min data, ~30 min wall

WHY THIS EXISTS. run_69 compared the clocks at 0.90 MIP and found NO yield difference —
correctly, because at that rate we are trigger-limited and the read clock is pure headroom.
run_70's flash-anchored analysis (~/beam_july/analysis/flash_timing_threshold/) then found
the exception: the 4-8 ms time-since-flash band yield DROPS as the plastic threshold is
lowered (10.9 -> 6.1 events/flash) while the total RISES (37 -> 103). Lowering a
discriminator can only ADD triggers, so the 4-8 ms DROP is events being LOST to DEADTIME:
the 1-4 ms window saturates (~16.5/flash) and its per-event readout spills past 4 ms,
shadowing 4-8 ms. The late window (20-81 ms) escapes it and dominates the clock-insensitive
total — which is why run_69 (total counts) and run_70 (total) both saw the clock do nothing.

THE READ CLOCK SETS THE SHADOW LENGTH. Per-event readout is ~262 us at 25 MHz vs ~314 us at
16.7 MHz (n32, IPD 5). The 1-4 ms readout demand is ~144% of the window at 25 MHz but ~173%
at 16.7 MHz, so the slower clock's shadow reaches FURTHER into 4-8 ms. Prediction:

    *** at 0.50 MIP, the 4-8 ms band holds measurably MORE events at 25 MHz than at
        16.7 MHz — a first-order effect (tens of %), NOT the sub-noise wash-out the
        totals showed. This is the falsifiable test. ***

DESIGN. Identical to run_69 EXCEPT the threshold is pinned at 0.50 MIP (the most saturating
point, biggest 1-4 ms shadow) and the clock alternates new/old across SIX sub-runs for
statistics and drift immunity. Everything else — PS+singles routing, PS delay 1800, latency
35, 32 smp x 60 ns, IPD 5, RAW, default FEU watermark, HV drift 600 / resist 530 — is held
fixed so the ONLY variable is the read clock. The old clock is forced via the per-sub-run
`rdclk_div=6.0` override (overrides the sample_period-derived RdClk_Div; WrClk/sampling
window UNCHANGED at 60 ns, so the two clocks are comparable event-for-event).

  new_a  RdClk 4.0  25.0 MHz   |  old_a  RdClk 6.0  16.7 MHz
  new_b  RdClk 4.0             |  old_b  RdClk 6.0
  new_c  RdClk 4.0             |  old_c  RdClk 6.0
  fully interleaved -> any beam drift averages out; ~3x4 min per clock = good band stats
  (~57 flashes/sub-run x 6 events/flash in 4-8 ms ~= 340 events/sub-run, ~1000 per clock).

READ IT OUT — the answer is OFFLINE, flash-anchored, NOT from the trigger counters:
  * The FEU trigger counters CANNOT see this. BUSY-vetoed triggers never arrive, so both
    clocks read ~0 drops and similar totals (the loss is in WHERE events land, not how many).
    Do not conclude "no effect" from feu_trig_counters — it is blind to this by construction.
  * Run the run_70 band analysis on run_71:
      cd ~/beam_july/analysis/flash_timing_threshold
      # add run_71's sub-runs to a small driver (analyze_run71.py) that groups by CLOCK
      # instead of threshold, and compares the 4-8 ms per-flash yield 25 vs 16.7 MHz.
    Deliverable: 4-8 ms events/flash at 25 MHz vs 16.7 MHz, with the new/old brackets as the
    error bar. A clean split confirms the deadtime-shadow model; a null refutes it (and says
    even the early band is not readout-limited at these rates).
  * Data-sanity FIRST on every 25 MHz point (above the ASIC's rated 20 MHz RCk): decoded
    baseline ~256, ZS tracers N/A (RAW), no processor_watcher decode errors, eventId gaps
    ~0. A point that reads fast but decodes badly is not a win.

PRE-RUN (boards free — check config/n1081b_access/ first; identical to run_69/70):
  .venv/bin/python n1081b/trigger_mode.py scint --singles --ps-pickup
  .venv/bin/python n1081b/set_ps_trigger_delay.py --delay 1800
  verify: trigger_mode.py status          -> C or_veto lemos=[0], D lemos=[0,1]
          set_ps_trigger_delay.py --show  -> delay 1800, enable_gd True
          set_mesh_injection.py status    -> RECORD; do not change (mesh state must match
                                             run_70 or the detector state differs)
  The dream_daq server must post-date the 2026-07-23 rdclk_div knob (commit 285b9f5) or the
  old-clock override is dropped SILENTLY and old_* become duplicates of new_*.
Launch: ./start_run.sh run_config_clock_pair_050mip.json
TEARDOWN: scan_control leaves the plastic threshold at 0.50 MIP; re-apply the standing set
  by hand for the next run. Restore production clock is automatic (next run's sample_period).
"""
import os

# --- repo-root shim (run_config_beam/base live one dir up) ---
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from run_config_beam import Config as BeamConfig

RUN_NUM = 71

LATENCY = 35
N_SAMPLES = 32
SAMPLE_PERIOD = 60        # -> WrClk 6.0 (60 ns/sample); RdClk from the per-sub-run override
IPD = 5

SUBRUN_MIN = float(os.environ.get('SUBRUN_MIN', '4'))

DRIFT_V = 600
RESIST_V = 530
DET_D_OFFSET = 0

THR_TAG = 'm050On'        # 0.50 MIP: A-66 / B-77 / C-87 / D-74 mV; no mesh_b target

# (label, rdclk_div override) — None = the 07-23 default 4.0 (25 MHz); 6.0 = 16.7 MHz.
POINTS = [
    ('new_a', None),
    ('old_a', 6.0),
    ('new_b', None),
    ('old_b', 6.0),
    ('new_c', None),
    ('old_c', 6.0),
]


class Config(BeamConfig):
    def _set_defaults(self, config_path=None):
        super()._set_defaults(config_path)

        self.run_name = f'run_{RUN_NUM}'
        self.run_out_dir = f'{self.data_out_dir}{self.run_name}/'
        self.dream_daq_info['run_directory'] = f'/home/mx17/july_dream/dream_run/{self.run_name}/'
        self.dream_daq_info['data_out_dir'] = f'{self.run_out_dir}'
        self.processor_info['run_dir'] = f'{self.run_out_dir}'
        self.hv_info['run_out_dir'] = self.run_out_dir
        self.resume = False
        self.n1081b_scan = 'on'   # re-asserts 0.50 MIP each sub-run (no-op after first)

        self.trigger = (
            f'READ-CLOCK A/B at 0.50 MIP ({self.run_name}) — the clean 25-vs-16.7 MHz test '
            f'in the deadtime-shadowed regime. scint --singles --ps-pickup, PS+singles '
            f'co-framed in 32 smp (latency 35, M4.D in0 G&D 1800 ns). 32 smp x 60 ns (WrClk '
            f'6.0, window UNCHANGED), IPD {IPD}, RAW, default FEU watermark. Clock alternates '
            f'new/old x3 (RdClk 4.0 = 25 MHz vs RdClk 6.0 = 16.7 MHz, forced per sub-run), '
            f'{SUBRUN_MIN:g} min each. Plastic threshold pinned 0.50 MIP (A-66/B-77/C-87/D-74) '
            f'via m050On. HV drift {DRIFT_V} / resist {RESIST_V} (det D = A/B/C). Deliverable: '
            f'4-8 ms time-since-flash per-flash yield, 25 vs 16.7 MHz (predicted HIGHER at 25). '
            f'Analysis: ~/beam_july/analysis/flash_timing_threshold. Scint PMT bias at 07-19 '
            f'Y88 setpoints. Ar/Iso 90/10, 3He, no Pb. NOT comparable to run_67 (Hwm 2).')

        self.dream_daq_info.update({
            'daq_config_template_path': f'{self.base_out_dir}dream_config/Tcm_Mx17_July.cfg',
            'zero_suppress': False,
            'latency': LATENCY,
            'n_samples_per_waveform': N_SAMPLES,
            'sample_period': SAMPLE_PERIOD,
            'inter_packet_delay': IPD,
        })

        def _drift(dv):
            return {'0': dv, '1': dv, '2': dv, '3': dv}

        def _resist(v):
            return {'1': v, '2': v, '3': v, '4': v - DET_D_OFFSET}

        self.sub_runs = []
        for k, (label, rd) in enumerate(POINTS):
            sr = {
                'sub_run_name': f'{THR_TAG}_{label}_{k:03d}',
                'run_time': SUBRUN_MIN, 'post_pause_s': 0,
                'inter_packet_delay': IPD,
                'hvs': {'5': _resist(RESIST_V), '9': _drift(DRIFT_V)},
            }
            if rd is not None:
                sr['rdclk_div'] = rd
            self.sub_runs.append(sr)

        scint_hvs = {}
        for det in self.detectors:
            if det['name'] not in self.included_detectors:
                continue
            if not str(det.get('det_type', '')).startswith('scintillator'):
                continue
            hc, sp = det.get('hv_channels'), det.get('hv_setpoint')
            if not isinstance(hc, dict) or sp is None:
                continue
            for slot, ch in hc.values():
                scint_hvs.setdefault(str(slot), {})[str(ch)] = sp
        for sr in self.sub_runs:
            for slot, chans in scint_hvs.items():
                sr['hvs'].setdefault(slot, {}).update(chans)


if __name__ == '__main__':
    c = Config()
    c.write_to_file('config/json_run_configs/run_config_clock_pair_050mip.json')
    n = len(c.sub_runs)
    data_min = sum(sr['run_time'] for sr in c.sub_runs)
    print(f'=== {c.run_name} — read-clock A/B at 0.50 MIP (deadtime-shadow test) ===')
    print(f'readout  : {N_SAMPLES} smp x {SAMPLE_PERIOD} ns, latency {LATENCY}, IPD {IPD}, RAW, '
          f'watermark DEFAULT')
    print(f'threshold: 0.50 MIP pinned via {THR_TAG} (A-66/B-77/C-87/D-74)')
    print(f'HV       : drift {DRIFT_V} / resist {RESIST_V} (det D = A/B/C), single point')
    print(f'\n{"sub-run":>16} {"RdClk":>7} {"read":>9}')
    for k, (label, rd) in enumerate(POINTS):
        rd_s = '4.0' if rd is None else f'{rd:.1f}'
        print(f'{THR_TAG + "_" + label + f"_{k:03d}":>16} {rd_s:>7} {100.0 / float(rd_s):>6.1f} MHz')
    print(f'\n{n} sub-runs x {SUBRUN_MIN:g} min = {data_min:.0f} min data (~{data_min + n:.0f} min wall)')
    print('Predicted: 4-8 ms events/flash HIGHER at 25 MHz than 16.7 MHz.')
    print('READ OUT OFFLINE (trigger counters are blind): '
          '~/beam_july/analysis/flash_timing_threshold, grouped by clock.')
    print('Pre-run: trigger_mode.py scint --singles --ps-pickup ; set_ps_trigger_delay.py --delay 1800')
    print('         RESTART dream_daq server if it predates commit 285b9f5 (rdclk_div!)')
