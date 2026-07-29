#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_drift_resist_nops.py — RAW singles-ONLY (no PS trigger) 2D drift x resist HV
scan, truncated 3 h version of run_config_drift_resist_scan.py (run_62, 2026-07-21).

WHY: run_61 took the full 60-sub-run singles+PS coarse-first interlaced scan. This is a
short follow-up with the PS/gamma-flash trigger leg REMOVED, to see the same drift x resist
grid on gated-singles triggers ALONE (no beam-pickup co-frame). Truncated to ~3 h: only 2
drift points and a single (non-interlaced) resist pass.

TRIGGER (set ONCE on M4/.243 before launch; M2/.241 plastics NOT touched — still out of
contact, driving singles at its resident 0.5-MIP set -65/-78/-86/-83 mV):
    n1081b/trigger_mode.py scint --singles      (NO --ps-pickup)
  -> M4.C = or_veto(Singles, lemo0) gated by the 30 ms N93B readout window (veto ON);
     M4.D = OR(lemo1 = C-out) ONLY. The PS/gamma-flash line (M4.D lemo0) is dropped, so
     every trigger is a 30 ms-gated single — no PS pickup pulse in the DREAM frame.
  RAW / full readout (ZS off), IPD 90, latency 33, 64 smp x 60 ns (3.84 us) — same window
  sizing as run_58/run_61 so the frames are directly comparable.

HV SCAN — coarse-first, drift OUTER / resist INNER, single pass (no offset interlace):
  Drift ladder (card 9 ch 0-3, all 4 dets same): 700 then 300.
  Resist ladder (card 5 ch 1-4): A/B/C single -5 V pass 560 -> 520 (9 pts); det D = A/B/C
    - 10 V at every point (run_58 practice; det D spark-sensitive).
  => 2 drift x 9 resist = 18 sub-runs x 10 min = 3.0 h data (~3.3 h wall).

SCINT PMT HV: plastics (card 07) + liquids (card 08) held at the 07-19 Y88 equalized
  setpoints (merged into every sub-run, inherited from run_config_beam).

PRE-RUN (once, after run_61 stops; NO M2 board op needed):
  .venv/bin/python n1081b/trigger_mode.py scint --singles
  verify: .venv/bin/python n1081b/trigger_mode.py status
          (expect C or_veto lemos=[0], D lemos=[1] -> "scint(singles)")
Launch: ./start_run.sh run_config_drift_resist_nops.json
"""
import os
# --- repo-root shim (run_config_beam/base live one dir up) ---
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from run_config_beam import Config as BeamConfig

RUN_NUM   = 62

# ---- DREAM readout (RAW singles, same window as run_58/run_61) ----
LATENCY   = 33
N_SAMPLES = 64
IPD       = 90
SAMPLE_PERIOD = 60

# ---- per-sub-run dwell (minutes) ----
SUBRUN_MIN = float(os.environ.get('SUBRUN_MIN', '10'))

# ---- HV ladders ----
DRIFTS = [700, 300]                              # card 9 ch 0-3, all dets same
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
        self.n1081b_scan = 'off'   # static trigger (scint --singles), no inline mesh modulation

        self.trigger = (
            f'RAW singles-ONLY (no PS) 2D drift x resist scan, truncated 3 h ({self.run_name}). '
            'Trigger scint --singles (NO ps-pickup): M4.C = or_veto(Singles, lemo0) gated by the '
            '30 ms N93B window (veto ON); M4.D = OR(lemo1 = C-out) ONLY -- the PS/gamma-flash '
            'line (M4.D lemo0) is REMOVED. Full readout (zero_suppress=False), IPD 90, latency '
            '33, 64 smp x 60 ns (3.84 us). HV: drift 700 then 300, each a single -5 V resist '
            'pass 560->520; det D 10 V lower; 18 sub-runs x 10 min. Plastic threshold left at M2 '
            'resident set (0.5-MIP -65/-78/-86/-83; M2 out of contact, not touched). Scint PMT '
            'bias held at the 07-19 Y88 equalized setpoints. Ar/Iso 90/10, 3He, no Pb.')

        # RAW / full readout: keep the beam TCM template (NOT the ZS one).
        self.dream_daq_info.update({
            'daq_config_template_path': f'{self.base_out_dir}dream_config/Tcm_Mx17_July.cfg',
            'zero_suppress': False,
            'latency': LATENCY,
            'n_samples_per_waveform': N_SAMPLES,
            'sample_period': SAMPLE_PERIOD,
            'inter_packet_delay': IPD,
        })

        # ----- sub-run build: drift OUTER -> resist INNER, single pass -----
        def _drift(dv):
            return {'0': dv, '1': dv, '2': dv, '3': dv}          # card 9 ch 0-3 = drift A/B/C/D

        def _resist(v):
            return {'1': v, '2': v, '3': v, '4': v - DET_D_OFFSET}  # card 5 ch 1-4; det D lower

        self.sub_runs = []
        k = 0
        for dv in DRIFTS:
            for v in RESIST:
                self.sub_runs.append({
                    'sub_run_name': f'sng_dr{fmt_v(dv)}_r{fmt_v(v)}_{k:03d}',
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
    c.write_to_file('config/json_run_configs/run_config_drift_resist_nops.json')

    n = len(c.sub_runs)
    data_min = sum(sr['run_time'] for sr in c.sub_runs)
    wall_min = data_min + n * 1.0   # ~1 min/sub-run HV-ramp + inter-sub-run overhead

    print(f'=== {c.run_name} — RAW singles-ONLY (no PS) 2D drift x resist scan (truncated 3 h) ===')
    print(f'DAQ readout : {N_SAMPLES} smp x {SAMPLE_PERIOD} ns = {N_SAMPLES*SAMPLE_PERIOD} ns, '
          f'latency {LATENCY}, IPD {IPD}, ZS=False (RAW)')
    print(f'drifts      : {DRIFTS}')
    print(f'resist      : {RESIST[0]}->{RESIST[-1]}  (-5 V, {len(RESIST)} pts; det D -{DET_D_OFFSET} V)')
    print(f'total       : {n} sub-runs x {SUBRUN_MIN:g} min  (drift OUTER, resist INNER)')
    print(f'  data time : {_fmt_hms(data_min)}')
    print(f'  wall time : ~{_fmt_hms(wall_min)}  (+ ~1 min/sub-run overhead)')
    print(f'first/last  : {c.sub_runs[0]["sub_run_name"]}  ...  {c.sub_runs[-1]["sub_run_name"]}')
    print('\ntrigger: scint --singles (NO ps-pickup) -- run BEFORE launch:')
    print('  .venv/bin/python n1081b/trigger_mode.py scint --singles')
    print('plastic threshold: LEFT at M2 resident set (0.5-MIP -65/-78/-86/-83, D=D_R); '
          'M2 out of contact -- NOT touched.')
    print('Launch: ./start_run.sh run_config_drift_resist_nops.json')
