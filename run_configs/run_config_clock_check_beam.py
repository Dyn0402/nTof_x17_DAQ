#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_clock_check_beam.py — run_69, 2026-07-23. QUICK beam confirmation of the new
25 MHz DREAM read clock: how many events do we actually get, and how are they distributed
in time since the flash?

    .venv/bin/python run_config_clock_check_beam.py
    ./start_run.sh run_config_clock_check_beam.json           # ~24 min data, ~30 min wall

WHY. The read clock went from 16.7 -> 25 MHz as the production default on 2026-07-23
(commit 285b9f5: SAMPLE_PERIOD_CLOCK_DIVS[60] = ('4.0','6.0')). That 1.5x was MEASURED on
a saturating 20 kHz pulser with beam off (7231 -> 10847 Hz, 0 FEU drops, docs/
CLOCK_RATE_SCAN_2026-07-23.md). It has never been seen with beam. Two open questions the
pulser cannot answer:

  1. YIELD. On 10 GbE we concluded we are TRIGGER-limited, not readout-limited
     (~95 ev/spill plateau, memory dream-rate-answer-trigger-limited). If that is true the
     faster clock buys NOTHING in ev/spill and the 1.5x is pure headroom. If it is false —
     i.e. the DAQ was still eating into the post-flash burst — the clock shows up directly.
  2. TIME DISTRIBUTION. The interesting failure mode is not the total but WHERE in the
     1->81 ms window the events land. A shorter readout cycle should let more of the dense
     early-post-flash burst through, i.e. move events toward small t-since-flash. That is
     the whole point of the flash-comb work (docs/DREAM_flash_comb_study_2026-07-19.md) and
     it is invisible to a pulser, which has no flash.

DESIGN — A/B BRACKETED, NOT a single "new" run. The beam intensity drifts pulse to pulse,
so a bare "we got N ev/spill at 25 MHz" is worth little. Every point is paired: the run
alternates NEW (RdClk 4.0, the new default) and OLD (RdClk 6.0, forced via the
`rdclk_div` per-sub-run override, which overrides the sample_period preset) and closes on
NEW, so drift shows up as a disagreement between the brackets rather than as a fake effect.
The sampling window is IDENTICAL at both points — 32 smp x 60 ns (WrClk 6.0) is untouched;
only how fast bytes leave the SCA changes. So the two are directly comparable event for
event, no re-calibration.

  new_a  RdClk 4.0  IPD 5      the new production default
  old_a  RdClk 6.0  IPD 5      yesterday's clock, everything else identical
  new_b  RdClk 4.0  IPD 5
  old_b  RdClk 6.0  IPD 5
  n_ipd2 RdClk 4.0  IPD 2      does the faster clock let IPD go lower for free?
  new_c  RdClk 4.0  IPD 5      closing bracket — must reproduce new_a/new_b

THRESHOLD 0.90 MIP, DELIBERATELY THE SATURATING END. A clock that only buys headroom is
invisible unless the DAQ is the thing being asked for more. 0.90 MIP was the most
saturating point of the run_67 ladder (recon: 77.1 ev/spill, and GEANT predicts ~120
trig/pulse there vs a measured ~95 DAQ plateau), so it is the one place a readout
improvement can still show. Held FIXED for all six sub-runs via the m090On schedule tag —
scan_control simply re-asserts the same four section thresholds each sub-run (a no-op after
the first) and touches nothing else.

WATERMARK: DEFAULT, not run_67's Hwm 2. run_67 forced Main_Trig_OvrWrnHwm = 2 to spread
events across the window at a measured ~10% yield cost. This run wants the honest yield, so
it leaves the watermark alone (template 20, RunCtrl cap ~11).
  *** Therefore ev/spill here is NOT directly comparable to run_67's numbers. ***

HV: single point, drift 600 / resist 530 (det D = A/B/C) — the run_67 recon point, so the
detector state is a known one. No HV scan: this run is about the DAQ, not the gas gain.

MESH: NOT TOUCHED. The schedule tag carries no mesh_b target, and after the 2026-07-23
re-cable the mesh sits on M6 SEC_B out2/out3 with SEC_B/C enables aliased
(docs/HANDOFF_2026-07-23_m6_mesh_recable_out34.md, ..._m6_secBC_control_aliasing.md).
Whatever state M6 is in when the run starts is the state for the whole run — RECORD IT
(`set_mesh_injection.py status`) before launching, because the time distribution depends on
it (mesh injection RELOCATES the blindness, memory run64-mesh-injection-result).

