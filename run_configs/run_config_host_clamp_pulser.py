#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_host_clamp_pulser.py — 2026-07-28, follow-up to run_90.
WHAT CLAMPS THE DAQ AT 658 MB/s?

run_90 (saturating 200 kHz pulser, beam off) found the per-event readout floor shortens
LINEARLY with IPD all the way to IPD 1 — 20 us per step, with a flat +14 us Hwm-1
serialisation handshake — and yet **delivered throughput was IDENTICAL at every IPD**:

    IPD   floor us   duty %   kHz    MB/s
      5      210      70.4    3.35    657
      4      190      63.8    3.36    658
      3      170      57.1    3.36    658
      2      150      50.4    3.36    658
      1      130      43.7    3.36    658

The duty cycle falls exactly as fast as the readout speeds up. So the binding constraint is
NOT the FEU (its floor keeps improving) and NOT the 10 GbE link (658 MB/s = 5.3 Gb/s, about
HALF the line rate). Something downstream clamps, and run_90 could not say what.

THE DISCRIMINATOR
-----------------
Two independent knobs change bytes-per-event WITHOUT changing the trigger rate, and the
answer is read off whichever column stays constant:

  * **n_samples** {10, 20, 40} at 8 FEUs. Halving n halves the bytes AND halves t_ev, so
    this alone is degenerate — but n40 is a deliberate CONTROL: at n40 the FEU ceiling
    (1/(40 x 9.82 us) = 2.55 kHz) drops BELOW the observed 3.36 kHz clamp, so if n40 lands
    on 2.55 kHz that re-confirms the model governs whenever the FEU is the slow element.

  * **FEU COUNT** {8, 4, 2, 1} at fixed n20. **This is the clean one.** Dropping FEUs cuts
    the aggregate bytes per event (196 -> 98 -> 49 -> 24.5 kB) while leaving each FEU's own
    readout time, and therefore the 210 us floor, completely untouched. So:

        clamp is aggregate BYTES  -> rate RISES toward the floor limit 1/210us = 4.76 kHz
                                     and MB/s stays pinned near 658
        clamp is EVENTS/triggers  -> rate STAYS 3.36 kHz and MB/s falls 658 -> 82

    Those predictions differ by 40% at f2/f1 and are impossible to confuse.

Also carries the one point run_90 lost when `/` filled: **LocThrot 0**. Main_Trig_LocThrot
(0x100008 bit 30) is documented UNVERIFIED for EXTERNAL (TCM) triggers. With it off, Hwm 1
should go inert and the floor should collapse from 210 us toward the drive granularity — the
same signature Hwm 2 / Lwm 0 produced in run_90 (floor 5 us, back-to-back pairs).

⚠ Read FEU 01 for every per-point number: it is the one FEU present in every subset.

SIZE — same page-cache-resident design as run_90 (see that generator for the write-path
measurements: `/` is the 477 GB SSD RunCtrl stages to, /mnt/data is a 122 MB/s spinning
IronWolf, RAM 15 GB => ~3 GB dirty ceiling). ⚠ `daq_run_events` is NOT exact: run_90 asked
2000/FEU and got 3364, so budget ~1.7x. At n40 the event doubles to ~392 kB => ~1.3 GB for
that point; everything else is ~660 MB or less.

