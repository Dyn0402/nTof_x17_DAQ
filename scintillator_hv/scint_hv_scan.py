#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Scintillator HV scan.

Steps the configured scint channels through SCAN_VOLTAGES, advancing to the
next voltage once PROTONS_PER_STEP_E10 protons have been delivered (read from
beam_state.json). A background thread logs HV + beam to CSV the whole time.

Opens its OWN CAEN session (keepalive + auto_reconnect) alongside the DAQ's
hv_control.py — supported by the mainframe as long as the channels are
disjoint, which is enforced at startup. Only ever touches SCINT_CHANNELS.

Usage:
    cd <repo root>
    python scintillator_hv/scint_hv_scan.py [label]

Behaviour:
  * Ramps each step and waits (up to RAMP_TIMEOUT_S) for vmon to reach target.
  * Waits for PROTONS_PER_STEP_E10 protons at each step (0 = advance on ramp).
  * On finish OR Ctrl-C: leaves channels per cfg.END_ACTION (default 'nominal'
    = ramp each plastic back to its operating voltage). Never touches a DAQ
    channel.

Do NOT SIGKILL this — let Ctrl-C run so the channels are left safe and the
CAEN session closes cleanly.
"""

import os
import sys
import time
import threading
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scint_hv_config as cfg
import scint_hv_lib as lib
from caen_hv_py.exceptions import CAENHVError


def apply_channel_limits(caen_hv):
    """Optionally set current limit / trip time on each scint channel."""
    for c in cfg.SCINT_CHANNELS:
        slot, ch = int(c['slot']), int(c['channel'])
        if cfg.I0SET_UA is not None:
            caen_hv.set_ch_i0set(slot, ch, cfg.I0SET_UA)
        if cfg.TRIP_S is not None:
            caen_hv.set_ch_trip(slot, ch, cfg.TRIP_S)


def _set_and_ramp(caen_hv, targets):
    """
    Set each channel to its target voltage and wait until every channel's vmon
    is within tolerance. `targets` is a list of (channel_config, target_v) pairs
    (channel configs are dicts, so a list of pairs, not a dict). A target of 0
    powers that channel off. Raises RuntimeError on ramp timeout.
    """
    for c, target_v in targets:
        slot, ch = int(c['slot']), int(c['channel'])
        if target_v == 0:
            if caen_hv.get_ch_power(slot, ch) == 1:
                caen_hv.set_ch_pw(slot, ch, 0)
        else:
            caen_hv.set_ch_v0(slot, ch, target_v)
            if caen_hv.get_ch_power(slot, ch) != 1:
                caen_hv.set_ch_pw(slot, ch, 1)

    ramping = [(c, v) for c, v in targets if v != 0]
    if not ramping:
        return

    deadline = time.time() + cfg.RAMP_TIMEOUT_S
    while True:
        done = True
        for c, target_v in ramping:
            slot, ch = int(c['slot']), int(c['channel'])
            vmon = caen_hv.get_ch_vmon(slot, ch)
            if abs(vmon - target_v) > cfg.RAMP_TOLERANCE_V:
                done = False
                print(f"  ramping {c['name']} ({slot}:{ch}): {vmon:.1f} -> {target_v} V")
        if done:
            print('  ramped')
            return
        if time.time() > deadline:
            raise RuntimeError(
                f'channels did not ramp within {cfg.RAMP_TIMEOUT_S}s '
                f'— aborting for safety')
        time.sleep(5)


def set_step_voltage(caen_hv, target_v):
    """Set every scint channel to the same target_v and wait for the ramp."""
    _set_and_ramp(caen_hv, [(c, target_v) for c in cfg.SCINT_CHANNELS])


def restore_end_state(caen_hv):
    """Leave channels per cfg.END_ACTION: 'nominal' | 'off' | 'hold'."""
    action = getattr(cfg, 'END_ACTION', 'nominal')
    if action == 'hold':
        print('End: holding channels at last scan voltage.')
        return
    try:
        if action == 'off':
            print('End: powering off all scint channels...')
            _set_and_ramp(caen_hv, [(c, 0) for c in cfg.SCINT_CHANNELS])
        else:  # 'nominal'
            print('End: restoring channels to per-channel nominal voltage...')
            targets = [(c, c['nominal_v']) for c in cfg.SCINT_CHANNELS]
            for c, v in targets:
                print(f"  {c['name']} ({c['slot']}:{c['channel']}) -> {v} V")
            _set_and_ramp(caen_hv, targets)
        print('End state reached.')
    except (CAENHVError, RuntimeError) as e:
        print(f'WARNING: could not fully reach end state ({e}). '
              f'CHECK THE PLASTICS MANUALLY (CAENGECO).')


def wait_for_protons(accumulator, target_e10, stop_event):
    """Block until the accumulator has seen target_e10 protons at this step."""
    if target_e10 <= 0:
        return
    accumulator.reset()
    print(f'  waiting for {target_e10:.0f}e10 protons at this step...')
    while not stop_event.is_set():
        if accumulator.cum_e10 >= target_e10:
            print(f'  reached {accumulator.cum_e10:.0f}e10 protons — advancing')
            return
        time.sleep(2)


def main():
    if not cfg.SCAN_VOLTAGES:
        raise SystemExit('SCAN_VOLTAGES is empty — set the ladder in scint_hv_config.py.')

    label = sys.argv[1] if len(sys.argv) > 1 else 'scan'
    stamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    out_dir = os.path.join(cfg.OUTPUT_ROOT, f'{stamp}_{label}')
    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, 'hv_beam_monitor.csv')

    lib.validate_channels()
    print(f'Scan output: {csv_path}')
    print(f'Ladder (V): {cfg.SCAN_VOLTAGES}')
    print(f'Protons/step: {cfg.PROTONS_PER_STEP_E10:.0f}e10\n')

    context = {'step_label': 'init', 'target_v': ''}
    accumulator = lib.BeamAccumulator()
    stop_event = threading.Event()

    with lib.open_session() as caen_hv:
        # Background HV+beam logger (shares the same session; the controller
        # serialises C calls internally, so set + monitor interleave safely).
        monitor = threading.Thread(
            target=lib.monitor_loop,
            args=(caen_hv, csv_path, stop_event, context, accumulator),
            kwargs={'verbose': True}, daemon=True)
        monitor.start()

        try:
            apply_channel_limits(caen_hv)
            for i, target_v in enumerate(cfg.SCAN_VOLTAGES):
                context['step_label'] = f'step_{i+1}_of_{len(cfg.SCAN_VOLTAGES)}'
                context['target_v'] = target_v
                print(f'\n=== {context["step_label"]}: {target_v} V ===')
                set_step_voltage(caen_hv, target_v)
                wait_for_protons(accumulator, cfg.PROTONS_PER_STEP_E10, stop_event)
                if stop_event.is_set():
                    break
            print('\nScan complete.')
        except KeyboardInterrupt:
            print('\nInterrupted by user.')
        except RuntimeError as e:
            print(f'\nSCAN ABORTED: {e}')
        finally:
            context['step_label'] = 'end_state'
            restore_end_state(caen_hv)
            stop_event.set()
            monitor.join(timeout=5)

    print(f'\nData saved to {csv_path}')


if __name__ == '__main__':
    main()
