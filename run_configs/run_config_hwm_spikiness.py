#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_hwm_spikiness.py — SUPERSEDED, NEVER RUN. Kept for the reasoning trail only.

⚠ Use `run_config_hwm_ipd_2x2.py` instead. This Hwm-only ladder (75 min) was replaced before
launch by the Hwm x IPD 2x2 (39 min), which answered the same question AND settled IPD in one
run. The 2x2 ran as run_82 on 2026-07-27; result in docs/PLAN_comb_spikiness_2026-07-27.md
section 4d — Hwm 1 / Lwm 0 takes the 1-10 ms starved-bin fraction from 26.7% to 3.3%, and IPD
2 makes the comb WORSE so IPD stays at 5.

⚠ Its docstring below also repeats the "gap = Hwm x full-FIFO readout" model, which run_79's
inter-trigger distribution FALSIFIED (the floor is ONE readout: 195 us measured vs 196 us
predicted, not 393 us). Do not quote it.

Original question: does Hwm 1 flatten the residual acceptance comb that survives at Hwm 2?

WHY THIS RUN EXISTS
  run_79/run_81 (latency 27 / n_samples 20 / Hwm 2 / IPD 5) reads CV 0.420 on the 1-10 ms
  distribution in 0.5 ms bins — 3.5x flatter than run_77 and apparently smooth. It is not.
  Re-measured on bins FINER than the readout drain (tools/flash_time_spikiness.py, run_79
  sub-runs 0000-0002, 3114 flash-anchored spills):

      bin width      CV(1-10 ms)   min/mean   empty bins
        0.5 ms          0.420        0.392       0.0%
        0.25 ms         0.584        0.019       0.0%     <- near-EMPTY bins appear
        0.1 ms          0.874        0.000       1.1%

  A 0.5 ms bin is WIDER than the 0.39 ms dead gap, so it averages a tooth and its gap
  together and reports "flat". The structure is real: a residual comb of period ~1.15 ms
  (autocorrelation r = 0.51) with 12 near-empty gaps of median width 0.35 ms (max 0.40 ms)
  occupying 33% of the 1-10 ms band. Measured profile, 0.25 ms bins, triggers per flash:

      1.00-1.25  2.32 ################   3.00-3.25  0.68 #####
      1.25-1.50  1.19 ########           3.25-3.50  0.02             <- dead
      1.50-1.75  1.04 #######            3.50-3.75  1.01 #######
      1.75-2.00  1.01 #######            3.75-4.00  1.56 ###########
      2.00-2.25  0.02                 <- dead      ... and so on to 10 ms

  Those gaps are energy-blind bands. On the EAR2 flight path t[ms] = 1.41/sqrt(E[eV]), so a
  0.39 ms gap at 5 ms is dE/E = 2*dt/t = 16% of the neutron energy scale, lost outright.

THE MECHANISM, AND WHY Hwm 1 IS THE RIGHT FIRST TEST
  The gap width IS the readout of a full FIFO. At n_samples 20 / IPD 5 the serialised
  per-event readout is 20 x (4.83 + 0.998 x 5) = 196 us. At Hwm 2 the FEU asserts BUSY at
  occupancy >= 2 and clears at <= 1, i.e. it drains ~2 events per BUSY block:

      2 x 196 us = 392 us  vs  measured max gap 0.40 ms, median 0.35 ms   <- it matches

  At Hwm 1 / Lwm 0 the block is ONE event: the gap should halve to ~0.20 ms and recur twice
  as often. That does not remove the dead time, it CHOPS it finer — which is exactly what a
  neutron-energy measurement wants, because it converts a handful of wide blind bands into a
  quasi-uniform efficiency loss that no longer erases whole energy bins.

  Throughput cost should be small here. Hwm 1 removes the derandomiser's readout/transmit
  overlap, whose measured worth is ~1.5x (2001 vs 3079 Hz on the 2026-07-22 saturating-pulser
  ladder, n32/IPD10). The Hwm 1 ceiling at n20/IPD5 is 1000/196 = 5.09 kHz, and the MEASURED
  in-gate accepted rate is 3.1-3.4 kHz over 2-10 ms — i.e. we are not sitting on the Hwm 1
  ceiling, so most of the overlap benefit is idle headroom anyway. Expect the cost to land in
  the 1-2 ms spike (5.56 kHz there, above the Hwm 1 ceiling), which is the cheapest place to
  spend it — exactly where Hwm 2 already spent its −10% vs Hwm 11.

  ⚠ This is a PREDICTION. Both halves must be checked against the data, not assumed.

