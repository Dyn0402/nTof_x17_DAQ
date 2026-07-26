#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_stats_run67.py — run_77, 2026-07-26.
PRODUCTION STATISTICS RUN at a SINGLE fixed operating point. No scan axis of any kind:
every sub-run is identical, 1 h long, and the grid is open-ended (operator-killed).

THE OPERATING POINT comes verbatim from
    calibrations/mm/statistics_run_config_run67.json
(the run_67 re-reco'd "most reconstructed tracks per spill over the whole 1-76 ms gate"
setpoint, exported 2026-07-26):

    drift    700 V on ALL FOUR detectors (uniform, incl. det D — see the D note below)
    resist   A 540, B 540, C 525, D 520 V      (C and D want ~15 V less — a real
                                                per-chamber gain difference)
    plastic  0.90 MIP  (M2/.241 thrA-D = -118/-139/-157/-134 mV)
    mesh     OFF (charge-injection legs SEC_B out2/out3 disabled)

WHAT'S DIFFERENT vs run_76 (run_config_48h_hv_stats.py)
  1. NO HV SCAN. run_76 walked a 2 drift x 5 resist grid; run_77 sits at one point. The
     HV block is byte-identical in every sub-run, so the crate does no ramping after the
     first sub-run and there are no HV-transition settling losses at all.
  2. PER-DETECTOR resist (540/540/525/520) instead of a common ladder value. This is the
     whole point of the calibration: C and D peak ~15 V below A and B.
  3. PLASTIC THRESHOLD 0.90 MIP, not 1.41. New scan tag `stat090` (added to
     config/n1081b_scan_schedule.json) carries the 0.90 MIP discriminator set AND the
     mesh-off assertion; it is the exact m090On/m090Off threshold set from run_67 plus
     acmeshOff's mesh_ac{output_status: false}. Every sub-run carries the same tag, so
     scan_control re-asserts both each sub-run — a HOLD, not a scan.
  4. 1 h sub-runs (was 30 min), open-ended count.
  Everything else — trigger routing, DREAM readout, scint PMT bias — is unchanged from
  run_76/run_71, deliberately: the calibration was measured on run_67 data taken through
  this same chain, and a statistics run is not the place to change the readout.

⚠ DET D DRIFT IS 700 V, NOT 650. run_71/75/76 ran det D 50 V below A/B/C after D tripped
  its drift on 2026-07-23. run_77 does NOT: run_67 itself ran D at the same drift as
  A/B/C, so "drift 700 V" in the calibration file literally means D's channel at 700 V,
  and the operator chose (2026-07-26) to reproduce the measured point rather than the
  protective offset. D is therefore at its highest sustained drift since the trip, for
  days, unattended. The 1 Hz HV trip/deviation Telegram alerts are the safety net. If D
  trips or sparks repeatedly, relaunch with DRIFT_D_OFFSET=50 (env) to put it back to
  650 V — that is a 50 V change on one channel and does not invalidate the run.

⚠ RESIST IS EFFICIENCY-ONLY. run_67 carries no spark/stability data, so a discharge
  threshold overrides these numbers. All four values (520-540) are well inside exercised
  territory (run_64 went to 570, run_57/58 to 580, run_76 is running 520-560 right now),
  so this point is GENTLER than run_76's high end.

TRIGGER (routing set ONCE pre-run; identical to run_71/75/76):
  scint --singles --ps-pickup, PS + singles CO-FRAMED in 32 smp: latency 35, M4.D in0
  G&D delay 1800 ns pulls the flash to ~smp 13 beside the singles MM at ~smp 11 ->
  per-event time-since-flash. M4.C = or_veto(Singles, lemo0) gated by the N93B ~1->81 ms
  acceptance window; M4.D = OR(lemo0 = PS/gamma-flash, lemo1 = C-out). 32 smp x 60 ns
  (1.92 us), IPD 5, RAW (zero_suppress=False). RdClk left at the current 25 MHz default.
  Walls (M1) unchanged at 0.5 MIP (external, not in the tag).

DISK — ⚠ THE BINDING CONSTRAINT ON AN OPEN-ENDED RAW RUN. At the run_75-measured beam
  singles rate (~17.5 Hz, ~311 kB/event RAW n32 => ~5.4 MB/s) a 1 h sub-run is ~19 GB and
  a day of continuous beam is ~460 GB. The SSD holds only a few hours of that, so the
  SSD->HDD->EOS pipeline (backup_watcher + space_manager) MUST be healthy and keeping up
  for the entire run; if the EOS backup stalls, space_manager cannot free the HDD and the
  run wedges on a full disk. CONFIRM both watchers are running and EOS is reachable before
  launch, and check headroom daily. Stop-anywhere: every sub-run is self-contained, so
  killing the run at any point costs at most the sub-run in flight.

