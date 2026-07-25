#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_pace.py — AND(singles, pulser)+veto PACED-TRIGGER comb test (2026-07-20).

HYPOTHESIS (operator): triggers *generated* during the ~9.6 ms FEU readout dead time
may limit the next readout batch. Test = GATE the scint singles trigger to narrow
(~1 us) windows spaced by a DETERMINISTIC pulser period P, so (almost) no triggers
are generated during the dead gap. Sweep P across 2->20 ms (relaunch per point) and
compare the event-time-since-flash structure to the default (combed) zs_* runs. If
pacing at P >= the ~9.6 ms block breaks the comb, pacing is the cure; if the comb
persists, it is the fixed FEU/TCM readout cycle (consistent with the 4 kHz test).

TRIGGER (set ONCE on the boards before launch):
  n1081b/setup_and_pace_trigger.py                       # C = AND(singles, pulser, veto-inv); D = OR(flash, C-out)
  n1081b/set_pulser.py --fixed --period <P_ns> --width 500   # DETERMINISTIC pulser, ~1 us wide
  -> the PS/gamma-flash on M4.D always fires UNVETOED (veto is on section C only), so
     tooth-0 stays populated every spill for the anchor.

DREAM side = IDENTICAL to the combed zs_* baseline so the only variable is the trigger:
  ZS k8 (Option B: CM on, Pd off, offline subtract), IPD 2, 32 smp x 60 ns, latency 5
  (flash framed at ~smp 13, taggable). Uses the prepped k8 tracer pedestal set.

RATE CAVEAT: with width ~1 us / period 10 ms the AND duty is ~1e-4, so the paced-
trigger rate is singles_rate x 1e-4 (~0.1 Hz at ~1 kHz singles). Sub-runs are long
and manually stopped; watch the Events count and stop each point when it has enough.
The flash on D still fires ~11x/spill regardless, so tooth-0 is always populated.

SWEEP: one launch per period. Set PACED_PERIOD_MS (default 10) to name the run, and
set the matching pulser period with set_pulser --fixed --period (PACED_PERIOD_MS*1e6).
  e.g.  PACED_PERIOD_MS=10 python run_config_pace.py   ->  run_name and_paced_10ms

