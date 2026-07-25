#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_overnight_hv_stats.py — run_75, 2026-07-24.
OVERNIGHT statistics run, modelled on run_71 (run_config_mesh_onoff_hv_scan.py) but
with the mesh axis DROPPED: mesh charge-injection is OFF for the entire run, and the
resist ladder is raised to a common 520 -> 560 V on all four detectors.

WHAT'S DIFFERENT vs run_71
  1. NO MESH AXIS — mesh circuits OFF the whole run. run_71's own tracking analysis
     found NO significant mesh On/Off difference (Det A -0.1sigma, Det C +0.4sigma in
     1-10 ms tracks; memory run71-mesh-track-analysis), so there is no evidence the
     mesh helps. Every sub-run carries the `acmeshOff` tag, so scan_control re-asserts
     mesh OFF (SEC_B out2 = det A, out3 = det C DISABLED) each sub-run while leaving
     out0/out1 (the SiPM-enable bias) ON. Dropping the On/Off pair HALVES the sub-runs
     per HV point vs run_71 -> 2x statistics per point in the same wall time.
  2. COMMON, RAISED resist ladder. run_71 used per-detector 4-pt ladders centred on the
     run_67 optima (515-540). Here a single 5-pt ladder 520/530/540/550/560 V is applied
     to ALL FOUR detectors, including D (operator 2026-07-24). This leans deliberately
     toward the HIGH-GAIN / late-window / cosmic regime: the cosmics runs 72-74 show
     tracking efficiency rising MONOTONICALLY to 560 V with no roll-over, and the beam
     optimum moves up with time-since-flash (run_67: Det A 530 -> >=550 V for late
     windows). It sits ABOVE the beam EARLY-window optima (A/C saturate >540/535 on
     high-intensity pulses), which is the intended trade for a raw-statistics run.

  ⚠ det D: 550/560 V resist is well above every charted D ceiling (D "pins the ceiling",
     tripped its drift 07-23, beam-off stress ceiling 475 V) though D held 560 V fine
     beam-OFF in run_72. The operator explicitly chose all four equal to 560. D drift is
     already 50 V lower than A/B/C. Over an unattended night the HV trip/deviation
     Telegram alerts (1 Hz loop) are the safety net — the highest-stress point is
     resist 560 @ drift 700 (D 650). If D sparks repeatedly, re-run with DET_D_RES_OFFSET
     set (caps D's resist that many volts below A/B/C).

TRIGGER (routing set ONCE pre-run; identical to run_71):
  scint --singles --ps-pickup, PS + singles CO-FRAMED in 32 smp: latency 35, M4.D in0
  G&D delay 1800 ns pulls the flash to ~smp 13 beside the singles MM at ~smp 11 ->
  per-event time-since-flash. M4.C = or_veto(Singles, lemo0) gated by the N93B ~1->81 ms
  acceptance window; M4.D = OR(lemo0 = PS/gamma-flash, lemo1 = C-out). 32 smp x 60 ns
  (1.92 us), IPD 5, RAW (zero_suppress=False). RdClk left at the current 25 MHz default.
  Plastic threshold is NOT scanned — held CONSTANT at the run_71 beam set 1.41 MIP
  (M2/.241 thrA-D = -185/-217/-245/-210). NOTE: the `acmeshOff` scan tag bundles BOTH
  mesh_ac{output_status=False} (SEC_B out2/out3 off) AND those 1.41 MIP thresholds, so
  scan_control re-asserts them together each sub-run. Same end state as "held", just
  actively re-applied. Walls (M1) unchanged at 0.5 MIP (external, not in the tag).

HV GRID (all inside already-exercised territory; run_64 to resist 570, run_57/58 to 580):
    drift  : 700, 600 V for A/B/C; det D = drift - 50 V (650, 550).
    resist : 5 pts, 520/530/540/550/560 V, COMMON to all four detectors (card 5 ch1-4).
    mesh   : OFF for the whole run (no axis).

ORDERING — cycle OUTER -> drift -> resist-index:
    for cycle:                       # whole grid repeats until manually killed
      for drift in [700, 600]:       # fewest HV transitions
        for ri in [0..4]:            # resist ladder index 520->560
          acmeshOff (15 min)
  10 sub-runs/cycle x 15 min = 2.5 h/cycle. N_CYCLES=6 -> 60 sub-runs = 15 h of grid
  (covers a night with headroom for beam gaps). Stop-anywhere / operator-killed; every
  sub-run is self-contained, so killing it at any point leaves a balanced grid.

DISK — RAW at IPD 5 is the binding constraint overnight. run_67 measured ~15.6 GB HDD /
  ~9 GB SSD-staging per 15 min sub-run at ~95 ev/spill; continuous RAW fills the ~1.4 TB
  HDD in ~25-73 h. A full night REQUIRES active SSD->HDD->EOS clearing (backup_watcher /
  space_manager, standing practice) — confirm both are running before launch. If disk
  gets tight, kill early (stop-anywhere) or re-run ZS via DREAM_ZS=1.

SCINT PMT HV: plastics (card 07) + liquids (card 08) held at the 07-19 Y88 equalized
  setpoints (merged into every sub-run, inherited from run_config_beam).

PRE-RUN (boards free; beam ON — daq_control has NO beam-gating, so wait for the first real
  pulse before launching or the empty sub-runs get marked complete and punch grid gaps):
  .venv/bin/python n1081b/trigger_mode.py scint --singles --ps-pickup
  .venv/bin/python n1081b/set_ps_trigger_delay.py --delay 1800
  verify: trigger_mode.py status          -> C or_veto lemos=[0], D lemos=[0,1]
          set_ps_trigger_delay.py --show   -> delay 1800, enable_gd True, invert False
  # Mesh legs: confirm A->out2 / C->out3 and out0/out1 (SiPM bias) ENABLED before launch:
  #   n1081b/inspect_m6_sections.py   (read-only)
  # The first sub-run (acmeshOff) then re-asserts out2/out3 OFF.
Launch: ./start_run.sh run_config_overnight_hv_stats.json
RESTORE / TEARDOWN: restore='snapshot' returns SEC_B out2/out3 to their found state on
  exit; scan_control restores any modulated channels. Trigger stays scint+ps (leave for
  the next beam run, or re-select as needed).
"""
import os

# --- repo-root shim (run_config_beam/base live one dir up) ---
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from run_config_beam import Config as BeamConfig

RUN_NUM = 75

# ---- DREAM readout (PS+singles co-framed in 32 smp -- run_56/67 recipe, IPD 5) ----
LATENCY = 35
N_SAMPLES = 32
IPD = 5
SAMPLE_PERIOD = 60
ZS = os.environ.get('DREAM_ZS', '0') == '1'   # default RAW (matches run_71); ZS opt-in

# ---- dwell + repeats ----
SUBRUN_MIN = float(os.environ.get('SUBRUN_MIN', '15'))     # 15 min per sub-run (operator)
N_CYCLES = int(os.environ.get('N_CYCLES', '6'))            # 10 sr x 15 min = 2.5 h/cycle -> 15 h

# ---- HV axes ----
# Drift common to A/B/C (card 9 ch0-2); det D (ch3) is 50 V lower (D tripped recently).
DRIFT_LADDER = [700, 600]
DRIFT_D_OFFSET = 50
# Common resist ladder (card 5 ch1-4 = A/B/C/D), all four detectors equal, raised to the
# high-gain end (operator 2026-07-24). DET_D_RES_OFFSET caps det D that many volts below
# A/B/C if D needs protecting (default 0 = all equal, as chosen).
RESIST_LADDER = [520, 530, 540, 550, 560]
DET_D_RES_OFFSET = int(os.environ.get('DET_D_RES_OFFSET', '0'))
N_RESIST = len(RESIST_LADDER)

# Mesh OFF the whole run: every sub-run carries this tag so scan_control re-asserts
# SEC_B out2/out3 disabled (SiPM enables out0/out1 untouched). NOTE the acmeshOff tag in
# config/n1081b_scan_schedule.json ALSO re-asserts the M2 plastic thresholds at the run_71
# beam set 1.41 MIP (thrA-D = -185/-217/-245/-210) — i.e. it holds the beam plastic
# threshold constant, which is exactly what a run_71-style beam run wants.
MESH_TAG = 'acmeshOff'


class Config(BeamConfig):
    def _set_defaults(self, config_path=None):
        super()._set_defaults(config_path)

        self.run_name = f'run_{RUN_NUM}'
        self.run_out_dir = f'{self.data_out_dir}{self.run_name}/'
        self.dream_daq_info['run_directory'] = f'/home/mx17/july_dream/dream_run/{self.run_name}/'
        self.dream_daq_info['data_out_dir'] = f'{self.run_out_dir}'
        self.processor_info['run_dir'] = f'{self.run_out_dir}'
        self.hv_info['run_out_dir'] = self.run_out_dir
        self.resume = True
        # scan_control re-asserts mesh OFF (acmeshOff -> mesh_ac output_status=False,
        # SEC_B out2/out3 ONLY) each sub-run. scint+ps routing + PS delay 1800 are set
        # ONCE pre-run. No HV/threshold modulation via scan_control.
        self.n1081b_scan = 'on'

        readout_txt = ('ZS (Tcm_..._ZS.cfg)' if ZS else 'RAW / full readout (zero_suppress=False)')
        self.trigger = (
            f'OVERNIGHT statistics run, PS + SINGLES trigger, per-HV scan with MESH OFF '
            f'({self.run_name}, modelled on run_71 minus the mesh axis). scint --singles '
            f'--ps-pickup, PS+singles co-framed in 32 smp (latency 35, M4.D in0 G&D 1800 ns '
            f'-> flash ~smp 13, MM ~smp 11); M4.C = or_veto(Singles, lemo0) gated by the '
            f'N93B ~1->81 ms window; M4.D = OR(lemo0 = PS/gamma-flash, lemo1 = C-out). '
            f'32 smp x 60 ns, IPD 5, {readout_txt}. HV: drift {DRIFT_LADDER} (det D -50 V), '
            f'COMMON resist ladder {RESIST_LADDER} V on all four detectors '
            f'(det D resist offset -{DET_D_RES_OFFSET} V). Mesh charge-injection OFF the '
            f'whole run (SEC_B out2/out3 disabled via scan_control acmeshOff each sub-run; '
            f'SiPM-enable bias out0/out1 left ON); no mesh On/Off axis (run_71 found no '
            f'significant mesh effect). 15 min sub-runs, grid repeats {N_CYCLES}x overnight '
            f'(operator-killed). Walls (M1) 0.5 MIP; plastic threshold held CONSTANT at '
            f'1.41 MIP (A-185/B-217/C-245/D-210), re-asserted by the acmeshOff tag (not scanned). '
            f'Scint PMT bias at 07-19 Y88 setpoints. Ar/Iso 90/10, 3He, no Pb.')

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

        def _drift(dv):
            # card 9 ch0-3 = drift A/B/C/D; det D 50 V lower.
            return {'0': dv, '1': dv, '2': dv, '3': dv - DRIFT_D_OFFSET}

        def _resist(ri):
            # card 5 ch1-4 = resist A/B/C/D; common ladder, det D optionally capped lower.
            v = RESIST_LADDER[ri]
            return {'1': v, '2': v, '3': v, '4': v - DET_D_RES_OFFSET}

        self.sub_runs = []
        k = 0
        for _cyc in range(N_CYCLES):
            for dv in DRIFT_LADDER:            # outer: fewest HV transitions
                for ri in range(N_RESIST):     # resist ladder index 520->560
                    # tag (leading '_'-token) keys scan_control -> acmeshOff (mesh OFF).
                    self.sub_runs.append({
                        'sub_run_name': f'{MESH_TAG}_dr{dv:g}_ri{ri}_{k:04d}',
                        'run_time': SUBRUN_MIN, 'post_pause_s': 0,
                        'inter_packet_delay': IPD,
                        'latency': LATENCY, 'n_samples_per_waveform': N_SAMPLES,
                        'hvs': {'5': _resist(ri), '9': _drift(dv)},
                    })
                    k += 1

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
    out = 'config/json_run_configs/run_config_overnight_hv_stats.json'
    c.write_to_file(out)

    n = len(c.sub_runs)
    data_min = sum(sr['run_time'] for sr in c.sub_runs)
    per_cycle = len(DRIFT_LADDER) * N_RESIST
    print(f'=== {c.run_name} — OVERNIGHT PS+singles resist x drift scan, MESH OFF ===')
    print(f'wrote       : {out}')
    print(f'DAQ readout : {N_SAMPLES} smp x {SAMPLE_PERIOD} ns, latency {LATENCY}, IPD {IPD}, '
          f'ZS={ZS}; PS delay 1800 (co-framed)')
    print(f'drift       : {DRIFT_LADDER} V (A/B/C); det D = drift - {DRIFT_D_OFFSET} V '
          f'({[d - DRIFT_D_OFFSET for d in DRIFT_LADDER]})')
    print(f'resist      : {RESIST_LADDER} V, COMMON to all four dets '
          f'(det D offset -{DET_D_RES_OFFSET} V)')
    print(f'mesh        : OFF whole run (acmeshOff -> SEC_B out2/out3 disabled; '
          f'SiPM enables out0/out1 untouched)')
    print(f'order       : cycle -> drift -> resist-index')
    print(f'cycle       : {per_cycle} sub-runs x {SUBRUN_MIN:g} min = '
          f'{_fmt_hms(per_cycle * SUBRUN_MIN)}/cycle')
    print(f'total       : {N_CYCLES} cycles = {n} sub-runs x {SUBRUN_MIN:g} min = '
          f'{_fmt_hms(data_min)} of grid (stop-anywhere; operator-killed)')
    print(f'first 5 sub-runs:')
    for sr in c.sub_runs[:5]:
        print(f'  {sr["sub_run_name"]:24s} '
              f'resist A{sr["hvs"]["5"]["1"]}/B{sr["hvs"]["5"]["2"]}/C{sr["hvs"]["5"]["3"]}/'
              f'D{sr["hvs"]["5"]["4"]}  drift {sr["hvs"]["9"]["0"]} (D{sr["hvs"]["9"]["3"]})')
    print('\nPRE-RUN (wait for first real beam pulse — daq_control has NO beam-gating):')
    print('  .venv/bin/python n1081b/trigger_mode.py scint --singles --ps-pickup')
    print('  .venv/bin/python n1081b/set_ps_trigger_delay.py --delay 1800')
    print('  (plastic threshold held at standing set; mesh auto-OFF from sub-run 1)')
    print('Launch: ./start_run.sh run_config_overnight_hv_stats.json')
