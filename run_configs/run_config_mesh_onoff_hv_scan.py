#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_mesh_onoff_hv_scan.py — run_71, 2026-07-23.
PS + SINGLES trigger, RAW at IPD 5. Tighter, higher-statistics successor to run_67:
per-detector optimal resist ladders x two drifts x a REAL mesh-circuit ON/OFF axis,
the whole grid repeating for >=24 h (operator kills it manually).

WHAT'S NEW vs run_67
  1. PER-DETECTOR resist ladders. run_67 stepped one resist common to A/B/C; here each
     Micromegas gets its own 4-point 5 V ladder centred on its measured optimum from
     calibrations/mm/resist_hv_run67.json (run_67 resist-zoom). All four chambers step
     together by ladder INDEX, so index i applies A[i]/B[i]/C[i]/D[i] simultaneously.
  2. A REAL mesh ON/OFF axis. After the 2026-07-23 M6 re-cable the mesh charge-injection
     legs sit on SEC_B Out 3 (det A, SDK out2) and Out 4 (det C, SDK out3); Out 1/2 carry
     only the SiPM-enable bias. So mesh ON/OFF is now toggled on SEC_B out2/out3 ONLY,
     leaving the SiPM enables (out0/out1) untouched — this is the fix for the SEC_C-out
     cross-talk that made mesh-off collapse the SiPM walls 28x in run_67 (which is why
     run_67 had NO mesh axis and used B/D as the control instead). ⚠ Whether that coupling
     is truly broken is the THING THIS RUN TESTS — see MESH below.
  3. det D drift 50 V LOWER than A/B/C (D tripped recently, operator 2026-07-23).

TRIGGER (routing set ONCE pre-run; scan_control only toggles the mesh legs per sub-run):
  scint --singles --ps-pickup, PS + singles CO-FRAMED in 32 smp (run_56/67 recipe):
  latency 35, M4.D in0 G&D delay 1800 ns pulls the flash to ~smp 13 beside the singles MM
  at lat-24 ~smp 11 -> per-event time-since-flash. M4.C = or_veto(Singles, lemo0) gated by
  the N93B ~1->81 ms acceptance window; M4.D = OR(lemo0 = PS/gamma-flash, lemo1 = C-out).
  32 smp x 60 ns (1.92 us), IPD 5, RAW (zero_suppress=False). DREAM read clock left at the
  current 25 MHz default (RdClk 4.0). Plastic threshold is NOT scanned and NOT touched by
  scan_control — held at its standing set (set once pre-run; see PRE-RUN).

HV GRID (all inside already-exercised territory; run_64 scanned drift 700->200 / resist to
  570, run_57/58 to 580 in this same 90/10 gas):
    drift  : 700, 600 V for A/B/C; det D = drift - 50 V (650, 550).
    resist : 4 pts, 5 V apart, PER DETECTOR (card 5 ch1-4 = A/B/C/D), from run_67 zoom:
               A  515 520 525 530   (opt 525; range 515-535, saturates >540)
               B  525 530 535 540   (opt 535; noisy M1, never saturates)
               C  515 520 525 530   (opt 525; saturates sharply >535)
               D  515 520 525 530   (opt 525 @ 20-30 ms; D is a LATE detector, dead 2-8 ms)
             Ladders centred on each chamber's early-window optimum (highest resist still
             ALIVE); index steps all four together. Sources + caveats in the JSON provenance.
    mesh   : ON then OFF at every HV point (adjacent same-beam / same-HV pair).

ORDERING — cycle OUTER -> drift -> resist-index -> mesh INNER:
    for cycle:                       # whole grid repeats until manually killed
      for drift in [700, 600]:       # fewest HV transitions
        for ri in [0,1,2,3]:         # per-detector resist ladder index
          acmeshOn  (15 min)         # mesh charge-injection ON  (SEC_B out2/out3)
          acmeshOff (15 min)         # mesh charge-injection OFF
  16 sub-runs/cycle x 15 min = 4.0 h/cycle. N_CYCLES=8 -> 128 sub-runs = 32 h wall of grid
  (>=24 h as requested, with headroom for beam gaps). The run is stop-anywhere: killing it
  after any completed mesh On/Off pair leaves a balanced grid. mesh INNERMOST so every
  On/Off contrast is at identical HV, same beam, minutes apart.

MESH — this run is the TEST of the re-cabled ON/OFF axis (memory m6-secBC-control-aliasing):
  scan_control toggles ONLY SEC_B out2 (det A) + out3 (det C) via the new `mesh_ac` target
  / `acmesh{On,Off}` tags in config/n1081b_scan_schedule.json. It NEVER touches SEC_B
  out0/out1 (the SiPM-enable bias) — that is the whole point of using out2/out3. If the
  07-22 wall collapse really was SEC_C-out cross-talk, mesh-OFF sub-runs now keep FULL SiPM
  wall gain and the singles rate should hold; if the walls still collapse on mesh-OFF, the
  coupling is elsewhere. Either way the mesh (A,C) vs the uninjected B/D remains an in-run
  control. First-cycle acmeshOn/acmeshOff DREAM rates + n_TOF wall-flash amplitude settle it.