WHAT TO READ OUT
  per sub-run rate  : .venv/bin/python dream_scripts/feu_trig_counters.py --latch
  data sanity       : decoded baseline ~256, amplitudes physical, no processor_watcher
                      decode errors, eventId discontinuities ~0. 25 MHz is above the ASIC's
                      rated 20 MHz RCk — a point that reads FAST but decodes badly is not a
                      win. Check this BEFORE quoting any rate.
  ev/spill + time   : offline, flash-anchored (PS co-framed at 1800 ns -> flash ~smp 13,
                      singles MM ~smp 11), same tooling as the run_67 / recov_0722 analyses.
                      ALWAYS quote the window start (1 ms) with any band number — memory
                      band-optimisation-window-start.

TRIGGER (set ONCE pre-run, unchanged from run_67):
  M4.C = or_veto(Singles, lemo0) gated by the N93B ~1 -> 81 ms post-flash window;
  M4.D = OR(lemo0 = PS/gamma-flash delayed 1800 ns, lemo1 = C-out).
  32 smp x 60 ns (1.92 us), latency 35, RAW.

PRE-RUN (boards free — check config/n1081b_access/ first):
  .venv/bin/python n1081b/trigger_mode.py scint --singles --ps-pickup
  .venv/bin/python n1081b/set_ps_trigger_delay.py --delay 1800
  verify: trigger_mode.py status          -> C or_veto lemos=[0], D lemos=[0,1]
          set_ps_trigger_delay.py --show  -> delay 1800, enable_gd True
          set_mesh_injection.py status    -> RECORD whatever it says; do not change it
  RESTART the dream_daq server if it predates commit 285b9f5 — an un-restarted server drops
  new per-sub-run knobs SILENTLY, and `rdclk_div` would then be ignored, making old_a/old_b
  duplicates of new_a/new_b (memory stale-dream-daq-server-drops-cfg-overrides).
