#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_ps_singles_threshold_scan.py — PS + SINGLES trigger, PLASTIC THRESHOLD scan
(0.5/1.0/1.5/2.0 MIP) x 2D HV micro-scan x mesh on/off, run_66, 2026-07-22.

Single run: the plastic discriminator threshold is now modulated PER SUB-RUN by
scan_control (n1081b_scan='on') via the new `threshold` field added to
n1081b_scan_watcher._apply_channel (2026-07-22) — the same set_input_configuration call
ThresholdRig uses, safe in scint mode. Tags m{05,10,15,20}{On,Off} carry both the
4-sector M2 threshold set for that MIP level AND the mesh on/off state.

TRIGGER (routing set ONCE pre-run; scan_control only modulates thresholds + mesh):
  scint --singles --ps-pickup: M4.C = or_veto(Singles, lemo0) gated by the ~1->81 ms N93B
  window (start moved 5 -> 1 ms on 2026-07-22 to match the GEANT study's t > 1 ms
  thermal gate; width still 80 ms); M4.D = OR(lemo0 = PS/gamma-flash, lemo1 = C-out).
  NOTE: run_66's four taken sub-runs predate that change (5 ms start).
  PS + singles CO-FRAMED
  in 32 smp (run_56 recipe): latency 35, M4.D in0 G&D delay 1800 ns pulls the flash from
  sample 43 to ~13, beside the singles MM at lat-24 ~11 -> per-event time-since-flash.
  32 smp x 60 ns (1.92 us), IPD 90, RAW (zero_suppress=False).

PLASTIC THRESHOLD SCAN (outer loop, 4 levels; per-sector, D1 REPAIRED -> D uses both
  bars) from mip_thresholds_y88.json per-arm mip_peak avg:
    0.5 MIP  A:-66  B:-77  C:-87  D:-74
    1.0 MIP  A:-131 B:-154 C:-174 D:-149
    1.5 MIP  A:-196 B:-232 C:-261 D:-224
    2.0 MIP  A:-262 B:-309 C:-348 D:-298
  Walls (M1) held at 0.5 MIP (25/35/34/36) — not scanned here; set pre-run if needed.

MESH: ON/OFF modulated per point (M6.B outs, A+C cabled; B/D = in-run no-mesh control).
  Re-enabled for this run (reverses the earlier run_66 'mesh off'): handoff runs
  set_mesh_injection.py on (in0 G&D 1260 ns + outputs); scan_control toggles outputs.

HV MICRO-SCAN (coarse, inside each MIP level): drift {600,400} x resist {530,520}
  (det D = A/B/C), = 4 points. mesh On/Off at each -> 8 sub-runs per MIP level.
  4 MIP x 4 HV x 2 mesh = 32 sub-runs x 10 min = 5.3 h data (~5.9 h wall).
  Order: MIP outer -> HV (drift outer, resist inner) -> mesh inner.

SCINT PMT HV: plastics (card 07) + liquids (card 08) at 07-19 Y88 equalized setpoints.

PRE-RUN handoff (boards currently flash_random; scan_control sets thresholds+mesh, so no
  flash_random threshold dance needed):
  n1081b/trigger_mode.py scint --singles --ps-pickup
  n1081b/set_ps_trigger_delay.py --delay 1800
  n1081b/set_mesh_injection.py on
  verify: trigger_mode.py status (C or_veto lemos=[0], D lemos=[0,1]);
          set_ps_trigger_delay.py --show (delay 1800, enable_gd True);
          set_mesh_injection.py status (in0 1260 ns, outs on)
  (M2 plastic thresholds are NOT pre-set — the first sub-run m05On applies 0.5 MIP.)
Launch: ./start_run.sh run_config_ps_singles_threshold_scan.json
TEARDOWN: scan_control does not restore the section threshold on exit (left at 2.0 MIP);
  re-apply the standing plastic set by hand after the run (in flash_random via
  threshold_ladder --apply-plastic, or set_mesh_injection/trigger_mode as next run needs).
