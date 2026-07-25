#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_flash_recovery.py — flash_random DAQ-recovery resist HV scan with the
mesh charge-injection MODULATED per sub-run (mesh-ON / mesh-OFF pairs), run_65,
2026-07-21. Designed to run IN PARALLEL with the parasitic N1081B trigger scan
(delay + threshold, n1081b/rate_scan_2d.py + timing_delay_scan_v2.py): that scan
only rewrites M1/M2 (walls/plastics) which are OUTSIDE the flash_random trigger
path, and it never touches M4/M6 — so the two runs are board-disjoint except a
single read-only M4 preflight.

WHY flash_random (Mode 2, RUN_MODES_2026-07.md §1.2):
  M4.C = or_veto(lemo4 = M6.D ~666 Hz Poisson pulser) gated by the N93B window;
  M4.D = OR(lemo0 = PS/gamma-flash line, lemo1 = C-out). Singles/Doubles are cut
  at the C inputs, so the DREAM trigger is ONLY the flash (~beam-pulse marker) +
  the veto-gated random pulser. Every event carries the PS pickup pulse, so
  time-since-flash is reconstructable per DREAM event (the high-statistics source
  for the recovery/comb curve — the parallel trigger scan adds the threshold
  dependence at coarser statistics).
  32 samples x 60 ns (1.92 us window), latency 5 (flash peak ~= latency + 8).

  *** ACCEPTANCE WINDOW: the N93B gate now spans ~1 -> 81 ms after the flash
  (start 1 ms, width 80 ms; only the START moved). History: ~30 ms wide ->
  ~5-85 ms on 07-21 -> start 5 ms -> 1 ms on 2026-07-22. The 1 ms start was chosen to match the t > 1 ms thermal gate of
  the GEANT trigger study (MX17_Full_Geant .claude/al_pair_background/
  PLASTIC_THRESHOLD.md), so the measured in-gate trigger rate is directly
  comparable to that study's per-pulse background budget. SCOPE-MEASURED on 2026-07-22 (delay to leading edge 1 ms, allow-pulse width 80 ms -> accept 1-81 ms).
  The N93B has no front panel and no software interface: it is not settable or readable from here, but it IS confirmable after the fact from the DREAM event time-since-flash distribution (the PS/flash pickup is co-framed at 1800 ns), which should show a hard turn-on at 1 ms and turn-off at 81 ms. At ~0.27 Hz beam the wide gate gives ~14-15 Hz accepted
  DREAM rate (was ~5-6 Hz at 30 ms); still far under the n32 DREAM ceiling
  (~312 Hz), so the 666 Hz pulser design is unchanged. ***

MESH MODULATION (this is what n1081b_scan='on' buys us): every HV point is taken
  TWICE, back to back at the same resist/drift and same beam epoch — once
  mesh-ON (sub_run tag 'randOn') and once mesh-OFF ('randOff'). scan_control
  applies config/n1081b_scan_schedule.json['scans'][tag] per sub-run:
    randOn  -> M6.B outputs 0-3 ENABLED  (mesh charge-injection ON)
    randOff -> M6.B outputs 0-3 DISABLED (no injection)
  plus, every sub-run, it re-asserts the flash_random input routing (c_in4 pulser
  ON, c_in0/c_in1 Singles/Doubles OFF, d_in1 C->D ON, d_in0 PS-leg G&D DISABLED).
  Mesh is physically cabled to detectors A and C only; B and D legs are enabled
  but uncabled, so B/D are the same-beam no-mesh control even within a mesh-ON
  sub-run. The mesh in0 G&D delay (1260 ns) is preserved from run_64 (scan_control
  only toggles the OUTPUT status; the snapshot/restore keeps in0 timing).

HV SCAN (single descending pass, MANUAL stop): resist A/B/C/D step DOWN from
  560 V to 520 V in -2.5 V steps (17 points). *** det D = SAME as A/B/C at every
  point *** (user 2026-07-21: 560 is a safe ceiling for D in this gas, so no
  det-D offset this run — unlike run_57/58/64 which ran D 10 V lower). Drift held
  FIXED at 700 V on ALL four detectors (matches run_64's top drift row, so these
  points are directly comparable to the run_64 dr700 grid).
  34 sub-runs (17 points x {mesh-ON, mesh-OFF}) x 5 min = 2.8 h data (~3.4 h wall)
  — chosen to run alongside the ~3 h trigger scan.

SCINT PMT HV: plastics (card 07) + liquids (card 08) held at the 07-19 Y88
  equalized setpoints (merged into every sub-run, inherited from run_config_beam).

PRE-RUN (once, AFTER run_64 stops at a sub-run boundary; boards must be free):
  .venv/bin/python n1081b/trigger_mode.py flash_random
  .venv/bin/python n1081b/set_pulser.py                    # M6.D Poisson 1.5 ms / width 100
  .venv/bin/python n1081b/set_ps_trigger_delay.py --delay 0 --disable
  verify: .venv/bin/python n1081b/trigger_mode.py status   # expect C or_veto lemos=[4],
                                                           #   D lemos=[0,1] -> flash_random
          .venv/bin/python n1081b/set_pulser.py --show     # freq_type=1, period=1500000, width=100