THE INDEPENDENT CONFIRMATION ALREADY IN THE run_79 DATA
  The comb only exists where the offered rate is high. Same run, same everything, by band:

      band        accepted rate   starved bins (<25% of mean)   CV(0.25 ms)
      1-2 ms         5.56 kHz            35.0%                     0.391
      2-4 ms         3.27 kHz            37.5%                     0.642
      4-6 ms         3.12 kHz            42.5%                     0.609
      6-10 ms        3.44 kHz            27.5%                     0.538
      10-20 ms       2.68 kHz             2.5%                     0.320
      20-40 ms       1.27 kHz             0.0%                     0.256

  Past ~10 ms the singles rate falls below ~2.7 kHz and the comb VANISHES on its own. The
  system sits right at the knee. That is the physical basis for the fallback test
  (run_config_plastic_thresh_spikiness.py): lowering the offered trigger rate should clear
  the comb the same way time does. Whichever lever wins, the target is the same — get the
  1-10 ms band to look like the 20-40 ms band already does.

DESIGN — INTERLEAVED, because beam intensity drifts
  Sub-runs alternate Hwm 2 / Hwm 1 / Hwm 2 / Hwm 1 / Hwm 2 / Hwm 1 rather than running three
  of each back to back. The comb's depth depends on the offered singles rate, which tracks
  beam intensity, so a block design would confound watermark against beam drift — the same
  confound that made the 2026-07-19 "busy-gap" numbers uninterpretable across sub-runs.
  Interleaving cancels any linear drift.

  The watermark is a per-sub-run DREAM cfg override (dream_daq_control.py builds
  effective_info = {**dream_info, **subrun}, so ovr_wrn_hwm in a sub-run dict wins). Nothing
  on the N1081B changes: every sub-run keeps the `stat090` tag, so scan_control re-asserts
  the SAME 0.90 MIP plastic thresholds and mesh-off state each time. Hwm is the only variable.

EVERYTHING ELSE IS THE PRODUCTION POINT, UNCHANGED
  latency 27, n_samples 20, 60 ns sampling, IPD 5, RAW; drift 700 V all four, resist
  A540/B540/C525/D520; plastic 0.90 MIP; walls 0.5 MIP; mesh off; M4.D1 PS delay 1440 ns.
  No board work is needed if this follows run_81 directly — the routing and the PS delay are
  already in the production state.

COST: 6 x 12 min = 72 min plus ~22 s/sub-run overhead, so ~75 min of beam.
  At the run_79 rate (17.5 spills/min) each point collects ~210 flash-anchored spills
  ~ 6700 triggers in 1-10 ms = ~190 per 0.25 ms bin, 7% statistical. Pooling the three
  sub-runs per setting gives ~4%, against a CV difference we expect to be >0.1. Enough.
  Disk: ~5.3 MB/s, so ~24 GB total.

PRE-RUN (beam ON — daq_control has NO beam-gating, wait for a real pulse)
  verify: .venv/bin/python n1081b/trigger_mode.py status         -> C or_veto [0], D [0,1]
          .venv/bin/python n1081b/set_ps_trigger_delay.py --show -> delay 1440
Generate: .venv/bin/python run_configs/run_config_hwm_spikiness.py
Launch:   ./start_run.sh run_config_hwm_spikiness.json

