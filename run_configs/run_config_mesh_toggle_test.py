#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_config_mesh_toggle_test.py — DIAGNOSTIC, 2026-07-22. Not a physics run.

Force the mesh charge-injection circuit (M6/.245 SEC_B outputs) ON/OFF on a fixed short
cadence with EVERYTHING ELSE HELD, so the DREAM rate and the n_TOF stream wall-flash
amplitude can be correlated against a known, operator-driven square wave.

WHY: on 2026-07-22 the SiPM walls collapsed (~1/40 gain -> ~1 event/pulse) whenever M6.B
outputs were disabled, and recovered when re-enabled — MM HV held constant at drift 600 /
resist 540 throughout, so HV is excluded. The first toggle run (2026-07-22 21:00) measured
DREAM 283.4 MB/min mesh-ON vs 4.6 mesh-OFF (61x) over 4 ON / 3 OFF sub-runs, and the n_TOF
wall flash showed the coupling is a PUMPED RAIL: still live 5 s after mesh-off, dead by
21 s, but back to full amplitude within 1 s of mesh-on. Slow decay, instant recovery.
The dropout handoff notes M6.B's outputs feed "the two ramp" generators, the likely route.

*** TWO ENABLE LAYERS — 2026-07-22 21:45, the hard lesson. *** A section's signal path
needs BOTH the per-channel `status` (get/set_output_channel_configuration, what the scan
schedule's mesh_b toggles) AND the function-level `lemo_enables`
(get_function_configuration). They are separate registers and both must be ON. The board's
WEB GUI drives lemo_enables; the SDK can only READ them for a fanout section — there is no
FN_FANOUT in the SDK's FunctionType and no configure_fanout — so lemo enables are
GUI-only. An evening was lost to one side setting `status` and the other setting
`lemo_enables` while both reported success. Known-good M6 lemo enables, from snapshots
taken while the walls demonstrably worked: A [0,1,2,3] · B [0,1,2,3] · C [1,2,3] · D
[0,1,2,3]. The GUI is 1-BASED on channels (GUI "Output 1" = lemo 0).

Consequently two earlier conclusions are WITHDRAWN: that taking M6.B "fully dead" behaved
like outputs-only (it was never a true full shutdown), and that M6.C could not be involved
(its lemo layer was never checked at the time).

    docs/HANDOFF_2026-07-22_sipm_wall_dropouts.md
    n1081b/n1081b_module_map.py::_module6   (A PS/T0 fanout / B mesh inject /
                                             C SiPM enable-blank / D pulser)

WHAT IS HELD: plastic threshold 1.41 MIP (A-185/B-217/C-245/D-210) applied every sub-run,
drift 600 / resist 530 on every sub-run (so the HV ramp between sub-runs is a no-op and the
cadence stays tight), readout identical to run_67 (RAW n32 x 60 ns, latency 35, IPD 5,
OvrWrnHwm 2 / Lwm 1). The ONLY thing that changes is M6.B output_status, via the
mtestOn/mtestOff schedule tags. Outputs only — the same knob as the historical mesh_b that
produced the measured 28x rate change.

*** SAMPLING CAVEAT — read before choosing SUBRUN_MIN. *** The n_TOF correlation observable
is the wall gamma-flash amplitude from stream1_monitor/wall_probe.py, which measures the
FIRST flash in each stream file. n_TOF completes a stream file every ~60-75 s, so the wall
observable is sampled only ~once a minute, near each file's START. With 1-minute sub-runs
most files straddle two sub-runs of OPPOSITE mesh state and cannot be assigned — expect only
a minority of cleanly-contained files. 2-minute sub-runs guarantee at least one contained
file per state. Default is 1 min (as requested); override with SUBRUN_MIN=2.
The DREAM-side observable has no such limit — it is continuous within each sub-run.

Analysis: analyze_mesh_toggle.py (aligns stream files to sub-run windows, probes each,
and flags straddling files as ambiguous rather than mis-assigning them). Remember EOS
mtimes are UTC and ours are CEST — a silent 2 h offset.

    .venv/bin/python run_config_mesh_toggle_test.py            # 16 x 1 min
    SUBRUN_MIN=2 .venv/bin/python run_config_mesh_toggle_test.py   # 16 x 2 min (recommended)
    ./start_run.sh run_config_mesh_toggle_test.json

PRE-RUN: same standing state as run_67 — scint --singles --ps-pickup, PS delay 1800, M6.B
outputs ON (the first sub-run is mtestOn anyway). No board setup needed if run_67's handoff
is still in place.
TEARDOWN: the scan watcher's snapshot restore returns M6.B to its found state on exit;
confirm with n1081b/inspect_m6_sections.py --sections B before starting physics again.
"""
import os

# --- repo-root shim (run_config_beam/base live one dir up) ---
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from run_config_beam import Config as BeamConfig

LATENCY, N_SAMPLES, SAMPLE_PERIOD, IPD = 35, 32, 60, 5
OVR_WRN_HWM, OVR_WRN_LWM = 2, 1
DRIFT, RESIST = 600, 530
N_CYCLES = 8                     # 8 x (on, off) = 16 sub-runs
SUBRUN_MIN = float(os.environ.get('SUBRUN_MIN', '1'))


class Config(BeamConfig):
    def _set_defaults(self, config_path=None):
        super()._set_defaults(config_path)

        self.run_name = os.environ.get('RUN_NAME', 'mesh_toggle_test')
        self.run_out_dir = f'{self.data_out_dir}{self.run_name}/'
        self.dream_daq_info['run_directory'] = f'/home/mx17/july_dream/dream_run/{self.run_name}/'
        self.dream_daq_info['data_out_dir'] = f'{self.run_out_dir}'
        self.processor_info['run_dir'] = f'{self.run_out_dir}'
        self.hv_info['run_out_dir'] = self.run_out_dir
        self.resume = False
        self.n1081b_scan = 'on'          # mtestOn/mtestOff drive M6.B per sub-run

        self.trigger = (
            f'DIAGNOSTIC mesh-toggle square wave ({self.run_name}, 2026-07-22). '
            f'{N_CYCLES} x (mesh ON, mesh OFF) x {SUBRUN_MIN:g} min, everything else held: '
            f'plastic 1.41 MIP (A-185/B-217/C-245/D-210), drift {DRIFT} / resist {RESIST}, '
            f'RAW n32 x 60 ns, latency 35, IPD 5, OvrWrnHwm 2. ONLY M6.B output_status '
            f'changes (mtestOn/mtestOff). Purpose: correlate the DREAM rate AND the n_TOF '
            f'stream wall-flash amplitude against a known mesh square wave, to find how '
            f'M6.B couples to the SiPM wall gain. scint --singles --ps-pickup, N93B window '
            f'~1->81 ms. Ar/Iso 90/10, 3He, no Pb. NOT a physics run.')

        self.dream_daq_info.update({
            'daq_config_template_path': f'{self.base_out_dir}dream_config/Tcm_Mx17_July.cfg',
            'zero_suppress': False,
            'latency': LATENCY,
            'n_samples_per_waveform': N_SAMPLES,
            'sample_period': SAMPLE_PERIOD,
            'inter_packet_delay': IPD,
        })

        hvs = {'5': {'1': RESIST, '2': RESIST, '3': RESIST, '4': RESIST},
               '9': {'0': DRIFT, '1': DRIFT, '2': DRIFT, '3': DRIFT}}

        self.sub_runs = []
        k = 0
        for _ in range(N_CYCLES):
            for tag in ('mtestOn', 'mtestOff'):
                self.sub_runs.append({
                    'sub_run_name': f'{tag}_{k:03d}',
                    'run_time': SUBRUN_MIN, 'post_pause_s': 0,
                    'ovr_wrn_hwm': OVR_WRN_HWM, 'ovr_wrn_lwm': OVR_WRN_LWM,
                    'hvs': {s: dict(v) for s, v in hvs.items()},
                })
                k += 1

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
    out = f'config/json_run_configs/run_config_{c.run_name}.json'
    c.write_to_file(out)
    n = len(c.sub_runs)
    data_min = sum(sr['run_time'] for sr in c.sub_runs)
    print('=== mesh_toggle_test — DIAGNOSTIC mesh ON/OFF square wave ===')
    print(f'wrote     : {out}')
    print(f'held      : plastic 1.41 MIP, drift {DRIFT} / resist {RESIST}, RAW n32, '
          f'IPD {IPD}, Hwm {OVR_WRN_HWM}')
    print(f'varying   : M6.B output_status ONLY (mtestOn / mtestOff)')
    print(f'sub-runs  : {n} x {SUBRUN_MIN:g} min = {data_min:.0f} min data '
          f'(~{data_min + n:.0f} min wall)')
    if SUBRUN_MIN < 2:
        print('  !! SUBRUN_MIN < 2: n_TOF files (~60-75 s) will straddle sub-runs and')
        print('     most will be unassignable. SUBRUN_MIN=2 is recommended.')
    print('first 4   : ' + ', '.join(sr['sub_run_name'] for sr in c.sub_runs[:4]))
