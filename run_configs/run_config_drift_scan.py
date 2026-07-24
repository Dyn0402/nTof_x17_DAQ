#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_drift_scan.py — overnight RAW singles+PS 2D drift x resist scan (2026-07-19/20).

TRIGGER: scint --singles --ps-pickup (set ONCE on the boards before launch) —
  M4.C = or_veto(Singles, lemo0) gated by the 30 ms N93B window; M4.D = OR(lemo0 =
  PS/gamma-flash line, lemo1 = C-out). So every trigger reads out the PS pickup pulse
  AND the 30 ms-gated singles. RAW / full readout (zero_suppress=False), IPD 90 (raw
  needs IPD >= ~75). Singles is ~30-50x the DREAM raw-readout budget, so this run is
  deliberately deadtime-limited (missing events is expected/accepted); with n=64 + IPD90
  the per-event deadtime is ~2x the n=32 case, so the effective event rate is ~halved.

WINDOW: 64 samples x 60 ns (3.84 us) so the drift COLUMN is fully captured across the
  WHOLE drift sweep. The DRIFT_WINDOW_ANALYSIS.md (2026-07-19) window sizing was measured
  only at drift 600-800 V (column 11-15 smp); this scan runs drift down to 150 V, where
  the drift velocity collapses and the column lengthens a lot, so a tight (n=29) window
  would truncate the far tail at low drift. n=64 holds it. LATENCY 33: first arrival
  (pulse rise onset) lands ~sample 3 (rise onset = latency - 30; measured latency-35 onset
  ~sample 5, shifts 1:1), leaving ~2-3 baseline samples ahead of it and ~61 samples of
  drift-column room out to ~sample 63. Verify at the first (drift 700, shortest column)
  sub-run that the pulse is contained with baseline pre-roll.

HV SCAN (2D, drift OUTER x resist INNER; A/B/C/D all together):
  Resist inner (card 5 ch 1-4): A/B/C step 580 -> 540 V in -5 V (9 pts); det D = A/B/C - 10
    (570 -> 530) at every point.
  Drift outer (card 9 ch 0-3, ALL FOUR dets same value): coarse-first interleave so an
    early beam loss still spans the full range —
      Pass 1 (coarse, full range): 700, 600, 500, 400, 300, 200
      Pass 2 (fine fill):          450, 350, 250, 150
    sorted grid 700,600,500,450,400,350,300,250,200,150 (100 V steps >=500, 50 V <500,
    floor 150). Highest-first, so A hits 700 V on the first sub-run.

STRUCTURE: 10 drift x 9 resist = 90 x 10 min sub-runs = 15.0 h data (~16.5 h wall).
  Resume-safe: each sub-run name carries its (drift, resist) point + a global index.

SCINT PMT HV: plastics (card 07) + liquids (card 08) held at the 2026-07-18/19 equalized
  setpoints (merged into every sub-run, inherited from run_config_beam).

PRE-RUN (once, after the current run stops + n1081b access is clear):
  .venv/bin/python n1081b/trigger_mode.py scint --singles --ps-pickup
  verify: .venv/bin/python n1081b/trigger_mode.py status   (expect C or_veto lemos=[0],
          D lemos=[0,1] -> "scint(singles)+ps")
