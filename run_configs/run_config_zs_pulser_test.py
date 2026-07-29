#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_zs_pulser_test.py — first PRODUCTION-path zero-suppression test, driven by the
RANDOM PULSER (2026-07-19).

Purpose: exercise the full ZS DAQ + processing chain end-to-end WITHOUT needing the
scintillator trigger boards (M1/.240, M2/.241, M5/.244) — those are held by the live
plastic-threshold ladder. The random-pulser + gamma-flash trigger lives on M4/.243 +
M6/.245, so this test runs concurrently with the threshold scan. The pulser trigger is
ALREADY applied on the boards from run_57 (flash_random) — this config changes only the
DREAM side (ZS on), reusing that trigger. Stop run_57 (DREAM contention: one RunCtrl at a
time), then launch this; the threshold ladder keeps running on its own boards.

What it validates (see docs/live_zs_run_sources_2026-07-19.md, "ZS enablement"):
  1. The prepped k8 threshold set loads       — via the ZS cfg template's per-FEU ZsFile
     lines + get_pedestals staging (was the production GAP 1).
  2. Zero-suppression actually suppresses      — pulser events read flat pedestal, so with
     ZS on they must shrink to ~empty (just tracer channels); flash events keep hits.
  3. Sparse ZS FDF decodes + processes cleanly — decoder auto-detects ZS; check hit maps.
  4. No pedestal DOUBLE-subtraction            — Option B below keeps offline pedestal
     subtraction in the processor (GAP 2). If hits look pedestal-shifted / garbage, flip
     to Option A (pedestal_subtraction=True + processor pedestal-skip) — see the doc.

ZS SCHEME — Option B (operator-preferred, offline pedestal subtraction; NEEDS TESTING):
  firmware  ZS=1, CM=1, Pd=0   (Feu_RunCtrl_ZS/CM/Pd)
  offline   processor subtracts the pedestal ROOT as today (unchanged), --cns 0.
  Loads both PdFile + ZsFile (ZS cfg template) so the firmware has pedestals for the ZS
  threshold reference even with the Pd OUTPUT bit off. If the FEU cannot zero-suppress with
  Pd=0 (no suppression / full-size pulser events), switch to Option A (Pd=1 + processor
  change). This is exactly the open question this test answers.

KEEP: 32 samples x 60 ns, latency 5 (pulser value — flash frames at latency+8; pulser
events are uncorrelated so latency is irrelevant to them). IPD = 2 (ZS-safe, SSD path).

HV: sub-runs carry NO resist/drift setpoints, so HV is LEFT wherever run_57 left it
(operator will set drifts/resist for the real physics run). Scint PMT bias is auto-held.

