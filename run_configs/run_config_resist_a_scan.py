#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_resist_a_scan.py — DETECTOR-A resist x drift scan on beam, 2026-08-09.
Prepared during the 16:06 beam stop, to launch when beam returns.

WHAT IT IS
  The production statistics operating point in every respect EXCEPT det A's two HV
  channels. Det A's resist walks a ladder inside each of three det-A drift points:

      drift 700 V (NOMINAL) : resist 570 -> 500, -5 V   -> 15 points   FULL ladder
      drift 600 V           : resist 570 -> 530, -10 V  ->  5 points   truncated
      drift 500 V           : resist 570 -> 530, -10 V  ->  5 points   truncated

      det A resist  (card 5 ch 1) : the ladder above
      det A drift   (card 9 ch 0) : 700 / 600 / 500, the outer loop
      det B/C/D resist (5:2/3/4)  : 540 / 525 / 520     HELD at the standing point
      det B/C/D drift  (9:1/2/3)  : 700 V               HELD
      readout / FIFO / trigger    : identical to run_config_stats_optimized.py

  25 sub-runs x 5 min = 2 h 05 min of data, ~2 h 30 min wall with the ~1 min/sub-run HV
  ramp + DAQ prep. Stop-anywhere, and the ORDER is chosen for it: nominal drift first and
  each ladder top-down, so an early stop costs the 500 V drift block and the low-resist
  tail, never the region around the standing 700/540 point.

WHY 700 / 600 / 500
  The same drift ladder run_67 scanned (2026-07-22), so these A curves overlay that
  dataset rung-for-rung instead of needing their own baseline. Deliberately NOT upward:
  A's drift sparked at its nominal 800 V WITH BEAM on 2026-07-17 (run_50 aborted in its
  first sub-run; run_51 ran A drift at 550 V while B/C/D stayed at 800), and that was
  never re-qualified. Anything above 700 V on 9:0 needs a fresh beam-off check first.
  Other points:  DRIFTS=700,650,600 .venv/bin/python run_configs/run_config_resist_a_scan.py

WHY THE OTHER TWO ARE TRUNCATED
  The -5 V resolution only earns its keep near the operating point, where the 540 V
  optimum and the >540 V roll-over live. Off-nominal drift is a coarse question — does the
  optimum MOVE, and which way — and 5 rungs at -10 V answer it for a third of the beam
  time. 570-530 also brackets the standing 540 V on both sides at every drift point, so
  each block can be normalised on its own 540 V rung.

WHY EVERYTHING ELSE IS PINNED
  This is a single-axis scan, so the readout window, the trigger-FIFO watermark and the
  trigger routing are exactly the settled run_82 point that switch_mode asserts:

      latency 27 / n_samples 20 / 60 ns / IPD 5 / RAW      (run_78 window measurement)
      Main_Trig_OvrWrnHwm 1 / Lwm 0                        (run_82: comb flattened 8x)
      M4.C = or_veto(Singles, lemo0), M4.D = OR(lemo0 PS/flash @1440 ns, lemo1 C-out)

  B/C/D at their standing voltages are not dead weight: they are a same-beam, same-spill,
  fixed-HV CONTROL inside every sub-run — on BOTH channels, since only A's drift moves.
  Normalise A's yield to them and the beam-intensity drift across the 2.5 h grid divides
  out — which matters, because a single descending pass otherwise confounds HV with time
  (see CYCLES below), and it matters more now that the grid is long enough for the beam
  to change character across it.

