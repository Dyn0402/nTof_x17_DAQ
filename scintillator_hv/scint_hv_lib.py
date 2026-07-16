#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Shared helpers for the scintillator HV scan: CAEN session, beam-intensity
reader, cross-check against the running DAQ's channels, and the HV+beam
CSV monitor loop.

Design constraints (why it looks like this):
  * Opens its OWN CAENHVController session with keepalive + auto_reconnect,
    exactly like hv_control.py, so it coexists with the DAQ's session and
    survives the ~15 s idle drop.
  * All crate access here is READ-ONLY (get_ch_*). Voltage sets live in
    scint_hv_scan.py so the passive logger can never move a channel.
  * Never touches a channel that is not in the configured SCINT_CHANNELS list.
"""

import os
import csv
import json
import time
import threading
from datetime import datetime

from caen_hv_py.CAENHVController import CAENHVController
from caen_hv_py.exceptions import CAENHVError

import scint_hv_config as cfg


# --------------------------------------------------------------------------- #
# Credentials / session
# --------------------------------------------------------------------------- #
def load_creds():
    """Read (username, password) from hv_creds.txt (line1 user, line2 pass)."""
    with open(cfg.HV_CREDS_PATH) as f:
        lines = f.readlines()
    return lines[0].strip(), lines[1].strip()


def open_session():
    """A resilient CAEN session that coexists with the DAQ's hv_control.py."""
    user, password = load_creds()
    return CAENHVController(
        cfg.CAEN_IP, user, password,
        keepalive_s=cfg.KEEPALIVE_S, auto_reconnect=True,
    )


# --------------------------------------------------------------------------- #
# Safety: refuse to run on any channel the DAQ controls
# --------------------------------------------------------------------------- #
def daq_channels():
    """
    (slot, channel) pairs the running DREAM DAQ controls, from
    run_config_beam.py. Best-effort: if the config can't be imported we return
    None so the caller can warn instead of hard-failing.
    """
    prev_cwd = os.getcwd()
    try:
        import sys
        if cfg.REPO_ROOT not in sys.path:
            sys.path.insert(0, cfg.REPO_ROOT)
        # Config() reads hv_creds.txt by a relative path, so run it from the
        # repo root regardless of where this tool was launched.
        os.chdir(cfg.REPO_ROOT)
        from run_config_beam import Config
        config = Config()
        included = set(getattr(config, 'included_detectors', []) or [])
        pairs = set()
        for det in config.detectors:
            if included and det.get('name') not in included:
                continue
            for ch in (det.get('hv_channels') or {}).values():
                try:
                    slot, channel = ch
                    pairs.add((int(slot), int(channel)))
                except (TypeError, ValueError):
                    continue
        return pairs
    except Exception as e:  # noqa: BLE001 - best-effort safety check
        print(f'WARNING: could not read DAQ channels for overlap check: {e}')
        return None
    finally:
        os.chdir(prev_cwd)


def validate_channels():
    """
    Validate SCINT_CHANNELS is populated and disjoint from the DAQ's channels.
    Raises SystemExit with a clear message on any problem.
    """
    scint = cfg.SCINT_CHANNELS
    if not scint:
        raise SystemExit(
            'SCINT_CHANNELS is empty — fill in scint_hv_config.py with the '
            'scintillator (slot, channel) list before running.')

    scint_pairs = {(int(c['slot']), int(c['channel'])) for c in scint}
    if len(scint_pairs) != len(scint):
        raise SystemExit('Duplicate (slot, channel) entries in SCINT_CHANNELS.')

    daq = daq_channels()
    if daq is None:
        print('WARNING: skipping DAQ-overlap check (config unreadable). '
              'Manually confirm these channels are not used by the DAQ:')
        for c in scint:
            print(f"    {c['name']}: slot {c['slot']} ch {c['channel']}")
        return

    overlap = scint_pairs & daq
    if overlap:
        pretty = ', '.join(f'{s}:{c}' for s, c in sorted(overlap))
        raise SystemExit(
            f'REFUSING TO RUN: channel(s) {pretty} are controlled by the '
            f'running DAQ (run_config_beam.py). Two sessions must not share a '
            f'channel. Fix SCINT_CHANNELS in scint_hv_config.py.')
    print(f'Channel check OK: {len(scint_pairs)} scint channel(s), '
          f'disjoint from {len(daq)} DAQ channel(s).')


# --------------------------------------------------------------------------- #
# Beam intensity
# --------------------------------------------------------------------------- #
def read_beam_state():
    """Latest beam_state.json as a dict, or None if unavailable/unreadable."""
    try:
        with open(cfg.BEAM_STATE_PATH) as f:
            return json.load(f)
    except Exception:  # noqa: BLE001 - beam file is advisory, never fatal
        return None


