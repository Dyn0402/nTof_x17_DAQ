#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_plastic_plateau.py — run_70, 2026-07-23. QUICK plastic-threshold PLATEAU scan:
where does lowering the plastic discriminator stop buying events, and what does the
time-since-flash distribution look like on the way there?

    .venv/bin/python run_config_plastic_plateau.py
    ./start_run.sh run_config_plastic_plateau.json            # ~28 min data, ~35 min wall

THE QUESTION. run_67's recon already showed the rate going FLAT below 1.13 MIP
(1.41 -> 64.2 ev/spill, 1.13 -> 76.8, 0.90 -> 77.1) — but that was three points, at the OLD
16.7 MHz read clock and with the FEU watermark forced to Hwm 2. Both of those cap the DAQ,
so the observed flatness could be the DAQ saturating rather than the trigger running out of
plastic hits. This run re-measures the same curve with the caps removed:

  * 25 MHz read clock (the 2026-07-23 production default) — 1.5x readout headroom
  * FEU watermark at DEFAULT, not Hwm 2 — recovers the ~10%/pulse Hwm 2 costs
  * six threshold points instead of three, spanning 2.00 -> 0.50 MIP

so the plateau it finds is a real TRIGGER plateau, not a readout ceiling. Deliverable is a
plateau curve — ev/spill vs plastic MIP fraction — plus, at each point, the time-since-flash
distribution, because a threshold that adds events only in the already-dense first
millisecond is not the same win as one that adds them across the window.

Run run_config_clock_check_beam.py (run_69) FIRST: it fixes the readout config and tells you
whether the clock changed the yield at all. This run then holds that config still and moves
only the threshold.

LADDER — descending, 6 points, per-sector mV = fraction x per-arm mean mip_peak from
calibrations/pss/mip_thresholds_y88.json (A 131 / B 154.5 / C 174 / D 149; D1 REPAIRED so D
uses both bars). Same convention as run_66/67, so the points overlap them exactly.

    2.00 MIP   A:-262  B:-309  C:-348  D:-298    above the GEANT optimum — the low-rate anchor
    1.41 MIP   A:-185  B:-217  C:-245  D:-210    GEANT optimum (~4.9 MeV) at the ~24 ev/pulse budget
    1.13 MIP   A:-148  B:-174  C:-196  D:-168
    0.90 MIP   A:-118  B:-139  C:-157  D:-134    run_67's floor; rate already flat here vs 1.13
    0.70 MIP   A: -92  B:-108  C:-122  D:-104    NEW — below anything run_66/67 took
    0.50 MIP   A: -66  B: -77  C: -87  D: -74    the long-standing production threshold
    1.41 MIP   (repeat)                          CLOSING BRACKET — beam drift check

All are far above the |10| mV hardware discriminator floor, so none of them sits in the
noise-saturated inverted-response regime (memory n1081b-threshold-floor-margin).

WHY DESCEND, AND WHY THE BRACKET. Descending means the run is stop-anywhere: kill it early
and you still have the top of the curve, which is the part that constrains the GEANT
optimum. The repeat of 1.41 MIP at the END is the whole basis for trusting the curve — the
SPS intensity drifts, and without a return to a visited point a slow beam decline is
indistinguishable from a threshold effect. If the two 1.41 points disagree by more than
their statistics, the curve is drift, not threshold.

READ THE PLATEAU HONESTLY. Two different things can flatten this curve:
  (a) TRIGGER exhaustion — no more plastic hits above threshold. The real answer.
  (b) DAQ saturation — the readout is refusing triggers. A FAKE plateau.
Tell them apart per sub-run with the FEU trigger counters, which report accepted vs offered:
    .venv/bin/python dream_scripts/feu_trig_counters.py --latch
If the accepted/offered ratio is falling as the threshold drops, you are in (b) and the
plateau is the DAQ, not the physics. Say which one you measured when quoting the result.

WALLS (M1) held at 0.5 MIP (25/35/34/36) throughout — the GEANT trigger model fixes the SiPM
leg at 0.5 MIP and our Singles is the per-sector wall AND plastic coincidence. Not scanned.

