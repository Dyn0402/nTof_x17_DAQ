#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_cosmics_resist_scan.py — BEAM-OFF cosmic scint-Singles resist ladder (run_68,
2026-07-23). Combines the UNGATED cosmics trigger of run_config_cosmics_singles.py (run_59)
with a cycled resist-voltage scan, to fill the beam-off gap with a gain-vs-resist cosmic
calibration.

TRIGGER — scint SINGLES, veto OPEN, NO PS / NO readout gate:
  Beam is off, so there are no PS pulses and the N93B 30 ms veto window never opens; a normal
  veto-gated scint trigger would be vetoed ~100 %. We OPEN the veto by making M4.C a *plain*
  OR on the Singles line (lemo0), veto input inert — exactly `setup_cosmics_singles_ungated.py`
  (M4.C = plain FN_OR lemo0; M4.D = OR lemo1 = C-out; flash line off). NO --ps-pickup, NO
  or_veto gate. Every trigger is an ungated scintillator single.

DREAM — RAW full readout (base template is RAW), 32 smp x 60 ns = 1920 ns, latency 35
  (scint MM ~smp 11), no ZS, no pedestal subtraction — same as run_59 cosmics so frames match.

HV SCAN — resist ladder, cycled:
  Drift (card 9 ch 0-3, all 4 dets): 600 V, fixed (was 700 on the 2026-07-23 first attempt;
    det D drift sparked/tripped at 700 -> aborted with no data, see DRIFT note below).
  Resist (card 5 ch 1-4): A/B/C 570 -> 520 in -10 V steps (6 pts); det D = A/B/C - 10 V at
    every point (det D spark-sensitive; operator choice 2026-07-23).
  Ladder repeated CYCLES times (default 12) => 6 x 12 = 72 sub-runs x 10 min = 12.0 h data
    (~13.2 h wall with ~1 min/sub-run HV-ramp overhead). Stop when beam returns / at 12 h.

SCINT PMT HV: plastics (card 07) + liquids (card 08) held at the 07-19 Y88 equalized
  setpoints (merged into every sub-run, inherited from run_config_beam).

PRE-RUN (once, boards free — check config/n1081b_access/ first):
  .venv/bin/python n1081b/setup_cosmics_singles_ungated.py   # M4.C = plain OR(Singles), veto OPEN
  verify: .venv/bin/python n1081b/trigger_mode.py status     # expect C plain-OR lemos=[0], D lemos=[1]
Launch:
  ./start_run.sh run_config_cosmics_resist_scan.json
RESTORE when beam returns:
  .venv/bin/python n1081b/trigger_mode.py scint --doubles    # re-arms the 30 ms N93B veto gate
