#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_drift_resist_scan.py — RAW singles+PS 2D drift x resist HV scan, COARSE-FIRST
INTERLACED (run_59, 2026-07-20, for next beam).

CONTEXT: the plastic-MIP-threshold outer loop is dropped (we LOST CONTACT with M2/.241,
the plastics board). M2 is left ALONE (do not poke a possibly-wedged board; it self-heals
with isolation). Its resident discriminator config keeps driving the singles trigger at
whatever it currently holds (last set = the 07-19 0.5-MIP set -65/-78/-86/-83 mV, D=D_R).
This is just the 2D HV scan at that fixed plastic threshold.

TRIGGER (already on the boards; set ONCE, unchanged): scint --singles --ps-pickup ->
  M4.C = or_veto(Singles, lemo0) gated by the 30 ms N93B window; M4.D = OR(lemo0 =
  PS/gamma-flash, lemo1 = C-out). Every trigger reads the PS pickup pulse AND the 30 ms-
  gated singles. RAW / full readout (ZS off), IPD 90, latency 33, 64 smp x 60 ns
  (3.84 us) -- window sized to hold the full drift column across the whole drift sweep
  (run_58 sizing). Singles >> DREAM raw budget, so this is deliberately deadtime-limited.

HV SCAN -- COARSE-FIRST INTERLACED so an early beam loss still leaves a spread-out grid.
  Resist ladder (card 5 ch 1-4): A/B/C -10 V steps; a BASE pass (560->520) then an
    OFFSET pass shifted -5 V (555->515) -> together a 5 V-fine grid over 515..560 V.
    det D = A/B/C - 10 V at every point (run_58 practice; det D spark-sensitive).
  Drift ladder (card 9 ch 0-3, all 4 dets same): coarse-first fill 700/500/300, then
    600/400, then 200. For EACH drift block, the BASE resist pass runs first, then the
    -5 V OFFSET pass ("go back over the resists offset by -5 V"). Passes in order:
      1) drift 700,500,300  resist 560->520   (coarse drift, base)
      2) drift 700,500,300  resist 555->515   (coarse drift, -5 V offset)
      3) drift 600,400      resist 560->520   (mid drift, base)
      4) drift 600,400      resist 555->515   (mid drift, -5 V offset)
      5) drift 200          resist 560->520   (low drift, base)
      6) drift 200          resist 555->515   (low drift, -5 V offset)
    => 6 drifts x 10 resist = 60 sub-runs. Drift OUTER, resist INNER within each pass.

SCINT PMT HV: plastics (card 07) + liquids (card 08) held at the 07-19 Y88 equalized
  setpoints (merged into every sub-run, inherited from run_config_beam).

PRE-RUN (once, after any current run stops; NO M2 board op needed):
  verify: .venv/bin/python n1081b/trigger_mode.py status
          (expect C or_veto lemos=[0], D lemos=[0,1] -> "scint(singles)+ps")
  -- if M2 is still out of contact, DO NOT try to re-set the trigger; leave it. Confirm
     live event rate before committing beam (trigger_mode status polls M4, not M2).
