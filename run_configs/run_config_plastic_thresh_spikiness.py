#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_plastic_thresh_spikiness.py — run_83, 2026-07-27.
FALLBACK test: can a higher plastic-scintillator threshold flatten the 1-10 ms acceptance
comb by lowering the offered trigger rate? Run this only if run_82 (Hwm 1 vs Hwm 2,
run_config_hwm_spikiness.py) does not flatten it enough.

THE ARGUMENT — and it is already half-proven by run_79's own data
  The watermark lever (run_82) redistributes a fixed dead-time budget: it chops the gaps
  finer without removing them. This lever removes them, because the comb is not a fixed
  deadtime — it is a pile-up artefact that only appears when the offered rate exceeds what
  the FEU can read out. Measured on run_79 (3114 flash-anchored spills, sub-runs 0000-0002,
  tools/flash_time_spikiness.py), same run, same config, split by time since the flash:

      band        accepted rate   starved bins (<25% of mean)   CV(0.25 ms bins)
      1-2 ms         5.56 kHz            35.0%                      0.391
      2-4 ms         3.27 kHz            37.5%                      0.642
      4-6 ms         3.12 kHz            42.5%                      0.609
      6-10 ms        3.44 kHz            27.5%                      0.538
      10-20 ms       2.68 kHz             2.5%                      0.320
      20-40 ms       1.27 kHz             0.0%                      0.256

  The comb switches off between 3.1 and 2.7 kHz. Nothing about the DAQ changes across that
  boundary — only the singles rate, which falls as the neutron flux does. So the system sits
  right at the knee, and a modest cut in offered rate should buy the 1-10 ms band the same
  smoothness that 20-40 ms already has for free. That is the hypothesis this run tests.

  The cost is triggers, and it is a real cost — but the comparison to make is not
  "triggers before vs after". It is triggers-in-the-energy-bins-you-can-actually-use. At
  Hwm 2 today, 33% of the 1-10 ms band is starved and those neutron energies are simply
  absent (t[ms] = 1.41/sqrt(E[eV]), so a 0.39 ms gap at 5 ms erases 16% of the energy
  scale there). Trading some rate for coverage can be a net gain even at lower total yield.

⚠ THE PRIOR MEASUREMENT UNDERSTATES THIS LEVER — do not pre-judge it from run_67
  run_67's recon read 1.41 MIP -> 64.2 ev/spill, 1.13 -> 76.8, 0.90 -> 77.1, i.e. almost no
  response between 0.90 and 1.13 and only -17% out to 1.41. That looks like a dead lever.
  It is not: run_67 ran at Hwm ~11, where the ACCEPTED yield was readout-ceiling-limited, so
  cutting the offered rate barely moved the accepted rate — the DAQ just wasted less. What
  matters here is the OFFERED rate and the comb it drives, which that measurement never
  looked at. This is also why the ladder reaches 2.00 MIP rather than stopping at 1.41: the
  0.90-1.41 span may be too narrow to move the offered rate below the ~2.7 kHz knee.

LADDER — 0.90 (production) / 1.13 / 1.41 / 2.00 MIP, then BACK to 0.90
  The closing 0.90 MIP point is the control: it must reproduce the opening 0.90 point. If it
  does not, the beam moved during the run and the ladder is confounded — the same trap that
  made the 2026-07-19 busy-gap numbers uninterpretable. Do not skip it.

  Thresholds are applied per sub-run by scan_control via the new `thr090/thr113/thr141/thr200`
  tags in config/n1081b_scan_schedule.json. Their mV values are byte-identical to the
  `m090On/m113On/m141On/m200On` tags used by run_67 and run_70, so this ladder is directly
  comparable to that data. Per-arm 1.0-MIP points A131/B154/C174/D149 mV
  (calibrations/pss/mip_thresholds_y88.json).

  Each tag also holds mesh charge-injection OFF (SEC_B out2/out3 only — the SiPM-enable bias
  on out0/out1 is NOT touched), exactly as `stat090` does. That matters: the 2026-07-22
  run_67 lesson is that toggling the mesh the wrong way collapses all four SiPM walls ~28x
  and voids any wall-dependent trigger. Nothing here toggles it; it is held off throughout.

  Walls (M1) stay at 0.5 MIP and are NOT scanned — the GEANT trigger model fixes the SiPM leg
  there, and the plastic leg is the one we are testing.