Pre-launch (see PLAN doc): the k8 set already exists
(~/beam_july/pedestals/zs_k8_tracer_from_07-18-26_14-06-43, from prep_zs_thresholds.py).
Regenerate the JSON (python run_config_zs_pulser_test.py) ONLY after run_57 is stopped.
"""
# --- repo-root shim (run_config_beam/base live one dir up) ---
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from run_config_beam import Config as BeamConfig

ZS_PED_SET = 'zs_k8_tracer_from_07-18-26_14-06-43'  # from dream_scripts/prep_zs_thresholds.py --k 8
N_TEST_SUBRUNS = 1
TEST_SUBRUN_MIN = 3.0


class Config(BeamConfig):
    def _set_defaults(self, config_path=None):
        # Inherit ALL detector / FEU / scintillator wiring from the beam config.
        super()._set_defaults(config_path)

        # ---- rename + re-derive every path that depends on run_name ----
        import os as _os
        _paced = _os.environ.get('PACED_RATE_HZ')
        self.run_name = f'zs_paced_{_paced}hz' if _paced else 'zs_flashoff_pulser'
        # static veto-open trigger (setup_fulltime_trigger.py) already holds M4.C open;
        # scan watcher not needed and not running -> 'off' so daq_control doesn't fail-closed.
        self.n1081b_scan = 'off'
        self.run_out_dir = f'{self.data_out_dir}{self.run_name}/'
        self.dream_daq_info['run_directory'] = f'/home/mx17/july_dream/dream_run/{self.run_name}/'
        self.dream_daq_info['data_out_dir'] = f'{self.run_out_dir}'
        self.processor_info['run_dir'] = f'{self.run_out_dir}'
        self.hv_info['run_out_dir'] = self.run_out_dir

        self.resume = False
        self.trigger = ('ZS TEST (random pulser + gamma flash, reuses run_57 flash_random '
                        'trigger on M4/M6). DREAM in ZERO-SUPPRESSION: k8 + IPD 2, CM on, '
                        'Pd off (Option B, offline pedestal subtraction). 32 smp x 60 ns, '
                        'latency 5. Scint trigger boards untouched (held by threshold ladder).')

        # ---- ZS DREAM config (overrides run_57's Raw dream_daq_info) ----
        self.dream_daq_info.update({
            'daq_config_template_path': f'{self.base_out_dir}dream_config/Tcm_Mx17_July_ZS.cfg',
            'zero_suppress': True,           # Sys DaqRun Mode ZS / Feu_RunCtrl_ZS = 1
            'common_noise_subtraction': True,   # Feu_RunCtrl_CM = 1 (mandatory per ZS study)
            'pedestal_subtraction': False,      # Feu_RunCtrl_Pd = 0 (Option B — offline subtracts)
            'zs_type': 'tpc',                # Feu_RunCtrl_ZsTyp = 1
            'zs_check_sample': 4,            # Feu_RunCtrl_ZsChkSmp = 4
            'inter_packet_delay': 2,         # Feu_InterPacket_Delay = 2 (ZS-safe, SSD path)
            'pedestals_dir': f'{self.base_out_dir}pedestals/',
            'pedestals': ZS_PED_SET,         # explicit prepped k8 set (NOT 'latest')
            # n_samples_per_waveform 32, sample_period 60, latency 5 inherited from run_57.
        })

        # ---- short test sub-runs; NO resist/drift hvs -> HV left as run_57 left it ----
        self.sub_runs = []
        for k in range(N_TEST_SUBRUNS):
            self.sub_runs.append({
                'sub_run_name': f'zs_k8_ipd2_pulser_{k:02d}',
                'run_time': TEST_SUBRUN_MIN, 'post_pause_s': 0,
                'hvs': {},   # scint PMT holds get merged in below; resist/drift untouched
            })

        # Re-merge the scintillator PMT bias holds onto the NEW sub_runs (BeamConfig did this
        # for its own sub_runs before we replaced them). Same logic as run_config_beam.
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
    config = Config()
    config.write_to_file('config/json_run_configs/run_config_zs_pulser_test.json')

    dd = config.dream_daq_info
    print('=== ZS pulser test config ===')
    print(f"run_name      : {config.run_name}")
    print(f"template       : {dd['daq_config_template_path']}")
    print(f"zero_suppress  : {dd['zero_suppress']}   CM={dd['common_noise_subtraction']}  "
          f"Pd={dd['pedestal_subtraction']}  ZsTyp={dd['zs_type']}  ZsChkSmp={dd['zs_check_sample']}")
    print(f"IPD            : {dd['inter_packet_delay']}   latency={dd['latency']}   "
          f"n_samples={dd['n_samples_per_waveform']}  sample_period={dd['sample_period']}")
    print(f"pedestals      : {dd['pedestals']}  (dir {dd['pedestals_dir']})")
    print(f"sub_runs       : {len(config.sub_runs)} x {TEST_SUBRUN_MIN} min "
          f"(HV left as-is; scint PMT holds merged)")
    for sr in config.sub_runs:
        print(f"   {sr['sub_run_name']:24s} hvs cards={sorted(sr['hvs'])}")
    print('\nLaunch (ONLY after stopping run_57):  ./start_run.sh run_config_zs_pulser_test.json')