TRIGGER — identical to run_90: M6.D fixed 200 kHz (`set_pulser.py --fixed --period 5000
--width 100`) into M4.C lemo4 alone (`set_veto_open.py --lemos 4`). Restore is
`set_pulser.py` then `./switch_mode.py cosmics --go`.
"""
# --- repo-root shim (run_config_beam/base live one dir up) ---
import os
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from run_config_beam import Config as BeamConfig

RUN_NUM = int(os.environ.get('RUN_NUM', '92'))

LATENCY = int(os.environ.get('LATENCY', '27'))
SAMPLE_PERIOD = 60
IPD = int(os.environ.get('IPD', '5'))
EVENT_CAP = int(os.environ.get('EVENT_CAP', '2000'))
SUBRUN_MIN = float(os.environ.get('SUBRUN_MIN', '0.2'))
RESUME = os.environ.get('RESUME', '0') == '1'

# MEASURED on run_90: 196 kB/event across 8 FEUs at n20 => 24.5 kB per FEU per n20 event.
KB_PER_FEU_N20 = 24.5
CLAMP_MBS = 658.0        # MEASURED on run_90, identical at every IPD
FLOOR_US_N20_IPD5 = 210  # MEASURED on run_90 at Hwm 1

DRIFT_V = 700
RESIST_V = {'A': 540, 'B': 540, 'C': 525, 'D': 520}

ALL_FEUS = [1, 2, 3, 4, 5, 6, 7, 8]

# (label, n_samples, included_feus, loc_throt)
POINTS = [
    ('base_n20f8', 20, ALL_FEUS,     None),   # reproduces run_90: expect 3.36 kHz / 658 MB/s
    ('n10f8',      10, ALL_FEUS,     None),   # half the bytes, FEU ceiling 10.2 kHz
    ('n40f8',      40, ALL_FEUS,     None),   # CONTROL: FEU ceiling 2.55 kHz < the clamp
    ('n20f4',      20, [1, 2, 3, 4], None),   # ---- the clean discriminator ----
    ('n20f2',      20, [1, 2],       None),
    ('n20f1',      20, [1],          None),
    ('n20f8_lt0',  20, ALL_FEUS,     0),      # the point run_90 lost when `/` filled
    ('close_n20f8', 20, ALL_FEUS,    None),   # closing bracket, must reproduce point 1
]


def per_event_us(n, ipd=IPD):
    return n * (4.83 + 0.998 * ipd)


def kb_per_event(n, nfeu):
    return KB_PER_FEU_N20 * (n / 20.0) * nfeu


def predictions(n, nfeu):
    """(feu_ceiling_kHz, byte_limited_kHz, event_limited_kHz) -> what each hypothesis says."""
    # The measured floor scales with t_ev and carries the flat +14 us Hwm-1 handshake.
    floor_us = per_event_us(n) + 14.0
    feu_khz = 1e3 / floor_us
    byte_khz = min(feu_khz, CLAMP_MBS * 1e3 / kb_per_event(n, nfeu) / 1e3)
    event_khz = min(feu_khz, 3.36)
    return feu_khz, byte_khz, event_khz


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
        self.n1081b_scan = 'off'
        self.beam_type = 'pulser'

        self.trigger = (
            f'HOST-CLAMP DISCRIMINATOR ({self.run_name}, 2026-07-28), saturating 200 kHz '
            f'pulser, beam off. run_90 showed the per-event readout floor shortens LINEARLY '
            f'with IPD to IPD 1 (20 us/step, flat +14 us Hwm-1 handshake) while delivered '
            f'throughput stayed IDENTICAL at 3.36 kHz / 658 MB/s at every IPD — duty fell '
            f'70.4% -> 43.7%, exactly cancelling the faster readout. So the binding '
            f'constraint is neither the FEU (its floor keeps improving) nor the 10 GbE link '
            f'(658 MB/s = 5.3 Gb/s, half the line rate). This run identifies it by changing '
            f'bytes-per-event WITHOUT changing the trigger rate, on two independent knobs. '
            f'The clean one is FEU COUNT {{8,4,2,1}} at fixed n20: dropping FEUs cuts '
            f'aggregate bytes 196->98->49->24.5 kB/event but leaves each FEU\'s own readout, '
            f'and hence the 210 us floor, untouched — so an aggregate-BYTE clamp predicts the '
            f'rate RISES toward 1/210us = 4.76 kHz with MB/s pinned near 658, while an '
            f'EVENT/trigger clamp predicts the rate STAYS 3.36 kHz with MB/s falling to 82. '
            f'Those differ by 40% at f2/f1. n_samples {{10,20,40}} is the second knob; n40 is '
            f'a deliberate CONTROL because its FEU ceiling (2.55 kHz) drops BELOW the '
            f'observed clamp, so landing on 2.55 kHz re-confirms the model governs whenever '
            f'the FEU is the slow element. Also carries LocThrot 0, the point run_90 lost '
            f'when `/` filled — Main_Trig_LocThrot is documented UNVERIFIED for external TCM '
            f'triggers, and with it off Hwm 1 should go inert and the floor collapse toward '
            f'the drive granularity (the signature Hwm 2 / Lwm 0 gave in run_90: floor 5 us, '
            f'back-to-back pairs). HELD: Hwm 1 / Lwm 0, IPD {IPD}, latency {LATENCY}, '
            f'{SAMPLE_PERIOD} ns sampling, RAW, production HV. Opens and CLOSES on n20/f8 so '
            f'a drift cannot fake a trend. ⚠ Read FEU 01 for every point — the one FEU in '
            f'every subset. BEAM OFF, no physics; beam_type=pulser keeps it out of the '
            f'projection.')

        self.dream_daq_info.update({
            'daq_config_template_path': f'{self.base_out_dir}dream_config/Tcm_Mx17_July.cfg',
            'zero_suppress': False,
            'latency': LATENCY,
            'n_samples_per_waveform': POINTS[0][1],
            'sample_period': SAMPLE_PERIOD,
            'inter_packet_delay': IPD,
            'ovr_wrn_hwm': 1,
            'ovr_wrn_lwm': 0,
            'daq_run_events': EVENT_CAP,
        })

        drift_hvs = {'0': DRIFT_V, '1': DRIFT_V, '2': DRIFT_V, '3': DRIFT_V}
        resist_hvs = {'1': RESIST_V['A'], '2': RESIST_V['B'],
                      '3': RESIST_V['C'], '4': RESIST_V['D']}

        self.sub_runs = []
        for k, (label, nsmp, feus, lt) in enumerate(POINTS):
            sr = {
                'sub_run_name': f'clamp_{label}_{k:04d}',
                'run_time': SUBRUN_MIN,
                'post_pause_s': 0,
                'daq_run_events': EVENT_CAP,
                'inter_packet_delay': IPD,
                'ovr_wrn_hwm': 1,
                'ovr_wrn_lwm': 0,
                'latency': LATENCY,
                'n_samples_per_waveform': nsmp,
                'included_feus': list(feus),
                'hvs': {'5': dict(resist_hvs), '9': dict(drift_hvs)},
            }
            if lt is not None:
                sr['loc_throt'] = lt
            self.sub_runs.append(sr)

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
    out = 'config/json_run_configs/run_config_host_clamp_pulser.json'
    c.write_to_file(out)

    print('=== host-clamp discriminator (saturating pulser, beam off) ===')
    print(f'run: {c.run_name}   Hwm 1 / Lwm 0, IPD {IPD}, latency {LATENCY}, RAW')
    print(f'run_90 measured: {CLAMP_MBS:.0f} MB/s / 3.36 kHz at EVERY IPD, '
          f'floor {FLOOR_US_N20_IPD5} us at n20\n')
    hdr = (f"{'sub-run':<22}{'n':>4}{'FEUs':>6}{'LTh':>5}{'kB/ev':>8}{'floor':>7}"
           f"{'FEUmax':>8}{'if BYTES':>10}{'if EVENTS':>11}")
    print(hdr); print('-' * len(hdr))
    for sr, (label, nsmp, feus, lt) in zip(c.sub_runs, POINTS):
        feu_k, byte_k, ev_k = predictions(nsmp, len(feus))
        print(f"{sr['sub_run_name']:<22}{nsmp:>4}{len(feus):>6}"
              f"{str(lt if lt is not None else '-'):>5}{kb_per_event(nsmp, len(feus)):>8.0f}"
              f"{per_event_us(nsmp) + 14:>7.0f}{feu_k:>7.2f}k{byte_k:>9.2f}k{ev_k:>10.2f}k")
    tot_gb = sum(EVENT_CAP * 1.7 * kb_per_event(n, len(f)) for _, n, f, _ in POINTS) / 1e6
    print(f'\n{len(POINTS)} points, ~{tot_gb:.1f} GB staged (the 1.7x daq_run_events overshoot '
          f'is folded in), ~4 min')
    print('THE ANSWER IS THE f2/f1 ROWS: 4.76k = aggregate bytes, 3.36k = events/triggers.')
