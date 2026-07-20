#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_zs_doubles.py — first ZS PHYSICS run: scint-DOUBLES + PS-pickup trigger, low
k-sigma (keep most hits), with a per-sub-run IPD scan (2026-07-19).

Builds on the validated ZS machinery (run `zs_pulser_test`, docs/ZS_PULSER_TEST_PROCEDURE.md):
Option B confirmed (firmware Pd=0 + offline pedestal subtraction, processor unchanged), IPD is
a run-config knob, k-sigma/tracers come from editing the latest pedestals' thr.prg.

GOALS (operator, 2026-07-19):
  1. Doubles + usual PS trigger (the run_56 mode: Doubles OR PS-pickup, flash/PS co-framed).
  2. Best "tight but safe" physics LATENCY = 34 (drift-window study, keep n=32: 0% primaries
     lost, ~0.02% charge; latency 35 clips the deepest ~1% tail). CONFIRM co-framing on the
     first sub-run (the 1800 ns PS delay + doubles MM were tuned at latency 35 — everything
     shifts 1 sample earlier at 34, still framed; verify flash/doubles/drift all in-window).
  3. Low k-sigma = 3.5 (keep most hits; sim-optimal per-strip retention, CM-cleaned floor).
     First point (IPD 100) tells us whether we capture ~all doubles or start losing some.
  4. IPD scan DOWN at this fixed trigger + k-sigma: event rate vs IPD, and when/if corruption
     appears (host Udp RcvbufErrors, tracer-511 truncation, FEU size truncation). Each sub-run
     overrides inter_packet_delay; everything else (HV, k, latency, trigger) held fixed.

PRE-RUN BOARD SETUP (needs the scint trigger boards FREE — wait for the threshold ladder to
finish; never SIGKILL it):
    .venv/bin/python n1081b/trigger_mode.py scint --doubles --ps-pickup
    .venv/bin/python n1081b/set_ps_trigger_delay.py --delay 1800
    .venv/bin/python n1081b/trigger_mode.py status          # expect C=or_veto[1], D=[0,1]
    .venv/bin/python n1081b/set_ps_trigger_delay.py --show  # expect enable_gd, delay 1800
  + apply the operator's FINALIZED scint discriminator thresholds (walls/plastics) from the
    Y88-HV ladder before starting.