HV: explicit safe high-gain point (resist A/B/C 560 / det D 550, drift A 600 / BCD 800
— within run_57's verified-safe range) so singles fire and the run is reproducible.
"""
import os
# --- repo-root shim (run_config_beam/base live one dir up) ---
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from run_config_beam import Config as BeamConfig

ZS_PED_SET = 'zs_k8_tracer_from_07-18-26_14-06-43'  # prepped k8 set (same as the zs_* baseline)

# --- period for THIS launch (ms); sweep by relaunching with a new value + set_pulser ---
PERIOD_MS = float(os.environ.get('PACED_PERIOD_MS', '10'))

# --- HV operating point (V) — single fixed point, run_57-safe range ---
RESIST_ABC = 560
DET_D_OFFSET = 10          # det D resist = A/B/C - 10 (=550)
DRIFT_A = 600              # det A drift (drift maxima 600 V all dets, per operator 2026-07-20)
DRIFT_BCD = 600            # det B/C/D drift (lowered 800 -> 600 with A)

# --- sub-run chunking (manual stop; extend/trim by watching the Events count) ---
N_SUBRUNS = 6
SUBRUN_MIN = 15.0


def _fmt(v):
    return f'{v:g}'


class Config(BeamConfig):
    def _set_defaults(self, config_path=None):
        super()._set_defaults(config_path)

        self.run_name = f'and_paced_{_fmt(PERIOD_MS)}ms'
        self.n1081b_scan = 'off'   # static AND trigger (setup_and_pace_trigger.py); no inline modulation
        self.run_out_dir = f'{self.data_out_dir}{self.run_name}/'
        self.dream_daq_info['run_directory'] = f'/home/mx17/july_dream/dream_run/{self.run_name}/'
        self.dream_daq_info['data_out_dir'] = f'{self.run_out_dir}'
        self.processor_info['run_dir'] = f'{self.run_out_dir}'
        self.hv_info['run_out_dir'] = self.run_out_dir
        self.resume = False
        self.trigger = (
            f'PACED comb test ({self.run_name}): C = AND(Singles lemo0, M6.D DETERMINISTIC '
            f'pulser lemo4 @ {_fmt(PERIOD_MS)} ms / ~1 us, veto lemo5 inverted); D = OR(PS/flash '
            f'lemo0 UNVETOED, C-out lemo1). Veto on section C ONLY. DREAM = ZS k8 (CM on, Pd off), '
            f'IPD 2, 32 smp x 60 ns, latency 5 (flash framed ~smp 13). HV resist A/B/C {RESIST_ABC} '
            f'/ D {RESIST_ABC - DET_D_OFFSET}, drift A {DRIFT_A} / BCD {DRIFT_BCD}. Ar/Iso 90/10, '
            f'3He target. Compare event-time-since-flash vs the default combed zs_* runs.')

        # ---- DREAM: identical to the combed ZS baseline (only the trigger differs) ----
        self.dream_daq_info.update({
            'daq_config_template_path': f'{self.base_out_dir}dream_config/Tcm_Mx17_July_ZS.cfg',
            'zero_suppress': True,
            'common_noise_subtraction': True,
            'pedestal_subtraction': False,
            'zs_type': 'tpc',
            'zs_check_sample': 4,
            'inter_packet_delay': 2,
            'n_samples_per_waveform': 32,
            'sample_period': 60,
            'latency': 5,
            'pedestals_dir': f'{self.base_out_dir}pedestals/',
            'pedestals': ZS_PED_SET,
        })

        # ---- HV: single fixed point (card 5 = resist ch1-4, card 9 = drift ch0-3) ----
        def _resist():
            return {'1': RESIST_ABC, '2': RESIST_ABC, '3': RESIST_ABC, '4': RESIST_ABC - DET_D_OFFSET}

        def _drift():
            return {'0': DRIFT_A, '1': DRIFT_BCD, '2': DRIFT_BCD, '3': DRIFT_BCD}

        self.sub_runs = []
        for k in range(N_SUBRUNS):
            self.sub_runs.append({
                'sub_run_name': f'paced_{_fmt(PERIOD_MS)}ms_{k:02d}',
                'run_time': SUBRUN_MIN, 'post_pause_s': 0,
                'hvs': {'5': _resist(), '9': _drift()},
            })

        # Re-merge scintillator PMT bias holds onto the new sub_runs (plastics card 07,
        # liquids card 08), same as run_config_beam / run_config_zs_pulser_test.
        scint_hvs = {}
        for det in self.detectors:
            if det['name'] not in self.included_detectors:
                continue
            if not str(det.get('det_type', '')).startswith('scintillator'):
                continue
            hv_channels = det.get('hv_channels')
            setpoint = det.get('hv_setpoint')
            if not isinstance(hv_channels, dict) or setpoint is None:
                continue
            for slot, channel in hv_channels.values():
                scint_hvs.setdefault(str(slot), {})[str(channel)] = setpoint
        for sub_run in self.sub_runs:
            hvs = sub_run.setdefault('hvs', {})
            for slot, chans in scint_hvs.items():
                hvs.setdefault(slot, {}).update(chans)


if __name__ == '__main__':
    c = Config()
    c.write_to_file('config/json_run_configs/run_config_pace.json')

    dd = c.dream_daq_info
    period_ns = int(round(PERIOD_MS * 1e6))
    print('=== AND(singles,pulser)+veto PACED comb test ===')
    print(f'run_name   : {c.run_name}   (PACED_PERIOD_MS={PERIOD_MS})')
    print(f'gas {c.gas} | beam {c.beam_type} | target {c.target_type} | filter {c.beam_filter}')
    print(f'DREAM      : ZS={dd["zero_suppress"]} CM={dd["common_noise_subtraction"]} '
          f'Pd={dd["pedestal_subtraction"]} k-set={dd["pedestals"]}')
    print(f'             {dd["n_samples_per_waveform"]} smp x {dd["sample_period"]} ns, '
          f'latency {dd["latency"]}, IPD {dd["inter_packet_delay"]}')
    print(f'HV         : resist A/B/C {RESIST_ABC} / D {RESIST_ABC - DET_D_OFFSET}, '
          f'drift A {DRIFT_A} / BCD {DRIFT_BCD}')
    print(f'sub_runs   : {N_SUBRUNS} x {SUBRUN_MIN} min (manual stop)')
    print()
    print('PRE-RUN (once, boards free — check config/n1081b_access/ first):')
    print('  .venv/bin/python n1081b/setup_and_pace_trigger.py           # AND trigger, read-back verified')
    print(f'  .venv/bin/python n1081b/set_pulser.py --fixed --period {period_ns} --width 500')
    print('  .venv/bin/python n1081b/setup_and_pace_trigger.py --status  # confirm C=[0,4,5], D=[0,1]')
    print('Launch:  ./start_run.sh run_config_pace.json')
    print('Sweep :  stop, set_pulser --fixed --period <new>, PACED_PERIOD_MS=<new_ms> python run_config_pace.py, relaunch')
