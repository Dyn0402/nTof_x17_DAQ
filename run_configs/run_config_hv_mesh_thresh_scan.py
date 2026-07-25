#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_hv_mesh_thresh_scan.py — run_67, 2026-07-22.
PS + SINGLES trigger, RAW at IPD 5, 2D HV scan (mesh ON throughout) x GEANT-optimised
PLASTIC THRESHOLD ladder (1.41 / 1.13 / 0.90 MIP, descending from the optimum).

    .venv/bin/python run_config_hv_mesh_thresh_scan.py --recon   # 3 x 3 min, ~12 min
    .venv/bin/python run_config_hv_mesh_thresh_scan.py           # full 36 sub-runs

RECON ALREADY RUN (2026-07-22 19:00, run_67_recon): 1.41 MIP -> 64.2 ev/spill,
1.13 -> 76.8, 0.90 -> 77.1, all with ~0 eventId discontinuities. Rate is FLAT below
1.13 MIP, so the plastic threshold has stopped being the rate-controlling leg. Ladder
kept as-is (user: saturating is the point — it tests whether tracking efficiency holds).

WHY IPD 5 (2026-07-22 10 GbE upgrade):
  On 10 GbE, RAW n32 is CLEAN at every IPD from 100 down to 1 (eventId gaps
  0.000-0.041%) — the corruption threshold that sat at IPD 75 on 1 GbE does not
  exist any more in the settable range. Yield PLATEAUS at ~95 ev/spill from
  IPD 5 -> 1 while the modelled readout cycle still falls 1.68x, so the DAQ is now
  recording essentially every trigger the beam offers: we are TRIGGER-limited, not
  link- or FEU-limited. IPD 5 sits on that plateau with margin.
  Refs: docs/network_upgrade_10g/results_2026-07-22_switch_swap.md,
        docs/network_upgrade_10g/04_bandwidth_model.md (311 kB/event measured).
  DO NOT quote `band/spill` from those ladders — moving-anchor artefact.

TRIGGER (routing set ONCE pre-run; scan_control only modulates thresholds + mesh):
  scint --singles --ps-pickup: M4.C = or_veto(Singles, lemo0) gated by the N93B
  window, now ~1 -> 81 ms after the flash (start moved 5 -> 1 ms on 2026-07-22,
  width unchanged at 80 ms). The 1 ms start was chosen so the measured in-gate
  trigger rate is directly comparable to the GEANT study's t > 1 ms thermal gate.
  Delay + width were SCOPE-MEASURED 2026-07-22 (1 ms delay, 80 ms width).
  The N93B has no front panel and no software interface — not settable or readable
  from here, but confirmable after the fact from the event time-since-flash
  distribution (PS/flash co-framed at 1800 ns): hard turn-on 1 ms, turn-off 81 ms.
  M4.D = OR(lemo0 = PS/gamma-flash, lemo1 = C-out). PS + singles CO-FRAMED in
  32 smp (run_56 recipe): latency 35, M4.D in0 G&D delay 1800 ns pulls the flash
  from sample 43 to ~13, beside the singles MM at lat-24 ~11 -> per-event
  time-since-flash. 32 smp x 60 ns (1.92 us), IPD 5, RAW (zero_suppress=False).