ANALYSE (both settings, same tool, same bins — the comparison is the whole point)
  /home/mx17/ana/.venv/bin/python \
      ~/beam_july/analysis/flash_comb/tools/flash_time_spikiness.py \
      --run run_82 --tmax 40 --fine 0.05 --out <outdir> --label run_82
  then split by sub-run to separate the two settings. Read CV at 0.25 ms and 0.1 ms bins,
  the starved-bin fraction, and trig/flash in 1-10 ms. Hwm 1 wins if the fine-bin CV drops
  and the gaps narrow, at a trigger cost you are willing to pay.

VERIFY BEFORE TRUSTING A NULL (a null here is worthless without this)
  1. The per-sub-run watermark actually reached the cfg — it is a per-sub-run override and
     those have been silently dropped before by a stale dream_daq server:
       grep -H "Main_Trig_OvrWrn" ~/july_dream/dream_run/run_82/*/Tcm_Mx17_July.cfg
     must show Hwm 2/Lwm 1 and Hwm 1/Lwm 0 on the sub-runs that asked for them.
  2. Hardware read-back, live, during a Hwm 1 sub-run:
       .venv/bin/python dream_scripts/feu_trig_counters.py     -> Hwm 1 / Lwm 0 on all 8
     (⚠ its accepted/drop counters read 0 without a --latch poke; ignore the "watermark
      cannot be biting" footer, which is computed from those unlatched zeros.)
  3. RunCtrl's clamp is one-directional and its cap here is (512-27)//20 = 24 -> Hwm 20, so
     both 2 and 1 pass through untouched. Nothing to worry about, but confirm via (1).

⚠ NOT TESTED HERE, and arguably the better lever — INTER-PACKET DELAY.
  The readout ceiling is 1000 / (n_samples x (4.83 + 0.998 x IPD)) kHz. Dropping IPD 5 -> 2
  moves it from 5.09 to 7.32 kHz (+44%) and shortens every dead gap in the same proportion,
  WITHOUT giving up any triggers — Hwm only redistributes them. The 2026-07-22 post-10GbE
  ladder measured IPD 5 at 0.026% corrupt gaps and never found the corruption threshold
  going down, so there is headroom, but IPD 2 was never measured on beam. Set IPD=2 on this
  generator to add it as a third axis, or run it separately. Recommended either way.
"""
import os

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from run_config_beam import Config as BeamConfig

RUN_NUM = int(os.environ.get('RUN_NUM', '82'))

# ---- readout: the production point, unchanged ----
LATENCY = int(os.environ.get('LATENCY', '27'))
N_SAMPLES = int(os.environ.get('N_SAMPLES', '20'))
IPD = int(os.environ.get('IPD', '5'))
SAMPLE_PERIOD = 60
ZS = False

# ---- the scan axis: interleaved, NOT blocked (beam intensity drifts) ----
#      Lwm is always Hwm-1: it must sit strictly below Hwm or the OverflowWarning
#      hysteresis cannot clear. Hwm 1 / Lwm 0 was measured working (2001 Hz sustained,
#      2026-07-22 saturating-pulser ladder).
HWM_LADDER = [int(x) for x in os.environ.get('HWM_LADDER', '2,1,2,1,2,1').split(',')]

SUBRUN_MIN = float(os.environ.get('SUBRUN_MIN', '12'))
RESUME = os.environ.get('RESUME', '0') == '1'

# ---- operating point, identical to run_79/run_81 ----
DRIFT_V = 700
DRIFT_D_OFFSET = int(os.environ.get('DRIFT_D_OFFSET', '0'))
RESIST_V = {'A': 540, 'B': 540, 'C': 525, 'D': 520}
TAG = 'stat090'            # N1081B state HELD: 0.90 MIP plastics, mesh off, every sub-run

PS_DELAY_NS = 60 * (2.0 + LATENCY - 5)


def _ceiling_khz(n, ipd):
    """Serialised single-event readout ceiling, kHz (the Hwm 1 ceiling)."""
    return 1000.0 / (n * (4.83 + 0.998 * ipd))


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

        ladder_txt = ' / '.join(f'Hwm {h}/Lwm {h-1}' for h in HWM_LADDER)
        self.trigger = (
            f'TRIGGER-FIFO WATERMARK test ({self.run_name}, 2026-07-27): does Hwm 1 flatten the '
            f'residual acceptance comb that survives at Hwm 2? run_79 reads CV 0.420 on 1-10 ms '
            f'in 0.5 ms bins but 0.584 at 0.25 ms and 0.874 at 0.1 ms — a real comb of period '
            f'~1.15 ms with 12 near-empty gaps (median 0.35 ms, max 0.40 ms) covering 33% of the '
            f'band. The 0.5 ms bin is wider than the gap and hides it. Gap width = a full-FIFO '
            f'readout: 2 x 196 us at Hwm 2/n20/IPD5 vs 0.40 ms measured. Hwm 1/Lwm 0 drains ONE '
            f'event per BUSY block, so the gap should halve to ~0.20 ms and recur twice as often '
            f'— dead time chopped finer, not removed, which is what the neutron-energy scale '
            f'wants (a 0.39 ms gap at 5 ms = 16% in dE/E). SCAN AXIS = watermark only, '
            f'INTERLEAVED {ladder_txt} to cancel beam-intensity drift; per-sub-run DREAM cfg '
            f'override. Everything else is the run_79/run_81 production point and is HELD: '
            f'latency {LATENCY}, n_samples {N_SAMPLES}, {SAMPLE_PERIOD} ns sampling, IPD {IPD}, '
            f'RAW / full readout, drift {DRIFT_V} V all four, resist A{RESIST_V["A"]}/'
            f'B{RESIST_V["B"]}/C{RESIST_V["C"]}/D{RESIST_V["D"]} V, plastic 0.90 MIP, walls (M1) '
            f'0.5 MIP, mesh charge-injection OFF — all re-asserted per sub-run by the `{TAG}` '
            f'tag, which is the SAME tag on every sub-run so the N1081B state never changes. '
            f'PS + SINGLES trigger, M4.D1 G&D delay {PS_DELAY_NS:.0f} ns (flash at sample ~5). '
            f'{len(HWM_LADDER)} x {SUBRUN_MIN:g} min. Ar/Iso 90/10, 3He, no Pb.')

        self.dream_daq_info.update({
            'daq_config_template_path': f'{self.base_out_dir}dream_config/Tcm_Mx17_July.cfg',
            'zero_suppress': ZS,
            'latency': LATENCY,
            'n_samples_per_waveform': N_SAMPLES,
            'sample_period': SAMPLE_PERIOD,
            'inter_packet_delay': IPD,
            'ovr_wrn_hwm': HWM_LADDER[0],
            'ovr_wrn_lwm': HWM_LADDER[0] - 1,
        })

        d_drift = DRIFT_V - DRIFT_D_OFFSET
        drift_hvs = {'0': DRIFT_V, '1': DRIFT_V, '2': DRIFT_V, '3': d_drift}
        resist_hvs = {'1': RESIST_V['A'], '2': RESIST_V['B'],
                      '3': RESIST_V['C'], '4': RESIST_V['D']}

        # Sub-run names keep ONE tag (stat090) so scan_control sees a single group and the
        # N1081B is never touched mid-run. The watermark rides in the DREAM override.
        self.sub_runs = []
        for k, hwm in enumerate(HWM_LADDER):
            self.sub_runs.append({
                'sub_run_name': f'{TAG}_hwm{hwm}_{k:04d}',
                'run_time': SUBRUN_MIN, 'post_pause_s': 0,
                'inter_packet_delay': IPD,
                'latency': LATENCY, 'n_samples_per_waveform': N_SAMPLES,
                'ovr_wrn_hwm': hwm, 'ovr_wrn_lwm': hwm - 1,
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
    c = Config()
    out = f'config/json_run_configs/run_config_hwm_spikiness{"_resume" if RESUME else ""}.json'
    c.write_to_file(out)

    # every sub-run name must still resolve to the stat090 schedule entry
    tags = {sr['sub_run_name'].split('_')[0] for sr in c.sub_runs}
    assert tags == {TAG}, f'scan tags drifted: {tags}'

    buf = (512 - LATENCY) // N_SAMPLES
    cap = buf - 4 if buf > 16 else (buf - 3 if buf > 8 else buf - 2)
    per_ev_us = N_SAMPLES * (4.83 + 0.998 * IPD)

    print(f'=== {c.run_name} — trigger-FIFO watermark vs comb spikiness ===')
    print(f'wrote        : {out}')
    print(f'ladder       : ' + '  '.join(f'Hwm{h}/Lwm{h-1}' for h in HWM_LADDER)
          + '   (INTERLEAVED — cancels beam drift)')
    print(f'held         : latency {LATENCY}, n_samples {N_SAMPLES}, IPD {IPD}, RAW, '
          f'0.90 MIP, mesh off, tag {TAG} on every sub-run')
    print(f'RunCtrl cap  : {cap}  -> every rung passes through (clamp is downward-only)')
    print(f'total        : {len(c.sub_runs)} sub-runs x {SUBRUN_MIN:g} min = '
          f'{sum(s["run_time"] for s in c.sub_runs)/60:.2f} h + ~22 s/sub-run overhead')
    print(f'disk         : ~5.3 MB/s -> ~{5.3*sum(s["run_time"] for s in c.sub_runs)*60/1000:.0f} GB')
    print()
    print(f'readout model at n={N_SAMPLES}, IPD={IPD}: {per_ev_us:.0f} us/event serialised')
    for h in sorted(set(HWM_LADDER)):
        print(f'  Hwm {h}: BUSY block drains ~{h} event(s) -> predicted dead gap '
              f'~{h*per_ev_us/1000:.2f} ms   (ceiling ~{_ceiling_khz(N_SAMPLES, IPD):.2f} kHz at Hwm 1)')
    print(f'  run_79 MEASURED at Hwm 2: median gap 0.35 ms, max 0.40 ms, period ~1.15 ms')
    print(f'  in-gate accepted rate MEASURED: 5.56 kHz (1-2 ms), 3.1-3.4 kHz (2-10 ms), '
          f'1.27 kHz (20-40 ms, comb-free)')
    print()
    print('VERIFY (a null is worthless without these):')
    print(f'  1. grep -H "Main_Trig_OvrWrn" ~/july_dream/dream_run/{c.run_name}/*/Tcm_Mx17_July.cfg')
    print('     -> per-sub-run overrides have been silently dropped before by a stale dream_daq')
    print('  2. .venv/bin/python dream_scripts/feu_trig_counters.py   (live, during a Hwm 1 sub-run)')
    print('     -> Hwm 1 / Lwm 0 on all 8 FEUs; ignore its "cannot be biting" footer (unlatched)')
    print()
    print('ANALYSE: /home/mx17/ana/.venv/bin/python \\')
    print('           ~/beam_july/analysis/flash_comb/tools/flash_time_spikiness.py \\')
    print(f'           --run {c.run_name} --tmax 40 --fine 0.05')
    print('  compare CV at 0.25 ms AND 0.1 ms bins, starved-bin fraction, trig/flash in 1-10 ms.')
    print()
    print(f'Launch: ./start_run.sh {out.split("/")[-1]}')
    print('NOTE: IPD 5 -> 2 would raise the ceiling 5.09 -> 7.32 kHz and shorten every gap by')
    print('      the same factor WITHOUT giving up triggers. Not in this ladder; set IPD=2 to')
    print('      add it. See the docstring.')
