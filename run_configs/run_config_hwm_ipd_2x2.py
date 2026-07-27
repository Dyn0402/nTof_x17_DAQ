#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_hwm_ipd_2x2.py — run_82, 2026-07-27.
ONE 40-minute run that answers the comb-spikiness question on both levers at once:
trigger-FIFO watermark x inter-packet delay, 2x2, palindrome-interleaved.

  Supersedes run_config_hwm_spikiness.py (Hwm only), which is left in place unused.
  The threshold ladder (run_config_plastic_thresh_spikiness.py, run_83) stays the
  FALLBACK and is not folded in here — it does not fit in 40 min, and it is only
  needed if these two levers come up short.

WHAT IS BEING TESTED, AND WHY THESE FOUR POINTS

  ⚠ FIRST, A CORRECTION MADE BEFORE THIS RUN WAS WRITTEN. The obvious model — "the dead
  gap is a full FIFO drained, gap = Hwm x n_samples x (4.83 + 0.998 x IPD)" — predicts
  393 us at the production point and looked confirmed by the 0.35-0.40 ms starved gaps in
  the run_79 profile. **It is wrong**, and the inter-trigger-interval distribution says so
  directly. Measured on run_79 (20 067 intervals, sub-runs 0000/0002/0007, both the 1-10 ms
  and 10-40 ms bands, flash-anchored):

      dt mode = 195 us,  p25 = 180 us      -- stable to +-10 us across every sub-run and band

  That is ONE per-event readout (model: 196 us), not two. So the hard floor on trigger
  spacing is a single readout regardless of Hwm — consistent with BUSY asserting at
  occupancy >= Hwm and clearing at <= Lwm, i.e. the FEU drains just far enough to clear
  the hysteresis, one event, not the whole FIFO. The 0.35 ms starved bands in the pooled
  profile are a DIFFERENT quantity: phase-coherent comb structure (period ~1.15 ms), closer
  to the DREAM delayed-cell-release cycle of the 2026-07-20 flash-off pulser study than to
  any Hwm x readout block.

  WHAT SURVIVES, AND IT IS THE USEFUL HALF:

    * **The per-event readout time is modelled and now CONFIRMED to 0.7%**:
          t_ev = n_samples x (4.83 + 0.998 x IPD)  ->  196 us predicted, 195 us measured
      IPD is the only free knob in it that we can move without touching the drift window.
      **IPD 5 -> 2 predicts the dt floor drops 196 -> 137 us, a clean -30%.** That is a
      sharp, high-statistics, directly falsifiable prediction — ~5000 intervals per point
      even on a 4.4 min sub-run, so it is settled long before the CV is.

    * **Hwm's mechanism is EMPIRICAL, not modelled.** run_78 vs run_77 showed Hwm 11 -> 2
      transformed the comb, so it clearly does something; exactly how it aggregates BUSY
      blocks into the 1.15 ms period is not understood. This run measures it rather than
      assuming it. Do not let the 2x2 be read as if both axes were equally well modelled.

      point   Hwm  IPD   t_ev (predicted dt floor)   1-ev ceiling   what it isolates
      A         2    5          196 us                 5.09 kHz     BASELINE = production
      B         1    2          137 us                 7.32 kHz     both levers together
      C         2    2          137 us                 7.32 kHz     IPD alone
      D         1    5          196 us                 5.09 kHz     Hwm alone

  Note A/D and B/C share a predicted dt floor — that is the point. If the measured floor
  tracks IPD and ignores Hwm, the correction above is confirmed and IPD is the lever that
  actually shortens readout. Whatever Hwm then does to the COMB (period, starved fraction,
  CV) it does by some other route, and A-vs-D / C-vs-B isolate it at fixed readout speed.

  THE TWO LEVERS ARE NOT EQUIVALENT, which is why they are separated:
    * Hwm 2->1 changes how BUSY aggregates; it costs triggers (it gives up the
      derandomiser's readout/transmit overlap, measured worth ~1.5x) and on the evidence
      so far it redistributes dead time rather than removing it.
    * IPD 5->2 makes every readout 30% faster, shortening dead time wherever it occurs,
      and gives up NO triggers. If it holds up it is strictly the better lever.
    * Point B tells us whether they compose.

⚠ IPD 2 HAS NEVER BEEN RUN ON BEAM — TREAT POINTS B AND C AS UNPROVEN
  The 2026-07-22 post-10 GbE ladder went down to IPD 5 (0.026% eventId gaps, clean) and
  never found the corruption threshold going lower, so there is headroom — but "never
  found" is not "measured". Below some IPD the FEUs outrun the wire and events are lost.
  The ordering below puts an IPD-2 point SECOND, ~6 minutes in, precisely so the integrity
  check can be run early and the rest of the run abandoned if it fails:

      /home/mx17/ana/.venv/bin/python \\
          ~/beam_july/analysis/flash_comb/tools/flash_time_spikiness.py \\
          --run run_82 --group-by-setting --tmax 40

  Its `gap%` column is the eventId gap fraction. **>0.1% on an IPD-2 point = corruption;
  discard those points and keep IPD 5.** Do not read a "flatter" distribution from a
  corrupt point as a win — dropping events flattens a comb beautifully and means nothing.

ORDER — palindrome, so linear beam drift cancels EXACTLY
      A B C D | D C B A
  Mean position is 4.5 for every one of the four settings, so a beam intensity ramp across
  the run contributes equally to all of them and cannot fake a difference. This matters
  more than usual today: comb depth is driven by the offered singles rate, which tracks
  beam intensity, and the beam is spotty.

  Truncation is graceful by construction:
      after 2 points (~10 min)  baseline vs both-levers -> "does it flatten at all?",
                               plus the IPD-2 integrity check
      after 4 points (~19 min)  the complete 2x2 -> the answer, single-exposure
      all 8      (~39 min)  doubled statistics AND drift-cancelled

SPOTTY BEAM — three independent defences
  1. **Every metric is per-flash-anchored-spill**, so a beam gap costs statistics but does
     NOT bias the shape. A short point is a noisy point, not a wrong one.
  2. **`beam_gate.py`** holds the `.pause_run` flag while beam is off. daq_control checks
     that flag at each sub-run boundary (daq_control.py:264), so the run will not START a
     point into a beam gap; it waits and picks up when beam returns. The gate only ever
     clears a hold it set itself, so it cannot stamp on an operator pause or on
     daq_control's own n1081b-apply hold. Launch it alongside the run:
         .venv/bin/python beam_gate.py &
  3. **RESUME=1** regenerates a config that skips every sub-run already carrying a
     `.subrun_complete` marker, so a run killed by a long beam stop can be picked up
     without re-taking or overwriting what it got.
  Backstop already in daq_control: it aborts after MAX_EMPTY_SUBRUNS=2 consecutive
  zero-byte sub-runs, so a beam stop that outlasts the gate cannot burn the whole schedule.

  ⚠ Judge every point by its FLASH COUNT, not its wall-clock. At the currently measured
  16.2 flashes/min (run_81, live) a 4.4 min point collects ~71 flashes, ~142 pooled over
  the two cycles. The analysis prints n_flash per setting — a point far below its partner
  was under-exposed and should be re-taken via RESUME rather than compared.

EVERYTHING ELSE IS THE PRODUCTION POINT, HELD
  latency 27, n_samples 20, 60 ns sampling, RAW; drift 700 V all four, resist
  A540/B540/C525/D520; plastic 0.90 MIP; walls 0.5 MIP; mesh off; M4.D1 PS delay 1440 ns.
  All eight sub-runs carry the SAME `stat090` tag, so the N1081B state is re-asserted
  identically each time and never changes across the run. Hwm and IPD are the only
  variables. No board work is needed if this follows run_81 directly.

PRE-RUN (beam ON — daq_control has NO beam-gating of its own)
  .venv/bin/python n1081b/trigger_mode.py status         -> C or_veto [0], D [0,1]
  .venv/bin/python n1081b/set_ps_trigger_delay.py --show -> delay 1440
  cat config/beam_state.json                             -> beam_on: true, recent pulse
Generate: .venv/bin/python run_configs/run_config_hwm_ipd_2x2.py
Launch:   .venv/bin/python beam_gate.py &        # then
          ./start_run.sh run_config_hwm_ipd_2x2.json

VERIFY ON THE FIRST TWO SUB-RUNS (a null is worthless without this)
  1. per-sub-run overrides reached the cfg — they have been silently dropped before by a
     stale dream_daq server:
       grep -H -E "Main_Trig_OvrWrn|InterPacket" ~/july_dream/dream_run/run_82/*/Tcm_Mx17_July.cfg
     must show Hwm 2/Lwm 1 + IPD 5 on 0000 and Hwm 1/Lwm 0 + IPD 2 on 0001.
  2. hardware, live, during sub-run 0001:
       .venv/bin/python dream_scripts/feu_trig_counters.py   -> Hwm 1 / Lwm 0 on all 8
     (⚠ ignore its "watermark cannot be biting" footer — computed from counters that read
      0 without the --latch poke the read-only default skips.)
  3. RunCtrl clamps the watermark DOWNWARD only and its cap here is (512-27)//20 = 24 ->
     Hwm 20, so both rungs pass through untouched.
"""
import os

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from run_config_beam import Config as BeamConfig

RUN_NUM = int(os.environ.get('RUN_NUM', '82'))

# ---- readout: the production point, held ----
LATENCY = int(os.environ.get('LATENCY', '27'))
N_SAMPLES = int(os.environ.get('N_SAMPLES', '20'))
SAMPLE_PERIOD = 60

# ---- the 2x2, palindrome-ordered: A B C D D C B A ----
#      key -> (hwm, ipd). Lwm is always hwm-1: it must sit strictly BELOW hwm or the
#      OverflowWarning hysteresis cannot clear. Hwm 1 / Lwm 0 was measured working
#      (2001 Hz sustained, 2026-07-22 saturating-pulser ladder).
POINTS = {'A': (2, 5), 'B': (1, 2), 'C': (2, 2), 'D': (1, 5)}
ORDER = os.environ.get('ORDER', 'A,B,C,D,D,C,B,A').split(',')

# ---- dwell: sized to the 40 min budget at the MEASURED 27 s/sub-run overhead ----
SUBRUN_MIN = float(os.environ.get('SUBRUN_MIN', '4.4'))
OVERHEAD_S = 27.0            # measured across run_79's 16 sub-run boundaries
RESUME = os.environ.get('RESUME', '0') == '1'

# ---- operating point, identical to run_79/run_81 ----
DRIFT_V = 700
DRIFT_D_OFFSET = int(os.environ.get('DRIFT_D_OFFSET', '0'))
RESIST_V = {'A': 540, 'B': 540, 'C': 525, 'D': 520}
TAG = 'stat090'              # N1081B state HELD: 0.90 MIP plastics, mesh off, every sub-run

PS_DELAY_NS = 60 * (2.0 + LATENCY - 5)

# measured live 2026-07-27 on run_81, used only for the exposure estimate printed below
FLASH_PER_MIN = 16.2


def per_event_us(n, ipd):
    """Serialised single-event readout time."""
    return n * (4.83 + 0.998 * ipd)


def predicted_dt_floor_us(ipd, n=None):
    """The hard floor on trigger spacing = ONE per-event readout. Confirmed on run_79:
    predicted 196 us at IPD 5, measured dt mode 195 us / p25 180 us. Independent of Hwm."""
    return per_event_us(n or N_SAMPLES, ipd)


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

        pts = '; '.join(f'{k}=Hwm{h}/IPD{i} (predicted dt floor {predicted_dt_floor_us(i):.0f} us)'
                        for k, (h, i) in POINTS.items())
        self.trigger = (
            f'WATERMARK x INTER-PACKET-DELAY 2x2 ({self.run_name}, 2026-07-27) — one ~40 min '
            f'run to settle the 1-10 ms acceptance-comb spikiness on both DAQ levers at once. '
            f'run_79/run_81 at the production point read CV 0.420 on 1-10 ms in 0.5 ms bins '
            f'but 0.584 at 0.25 ms and 0.874 at 0.1 ms: a real comb of period ~1.15 ms with 12 '
            f'near-empty gaps (median 0.35 ms, max 0.40) covering 33% of the band — the 0.5 ms '
            f'bin is wider than the gap and hides it. ⚠ The naive "gap = Hwm x full-FIFO readout" '
            f'model is WRONG and was corrected before this run: the measured inter-trigger '
            f'floor on run_79 is dt mode 195 us / p25 180 us (20067 intervals, stable across '
            f'sub-runs and bands) = ONE per-event readout t_ev = n_samples x (4.83 + 0.998 x '
            f'IPD) = 196 us predicted, NOT the 393 us a 2-deep drain would give. So t_ev is '
            f'confirmed to 0.7% and IPD is its only free knob: IPD 5->2 predicts the floor '
            f'drops to 137 us (-30%), testable to high precision within one sub-run. Hwm acts '
            f'on the comb by some OTHER route (period ~1.15 ms, starved gaps ~0.35 ms) that is '
            f'empirical, not modelled — this run measures it. SCAN AXES = watermark and IPD only, 2x2: {pts}. Hwm 2->1 chops the '
            f'dead time finer at a trigger cost; IPD 5->2 makes every readout 30% faster and '
            f'gives up NO triggers, so if it works it is the better lever. ORDER is the '
            f'palindrome {"".join(ORDER)} so linear beam drift cancels exactly (equal mean '
            f'position for all four settings) and truncation stays graceful. ⚠ IPD 2 has NEVER '
            f'been run on beam — an IPD-2 point runs SECOND so the eventId-gap integrity check '
            f'happens ~6 min in; >0.1% gaps means corruption and those points are void. HELD: '
            f'latency {LATENCY}, n_samples {N_SAMPLES}, {SAMPLE_PERIOD} ns sampling, RAW / full '
            f'readout, drift {DRIFT_V} V all four, resist A{RESIST_V["A"]}/B{RESIST_V["B"]}/'
            f'C{RESIST_V["C"]}/D{RESIST_V["D"]} V, plastic 0.90 MIP, walls (M1) 0.5 MIP, mesh '
            f'charge-injection OFF — all re-asserted per sub-run by the `{TAG}` tag, the SAME '
            f'tag on every sub-run so the N1081B state never changes. PS + SINGLES trigger, '
            f'M4.D1 G&D delay {PS_DELAY_NS:.0f} ns (flash at sample ~5). {len(ORDER)} x '
            f'{SUBRUN_MIN:g} min. Beam is spotty: run with beam_gate.py holding .pause_run '
            f'across beam-off, and judge each point by its flash count, not wall-clock. '
            f'Ar/Iso 90/10, 3He, no Pb.')

        # dream_daq_info carries point A (the baseline); each sub-run overrides.
        hwm0, ipd0 = POINTS[ORDER[0]]
        self.dream_daq_info.update({
            'daq_config_template_path': f'{self.base_out_dir}dream_config/Tcm_Mx17_July.cfg',
            'zero_suppress': False,
            'latency': LATENCY,
            'n_samples_per_waveform': N_SAMPLES,
            'sample_period': SAMPLE_PERIOD,
            'inter_packet_delay': ipd0,
            'ovr_wrn_hwm': hwm0,
            'ovr_wrn_lwm': hwm0 - 1,
        })

        d_drift = DRIFT_V - DRIFT_D_OFFSET
        drift_hvs = {'0': DRIFT_V, '1': DRIFT_V, '2': DRIFT_V, '3': d_drift}
        resist_hvs = {'1': RESIST_V['A'], '2': RESIST_V['B'],
                      '3': RESIST_V['C'], '4': RESIST_V['D']}

        # Sub-run names keep ONE scan tag (stat090) so the N1081B is never touched
        # mid-run; the settings ride in the DREAM per-sub-run override. The name still
        # carries h#i# so a point is identifiable from the path alone.
        self.sub_runs = []
        for k, key in enumerate(ORDER):
            hwm, ipd = POINTS[key]
            self.sub_runs.append({
                'sub_run_name': f'{TAG}_h{hwm}i{ipd}_{k:04d}',
                'run_time': SUBRUN_MIN, 'post_pause_s': 0,
                'latency': LATENCY, 'n_samples_per_waveform': N_SAMPLES,
                'inter_packet_delay': ipd,
                'ovr_wrn_hwm': hwm, 'ovr_wrn_lwm': hwm - 1,
                'hvs': {'5': dict(resist_hvs), '9': dict(drift_hvs)},
            })

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
    out = (f'config/json_run_configs/run_config_hwm_ipd_2x2'
           f'{"_resume" if RESUME else ""}.json')
    c.write_to_file(out)

    # --- assertions that would otherwise fail mid-beam ---
    tags = {sr['sub_run_name'].split('_')[0] for sr in c.sub_runs}
    assert tags == {TAG}, f'scan tags drifted: {tags}'
    assert len(ORDER) == len({sr["sub_run_name"] for sr in c.sub_runs}), 'duplicate sub-run name'
    # palindrome check: every setting must share the same mean position, else drift bites
    pos = {}
    for i, k in enumerate(ORDER):
        pos.setdefault(k, []).append(i + 1)
    means = {k: sum(v) / len(v) for k, v in pos.items()}
    assert len(set(round(m, 6) for m in means.values())) == 1, \
        f'ORDER is not drift-balanced, mean positions {means} — fix ORDER or accept the confound'
    buf = (512 - LATENCY) // N_SAMPLES
    cap = buf - 4 if buf > 16 else (buf - 3 if buf > 8 else buf - 2)
    for k, (h, i) in POINTS.items():
        assert h <= cap, f'point {k}: Hwm {h} exceeds the RunCtrl cap {cap} and would be clamped'
        assert h - 1 >= 0, f'point {k}: Lwm would be negative'

    total_min = sum(s['run_time'] for s in c.sub_runs) + len(c.sub_runs) * OVERHEAD_S / 60

    print(f'=== {c.run_name} — watermark x inter-packet-delay 2x2'
          f'{" [RESUME]" if RESUME else ""} ===')
    print(f'wrote        : {out}')
    print(f'order        : {" ".join(ORDER)}   (palindrome — mean position '
          f'{list(means.values())[0]:.1f} for ALL four settings, so beam drift cancels)')
    print()
    print(f'{"pt":>3}  {"Hwm":>3} {"Lwm":>3} {"IPD":>3}   {"t_ev = predicted dt floor":>25}  '
          f'{"1-ev ceiling":>12}   isolates')
    what = {'A': 'BASELINE = production', 'B': 'both levers', 'C': 'IPD alone', 'D': 'Hwm alone'}
    for k, (h, i) in POINTS.items():
        pe = per_event_us(N_SAMPLES, i)
        print(f'{k:>3}  {h:>3} {h-1:>3} {i:>3}   {pe:>22.0f} us  '
              f'{1000/pe:>9.2f} kHz   {what[k]}')
    print(f'  A/D and B/C share a predicted floor ON PURPOSE: if the measured floor tracks IPD')
    print(f'  and ignores Hwm, the readout model is confirmed and Hwm acts on the comb some')
    print(f'  other way — which A-vs-D and C-vs-B then isolate at fixed readout speed.')
    print(f'  run_79 MEASURED at point A: dt mode 195 us / p25 180 us vs '
          f'{per_event_us(N_SAMPLES, 5):.0f} us predicted (0.7%); comb period ~1.15 ms, '
          f'starved gaps ~0.35 ms.')
    print()
    print(f'RunCtrl cap  : {cap}  -> every rung passes through (clamp is downward-only)')
    print(f'held         : latency {LATENCY}, n_samples {N_SAMPLES}, RAW, 0.90 MIP, mesh off, '
          f'tag {TAG} on every sub-run')
    print(f'total        : {len(c.sub_runs)} x {SUBRUN_MIN:g} min + {OVERHEAD_S:.0f} s/sub-run '
          f'overhead = {total_min:.1f} min')
    print(f'exposure     : ~{FLASH_PER_MIN*SUBRUN_MIN:.0f} flashes/sub-run at the live '
          f'{FLASH_PER_MIN:.1f}/min, ~{2*FLASH_PER_MIN*SUBRUN_MIN:.0f} pooled per setting')
    print(f'disk         : ~5.3 MB/s -> ~{5.3*sum(s["run_time"] for s in c.sub_runs)*60/1000:.0f} GB')
    print()
    print('GRACEFUL TRUNCATION — stop anywhere, you still have a result:')
    for n, txt in ((2, 'baseline vs both-levers + the IPD-2 integrity check'),
                   (4, 'the COMPLETE 2x2 — the answer, single exposure'),
                   (8, 'doubled statistics AND drift-cancelled')):
        if n <= len(ORDER):
            print(f'  after {n} pts (~{n*SUBRUN_MIN + n*OVERHEAD_S/60:.0f} min): {txt}')
    print()
    print('SPOTTY BEAM:')
    print('  .venv/bin/python beam_gate.py &     # holds .pause_run across beam-off;')
    print('                                      # daq_control checks it at each boundary')
    print('  RESUME=1 .venv/bin/python run_configs/run_config_hwm_ipd_2x2.py   # re-take gaps')
    print('  judge points by FLASH COUNT (printed by the analysis), never by wall-clock')
    print()
    print('⚠ IPD 2 IS UNPROVEN ON BEAM. Check integrity ~6 min in, on sub-run 0001:')
    print('  /home/mx17/ana/.venv/bin/python \\')
    print('    ~/beam_july/analysis/flash_comb/tools/flash_time_spikiness.py \\')
    print(f'    --run {c.run_name} --group-by-setting --tmax 40')
    print('  gap% > 0.1 on an IPD-2 point = CORRUPTION -> those points are void, keep IPD 5.')
    print('  (a corrupt point looks FLATTER, because dropping events flattens a comb.)')
    print()
    print(f'Launch: ./start_run.sh {out.split("/")[-1]}')
