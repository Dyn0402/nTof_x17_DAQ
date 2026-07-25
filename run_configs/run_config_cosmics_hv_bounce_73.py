#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_cosmics_hv_bounce_73.py — run_73, 2026-07-24 (INTERMEDIATE resist points
between the run_72 ladder). Same trigger/DREAM/HV setup as run_72, but resist scans
the two midpoints 553 -> 538 V (mid(545,560) and mid(530,545)) so run_72+run_73 give a
fine 530/538/545/553/560 V ladder at drift 600 V.
BEAM-OFF cosmic scint-Singles: resist HV bounced 553 -> 538 V at drift 600 V,
15 min sub-runs, grid repeated 2x. Built for a short (~30 min) beam access.

Ungated cosmics trigger of run_68 (run_config_cosmics_resist_scan.py), reduced to
the three resist points the operator wants and sized so the first cycle fits a
short gap while a second cycle is there if the access runs long.

NO MESH AXIS — and that is deliberate. The mesh charge-injection legs (M6/.245
SEC_B out2/out3) are FIRED by SEC_B **in0**, whose Gate&Delay (1260 ns) is
referenced to the PS/T0 gamma-flash fanned in from M6.A (RUN_MODES_2026-07.md
§M6: "1260 ns = injection ~180 ns before flash rise"). With no beam there are no
PS pulses, in0 never fires, and enabling the output legs injects NOTHING — a
beam-off mesh ON/OFF scan is a null axis. Dropping it doubles the cosmic
statistics per HV point in the same wall time, and means **board .245 is never
touched by this run**. (If a beam-off mesh study is ever wanted, patch the M6.D
pulser into M6.B in0 first — see memory `mesh-injection-is-ps-triggered`.)

TRIGGER — scint SINGLES, veto OPEN, NO PS / NO readout gate:
  Beam is off, so there are no PS pulses and the N93B 30 ms veto window never
  opens; a normal veto-gated scint trigger would be vetoed ~100 %. We OPEN the
  veto by making M4.C a *plain* OR on the Singles line (lemo0), veto input inert
  — exactly `setup_cosmics_singles_ungated.py` (M4.C = plain FN_OR lemo0;
  M4.D = OR lemo1 = C-out; flash line off). Every trigger is an ungated
  scintillator single.

  Plastic discriminator threshold is DROPPED to 0.5 MIP (A-65/B-78/C-86/D-83,
  from calibrations/pss/mip_thresholds_y88.json) and re-asserted every sub-run by
  the `cosbounce` scan tag. Cosmics are MIPs — Landau MPV = 1 MIP — so run_71's
  standing 1.41 MIP bar would cut most of them; 0.5 MIP is ~3x the trigger rate.
  That threshold-only tag is the ONLY thing scan_control does here, and it only
  ever touches M2/.241. Walls (M1) are NOT touched.

DREAM — RAW full readout, 32 smp x 60 ns = 1920 ns, latency 35 (scint MM ~smp
  11), no ZS, no pedestal subtraction — same framing as run_59/run_68 cosmics so
  the frames are directly comparable.

HV GRID — drift 600 V fixed on all four dets (card 9 ch0-3). Resist (card 5
  ch1-4) bounced 560 -> 530 -> 545 V in that order, ALL FOUR detectors at the
  same value (operator 2026-07-24; the usual det D -10 V offset is deliberately
  NOT applied — set DET_D_OFFSET=10 to reinstate it).

  ⚠ det D: 560 V resist is above the 2026-07-17 beam-off stress ceiling recorded
  for D (475 V), though inside what run_68 actually ran beam-off at drift 600
  (A/B/C 570 down to 520, D at -10). D has no headroom margin here and it is the
  chamber that tripped its drift on 07-23. Watch the HV alerts on the FIRST
  sub-run (which is the 560 V one); if D sparks, re-run with DET_D_OFFSET=10.

ORDERING — cycle OUTER -> resist INNER:
    for cycle in 1..2:
      560 V (15 min) -> 530 V (15 min) -> 545 V (15 min)
  3 sub-runs/cycle x 15 min = 45 min/cycle; N_CYCLES=2 -> 6 sub-runs = 1 h 30 min.
  Stop-anywhere. In a 30 min access expect the 560 and 530 points to complete;
  the bounce order is the operator's, so the two most-separated points land
  first. A partial cycle is still usable — every sub-run is self-contained.

