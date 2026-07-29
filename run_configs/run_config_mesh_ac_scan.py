#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_mesh_ac_scan.py — RAW singles+PS 2D drift x resist HV scan with MESH
CHARGE-INJECTION LIVE on detectors A and C (run_64, 2026-07-21).

WHAT'S NEW vs run_61: the mesh charge-injection circuit is now physically cabled to
detectors A and C (B and D are NOT — they are the in-run control pair). Injection is
fired by M6/.245 SEC_B (fan-out, mono 500 ns, G&D delay 1260 ns on in0) and is left ON
for the WHOLE run, not modulated per sub-run — so every sub-run gives a simultaneous
mesh (A, C) vs no-mesh (B, D) comparison at the same HV point and the same beam.
Verified ON before launch with `n1081b/set_mesh_injection.py status` (all 4 legs
enabled). n1081b_scan stays 'off': nothing modulates the trigger inline.

TRIGGER: scint --singles --ps-pickup -> M4.C = or_veto(Singles, lemo0) gated by the
  30 ms N93B readout window; M4.D = OR(lemo0 = PS/gamma-flash, lemo1 = C-out). Every
  trigger reads the PS pickup pulse AND the 30 ms-gated singles, so time-since-flash is
  reconstructable per event (needed to bin the comb — see below). RAW / full readout
  (ZS off), IPD 90, latency 33, 64 smp x 60 ns (3.84 us) — identical window to
  run_58/61/62/63 so frames compare directly. Deliberately deadtime-limited (singles is
  30-50x the DREAM raw-readout budget); the mesh injection at +1260 ns sits inside the
  3.84 us frame.

HV SCAN — COARSE-FIRST, PROGRESSIVELY REFINED, first pass ~1 h.
  run_61's "coarse" pass was 15 sub-runs (2.5 h), which is too coarse a stopping
  granularity when beam can vanish. Here the passes refine the SAME 6 x 11 grid in
  cost-ordered steps, and the run is stop-anywhere: whenever the beam ends, whatever
  passes completed leave an evenly-spread map, never a half-swept edge.

    P1 coarse box       drift 700,400          resist 570,545,520     6 sr   1.0 h  (cum 1.0 h)
    P2 drift fill A     drift 500,200          resist 570,545,520     6 sr   1.0 h  (cum 2.0 h)
    P3 drift fill B     drift 600,300          resist 570,545,520     6 sr   1.0 h  (cum 3.0 h)
    P4 resist fill 1    all 6 drifts           resist 560,535        12 sr   2.0 h  (cum 5.0 h)
    P5 resist fill 2    all 6 drifts           resist 565,550,525    18 sr   3.0 h  (cum 8.0 h)
    P6 resist fill 3    all 6 drifts           resist 555,540,530    18 sr   3.0 h  (cum 11.0 h)

  After P1 you have the corners + centre of the whole box; after P3 the full drift axis
  at 25 V resist steps; after P4 a 10-15 V resist grid; after P6 the complete 5 V ladder
  520..570 at all six drifts. 66 sub-runs x 10 min = 11.0 h data (~12.1 h wall).

  Resist ceiling raised 560 -> 570 (run_61 topped out at 560). run_61 showed the optimum
  MOVES with time since flash and at 27+ ms it sat at or ABOVE run_61's 560 V ceiling —
  i.e. the late-tooth optimum was never bracketed. 570 is well inside already-exercised
  territory (run_57 and run_58 both ran resist up to 580 V in this 90/10 gas).
  det D resist = A/B/C - 10 V at every point (run_58 practice; det D is spark-sensitive).
  Drift (card 9 ch 0-3) is common to all four detectors.

ANALYSIS NOTE (carry over from run_61): do NOT pool the post-flash comb when picking an
  optimum — split by tooth group (~4 ms / ~13 ms / ~27+ ms), because the best resist
  setting shifts upward with time since flash.

SCINT PMT HV: plastics (card 07) + liquids (card 08) held at the 07-19 Y88 equalized
  setpoints (merged into every sub-run, inherited from run_config_beam).

PRE-RUN (once, after run_63 stops; M2/.241 plastics NOT touched — out of contact,
driving the discriminator at its resident 0.5-MIP set -65/-78/-86/-83 mV):
  .venv/bin/python n1081b/set_mesh_injection.py status     # expect all 4 legs ON
  .venv/bin/python n1081b/trigger_mode.py scint --singles --ps-pickup
  verify: .venv/bin/python n1081b/trigger_mode.py status
          (expect C or_veto lemos=[0], D lemos=[0,1] -> "scint(singles)+ps")