HV: single point, drift 600 / resist 530 (det D = A/B/C) — same as run_69, the run_67 recon
point. Held fixed so the ONLY moving variable is the plastic threshold.

MESH: NOT TOUCHED. None of the m### schedule tags used here carries a mesh_b target. After
the 2026-07-23 re-cable the mesh sits on M6 SEC_B out2/out3 and SEC_B/C enables are aliased
(docs/HANDOFF_2026-07-23_m6_mesh_recable_out34.md, ..._m6_secBC_control_aliasing.md), so
scan_control stays away from M6 entirely. Record the mesh state pre-run; it must not change
mid-run or the curve mixes two detector states.
  Related trap: NEVER put a mesh ON/OFF axis in a Singles-triggered run — mesh-OFF collapses
  the SiPM wall gain ~40x and kills the wall leg of the coincidence (28x measured on
  run_67). Memory run67-hv-mesh-thresh-scan.

TRIGGER + READOUT: identical to run_69 — scint --singles --ps-pickup, PS delay 1800
(co-framed), latency 35, 32 smp x 60 ns, RAW, IPD 5, 25 MHz read clock, default watermark.
  *** ev/spill here is NOT comparable to run_67's numbers (that run forced Hwm 2). ***

PRE-RUN (boards free — check config/n1081b_access/ first):
  Nothing new if run_69 has just finished — the routing carries over. Otherwise:
  .venv/bin/python n1081b/trigger_mode.py scint --singles --ps-pickup
  .venv/bin/python n1081b/set_ps_trigger_delay.py --delay 1800
  verify: trigger_mode.py status          -> C or_veto lemos=[0], D lemos=[0,1]
          set_ps_trigger_delay.py --show  -> delay 1800, enable_gd True
          set_mesh_injection.py status    -> RECORD; must match run_69 and not change
Launch: ./start_run.sh run_config_plastic_plateau.json
TEARDOWN: scan_control does NOT restore the section threshold on exit — it is left at the
  LAST point taken (1.41 MIP if the run completes, otherwise whatever it reached). Re-apply
  the standing plastic set by hand afterwards for whatever the next run needs.