DISK — MEASURED on run_72 sub-run 000 (2026-07-24): ungated cosmics ran at
  **25.6 Hz** integrated, giving **7.8 MB/s → ~6.5 GB per 15 min sub-run** and
  **~39 GB for the full 6**. (An earlier estimate in this docstring of "well under
  a GB per sub-run" was wrong by ~7x — RAW at 32 smp is ~311 kB/event regardless
  of how sparse cosmics feel.) That still fits comfortably: at launch the SSD
  staging volume had 111 GB free and the HDD 1.7 TB, and backup_watcher /
  space_watcher were both running. No special clearing needed for a 1.5 h run,
  but do NOT extend this config to many cycles without re-checking disk.

SCINT PMT HV: plastics (card 07) + liquids (card 08) held at the 07-19 Y88
  equalized setpoints (merged into every sub-run, inherited from run_config_beam).

PRE-RUN (beam OFF, run_71 stopped, boards free — check config/n1081b_access/):
  .venv/bin/python n1081b/setup_cosmics_singles_ungated.py   # M4.C plain OR(Singles), veto OPEN
  verify: .venv/bin/python n1081b/trigger_mode.py status     # expect C plain-OR lemos=[0], D lemos=[1]
Launch:
  ./start_run.sh run_config_cosmics_hv_bounce.json
RESTORE when beam returns:
  .venv/bin/python n1081b/trigger_mode.py scint --doubles    # re-arms the 30 ms N93B veto gate
  # scan_control restores the M2 thresholds to their found state on exit.
