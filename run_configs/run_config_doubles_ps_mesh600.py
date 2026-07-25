#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_doubles_ps_mesh600.py — RAW DOUBLES+PS resist scan at FIXED drift 600 V
(run_63, 2026-07-21). Short ~3 h gain-curve companion to run_61/62.

WHY: run_61 (singles+PS) and run_62 (singles-only) mapped the full drift x resist grid.
This is a short doubles-coincidence + PS/flash run at a single drift point (600 V), sweeping
only the resist/mesh (card 5) to trace the gain curve with a clean two-plane track trigger.

TRIGGER (set ONCE on M4/.243 before launch; M2/.241 plastics NOT touched — out of contact,
driving the discriminator at its resident set):
    n1081b/trigger_mode.py scint --doubles --ps-pickup
  -> M4.C = or_veto(Doubles, lemo1) gated by the 30 ms N93B readout window (veto ON);
     M4.D = OR(lemo0 = PS/gamma-flash, lemo1 = C-out). Every trigger reads the PS pickup
     pulse AND a 30 ms-gated two-plane coincidence. The PS leg's G&D co-frame delay on
     M4.D in0 is UNCHANGED from run_61 (trigger_mode does not touch it, no .243 power event
     since), so the flash lands in-window with the doubles MM pulse (64 smp = 3.84 us leaves
     ample room; exact co-frame non-critical for a gain scan).
  RAW / full readout (ZS off), IPD 90, latency 33, 64 smp x 60 ns — identical to run_61/62
  so frames compare directly.

HV: drift (card 9 ch 0-3) FIXED at 600 V, all four dets. Resist (card 5 ch 1-4) sweep,
  single -5 V pass 560 -> 520 (9 pts); det D = A/B/C - 10 V (run_58 practice; det D
  spark-sensitive). All resist points within the run_58/61/62 tested range.
  => 9 sub-runs x 20 min = 3.0 h data (~3.2 h wall). Longer dwell than run_62 because the
  doubles coincidence rate is far lower than singles.

SCINT PMT HV: plastics (card 07) + liquids (card 08) held at the 07-19 Y88 equalized
  setpoints (merged into every sub-run, inherited from run_config_beam).

PRE-RUN (once, after run_62 stops; NO M2 board op needed):
  .venv/bin/python n1081b/trigger_mode.py scint --doubles --ps-pickup
  verify: .venv/bin/python n1081b/trigger_mode.py status
          (expect C or_veto lemos=[1], D lemos=[0,1] -> "scint(doubles)+ps")
Launch: ./start_run.sh run_config_doubles_ps_mesh600.json
"""
import os
# --- repo-root shim (run_config_beam/base live one dir up) ---
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from run_config_beam import Config as BeamConfig

RUN_NUM   = 63

# ---- DREAM readout (RAW, same window as run_58/61/62) ----
LATENCY   = 33
N_SAMPLES = 64
IPD       = 90
SAMPLE_PERIOD = 60

# ---- per-sub-run dwell (minutes): 20 min x 9 pts = 3.0 h (low doubles rate) ----
SUBRUN_MIN = float(os.environ.get('SUBRUN_MIN', '20'))

# ---- HV ----
DRIFT_FIXED = 600                                # card 9 ch 0-3, all dets, held all run
RESIST = list(range(560, 520 - 1, -5))           # 560,555,...,520 (9 pts), single -5 V pass
DET_D_OFFSET = 10                                # det D resist = A/B/C - 10 (run_58 practice)


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
        self.n1081b_scan = 'off'   # static trigger (scint --doubles --ps-pickup), no inline mesh modulation

        self.trigger = (
            f'RAW doubles+PS resist scan at FIXED drift 600 V ({self.run_name}). Trigger scint '
            '--doubles --ps-pickup: M4.C = or_veto(Doubles, lemo1) gated by the 30 ms N93B '
            'window (veto ON); M4.D = OR(lemo0 = PS/gamma-flash, lemo1 = C-out). Full readout '
            '(zero_suppress=False), IPD 90, latency 33, 64 smp x 60 ns (3.84 us). HV: drift '
            'fixed 600 V all dets; resist single -5 V pass 560->520 (9 pts); det D 10 V lower; '
            '9 sub-runs x 20 min. Plastic threshold left at M2 resident set (0.5-MIP '
            '-65/-78/-86/-83; M2 out of contact, not touched). Scint PMT bias held at the '
            '07-19 Y88 equalized setpoints. Ar/Iso 90/10, 3He, no Pb.')

        # RAW / full readout: keep the beam TCM template (NOT the ZS one).
        self.dream_daq_info.update({
            'daq_config_template_path': f'{self.base_out_dir}dream_config/Tcm_Mx17_July.cfg',
            'zero_suppress': False,
            'latency': LATENCY,
            'n_samples_per_waveform': N_SAMPLES,
            'sample_period': SAMPLE_PERIOD,
            'inter_packet_delay': IPD,
        })

        # ----- sub-run build: fixed drift 600, resist sweep -----
        def _drift(dv):
            return {'0': dv, '1': dv, '2': dv, '3': dv}          # card 9 ch 0-3 = drift A/B/C/D

        def _resist(v):
            return {'1': v, '2': v, '3': v, '4': v - DET_D_OFFSET}  # card 5 ch 1-4; det D lower

        self.sub_runs = []
        for k, v in enumerate(RESIST):
            self.sub_runs.append({
                'sub_run_name': f'dblPS_dr{fmt_v(DRIFT_FIXED)}_r{fmt_v(v)}_{k:03d}',
                'run_time': SUBRUN_MIN, 'post_pause_s': 0,
                'inter_packet_delay': IPD,
                'latency': LATENCY, 'n_samples_per_waveform': N_SAMPLES,
                'hvs': {'5': _resist(v), '9': _drift(DRIFT_FIXED)},
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
    c.write_to_file('config/json_run_configs/run_config_doubles_ps_mesh600.json')

    n = len(c.sub_runs)
    data_min = sum(sr['run_time'] for sr in c.sub_runs)
    wall_min = data_min + n * 1.0   # ~1 min/sub-run HV-ramp + inter-sub-run overhead

    print(f'=== {c.run_name} — RAW DOUBLES+PS resist scan at FIXED drift 600 V (~3 h) ===')
    print(f'DAQ readout : {N_SAMPLES} smp x {SAMPLE_PERIOD} ns = {N_SAMPLES*SAMPLE_PERIOD} ns, '
          f'latency {LATENCY}, IPD {IPD}, ZS=False (RAW)')
    print(f'drift FIXED : {DRIFT_FIXED} V (all dets)')
    print(f'resist scan : {RESIST[0]}->{RESIST[-1]}  (-5 V, {len(RESIST)} pts; det D -{DET_D_OFFSET} V)')
    print(f'total       : {n} sub-runs x {SUBRUN_MIN:g} min')
    print(f'  data time : {_fmt_hms(data_min)}')
    print(f'  wall time : ~{_fmt_hms(wall_min)}  (+ ~1 min/sub-run overhead)')
    print(f'first/last  : {c.sub_runs[0]["sub_run_name"]}  ...  {c.sub_runs[-1]["sub_run_name"]}')
    print('\ntrigger: scint --doubles --ps-pickup -- run BEFORE launch:')
    print('  .venv/bin/python n1081b/trigger_mode.py scint --doubles --ps-pickup')
    print('plastic threshold: LEFT at M2 resident set (0.5-MIP -65/-78/-86/-83, D=D_R); '
          'M2 out of contact -- NOT touched.')
    print('Launch: ./start_run.sh run_config_doubles_ps_mesh600.json')