EVERYTHING ELSE IS THE PRODUCTION POINT, HELD
  latency 27, n_samples 20, 60 ns sampling, IPD 5, RAW; drift 700 V all four, resist
  A540/B540/C525/D520; M4.D1 PS delay 1440 ns; PS + SINGLES trigger. The watermark is held
  at Hwm 2 by default — set HWM to whatever run_82 concludes is best before running this, so
  the two levers compose rather than fight.

COST: 5 x 12 min = 60 min plus ~22 s/sub-run overhead, ~63 min of beam, ~19 GB.
  At the run_79 rate (17.5 spills/min) each point gets ~210 flash-anchored spills. The high
  rungs collect FEWER triggers by construction, which is the point — but it also means the
  2.00 MIP rung has the weakest statistics per bin. If it looks marginal, re-take it longer
  rather than trusting a noisy CV.

PRE-RUN (beam ON — daq_control has NO beam-gating, wait for a real pulse)
  verify: .venv/bin/python n1081b/trigger_mode.py status         -> C or_veto [0], D [0,1]
          .venv/bin/python n1081b/set_ps_trigger_delay.py --show -> delay 1440
  Boards must be free — scan_control writes M2 section thresholds every sub-run. Check
  config/n1081b_access/ for a holder before launching.
Generate: .venv/bin/python run_configs/run_config_plastic_thresh_spikiness.py
Launch:   ./start_run.sh run_config_plastic_thresh_spikiness.json

⚠ TEARDOWN — scan_control's snapshot/restore does NOT capture section thresholds, so the
  plastics are LEFT at the last rung (2.00 MIP if the run completes, whatever rung it died
  on otherwise). Production is 0.90 MIP. Re-apply it by hand before restarting statistics,
  or the next run silently takes data at the wrong trigger point.

ANALYSE
  /home/mx17/ana/.venv/bin/python \
      ~/beam_july/analysis/flash_comb/tools/flash_time_spikiness.py \
      --run run_83 --tmax 40 --fine 0.05
  per rung, read: CV at 0.25 ms and 0.1 ms bins, starved-bin fraction, trig/flash in 1-10 ms,
  and the accepted rate per band. The win condition is the starved fraction going to ~0 in
  1-10 ms — i.e. the band starting to look like 20-40 ms does now — at a trigger cost you
  are willing to pay. Check the closing 0.90 point against the opening one FIRST.