DISK — RAW at IPD 5 is the binding constraint over 24 h+. run_67 measured ~10.4 GB/HDD and
  ~6 GB SSD-staging per 10-min sub-run at ~95 ev/spill; at 15 min that is ~15.6 GB HDD /
  ~9 GB SSD per sub-run, and continuous RAW fills the ~1.4 TB HDD in ~25-73 h. So a 24 h+
  run REQUIRES active SSD->HDD->EOS clearing (space_manager.py / backup_watcher, standing
  practice) and will get tight past ~1 day. If disk is a concern, run ZS instead:
  `DREAM_ZS=1 .venv/bin/python run_config_mesh_onoff_hv_scan.py` (swaps in Tcm_..._ZS.cfg).

SCINT PMT HV: plastics (card 07) + liquids (card 08) held at the 07-19 Y88 equalized
  setpoints (merged into every sub-run, inherited from run_config_beam).

PRE-RUN (boards must be free; scan_control only drives SEC_B out2/out3 per sub-run):
  .venv/bin/python n1081b/trigger_mode.py scint --singles --ps-pickup
  .venv/bin/python n1081b/set_ps_trigger_delay.py --delay 1800
  # Plastic threshold: leave at the standing set, or set it explicitly to your fixed value
  # (this run holds it constant — no threshold axis). Walls (M1) held at 0.5 MIP.
  verify: trigger_mode.py status          -> C or_veto lemos=[0], D lemos=[0,1]
          set_ps_trigger_delay.py --show   -> delay 1800, enable_gd True, invert False
  # Mesh: do NOT pre-set — the first sub-run (acmeshOn) enables SEC_B out2/out3. Confirm the
  # legs are cabled A->out2 / C->out3 and out0/out1 are ENABLED (SiPM bias) before launch:
  #   n1081b/inspect_m6_sections.py   (read-only)
