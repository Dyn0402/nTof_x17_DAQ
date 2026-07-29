#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_cosmics_singles.py — quick BEAM-OFF scint-Singles, NO veto (2026-07-20).

Beam is off, so we trigger on scintillator SINGLES with the veto OPEN (ungated):
the N93B 30 ms veto window never opens without PS pulses, so a normal veto-gated
scint trigger would be vetoed ~100 %. Open the veto by making M4.C a plain OR on
the Singles line (lemo0) — exactly `setup_cosmics_singles_ungated.py`.

DREAM = RAW full readout, 32 smp x 60 ns, latency 35 (scint MM at ~smp 11). No ZS,
no pedestal subtraction. HV = run_57-safe high-gain point: resist A/B/C 560 / det D
550, drift 600 all dets. One long sub-run (2 h cap) — stop manually when done.

PRE-RUN (once, boards free — check config/n1081b_access/ first):
  .venv/bin/python n1081b/setup_cosmics_singles_ungated.py   # C = plain OR(Singles), veto OPEN
Launch:
  ./start_run.sh run_config_cosmics_singles.json
RESTORE when beam returns:
  .venv/bin/python n1081b/trigger_mode.py scint --doubles    # re-arms the 30 ms veto gate
"""
# --- repo-root shim (run_config_beam/base live one dir up) ---
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from run_config_beam import Config as BeamConfig

LATENCY, N_SAMPLES, SUBRUN_MIN = 35, 32, 120.0
RESIST_ABC = 560
DET_D_OFFSET = 10          # det D resist = A/B/C - 10 (=550)
DRIFT = 600                # all dets (drift maxima 600 V, per operator 2026-07-20)


class Config(BeamConfig):
    def _set_defaults(self, config_path=None):
        super()._set_defaults(config_path)

        self.run_name = 'run_59'
        self.beam_type = 'cosmics'          # beam off
        self.n1081b_scan = 'off'            # static ungated trigger; no inline modulation
        self.run_out_dir = f'{self.data_out_dir}{self.run_name}/'
        self.dream_daq_info['run_directory'] = f'/home/mx17/july_dream/dream_run/{self.run_name}/'
        self.dream_daq_info['data_out_dir'] = f'{self.run_out_dir}'
        self.processor_info['run_dir'] = f'{self.run_out_dir}'
        self.hv_info['run_out_dir'] = self.run_out_dir
        self.resume = False
        self.trigger = (
            'Quick BEAM-OFF scint-Singles, veto OPEN (ungated): M4.C = plain OR(Singles '
            'lemo0), M4.D = OR(C-out lemo1) — set via setup_cosmics_singles_ungated.py. '
            f'DREAM = RAW full readout, {N_SAMPLES} smp x 60 ns, latency {LATENCY} '
            '(scint MM ~smp 11), no ZS. HV resist A/B/C '
            f'{RESIST_ABC} / D {RESIST_ABC - DET_D_OFFSET}, drift {DRIFT} all dets. '
            'Ar/Iso 90/10, 3He target. RESTORE: trigger_mode.py scint --doubles.')

        # ---- DREAM: RAW full readout (base defaults are RAW; just pin samples/latency) ----
        self.dream_daq_info.update({
            'zero_suppress': False,
            'pedestal_subtraction': False,
            'n_samples_per_waveform': N_SAMPLES,
            'latency': LATENCY,
        })

        # ---- HV: single fixed point (card 5 = resist ch1-4, card 9 = drift ch0-3) ----
        hv = {'5': {'1': RESIST_ABC, '2': RESIST_ABC, '3': RESIST_ABC, '4': RESIST_ABC - DET_D_OFFSET},
              '9': {'0': DRIFT, '1': DRIFT, '2': DRIFT, '3': DRIFT}}

        self.sub_runs = [{
            'sub_run_name': 'run_59_00',
            'run_time': SUBRUN_MIN, 'post_pause_s': 0,
            'hvs': {k: dict(v) for k, v in hv.items()},
        }]

        # Re-merge scintillator PMT bias holds (plastics card 07, liquids card 08).
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
    c.write_to_file('config/json_run_configs/run_config_cosmics_singles.json')
    dd = c.dream_daq_info
    print('=== quick BEAM-OFF scint-Singles, veto OPEN ===')
    print(f'run_name : {c.run_name}   beam {c.beam_type} | gas {c.gas} | target {c.target_type}')
    print(f'DREAM    : RAW (ZS={dd["zero_suppress"]}), {dd["n_samples_per_waveform"]} smp x '
          f'{dd["sample_period"]} ns, latency {dd["latency"]}, IPD {dd["inter_packet_delay"]}')
    print(f'HV       : resist A/B/C {RESIST_ABC} / D {RESIST_ABC - DET_D_OFFSET}, drift {DRIFT} all')
    print(f'sub_runs : {len(c.sub_runs)} x {SUBRUN_MIN:g} min (manual stop)')
    print()
    print('PRE-RUN (once, boards free — check config/n1081b_access/ first):')
    print('  .venv/bin/python n1081b/setup_cosmics_singles_ungated.py')
    print('Launch:  ./start_run.sh run_config_cosmics_singles.json')
    print('RESTORE: .venv/bin/python n1081b/trigger_mode.py scint --doubles')