Launch: ./start_run.sh run_config_drift_resist_scan.json
"""
import os
# --- repo-root shim (run_config_beam/base live one dir up) ---
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from run_config_beam import Config as BeamConfig

RUN_NUM   = 61

# ---- DREAM readout (RAW singles+PS, same as run_58) ----
LATENCY   = 33
N_SAMPLES = 64
IPD       = 90
SAMPLE_PERIOD = 60

# ---- per-sub-run dwell (minutes) ----
SUBRUN_MIN = float(os.environ.get('SUBRUN_MIN', '10'))

# ---- resist ladders (card 5): base -10 V steps, plus a -5 V-shifted offset pass ----
RESIST_BASE   = list(range(560, 520 - 1, -10))   # 560,550,540,530,520
RESIST_OFFSET = [v - 5 for v in RESIST_BASE]      # 555,545,535,525,515
DET_D_OFFSET  = 10                                # det D resist = A/B/C - 10 (run_58 practice)

# ---- coarse-first interlaced passes: (label, drift list, resist ladder) ----
PASSES = [
    ('coarse-drift base',   [700, 500, 300], RESIST_BASE),
    ('coarse-drift offset', [700, 500, 300], RESIST_OFFSET),
    ('mid-drift base',      [600, 400],      RESIST_BASE),
    ('mid-drift offset',    [600, 400],      RESIST_OFFSET),
    ('low-drift base',      [200],           RESIST_BASE),
    ('low-drift offset',    [200],           RESIST_OFFSET),
]


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
        self.n1081b_scan = 'off'   # static trigger (scint --singles --ps-pickup), no inline mesh modulation

        self.trigger = (
            f'RAW singles+PS 2D drift x resist scan, COARSE-FIRST INTERLACED ({self.run_name}). '
            'Trigger scint --singles --ps-pickup: M4.C = or_veto(Singles, lemo0) gated by the '
            '30 ms N93B window; M4.D = OR(lemo0 = PS/gamma-flash, lemo1 = C-out). Full readout '
            '(zero_suppress=False), IPD 90, latency 33, 64 smp x 60 ns (3.84 us). HV interlace: '
            'drift 700/500/300 then 600/400 then 200, each with a base resist pass (560->520, '
            '-10 V) then a -5 V offset pass (555->515); det D 10 V lower; 60 sub-runs. Plastic '
            'threshold left at M2 resident set (0.5-MIP -65/-78/-86/-83; M2 out of contact, not '
            'touched). Scint PMT bias held at the 07-19 Y88 equalized setpoints. Ar/Iso 90/10, '
            '3He, no Pb.')

        # RAW / full readout: keep the beam TCM template (NOT the ZS one).
        self.dream_daq_info.update({
            'daq_config_template_path': f'{self.base_out_dir}dream_config/Tcm_Mx17_July.cfg',
            'zero_suppress': False,
            'latency': LATENCY,
            'n_samples_per_waveform': N_SAMPLES,
            'sample_period': SAMPLE_PERIOD,
            'inter_packet_delay': IPD,
        })

        # ----- interlaced sub-run build: pass -> drift OUTER -> resist INNER -----
        def _drift(dv):
            return {'0': dv, '1': dv, '2': dv, '3': dv}          # card 9 ch 0-3 = drift A/B/C/D

        def _resist(v):
            return {'1': v, '2': v, '3': v, '4': v - DET_D_OFFSET}  # card 5 ch 1-4; det D lower

        self.sub_runs = []
        k = 0
        for _label, drifts, resists in PASSES:
            for dv in drifts:
                for v in resists:
                    self.sub_runs.append({
                        'sub_run_name': f'sngPS_dr{fmt_v(dv)}_r{fmt_v(v)}_{k:03d}',
                        'run_time': SUBRUN_MIN, 'post_pause_s': 0,
                        'inter_packet_delay': IPD,
                        'latency': LATENCY, 'n_samples_per_waveform': N_SAMPLES,
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


def _fmt_hms(minutes):
    h = int(minutes // 60)
    m = int(round(minutes - 60 * h))
    return f'{h} h {m:02d} min'


if __name__ == '__main__':
    c = Config()
    c.write_to_file('config/json_run_configs/run_config_drift_resist_scan.json')

    n = len(c.sub_runs)
    data_min = sum(sr['run_time'] for sr in c.sub_runs)
    wall_min = data_min + n * 1.0   # ~1 min/sub-run HV-ramp + inter-sub-run overhead

    print(f'=== {c.run_name} — RAW singles+PS 2D drift x resist scan (COARSE-FIRST INTERLACED) ===')
    print(f'DAQ readout : {N_SAMPLES} smp x {SAMPLE_PERIOD} ns = {N_SAMPLES*SAMPLE_PERIOD} ns, '
          f'latency {LATENCY}, IPD {IPD}, ZS=False (RAW)')
    print(f'resist base : {RESIST_BASE}  |  offset {RESIST_OFFSET}  (det D -{DET_D_OFFSET} V)')
    print('passes (in order):')
    kk = 0
    for label, drifts, resists in PASSES:
        cnt = len(drifts) * len(resists)
        print(f'  [{kk:02d}-{kk+cnt-1:02d}] {label:20s} drift {drifts}  resist {resists[0]}->{resists[-1]}')
        kk += cnt
    print(f'total       : {n} sub-runs x {SUBRUN_MIN:g} min')
    print(f'  data time : {_fmt_hms(data_min)}')
    print(f'  wall time : ~{_fmt_hms(wall_min)}  (+ ~1 min/sub-run overhead)')
    print(f'first/last  : {c.sub_runs[0]["sub_run_name"]}  ...  {c.sub_runs[-1]["sub_run_name"]}')
    print('\nplastic threshold: LEFT at M2 resident set (0.5-MIP -65/-78/-86/-83, D=D_R); '
          'M2 out of contact -- NOT touched.')
    print('Launch: ./start_run.sh run_config_drift_resist_scan.json')