Launch: ./start_run.sh run_config_clock_check_beam.json
FOLLOW-ON: run_config_plastic_plateau.py (run_70) reuses this exact readout config.
"""
import os

# --- repo-root shim (run_config_beam/base live one dir up) ---
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from run_config_beam import Config as BeamConfig

RUN_NUM = 69

# ---- DREAM readout (run_67 recipe: PS+singles co-framed in 32 smp) ----
LATENCY = 35
N_SAMPLES = 32
SAMPLE_PERIOD = 60        # -> WrClk 6.0 (60 ns/sample) and, since 07-23, RdClk 4.0 (25 MHz)
IPD = 5                   # on the 10 GbE yield plateau with margin

SUBRUN_MIN = float(os.environ.get('SUBRUN_MIN', '4'))   # ~17 spills @ 14.4 s supercycle

# ---- single HV point (run_67 recon point) ----
DRIFT_V = 600
RESIST_V = 530
DET_D_OFFSET = 0          # det D resist = A/B/C

# Plastic threshold held fixed at the saturating end of the run_67 ladder.
THR_TAG = 'm090On'        # A-118 / B-139 / C-157 / D-134 mV; no mesh_b target

# (label, rdclk_div override or None = the 07-23 default 4.0, inter_packet_delay)
POINTS = [
    ('new_a',  None, IPD),   # 25.0 MHz read — production default
    ('old_a',   6.0, IPD),   # 16.7 MHz read — pre-07-23 clock, everything else identical
    ('new_b',  None, IPD),
    ('old_b',   6.0, IPD),
    ('n_ipd2', None,   2),   # faster clock + lower IPD: is there anything left to take?
    ('new_c',  None, IPD),   # closing bracket
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
        # scan_control re-asserts the SAME plastic thresholds every sub-run (no-op after the
        # first) and touches nothing else — the m090On tag carries no mesh_b target.
        self.n1081b_scan = 'on'

        self.trigger = (
            f'QUICK beam confirmation of the 25 MHz DREAM read clock ({self.run_name}). '
            f'scint --singles --ps-pickup, PS+singles co-framed in 32 smp (latency 35, M4.D '
            f'in0 G&D 1800 ns -> flash ~smp 13, MM ~smp 11); N93B window ~1->81 ms post-flash. '
            f'32 smp x 60 ns (WrClk 6.0, window UNCHANGED), RAW. A/B bracketed read clock: '
            f'RdClk 4.0 (25 MHz, the 07-23 default) vs RdClk 6.0 (16.7 MHz) forced per '
            f'sub-run, new/old/new/old/(IPD 2)/new, {SUBRUN_MIN:g} min each. IPD 5 except the '
            f'n_ipd2 point. Plastic threshold held at 0.90 MIP (A-118/B-139/C-157/D-134) via '
            f'the m090On tag — the saturating end, where a readout gain can still show. Walls '
            f'0.5 MIP (25/35/34/36). FEU watermark left at DEFAULT (run_67 used Hwm 2, so '
            f'ev/spill here is NOT comparable to run_67). HV single point drift {DRIFT_V} / '
            f'resist {RESIST_V} (det D = A/B/C). M6/mesh NOT touched — record its state '
            f'pre-run. Scint PMT bias at 07-19 Y88 setpoints. Ar/Iso 90/10, 3He, no Pb. '
            f'Deliverable: ev/spill and time-since-flash distribution, new clock vs old.')

        self.dream_daq_info.update({
            'daq_config_template_path': f'{self.base_out_dir}dream_config/Tcm_Mx17_July.cfg',
            'zero_suppress': False,
            'latency': LATENCY,
            'n_samples_per_waveform': N_SAMPLES,
            'sample_period': SAMPLE_PERIOD,
            'inter_packet_delay': IPD,
        })

        def _drift(dv):
            return {'0': dv, '1': dv, '2': dv, '3': dv}             # card 9 ch 0-3

        def _resist(v):
            return {'1': v, '2': v, '3': v, '4': v - DET_D_OFFSET}  # card 5 ch 1-4

        self.sub_runs = []
        for k, (label, rd, ipd) in enumerate(POINTS):
            # sub_run_name MUST lead with the schedule tag — scan_control keys on it.
            sr = {
                'sub_run_name': f'{THR_TAG}_{label}_{k:03d}',
                'run_time': SUBRUN_MIN, 'post_pause_s': 0,
                'inter_packet_delay': ipd,
                'hvs': {'5': _resist(RESIST_V), '9': _drift(DRIFT_V)},
            }
            if rd is not None:
                sr['rdclk_div'] = rd     # overrides the sample_period-derived RdClk_Div
            self.sub_runs.append(sr)

        # Re-merge the scintillator PMT bias holds (plastics card 07, liquids card 08).
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
    c.write_to_file('config/json_run_configs/run_config_clock_check_beam.json')
    n = len(c.sub_runs)
    data_min = sum(sr['run_time'] for sr in c.sub_runs)
    print(f'=== {c.run_name} — 25 MHz read clock, beam confirmation ===')
    print(f'readout  : {N_SAMPLES} smp x {SAMPLE_PERIOD} ns, latency {LATENCY}, RAW; '
          f'PS delay 1800 (co-framed)')
    print(f'threshold: 0.90 MIP held fixed via {THR_TAG} (A-118/B-139/C-157/D-134)')
    print(f'HV       : drift {DRIFT_V} / resist {RESIST_V} (det D = A/B/C), single point')
    print(f'watermark: DEFAULT (not run_67\'s Hwm 2) -> ev/spill NOT comparable to run_67')
    print(f'\n{"sub-run":>16} {"RdClk":>7} {"read":>9} {"IPD":>4}')
    for k, (label, rd, ipd) in enumerate(POINTS):
        rd_s = '4.0' if rd is None else f'{rd:.1f}'
        print(f'{THR_TAG + "_" + label + f"_{k:03d}":>16} {rd_s:>7} '
              f'{100.0 / float(rd_s):>6.1f} MHz {ipd:>4}')
    print(f'\n{n} sub-runs x {SUBRUN_MIN:g} min = {data_min:.0f} min data '
          f'(~{data_min + n:.0f} min wall); ~{data_min / 10 * 10.4:.0f} GB HDD worst case')
    print('\nPre-run: trigger_mode.py scint --singles --ps-pickup ; '
          'set_ps_trigger_delay.py --delay 1800')
    print('         set_mesh_injection.py status   # RECORD, do not change')
    print('         RESTART dream_daq server if it predates commit 285b9f5 (rdclk_div!)')
    print('Launch : ./start_run.sh run_config_clock_check_beam.json')
    print('Per pt : .venv/bin/python dream_scripts/feu_trig_counters.py --latch')
