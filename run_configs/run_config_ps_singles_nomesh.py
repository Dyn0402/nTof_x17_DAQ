#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_ps_singles_nomesh.py — PS + SINGLES trigger, MESH OFF, small 2D drift x
resist HV micro-scan cycled for 16 h (run_66, 2026-07-21/22). Auto-started by the
trigger-scan/run_65 completion watcher.

TRIGGER (set ONCE pre-run by the handoff, n1081b_scan='off' -> not modulated):
  scint --singles --ps-pickup:
    M4.C = or_veto(Singles = M4.A out, lemo0) gated by the ~1->81 ms N93B window (start moved 5->1 ms on 07-22);
    M4.D = OR(lemo0 = PS/gamma-flash, lemo1 = C-out).
  PS + singles CO-FRAMED in the 32-sample window (run_56 recipe): at latency 35 the
  flash peak would sit at sample lat+8 = 43 (off a 32-smp window); the M4.D in0 G&D
  delay = 1800 ns pulls it to ~13, next to the singles MM at lat-24 = ~11. So every
  event carries BOTH the PS pickup and the gated singles -> time-since-flash per event.
  32 smp x 60 ns (1.92 us), latency 35, IPD 90, RAW (zero_suppress=False).
  NOTE: with flash+MM at samples ~11-13 the drift column after them is ~19 smp
  (~1.14 us), so at 400 V drift the far drift tail clips a little (accepted).

MESH: OFF for this whole run. The handoff runs `set_mesh_injection.py off` (M6.B
  outputs 0-3 disabled). *** TO RE-ENABLE for a future mesh run:
  `.venv/bin/python n1081b/set_mesh_injection.py on` (in0 G&D delay 1260 ns is
  preserved; see memory mesh-off-run66). *** n1081b_scan stays 'off'.

HV SCAN (small 2D micro-scan, CYCLED):
  resist A/B/C/D: 530, 525, 520, 515 V  (-5 V, 4 points; det D = A/B/C this run)
  drift  A/B/C/D: 600, 500, 400 V       (-100 V, 3 points, common to all four)
  -> 4 x 3 = 12-point grid per cycle, drift OUTER / resist INNER.
  10-min sub-runs; the 12-point cycle is REPEATED 8 times = 96 sub-runs = 16.0 h data
  (~17.6 h wall). Stop-anywhere / manual-stop friendly (each cycle is a complete grid).

SCINT DISCRIMINATOR THRESHOLDS (N1081B, set pre-run by the handoff, NOT in this
  config -- they are board settings on M1/M2, applied while still in flash_random):
    walls  (M1): 0.5 MIP = A:25 B:35 C:34 D:36 mV (Y88 half-MIP)
    plastic (M2): 1.0 MIP = A:-131 B:-155 C:-174 D:-149 mV
       (per-arm avg of mip_peak_mV from mip_thresholds_y88.json; D includes the
        now-REPAIRED D1 bar -> (132+166)/2 = 149; the 0.5-MIP set used D_R only.)

SCINT PMT HV: plastics (card 07) + liquids (card 08) held at the 07-19 Y88 equalized
  setpoints (merged into every sub-run, inherited from run_config_beam).

PRE-RUN handoff (run automatically once run_65 + the trigger scan both finish; boards
  are still in flash_random then, which threshold_ladder --apply requires):
  # thresholds FIRST (must be in a safe/flash mode):
  n1081b/threshold_ladder.py --apply-wall "A:25,B:35,C:34,D:36" \
                             --apply-plastic "A:-131,B:-155,C:-174,D:-149"
  n1081b/set_mesh_injection.py off
  n1081b/trigger_mode.py scint --singles --ps-pickup
  n1081b/set_ps_trigger_delay.py --delay 1800
  verify: trigger_mode.py status (expect C or_veto lemos=[0], D lemos=[0,1]);
          set_ps_trigger_delay.py --show (delay 1800, enable_gd True);
          threshold read-back.
