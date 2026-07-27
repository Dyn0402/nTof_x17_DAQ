#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_cosmics_optimal_80.py — run_80, 2026-07-27.
A 2-hour BEAM-OFF cosmic run at the run_79 production operating point. Single fixed
point, no scan axis, 15 min sub-runs x 8 = 2 h, stop-anywhere.

Purpose: use the beam stop to get a cosmic (pure-MIP, flash-free, spill-free) reference
at exactly the operating point run_79 is taking beam data on, so the two are directly
comparable — same HV, same readout window, same watermark, same RAW chain.

WHAT IS CARRIED OVER FROM run_79 (unchanged — this is the whole point)
    HV      : drift 700 V all four; resist A540 / B540 / C525 / D520
              (calibrations/mm/statistics_run_config_run67.json, the run_67 optimum)
    readout : RAW full readout, latency 27, n_samples 20, 60 ns sampling, IPD 5
    FIFO    : OvrWrnHwm 2 / Lwm 1
    scint PMT bias: the 07-19 Y88 equalised setpoints (merged in per sub-run)

WHAT NECESSARILY CHANGES (beam is off)
  1. TRIGGER GATING. run_79 runs `scint --singles --ps-pickup`: M4.C = or_veto(Singles,
     lemo0) gated by the N93B ~1->81 ms acceptance window, which is opened by the PS
     gamma-flash. With no beam there are no PS pulses, that gate never opens, and a
     veto-gated trigger is vetoed ~100%. So M4.C becomes a PLAIN OR on the Singles line
     (veto input inert) and M4.D drops the PS leg:

         .243 M4.C = FN_OR, lemo0 only   -> Singles, veto OPEN (ungated)
         .243 M4.D = FN_OR, lemo1 only   -> C out; PS/flash line (lemo0) off

     which is exactly `n1081b/setup_cosmics_singles_ungated.py`, as run_72/73/74 used.
     ⚠ The M4.D1 PS gate&delay (1440 ns) is NOT touched by that script and does NOT need
     to be — the leg is simply de-selected from the OR. It is still 1440 ns when beam
     returns. Nothing about the run_79 flash framing has to be re-done.

  2. PLASTIC DISCRIMINATOR THRESHOLD: 0.90 MIP -> 0.5 MIP (A-65/B-78/C-86/D-83 mV,
     calibrations/pss/mip_thresholds_y88.json), re-asserted every sub-run by the
     `cosbounce` scan tag. This is a DELIBERATE departure from "the run_79 point" and
     the one judgement call in this config: cosmics are MIPs, so their plastic Landau
     sits with its MPV at ~1 MIP, and run_79's 0.90 MIP bar would cut a large fraction
     of them for no physics gain. Every previous cosmic run here (run_68/72/73/74) used
     0.5 MIP, so this also keeps run_80 comparable with them.
     ** To hold run_79's 0.90 MIP bar instead, set MIP=0.90 (uses the `stat090` tag). **

  3. MESH charge injection: not touched, and no mesh axis. M6.B in0 (the injection
     trigger) is PS/T0-fed, so with beam off it never fires and injection is a null
     axis (memory `mesh-injection-is-ps-triggered`). The legs are already OFF —
     run_79's `stat090` tag has been asserting SEC_B out2/out3 = False every sub-run —
     and the `cosbounce` tag does not touch .245 at all, so board .245 is never
     contacted by this run and the legs stay off.

READOUT WINDOW — why latency 27 / n 20 is still right with no beam.
  The run_78 latency ladder measured the drift-charge onset moving 1:1 with latency at
  onset = latency - 25 (lat 33 -> smp 8, lat 35 -> smp 10), giving onset ~smp 2 at
  latency 27. That relation is a property of the SCINT trigger path (M2 plastics ->
  M3 -> M4.C -> M4.D), which cosmics use unchanged: opening the veto and dropping the
  PS leg from the M4.D OR changes *which* pulses are accepted, not their timing. So the
  cosmic MM signal lands in the same samples as the beam one, and the 20-sample window
  (2 lead-in + 14 = 95% of the drift charge + 4 margin) still contains it.
  ⚠ The 4-sample margin is not large. If this run is meant to be compared frame-for-
  frame against the OLDER cosmic runs (run_59/68/72/73/74, which all ran latency 35 /
  n 32), regenerate with `LATENCY=35 N_SAMPLES=32` instead — at the cost of ~60% more
  disk and a window no longer matching run_79.

WATERMARK — Hwm 2 / Lwm 1 kept for consistency, but expect it to be INERT here. The
  acceptance comb it fixes is a flash-burst pile-up effect; ungated cosmics arrive at
  ~25 Hz Poisson, far below any FIFO pressure. Keeping it means the run_80 and run_79
  FEU configs differ in nothing but the trigger. RunCtrl's cap at lat 27 / n 20 is
  (512-27)//20 = 24 -> 20, well above 2, so 2 passes through unclamped.

