#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Trigger logger for CAEN N1081B programmable-logic units.

Polls one or more N1081B units over their WebSocket SDK and records a per-trigger
timestamp (the board "time tag") for every enabled input to a CSV file, one file
per unit.  Each unit runs in its own thread with its own connection (the SDK is
not thread safe).

Runs independently of daq_control.py (watcher style, like qa_watcher.py).  Start
it standalone now; later the flask UI / run control can launch it pointing at a
per-run output directory.

Usage:
    python trigger_logger.py <trigger_config_json_path>

Config keys (see trigger_config.py to generate the JSON):
  output_dir          : directory where <unit_name>_triggers.csv files are written
  reconnect_interval  : seconds to wait before reconnecting a unit after an error (default 5)
  recv_timeout        : websocket recv timeout in seconds; bounds shutdown latency (default 2)
  units               : list of unit dicts, each with:
        name          : label used for the CSV filename and log lines
        ip            : board IP address (SDK connects to ws://<ip>:8080/)
        password      : login password (board default is "password")
        section       : "A" | "B" | "C" | "D"  — section running the time-tag function
        channels      : list of LEMO input indices (0-5) to time-tag
        input_standard: "NIM" | "TTL" | "DISCRIMINATOR"  (default "NIM")
        threshold     : discriminator threshold, only used for DISCRIMINATOR (default 0)
        impedance     : 50 | "high"   — input impedance (default 50)

Notes / caveats:
  * Board time tags are clock ticks counted from acquisition start, NOT wall clock.
    A sidecar <unit_name>_triggers.meta.json records the host wall-clock time of
    acquisition start (t0) so the ticks can be converted offline.  To align units
    against each other or against the DREAM DAQ you still want a common start/sync
    pulse; t0 alone is only as good as the host clock.
  * The exact tick resolution (ns per tick) is board/firmware specific — confirm it
    from docs/SDK-n1081b.pdf before trusting absolute deltas.  The raw fields are
    written through verbatim so no precision is lost here.
  * Time tag is a streaming push: the board sends packets as triggers arrive.  At
    very high rates the websocket + JSON decode can become the bottleneck; for pure
    rate logging a Counter/RateMeter section polled on an interval is cheaper.
"""

import sys
import csv
import json
import time
import signal
import threading
import datetime
from pathlib import Path

# PyPI package exposes `n1081b_sdk`; the in-repo source module is `N1081B_sdk`.
try:
    from n1081b_sdk import N1081B
except ImportError:  # pragma: no cover - depends on install method
    from N1081B_sdk import N1081B

try:
    from websocket import WebSocketTimeoutException
except ImportError:  # pragma: no cover
    WebSocketTimeoutException = TimeoutError


_LOG_FILE = Path(__file__).parent / 'logs' / 'trigger_logger.log'


def _log(event: str, **details):
    try:
        _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        ts         = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        detail_str = ' | '.join(f'{k}={v}' for k, v in details.items())
        line       = f"{ts} | {event:<16} | trigger_log  | {detail_str}\n"
        with open(_LOG_FILE, 'a') as f:
            f.write(line)
    except Exception as e:
        print(f"[trigger_logger] Warning: could not write to log: {e}")


# ---------------------------------------------------------------------------
# Config -> SDK enum mapping
# ---------------------------------------------------------------------------

def _section_enum(name: str):
    return getattr(N1081B.Section, f'SEC_{str(name).strip().upper()}')


def _standard_enum(name: str):
    return getattr(N1081B.SignalStandard, f'STANDARD_{str(name).strip().upper()}')


def _impedance_enum(value):
    if str(value).strip().lower() in ('50', '50ohm', 'low'):
        return N1081B.SignalImpedance.IMPEDANCE_50
    return N1081B.SignalImpedance.IMPEDANCE_HIGH


def _channel_enables(channels):
    """Return a 6-tuple of bools for LEMO inputs 0-5 from a list of indices."""
    chset = {int(c) for c in channels}
    return tuple(i in chset for i in range(6))


# ---------------------------------------------------------------------------
# Per-unit acquisition thread
# ---------------------------------------------------------------------------

def log_unit(unit: dict, output_dir: Path, stop_event: threading.Event,
             reconnect_interval: float, recv_timeout: float):
    """
    Connect to one N1081B, configure a section for time tagging, and stream every
    trigger's time tag to <output_dir>/<name>_triggers.csv until stop_event is set.
    Reconnects automatically (re-opening the CSV in append mode) on any error.
    """
    name      = unit['name']
    ip        = unit['ip']
    password  = unit.get('password', 'password')
    section   = _section_enum(unit['section'])
    channels  = unit.get('channels', [0])
    std       = _standard_enum(unit.get('input_standard', 'NIM'))
    threshold = int(unit.get('threshold', 0))
    impedance = _impedance_enum(unit.get('impedance', 50))
    enables   = _channel_enables(channels)

    csv_path  = output_dir / f'{name}_triggers.csv'
    meta_path = output_dir / f'{name}_triggers.meta.json'

    while not stop_event.is_set():
        device = N1081B(ip)
        try:
            if not device.connect():
                raise ConnectionError(f'connect() returned False for {ip}')
            # Bound recv so the loop can notice stop_event even with no triggers.
            try:
                device.ws.settimeout(recv_timeout)
            except Exception:
                pass

            if not device.login(password):
                _log('LOGIN_FAIL', unit=name, ip=ip)
                print(f"[trigger_logger] {name}: wrong password — retrying in {reconnect_interval}s")
                _safe_disconnect(device)
                _sleep_or_stop(stop_event, reconnect_interval)
                continue

            device.set_input_configuration(section, std, std, threshold, impedance)
            device.set_section_function(section, N1081B.FunctionType.FN_TIME_TAG)
            device.configure_time_tagging(section, *enables)

            # Reset, then start the time-tag data stream and record host t0.
            device.stop_acquisition(section, N1081B.FunctionType.FN_TIME_TAG)
            t0 = time.time()
            device.start_acquisition(section, N1081B.FunctionType.FN_TIME_TAG)

            _write_meta(meta_path, unit, t0)
            _log('STREAM_START', unit=name, ip=ip, section=unit['section'],
                 channels=channels, t0=f'{t0:.6f}')
            print(f"[trigger_logger] {name}: streaming time tags from {ip} "
                  f"section {unit['section']} channels {channels}")

            _stream_to_csv(device, name, unit['section'], csv_path, stop_event)

            # Clean stop requested.
            device.stop_acquisition(section, N1081B.FunctionType.FN_TIME_TAG)
            _safe_disconnect(device)
            _log('STREAM_STOP', unit=name, ip=ip)
            return

        except Exception as e:
            _safe_disconnect(device)
            _log('ERROR', unit=name, ip=ip, error=repr(e))
            print(f"[trigger_logger] {name}: error ({e}) — reconnecting in {reconnect_interval}s")
            _sleep_or_stop(stop_event, reconnect_interval)


def _stream_to_csv(device, name: str, section_label: str, csv_path: Path,
                   stop_event: threading.Event):
    """
    Drain get_time_tag_data() and append rows to csv_path until stop_event is set.
    The element schema of timetag_data is firmware specific, so the CSV header is
    derived from the first non-empty packet and the raw fields are written through.
    """
    writer = None
    extra_cols = None
    new_file = not csv_path.exists() or csv_path.stat().st_size == 0

    with open(csv_path, 'a', newline='') as csvfile:
        while not stop_event.is_set():
            try:
                packet = device.get_time_tag_data()
            except WebSocketTimeoutException:
                continue  # no triggers in this window — check stop_event and retry
            if not packet:
                continue

            recv_unix = time.time()
            recv_iso  = datetime.datetime.fromtimestamp(recv_unix).isoformat(timespec='microseconds')

            if writer is None:
                extra_cols = _packet_columns(packet[0])
                header = ['recv_iso', 'recv_unix', 'unit', 'section'] + extra_cols
                writer = csv.writer(csvfile)
                if new_file:
                    writer.writerow(header)

            for item in packet:
                row = [recv_iso, f'{recv_unix:.6f}', name, section_label]
                row.extend(_packet_values(item, extra_cols))
                writer.writerow(row)
            csvfile.flush()


def _packet_columns(sample):
    """Column names for the time-tag payload, inferred from one element."""
    if isinstance(sample, dict):
        return [f'tt_{k}' for k in sample.keys()]
    if isinstance(sample, (list, tuple)):
        return [f'tt_{i}' for i in range(len(sample))]
    return ['tt_value']


def _packet_values(item, extra_cols):
    if isinstance(item, dict):
        return [item.get(c[3:], '') for c in extra_cols]  # strip 'tt_' prefix
    if isinstance(item, (list, tuple)):
        return list(item)
    return [item]


def _write_meta(meta_path: Path, unit: dict, t0_unix: float):
    meta = {
        'unit': unit['name'],
        'ip': unit['ip'],
        'section': unit['section'],
        'channels': unit.get('channels', [0]),
        't0_unix': t0_unix,
        't0_iso': datetime.datetime.fromtimestamp(t0_unix).isoformat(timespec='microseconds'),
        'note': 'board time tags are clock ticks from acquisition start (t0); '
                'confirm ns/tick from docs/SDK-n1081b.pdf',
    }
    try:
        with open(meta_path, 'w') as f:
            json.dump(meta, f, indent=2)
    except Exception as e:
        print(f"[trigger_logger] {unit['name']}: could not write meta: {e}")


def _safe_disconnect(device):
    try:
        device.disconnect()
    except Exception:
        pass


def _sleep_or_stop(stop_event: threading.Event, seconds: float):
    """Sleep up to `seconds`, returning early if stop_event is set."""
    stop_event.wait(timeout=seconds)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) != 2:
        print("Usage: python trigger_logger.py <trigger_config_json_path>")
        sys.exit(1)

    with open(sys.argv[1]) as f:
        config = json.load(f)

    output_dir         = Path(config['output_dir'])
    reconnect_interval = config.get('reconnect_interval', 5)
    recv_timeout       = config.get('recv_timeout', 2)
    units              = config.get('units', [])

    output_dir.mkdir(parents=True, exist_ok=True)

    if not units:
        print("[trigger_logger] No units configured — nothing to do.")
        return

    print(f"[trigger_logger] output_dir : {output_dir}")
    print(f"[trigger_logger] units      : {[u['name'] for u in units]}")
    _log('START', output_dir=output_dir, n_units=len(units))

    stop_event = threading.Event()

    def _handle_signal(signum, frame):
        print(f"\n[trigger_logger] signal {signum} — stopping all units...")
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    threads = []
    for unit in units:
        t = threading.Thread(
            target=log_unit,
            args=(unit, output_dir, stop_event, reconnect_interval, recv_timeout),
            name=f"unit-{unit['name']}",
            daemon=True,
        )
        t.start()
        threads.append(t)

    # Keep the main thread alive so signal handlers fire; join lets threads finish
    # their clean stop_acquisition/disconnect.
    while any(t.is_alive() for t in threads) and not stop_event.is_set():
        time.sleep(0.5)
    stop_event.set()
    for t in threads:
        t.join(timeout=10)

    _log('STOP', n_units=len(units))
    print("[trigger_logger] stopped.")


if __name__ == '__main__':
    main()