"""
import os

# --- repo-root shim (run_config_beam/base live one dir up) ---
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from run_config_beam import Config as BeamConfig

RUN_NUM = 73

# ---- DREAM readout (RAW cosmic singles, same window as run_59/run_68) ----
LATENCY, N_SAMPLES, SAMPLE_PERIOD = 35, 32, 60

# ---- per-sub-run dwell (minutes) and number of grid repeats ----
SUBRUN_MIN = float(os.environ.get('SUBRUN_MIN', '15'))
N_CYCLES = int(os.environ.get('N_CYCLES', '2'))     # 3 sr x 15 min = 45 min/cycle -> 1.5 h

# ---- HV axes ----
DRIFT = 600                                # card 9 ch0-3, all dets, fixed
RESIST = [553, 538]                        # card 5 ch1-4 — INTERMEDIATE points, midway
                                           # between the run_72/run_73(orig) ladder
                                           # (530/545/560): 538 = mid(530,545),
                                           # 553 = mid(545,560). Fills the ladder to
                                           # 530/538/545/553/560 when combined.
DET_D_OFFSET = int(os.environ.get('DET_D_OFFSET', '0'))   # 0 = all four dets equal (operator)

# ---- scan tag: threshold-only (0.5 MIP plastics). No mesh target, no .245. ----
SCAN_TAG = 'cosbounce'


class Config(BeamConfig):
    def _set_defaults(self, config_path=None):
        super()._set_defaults(config_path)

        self.run_name = f'run_{RUN_NUM}'
        self.beam_type = 'cosmics'          # beam off
        self.run_out_dir = f'{self.data_out_dir}{self.run_name}/'
        self.dream_daq_info['run_directory'] = f'/home/mx17/july_dream/dream_run/{self.run_name}/'
        self.dream_daq_info['data_out_dir'] = f'{self.run_out_dir}'
        self.processor_info['run_dir'] = f'{self.run_out_dir}'
        self.hv_info['run_out_dir'] = self.run_out_dir
        self.resume = False
        # scan_control asserts the 0.5 MIP plastic thresholds per sub-run (M2/.241
        # only). The ungated singles routing is set ONCE pre-run and is NOT touched
        # by the cosbounce tag.
        self.n1081b_scan = 'on'

        self.trigger = (
            f'BEAM-OFF cosmic scint-Singles resist bounce ({self.run_name}). Trigger: scint '
            f'SINGLES with veto OPEN (ungated), NO PS / NO readout gate -- M4.C = plain '
            f'OR(Singles lemo0), M4.D = OR(C-out lemo1), set via '
            f'setup_cosmics_singles_ungated.py. Every trigger is an ungated scintillator '
            f'single (N93B 30 ms gate NOT applied). DREAM = RAW full readout '
            f'(zero_suppress=False), {N_SAMPLES} smp x {SAMPLE_PERIOD} ns = '
            f'{N_SAMPLES * SAMPLE_PERIOD} ns, latency {LATENCY} (scint MM ~smp 11), no ZS. '
            f'HV: drift {DRIFT} V all dets (fixed); resist bounced {RESIST} V, all four dets '
            f'at the same value (det D offset {DET_D_OFFSET} V). Plastic discriminator '
            f'DROPPED to 0.5 MIP (A-65/B-78/C-86/D-83) for cosmic MIP efficiency via the '
            f'{SCAN_TAG} scan tag; walls (M1) not touched. NO MESH AXIS -- the mesh '
            f'charge-injection trigger (M6.B in0) is PS/T0-fed and never fires with beam '
            f'off, so a beam-off mesh ON/OFF scan measures nothing; board .245 is untouched '
            f'by this run. {len(RESIST)} sub-runs x {SUBRUN_MIN:g} min per cycle, '
            f'{N_CYCLES} cycles. Scint PMT bias held at the 07-19 Y88 equalized setpoints. '
            f'Ar/Iso 90/10, 3He, no Pb. RESTORE: trigger_mode.py scint --doubles.')

        # RAW / full readout (base template is already Tcm RAW; just pin samples/latency).
        self.dream_daq_info.update({
            'zero_suppress': False,
            'pedestal_subtraction': False,
            'n_samples_per_waveform': N_SAMPLES,
            'latency': LATENCY,
            'sample_period': SAMPLE_PERIOD,
        })

        # ----- sub-run build: cycle OUTER -> resist INNER -----
        def _drift(dv):
            return {'0': dv, '1': dv, '2': dv, '3': dv}             # card 9 ch0-3 = drift A/B/C/D

        def _resist(v):
            return {'1': v, '2': v, '3': v, '4': v - DET_D_OFFSET}  # card 5 ch1-4 = resist A/B/C/D

        self.sub_runs = []
        k = 0
        for cyc in range(N_CYCLES):
            for v in RESIST:
                # tag (leading '_'-token) keys scan_control -> cosbounce.
                self.sub_runs.append({
                    'sub_run_name': f'{SCAN_TAG}_r{v:g}_c{cyc:02d}_{k:03d}',
                    'run_time': SUBRUN_MIN, 'post_pause_s': 0,
                    'latency': LATENCY, 'n_samples_per_waveform': N_SAMPLES,
                    'hvs': {'5': _resist(v), '9': _drift(DRIFT)},
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
    out = 'config/json_run_configs/run_config_cosmics_hv_bounce_73.json'
    c.write_to_file(out)
    dd = c.dream_daq_info

    n = len(c.sub_runs)
    data_min = sum(sr['run_time'] for sr in c.sub_runs)
    per_cycle = len(RESIST)

    print(f'=== {c.run_name} — BEAM-OFF cosmic scint-Singles, resist bounce (no mesh axis) ===')
    print(f'wrote    : {out}')
    print(f'run_name : {c.run_name}   beam {c.beam_type} | gas {c.gas} | target {c.target_type}')
    print(f'DREAM    : RAW (ZS={dd["zero_suppress"]}), {dd["n_samples_per_waveform"]} smp x '
          f'{dd["sample_period"]} ns = {dd["n_samples_per_waveform"] * dd["sample_period"]} ns, '
          f'latency {dd["latency"]}, IPD {dd["inter_packet_delay"]}')
    print(f'drift    : {DRIFT} V all dets (fixed)')
    print(f'resist   : {RESIST} V (bounce order), det D offset -{DET_D_OFFSET} V')
    print(f'plastic  : 0.5 MIP  A-65 / B-78 / C-86 / D-83  (tag {SCAN_TAG!r}, M2/.241 only)')
    print(f'mesh     : NONE — axis dropped (PS-fed trigger, null with beam off); .245 untouched')
    print(f'cycle    : {per_cycle} sub-runs x {SUBRUN_MIN:g} min = {_fmt_hms(per_cycle * SUBRUN_MIN)}/cycle')
    print(f'total    : {N_CYCLES} cycles = {n} sub-runs = {_fmt_hms(data_min)}  (stop-anywhere)')
    print(f'sub-runs :')
    for i, sr in enumerate(c.sub_runs):
        mark = '   <- fits a 30 min access' if i == 1 else ''
        print(f'  {sr["sub_run_name"]:26s} resist A{sr["hvs"]["5"]["1"]}/B{sr["hvs"]["5"]["2"]}/'
              f'C{sr["hvs"]["5"]["3"]}/D{sr["hvs"]["5"]["4"]}  drift {sr["hvs"]["9"]["0"]}{mark}')
    print()
    print('PRE-RUN (beam OFF, run_71 stopped, boards free — check config/n1081b_access/ first):')
    print('  .venv/bin/python n1081b/setup_cosmics_singles_ungated.py')
    print('  verify: .venv/bin/python n1081b/trigger_mode.py status')
    print('Launch:  ./start_run.sh run_config_cosmics_hv_bounce.json')
    print('RESTORE: .venv/bin/python n1081b/trigger_mode.py scint --doubles')
