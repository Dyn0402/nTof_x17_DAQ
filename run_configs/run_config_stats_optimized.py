#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_stats_optimized.py — run_79, 2026-07-26.
The production statistics run, on a window and a trigger-FIFO watermark that were MEASURED
rather than assumed. Single fixed operating point, 1 h sub-runs, open-ended.

    HV / thresholds : unchanged from run_77 (the run_67 optimum)
    latency         : 35 -> 27          (window onset moved to sample 2)
    n_samples       : 32 -> 20          (95% of drift charge + margin, 37.5% less readout)
    OvrWrnHwm/Lwm   : 2 / 1             (comb flattened; kept from run_78)
    M4.D1 PS delay  : 1800 -> 1440 ns   (flash re-framed to sample 5) -- SET ON THE BOARD
                                           already, 2026-07-26, read back OK

WHAT run_78 MEASURED (latency ladder 33/35/39/43/47/51, 2 min/point, 24 min total)

  The charge-weighted sample profile (det A+C, low-occupancy non-flash events, the only
  view free of hit-finder and pile-up artefacts) shows the drift signal as a ~19-sample
  BAND whose leading edge moves 1:1 with latency:

      latency  33 -> onset sample  8
      latency  35 -> onset sample 10      <- run_77 ran here
      latency  39 -> onset sample 14
      latency  43 -> onset sample 18      (band then truncated by the window end)

  So run_77 was wasting TEN samples of empty lead-in and the band only just fitted. Moving
  the onset to 2 samples needs latency 35 - 8 = 27.

  Charge containment measured at latency 33 (the point where the whole band is inside the
  window), floor-subtracted, both edge-artefact samples (0 and 31) excluded:

      90% of signal charge within 12 samples of onset
      95%                    within 14 samples
      99%                    within 21 samples

  Hence n_samples = 2 (lead-in) + 14 (95%) + 4 (margin) = 20. That margin is deliberate:
  drift time scales with drift velocity, so a future drop to drift 600 V lengthens the band
  by roughly 15% (~16 samples) and would clip at n=18. n=20 still contains it.

⚠ WHAT I GOT WRONG FIRST, recorded so nobody re-derives it from the same bad evidence.
  The run_77 analysis concluded the window was FRONT-CLIPPED, from drift tracks whose
  first hit sat in sample 0 (A 23.9 / B 36.7 / C 19.0 / D 45.1% after full spark/flash/
  pile-up rejection). That conclusion was WRONG. run_78 disproves it two ways:
    * the sample-0 population does NOT drain as the window moves later (det A: 15.6, 14.1,
      19.2, 16.4, 17.0, 20.9% across latency 33..51) — clipped signal would have drained;
    * the track-finder t_min peak appears to move only ~1 sample per 3 latency units, while
      the CHARGE profile and the flash both move exactly 1:1. The track-based number is a
      hit-finder edge artefact, not physics.
  Detectors B and D are dominated by that artefact (their t_min peak sits at sample 0-1 at
  every latency) so they cannot be used for window timing at all; A and C carry the real
  signal. USE THE CHARGE PROFILE, not track t_min, for any future window work.

FLASH FRAMING — calibrated, not guessed. With the PS delay held at 1800 ns the flash peak
  tracked latency exactly:

      latency  33 35 39 43   ->  flash peak sample  5  7 11 15
      model    flash_peak = 2.0 + latency - delay_ns/60   (predicts 5.5/7.5/11.5/15.5)

  Inverting at latency 27 for a flash peak at sample 5:
      delay = 60 x (2.0 + 27 - 5) = 1440 ns
  which leaves ~3 samples of pre-flash baseline and puts the flash's rise and peak well
  inside a 20-sample window. The far flash tail (detector recovery, old samples >20) is no
  longer framed; flash events are excluded from the physics numerator and denominator
  anyway, so this costs nothing here — but flash-RECOVERY studies should re-frame.