FEU GUARDS — the 07-24 fix must be standing before an unattended multi-day run:
  feu_health.py preflight + subrun_health.py byte check + daq_control fail-closed + the 2
  Telegram rules (memory feu-crate-guards-and-alarms). run_75 silently lost 51/60 sub-runs
  to a dead FEU without them.

SCINT PMT HV: plastics (card 07) + liquids (card 08) held at the 07-19 Y88 equalized
  setpoints (merged into every sub-run, inherited from run_config_beam).

PRE-RUN (boards free; beam ON — daq_control has NO beam-gating, so wait for the first real
  pulse before launching or the empty sub-runs get marked complete and punch permanent
  gaps):
  .venv/bin/python n1081b/trigger_mode.py scint --singles --ps-pickup
  .venv/bin/python n1081b/set_ps_trigger_delay.py --delay 1800
  verify: trigger_mode.py status          -> C or_veto lemos=[0], D lemos=[0,1]
          set_ps_trigger_delay.py --show   -> delay 1800, enable_gd True, invert False
  # Mesh legs: confirm out0/out1 (SiPM bias) ENABLED before launch (read-only):
  #   n1081b/inspect_m6_sections.py
  # The first sub-run (stat090) then re-asserts out2/out3 OFF and drops the plastic
  # discriminators from run_76's 1.41 MIP to 0.90 MIP — expect the trigger rate to RISE.
Launch: ./start_run.sh run_config_stats_run67.json
RESTORE / TEARDOWN: restore='snapshot' returns SEC_B out2/out3 and the M2 thresholds to
  their found state on exit. Trigger stays scint+ps (leave for the next beam run).