"""
import os
# --- repo-root shim (run_config_beam/base live one dir up) ---
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from run_config_beam import Config as BeamConfig

RUN_NUM = 68

# ---- DREAM readout (RAW cosmic singles, same window as run_59) ----
LATENCY, N_SAMPLES, SAMPLE_PERIOD = 35, 32, 60

# ---- per-sub-run dwell (minutes) and number of ladder repeats ----
SUBRUN_MIN = float(os.environ.get('SUBRUN_MIN', '10'))
CYCLES = int(os.environ.get('CYCLES', '12'))     # 6 resist pts x 12 = 72 sub-runs = 12.0 h data

# ---- HV ladders ----
DRIFT = 600                                       # card 9 ch 0-3, all dets, fixed
# 2026-07-23: first launch used 700 V — det D drift (9:3) REACHED 700 and held ~10 s at normal
# current (0.18 uA, same as A/B/C), then tripped/sparked and powered off; ramp failed -> sub-run
# aborted with no data. A/B/C were fine at 700. Dropped to 600 for all dets (operator).
RESIST = list(range(570, 520 - 1, -10))           # 570,560,...,520 (6 pts), -10 V steps
DET_D_OFFSET = 10                                 # det D resist = A/B/C - 10 (spark-sensitive)


def fmt_v(v):
    return f'{v:g}'


class Config(BeamConfig):
    def _set_defaults(self, config_path=None):
        super()._set_defaults(config_path)

        self.run_name = f'run_{RUN_NUM}'
        self.beam_type = 'cosmics'          # beam off
        self.n1081b_scan = 'off'            # static ungated trigger; no inline modulation
        self.run_out_dir = f'{self.data_out_dir}{self.run_name}/'
        self.dream_daq_info['run_directory'] = f'/home/mx17/july_dream/dream_run/{self.run_name}/'
        self.dream_daq_info['data_out_dir'] = f'{self.run_out_dir}'
        self.processor_info['run_dir'] = f'{self.run_out_dir}'
        self.hv_info['run_out_dir'] = self.run_out_dir
        self.resume = False

        self.trigger = (
            f'BEAM-OFF cosmic scint-Singles resist ladder ({self.run_name}). Trigger: scint '
            'SINGLES with veto OPEN (ungated), NO PS / NO readout gate -- M4.C = plain OR(Singles '
            'lemo0), M4.D = OR(C-out lemo1), set via setup_cosmics_singles_ungated.py. Every '
            'trigger is an ungated scintillator single (N93B 30 ms gate NOT applied). DREAM = RAW '
            f'full readout (zero_suppress=False), {N_SAMPLES} smp x {SAMPLE_PERIOD} ns = '
            f'{N_SAMPLES*SAMPLE_PERIOD} ns, latency {LATENCY} (scint MM ~smp 11), no ZS. HV: drift '
            f'{DRIFT} all dets (fixed); resist A/B/C 570->520 in -10 V steps (6 pts), det D '
            f'-{DET_D_OFFSET} V; ladder repeated {CYCLES}x -> {len(RESIST)*CYCLES} sub-runs x '
            f'{SUBRUN_MIN:g} min. Plastic threshold left at M2 resident set (0.5-MIP '
            '-65/-78/-86/-83; M2 out of contact, not touched). Scint PMT bias held at the 07-19 '
            'Y88 equalized setpoints. Ar/Iso 90/10, 3He, no Pb. RESTORE: trigger_mode.py scint '
            '--doubles.')

        # RAW / full readout (base template is already Tcm RAW; just pin samples/latency).
        self.dream_daq_info.update({
            'zero_suppress': False,
            'pedestal_subtraction': False,
            'n_samples_per_waveform': N_SAMPLES,
            'latency': LATENCY,
            'sample_period': SAMPLE_PERIOD,
        })

        # ----- sub-run build: cycle OUTER -> resist INNER -----
        def _drift(dv):
            return {'0': dv, '1': dv, '2': dv, '3': dv}             # card 9 ch 0-3 = drift A/B/C/D

        def _resist(v):
            return {'1': v, '2': v, '3': v, '4': v - DET_D_OFFSET}  # card 5 ch 1-4; det D lower

        self.sub_runs = []
        k = 0
        for cyc in range(CYCLES):
            for v in RESIST:
                self.sub_runs.append({
                    'sub_run_name': f'cos_{k:03d}_r{fmt_v(v)}_c{cyc:02d}',
                    'run_time': SUBRUN_MIN, 'post_pause_s': 0,
                    'latency': LATENCY, 'n_samples_per_waveform': N_SAMPLES,
                    'hvs': {'5': _resist(v), '9': _drift(DRIFT)},
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
    c.write_to_file('config/json_run_configs/run_config_cosmics_resist_scan.json')
    dd = c.dream_daq_info

    n = len(c.sub_runs)
    data_min = sum(sr['run_time'] for sr in c.sub_runs)
    wall_min = data_min + n * 1.0   # ~1 min/sub-run HV-ramp + inter-sub-run overhead

    print('=== run_68 — BEAM-OFF cosmic scint-Singles resist ladder (ungated, no PS/no gate) ===')
    print(f'run_name : {c.run_name}   beam {c.beam_type} | gas {c.gas} | target {c.target_type}')
    print(f'DREAM    : RAW (ZS={dd["zero_suppress"]}), {dd["n_samples_per_waveform"]} smp x '
          f'{dd["sample_period"]} ns = {dd["n_samples_per_waveform"]*dd["sample_period"]} ns, '
          f'latency {dd["latency"]}, IPD {dd["inter_packet_delay"]}')
    print(f'drift    : {DRIFT} V all dets (fixed)')
    print(f'resist   : {RESIST[0]}->{RESIST[-1]}  (-10 V, {len(RESIST)} pts; det D -{DET_D_OFFSET} V)')
    print(f'cycles   : {CYCLES}  (cycle OUTER, resist INNER)')
    print(f'total    : {n} sub-runs x {SUBRUN_MIN:g} min')
    print(f'  data time : {_fmt_hms(data_min)}')
    print(f'  wall time : ~{_fmt_hms(wall_min)}  (+ ~1 min/sub-run overhead)')
    print(f'first/last  : {c.sub_runs[0]["sub_run_name"]}  ...  {c.sub_runs[-1]["sub_run_name"]}')
    print()
    print('PRE-RUN (once, boards free — check config/n1081b_access/ first):')
    print('  .venv/bin/python n1081b/setup_cosmics_singles_ungated.py')
    print('  verify: .venv/bin/python n1081b/trigger_mode.py status')
    print('Launch:  ./start_run.sh run_config_cosmics_resist_scan.json')
    print('RESTORE: .venv/bin/python n1081b/trigger_mode.py scint --doubles')