WATERMARK Hwm=2 — kept, and it earned it. Measured on run_78 vs run_77:

      metric (1-10 ms)          Hwm 11    Hwm 2
      evenness CV, 0.5 ms bins   1.500    0.513
      burst depth                 1.72     1.13
      drain between bursts       1.087 ms 0.664 ms
      triggers per pulse         88.9     79.7   (-10%)

  The starved bins filled in (3.0-3.5 ms: 0.103 -> 0.927 per pulse; 6.0-6.5: 0.165 -> 1.000;
  7.5-8.0: 0.150 -> 0.992) and the cost came almost entirely out of the 1.0-1.5 ms spike
  (10.64 -> 2.62). The 2026-07-22 scan that called low watermarks harmful measured TOTAL
  band throughput on ZS+IPD10 where no comb existed; it does not apply to RAW+IPD5.
  ⚠ RunCtrl clamps the watermark DOWNWARD only, so 2 passes through: at latency 27 /
  n_samples 20 its cap is (512-27)//20 = 24 -> Hwm 20, far above 2.

  n_samples 32 -> 20 shortens readout by a further 37.5%, so the drain should fall from
  ~0.66 ms to ~0.42 ms and the distribution should get flatter still. That is a PREDICTION
  — verify it on sub-run 0000 rather than assuming it.

DISK — the one unambiguous win: ~8.4 MB/s at n32 becomes ~5.3 MB/s, i.e. ~19 GB/h and
  ~450 GB/day instead of ~30 GB/h and ~720 GB/day. The SSD->HDD->EOS pipeline still has to
  keep up for the whole run.