⚠ THE TOP OF THE LADDER IS ABOVE THE STANDING POINT — two things to know
  1. 570 V on A is 30 V above the production 540 V, but it is NOT unprecedented on beam:
     run_64 (2026-07-22, mesh A+C scan) ran A/B/C resist at 570 V with drift 700 and beam
     on. The 2026-07-17 beam-off stress test put A's *current wall* at 491 V in 95/5 gas;
     the gas is now 90/10, worth ~+72 V of Garfield-equivalent gain, so ~563 V — i.e. 570
     sits right at the edge of that extrapolated wall, and the wall is rate-dependent, so
     beam-on current at 570 will be higher than anything the stress test saw. Watch the
     first sub-run's Imon on the HV card-5 trace; the trip/deviation alerts are live.
     To start one rung lower instead:  V_HI=565 .venv/bin/python run_configs/run_config_resist_a_scan.py
  2. Above ~540 V, det A's EARLY (1-8 ms post-flash) reconstructable-track yield ROLLS
     OVER on HIGH-intensity pulses — run_67 measured A's 4-8 ms band peak at 540 V (1.52%)
     collapsing to 0.43% at 550 V, while LOW-intensity pulses rise monotonically with no
     collapse (memory run67-track-hv-time-cns). So expect the 570-550 rungs to look BAD in
     the early bands and fine in the 20-81 ms band. That is the physics being measured
     here, not a fault — but split by pulse intensity before concluding anything, since
     every event carries its e10.

CYCLES (default 1 — a single descending pass per drift block, which is what was asked for)
  One pass ties each HV point to one 5 min window of beam. If the beam is unstable, run
  the grid more than once: CYCLES=2 appends an ASCENDING pass (palindrome) to every drift
  block, so any monotonic drift in beam intensity cancels between the two visits to each
  rung.  CYCLES=2 .venv/bin/python run_configs/run_config_resist_a_scan.py   # 50 sr, 4 h 10

PRE-RUN
  Board state must be the BEAM trigger (it is, if the last thing running was a beam run;
  a cosmics run leaves M4.C a plain OR and must be put back):
      .venv/bin/python n1081b/trigger_mode.py status          -> C or_veto [0], D [0,1]
      # if not:  .venv/bin/python n1081b/trigger_mode.py scint --singles --ps-pickup
      .venv/bin/python n1081b/set_ps_trigger_delay.py --show  -> 1440
  mode_watcher MUST stay disarmed for the whole scan, or it will stop this run at the
  first 5 min beam gap and start cosmics on top of it:
      touch config/.mode_watcher_disarmed        # (already present 2026-08-09 16:10)

LAUNCH (beam back, or waiting for it)
      .venv/bin/python run_configs/run_config_resist_a_scan.py     # writes the JSON
      bash_scripts/start_run.sh run_config_resist_a_scan_<N>.json
      .venv/bin/python beam_gate.py &                             # hold at boundaries
  beam_gate is what makes "prepared for when beam returns" safe: daq_control has no
  beam-gating of its own, so without the gate a sub-run started into a gap records nothing,
  is still marked complete, and leaves a permanent hole at that HV point.

AFTER
  Re-arm mode_watcher from the Run Mode card (a manual Stop Run does NOT re-arm it), and
  the next changeover puts the standing config back — this generator never writes A's
  voltage anywhere persistent, so nothing has to be undone by hand.