HV: set the physics operating point below (operator to confirm drift/resist). Held FIXED across
the whole IPD scan (this is NOT an HV scan). Scint PMT bias auto-held at the run_config_beam
(Y88-equalized) setpoints.
"""
from run_config_beam import Config as BeamConfig

# ---- ZS + framing knobs (validated) ----
KSIGMA      = 8.0   # beam k-ladder (2026-07-19): k3 floods, k5=17%/k8=6% Raw plateau (noise
                    # gone, real hits kept); k8 = safe suppressed point for the IPD scan.
                    # FOLLOW-UP: finer k scan 3->8 to map the exact flood cliff / keep-most-hits knee.
ZS_PED_SET  = 'zs_k8_tracer_from_07-18-26_14-06-43'
LATENCY     = 34    # drift-window "tight but safe" for n=32 (was 35). Confirm co-framing run 1.

# ---- IPD scan: one sub-run per IPD, high->low. First point (100) = safe confirm. ----
IPD_LADDER  = [100, 50, 20, 10, 5, 2]
SUBRUN_MIN  = 5     # minutes per IPD point

# ---- Physics HV operating point (operator-set 2026-07-19) ----
# Held FIXED across the IPD scan. Operator: "550 V on all resists, 700 V on all drifts."
# NOTE: det-A drift at 700 V is the drift-window study's target (stage via 650) but has spark
# history (sparked at 800 V beam-on; run_51+ had run A_drift 550-600 V). Ramp A_drift carefully
# under HV monitoring on launch.
RESIST_ABC   = 550   # V, dets A/B/C resist (card 5 ch 1-3)
DET_D_OFFSET = 0     # V, det D resist same as A/B/C (all resists 550 V)
DRIFT_BCD    = 700   # V, drift B/C/D (card 9 ch 1-3)
DRIFT_A      = 700   # V, drift A (card 9 ch 0) — see spark note above


class Config(BeamConfig):
    def _set_defaults(self, config_path=None):
        super()._set_defaults(config_path)  # inherit detectors / FEUs / Y88 plastic HVs

        # ---- rename + re-derive paths ----
        self.run_name = 'zs_doubles_ipdscan'
        self.run_out_dir = f'{self.data_out_dir}{self.run_name}/'
        self.dream_daq_info['run_directory'] = f'/home/mx17/july_dream/dream_run/{self.run_name}/'
        self.dream_daq_info['data_out_dir'] = f'{self.run_out_dir}'
        self.processor_info['run_dir'] = f'{self.run_out_dir}'
        self.hv_info['run_out_dir'] = self.run_out_dir
        self.resume = False

        self.trigger = (f'ZS PHYSICS: scint-DOUBLES OR PS-pickup (run_56 mode; C=or_veto[1], '
                        f'D=OR[0,1], PS leg G&D delay 1800 ns co-frames flash+doubles). DREAM ZS '
                        f'k{KSIGMA}sigma + CM on, Pd off (Option B, offline pedestal subtraction). '
                        f'32 smp x 60 ns, latency {LATENCY}. IPD SCAN {IPD_LADDER} (event rate + '
                        f'corruption vs IPD), HV + trigger + k fixed.')

        # ---- ZS DREAM config (Option B, validated) ----
        self.dream_daq_info.update({
            'daq_config_template_path': f'{self.base_out_dir}dream_config/Tcm_Mx17_July_ZS.cfg',
            'zero_suppress': True,
            'common_noise_subtraction': True,   # firmware CM (mandatory)
            'pedestal_subtraction': False,      # Pd=0 — Option B (offline subtracts; validated)
            'zs_type': 'tpc',
            'zs_check_sample': 4,
            'inter_packet_delay': IPD_LADDER[0],  # default; each sub-run overrides
            'pedestals_dir': f'{self.base_out_dir}pedestals/',
            'pedestals': ZS_PED_SET,
            'latency': LATENCY,                 # 34 — tight-but-safe for n=32
            # n_samples_per_waveform 32, sample_period 60 inherited.
        })

        # ---- IPD-scan sub-runs: fixed HV/trigger/k, only inter_packet_delay changes ----
        def _resist():
            return {'1': RESIST_ABC, '2': RESIST_ABC, '3': RESIST_ABC, '4': RESIST_ABC - DET_D_OFFSET}

        def _drift():
            return {'0': DRIFT_A, '1': DRIFT_BCD, '2': DRIFT_BCD, '3': DRIFT_BCD}

        self.sub_runs = []
        for ipd in IPD_LADDER:
            self.sub_runs.append({
                'sub_run_name': f'zs_k{KSIGMA:g}_dbl_ipd{ipd}',
                'run_time': SUBRUN_MIN, 'post_pause_s': 0,
                'inter_packet_delay': ipd,          # per-sub-run override (flows to effective_info)
                'hvs': {'5': _resist(), '9': _drift()},
            })

        # ---- merge scint PMT bias holds (Y88 set) into every sub-run (same as run_config_beam) ----
        scint_hvs = {}
        for det in self.detectors:
            if det['name'] not in self.included_detectors:
                continue
            if not str(det.get('det_type', '')).startswith('scintillator'):
                continue
            hv_channels = det.get('hv_channels'); setpoint = det.get('hv_setpoint')
            if not isinstance(hv_channels, dict) or setpoint is None:
                continue
            for slot, channel in hv_channels.values():
                scint_hvs.setdefault(str(slot), {})[str(channel)] = setpoint
        for sub_run in self.sub_runs:
            hvs = sub_run.setdefault('hvs', {})
            for slot, chans in scint_hvs.items():
                hvs.setdefault(slot, {}).update(chans)


if __name__ == '__main__':
    config = Config()
    config.write_to_file('config/json_run_configs/run_config_zs_doubles.json')
    dd = config.dream_daq_info
    print('=== ZS doubles physics run (IPD scan) ===')
    print(f"run_name  : {config.run_name}")
    print(f"trigger   : scint-DOUBLES + PS-pickup (set on boards pre-run)")
    print(f"ZS        : k{KSIGMA}sigma  CM={dd['common_noise_subtraction']} Pd={dd['pedestal_subtraction']} "
          f"ZsTyp={dd['zs_type']} ZsChkSmp={dd['zs_check_sample']}  pedestals={dd['pedestals']}")
    print(f"framing   : {dd['n_samples_per_waveform']} smp x {dd['sample_period']} ns, latency {dd['latency']}")
    print(f"HV (fixed): resist A/B/C={RESIST_ABC} D={RESIST_ABC-DET_D_OFFSET}  drift A={DRIFT_A} B/C/D={DRIFT_BCD}   *** CONFIRM ***")
    print(f"IPD scan  : {IPD_LADDER}  ({SUBRUN_MIN} min each)")
    for sr in config.sub_runs:
        print(f"   {sr['sub_run_name']:22s} IPD={sr['inter_packet_delay']:>3}  resist={sr['hvs']['5']}  drift={sr['hvs']['9']}")
    print('\nPRE-RUN (boards free): trigger_mode.py scint --doubles --ps-pickup ; '
          'set_ps_trigger_delay.py --delay 1800 ; apply final scint thresholds.')
    print('Launch: ./start_run.sh run_config_zs_doubles.json  (after boards set + HV confirmed)')