"""
import os

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from run_config_beam import Config as BeamConfig

# ⚠ 2026-07-27: run_83 is now TAKEN by the post-run_82 production run at Hwm 1 / Lwm 0
# (`RUN_NUM=83 OVR_WRN_HWM=1 OVR_WRN_LWM=0 run_configs/run_config_stats_optimized.py`).
# If this ladder is ever needed, run it with RUN_NUM=84 or later. It is probably NOT needed:
# run_82 measured Hwm 1 alone taking the starved-bin fraction from 27.8% to 3.3%, which was
# the outcome this ladder was the fallback for.
RUN_NUM = int(os.environ.get('RUN_NUM', '84'))

# ---- readout: the production point, held ----
LATENCY = int(os.environ.get('LATENCY', '27'))
N_SAMPLES = int(os.environ.get('N_SAMPLES', '20'))
IPD = int(os.environ.get('IPD', '5'))
SAMPLE_PERIOD = 60

# ---- watermark: HOLD. Set this to whatever run_82 concludes before running. ----
HWM = int(os.environ.get('HWM', '2'))
LWM = int(os.environ.get('LWM', str(HWM - 1)))

# ---- the scan axis: plastic discriminator level, opening AND closing on production ----
#      tags live in config/n1081b_scan_schedule.json (added 2026-07-27); mV identical to
#      m090On/m113On/m141On/m200On so this is comparable to run_67 / run_70.
LADDER = os.environ.get('LADDER', '090,113,141,200,090').split(',')
MIP_OF = {'090': 0.90, '113': 1.13, '141': 1.41, '200': 2.00}
MV_OF = {'090': 'A-118/B-139/C-157/D-134', '113': 'A-148/B-174/C-196/D-168',
         '141': 'A-185/B-217/C-245/D-210', '200': 'A-262/B-309/C-348/D-298'}

SUBRUN_MIN = float(os.environ.get('SUBRUN_MIN', '12'))
RESUME = os.environ.get('RESUME', '0') == '1'

# ---- operating point, identical to run_79/run_81 ----
DRIFT_V = 700
DRIFT_D_OFFSET = int(os.environ.get('DRIFT_D_OFFSET', '0'))
RESIST_V = {'A': 540, 'B': 540, 'C': 525, 'D': 520}

PS_DELAY_NS = 60 * (2.0 + LATENCY - 5)


class Config(BeamConfig):
    def _set_defaults(self, config_path=None):
        super()._set_defaults(config_path)

        self.run_name = f'run_{RUN_NUM}'
        self.run_out_dir = f'{self.data_out_dir}{self.run_name}/'
        self.dream_daq_info['run_directory'] = f'/home/mx17/july_dream/dream_run/{self.run_name}/'
        self.dream_daq_info['data_out_dir'] = f'{self.run_out_dir}'
        self.processor_info['run_dir'] = f'{self.run_out_dir}'
        self.hv_info['run_out_dir'] = self.run_out_dir
        self.resume = RESUME
        self.n1081b_scan = 'on'

        rungs = ' -> '.join(f'{MIP_OF[l]:.2f}' for l in LADDER)
        self.trigger = (
            f'PLASTIC-THRESHOLD ladder vs acceptance-comb spikiness ({self.run_name}, '
            f'2026-07-27) — the FALLBACK lever if the run_82 watermark test does not flatten '
            f'the 1-10 ms distribution. Hypothesis, from run_79 itself: the comb is a pile-up '
            f'artefact that only exists above ~3 kHz offered rate — 33% of the 1-10 ms band is '
            f'starved at 3.1-5.6 kHz, while 20-40 ms is comb-FREE at 1.27 kHz with nothing else '
            f'changed. So cutting the singles rate should clear it the way falling flux already '
            f'does past 10 ms. SCAN AXIS = M2 plastic discriminator only, {rungs} MIP, applied '
            f'per sub-run by scan_control tags thr090/thr113/thr141/thr200 (mV identical to '
            f'run_67/run_70 m###On tags, per-arm 1 MIP = A131/B154/C174/D149). The ladder OPENS '
            f'AND CLOSES on 0.90 MIP = the production point, as an in-run control against beam '
            f'drift. Reaches 2.00 MIP because run_67 saw only -17% out to 1.41 MIP — but that '
            f'was at Hwm ~11 where accepted yield was readout-ceiling-limited, so it measured '
            f'the wrong thing; the OFFERED rate is what drives the comb. HELD: latency '
            f'{LATENCY}, n_samples {N_SAMPLES}, {SAMPLE_PERIOD} ns sampling, IPD {IPD}, RAW / '
            f'full readout, Hwm {HWM}/Lwm {LWM}, drift {DRIFT_V} V all four, resist '
            f'A{RESIST_V["A"]}/B{RESIST_V["B"]}/C{RESIST_V["C"]}/D{RESIST_V["D"]} V, walls (M1) '
            f'0.5 MIP (NOT scanned — GEANT fixes the SiPM leg there), mesh charge-injection OFF '
            f'throughout (every tag holds SEC_B out2/out3 off; out0/out1 SiPM-enable untouched). '
            f'PS + SINGLES trigger, M4.D1 G&D delay {PS_DELAY_NS:.0f} ns (flash at sample ~5). '
            f'{len(LADDER)} x {SUBRUN_MIN:g} min. Ar/Iso 90/10, 3He, no Pb. ⚠ scan_control does '
            f'NOT restore section thresholds on exit — re-apply 0.90 MIP by hand afterwards.')

        self.dream_daq_info.update({
            'daq_config_template_path': f'{self.base_out_dir}dream_config/Tcm_Mx17_July.cfg',
            'zero_suppress': False,
            'latency': LATENCY,
            'n_samples_per_waveform': N_SAMPLES,
            'sample_period': SAMPLE_PERIOD,
            'inter_packet_delay': IPD,
            'ovr_wrn_hwm': HWM,
            'ovr_wrn_lwm': LWM,
        })

        d_drift = DRIFT_V - DRIFT_D_OFFSET
        drift_hvs = {'0': DRIFT_V, '1': DRIFT_V, '2': DRIFT_V, '3': d_drift}
        resist_hvs = {'1': RESIST_V['A'], '2': RESIST_V['B'],
                      '3': RESIST_V['C'], '4': RESIST_V['D']}

        self.sub_runs = []
        for k, lvl in enumerate(LADDER):
            self.sub_runs.append({
                'sub_run_name': f'thr{lvl}_{k:04d}',      # leading token = the scan tag
                'run_time': SUBRUN_MIN, 'post_pause_s': 0,
                'inter_packet_delay': IPD,
                'latency': LATENCY, 'n_samples_per_waveform': N_SAMPLES,
                'ovr_wrn_hwm': HWM, 'ovr_wrn_lwm': LWM,
                'hvs': {'5': dict(resist_hvs), '9': dict(drift_hvs)},
            })

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
    import json

    c = Config()
    out = (f'config/json_run_configs/run_config_plastic_thresh_spikiness'
           f'{"_resume" if RESUME else ""}.json')
    c.write_to_file(out)

    # fail loudly rather than at the first sub-run boundary, mid-beam
    sched = json.load(open('config/n1081b_scan_schedule.json'))
    tags = [sr['sub_run_name'].split('_')[0] for sr in c.sub_runs]
    missing = sorted({t for t in tags if t not in sched['scans']})
    assert not missing, f'scan tags with no schedule entry: {missing}'
    for t in set(tags):
        s = sched['scans'][t]
        assert s.get('mesh_ac', {}).get('output_status') is False, \
            f'{t} does not hold mesh_ac OFF — refusing (run_67: mesh toggling collapses the walls 28x)'
    assert LADDER[0] == LADDER[-1], \
        'ladder must open and close on the same rung — that closing point is the drift control'

    print(f'=== {c.run_name} — plastic-threshold ladder vs comb spikiness ===')
    print(f'wrote        : {out}')
    print(f'ladder       : ' + '  ->  '.join(
        f'{MIP_OF[l]:.2f} MIP' for l in LADDER))
    for l in LADDER[:-1] if LADDER[0] == LADDER[-1] else LADDER:
        print(f'                 thr{l}  {MIP_OF[l]:.2f} MIP  {MV_OF[l]} mV')
    print(f'               (opens AND closes on {MIP_OF[LADDER[0]]:.2f} MIP — the closing point '
          f'is the beam-drift control; check it FIRST)')
    print(f'held         : latency {LATENCY}, n_samples {N_SAMPLES}, IPD {IPD}, RAW, '
          f'Hwm {HWM}/Lwm {LWM}, walls 0.5 MIP, mesh OFF')
    print(f'total        : {len(c.sub_runs)} sub-runs x {SUBRUN_MIN:g} min = '
          f'{sum(s["run_time"] for s in c.sub_runs)/60:.2f} h + ~22 s/sub-run overhead')
    print(f'disk         : ~5.3 MB/s -> ~{5.3*sum(s["run_time"] for s in c.sub_runs)*60/1000:.0f} GB '
          f'(less, since the high rungs trigger less)')
    print()
    print('WHAT WE ARE LOOKING FOR (run_79 baseline, same tool, same bins):')
    print('  1-10 ms  CV 0.584 @0.25 ms / 0.874 @0.1 ms, 33% of the band starved, 32.1 trig/flash')
    print('  20-40 ms CV 0.256 @0.25 ms,  0% starved   <- what 1-10 ms should start to look like')
    print('  the comb switches off between 3.1 and 2.7 kHz accepted; find the rung that crosses it')
    print()
    print('⚠ TEARDOWN: scan_control does NOT restore section thresholds. The plastics are LEFT')
    print(f'  at {MIP_OF[LADDER[-1]]:.2f} MIP. Re-apply the 0.90 MIP production set by hand before')
    print('  restarting statistics, or the next run takes data at the wrong trigger point.')
    print()
    print(f'Launch: ./start_run.sh {out.split("/")[-1]}')