Launch: ./start_run.sh run_config_mesh_ac_scan.json
"""
import os
# --- repo-root shim (run_config_beam/base live one dir up) ---
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from run_config_beam import Config as BeamConfig

RUN_NUM = 64

# ---- DREAM readout (RAW singles+PS, same window as run_58/61/62/63) ----
LATENCY = 33
N_SAMPLES = 64
IPD = 90
SAMPLE_PERIOD = 60

# ---- per-sub-run dwell (minutes) ----
SUBRUN_MIN = float(os.environ.get('SUBRUN_MIN', '10'))

DET_D_OFFSET = 10          # det D resist = A/B/C - 10 V (run_58 practice)
ALL_DRIFTS = [700, 600, 500, 400, 300, 200]

# ---- coarse-first refinement passes: (label, drift list, resist ladder) ----
# Each pass is self-contained: stopping after any pass leaves an evenly-spread grid.
PASSES = [
    ('P1 coarse box',    [700, 400], [570, 545, 520]),
    ('P2 drift fill A',  [500, 200], [570, 545, 520]),
    ('P3 drift fill B',  [600, 300], [570, 545, 520]),
    ('P4 resist fill 1', ALL_DRIFTS, [560, 535]),
    ('P5 resist fill 2', ALL_DRIFTS, [565, 550, 525]),
    ('P6 resist fill 3', ALL_DRIFTS, [555, 540, 530]),
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
        # Static trigger + STANDING mesh injection (set once on M6.B before launch,
        # left ON all run) — nothing is modulated per sub-run.
        self.n1081b_scan = 'off'

        self.trigger = (
            f'RAW singles+PS 2D drift x resist scan with MESH CHARGE-INJECTION LIVE on '
            f'detectors A and C ({self.run_name}). Mesh = M6/.245 SEC_B fan-out (4 legs '
            'enabled, in0 G&D delay 1260 ns, mono 500 ns), ON for the whole run and NOT '
            'modulated — dets B and D are uncabled, so every sub-run is a simultaneous '
            'mesh (A,C) vs no-mesh (B,D) comparison. Trigger scint --singles --ps-pickup: '
            'M4.C = or_veto(Singles, lemo0) gated by the 30 ms N93B window; M4.D = '
            'OR(lemo0 = PS/gamma-flash, lemo1 = C-out). Full readout (zero_suppress=False), '
            'IPD 90, latency 33, 64 smp x 60 ns (3.84 us). HV: coarse-first refinement of a '
            '6 drift x 11 resist grid — P1 drift 700/400 x resist 570/545/520 (~1 h), then '
            'drift fills 500/200 and 600/300, then resist fills to the full 5 V ladder '
            '570->520; det D 10 V lower; 66 sub-runs x 10 min, stop-anywhere. Resist ceiling '
            'raised to 570 (run_61 topped at 560 and the late-tooth optimum sat at/above it). '
            'Plastic threshold left at M2 resident set (0.5-MIP -65/-78/-86/-83; M2 out of '
            'contact, not touched). Scint PMT bias held at the 07-19 Y88 equalized setpoints. '
            'Ar/Iso 90/10, 3He, no Pb.')

        # RAW / full readout: keep the beam TCM template (NOT the ZS one).
        self.dream_daq_info.update({
            'daq_config_template_path': f'{self.base_out_dir}dream_config/Tcm_Mx17_July.cfg',
            'zero_suppress': False,
            'latency': LATENCY,
            'n_samples_per_waveform': N_SAMPLES,
            'sample_period': SAMPLE_PERIOD,
            'inter_packet_delay': IPD,
        })

        # ----- sub-run build: pass -> drift OUTER -> resist INNER -----
        def _drift(dv):
            return {'0': dv, '1': dv, '2': dv, '3': dv}             # card 9 ch 0-3 = drift A/B/C/D

        def _resist(v):
            return {'1': v, '2': v, '3': v, '4': v - DET_D_OFFSET}  # card 5 ch 1-4; det D lower

        self.sub_runs = []
        k = 0
        for _label, drifts, resists in PASSES:
            for dv in drifts:
                for v in resists:
                    self.sub_runs.append({
                        'sub_run_name': f'sngPSmesh_dr{fmt_v(dv)}_r{fmt_v(v)}_{k:03d}',
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
    c.write_to_file('config/json_run_configs/run_config_mesh_ac_scan.json')

    n = len(c.sub_runs)
    data_min = sum(sr['run_time'] for sr in c.sub_runs)
    wall_min = data_min + n * 1.0   # ~1 min/sub-run HV-ramp + inter-sub-run overhead

    print(f'=== {c.run_name} — RAW singles+PS drift x resist scan, MESH INJECTION on A + C ===')
    print(f'DAQ readout : {N_SAMPLES} smp x {SAMPLE_PERIOD} ns = {N_SAMPLES*SAMPLE_PERIOD} ns, '
          f'latency {LATENCY}, IPD {IPD}, ZS=False (RAW)')
    print(f'mesh        : M6/.245 SEC_B ON all run (A + C cabled; B + D = no-mesh control)')
    print(f'det D resist: A/B/C - {DET_D_OFFSET} V at every point')
    print('passes (coarse-first, stop-anywhere):')
    kk, cum = 0, 0.0
    for label, drifts, resists in PASSES:
        cnt = len(drifts) * len(resists)
        cum += cnt * SUBRUN_MIN
        print(f'  [{kk:02d}-{kk+cnt-1:02d}] {label:16s} drift {str(drifts):28s} '
              f'resist {resists}  -> {cnt:2d} sr, cum {_fmt_hms(cum)}')
        kk += cnt
    drifts_all = sorted({d for _l, ds, _r in PASSES for d in ds}, reverse=True)
    resists_all = sorted({v for _l, _d, rs in PASSES for v in rs}, reverse=True)
    print(f'final grid  : drift {drifts_all}')
    print(f'              resist {resists_all}  ({len(resists_all)} pts)')
    print(f'total       : {n} sub-runs x {SUBRUN_MIN:g} min')
    print(f'  data time : {_fmt_hms(data_min)}')
    print(f'  wall time : ~{_fmt_hms(wall_min)}  (+ ~1 min/sub-run overhead)')
    print(f'first/last  : {c.sub_runs[0]["sub_run_name"]}  ...  {c.sub_runs[-1]["sub_run_name"]}')
    print('\nPRE-RUN:')
    print('  .venv/bin/python n1081b/set_mesh_injection.py status   # expect all 4 legs ON')
    print('  .venv/bin/python n1081b/trigger_mode.py scint --singles --ps-pickup')
    print('Launch: ./start_run.sh run_config_mesh_ac_scan.json')