Launch: ./start_run.sh run_config_flash_recovery.json
"""
import os
# --- repo-root shim (run_config_beam/base live one dir up) ---
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from run_config_beam import Config as BeamConfig

RUN_NUM = 65

# ---- DREAM readout (flash_random, Mode 2 — same window as run_57) ----
LATENCY = 5
N_SAMPLES = 32
IPD = 100
SAMPLE_PERIOD = 60

# ---- per-sub-run dwell (minutes) ----
SUBRUN_MIN = float(os.environ.get('SUBRUN_MIN', '5'))

# ---- HV scan ----
DET_D_OFFSET = 0                  # det D = A/B/C this run (560 is a safe D ceiling)
DRIFT_ALL = 700                   # V, drift, all four detectors (card 9 ch 0-3)
RESIST_TOP = 560.0               # V, resist start (A/B/C/D)
RESIST_BOTTOM = 520.0            # V, resist floor (inclusive) — manual stop before here
RESIST_STEP = 2.5                # V, step DOWN
# 560, 557.5, ..., 520  -> 17 points
RESIST_LADDER = [RESIST_TOP - RESIST_STEP * k
                 for k in range(int(round((RESIST_TOP - RESIST_BOTTOM) / RESIST_STEP)) + 1)]

# Per HV point: take mesh-ON then mesh-OFF, back to back, same HV + beam epoch.
MESH_TAGS = ['randOn', 'randOff']


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
        # Per-sub-run mesh modulation via scan_control (randOn/randOff tags in
        # config/n1081b_scan_schedule.json). Function types + base enables are set
        # ONCE pre-run by trigger_mode.py flash_random; scan_control only toggles
        # inputs + the M6.B mesh outputs each sub-run.
        self.n1081b_scan = 'on'

        self.trigger = (
            f'flash_random DAQ-recovery resist HV scan with MESH INJECTION modulated '
            f'per sub-run ({self.run_name}). Mode 2: M4.C = or_veto(lemo4 = M6.D ~666 Hz '
            'Poisson pulser) gated by the N93B window (now ~1->81 ms post-flash, external '
            'setting); M4.D = OR(lemo0 = PS/gamma-flash, lemo1 = C-out). Singles/Doubles '
            'cut at C. 32 smp x 60 ns (1.92 us), latency 5. Every HV point taken twice: '
            'mesh-ON (randOn -> M6.B outs 0-3 enabled) then mesh-OFF (randOff -> M6.B outs '
            'disabled), back to back at the same HV + beam epoch; mesh cabled to A+C only '
            '(B/D uncabled = in-run no-mesh control). Resist A/B/C/D 560->520 V in -2.5 V '
            'steps (17 pts, det D = A/B/C this run), drift 700 V all four (matches run_64 '
            'dr700 row). 34 sub-runs x 5 min, manual stop. Runs parasitically alongside the '
            'N1081B delay + threshold trigger scan (M1/M2 only, outside this trigger path). '
            'Plastic threshold at M2 resident 0.5-MIP set (-65/-78/-86/-83). Scint PMT bias '
            'at 07-19 Y88 equalized setpoints. Ar/Iso 90/10, 3He, no Pb.')

        # flash_random / full-readout window (same as run_57).
        self.dream_daq_info.update({
            'daq_config_template_path': f'{self.base_out_dir}dream_config/Tcm_Mx17_July.cfg',
            'zero_suppress': False,
            'latency': LATENCY,
            'n_samples_per_waveform': N_SAMPLES,
            'sample_period': SAMPLE_PERIOD,
            'inter_packet_delay': IPD,
        })

        # ----- sub-run build: resist OUTER (descending) -> mesh ON/OFF INNER -----
        def _drift():
            return {'0': DRIFT_ALL, '1': DRIFT_ALL, '2': DRIFT_ALL, '3': DRIFT_ALL}  # card 9 ch 0-3

        def _resist(v):
            return {'1': v, '2': v, '3': v, '4': v - DET_D_OFFSET}  # card 5 ch 1-4 = det A/B/C/D

        self.sub_runs = []
        k = 0
        for v in RESIST_LADDER:
            for tag in MESH_TAGS:
                self.sub_runs.append({
                    # NOTE: tag MUST be the leading '_'-token — scan_control maps
                    # sub_run_name.split('_')[0] -> schedule['scans'][tag].
                    'sub_run_name': f'{tag}_r{fmt_v(v)}_dr{DRIFT_ALL}_{k:03d}',
                    'run_time': SUBRUN_MIN, 'post_pause_s': 0,
                    'hvs': {'5': _resist(v), '9': _drift()},
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


if __name__ == '__main__':
    c = Config()
    c.write_to_file('config/json_run_configs/run_config_flash_recovery.json')

    n = len(c.sub_runs)
    data_min = sum(sr['run_time'] for sr in c.sub_runs)
    wall_min = data_min + n * 1.0   # ~1 min/sub-run HV-ramp + inter-sub-run overhead

    print(f'=== {c.run_name} — flash_random DAQ-recovery resist scan, MESH ON/OFF pairs ===')
    print(f'DAQ readout : {N_SAMPLES} smp x {SAMPLE_PERIOD} ns = {N_SAMPLES*SAMPLE_PERIOD} ns, '
          f'latency {LATENCY}, IPD {IPD}, ZS=False')
    print(f'trigger     : flash_random (pulser ~666 Hz gated by N93B ~1-81 ms); mesh modulated')
    print(f'drift       : {DRIFT_ALL} V all four detectors')
    print(f'det D resist: SAME as A/B/C (offset {DET_D_OFFSET} V)')
    print(f'resist ladder ({len(RESIST_LADDER)} pts, -{RESIST_STEP} V): '
          f'{", ".join(fmt_v(v) for v in RESIST_LADDER)}')
    print(f'sub-runs    : {n} ({len(RESIST_LADDER)} pts x mesh ON/OFF) x {SUBRUN_MIN:g} min '
          f'= {data_min:g} min data (~{wall_min/60:.1f} h wall)')
    print('first 6 sub-runs:')
    for sr in c.sub_runs[:6]:
        print(f'  {sr["sub_run_name"]:28s} resist {sr["hvs"]["5"]}  drift {sr["hvs"]["9"]["0"]}')
