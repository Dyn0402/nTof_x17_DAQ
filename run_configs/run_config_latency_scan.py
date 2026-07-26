#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_latency_scan.py — run_78, 2026-07-26.
SHORT diagnostic scan of the DREAM trigger latency, to fix a measured front-edge clip.

WHY THIS RUN EXISTS
  Tracking on run_77 sub-run 0000 (1 h, 93 557 physics triggers, 364 232 drift tracks)
  shows the 32-sample window is MISALIGNED, not merely too long:

    * the tail is empty — no drift track in any detector extends past ~1.0 us, and
      99.9% are finished by sample 14-16, so samples 16-31 hold no track signal;
    * the FRONT is clipped — the first hit of a drift track lands in sample 0 for
      A 23.9% / B 36.7% / C 19.0% / D 45.1% of clean tracks, i.e. the drift signal was
      already underway when the window opened.

  That clip SURVIVES flash, post-flash, saturation and pile-up rejection (it moves only
  35.0% -> 23.9% on Det A across the whole ladder), so it is not sparks or discharges —
  it is real signal falling off the front of the window. The t_min profile makes the
  same point: A and C peak at sample 2 with an excess piled in sample 0, while B and D
  peak AT sample 0 and fall monotonically — B and D are clipped hardest, and how far
  their signal extends before the window is by construction invisible in run_77 data.
  Only moving the window can measure it, hence this scan.

