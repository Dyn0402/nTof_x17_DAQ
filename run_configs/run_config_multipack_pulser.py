#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_multipack_pulser.py — 2026-07-28. Can the missing 34% of the per-FEU link be
recovered by filling the jumbo frame?

WHERE THIS COMES FROM
---------------------
run_92 established that the DAQ ceiling is a **PER-FEU output cap of ~83 MB/s**, invariant
across 4x in event size (n10/n20/n40) and 8x in FEU count (8/4/2/1). Aggregate scales
linearly, so it is not the host, not the 10 GbE aggregate, not the disk. 83 MB/s is
~0.66 Gb/s = ~66% of a 1 GbE port. run_90 had already shown IPD cannot move it.

**The candidate explanation is that we are not filling the frame.** The template says

    # UdpChan_MultiPackThr = Eth_MTU - MaxSmpData - 60
    # MaxSmpData = 2220 bytes
    # Eth_Mtu depends on Eth NIC but is less 8kbytes
    # E.g. for MTU 7k th parameter is 4888
    Feu * UdpChan_MultiPackEnb 1
    Feu * UdpChan_MultiPackThr 4888

4888 + 2220 + 60 = 7168 = 7 KiB. So the STANDING VALUE IS SIZED FOR A 7168-BYTE FRAME —
while the host NIC `enp4s0` is at **MTU 9000** (set during the 10 GbE switch swap). Every
datagram is therefore closed ~1800 bytes short of what the path allows.

    MTU 7168 -> Thr 4888   (what we run today)
    MTU 8192 -> Thr 5912
    MTU 9000 -> Thr 6720

THE LADDER, AND WHY EACH POINT IS HERE
--------------------------------------
    mp4888_base   the standing production value — must reproduce 83 MB/s/FEU
    mp2888        ⚠ LOW CONTROL. Deliberately smaller frames. If throughput does NOT fall
                  here, the knob is INERT and any null at the top of the ladder means
                  nothing — exactly the trap the 2019 "raise the HWM" sweep fell into
                  (RunCtrl silently clamped it, so nothing was ever varied).
    mp5912        the formula's value for an 8 KiB frame
    mp6720        the formula's value for the host's actual MTU 9000
    mpoff         MultiPackEnb 0 — no aggregation at all, the floor of the knob's range
    mp4888_close  closing bracket; run_92's bracket came back 11% low under concurrent
                  decode load, so this one is not optional

READ IT AS: **per-FEU MB/s**, not aggregate, and not kHz. That is the invariant run_92
identified, and it is the quantity this run is trying to move.

⚠ THE HAZARD, AND WHY THE INTEGRITY CHECK IS MANDATORY
The comb study (2026-07-19) found that above MTU − MaxDataPacketSize the last add can
overrun the frame and **the last packet is dropped**. The template's own note that the FEU
Eth_Mtu "is less 8kbytes" suggests the FEU transmit side may cap below 8 KiB even though the
host receives 9000 — in which case mp6720 (and possibly mp5912) will CORRUPT rather than
speed up. That failure is silent on the wire and shows up only as eventId discontinuities,
so every point is judged on gap% and cross-FEU spread BEFORE its throughput is quoted.
A corrupt point that "goes faster" is just dropping data.

Note also that jumbo cannot be verified by ping on this subnet (a jumbo ping is a known
FALSE failure here), so this run IS the end-to-end jumbo path test.

Outcomes:
  throughput rises with Thr, integrity clean  -> the frame was under-filled; adopt the value
                                                 matching the real path MTU
  throughput flat, mp2888/mpoff DO drop       -> knob live, but 83 MB/s is set elsewhere
                                                 (FEU internal, not framing)
  throughput flat and mp2888/mpoff flat too   -> knob inert; ignore the whole run

HELD: Hwm 1 / Lwm 0, IPD 5, n20, latency 27, 60 ns, RAW, production HV, saturating fixed
200 kHz pulser into M4.C lemo4 alone. Beam off.