"""
import os

# --- repo-root shim (run_config_beam/base live one dir up) ---
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from run_config_beam import Config as BeamConfig

RUN_NUM = 70

# ---- DREAM readout: identical to run_69 (25 MHz read clock comes from sample_period 60) ----
LATENCY = 35
N_SAMPLES = 32
SAMPLE_PERIOD = 60
IPD = 5

SUBRUN_MIN = float(os.environ.get('SUBRUN_MIN', '4'))   # ~17 spills @ 14.4 s supercycle

# ---- single HV point, same as run_69 ----
DRIFT_V = 600
RESIST_V = 530
DET_D_OFFSET = 0          # det D resist = A/B/C

# (schedule tag, MIP fraction) — descending, closing on a repeat of 1.41 as the drift check.
# Tags are defined in config/n1081b_scan_schedule.json; m200On/m070On/m050On were added
# 2026-07-23 for this run, and NONE of these tags carries a mesh_b target.
LADDER = [
    ('m200On', 2.00),
    ('m141On', 1.41),   # GEANT optimum
    ('m113On', 1.13),
    ('m090On', 0.90),   # run_67's floor
    ('m070On', 0.70),   # new territory
    ('m050On', 0.50),   # long-standing production threshold
    ('m141On', 1.41),   # CLOSING BRACKET — must reproduce point 2 or the curve is drift
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
        # Per-sub-run plastic threshold via scan_control (m### tags). Routing + PS delay
        # are static (pre-run); scan_control touches ONLY the four M2 section thresholds.
        self.n1081b_scan = 'on'

        self.trigger = (
            f'PLASTIC THRESHOLD PLATEAU scan ({self.run_name}) — where does lowering the '
            f'plastic discriminator stop buying events? scint --singles --ps-pickup, '
            f'PS+singles co-framed in 32 smp (latency 35, M4.D in0 G&D 1800 ns -> flash '
            f'~smp 13, MM ~smp 11); N93B window ~1->81 ms post-flash. 32 smp x 60 ns, IPD '
            f'{IPD}, RAW, 25 MHz read clock (07-23 default), FEU watermark DEFAULT. Plastic '
            f'threshold per sub-run via scan_control: 2.00 MIP A-262/B-309/C-348/D-298, 1.41 '
            f'A-185/B-217/C-245/D-210 (GEANT optimum), 1.13 A-148/B-174/C-196/D-168, 0.90 '
            f'A-118/B-139/C-157/D-134, 0.70 A-92/B-108/C-122/D-104, 0.50 A-66/B-77/C-87/'
            f'D-74, then 1.41 REPEATED as the beam-drift bracket; {SUBRUN_MIN:g} min each. '
            f'Walls 0.5 MIP (25/35/34/36), not scanned. HV single point drift {DRIFT_V} / '
            f'resist {RESIST_V} (det D = A/B/C). M6/mesh NOT touched (no mesh_b target on any '
            f'tag). Deliverable: ev/spill vs MIP fraction plus per-point time-since-flash. '
            f'NOT comparable to run_67 ev/spill (that run forced Hwm 2). Scint PMT bias at '
            f'07-19 Y88 setpoints. Ar/Iso 90/10, 3He, no Pb.')

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
        for k, (tag, frac) in enumerate(LADDER):
            # sub_run_name MUST lead with the schedule tag — scan_control keys on it.
            self.sub_runs.append({
                # tag is the leading '_'-token (scan_control keys on it); the mip token is
                # human-readable, the index keeps the two 1.41 points distinct.
                'sub_run_name': f'{tag}_mip{frac:.2f}_{k:03d}'.replace('.', 'p'),
                'run_time': SUBRUN_MIN, 'post_pause_s': 0,
                'inter_packet_delay': IPD,
                'hvs': {'5': _resist(RESIST_V), '9': _drift(DRIFT_V)},
            })

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
    c.write_to_file('config/json_run_configs/run_config_plastic_plateau.json')
    n = len(c.sub_runs)
    data_min = sum(sr['run_time'] for sr in c.sub_runs)
    print(f'=== {c.run_name} — plastic threshold PLATEAU scan ===')
    print(f'readout  : {N_SAMPLES} smp x {SAMPLE_PERIOD} ns, latency {LATENCY}, IPD {IPD}, RAW, '
          f'25 MHz read clock, watermark DEFAULT')
    print(f'HV       : drift {DRIFT_V} / resist {RESIST_V} (det D = A/B/C), single point')
    print(f'\n{"sub-run":>14} {"MIP":>6}   per-sector threshold (mV)')
    thr = {2.00: 'A-262 B-309 C-348 D-298', 1.41: 'A-185 B-217 C-245 D-210',
           1.13: 'A-148 B-174 C-196 D-168', 0.90: 'A-118 B-139 C-157 D-134',
           0.70: 'A -92 B-108 C-122 D-104', 0.50: 'A -66 B -77 C -87 D -74'}
    for k, (tag, frac) in enumerate(LADDER):
        mark = '   <- closing bracket' if k == len(LADDER) - 1 else ''
        print(f'{tag + f"_{k:03d}":>14} {frac:>6.2f}   {thr[frac]}{mark}')
    print(f'\n{n} sub-runs x {SUBRUN_MIN:g} min = {data_min:.0f} min data '
          f'(~{data_min + n:.0f} min wall); ~{data_min / 10 * 10.4:.0f} GB HDD worst case')
    print('\nRun run_config_clock_check_beam.json (run_69) FIRST.')
    print('Pre-run: routing carries over from run_69; else trigger_mode.py scint --singles '
          '--ps-pickup + set_ps_trigger_delay.py --delay 1800')
    print('Launch : ./start_run.sh run_config_plastic_plateau.json')
    print('Per pt : .venv/bin/python dream_scripts/feu_trig_counters.py --latch   '
          '# accepted/offered -> trigger plateau vs DAQ saturation')
    print('TEARDOWN: threshold is LEFT at the last point taken — re-apply by hand.')