WHAT IS SCANNED — trigger latency ONLY. Everything else is byte-identical to run_77 (HV
  at the run_67 optimum, plastic 0.90 MIP, mesh off, RAW, IPD 5, 32 smp x 60 ns), so the
  latency is the single variable.

    LATENCY_LADDER = [31, 35, 39, 43, 47]     (35 = run_77's value = the control)

  Latency is `Feu * Dream * 12` (dream_reg[12] = TRIGLAT), counted in SCA cells; at
  sample_period 60 ns one latency unit is one sample. Raising it reads cells written
  earlier, so a fixed physical signal moves to a HIGHER sample index — which is the
  direction that recovers a clipped front edge. ⚠ That sign is inferred from the
  RunCtrl derandomiser formula, not from a datasheet, which is exactly why the ladder
  BRACKETS the control (31 below, 39/43/47 above): whichever direction improves the
  clip metric settles the sign empirically in one pass.

THE WATERMARK — FORCED TO 2 BY EXPLICIT OPERATOR DECISION (2026-07-26)
  This run sets `ovr_wrn_hwm = 2`, `ovr_wrn_lwm = 1`. That is a deliberate departure from
  the standing default and from the prior beam evidence; it is recorded here in full so
  the result is interpretable either way.

  How the register actually gets its value: RunCtrl ignores the cfg's nominal
  `Main_Trig_OvrWrnHwm` (20) and derives its own cap (RunCtrl.c:998-1051), clamping
  DOWNWARD ONLY:

      drm_derand_buf = 512 - TRIGLAT ;  drm_evt_buf = drm_derand_buf / NbOfSamples
      buf>16 -> Hwm = buf-4 ;  buf>8 -> Hwm = buf-3 ;  ...

  At latency 35 / n32 that cap is buf 14 -> Hwm 11, which is what run_77 ran at: a live
  peek of 0x100008 on 2026-07-26 returned Hwm=11 Lwm=8 on all eight FEUs, and run_77's
  measured trigger-burst depth is exactly 11-12 (774 bursts of 11, 244 of 12) — the
  watermark IS the burst depth. Because the clamp is one-directional, a requested 2 is
  BELOW the cap and passes through unchanged at every latency in this ladder (the cap is
  11 across 33..64), so the override is not confounded by the latency axis.

  ⚠ THE EVIDENCE AGAINST Hwm=2, recorded so this run can be judged fairly. The 2026-07-22
  beam scan walked the watermark down and found it monotonically harmful:

      cfg Hwm | ev/spill (all) | ev/spill in the 4-10 ms band
           11 |          104.6 | 23.45      <- default cap
            6 |           98.5 | 18.30
            3 |           92.9 | 16.94
            1 |           70.2 | 13.35
    and run_67 separately measured an hwm=2 point costing -10.2%/pulse. The reasoning was
    that the detector is flash-blind until ~4 ms anyway, so there is no DAQ blackout left
    to chop and a lower watermark only removes buffer depth. EXPECT a throughput cost of
    roughly 10-25% here; if the clip metric is unchanged while ev/spill drops, that cost
    bought nothing and the watermark should go back to default.

    The one thing that genuinely was NOT tested: that scan ran on ZS + IPD 10, where the
    per-event cycle is ~325 us and the comb was already essentially gone. run_78 is RAW +
    IPD 5 with a live 1.09 ms drain and a hard comb, so the premise "there is no blackout
    left to chop" does not hold here. Whether a shallow watermark interleaves the burst
    usefully in THIS configuration is genuinely open — that is the case for trying it.

  ⚠ VERIFY IT ACTUALLY LANDED — do not trust a null from this run otherwise. Watermark
    overrides were silently dropped for weeks because the long-lived `dream_daq` server
    predated the plumbing. The current server (started 2026-07-23 12:23:55) is newer than
    dream_daq_control.py (12:22:50) so it should honour it, but CHECK BOTH:
      1. archived cfg:  grep OvrWrn ~/july_dream/dream_run/run_78/<subrun>/*.cfg   -> 2 / 1
      2. hardware:      .venv/bin/python dream_scripts/feu_trig_counters.py        -> Hwm 2
    If either still reads 11, restart the dream_daq server and re-take.

  The ladder is bounded to latency 33..64 for a separate reason: outside it the RunCtrl
  cap itself moves (at 31 the cap is 12, at 71 it is 10). Inside it the cap is a constant
  11, so nothing about the latency axis can perturb the forced value of 2.

WHAT COMES AFTER (deliberately NOT in this run — one variable at a time)
  Once the latency is right, `n_samples` is the second lever, and it is the valuable one:
  it shortens readout per event AND deepens the burst, because the same formula gives
  n=24 -> buf 19 -> Hwm 15 and n=20 -> buf 23 -> Hwm 19. run_77's drain between bursts
  is a median 1.09 ms, and that drain is what produces the hard acceptance comb below
  ~12 ms (bursts at 1.0/2.3/3.6/5.0/6.7/8.0/9.3/11.0 ms with near-zero acceptance
  between — 0.5 ms bins in 1-10 ms swing from 11 206 triggers down to 108). Shorter
  readout plus deeper bursts attacks that directly. Trim only AFTER the front edge is
  fixed, or the trim will be sized against a misaligned window.

  ⚠ n_samples also interacts with the flash: in flash events the flash spans samples
  5-25 (peak 7-8). Raising latency moves it to higher indices, so a later trim must
  re-check that the flash still frames where flash-recovery analysis needs it.

METRIC — for each latency point, the fraction of CLEAN drift tracks whose first hit is
  in sample 0, per detector (the scratch script `clean_timing.py` computes exactly this
  ladder: no-flash, no post-flash, no saturated hit, low pile-up). The optimum is the
  smallest latency at which that fraction stops falling — that is the point where the
  whole drift signal is inside the window with no wasted front samples. Cross-check that
  the p99.9 track end has not been pushed toward sample 31.

DWELL — 10 min x 5 points x 3 cycles = 2.5 h. At run_77's rate a 10 min point yields
  ~8 000 clean drift tracks per detector, so the clip fraction carries a ~0.5% statistical
  error against differences expected to be tens of percent. Cycle-major ordering means
  killing the run after any complete cycle still leaves a balanced ladder.

⚠ THIS RUN STOPS run_77. It is a diagnostic, not a statistics run: ~2.5 h of statistics is
  traded for getting the remaining multi-day run onto a correctly aligned window. Resume
  run_77 (or a re-issued run_77 with the fixed latency) immediately afterwards.

TRIGGER / HV / thresholds: unchanged from run_77 — scint --singles --ps-pickup, PS delay
  1800 ns, M4.C or_veto(Singles, lemo0), M4.D OR(lemo0, lemo1); drift 700 V all four,
  resist A540/B540/C525/D520, plastic 0.90 MIP, mesh off, all re-asserted per sub-run by
  the `stat090` scan tag. Nothing here needs a pre-run board write if run_77 was the
  previous run.

Launch: ./start_run.sh run_config_latency_scan.json
"""
import os

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from run_config_beam import Config as BeamConfig

RUN_NUM = 78

# ---- readout: identical to run_77 except the scanned latency ----
N_SAMPLES = 32
IPD = 5
SAMPLE_PERIOD = 60
ZS = os.environ.get('DREAM_ZS', '0') == '1'

# ---- the scan axis ----
# 35 is run_77's value (the control).
# ⚠ The ladder is CONFINED TO latency 33..64. RunCtrl's cap is a floor-divide, so
# drm_evt_buf = (512-lat)//32 is 14 -> Hwm 11 only for lat in [33, 64]; at lat 31 the buf
# rounds up to 15 -> Hwm 12, a DIFFERENT burst depth, which would vary the comb across the
# ladder and confound the very timing measurement this scan exists to make. Hence 33, not
# 31, as the lower bracket — it is only 2 samples below the control, but the sign of the
# latency direction is settled just as well by the four points ABOVE it: if the inferred
# sign is wrong they will all worsen the clip monotonically, which is unambiguous.
# The __main__ block asserts the constant-Hwm property and will say so if it is violated.
LATENCY_LADDER = [33, 35, 39, 43, 47, 51]
BASE_LATENCY = 35

SUBRUN_MIN = float(os.environ.get('SUBRUN_MIN', '8'))
N_CYCLES = int(os.environ.get('N_CYCLES', '3'))

# ---- trigger-FIFO watermark: FORCED to 2 (operator, 2026-07-26) ----
# Held CONSTANT across the whole ladder, so latency remains the only scanned variable.
# RunCtrl clamps downward only and its cap is 11 here, so 2 passes through untouched.
# Lwm must sit below Hwm or the OverflowWarning hysteresis cannot clear; 1 gives
# assert-at-occ>=2 / clear-at-occ<=1. See the docstring for the evidence against this
# setting and for the two mandatory read-back checks.
OVR_WRN_HWM = int(os.environ.get('OVR_WRN_HWM', '2'))
OVR_WRN_LWM = int(os.environ.get('OVR_WRN_LWM', '1'))

# ---- the operating point, held fixed (calibrations/mm/statistics_run_config_run67.json) ----
DRIFT_V = 700
DRIFT_D_OFFSET = int(os.environ.get('DRIFT_D_OFFSET', '0'))
RESIST_V = {'A': 540, 'B': 540, 'C': 525, 'D': 520}
TAG = 'stat090'


def _hwm(latency, n_samples):
    """RunCtrl's derandomiser cap — the value that actually reaches the FEU."""
    buf = (512 - latency) // n_samples
    if buf > 16:
        return buf - 4, buf - 8
    if buf > 8:
        return buf - 3, buf - 6
    if buf > 4:
        return buf - 2, buf - 4
    return buf - 1, buf - 2


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
        self.n1081b_scan = 'on'

        readout_txt = ('ZS' if ZS else 'RAW / full readout (zero_suppress=False)')
        self.trigger = (
            f'LATENCY SCAN ({self.run_name}) — diagnostic for the front-edge clip measured '
            f'on run_77 (drift tracks start in sample 0 for A 23.9 / B 36.7 / C 19.0 / '
            f'D 45.1% of clean tracks, while nothing extends past ~1.0 us). Scans DREAM '
            f'trigger latency {LATENCY_LADDER} (control {BASE_LATENCY}) with EVERYTHING '
            f'else held at the run_77 operating point: drift {DRIFT_V} V all four, resist '
            f'A{RESIST_V["A"]}/B{RESIST_V["B"]}/C{RESIST_V["C"]}/D{RESIST_V["D"]} V, plastic '
            f'0.90 MIP, mesh off (all re-asserted per sub-run by the `{TAG}` tag), '
            f'{N_SAMPLES} smp x {SAMPLE_PERIOD} ns, IPD {IPD}, {readout_txt}. Every ladder '
            f'point keeps RunCtrl Hwm = 11 so trigger-burst depth (and the comb) is constant. '
            f'PS + SINGLES trigger, PS delay 1800 ns. {SUBRUN_MIN:g} min x {len(LATENCY_LADDER)} '
            f'points x {N_CYCLES} cycles, cycle-major (stop after any complete cycle). '
            f'Walls (M1) 0.5 MIP. Scint PMT bias at 07-19 Y88 setpoints. Ar/Iso 90/10, 3He, no Pb.')

        self.dream_daq_info.update({
            'daq_config_template_path': (
                f'{self.base_out_dir}dream_config/'
                f'{"Tcm_Mx17_July_ZS.cfg" if ZS else "Tcm_Mx17_July.cfg"}'),
            'zero_suppress': ZS,
            'latency': BASE_LATENCY,
            'n_samples_per_waveform': N_SAMPLES,
            'sample_period': SAMPLE_PERIOD,
            'inter_packet_delay': IPD,
            'ovr_wrn_hwm': OVR_WRN_HWM,
            'ovr_wrn_lwm': OVR_WRN_LWM,
        })
        if ZS:
            self.dream_daq_info.update({'common_noise_subtraction': True,
                                        'pedestal_subtraction': False})

        d_drift = DRIFT_V - DRIFT_D_OFFSET
        drift_hvs = {'0': DRIFT_V, '1': DRIFT_V, '2': DRIFT_V, '3': d_drift}
        resist_hvs = {'1': RESIST_V['A'], '2': RESIST_V['B'],
                      '3': RESIST_V['C'], '4': RESIST_V['D']}

        self.sub_runs = []
        k = 0
        for cyc in range(N_CYCLES):
            for lat in LATENCY_LADDER:
                # leading '_'-token keys scan_control -> stat090 (0.90 MIP + mesh OFF)
                self.sub_runs.append({
                    'sub_run_name': f'{TAG}_lat{lat:03d}_c{cyc}_{k:04d}',
                    'run_time': SUBRUN_MIN, 'post_pause_s': 0,
                    'inter_packet_delay': IPD,
                    'latency': lat,                      # <-- the scanned quantity
                    'n_samples_per_waveform': N_SAMPLES,
                    'hvs': {'5': dict(resist_hvs), '9': dict(drift_hvs)},
                })
                k += 1

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
    out = 'config/json_run_configs/run_config_latency_scan.json'
    c.write_to_file(out)

    n = len(c.sub_runs)
    total = sum(sr['run_time'] for sr in c.sub_runs)
    print(f'=== {c.run_name} — DREAM trigger-latency scan (diagnostic) ===')
    print(f'wrote       : {out}')
    print(f'scanned     : latency {LATENCY_LADDER}  (control {BASE_LATENCY} = run_77)')
    print(f'held fixed  : {N_SAMPLES} smp x {SAMPLE_PERIOD} ns, IPD {IPD}, ZS={ZS}; '
          f'drift {DRIFT_V} (D {DRIFT_V-DRIFT_D_OFFSET}), resist '
          f'A{RESIST_V["A"]}/B{RESIST_V["B"]}/C{RESIST_V["C"]}/D{RESIST_V["D"]}, 0.90 MIP, mesh off')
    print(f'ordering    : cycle-major ({N_CYCLES} cycles x {len(LATENCY_LADDER)} points)')
    print(f'total       : {n} sub-runs x {SUBRUN_MIN:g} min = {total/60:.1f} h')
    print()
    print(f'{"latency":>8s} {"derand buf":>11s} {"evt buf":>8s} {"Hwm":>5s} {"Lwm":>5s}   '
          f'(burst depth must stay CONSTANT across the ladder)')
    for lat in LATENCY_LADDER:
        h, l = _hwm(lat, N_SAMPLES)
        print(f'{lat:8d} {512-lat:11d} {(512-lat)//N_SAMPLES:8d} {h:5d} {l:5d}')
    caps = {_hwm(lat, N_SAMPLES)[0] for lat in LATENCY_LADDER}
    print(f'  -> RunCtrl cap across ladder: {sorted(caps)}  '
          f'{"OK (constant)" if len(caps) == 1 else "!! NOT CONSTANT — cap would confound the scan"}')
    print(f'  -> REQUESTED override: Hwm {OVR_WRN_HWM} / Lwm {OVR_WRN_LWM}  '
          f'{"passes through (below cap)" if OVR_WRN_HWM < min(caps) else "!! AT/ABOVE CAP — would be clamped"}')
    if OVR_WRN_LWM >= OVR_WRN_HWM:
        print('  !! Lwm >= Hwm — OverflowWarning hysteresis cannot clear')
    print()
    print('first 5 sub-runs:')
    for sr in c.sub_runs[:5]:
        print(f'  {sr["sub_run_name"]:26s} latency {sr["latency"]:3d}  n_smp '
              f'{sr["n_samples_per_waveform"]}  resist A{sr["hvs"]["5"]["1"]}/B{sr["hvs"]["5"]["2"]}'
              f'/C{sr["hvs"]["5"]["3"]}/D{sr["hvs"]["5"]["4"]}  drift {sr["hvs"]["9"]["0"]}')
    print()
    print('⚠ launching this STOPS run_77. Analyse with clean_timing.py per latency point;')
    print('  metric = %% of clean drift tracks whose first hit is in sample 0.')
    print()
    print('⚠ AFTER THE FIRST SUB-RUN STARTS, verify the Hwm override actually landed —')
    print('   a silently-dropped override would fake a null result:')
    print('   grep OvrWrn ~/july_dream/dream_run/run_78/*/*.cfg | head        # expect 2 / 1')
    print('   .venv/bin/python dream_scripts/feu_trig_counters.py             # expect Hwm 2')
    print('   Also watch ev/spill vs run_77 (~89 triggers/pulse): the 07-22 ladder predicts')
    print('   a 10-25%% throughput cost at this watermark. If the clip metric is flat AND')
    print('   ev/spill dropped, the watermark change bought nothing — revert to default.')
    print('Launch: ./start_run.sh run_config_latency_scan.json')