Launch: ./start_run.sh run_config_mesh_onoff_hv_scan.json
TEARDOWN: restore='snapshot' returns SEC_B out2/out3 to their found state on exit.
"""
import os

# --- repo-root shim (run_config_beam/base live one dir up) ---
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from run_config_beam import Config as BeamConfig

RUN_NUM = 71

# ---- DREAM readout (PS+singles co-framed in 32 smp -- run_56/67 recipe, IPD 5) ----
LATENCY = 35
N_SAMPLES = 32
IPD = 5
SAMPLE_PERIOD = 60
ZS = os.environ.get('DREAM_ZS', '0') == '1'   # default RAW (matches run_67); ZS opt-in

# ---- dwell + repeats ----
SUBRUN_MIN = float(os.environ.get('SUBRUN_MIN', '15'))     # 15 min per sub-run (operator)
N_CYCLES = int(os.environ.get('N_CYCLES', '8'))            # 16 sr x 15 min = 4 h/cycle -> 32 h

# ---- HV axes ----
# Drift common to A/B/C (card 9 ch0-2); det D (ch3) is 50 V lower (D tripped recently).
DRIFT_LADDER = [700, 600]
DRIFT_D_OFFSET = 50
# Per-detector resist ladders (card 5 ch1-4 = A/B/C/D): 4 points, 5 V apart, centred on
# each chamber's early-window optimum from calibrations/mm/resist_hv_run67.json.
RESIST_LADDERS = {
    'A': [515, 520, 525, 530],   # opt 525; range 515-535, saturates >540
    'B': [525, 530, 535, 540],   # opt 535; noisy M1, never saturates
    'C': [515, 520, 525, 530],   # opt 525; saturates sharply >535
    'D': [515, 520, 525, 530],   # opt 525 @ 20-30 ms; LATE detector (dead 2-8 ms)
}
N_RESIST = len(RESIST_LADDERS['A'])
MESH_STATES = ['On', 'Off']      # acmesh{On,Off}: SEC_B out2 (det A) + out3 (det C) ONLY


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
        # Per-sub-run mesh ON/OFF via scan_control (acmesh{On,Off} tags -> mesh_ac target =
        # SEC_B out2/out3 ONLY). scint+ps routing + PS delay 1800 are set ONCE pre-run.
        self.n1081b_scan = 'on'

        readout_txt = ('ZS (Tcm_..._ZS.cfg)' if ZS else 'RAW / full readout (zero_suppress=False)')
        self.trigger = (
            f'PS + SINGLES trigger, per-detector resist x drift HV scan with a REAL mesh '
            f'circuit ON/OFF axis ({self.run_name}). scint --singles --ps-pickup, PS+singles '
            f'co-framed in 32 smp (latency 35, M4.D in0 G&D 1800 ns -> flash ~smp 13, MM '
            f'~smp 11); M4.C = or_veto(Singles, lemo0) gated by the N93B ~1->81 ms window; '
            f'M4.D = OR(lemo0 = PS/gamma-flash, lemo1 = C-out). 32 smp x 60 ns, IPD 5, '
            f'{readout_txt}. HV: drift {DRIFT_LADDER} (det D -50 V), per-detector resist '
            f'ladders A{RESIST_LADDERS["A"]} B{RESIST_LADDERS["B"]} C{RESIST_LADDERS["C"]} '
            f'D{RESIST_LADDERS["D"]} (4 pts x 5 V, centred on each run_67 optimum). Mesh '
            f'charge-injection toggled ON/OFF per sub-run on SEC_B out2 (det A) + out3 '
            f'(det C) ONLY (SiPM-enable bias out0/out1 left ON) via scan_control '
            f'acmesh{{On,Off}}; 15 min sub-runs, mesh On then Off at each HV point, grid '
            f'repeats {N_CYCLES}x (>=24 h, operator-killed). Walls (M1) 0.5 MIP; plastic '
            f'threshold held (not scanned). Scint PMT bias at 07-19 Y88 setpoints. '
            f'Ar/Iso 90/10, 3He, no Pb.')

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
            # card 5 ch1-4 = resist A/B/C/D; each chamber steps its own ladder by index.
            return {'1': RESIST_LADDERS['A'][ri], '2': RESIST_LADDERS['B'][ri],
                    '3': RESIST_LADDERS['C'][ri], '4': RESIST_LADDERS['D'][ri]}

        self.sub_runs = []
        k = 0
        for _cyc in range(N_CYCLES):
            for dv in DRIFT_LADDER:            # outer: fewest HV transitions
                for ri in range(N_RESIST):     # per-detector resist ladder index
                    for mo in MESH_STATES:     # inner: adjacent same-HV mesh On/Off pair
                        # tag (leading '_'-token) keys scan_control -> acmesh{On,Off}.
                        self.sub_runs.append({
                            'sub_run_name': f'acmesh{mo}_dr{dv:g}_ri{ri}_{k:04d}',
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
    out = 'config/json_run_configs/run_config_mesh_onoff_hv_scan.json'
    c.write_to_file(out)

    n = len(c.sub_runs)
    data_min = sum(sr['run_time'] for sr in c.sub_runs)
    per_cycle = len(DRIFT_LADDER) * N_RESIST * len(MESH_STATES)
    print(f'=== {c.run_name} — PS+singles, per-detector resist x drift x MESH ON/OFF scan ===')
    print(f'wrote       : {out}')
    print(f'DAQ readout : {N_SAMPLES} smp x {SAMPLE_PERIOD} ns, latency {LATENCY}, IPD {IPD}, '
          f'ZS={ZS}; PS delay 1800 (co-framed)')
    print(f'drift       : {DRIFT_LADDER} V (A/B/C); det D = drift - {DRIFT_D_OFFSET} V '
          f'({[d - DRIFT_D_OFFSET for d in DRIFT_LADDER]})')
    print(f'resist      : per-detector 4-pt ladders (5 V), centred on run_67 optima:')
    for det in ('A', 'B', 'C', 'D'):
        print(f'                {det}: {RESIST_LADDERS[det]}')
    print(f'mesh        : acmesh{{On,Off}} = SEC_B out2 (det A) + out3 (det C) ONLY '
          f'(SiPM enables out0/out1 untouched)')
    print(f'order       : cycle -> drift -> resist-index -> mesh(On,Off)')
    print(f'cycle       : {per_cycle} sub-runs x {SUBRUN_MIN:g} min = '
          f'{_fmt_hms(per_cycle * SUBRUN_MIN)}/cycle')
    print(f'total       : {N_CYCLES} cycles = {n} sub-runs x {SUBRUN_MIN:g} min = '
          f'{_fmt_hms(data_min)} of grid (stop-anywhere; operator-killed)')
    print(f'first 4 sub-runs:')
    for sr in c.sub_runs[:4]:
        print(f'  {sr["sub_run_name"]:26s} '
              f'resist A{sr["hvs"]["5"]["1"]}/B{sr["hvs"]["5"]["2"]}/C{sr["hvs"]["5"]["3"]}/'
              f'D{sr["hvs"]["5"]["4"]}  drift {sr["hvs"]["9"]["0"]} (D{sr["hvs"]["9"]["3"]})')
    print('\nPRE-RUN:')
    print('  .venv/bin/python n1081b/trigger_mode.py scint --singles --ps-pickup')
    print('  .venv/bin/python n1081b/set_ps_trigger_delay.py --delay 1800')
    print('  (plastic threshold held at standing set; mesh auto-driven from sub-run 1)')
    print('Launch: ./start_run.sh run_config_mesh_onoff_hv_scan.json')