"""
import os
# --- repo-root shim (run_config_beam/base live one dir up) ---
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from run_config_beam import Config as BeamConfig

RUN_NUM = 66

# ---- DREAM readout (PS+singles co-framed in 32 smp -- run_56 recipe) ----
LATENCY = 35
N_SAMPLES = 32
IPD = 90
SAMPLE_PERIOD = 60

SUBRUN_MIN = float(os.environ.get('SUBRUN_MIN', '10'))

# ---- scan axes ----
DET_D_OFFSET = 0                       # det D = A/B/C
DRIFT_LADDER = [600, 400]              # V, common to all four (card 9 ch 0-3)
RESIST_LADDER = [530, 520]             # V, det A/B/C/D (card 5 ch 1-4)
MIP_LEVELS = ['05', '10', '15', '20']  # tag suffixes -> schedule m{lvl}{On,Off}
MESH_STATES = ['On', 'Off']            # mesh inner, adjacent on/off at same HV+beam


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
        self.resume = True   # RESUME after 2026-07-22 beam-off stop (skips 000-003, retakes from 004)
        # Per-sub-run plastic THRESHOLD + mesh modulation via scan_control (m{lvl}{On,Off}
        # tags). scint+ps routing + PS delay 1800 + mesh in0 are set ONCE pre-run.
        self.n1081b_scan = 'on'

        self.trigger = (
            f'PS + SINGLES trigger, PLASTIC THRESHOLD scan (0.5/1.0/1.5/2.0 MIP) x 2D HV '
            f'micro-scan x mesh on/off ({self.run_name}). scint --singles --ps-pickup, PS+'
            'singles co-framed in 32 smp (latency 35, M4.D in0 G&D 1800 ns -> flash ~smp 13, '
            'MM ~smp 11). 32 smp x 60 ns, IPD 90, RAW. Plastic threshold modulated per '
            'sub-run by scan_control (m{05,10,15,20}{On,Off} tags -> M2 per-sector threshold '
            '+ M6.B mesh): 0.5 MIP A-66/B-77/C-87/D-74, 1.0 A-131/B-154/C-174/D-149, 1.5 '
            'A-196/B-232/C-261/D-224, 2.0 A-262/B-309/C-348/D-298 (D1 repaired). Walls 0.5 '
            'MIP (25/35/34/36). HV: drift {600,400} x resist {530,520} (det D = A/B/C), mesh '
            'On/Off at each; 4 MIP x 4 HV x 2 mesh = 32 sub-runs x 10 min. Scint PMT bias at '
            '07-19 Y88 setpoints. Ar/Iso 90/10, 3He, no Pb.')

        self.dream_daq_info.update({
            'daq_config_template_path': f'{self.base_out_dir}dream_config/Tcm_Mx17_July.cfg',
            'zero_suppress': False,
            'latency': LATENCY,
            'n_samples_per_waveform': N_SAMPLES,
            'sample_period': SAMPLE_PERIOD,
            'inter_packet_delay': IPD,
        })

        # ----- sub-run build: MIP outer -> HV (drift outer, resist inner) -> mesh inner -----
        def _drift(dv):
            return {'0': dv, '1': dv, '2': dv, '3': dv}             # card 9 ch 0-3

        def _resist(v):
            return {'1': v, '2': v, '3': v, '4': v - DET_D_OFFSET}  # card 5 ch 1-4

        self.sub_runs = []
        k = 0
        for lvl in MIP_LEVELS:
            for dv in DRIFT_LADDER:
                for rv in RESIST_LADDER:
                    for mo in MESH_STATES:
                        tag = f'm{lvl}{mo}'   # MUST be the leading '_'-token (scan_control)
                        self.sub_runs.append({
                            'sub_run_name': f'{tag}_dr{fmt_v(dv)}_r{fmt_v(rv)}_{k:03d}',
                            'run_time': SUBRUN_MIN, 'post_pause_s': 0,
                            'hvs': {'5': _resist(rv), '9': _drift(dv)},
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
    c.write_to_file('config/json_run_configs/run_config_ps_singles_threshold_scan.json')
    n = len(c.sub_runs)
    data_min = sum(sr['run_time'] for sr in c.sub_runs)
    print(f'=== {c.run_name} — PS+singles, plastic THRESHOLD scan x HV x mesh ===')
    print(f'DAQ readout : {N_SAMPLES} smp x {SAMPLE_PERIOD} ns, latency {LATENCY}, IPD {IPD}, '
          f'ZS=False; PS delay 1800 (co-framed)')
    print(f'plastic MIP : {MIP_LEVELS} (per-sector, D1 repaired) via scan_control m##On/Off tags')
    print(f'HV grid     : drift {DRIFT_LADDER} x resist {RESIST_LADDER} (det D = A/B/C); mesh On/Off each')
    print(f'sub-runs    : {n} (4 MIP x 4 HV x 2 mesh) x {SUBRUN_MIN:g} min = {data_min/60:.1f} h data '
          f'(~{(data_min + n)/60:.1f} h wall)')
    print('first 6 sub-runs (tag -> schedule threshold+mesh):')
    for sr in c.sub_runs[:6]:
        print(f'  {sr["sub_run_name"]:26s} resist {sr["hvs"]["5"]["1"]}  drift {sr["hvs"]["9"]["0"]}')