Launch: ./start_run.sh run_config_ps_singles_nomesh.json
"""
import os
# --- repo-root shim (run_config_beam/base live one dir up) ---
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from run_config_beam import Config as BeamConfig

RUN_NUM = 66

# ---- DREAM readout (PS+singles co-framed in 32 smp -- run_56 recipe) ----
LATENCY = 35          # flash peak lat+8=43 pulled to ~13 by the 1800 ns PS delay; MM=lat-24=11
N_SAMPLES = 32
IPD = 90
SAMPLE_PERIOD = 60

# ---- per-sub-run dwell (minutes) ----
SUBRUN_MIN = float(os.environ.get('SUBRUN_MIN', '10'))

# ---- HV micro-scan ----
DET_D_OFFSET = 0                    # det D = A/B/C (515-530 V is safe for D)
RESIST_LADDER = [530, 525, 520, 515]   # V, -5 V, det A/B/C/D (card 5 ch 1-4)
DRIFT_LADDER = [600, 500, 400]         # V, -100 V, common to all four (card 9 ch 0-3)
N_CYCLES = int(os.environ.get('N_CYCLES', '8'))   # 8 x 12 x 10 min = 16.0 h data


def fmt_v(v):
    return f'{v:g}'


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
        # Static PS+singles trigger + MESH OFF, set ONCE pre-run by the handoff.
        # Nothing modulated per sub-run.
        self.n1081b_scan = 'off'

        self.trigger = (
            f'PS + SINGLES trigger, MESH OFF, 2D drift x resist micro-scan cycled 16 h '
            f'({self.run_name}). scint --singles --ps-pickup: M4.C = or_veto(Singles, lemo0) '
            'gated by the ~1-81 ms N93B window (start moved 5->1 ms on 07-22); M4.D = '
            'OR(lemo0 = PS/gamma-'
            'flash, lemo1 = '
            'C-out). PS + singles CO-FRAMED in 32 smp (run_56 recipe): latency 35, M4.D in0 '
            'G&D delay 1800 ns pulls the flash from sample 43 to ~13, beside the singles MM '
            'at lat-24 ~11 -> per-event time-since-flash. 32 smp x 60 ns (1.92 us), IPD 90, '
            'RAW (zero_suppress=False). MESH OFF (M6.B outputs disabled). HV: resist '
            '530/525/520/515 V (det D = A/B/C) x drift 600/500/400 V, 12-pt grid x 8 cycles '
            'x 10 min = 96 sub-runs (16 h data). Thresholds: walls 0.5 MIP (25/35/34/36), '
            'plastics 1.0 MIP (A:-131 B:-155 C:-174 D:-149, D1 repaired). Scint PMT bias at '
            '07-19 Y88 equalized setpoints. Ar/Iso 90/10, 3He, no Pb.')

        # RAW / full readout, 32 smp window (co-framed PS+singles).
        self.dream_daq_info.update({
            'daq_config_template_path': f'{self.base_out_dir}dream_config/Tcm_Mx17_July.cfg',
            'zero_suppress': False,
            'latency': LATENCY,
            'n_samples_per_waveform': N_SAMPLES,
            'sample_period': SAMPLE_PERIOD,
            'inter_packet_delay': IPD,
        })

        # ----- sub-run build: cycle OUTER -> drift -> resist INNER -----
        def _drift(dv):
            return {'0': dv, '1': dv, '2': dv, '3': dv}             # card 9 ch 0-3 = drift A/B/C/D

        def _resist(v):
            return {'1': v, '2': v, '3': v, '4': v - DET_D_OFFSET}  # card 5 ch 1-4 = det A/B/C/D

        self.sub_runs = []
        k = 0
        for cyc in range(N_CYCLES):
            for dv in DRIFT_LADDER:
                for v in RESIST_LADDER:
                    self.sub_runs.append({
                        'sub_run_name': f'sngPSnomesh_dr{fmt_v(dv)}_r{fmt_v(v)}_c{cyc}_{k:03d}',
                        'run_time': SUBRUN_MIN, 'post_pause_s': 0,
                        'hvs': {'5': _resist(v), '9': _drift(dv)},
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


if __name__ == '__main__':
    c = Config()
    c.write_to_file('config/json_run_configs/run_config_ps_singles_nomesh.json')

    n = len(c.sub_runs)
    data_min = sum(sr['run_time'] for sr in c.sub_runs)
    wall_min = data_min + n * 1.0
    print(f'=== {c.run_name} — PS+singles, MESH OFF, 2D micro-scan cycled ===')
    print(f'DAQ readout : {N_SAMPLES} smp x {SAMPLE_PERIOD} ns = {N_SAMPLES*SAMPLE_PERIOD} ns, '
          f'latency {LATENCY}, IPD {IPD}, ZS=False; PS delay 1800 ns (co-framed)')
    print(f'grid        : resist {RESIST_LADDER} x drift {DRIFT_LADDER} (det D = A/B/C)')
    print(f'cycles      : {N_CYCLES} x {len(DRIFT_LADDER)*len(RESIST_LADDER)} pts x {SUBRUN_MIN:g} min '
          f'= {n} sub-runs = {data_min/60:.1f} h data (~{wall_min/60:.1f} h wall)')
    print('first 5 sub-runs:')
    for sr in c.sub_runs[:5]:
        print(f'  {sr["sub_run_name"]:30s} resist {sr["hvs"]["5"]}  drift {sr["hvs"]["9"]["0"]}')