class BeamAccumulator:
    """
    Integrates delivered protons from the per-pulse beam-intensity CSV that
    beam_watcher.py writes (path taken from beam_state.json's 'csv_path'; column
    'intensity_e10' = protons per pulse, units of 1e10).

    Why not beam_state.json's fields: it refreshes only ~every 30 s and exposes
    just the single latest pulse, while the beam fires every ~1-3 s, so summing
    last_pulse_e10 undercounts delivered protons by ~6x. The CSV has every pulse.

    Reads incrementally by byte offset so it never re-parses the whole (growing)
    file. cum_e10 counts protons since the last reset() (or since first update).
    """

    def __init__(self):
        self.cum_e10 = 0.0
        self._path = None
        self._offset = None   # None => initialise to end-of-file on first read
        self._lock = threading.Lock()   # update() (monitor) vs reset() (scan)

    def _csv_path(self, state):
        return (state or {}).get('csv_path')

    def _consume_new_pulses(self, add=True):
        """Read appended complete lines; if add, sum their intensity into cum."""
        if not self._path or not os.path.exists(self._path):
            return
        try:
            size = os.path.getsize(self._path)
            if self._offset is None or self._offset > size:
                # First read, or file shrank/rotated: start from current end.
                self._offset = size
                return
            with open(self._path, 'rb') as f:
                f.seek(self._offset)
                chunk = f.read()
            last_nl = chunk.rfind(b'\n')
            if last_nl == -1:
                return  # no complete new line yet
            self._offset += last_nl + 1
            if not add:
                return
            for line in chunk[:last_nl + 1].decode(errors='ignore').splitlines():
                parts = line.split(',')
                if len(parts) < 3 or parts[0].startswith('timestamp'):
                    continue
                try:
                    self.cum_e10 += float(parts[2])
                except ValueError:
                    continue
        except OSError:
            return  # CSV transiently unreadable; try again next tick

    def update(self, state):
        path = self._csv_path(state)
        with self._lock:
            if path and path != self._path:
                # New CSV (first use or midnight rollover): count from its end.
                self._path = path
                self._offset = None
            self._consume_new_pulses(add=True)

    def reset(self):
        """Zero the counter and re-baseline to the current end of the CSV."""
        with self._lock:
            self.cum_e10 = 0.0
            if self._path and os.path.exists(self._path):
                self._offset = os.path.getsize(self._path)


# --------------------------------------------------------------------------- #
# CSV monitor loop (HV readings + beam intensity)
# --------------------------------------------------------------------------- #
def _read_channel(caen_hv, slot, channel):
    """(power, vmon, imon), tolerating a hard read failure without crashing."""
    try:
        power = caen_hv.get_ch_power(slot, channel)
        vmon = caen_hv.get_ch_vmon(slot, channel)
        imon = caen_hv.get_ch_imon(slot, channel)
        return power, vmon, imon
    except CAENHVError as e:
        print(f'HV read failed for {slot}:{channel}: {e}')
        return '', float('nan'), float('nan')


def monitor_loop(caen_hv, csv_path, stop_event, context, accumulator=None,
                 interval_s=None, verbose=True):
    """
    Poll HV readings for every SCINT_CHANNELS entry plus the current beam
    intensity, append a row to csv_path, and (if given) advance `accumulator`.

    context: a dict shared with the caller; whatever is in context['target_v']
             and context['step_label'] at each tick is logged, so the scanner
             can annotate rows with the step it is on.
    Runs until stop_event is set. Read-only on the crate.
    """
    interval_s = interval_s or cfg.MONITOR_INTERVAL_S
    channels = cfg.SCINT_CHANNELS

    headers = ['timestamp', 'step_label', 'target_v']
    for c in channels:
        p = c['name']
        headers += [f'{p} power', f'{p} vmon', f'{p} imon']
    headers += ['beam_on', 'beam_last_pulse_time', 'beam_last_pulse_e10',
                'beam_protons_10min_e10', 'beam_seconds_since_pulse',
                'beam_cum_e10_since_start']

    new_file = not os.path.exists(csv_path)
    with open(csv_path, 'a', newline='') as f:
        writer = csv.writer(f)
        if new_file:
            writer.writerow(headers)
            f.flush()

        while not stop_event.is_set():
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            state = read_beam_state()
            if accumulator is not None:
                accumulator.update(state)

            row = [now, context.get('step_label', ''), context.get('target_v', '')]
            for c in channels:
                power, vmon, imon = _read_channel(caen_hv, int(c['slot']), int(c['channel']))
                row += [power, f'{vmon:.2f}', f'{imon:.4f}']

            state = state or {}
            row += [
                state.get('beam_on', ''),
                state.get('last_pulse_time', ''),
                state.get('last_pulse_e10', ''),
                state.get('protons_10min_e10', ''),
                state.get('seconds_since_pulse', ''),
                f'{accumulator.cum_e10:.1f}' if accumulator is not None else '',
            ]
            writer.writerow(row)
            f.flush()

            if verbose:
                mons = '  '.join(
                    f"{c['name']}={row[3 + 3*i + 1]}V" for i, c in enumerate(channels))
                beam = f"beam {'ON' if state.get('beam_on') else 'off'}"
                if accumulator is not None:
                    beam += f", {accumulator.cum_e10:.0f}e10 this step"
                print(f'[{now[11:]}] {mons}   {beam}')

            # Sleep in small slices so stop_event is responsive.
            slept = 0.0
            while slept < interval_s and not stop_event.is_set():
                time.sleep(min(0.5, interval_s - slept))
                slept += 0.5