DISK — run_72 MEASURED ungated cosmics at 25.6 Hz. RAW at n 20 is 20/32 of the n 32
  rate, so ~4.9 MB/s -> ~4.4 GB per 15 min sub-run, ~35 GB for the full 2 h.
  ⚠ At the time this was written /mnt/data had 451 GB free (87% used) with run_79
  already occupying 540 GB. 35 GB fits, but check `df -h /mnt/data` before launching
  and let space_watcher / backup_watcher keep draining to HDD+EOS.

NO PEDESTALS. This is a RAW run; it does not need them, and pedestals are not taken
  without an explicit instruction from the operator.

PRE-RUN (beam OFF, run_79 STOPPED, boards free — check config/n1081b_access/ first):
  .venv/bin/python n1081b/setup_cosmics_singles_ungated.py
  verify: .venv/bin/python n1081b/trigger_mode.py status
          -> expect C fn=or lemos=[0]  (plain OR = veto OPEN), D fn=or lemos=[1]
Launch:
  ./start_run.sh run_config_cosmics_optimal_80.json
RESTORE when beam returns — see docs/RESTORE_run79.md (one script + one verify).
"""
import os

# --- repo-root shim (run_config_beam/base live one dir up) ---
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from run_config_beam import Config as BeamConfig

RUN_NUM = 80

# ---- readout: identical to run_79 unless overridden ----
LATENCY = int(os.environ.get('LATENCY', '27'))
N_SAMPLES = int(os.environ.get('N_SAMPLES', '20'))
SAMPLE_PERIOD = 60
IPD = 5
OVR_WRN_HWM = int(os.environ.get('OVR_WRN_HWM', '2'))
OVR_WRN_LWM = int(os.environ.get('OVR_WRN_LWM', '1'))

# ---- dwell: 8 x 15 min = 2 h ----
SUBRUN_MIN = float(os.environ.get('SUBRUN_MIN', '15'))
N_SUBRUNS = int(os.environ.get('N_SUBRUNS', '8'))

# ---- operating point: the run_79 / run_67 optimum, unchanged ----
DRIFT_V = 700
DRIFT_D_OFFSET = int(os.environ.get('DRIFT_D_OFFSET', '0'))
RESIST_V = {'A': 540, 'B': 540, 'C': 525, 'D': 520}

# ---- plastic discriminator bar: 0.5 MIP for cosmics (see docstring), 0.90 to match run_79
MIP = os.environ.get('MIP', '0.50')
TAG = 'stat090' if MIP in ('0.9', '0.90') else 'cosbounce'
_THR = ('A-118/B-139/C-157/D-134 mV' if TAG == 'stat090'
        else 'A-65/B-78/C-86/D-83 mV')


class Config(BeamConfig):
    def _set_defaults(self, config_path=None):
        super()._set_defaults(config_path)

        self.run_name = f'run_{RUN_NUM}'
        self.beam_type = 'cosmics'          # beam off
        self.run_out_dir = f'{self.data_out_dir}{self.run_name}/'
        self.dream_daq_info['run_directory'] = f'/home/mx17/july_dream/dream_run/{self.run_name}/'
        self.dream_daq_info['data_out_dir'] = f'{self.run_out_dir}'
        self.processor_info['run_dir'] = f'{self.run_out_dir}'
        self.hv_info['run_out_dir'] = self.run_out_dir
        self.resume = False
        # scan_control re-asserts the plastic thresholds every sub-run. The ungated
        # singles ROUTING is set ONCE pre-run and is not touched by the tag.
        self.n1081b_scan = 'on'

        self.trigger = (
            f'BEAM-OFF COSMIC reference run at the run_79 production operating point '
            f'({self.run_name}). Single fixed point, no scan axis. Trigger: scint SINGLES '
            f'with the veto OPEN (UNGATED) -- M4.C = plain OR(Singles lemo0), M4.D = '
            f'OR(C-out lemo1), PS/flash leg de-selected, set once pre-run via '
            f'setup_cosmics_singles_ungated.py. The N93B ~1-81 ms acceptance gate is NOT '
            f'applied (it is PS-opened and there is no beam). READOUT identical to run_79: '
            f'RAW full readout (zero_suppress=False), latency {LATENCY}, {N_SAMPLES} smp x '
            f'{SAMPLE_PERIOD} ns = {N_SAMPLES * SAMPLE_PERIOD} ns, IPD {IPD}, trigger-FIFO '
            f'OvrWrnHwm {OVR_WRN_HWM}/Lwm {OVR_WRN_LWM} (inert at cosmic rates, kept so the '
            f'FEU config differs from run_79 in nothing but the trigger). HV identical to '
            f'run_79: drift {DRIFT_V} V all four (D {DRIFT_V - DRIFT_D_OFFSET}), resist '
            f'A{RESIST_V["A"]}/B{RESIST_V["B"]}/C{RESIST_V["C"]}/D{RESIST_V["D"]} V. Plastic '
            f'discriminators at {MIP} MIP ({_THR}) re-asserted per sub-run by the `{TAG}` '
            f'tag; walls (M1) not touched. NO MESH AXIS and .245 is never contacted -- the '
            f'injection trigger (M6.B in0) is PS/T0-fed and is a null axis with beam off; '
            f'the det A/C legs are left OFF as run_79 held them. {SUBRUN_MIN:g} min '
            f'sub-runs x {N_SUBRUNS} = {N_SUBRUNS * SUBRUN_MIN / 60:g} h, stop-anywhere. '
            f'Scint PMT bias at the 07-19 Y88 equalised setpoints. Ar/Iso 90/10, 3He, no Pb. '
            f'RESTORE to run_79: see docs/RESTORE_run79.md.')

        self.dream_daq_info.update({
            'daq_config_template_path': f'{self.base_out_dir}dream_config/Tcm_Mx17_July.cfg',
            'zero_suppress': False,
            'latency': LATENCY,
            'n_samples_per_waveform': N_SAMPLES,
            'sample_period': SAMPLE_PERIOD,
            'inter_packet_delay': IPD,
            'ovr_wrn_hwm': OVR_WRN_HWM,
            'ovr_wrn_lwm': OVR_WRN_LWM,
        })

        d_drift = DRIFT_V - DRIFT_D_OFFSET
        drift_hvs = {'0': DRIFT_V, '1': DRIFT_V, '2': DRIFT_V, '3': d_drift}
        resist_hvs = {'1': RESIST_V['A'], '2': RESIST_V['B'],
                      '3': RESIST_V['C'], '4': RESIST_V['D']}

        self.sub_runs = []
        for k in range(N_SUBRUNS):
            self.sub_runs.append({
                'sub_run_name': f'{TAG}_cos_{k:04d}',
                'run_time': SUBRUN_MIN, 'post_pause_s': 0,
                'inter_packet_delay': IPD,
                'latency': LATENCY, 'n_samples_per_waveform': N_SAMPLES,
                'hvs': {'5': dict(resist_hvs), '9': dict(drift_hvs)},
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
    out = 'config/json_run_configs/run_config_cosmics_optimal_80.json'
    c.write_to_file(out)

    buf = (512 - LATENCY) // N_SAMPLES
    cap = buf - 4 if buf > 16 else (buf - 3 if buf > 8 else buf - 2)
    mb_s = 7.8 * N_SAMPLES / 32          # run_72 measured 7.8 MB/s at n32, 25.6 Hz
    total_min = sum(s['run_time'] for s in c.sub_runs)

    print(f'=== {c.run_name} — BEAM-OFF cosmic reference at the run_79 operating point ===')
    print(f'wrote      : {out}')
    print(f'beam       : {c.beam_type} | gas {c.gas} | target {c.target_type}')
    print(f'readout    : RAW, latency {LATENCY}, {N_SAMPLES} smp x {SAMPLE_PERIOD} ns '
          f'= {N_SAMPLES * SAMPLE_PERIOD} ns, IPD {IPD}   [same as run_79]')
    print(f'watermark  : Hwm {OVR_WRN_HWM} / Lwm {OVR_WRN_LWM}  (RunCtrl cap here is {cap} '
          f'-> passes through; inert at cosmic rates)')
    print(f'HV         : drift {DRIFT_V} (D {DRIFT_V - DRIFT_D_OFFSET}), resist '
          f'A{RESIST_V["A"]}/B{RESIST_V["B"]}/C{RESIST_V["C"]}/D{RESIST_V["D"]}   [same as run_79]')
    print(f'plastics   : {MIP} MIP  {_THR}   (tag {TAG!r}, M2/.241 only)')
    print(f'trigger    : UNGATED scint singles — M4.C plain OR[0], M4.D OR[1]  '
          f'(set pre-run, NOT by the tag)')
    print(f'mesh       : no axis; legs left OFF; board .245 never contacted')
    print(f'total      : {len(c.sub_runs)} sub-runs x {SUBRUN_MIN:g} min = '
          f'{total_min / 60:g} h   (stop-anywhere)')
    print(f'disk       : ~{mb_s:.1f} MB/s -> ~{mb_s * SUBRUN_MIN * 60 / 1000:.1f} GB/sub-run, '
          f'~{mb_s * total_min * 60 / 1000:.0f} GB total  (check `df -h /mnt/data` first)')
    print()
    print('PRE-RUN (beam OFF, run_79 stopped, boards free — check config/n1081b_access/):')
    print('  .venv/bin/python n1081b/setup_cosmics_singles_ungated.py')
    print('  verify: .venv/bin/python n1081b/trigger_mode.py status   # C or[0], D or[1]')
    print('Launch : ./start_run.sh run_config_cosmics_optimal_80.json')
    print('RESTORE: docs/RESTORE_run79.md')