"""
import os

# --- repo-root shim (run_config_beam/base live one dir up) ---
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from run_config_beam import Config as BeamConfig

RUN_NUM = 77

# ---- DREAM readout (PS+singles co-framed in 32 smp -- run_56/67 recipe, IPD 5) ----
LATENCY = 35
N_SAMPLES = 32
IPD = 5
SAMPLE_PERIOD = 60
ZS = os.environ.get('DREAM_ZS', '0') == '1'   # default RAW (matches run_67/71/76)

# ---- dwell + length ----
SUBRUN_MIN = float(os.environ.get('SUBRUN_MIN', '60'))    # 1 h sub-runs (operator, run_77)
N_SUBRUNS = int(os.environ.get('N_SUBRUNS', '120'))       # 120 h of grid; operator-killed

# ---- THE operating point (calibrations/mm/statistics_run_config_run67.json) ----
DRIFT_V = 700                                             # uniform, all four detectors
DRIFT_D_OFFSET = int(os.environ.get('DRIFT_D_OFFSET', '0'))   # set 50 to protect det D
RESIST_V = {'A': 540, 'B': 540, 'C': 525, 'D': 520}       # per-detector, card 5 ch1-4

# Plastic 0.90 MIP + mesh OFF, re-asserted every sub-run by scan_control.
TAG = 'stat090'


class Config(BeamConfig):
    def _set_defaults(self, config_path=None):
        super()._set_defaults(config_path)

        self.run_name = f'run_{RUN_NUM}'
        self.run_out_dir = f'{self.data_out_dir}{self.run_name}/'
        self.dream_daq_info['run_directory'] = f'/home/mx17/july_dream/dream_run/{self.run_name}/'
        self.dream_daq_info['data_out_dir'] = f'{self.run_out_dir}'
        self.processor_info['run_dir'] = f'{self.run_out_dir}'
        self.hv_info['run_out_dir'] = self.run_out_dir
        self.resume = False   # fresh run
        # scan_control holds plastic 0.90 MIP + mesh OFF (stat090) each sub-run. scint+ps
        # routing and the PS delay 1800 are set ONCE pre-run. No HV modulation here.
        self.n1081b_scan = 'on'

        readout_txt = ('ZS (Tcm_..._ZS.cfg)' if ZS else 'RAW / full readout (zero_suppress=False)')
        d_drift = DRIFT_V - DRIFT_D_OFFSET
        self.trigger = (
            f'STATISTICS run at the SINGLE run_67 optimal operating point ({self.run_name}); '
            f'no scan axis — every sub-run identical. Setpoint from '
            f'calibrations/mm/statistics_run_config_run67.json: drift {DRIFT_V} V on all four '
            f'(det D {d_drift} V), resist A{RESIST_V["A"]}/B{RESIST_V["B"]}/C{RESIST_V["C"]}/'
            f'D{RESIST_V["D"]} V, plastic 0.90 MIP (A-118/B-139/C-157/D-134), mesh '
            f'charge-injection OFF (SEC_B out2/out3 disabled; SiPM-enable bias out0/out1 left '
            f'ON) — all three re-asserted each sub-run by the scan_control `{TAG}` tag. '
            f'PS + SINGLES trigger: scint --singles --ps-pickup, PS+singles co-framed in 32 smp '
            f'(latency 35, M4.D in0 G&D 1800 ns -> flash ~smp 13, MM ~smp 11); M4.C = '
            f'or_veto(Singles, lemo0) gated by the N93B ~1->81 ms window; M4.D = OR(lemo0 = '
            f'PS/gamma-flash, lemo1 = C-out). 32 smp x 60 ns, IPD 5, {readout_txt}. '
            f'{SUBRUN_MIN:g} min sub-runs x {N_SUBRUNS} (open-ended, operator-killed). '
            f'Walls (M1) 0.5 MIP. Scint PMT bias at 07-19 Y88 setpoints. Ar/Iso 90/10, 3He, no Pb.')

        self.dream_daq_info.update({
            'daq_config_template_path': (
                f'{self.base_out_dir}dream_config/'
                f'{"Tcm_Mx17_July_ZS.cfg" if ZS else "Tcm_Mx17_July.cfg"}'),
            'zero_suppress': ZS,
            'latency': LATENCY,
            'n_samples_per_waveform': N_SAMPLES,
            'sample_period': SAMPLE_PERIOD,
            'inter_packet_delay': IPD,
        })
        if ZS:
            # match run_config_zs_singles defaults for a self-consistent ZS run
            self.dream_daq_info.update({'common_noise_subtraction': True,
                                        'pedestal_subtraction': False})

        # card 9 ch0-3 = drift A/B/C/D; card 5 ch1-4 = resist A/B/C/D.
        drift_hvs = {'0': DRIFT_V, '1': DRIFT_V, '2': DRIFT_V, '3': d_drift}
        resist_hvs = {'1': RESIST_V['A'], '2': RESIST_V['B'],
                      '3': RESIST_V['C'], '4': RESIST_V['D']}

        self.sub_runs = []
        for k in range(N_SUBRUNS):
            # tag (leading '_'-token) keys scan_control -> stat090 (0.90 MIP + mesh OFF).
            self.sub_runs.append({
                'sub_run_name': f'{TAG}_{k:04d}',
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


def _fmt_hms(minutes):
    h = int(minutes // 60)
    m = int(round(minutes - 60 * h))
    return f'{h} h {m:02d} min'


if __name__ == '__main__':
    c = Config()
    out = 'config/json_run_configs/run_config_stats_run67.json'
    c.write_to_file(out)

    n = len(c.sub_runs)
    data_min = sum(sr['run_time'] for sr in c.sub_runs)
    gb_per_subrun = 5.4 * 60 * SUBRUN_MIN / 1000.0     # 5.4 MB/s RAW n32 @ ~17.5 Hz
    print(f'=== {c.run_name} — FIXED-POINT statistics run (run_67 optimum), no scan ===')
    print(f'wrote       : {out}')
    print(f'DAQ readout : {N_SAMPLES} smp x {SAMPLE_PERIOD} ns, latency {LATENCY}, IPD {IPD}, '
          f'ZS={ZS}; PS delay 1800 (co-framed)')
    print(f'drift       : {DRIFT_V} V on A/B/C, det D {DRIFT_V - DRIFT_D_OFFSET} V '
          f'(offset -{DRIFT_D_OFFSET} V)')
    print(f'resist      : A{RESIST_V["A"]} / B{RESIST_V["B"]} / C{RESIST_V["C"]} / '
          f'D{RESIST_V["D"]} V (per-detector, from the run_67 calibration)')
    print(f'plastic     : 0.90 MIP (A-118/B-139/C-157/D-134 mV) via scan_control `{TAG}`')
    print(f'mesh        : OFF whole run ({TAG} -> SEC_B out2/out3 disabled; '
          f'SiPM enables out0/out1 untouched)')
    print(f'total       : {n} sub-runs x {SUBRUN_MIN:g} min = {_fmt_hms(data_min)} '
          f'(open-ended, stop-anywhere, operator-killed)')
    print(f'disk (RAW)  : ~{gb_per_subrun:.0f} GB/sub-run, ~{gb_per_subrun * 24 * 60 / SUBRUN_MIN:.0f} '
          f'GB/day — SSD->HDD->EOS pipeline MUST keep up')
    print(f'first 3 sub-runs:')
    for sr in c.sub_runs[:3]:
        print(f'  {sr["sub_run_name"]:16s} '
              f'resist A{sr["hvs"]["5"]["1"]}/B{sr["hvs"]["5"]["2"]}/C{sr["hvs"]["5"]["3"]}/'
              f'D{sr["hvs"]["5"]["4"]}  drift {sr["hvs"]["9"]["0"]} (D{sr["hvs"]["9"]["3"]})')
    print('\nPRE-RUN (wait for first real beam pulse — daq_control has NO beam-gating):')
    print('  .venv/bin/python n1081b/trigger_mode.py scint --singles --ps-pickup')
    print('  .venv/bin/python n1081b/set_ps_trigger_delay.py --delay 1800')
    print('  (plastic drops 1.41 -> 0.90 MIP and mesh stays OFF from sub-run 1, via scan_control)')
    print('Launch: ./start_run.sh run_config_stats_run67.json')