VERIFY ON SUB-RUN 0000 before letting this run for days (scratch scripts in the session
  scratchpad; the charge-profile recipe is in the docstring above):
   1. signal onset at sample ~2 in the charge profile (det A+C, low-occupancy, non-flash)
   2. flash peak at sample ~5 in flash events
   3. Hwm reads 2: .venv/bin/python dream_scripts/feu_trig_counters.py
   4. n_samples 20 in the archived cfg, and latency 0x001B:
        grep -E "^Feu \\* Dream \\* 12|NbOfSamples" ~/july_dream/dream_run/run_79/*/*.cfg
      (⚠ the template carries TWO COMMENTED "Feu * Dream * 12" lines before the live one —
       grep for the line WITHOUT a leading '#', or you will read 0x005F and think the
       latency never changed. It cost me a false alarm on run_78.)
   5. evenness: the 1-10 ms profile should be flatter than run_78's CV of 0.513

TRIGGER: scint --singles --ps-pickup, M4.C = or_veto(Singles, lemo0) gated by the N93B
  ~1->81 ms window, M4.D = OR(lemo0 = PS/flash delayed 1440 ns, lemo1 = C-out). Routing is
  already in this state; only the PS delay changed and it is already applied.

PRE-RUN (beam ON — daq_control has NO beam-gating, wait for a real pulse):
  verify: .venv/bin/python n1081b/trigger_mode.py status         -> C or_veto [0], D [0,1]
          .venv/bin/python n1081b/set_ps_trigger_delay.py --show -> delay 1440
Launch: ./start_run.sh run_config_stats_optimized.json

run_81 (2026-07-27) — the SAME operating point, restarted as a fresh run after the midday beam
  stop. run_79 ran 15 complete 1 h sub-runs (stat090_0000..0014) and was stopped manually part
  way through 0015, which was therefore NOT marked complete. The stop was followed by run_80
  (2 h of beam-off cosmics at this same point) and a fresh pedestal set, so run_81 starts on a
  clean boundary rather than resuming run_79 mid-grid. Nothing about the configuration changed:
      RUN_NUM=81 .venv/bin/python run_configs/run_config_stats_optimized.py
      ./start_run.sh run_config_stats_optimized_81.json
  Board work needed first: the cosmic run left M4.C as a plain OR (veto open) and M4.D on
  lemo1 only, so `n1081b/trigger_mode.py scint --singles --ps-pickup` must be re-run to put the
  N93B acceptance gate and the PS leg back. The M4.D1 1440 ns PS delay was never disturbed
  (verified on hardware 2026-07-27 after the cosmic setup ran) — nothing else to re-apply.

PAUSING FOR A BEAM STOP (added 2026-07-27) — see docs/RESTORE_run79.md, which records the
  as-built board state while this run was live and the exact steps back. In short:
  `RESUME=1 python run_configs/run_config_stats_optimized.py` writes
  run_config_stats_optimized_resume.json (resume=True, i.e. sub-runs with a
  .subrun_complete marker are skipped), and the only board work to come back is
  `n1081b/trigger_mode.py scint --singles --ps-pickup` — the M4.D1 1440 ns PS delay is NOT
  disturbed by the cosmic setup script, so the flash framing survives untouched.
"""
import os

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from run_config_beam import Config as BeamConfig

# RUN_NUM env var (added 2026-07-27): the operating point below is measured and settled, so a
# later production run at the SAME point should re-use this generator rather than fork a copy
# that can drift. `RUN_NUM=81 python run_configs/run_config_stats_optimized.py` writes
# run_config_stats_optimized_81.json; the default 79 keeps the original filenames untouched.
RUN_NUM = int(os.environ.get('RUN_NUM', '79'))

# ---- readout: the run_78 result ----
LATENCY = int(os.environ.get('LATENCY', '27'))        # onset at sample 2 (was 35)
N_SAMPLES = int(os.environ.get('N_SAMPLES', '20'))    # 95% charge + margin (was 32)
IPD = 5
SAMPLE_PERIOD = 60
ZS = os.environ.get('DREAM_ZS', '0') == '1'

# ---- trigger-FIFO watermark: keep the run_78 setting ----
OVR_WRN_HWM = int(os.environ.get('OVR_WRN_HWM', '2'))
OVR_WRN_LWM = int(os.environ.get('OVR_WRN_LWM', '1'))

# ---- dwell ----
SUBRUN_MIN = float(os.environ.get('SUBRUN_MIN', '60'))
N_SUBRUNS = int(os.environ.get('N_SUBRUNS', '120'))

# ---- resume (2026-07-27): set RESUME=1 to write a RESUME config that skips every
#      sub-run already carrying a .subrun_complete marker, so run_79 can be paused for a
#      beam stop and picked up afterwards without re-taking or overwriting what it has.
#      Writes to a SEPARATE json so the original fresh-start config is never clobbered.
#      See docs/RESTORE_run79.md.
RESUME = os.environ.get('RESUME', '0') == '1'

# ---- operating point (calibrations/mm/statistics_run_config_run67.json), unchanged ----
DRIFT_V = 700
DRIFT_D_OFFSET = int(os.environ.get('DRIFT_D_OFFSET', '0'))
RESIST_V = {'A': 540, 'B': 540, 'C': 525, 'D': 520}
TAG = 'stat090'

# ---- flash framing (applied on the board pre-run, not by this config) ----
PS_DELAY_NS = 60 * (2.0 + LATENCY - 5)                # -> 1440 ns at latency 27


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

        readout_txt = ('ZS' if ZS else 'RAW / full readout (zero_suppress=False)')
        self.trigger = (
            f'PRODUCTION statistics run at the run_67 optimum on a MEASURED readout window '
            f'({self.run_name}). No scan axis — every sub-run identical. HV: drift {DRIFT_V} V '
            f'all four, resist A{RESIST_V["A"]}/B{RESIST_V["B"]}/C{RESIST_V["C"]}/D{RESIST_V["D"]} V; '
            f'plastic 0.90 MIP; mesh charge-injection OFF — all re-asserted per sub-run by the '
            f'`{TAG}` tag. READOUT set from the run_78 latency scan: latency {LATENCY} puts the '
            f'drift-signal onset at sample ~2 (run_77 wasted 10 empty lead-in samples at latency '
            f'35), n_samples {N_SAMPLES} holds 95% of the drift charge plus margin, '
            f'{SAMPLE_PERIOD} ns sampling, IPD {IPD}, {readout_txt}. Trigger-FIFO watermark forced '
            f'to Hwm {OVR_WRN_HWM}/Lwm {OVR_WRN_LWM}: measured to cut the 1-10 ms acceptance-comb '
            f'CV from 1.50 to 0.51 for -10% total triggers, spending it on the 1-1.5 ms spike. '
            f'PS + SINGLES trigger, M4.D1 G&D delay {PS_DELAY_NS:.0f} ns re-frames the gamma flash '
            f'to sample ~5 (calibrated: flash_peak = 2 + latency - delay/60). '
            f'{SUBRUN_MIN:g} min sub-runs x {N_SUBRUNS} (open-ended, operator-killed). Walls (M1) '
            f'0.5 MIP. Scint PMT bias at 07-19 Y88 setpoints. Ar/Iso 90/10, 3He, no Pb.')

        self.dream_daq_info.update({
            'daq_config_template_path': (
                f'{self.base_out_dir}dream_config/'
                f'{"Tcm_Mx17_July_ZS.cfg" if ZS else "Tcm_Mx17_July.cfg"}'),
            'zero_suppress': ZS,
            'latency': LATENCY,
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
        for k in range(N_SUBRUNS):
            self.sub_runs.append({
                'sub_run_name': f'{TAG}_{k:04d}',
                'run_time': SUBRUN_MIN, 'post_pause_s': 0,
                'inter_packet_delay': IPD,
                'latency': LATENCY, 'n_samples_per_waveform': N_SAMPLES,
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
    _stem = 'run_config_stats_optimized' + (f'_{RUN_NUM}' if RUN_NUM != 79 else '')
    out = f'config/json_run_configs/{_stem}{"_resume" if RESUME else ""}.json'
    c.write_to_file(out)

    buf = (512 - LATENCY) // N_SAMPLES
    cap = buf - 4 if buf > 16 else (buf - 3 if buf > 8 else buf - 2)
    scale = N_SAMPLES / 32
    print(f'=== {c.run_name} — production statistics run on the MEASURED window'
          f'{" [RESUME]" if RESUME else ""} ===')
    print(f'wrote        : {out}')
    print(f'resume       : {RESUME}   '
          f'({"completed sub-runs are SKIPPED — pause/restart safe" if RESUME else "fresh start"})')
    print(f'latency      : {LATENCY}   (run_77 used 35 -> signal onset was sample 10; '
          f'now sample ~2)')
    print(f'n_samples    : {N_SAMPLES}   (95% of drift charge = 14 smp from onset, +2 lead-in, '
          f'+4 margin)')
    print(f'watermark    : Hwm {OVR_WRN_HWM} / Lwm {OVR_WRN_LWM}  '
          f'(RunCtrl cap here is {cap} -> passes through)')
    print(f'PS delay     : {PS_DELAY_NS:.0f} ns -> flash peak at sample '
          f'{2.0 + LATENCY - PS_DELAY_NS/60:.0f}   [ALREADY SET ON THE BOARD]')
    print(f'HV           : drift {DRIFT_V} (D {DRIFT_V-DRIFT_D_OFFSET}), resist '
          f'A{RESIST_V["A"]}/B{RESIST_V["B"]}/C{RESIST_V["C"]}/D{RESIST_V["D"]}, 0.90 MIP, mesh off')
    print(f'total        : {len(c.sub_runs)} sub-runs x {SUBRUN_MIN:g} min = '
          f'{sum(s["run_time"] for s in c.sub_runs)/60:.0f} h (open-ended, stop-anywhere)')
    print()
    print(f'expected vs run_77 (n32/lat35/Hwm11):')
    print(f'  readout per event  x{scale:.3f}  -> drain ~0.66 ms (Hwm2 @ n32) should fall to '
          f'~{0.664*scale:.2f} ms')
    print(f'  disk               ~8.4 MB/s -> ~{8.4*scale:.1f} MB/s  '
          f'(~{8.4*scale*3600/1000:.0f} GB/h, ~{8.4*scale*86400/1000:.0f} GB/day)')
    print()
    print('VERIFY ON SUB-RUN 0000 (see docstring): onset at smp ~2, flash peak ~5, Hwm 2,')
    print('  archived cfg latency 0x%04X / NbOfSamples %d, 1-10 ms CV better than 0.513.' % (LATENCY, N_SAMPLES))
    print('  ⚠ grep the UNCOMMENTED "Feu * Dream * 12" line — two commented ones precede it.')
    print(f'Launch: ./start_run.sh {out.split("/")[-1]}')
    if not RESUME:
        print('        (to PICK UP an interrupted run_79 instead of restarting it, regenerate '
              'with RESUME=1 — see docs/RESTORE_run79.md)')