PLASTIC THRESHOLD LADDER — descending from the GEANT optimum, never above it.
  Source: ~/CLionProjects/MX17_Full_Geant/.claude/al_pair_background/
  PLASTIC_THRESHOLD.md (2026-07-22, commit 700a81a). At the post-upgrade ~24
  events/pulse thermal-band budget the optimum plastic threshold is 1.41 MIP
  (~4.9 MeV): IPC efficiency 4.5%, ~4.8x more IPC recorded than 0.5 MIP at the
  same budget. Its MIP definition (3.47 MeV, 2 cm) agrees with our Y88 scale
  (3.4 MeV -> 152 mV fleet), so MIP fractions convert directly.

  We scan the optimum and DOWNWARD only (user 2026-07-22: "always push the DAQ's
  capabilities ... only go lower to get more events"). Above the optimum you buy
  no throughput and pay IPC efficiency; below it the DAQ saturates but every point
  is still usable, so the run is stop-anywhere.

  Per-sector mV = fraction x per-arm mean mip_peak from
  calibrations/pss/mip_thresholds_y88.json (A 131 / B 154 / C 174 / D 149;
  D1 REPAIRED so D uses both bars), same convention as run_66:
     1.41 MIP (optimum)  A:-185  B:-217  C:-245  D:-210   ~24 trig/pulse predicted
     1.13 MIP (-20%)     A:-148  B:-174  C:-196  D:-168   ~63 trig/pulse predicted
     0.90 MIP (-40%)     A:-118  B:-139  C:-157  D:-134  ~120 -> DAQ-capped ~95
  Walls (M1) held at 0.5 MIP (25/35/34/36) — the GEANT study fixes the SiPM leg at
  0.5 MIP, and its trigger model is ">=1 leg", which is our Singles. Not scanned.

  *** EXPECT the predicted rates to come in LOW, and measure rather than assume. ***
  GEANT predicts 202 trig/pulse at 0.5 MIP; we MEASURED the trigger plateau at
  ~95 ev/spill at that same standing 0.5-MIP threshold — a factor ~2. Plausible
  causes: the old 5 ms gate cutting the 1-5 ms slice (now restored by the 1 ms
  start, which pushes back the other way), and our Singles being a per-sector
  wall AND plastic coincidence vs the sim's looser ">=1 leg". If the factor is
  real, 1.41 MIP lands nearer ~11/spill and we are already UNDER budget at the
  optimum — which is exactly why the ladder only goes down. The recon settles it.

RECON (--recon): 3 sub-runs x 3 min at drift 600 / resist 530 / mesh ON, one per
  threshold, ~12 min total. Deliverable = measured ev/spill vs threshold under the
  NEW 1 ms gate. Read it as: which threshold puts the in-gate rate at the ~24
  events/pulse budget? If 1.41 MIP already reads well under 24, shift the whole
  ladder down before launching the main run. Costs 12 min, de-risks 13 h.
  ALWAYS state the window start alongside any band number (see memory
  band-optimisation-window-start) — these numbers are 1-ms-start numbers.

FEU WATERMARK: Main_Trig_OvrWrnHwm = 2 (Lwm 1) on every sub-run, vs the cfg template
  20 / RunCtrl cap ~11. The FEU asserts OverflowWarning -> BUSY at 2 queued triggers, so
  the post-flash burst cannot seize the readout and events spread more evenly across the
  window. Measured on the 07-22 beam scan: Hwm<=4 gives +13% in the 3-8 ms band at ~-7%
  total yield. The yield cost is ACCEPTED (user 2026-07-22) — evenness is the goal here.
  RunCtrl's clamp only pulls values DOWN to its cap, so 2 applies as written; verify in
  the archived per-sub-run cfg regardless (Feu * Main_Trig_OvrWrnHwm 2).

MESH: ON for the whole run, and NOT switched. This is a HARD CONSTRAINT, not a choice —
  M6.B's fan-out drives the Micromegas mesh switches AND holds up the SiPM wall bias
  (proven 2026-07-22: disabling M6.B's lemo outputs stops the mesh switch, and disabling
  M6.B's outputs collapses all four walls to ~1/40 gain -> ~1 event/pulse, 61x in DREAM
  rate). So **the mesh cannot be switched off without switching off the walls**, and there
  is NO mesh ON/OFF axis available to a Singles-triggered run until that coupling is traced.
  Full story: docs/HANDOFF_2026-07-22_m6_enable_layers.md.
  The no-mesh control is instead **detectors B and D**, which are uncabled for mesh
  injection and are therefore a same-beam, same-HV, FULL-GAIN control inside every sub-run.
  The schedule tags m{141,113,090}On carry NO mesh_b target, so scan_control never touches
  M6 — deliberate, per the standing "do not touch M6 until 2026-07-23" decision.

HV GRID: drift {600, 700, 500} x resist {540, 530, 520, 510}, det D = A/B/C.
  All inside already-exercised territory: run_64 scanned drift 700->200 and resist
  to 570; run_57/58 ran resist to 580 in this same 90/10 gas. (The 07-17 beam-off
  resist ceilings of ~490 V were measured in 95/5; the Garfield 90/10 equivalence
  is +72 V -> ~562 V, so 540 has margin. See memory hv-stress-test-2026-07-17.)

ORDERING — drift OUTER -> resist -> threshold INNER:
  * mesh is ON throughout and never switched — the A/C-vs-B/D split IS the mesh control
  * threshold triplet inside one HV point = same-HV comparison over ~60 min
  * 12 HV settings visited ONCE each instead of 36 revisits (fewer ramps)
  * drift order 600 -> 700 -> 500 and threshold order 1.41 -> 1.13 -> 0.90 make every
    truncation graceful: stop after 2.2 h and you have a COMPLETE resist x threshold
    map at our nominal drift 600, with the optimum threshold taken first everywhere.
    Resist descends 540->510 within a block (single-pass, no ping-pong).
  4 drift x 7 resist x 3 thr = 84 sub-runs x 10 min = 14.0 h data (~15.4 h wall).
  Blocks: drift 600 / 700 / 500 = 63 sub-runs = 11.6 h; the drift-400 block adds the
  rest. Requested >= 12 h with "one more drift point if there's time" — putting 400 last
  means stopping any time after ~10:15 still leaves three COMPLETE drift blocks.

DISK (measured, not modelled — from raw_ipd_10g_low/ipd005): at the full 95 ev/spill
  a 10-min sub-run writes ~10.4 GB to the HDD (~/beam_july IS a symlink to
  /mnt/data/x17/beam_july) and stages ~6.0 GB of .fdf on the SSD
  (~/july_dream/dream_run). Scaling by expected ev/spill, this run writes ~250-480 GB
  to the HDD (worst case 750 GB if every point saturates) against 1.4 TB free -> the
  HDD is fine for this run and fills in ~25-73 h of continuous RAW at IPD 5.
  *** The SSD staging is the binding constraint and needs clearing during the run
  (space_manager.py, SSD -> HDD -> EOS verified). Operator-managed. ***

SCINT PMT HV: plastics (card 07) + liquids (card 08) at 07-19 Y88 equalized setpoints.

PRE-RUN handoff (boards must be free; scan_control sets thresholds+mesh per sub-run):
  .venv/bin/python n1081b/trigger_mode.py scint --singles --ps-pickup
  .venv/bin/python n1081b/set_ps_trigger_delay.py --delay 1800
  .venv/bin/python n1081b/set_mesh_injection.py on
  verify: trigger_mode.py status          -> C or_veto lemos=[0], D lemos=[0,1]
          set_ps_trigger_delay.py --show  -> delay 1800, enable_gd True, invert False
          set_mesh_injection.py status    -> in0 G&D 1260 ns, outs 0-3 on
  (N93B needs no action: scope-measured 1 ms delay / 80 ms width on 2026-07-22.
   Confirm post-hoc from the event time-since-flash distribution, not by hand.)
  (M2 plastic thresholds are NOT pre-set — the first sub-run applies 1.41 MIP.)
Launch: ./start_run.sh run_config_hv_mesh_thresh_scan.json
        ./start_run.sh run_config_hv_mesh_thresh_scan_recon.json   (recon first)
TEARDOWN: scan_control does not restore the section threshold on exit (it is left at
  0.90 MIP); re-apply the standing 0.5-MIP plastic set by hand afterwards.
"""
import os
import sys

# --- repo-root shim (run_config_beam/base live one dir up) ---
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from run_config_beam import Config as BeamConfig

RUN_NUM = 67
RECON = '--recon' in sys.argv

# ---- DREAM readout (PS+singles co-framed in 32 smp -- run_56 recipe, IPD 5) ----
LATENCY = 35
N_SAMPLES = 32
IPD = 5
SAMPLE_PERIOD = 60

SUBRUN_MIN = float(os.environ.get('SUBRUN_MIN', '3' if RECON else '10'))

# ---- scan axes ----
# ---- FEU trigger-FIFO watermark (Main_Trig_OvrWrnHwm / OvrWrnLwm) ----
# Hwm 2 (user 2026-07-22): makes the FEU assert OverflowWarning -> BUSY early, so the
# post-flash burst cannot seize the readout and events spread more evenly over the
# window. Costs total yield (~7-12% measured on the 07-22 beam scan) — accepted.
# RunCtrl's clamp is ONE-DIRECTIONAL (it only pulls the cfg value DOWN to its cap), so a
# value BELOW the cap applies as written. Lwm is the hysteresis partner and must be < Hwm.
OVR_WRN_HWM = 2
OVR_WRN_LWM = 1

DET_D_OFFSET = 0                     # det D resist = A/B/C
# 600 first = nominal, then 700, 500; 400 is the "if there's time" 4th block and is
# LAST so it truncates cleanly. NOT 800: A_drift sparked at its nominal 800 V with beam
# on (2026-07-17 sequel; run_51 had to drop A to 550), so the range is extended DOWNWARD
# instead — run_64 already scanned drift 700->200 without incident.
DRIFT_LADDER = [600, 700, 500, 400]
# 5 V steps 550 -> 520 (7 points, descending single pass). 550 is inside exercised
# territory: run_64 scanned resist to 570 and run_57/58 to 580 in this same 90/10 gas.
RESIST_LADDER = [550, 545, 540, 535, 530, 525, 520]
MIP_LEVELS = ['141', '113', '090']   # optimum first, then DOWN only
# MESH: mesh-OFF sub-runs are VOID for a Singles-triggered run — mesh-OFF collapses the
# SiPM wall gain ~40x (docs/HANDOFF_2026-07-22_sipm_wall_dropouts.md), killing the wall
# leg of the wall AND plastic coincidence, so the only surviving trigger is the PS/flash:
# ~1 event/pulse. MEASURED on run_67 2026-07-22: m141On 174 MB/min vs m141Off 6.2 MB/min
# = 28x. So we run mesh ON throughout; the no-mesh control comes from B/D, which are
# uncabled and therefore a same-beam, same-HV, FULL-wall-gain control inside every sub-run.
MESH_STATES = ['On']

# Recon: one point per threshold at the nominal HV, mesh ON.
RECON_DRIFT, RECON_RESIST = 600, 530


def fmt_v(v):
    return f'{v:g}'


class Config(BeamConfig):
    def _set_defaults(self, config_path=None):
        super()._set_defaults(config_path)

        self.run_name = f'run_{RUN_NUM}_recon' if RECON else f'run_{RUN_NUM}'
        self.run_out_dir = f'{self.data_out_dir}{self.run_name}/'
        self.dream_daq_info['run_directory'] = f'/home/mx17/july_dream/dream_run/{self.run_name}/'
        self.dream_daq_info['data_out_dir'] = f'{self.run_out_dir}'
        self.processor_info['run_dir'] = f'{self.run_out_dir}'
        self.hv_info['run_out_dir'] = self.run_out_dir
        # 2026-07-22 22:45: run_67 was CLEARED and restarted from scratch (the three
        # sub-runs taken earlier were all compromised — half-beam, or taken while the
        # mesh circuit was off and the SiPM walls collapsed). resume=False so the full
        # 36-point grid is retaken.
        self.resume = False
        # Per-sub-run plastic THRESHOLD + mesh modulation via scan_control
        # (m{141,113,090}{On,Off} tags in config/n1081b_scan_schedule.json).
        # scint+ps routing + PS delay 1800 + mesh in0 delay are set ONCE pre-run.
        self.n1081b_scan = 'on'

        thr_txt = ('plastic threshold per sub-run via scan_control m{141,113,090}{On,Off} '
                   '(M2 per-sector + M6.B mesh): 1.41 MIP A-185/B-217/C-245/D-210 (GEANT '
                   'optimum @ ~24 ev/pulse budget), 1.13 A-148/B-174/C-196/D-168, 0.90 '
                   'A-118/B-139/C-157/D-134 — optimum-and-DOWN only. Walls 0.5 MIP '
                   '(25/35/34/36).')
        common = (
            'scint --singles --ps-pickup, PS+singles co-framed in 32 smp (latency 35, '
            'M4.D in0 G&D 1800 ns -> flash ~smp 13, MM ~smp 11); N93B acceptance window '
            '~1->81 ms post-flash (start moved 5->1 ms 2026-07-22 to match the GEANT '
            't>1ms thermal gate, width 80 ms). 32 smp x 60 ns, IPD 5, RAW. ' + thr_txt +
            ' Scint PMT bias at 07-19 Y88 setpoints. Ar/Iso 90/10, 3He, no Pb.')

        if RECON:
            self.trigger = (
                f'RECON for run_{RUN_NUM} ({self.run_name}): plastic-threshold rate '
                f'reconnaissance, 3 x {SUBRUN_MIN:g} min at drift {RECON_DRIFT} / resist '
                f'{RECON_RESIST} / mesh ON, one per threshold. Measures ev/spill vs '
                f'threshold under the NEW 1 ms gate start to place the ladder before the '
                f'full 13 h scan. ' + common)
        else:
            self.trigger = (
                f'PS + SINGLES trigger, 2D HV scan x mesh on/off x GEANT-optimised PLASTIC '
                f'THRESHOLD ladder ({self.run_name}). ' + common +
                f' HV: drift {DRIFT_LADDER} x resist {RESIST_LADDER} (det D = A/B/C), mesh '
                f'circuit FULLY OFF (M6.B ins+outs all disabled); order drift outer -> resist -> threshold; '
                f'3 drift x 4 resist x 3 thr = 36 sub-runs x 10 min. NO mesh axis: M6.B '
                f'drives the mesh switches AND holds up the SiPM wall bias, so mesh-off '
                f'kills the walls (61x) — B/D (uncabled) are the in-run no-mesh control.')

        self.dream_daq_info.update({
            'daq_config_template_path': f'{self.base_out_dir}dream_config/Tcm_Mx17_July.cfg',
            'zero_suppress': False,
            'latency': LATENCY,
            'n_samples_per_waveform': N_SAMPLES,
            'sample_period': SAMPLE_PERIOD,
            'inter_packet_delay': IPD,
        })

        def _drift(dv):
            return {'0': dv, '1': dv, '2': dv, '3': dv}             # card 9 ch 0-3

        def _resist(v):
            return {'1': v, '2': v, '3': v, '4': v - DET_D_OFFSET}  # card 5 ch 1-4

        def _add(tag, dv, rv, k):
            # tag MUST be the leading '_'-token — scan_control keys on it.
            self.sub_runs.append({
                'sub_run_name': f'{tag}_dr{fmt_v(dv)}_r{fmt_v(rv)}_{k:03d}',
                'run_time': SUBRUN_MIN, 'post_pause_s': 0,
                'ovr_wrn_hwm': OVR_WRN_HWM, 'ovr_wrn_lwm': OVR_WRN_LWM,
                'hvs': {'5': _resist(rv), '9': _drift(dv)},
            })

        self.sub_runs = []
        if RECON:
            for k, lvl in enumerate(MIP_LEVELS):
                _add(f'm{lvl}On', RECON_DRIFT, RECON_RESIST, k)
        else:
            k = 0
            for dv in DRIFT_LADDER:            # outer: fewest HV transitions
                for rv in RESIST_LADDER:       # descending single pass
                    for lvl in MIP_LEVELS:     # optimum first, then down
                        for mo in MESH_STATES:  # inner: adjacent same-beam pair
                            _add(f'm{lvl}{mo}', dv, rv, k)
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
    suffix = '_recon' if RECON else ''
    out = f'config/json_run_configs/run_config_hv_mesh_thresh_scan{suffix}.json'
    c.write_to_file(out)

    n = len(c.sub_runs)
    data_min = sum(sr['run_time'] for sr in c.sub_runs)
    kind = 'RECON' if RECON else 'FULL'
    print(f'=== {c.run_name} — PS+singles, HV x mesh x plastic-threshold scan [{kind}] ===')
    print(f'wrote       : {out}')
    print(f'DAQ readout : {N_SAMPLES} smp x {SAMPLE_PERIOD} ns, latency {LATENCY}, IPD {IPD}, '
          f'ZS=False; PS delay 1800 (co-framed); N93B window ~1->81 ms')
    print(f'thresholds  : 1.41 / 1.13 / 0.90 MIP (optimum-and-down) via scan_control '
          f'm{{141,113,090}}{{On,Off}}')
    if RECON:
        print(f'HV (fixed)  : drift {RECON_DRIFT} / resist {RECON_RESIST}, mesh ON')
    else:
        print(f'HV grid     : drift {DRIFT_LADDER} x resist {RESIST_LADDER} (det D = A/B/C); '
              f'mesh ON throughout, never switched (B/D = in-run no-mesh control)')
        print(f'order       : drift outer -> resist (desc) -> threshold (desc)')
    print(f'sub-runs    : {n} x {SUBRUN_MIN:g} min = {data_min/60:.1f} h data '
          f'(~{(data_min + n)/60:.1f} h wall)')
    print(f'first {min(6, n)} sub-runs:')
    for sr in c.sub_runs[:6]:
        print(f'  {sr["sub_run_name"]:28s} resist {sr["hvs"]["5"]["1"]}  drift {sr["hvs"]["9"]["0"]}')