"""
import os

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from run_config_beam import Config as BeamConfig
import run_num

# ---- run number: peek by default (nothing else should be starting runs right now), or
#      ALLOCATE=1 to claim it, or RUN_NUM=N to pin it explicitly.
if os.environ.get('RUN_NUM'):
    RUN_NUM = int(os.environ['RUN_NUM'])
    RUN_NUM_SRC = 'RUN_NUM env'
elif os.environ.get('ALLOCATE') == '1':
    RUN_NUM = run_num.allocate()
    RUN_NUM_SRC = 'run_num.allocate() — CLAIMED'
else:
    RUN_NUM = run_num.peek()
    RUN_NUM_SRC = 'run_num.peek() — NOT claimed, regenerate if another run starts first'

# ---- inner axis: det A resist (card 5 ch 1). FULL ladder at the nominal drift point,
#      TRUNCATED (coarser step, stops higher) at the other two.
V_HI = int(os.environ.get('V_HI', '570'))          # top of every ladder, all drift points
V_LO = int(os.environ.get('V_LO', '500'))          # bottom, NOMINAL drift only
V_STEP = int(os.environ.get('V_STEP', '5'))        # step,   NOMINAL drift only
V_LO_OFF = int(os.environ.get('V_LO_OFF', '530'))  # bottom, off-nominal drift points
V_STEP_OFF = int(os.environ.get('V_STEP_OFF', '10'))   # step, off-nominal drift points
CYCLES = int(os.environ.get('CYCLES', '1'))        # >1 alternates direction (palindrome)

# ---- outer axis: det A drift (card 9 ch 0). First entry is the NOMINAL point and is the
#      one that gets the full -5 V ladder; the rest are truncated. Order is the order run.
DRIFTS = [int(v) for v in os.environ.get('DRIFTS', '700,600,500').split(',')]
DRIFT_NOMINAL = DRIFTS[0]

# ---- held HV: the standing production point (run_67 optimum) ----
DRIFT_HELD = 700                                   # card 9 ch 1-3 = det B/C/D drift
RESIST_HELD = {'B': 540, 'C': 525, 'D': 520}       # card 5 ch 2/3/4 — untouched by the scan

# ---- readout: the settled run_82 point, same as run_config_stats_optimized.py ----
LATENCY = int(os.environ.get('LATENCY', '27'))
N_SAMPLES = int(os.environ.get('N_SAMPLES', '20'))
IPD = 5
SAMPLE_PERIOD = 60
OVR_WRN_HWM = int(os.environ.get('OVR_WRN_HWM', '1'))
OVR_WRN_LWM = int(os.environ.get('OVR_WRN_LWM', '0'))

# ---- dwell ----
SUBRUN_MIN = float(os.environ.get('SUBRUN_MIN', '5'))

# ---- n1081b scan tag: the production HOLD (0.90 MIP plastics, mesh injection off).
#      scan_control takes the tag from sub_run_name.split('_')[0], so every sub-run name
#      starts with it and the thresholds + mesh-off are re-asserted at every HV step.
TAG = 'stat090'

RESUME = os.environ.get('RESUME', '0') == '1'

# ---- DREAM cables physically removed from this DAQ (2026-08-09) ----
# Det A's LAST Y cable, y_8 = FEU 4 connector 8, is plugged into the nTOF DAQ instead of
# ours. Dropping it from the detector's dream_feus map propagates automatically:
# get_active_feu_connectors() then reports FEU 4 as connectors 1-7, and
# dream_daq_control.set_active_feus() writes that Dream's role as `Msk` instead of `Dat` on
# the `Sys Topo Feu 4` line of the applied cfg (connector = dream_index + 1, so connector 8
# is Dream 7). FEU 4 itself stays in the run — only that one Dream is masked.
#   with y_8 :  Sys Topo Feu  4 Dream  0 Dat ... 6 Dat  7 Dat
#   without  :  Sys Topo Feu  4 Dream  0 Dat ... 6 Dat  7 Msk
# ⚠ Det A's Y view therefore covers 7/8 of its strips for this run — the x view is
# untouched, so A still tracks, but its Y acceptance is cut at one edge. That is a
# GEOMETRIC acceptance change, not a gain change, so it does NOT bias the resist/drift
# comparison between rungs (every rung loses the same strips) — but it does mean det A's
# absolute yield here is not comparable with earlier runs, and any efficiency number must
# be computed on the reduced Y map. Set DROP_A_Y8=0 if the cable goes back before launch.
DROP_A_Y8 = os.environ.get('DROP_A_Y8', '1') == '1'
DROPPED_CABLES = {'mx17_A': ['y_8']} if DROP_A_Y8 else {}


def drop_dream_cables(detectors, dropped):
    """Remove named DREAM cables from a detector's maps, in place.

    ``dropped`` is ``{detector_name: [cable_key, ...]}`` with cable keys as they appear in
    ``dream_feus`` ('y_8'). The three parallel per-cable maps (``dream_feus``,
    ``dream_feu_orientation``, ``dream_feu_cable_length``) are kept in step so nothing
    downstream sees a cable in one map and not another. Returns
    ``{detector_name: [(cable, feu, connector), ...]}`` of what was actually removed.
    """
    removed = {}
    for det in detectors:
        keys = dropped.get(det.get('name'))
        if not keys:
            continue
        feus = det.get('dream_feus')
        if not isinstance(feus, dict):
            continue
        for key in keys:
            if key not in feus:
                raise KeyError(f'{det["name"]} has no DREAM cable {key!r} to drop '
                               f'(have: {sorted(feus)})')
            feu, conn = feus.pop(key)
            for parallel in ('dream_feu_orientation', 'dream_feu_cable_length'):
                if isinstance(det.get(parallel), dict):
                    det[parallel].pop(key, None)
            removed.setdefault(det['name'], []).append((key, feu, conn))
    return removed


def resist_ladder(drift_v):
    """The det-A resist rungs for one drift point: full at nominal, truncated elsewhere."""
    lo, step = ((V_LO, V_STEP) if drift_v == DRIFT_NOMINAL else (V_LO_OFF, V_STEP_OFF))
    return list(range(V_HI, lo - 1, -step))


def grid():
    """[(drift, cycle_index, [resist rungs in order]), ...] — the run, in order.

    Outer loop drift, inner loop resist, descending. With CYCLES > 1 each drift block is
    visited CYCLES times with the ladder direction alternating, so a monotonic beam drift
    across a block cancels between the two visits to a rung.
    """
    out = []
    for drift_v in DRIFTS:
        down = resist_ladder(drift_v)
        for cyc in range(CYCLES):
            out.append((drift_v, cyc, down if cyc % 2 == 0 else down[::-1]))
    return out


class Config(BeamConfig):
    def _set_defaults(self, config_path=None):
        super()._set_defaults(config_path)

        self.run_name = f'run_{RUN_NUM}'
        self.run_out_dir = f'{self.data_out_dir}{self.run_name}/'
        self.dream_daq_info['run_directory'] = f'/home/mx17/july_dream/dream_run/{self.run_name}/'
        self.dream_daq_info['data_out_dir'] = f'{self.run_out_dir}'
        self.processor_info['run_dir'] = f'{self.run_out_dir}'
        self.hv_info['run_out_dir'] = self.run_out_dir
        self.resume = RESUME
        self.n1081b_scan = 'on'

        # Drop the cables that are not on this DAQ, then RE-DERIVE the active FEU/connector
        # map — the base class computes it at the end of its own _set_defaults, i.e. before
        # this edit, so without the re-derivation the cfg would still mark connector 8 Dat.
        self.dropped_cables = drop_dream_cables(self.detectors, DROPPED_CABLES)
        if self.dropped_cables and self.dream_daq_info.get('set_feus_from_detectors', False):
            feu_connectors = self.get_active_feu_connectors()
            self.dream_daq_info['included_feus'] = sorted(feu_connectors)
            self.dream_daq_info['feu_connectors'] = feu_connectors

        blocks = grid()
        drift_txt = ' / '.join(
            f'{d} V x {len(resist_ladder(d))} rungs'
            f'{" (-%d V, FULL)" % V_STEP if d == DRIFT_NOMINAL else " (-%d V, to %d)" % (V_STEP_OFF, V_LO_OFF)}'
            for d in DRIFTS)
        self.trigger = (
            f'DETECTOR-A resist x drift scan on beam ({self.run_name}). Both scan axes are det '
            f'A ONLY. Outer: det A drift (card 9 ch 0) {drift_txt} — nominal {DRIFT_NOMINAL} V '
            f'first, taking the full {V_HI}->{V_LO} V resist ladder in -{V_STEP} V steps, the '
            f'other drift points truncated to {V_HI}->{V_LO_OFF} V in -{V_STEP_OFF} V steps. '
            f'Inner: det A resist (card 5 ch 1), descending, {SUBRUN_MIN:g} min per point'
            + (f', each drift block repeated {CYCLES}x with alternating direction'
               if CYCLES > 1 else '')
            + f'. EVERYTHING ELSE HELD at the production statistics point: resist B'
            f'{RESIST_HELD["B"]}/C{RESIST_HELD["C"]}/D{RESIST_HELD["D"]} V and drift '
            f'{DRIFT_HELD} V on det B/C/D (so B/C/D are a same-beam fixed-HV control on BOTH '
            f'channels inside every sub-run); '
            f'plastic discriminators 0.90 MIP and mesh charge-injection OFF, re-asserted every '
            f'sub-run by the `{TAG}` tag; RAW full readout (zero_suppress=False), latency '
            f'{LATENCY}, n_samples {N_SAMPLES}, {SAMPLE_PERIOD} ns sampling, IPD {IPD}, '
            f'trigger-FIFO Hwm {OVR_WRN_HWM}/Lwm {OVR_WRN_LWM} (the run_82 setting). Trigger: '
            f'veto-gated scint SINGLES + PS/flash leg (M4.C = or_veto(Singles lemo0) under the '
            f'N93B ~1-81 ms window, M4.D = OR(lemo0 PS delayed 1440 ns, lemo1 C-out)) — routing '
            f'unchanged, set by trigger_mode.py scint --singles --ps-pickup. ⚠ A above ~540 V '
            f'rolls the early (1-8 ms) track yield over on HIGH-intensity pulses (run_67) and '
            f'{V_HI} V sits near A\'s extrapolated 90/10 current wall (~563 V) — watch card-5 '
            f'Imon. ⚠ The drift axis goes DOWN from nominal only: A drift sparked at 800 V with '
            f'beam on 2026-07-17 (run_50 aborted, run_51 dropped A to 550) and was never '
            f're-qualified. Walls (M1) 0.5 MIP. Scint PMT bias at the 07-19 Y88 setpoints. '
            f'Ar/Iso 90/10, 3He, no Pb.'
            + (' ⚠ READOUT NOT FULL: det A DREAM cable y_8 (FEU 4 connector 8, the last Y '
               'cable) is plugged into the nTOF DAQ, so it is dropped from this config and '
               'FEU 4 Dream 7 is masked (Msk) in the applied cfg. Det A Y covers 7/8 of its '
               'strips; x untouched. Same for every rung, so rung-to-rung comparison is '
               'unaffected, but det A absolute yield is NOT comparable with earlier runs.'
               if self.dropped_cables else ''))

        self.dream_daq_info.update({
            'daq_config_template_path': f'{self.base_out_dir}dream_config/Tcm_Mx17_July.cfg',
            'zero_suppress': False,
            'latency': LATENCY,
            'n_samples_per_waveform': N_SAMPLES,
            'sample_period': SAMPLE_PERIOD,
            'inter_packet_delay': IPD,
            'ovr_wrn_hwm': OVR_WRN_HWM,
            'ovr_wrn_lwm': OVR_WRN_LWM,
        })

        def _drift(v_a):
            # card 9 ch 0-3 = det A/B/C/D drift. Only ch 0 moves; B/C/D re-asserted at the
            # standing 700 V every sub-run.
            return {'0': v_a, '1': DRIFT_HELD, '2': DRIFT_HELD, '3': DRIFT_HELD}

        def _resist(v_a):
            # card 5 ch 1-4 = det A/B/C/D. Only ch 1 moves; B/C/D are re-asserted at their
            # standing values every sub-run so a stray earlier setting cannot persist.
            return {'1': v_a, '2': RESIST_HELD['B'],
                    '3': RESIST_HELD['C'], '4': RESIST_HELD['D']}

        self.sub_runs = []
        k = 0
        for drift_v, cyc, pts in blocks:
            for v in pts:
                name = (f'{TAG}_dA{drift_v}rA{v}_{k:03d}'
                        + (f'_c{cyc}' if CYCLES > 1 else ''))
                self.sub_runs.append({
                    'sub_run_name': name,
                    'run_time': SUBRUN_MIN, 'post_pause_s': 0,
                    'inter_packet_delay': IPD,
                    'latency': LATENCY, 'n_samples_per_waveform': N_SAMPLES,
                    'hvs': {'5': _resist(v), '9': _drift(drift_v)},
                })
                k += 1

        # Scintillator PMT bias holds (plastics card 07, liquids card 08), as every other
        # beam config does — without these a sub-run's HV dict would drop them.
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
    out = (f'config/json_run_configs/run_config_resist_a_scan_{RUN_NUM}'
           f'{"_resume" if RESUME else ""}.json')
    c.write_to_file(out)

    n = len(c.sub_runs)
    data_min = sum(sr['run_time'] for sr in c.sub_runs)
    # Per-sub-run overhead, MEASURED not assumed (2026-08-09): run_159's 15 min cosmic
    # sub-runs came 15.38 min apart, i.e. ~0.4 min of DAQ prep between them. The HV step
    # adds nothing here — the crate ramps at ~6.25 V/s (measured on run_161 sub-run 0000),
    # so a -5 V rung settles in under a second and even a 100 V drift-block change is a few
    # seconds. The first sub-run is the exception: it carries the full RunCtrl config plus
    # the ramp up from wherever HV was left (~2 min from the 200 V pedestal park).
    OVERHEAD_MIN = 0.4
    wall_min = data_min + n * OVERHEAD_MIN + 2.0

    print('=== det-A resist x drift scan on beam — everything else at the production point ===')
    print(f'wrote      : {out}')
    print(f'run number : {RUN_NUM}   ({RUN_NUM_SRC})')
    print(f'outer axis : det A drift (9:0)   {", ".join(str(d) for d in DRIFTS)} V'
          f'   (nominal {DRIFT_NOMINAL} first; NOT scanned upward — see docstring)')
    print(f'inner axis : det A resist (5:1), descending')
    for d in DRIFTS:
        pts = resist_ladder(d)
        step = V_STEP if d == DRIFT_NOMINAL else V_STEP_OFF
        kind = 'FULL     ' if d == DRIFT_NOMINAL else 'truncated'
        print(f'   drift {d:>3} V : {kind}  {pts[0]} -> {pts[-1]} V, -{step} V, '
              f'{len(pts):>2} rungs  = {len(pts)*CYCLES*SUBRUN_MIN:>3.0f} min data')
    print(f'held       : resist B{RESIST_HELD["B"]}/C{RESIST_HELD["C"]}/D{RESIST_HELD["D"]} V, '
          f'drift {DRIFT_HELD} V on B/C/D (control on both channels)')
    print(f'readout    : RAW, latency {LATENCY}, n_samples {N_SAMPLES}, {SAMPLE_PERIOD} ns, '
          f'IPD {IPD}, Hwm {OVR_WRN_HWM}/Lwm {OVR_WRN_LWM}')
    print(f'tag        : {TAG}  (0.90 MIP plastics + mesh injection OFF, re-asserted per '
          f'sub-run)')
    fc = c.dream_daq_info['feu_connectors']
    if c.dropped_cables:
        for det_name, cables in c.dropped_cables.items():
            for cable, feu, conn in cables:
                print(f'dropped    : {det_name} {cable} = FEU {feu} connector {conn} '
                      f'(on the nTOF DAQ) -> FEU {feu} Dream {conn-1} = Msk, '
                      f'connectors now {fc[feu]}')
    else:
        print('dropped    : none — all 8 connectors on all 8 FEUs read out')
    print(f'total      : {n} sub-runs x {SUBRUN_MIN:g} min = {_fmt_hms(data_min)} data, '
          f'~{_fmt_hms(wall_min)} wall'
          + (f'   [{CYCLES} passes per drift block]' if CYCLES > 1 else ''))
    print(f'first/last : {c.sub_runs[0]["sub_run_name"]}  ...  {c.sub_runs[-1]["sub_run_name"]}')
    print()
    print(f'⚠ {V_HI} V resist is {V_HI-540} V above the standing 540 V and near A\'s extrapolated')
    print('  90/10 current wall (~563 V). run_64 did run A at 570 V on beam, but watch the')
    print('  card-5 Imon on the first sub-run.  V_HI=565 starts one rung lower.')
    print('⚠ Stop-anywhere order: nominal drift first, each ladder top-down — an early stop')
    print(f'  costs the {DRIFTS[-1]} V drift block and the low-resist tail, not the standing point.')
    print()
    print('PRE-RUN : trigger_mode.py status -> C or_veto [0], D [0,1]; PS delay 1440')
    print('          config/.mode_watcher_disarmed MUST exist for the whole scan')
    print(f'LAUNCH  : bash_scripts/start_run.sh {out.split("/")[-1]}')
    print('          .venv/bin/python beam_gate.py &      # no empty sub-runs in a beam gap')