Launch: .venv/bin/python daq_control.py run_config_drift_scan.json
"""
# --- repo-root shim (run_config_beam/base live one dir up) ---
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from run_config_beam import Config as BeamConfig

LATENCY   = 33     # first-arrival ~sample 3 (2-3 baseline samples ahead), max tail room in n=64
N_SAMPLES = 64     # capture the full drift column across the whole drift sweep (down to 150 V)
IPD       = 90     # raw/full readout needs IPD >= ~75
SUBRUN_MIN = 10

# Resist inner ladder (V): A/B/C top-down; det D held DET_D_OFFSET below at every point.
RESIST_LADDER = list(range(580, 540 - 1, -5))   # 580..540 -5 V -> 9 pts
DET_D_OFFSET  = 10                               # det D resist = A/B/C - 10 (570..530)

# Drift outer ladder (V), applied to ALL four dets. Coarse-first interleave: pass 1 spans
# the full range at 100 V, pass 2 fills the 50 V midpoints, so an early stop still covers
# the whole range. Sorted grid: 700,600,500,450,400,350,300,250,200,150.
DRIFT_ORDER = [700, 600, 500, 400, 300, 200,   # pass 1 (coarse, full range)
               450, 350, 250, 150]             # pass 2 (fine fill)


def fmt_v(v):
    return f'{v:g}'


class Config(BeamConfig):
    def _set_defaults(self, config_path=None):
        super()._set_defaults(config_path)
        self.run_name = 'run_58'
        self.run_out_dir = f'{self.data_out_dir}{self.run_name}/'
        self.dream_daq_info['run_directory'] = f'/home/mx17/july_dream/dream_run/{self.run_name}/'
        self.dream_daq_info['data_out_dir'] = f'{self.run_out_dir}'
        self.processor_info['run_dir'] = f'{self.run_out_dir}'
        self.hv_info['run_out_dir'] = self.run_out_dir
        self.resume = False
        self.n1081b_scan = 'off'  # static trigger (scint --singles --ps-pickup), no inline mesh modulation
        self.trigger = ('RAW singles+PS 2D drift x resist scan (run_58): scint --singles --ps-pickup '
                        '— M4.C = or_veto(Singles, lemo0) gated by the 30 ms N93B window; M4.D = '
                        'OR(lemo0 = PS/gamma-flash, lemo1 = C-out). Full readout (zero_suppress=False), '
                        'IPD 90, latency 33, 64 smp x 60 ns (3.84 us) — window sized to contain the '
                        'full drift column across the whole drift sweep (down to 150 V), first arrival '
                        '~sample 3. Deliberately deadtime-limited (singles >> DREAM raw budget). '
                        'HV: drift OUTER 700->150 V (10 pts, all 4 dets), resist INNER A/B/C 580->540 V '
                        '(-5 V, 9 pts), det D 10 V lower. Scint PMT bias held at the 07-18/19 equalized '
                        'setpoints. Ar/Iso 90/10, 3He target, no Pb filter.')

        # RAW / full readout: keep the beam TCM template (NOT the ZS one). Override the
        # flash_random latency/window with the drift-scan window.
        self.dream_daq_info.update({
            'daq_config_template_path': f'{self.base_out_dir}dream_config/Tcm_Mx17_July.cfg',
            'zero_suppress': False,
            'latency': LATENCY,
            'n_samples_per_waveform': N_SAMPLES,
            'sample_period': 60,
            'inter_packet_delay': IPD,
        })

        # ----- 2D sub-run build: drift OUTER, resist INNER -----
        def _drift(dv):
            # card 9 ch 0-3 = drift A/B/C/D, all four at dv
            return {'0': dv, '1': dv, '2': dv, '3': dv}

        def _resist(v):
            # card 5 ch 1-4 = resist A/B/C/D; A/B/C at v, det D at v - DET_D_OFFSET
            return {'1': v, '2': v, '3': v, '4': v - DET_D_OFFSET}

        self.sub_runs = []
        k = 0
        for dv in DRIFT_ORDER:
            for v in RESIST_LADDER:
                self.sub_runs.append({
                    'sub_run_name': f'sngPS_dr{fmt_v(dv)}_r{fmt_v(v)}_{k:03d}',
                    'run_time': SUBRUN_MIN, 'post_pause_s': 0,
                    'inter_packet_delay': IPD,
                    'latency': LATENCY, 'n_samples_per_waveform': N_SAMPLES,
                    'hvs': {'5': _resist(v), '9': _drift(dv)},
                })
                k += 1

        # Re-merge the scintillator PMT bias into the rebuilt sub-runs (plastics card 07,
        # liquids card 08) so they are held on for the whole run + appear in HV monitoring.
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
    c.write_to_file('config/json_run_configs/run_config_drift_scan.json')

    ns = c.dream_daq_info['n_samples_per_waveform']
    sp = c.dream_daq_info['sample_period']
    lat = c.dream_daq_info['latency']
    n = len(c.sub_runs)
    data_min = sum(sr['run_time'] for sr in c.sub_runs)
    print('=== run_58 — RAW singles+PS 2D drift x resist scan ===')
    print(f'Gas {c.gas} | beam {c.beam_type} | target {c.target_type} | filter {c.beam_filter}')
    print(f'DAQ: {ns} smp x {sp} ns = {ns*sp} ns window, latency {lat}, IPD {IPD}, '
          f'ZS={c.dream_daq_info["zero_suppress"]}')
    print(f'Drift (all 4 dets): {DRIFT_ORDER}  (sorted {sorted(DRIFT_ORDER, reverse=True)})')
    print(f'Resist A/B/C: {RESIST_LADDER[0]}->{RESIST_LADDER[-1]} -5 V ({len(RESIST_LADDER)} pts), '
          f'det D -{DET_D_OFFSET}')
    print(f'{len(DRIFT_ORDER)} drift x {len(RESIST_LADDER)} resist = {n} sub-runs x {SUBRUN_MIN} min '
          f'= {data_min/60:.1f} h data (~{(data_min + n)/60:.1f} h wall w/ ~1 min/subrun overhead)')
    print('First sub-run:', c.sub_runs[0]['sub_run_name'], '| last:', c.sub_runs[-1]['sub_run_name'])
    print('\nPRE-RUN (once, after current run stops + n1081b access clear):')
    print('  .venv/bin/python n1081b/trigger_mode.py scint --singles --ps-pickup')
    print('  .venv/bin/python n1081b/trigger_mode.py status   # expect C or_veto [0], D [0,1]')
    print('Launch: .venv/bin/python daq_control.py run_config_drift_scan.json')
