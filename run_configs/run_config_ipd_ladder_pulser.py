#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_ipd_ladder_pulser.py — 2026-07-28. IPD 5/4/3/2/1 at the ADOPTED Hwm 1 / Lwm 0
production point, driven by a SATURATING pulser with beam off.

WHAT QUESTION THIS ANSWERS, AND WHAT IT DOES NOT
------------------------------------------------
run_82 settled the comb on beam: Hwm 2 -> 1 took starved 1-10 ms bins from 26.7% to 3.3%,
and IPD 5 -> 2 made it WORSE at both watermarks (26.7 -> 41.1 at Hwm 2, 3.3 -> 6.7 at
Hwm 1). So IPD is not the evenness lever and this run is NOT a second attempt at that
question — a pulser has no gamma flash, so it cannot measure comb evenness at all.

What it CAN measure, and what run_82 left open, is the readout/transport envelope:

  1. **Does the per-event readout floor keep shortening below IPD 5 at Hwm 1?**
     The model t_ev = n_samples x (4.83 + 0.998 x IPD) was confirmed to -1% at Hwm 2 and
     read ~10% HIGH at Hwm 1 (predicted 196/137 us, measured 215/155) — strict
     serialisation adds a per-event handshake the model omits. That handshake is
     IPD-INDEPENDENT, so as IPD falls it becomes a larger share of the cycle and the
     returns diminish. Five points resolve the curve; two (run_82's) cannot.

  2. **Where does the wire bind?** At the production point one RAW n20 event is 196 kB
     across 8 FEUs (MEASURED: run_89 cosmics, 4.4 GB of FDF for 22 400 events). Multiply
     by the model ceiling and the aggregate host rate is

         IPD 5  5.09 kHz  1.00 GB/s   8.0 Gb/s
         IPD 4  5.67 kHz  1.11 GB/s   8.9 Gb/s
         IPD 3  6.39 kHz  1.25 GB/s  10.0 Gb/s   <- 10 GbE line rate, exactly
         IPD 2  7.33 kHz  1.44 GB/s  11.5 Gb/s   <- over
         IPD 1  8.58 kHz  1.68 GB/s  13.5 Gb/s   <- over

     ⚠ **Prediction: sustained throughput plateaus at IPD ~3 and IPD 2/1 lose events on
     the wire.** If that is what happens, the FEUs are fine and the link is the wall —
     which is a DIFFERENT verdict from "IPD 2 is unsafe", and the two are told apart by
     instrument 3 below.

  3. **FEU-side vs host-side loss, separated.** Log `accepted` (32-bit, per FEU) with
     `dream_scripts/feu_trig_counters.py --latch --watch 2 --csv ...` straight through the
     run. Then per point:
         d(accepted)/dt  vs  events landing in decoded_root
     equal          -> nothing lost downstream; the FEU throttle is the whole story
     accepted higher -> the FEU took the trigger and the event died on the wire/host/disk
     ⚠ closeDrop/fifoDrop are 8-bit and maxFIFOocc 6-bit: they WRAP under saturation.
     Nonzero means "dropping", nothing more.

  ⚠ **A saturating ladder does not by itself license an IPD for beam.** Beam never
  sustains kHz — it delivers ~11 events into a rested buffer and then idles. A point that
  fails here at 8 kHz sustained may still be safe on beam, and a point that passes here is
  not thereby better on beam (run_82 says the opposite for evenness). Treat the result as
  the ENVELOPE, and see the paced companion pass in the plan doc for the beam-like duty.

THE THREE TAIL POINTS (extras, after the ladder, so truncation stays graceful)
  h2i5    Hwm 2 / Lwm 1, IPD 5 — the throughput cost of Hwm 1 measured at SATURATION,
                                 where run_82's -12% whole-gate figure was beam-confounded.
  h2l0i5  Hwm 2 / Lwm 0, IPD 5 — **the Lwm axis has never been varied.** Same Hwm, drain to
                                 EMPTY before BUSY clears instead of to one free slot. If
                                 Lwm matters this is where it shows.
  lt0i5   Hwm 1 / Lwm 0, LocThrot 0 — Main_Trig_LocThrot is documented UNVERIFIED for
                                 EXTERNAL (TCM) triggers. With it off, Hwm 1 should become
                                 inert and throughput jump back to the un-throttled ceiling.
                                 A null here means the watermark works by some other path.

ORDER — palindrome 5 4 3 2 1 | 1 2 3 4 5. There is no beam drift to cancel, but the disk
fills and the FEUs warm as the run proceeds; the palindrome gives every IPD the same mean
position, so any monotone drift cancels the same way it did in run_82.

SIZE — ⚠ REVISED 2026-07-28 11:25 AFTER MEASURING THE WRITE PATH. The first version asked
for 20 000 events/FEU (~3.9 GB/point, ~51 GB). That cannot run here:

  * RunCtrl stages FDFs to `~/july_dream/dream_run/` which is on **`/` (sdb2, the 477 GB
    SK hynix SSD), 97% FULL — 15 GB free**. 51 GB fills the ROOT filesystem after three
    points.
  * `/mnt/data` is **not** an SSD. It is a spinning ST4000NE001 IronWolf: `dd oflag=direct`
    measures **122 MB/s**, 8x below the 1.0 GB/s the IPD-5 point alone would demand.
  * RAM is 15 GB, so vm.dirty_ratio 20% puts the dirty-page ceiling at **~3 GB**.

So each point is now capped at **2000 events/FEU = 392 MB**, which is FIVE TIMES under the
dirty ceiling. The whole burst therefore lands in page cache and the kernel flushes it
during the 27 s inter-sub-run gap — **the disk never backpressures the DAQ mid-point.**

This is not a compromise, it is the correct design: the FEU -> host WIRE transfer still
happens in real time at the full 8-13.5 Gb/s, so the link question is measured exactly as
intended; only the disk write is deferred. What CANNOT be tested on this host is SUSTAINED
saturation, and that is moot — production runs at ~100 Hz x 196 kB = 20 MB/s.

2000 events is ~0.4 s of saturated data and ~2000 dt intervals per point per FEU, against
the ~5000 run_82 used to pin the floor to +-10 us. The floor is a hard edge, so this is
ample. Total: 13 x 392 MB = **~5.1 GB staged**, leaving ~10 GB free on `/`.

run_time is a 0.2 min BACKSTOP for a point that cannot saturate.

TRIGGER — pulser only, no cosmics contamination (pulser-saturating-trigger recipe,
2026-07-22, verified). Apply and restore are in docs/PLAN_2026-07-28_pulser_ipd_ladder.md;
restore is `./switch_mode.py cosmics --go` (or `beam --go`), which re-applies the routing
and verifies it.

HV — the production setpoints, re-asserted per sub-run. They are already there (this run
follows cosmics at the same point), so nothing ramps; carrying them explicitly means the
detector state provably cannot drift across the test.

⚠ beam_type is 'pulser' so `projections/run_stats.py` skips the run — otherwise a quarter
of a million pulser events would land in the physics projection.
"""
# --- repo-root shim (run_config_beam/base live one dir up) ---
import os
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from run_config_beam import Config as BeamConfig

RUN_NUM = int(os.environ.get('RUN_NUM', '90'))

# ---- readout: the production point, held (run_84/run_89) ----
LATENCY = int(os.environ.get('LATENCY', '27'))
N_SAMPLES = int(os.environ.get('N_SAMPLES', '20'))
SAMPLE_PERIOD = 60

# ---- size ----
EVENT_CAP = int(os.environ.get('EVENT_CAP', '2000'))      # per FEU; the real stop condition
SUBRUN_MIN = float(os.environ.get('SUBRUN_MIN', '0.2'))   # backstop only
DIRTY_CEILING_GB = 3.0    # vm.dirty_ratio 20% x 15 GB RAM, MEASURED 2026-07-28
OVERHEAD_S = 27.0                                         # measured across run_79 boundaries
RESUME = os.environ.get('RESUME', '0') == '1'

# ---- MEASURED event size at this point: run_89 cosmics, 4.4 GB FDF / 22 400 events ----
BYTES_PER_EVENT = 196_000     # all 8 FEUs
LINK_GBPS = 10.0

# ---- the ladder. (label, hwm, lwm, ipd, loc_throt); loc_throt None = leave cfg default ----
LADDER = [(f'i{ipd}', 1, 0, ipd, None) for ipd in (5, 4, 3, 2, 1)]
LADDER = LADDER + list(reversed(LADDER))                  # palindrome
EXTRAS = [
    ('h2i5',   2, 1, 5, None),   # Hwm cost at saturation
    ('h2l0i5', 2, 0, 5, None),   # the untested Lwm axis
    ('lt0i5',  1, 0, 5, 0),      # is LocThrot what makes Hwm bite on EXTERNAL triggers?
]
POINTS = LADDER + ([] if os.environ.get('NO_EXTRAS') == '1' else EXTRAS)

# ---- operating point, identical to run_84/run_89 ----
DRIFT_V = 700
RESIST_V = {'A': 540, 'B': 540, 'C': 525, 'D': 520}

# ---- the pulser that drives it (applied by hand; recorded here so the run is self-describing)
PULSER_PERIOD_NS = int(os.environ.get('PULSER_PERIOD_NS', '5000'))   # fixed 200 kHz


def per_event_us(n, ipd):
    """Serialised single-event readout time. Confirmed to -1% at Hwm 2; reads ~10% high at
    Hwm 1, where strict serialisation adds a per-event handshake the model omits."""
    return n * (4.83 + 0.998 * ipd)


def model_rate_hz(n, ipd):
    return 1e6 / per_event_us(n, ipd)


def wire_gbps(n, ipd):
    return model_rate_hz(n, ipd) * BYTES_PER_EVENT * 8 / 1e9


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
        self.n1081b_scan = 'off'        # static pulser trigger; NO scan watcher, no board writes
        self.beam_type = 'pulser'       # keeps the run out of the physics projection

        self.trigger = (
            f'IPD LADDER at Hwm 1 / Lwm 0, SATURATING PULSER, BEAM OFF ({self.run_name}, '
            f'2026-07-28). Measures the readout/transport ENVELOPE below the production '
            f'IPD 5 — not comb evenness, which a flashless pulser cannot see and which '
            f'run_82 already settled (Hwm is the lever; IPD 2 made the comb WORSE at both '
            f'watermarks). Two things are open and this answers both: (1) the per-event '
            f'floor t_ev = n x (4.83 + 0.998 x IPD) was confirmed to -1% at Hwm 2 but reads '
            f'~10% HIGH at Hwm 1 (196/137 predicted vs 215/155 measured) because strict '
            f'serialisation adds an IPD-INDEPENDENT per-event handshake — so the returns '
            f'from lowering IPD must diminish, and five points resolve where; (2) at 196 kB '
            f'per RAW n20 event (MEASURED on run_89) the model ceiling puts IPD 3 at exactly '
            f'10.0 Gb/s aggregate and IPD 2/1 at 11.5/13.5 Gb/s, OVER the host link — so the '
            f'prediction is that throughput plateaus at IPD ~3 and the lower points lose '
            f'events on the WIRE, not in the FEU. FEU-side and host-side loss are separated '
            f'by logging the 32-bit `accepted` register per FEU '
            f'(feu_trig_counters.py --latch --watch 2 --csv) against the events that reach '
            f'decoded_root. ORDER is the palindrome 5 4 3 2 1 | 1 2 3 4 5 so any monotone '
            f'drift cancels; three tail points follow: Hwm 2/Lwm 1 (the throughput cost of '
            f'Hwm 1 at saturation, unconfounded by beam), Hwm 2/Lwm 0 (the Lwm axis, NEVER '
            f'varied) and LocThrot 0 (Main_Trig_LocThrot is documented UNVERIFIED for '
            f'external TCM triggers — with it off Hwm 1 should go inert). Points are '
            f'EVENT-CAPPED at {EVENT_CAP}/FEU ({EVENT_CAP * BYTES_PER_EVENT / 1e6:.0f} MB, '
            f'~0.4 s each), FIVE TIMES under the {DIRTY_CEILING_GB:.0f} GB dirty-page ceiling '
            f'(vm.dirty_ratio 20% x 15 GB RAM), so each burst lands in page cache and the '
            f'kernel flushes it during the 27 s inter-sub-run gap — the disk cannot '
            f'backpressure the DAQ mid-point. That matters because /mnt/data is a SPINNING '
            f'IronWolf measured at 122 MB/s (NOT an SSD) and RunCtrl stages to `/`, which is '
            f'97% full with 15 GB free. The FEU->host wire still runs in real time at the '
            f'full 8-13.5 Gb/s, so the link question is measured exactly as intended; only '
            f'the disk write is deferred. SUSTAINED saturation cannot be tested on this host '
            f'and is moot (production is ~20 MB/s). run_time {SUBRUN_MIN:g} min is a '
            f'backstop. HELD: latency {LATENCY}, n_samples {N_SAMPLES}, {SAMPLE_PERIOD} ns '
            f'sampling, RAW/full readout, drift {DRIFT_V} V, resist A{RESIST_V["A"]}/'
            f'B{RESIST_V["B"]}/C{RESIST_V["C"]}/D{RESIST_V["D"]} V — the production values, '
            f'already standing, carried explicitly so the detector provably cannot drift. '
            f'TRIGGER: M6.D pulser ALONE via set_veto_open.py --lemos 4 (singles dropped, so '
            f'no cosmics contamination), fixed {1e9 / PULSER_PERIOD_NS / 1e3:.0f} kHz '
            f'(period {PULSER_PERIOD_NS} ns) = {1e9 / PULSER_PERIOD_NS / model_rate_hz(N_SAMPLES, 1):.0f}x '
            f'the highest ceiling in the ladder, so every point saturates and the 5 us drive '
            f'granularity stays well under the 20 us steps being resolved. ⚠ A saturating '
            f'ladder measures the envelope, NOT a licence for beam: beam delivers ~11 events '
            f'into a rested buffer and then idles. BEAM OFF, no flash, no physics — '
            f'beam_type=pulser keeps it out of the projection. '
            f'{len(POINTS)} x {EVENT_CAP} events.')

        self.dream_daq_info.update({
            'daq_config_template_path': f'{self.base_out_dir}dream_config/Tcm_Mx17_July.cfg',
            'zero_suppress': False,
            'latency': LATENCY,
            'n_samples_per_waveform': N_SAMPLES,
            'sample_period': SAMPLE_PERIOD,
            'inter_packet_delay': POINTS[0][3],
            'ovr_wrn_hwm': POINTS[0][1],
            'ovr_wrn_lwm': POINTS[0][2],
            'daq_run_events': EVENT_CAP,
        })

        drift_hvs = {'0': DRIFT_V, '1': DRIFT_V, '2': DRIFT_V, '3': DRIFT_V}
        resist_hvs = {'1': RESIST_V['A'], '2': RESIST_V['B'],
                      '3': RESIST_V['C'], '4': RESIST_V['D']}

        self.sub_runs = []
        for k, (label, hwm, lwm, ipd, lt) in enumerate(POINTS):
            sr = {
                'sub_run_name': f'pulsipd_{label}_{k:04d}',
                'run_time': SUBRUN_MIN,
                'post_pause_s': 0,
                'daq_run_events': EVENT_CAP,
                'inter_packet_delay': ipd,
                'ovr_wrn_hwm': hwm,
                'ovr_wrn_lwm': lwm,
                'latency': LATENCY,
                'n_samples_per_waveform': N_SAMPLES,
                'hvs': {'5': dict(resist_hvs), '9': dict(drift_hvs)},
            }
            if lt is not None:
                sr['loc_throt'] = lt
            self.sub_runs.append(sr)

        # Scintillator PMT holds, from the detector table (same construction the beam and
        # cosmics generators use) so they track the table instead of being pinned here.
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
    out = f'config/json_run_configs/run_config_ipd_ladder_pulser.json'
    c.write_to_file(out)

    print('=== IPD ladder at Hwm 1 / Lwm 0, saturating pulser, beam off ===')
    print(f'run       : {c.run_name}   ({out})')
    print(f'held      : latency {LATENCY}  n {N_SAMPLES}  {SAMPLE_PERIOD} ns  RAW  '
          f'cap {EVENT_CAP} ev/FEU  backstop {SUBRUN_MIN:g} min')
    print(f'pulser    : fixed {1e9 / PULSER_PERIOD_NS / 1e3:.0f} kHz (period {PULSER_PERIOD_NS} ns, '
          f'width 100) — set by hand, see docs/PLAN_2026-07-28_pulser_ipd_ladder.md')
    print(f'event size: {BYTES_PER_EVENT / 1e3:.0f} kB (MEASURED run_89: 4.4 GB / 22 400 ev)\n')
    hdr = (f"{'sub-run':<20} {'Hwm':>4} {'Lwm':>4} {'LTh':>4} {'IPD':>4} {'t_ev us':>8} "
           f"{'ceiling':>9} {'GB/s':>6} {'Gb/s':>6}  wire")
    print(hdr)
    print('-' * len(hdr))
    for sr, (label, hwm, lwm, ipd, lt) in zip(c.sub_runs, POINTS):
        t_ev = per_event_us(N_SAMPLES, ipd)
        hz = model_rate_hz(N_SAMPLES, ipd)
        gbps = wire_gbps(N_SAMPLES, ipd)
        verdict = ('AT the 10 GbE line rate' if abs(gbps - LINK_GBPS) <= 0.3 * LINK_GBPS / 10
                   else 'OVER the 10 GbE link' if gbps > LINK_GBPS else 'fits')
        print(f"{sr['sub_run_name']:<20} {hwm:>4} {lwm:>4} {str(lt if lt is not None else '-'):>4} "
              f"{ipd:>4} {t_ev:>8.0f} {hz / 1e3:>7.2f} kHz {gbps / 8:>6.2f} {gbps:>6.1f}  {verdict}")

    gb = len(POINTS) * EVENT_CAP * BYTES_PER_EVENT / 1e9
    mins = sum(EVENT_CAP / model_rate_hz(N_SAMPLES, p[3]) + OVERHEAD_S for p in POINTS) / 60
    per_mb = EVENT_CAP * BYTES_PER_EVENT / 1e6
    print(f'\n{len(POINTS)} points x {per_mb:.0f} MB = ~{gb:.1f} GB staged  '
          f'~{mins:.1f} min wall-clock (the {OVERHEAD_S:.0f} s/sub-run overhead dominates)')
    print(f'per-point burst {per_mb:.0f} MB vs the {DIRTY_CEILING_GB * 1e3:.0f} MB dirty ceiling '
          f'-> {DIRTY_CEILING_GB * 1e3 / per_mb:.1f}x margin, so it stays in page cache')
    print('\nLaunch: docs/PLAN_2026-07-28_pulser_ipd_ladder.md — the trigger must be applied FIRST.')