⚠ Y88 and Cs137 sources are sitting near the MMs. In RAW the event size is fixed regardless
of occupancy, so they cannot touch any number here; the trigger is pulser-only so they
cannot reach it either. (They WOULD matter in ZS, where hits set the event size.)
"""
# --- repo-root shim ---
import os
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from run_config_beam import Config as BeamConfig

RUN_NUM = int(os.environ.get('RUN_NUM', '94'))

LATENCY, N_SAMPLES, SAMPLE_PERIOD, IPD = 27, 20, 60, 5
EVENT_CAP = int(os.environ.get('EVENT_CAP', '2000'))
SUBRUN_MIN = float(os.environ.get('SUBRUN_MIN', '0.2'))
RESUME = os.environ.get('RESUME', '0') == '1'

KB_PER_FEU = 24.5          # MEASURED, n20 RAW
PER_FEU_MBS = 83.0         # MEASURED, run_92, invariant over event size and FEU count
MAX_SMP_DATA, HDR = 2220, 60

DRIFT_V = 700
RESIST_V = {'A': 540, 'B': 540, 'C': 525, 'D': 520}

# (label, multipack_enb, multipack_thr)
POINTS = [
    ('mp4888_base',  1, 4888),   # standing production value  (frame 7168 = 7 KiB)
    ('mp2888',       1, 2888),   # LOW CONTROL — must get WORSE or the knob is inert
    ('mp5912',       1, 5912),   # frame 8192 = 8 KiB
    ('mp6720',       1, 6720),   # frame 9000 = the host NIC's actual MTU
    ('mpoff',        0, None),   # no aggregation at all
    ('mp4888_close', 1, 4888),   # bracket
]


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
            f'UDP MULTI-PACK / JUMBO-FRAME LADDER ({self.run_name}, 2026-07-28), saturating '
            f'200 kHz pulser, beam off. run_92 found the DAQ ceiling is a PER-FEU cap of '
            f'~83 MB/s (~0.66 Gb/s, ~66% of 1 GbE), invariant over 4x in event size and 8x '
            f'in FEU count, so it is not the host, the 10 GbE aggregate, the disk or IPD. '
            f'This run tests the leading candidate: WE ARE NOT FILLING THE FRAME. The '
            f'template runs UdpChan_MultiPackThr 4888, which by its own formula '
            f'(Thr = Eth_MTU - MaxSmpData(2220) - 60) is sized for a 7168-byte frame, while '
            f'the host NIC enp4s0 is at MTU 9000 — every datagram closes ~1800 bytes short. '
            f'Ladder 4888 (standing) / 2888 (LOW CONTROL) / 5912 (8 KiB) / 6720 (MTU 9000) / '
            f'MultiPackEnb 0, bracketed by 4888. ⚠ mp2888 and mpoff exist so a null cannot be '
            f'mistaken for an INERT knob — the failure mode that made the 2019 HWM sweep a '
            f'false null. ⚠ Above MTU - MaxDataPacketSize the last add overruns the frame and '
            f'the LAST PACKET IS DROPPED (comb study 07-19), and the template warns the FEU '
            f'Eth_Mtu "is less 8kbytes", so 6720 (and maybe 5912) may CORRUPT instead of '
            f'speed up — silently, visible only as eventId discontinuities. Every point is '
            f'judged on gap% and cross-FEU spread BEFORE its throughput is quoted; a corrupt '
            f'point that looks faster is just dropping data. Jumbo cannot be checked by ping '
            f'on this subnet (known false failure), so this run IS the end-to-end jumbo test. '
            f'READ per-FEU MB/s, not aggregate. HELD: Hwm 1 / Lwm 0, IPD {IPD}, n{N_SAMPLES}, '
            f'latency {LATENCY}, {SAMPLE_PERIOD} ns, RAW, production HV. ⚠ Y88 + Cs137 '
            f'sources are near the MMs; in RAW the event size is fixed regardless of '
            f'occupancy and the trigger is pulser-only, so neither can touch these numbers.')

        self.dream_daq_info.update({
            'daq_config_template_path': f'{self.base_out_dir}dream_config/Tcm_Mx17_July.cfg',
            'zero_suppress': False,
            'latency': LATENCY,
            'n_samples_per_waveform': N_SAMPLES,
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
        for k, (label, enb, thr) in enumerate(POINTS):
            sr = {
                'sub_run_name': f'mpack_{label}_{k:04d}',
                'run_time': SUBRUN_MIN, 'post_pause_s': 0,
                'daq_run_events': EVENT_CAP,
                'inter_packet_delay': IPD, 'ovr_wrn_hwm': 1, 'ovr_wrn_lwm': 0,
                'latency': LATENCY, 'n_samples_per_waveform': N_SAMPLES,
                'multipack_enb': enb,
                'hvs': {'5': dict(resist_hvs), '9': dict(drift_hvs)},
            }
            if thr is not None:
                sr['multipack_thr'] = thr
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
    out = 'config/json_run_configs/run_config_multipack_pulser.json'
    c.write_to_file(out)
    print('=== UDP multi-pack / jumbo ladder (saturating pulser, beam off) ===')
    print(f'run: {c.run_name}   Hwm 1 / Lwm 0, IPD {IPD}, n{N_SAMPLES}, RAW')
    print(f'baseline to beat: {PER_FEU_MBS:.0f} MB/s per FEU '
          f'(= {PER_FEU_MBS * 1e6 / (KB_PER_FEU * 1e3) / 1e3:.2f} kHz at {KB_PER_FEU} kB/FEU/event)\n')
    hdr = f"{'sub-run':<22}{'MPEnb':>7}{'Thr':>7}{'implied frame':>15}   note"
    print(hdr); print('-' * len(hdr))
    notes = {4888: 'STANDING VALUE (7 KiB)', 2888: 'LOW CONTROL — must get worse',
             5912: '8 KiB', 6720: 'host NIC MTU 9000'}
    for sr, (label, enb, thr) in zip(c.sub_runs, POINTS):
        frame = f'{thr + MAX_SMP_DATA + HDR} B' if thr else '—'
        print(f"{sr['sub_run_name']:<22}{enb:>7}{str(thr or '-'):>7}{frame:>15}   "
              f"{notes.get(thr, 'no aggregation')}")
    print(f'\n{len(POINTS)} points x ~{EVENT_CAP * 1.7 * KB_PER_FEU * 8 / 1e6:.2f} GB '
          f'= ~{len(POINTS) * EVENT_CAP * 1.7 * KB_PER_FEU * 8 / 1e6:.1f} GB, ~4 min')
    print('⚠ CHECK gap% AND cross-FEU spread BEFORE quoting any throughput.')
