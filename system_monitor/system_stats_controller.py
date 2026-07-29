#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on July 15 2026
Created in PyCharm
Created as nTof_x17_DAQ/system_monitor/system_stats_controller.py

@author: Dylan Neff, dylan

Monitoring layer for the DAQ box's own system resources (CPU / memory / disk-space /
network + disk I/O). Mirrors the 3He pressure and gas watchers, but has NO hardware bus
to own and NO command interface: it is a pure sampler that reads psutil at a fixed rate
and appends one row per sample to a per-day CSV.

Unlike the gas/he3 watchers there is also NO state file — the Flask /system_stats
endpoint already reads psutil directly for the live Overview plots, so the watcher's
only job is durable logging. It computes network/disk throughput itself from consecutive
counter reads (independent of Flask), so it is fully self-contained.

CSV columns (one row per sample):
    timestamp                                 ISO local time, millisecond resolution
    cpu0..cpu{N-1}                            per-core utilisation, %
    cpu_avg                                   mean over all cores, %
    mem_percent, mem_used_gb                  RAM
    swap_percent                              swap
    ssd_percent, hdd_percent                  disk-space used on / and /mnt/data
    net_<iface>_rx_bps, net_<iface>_tx_bps    per-interface throughput, bytes/sec
    disk_<key>_read_bps, disk_<key>_write_bps per-device throughput, bytes/sec
    load1, load5, load15                      OS load averages

The per-day CSVs live with the other slow-control logs under
~/beam_july/slow_control/system_stats/ (see the gas/he3/beam controllers).
"""

import os
import csv
import json
import sys
import time
import signal
import threading
import traceback
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common_functions import log_event

try:
    import psutil
except ImportError:  # keep import-safe so nothing else breaks if psutil is missing
    psutil = None

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_DIR = os.path.dirname(_MODULE_DIR)

# Per-day CSVs live on the data disk with the other slow-control logs, NOT in the repo.
SYSTEM_STATS_LOG_DIR = os.path.expanduser("~/beam_july/slow_control/system_stats")
# The Flask app (or an operator) may write {"poll_s": <seconds>} here to retune the
# sample rate; the watcher picks it up within one cycle. Missing file -> default rate.
SYSTEM_STATS_CONFIG_PATH = os.path.join(_REPO_DIR, "config", "system_stats_config.json")
# Durable event log: baseline lifecycle only. This is a pure logger — it owns no
# hardware and commands nothing — so START/STOP/CRASH is the whole useful set.
SYSTEM_STATS_EVENT_LOG = os.path.join(_REPO_DIR, "logs", "system_stats_watcher.log")


def _log(event, **details):
    log_event(SYSTEM_STATS_EVENT_LOG, event, 'sysstats', **details)

# Which network interfaces / physical disks to log. KEEP IN SYNC with the same two
# constants in flask_app/app.py (_NET_IFACES / _DISK_DEVS) — both read the same box.
#
# ⚠ THE NAMES SURVIVED THE 2026-07-22 NIC SWAP BUT THEIR ROLES INVERTED. The list below
# is unchanged and still correct as strings, so nothing here errors — but the CSV columns
# mean the opposite of what they meant before 2026-07-22 ~15:44:
#     enp4s0  was I210    / CERN   ->  is now AQC113 (atlantic) / DREAM+FEU, MTU 9000
#     eno1    was I219-LM / DREAM  ->  is now I219-LM (e1000e)  / CERN, DHCP
# So net_enp4s0_rx_bps is the FEU readout link TODAY and was CERN idle traffic BEFORE.
# Analysing an IPD ladder: pre-swap runs need `analyze_link_load.py --iface eno1`,
# post-swap runs `--iface enp4s0`. Identify NICs by driver/MAC, never by name.
# Full record: docs/network_upgrade_10g/05_as_built_2026-07-22.md §3.
NET_IFACES = ["enp4s0", "eno1"]
DISK_DEVS = {"ssd": "sdb", "hdd": "sda"}   # sdb2 -> /, sda4 -> /mnt/data
DISK_MOUNTS = {"ssd": "/", "hdd": "/mnt/data"}

# --- sample-rate limits ---
# psutil sampling is cheap (a few ms), but this box also runs the DAQ, HV, gas, backup
# and processor watchers, so keep a sane ceiling. 2 Hz is the requested default and the
# fastest we allow; one sample/minute is the floor.
MIN_SAMPLE_PERIOD_S = 0.5     # -> 2.0 Hz max
MAX_SAMPLE_PERIOD_S = 60.0    # -> ~0.0167 Hz min
DEFAULT_SAMPLE_PERIOD_S = 0.5  # 2 Hz


def clamp_period(p):
    """Clamp a requested sample period (seconds) into the safe [MIN, MAX] range.
    Non-numeric input falls back to the default period."""
    try:
        p = float(p)
    except (TypeError, ValueError):
        return DEFAULT_SAMPLE_PERIOD_S
    return max(MIN_SAMPLE_PERIOD_S, min(MAX_SAMPLE_PERIOD_S, p))


class SystemStatsController:
    """Samples psutil at a fixed rate and appends each sample to a per-day CSV."""

    def __init__(self, poll_s=DEFAULT_SAMPLE_PERIOD_S, log_dir=None,
                 config_path=SYSTEM_STATS_CONFIG_PATH):
        self.poll_s = clamp_period(poll_s)
        self.log_dir = log_dir or SYSTEM_STATS_LOG_DIR
        self.config_path = config_path

        self.n_cores = psutil.cpu_count(logical=True) if psutil else 0

        # Previous I/O counters + timestamp, for deriving byte/sec rates.
        self._prev_t = None
        self._prev_net = None
        self._prev_disk = None

        self._stop = threading.Event()
        self._thread = None

    # ---------------- sampling ----------------

    def _rate(self, cur, prev, dt):
        """Bytes/sec between two monotonic counter reads; 0 on the first sample or a
        counter reset (e.g. interface bounce)."""
        if prev is None or dt is None or dt <= 0:
            return 0.0
        return max(0.0, (cur - prev) / dt)

    def _sample(self):
        """Read one full set of resource stats and return a flat CSV row dict."""
        cpu = psutil.cpu_percent(percpu=True)   # % since the previous call (~poll_s window)
        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()
        load = os.getloadavg()

        now = time.monotonic()
        net_ctr = psutil.net_io_counters(pernic=True)
        disk_ctr = psutil.disk_io_counters(perdisk=True)
        dt = (now - self._prev_t) if self._prev_t is not None else None

        row = {"timestamp": datetime.now().isoformat(timespec="milliseconds")}

        for i in range(self.n_cores):
            row[f"cpu{i}"] = round(cpu[i], 1) if i < len(cpu) else ""
        row["cpu_avg"] = round(sum(cpu) / len(cpu), 1) if cpu else ""

        row["mem_percent"] = round(mem.percent, 1)
        row["mem_used_gb"] = round(mem.used / 1e9, 2)
        row["swap_percent"] = round(swap.percent, 1)

        for key, mount in DISK_MOUNTS.items():
            try:
                row[f"{key}_percent"] = round(psutil.disk_usage(mount).percent, 1)
            except Exception:
                row[f"{key}_percent"] = ""

        for name in NET_IFACES:
            cur = net_ctr.get(name)
            prev = (self._prev_net or {}).get(name)
            row[f"net_{name}_rx_bps"] = round(self._rate(
                cur.bytes_recv, prev.bytes_recv if prev else None, dt)) if cur else ""
            row[f"net_{name}_tx_bps"] = round(self._rate(
                cur.bytes_sent, prev.bytes_sent if prev else None, dt)) if cur else ""

        for key, dev in DISK_DEVS.items():
            cur = disk_ctr.get(dev)
            prev = (self._prev_disk or {}).get(dev)
            row[f"disk_{key}_read_bps"] = round(self._rate(
                cur.read_bytes, prev.read_bytes if prev else None, dt)) if cur else ""
            row[f"disk_{key}_write_bps"] = round(self._rate(
                cur.write_bytes, prev.write_bytes if prev else None, dt)) if cur else ""

        row["load1"], row["load5"], row["load15"] = (round(x, 2) for x in load)

        self._prev_t = now
        self._prev_net = net_ctr
        self._prev_disk = disk_ctr
        return row

    def _fieldnames(self):
        """Stable CSV column order (must match _sample()'s keys)."""
        fields = ["timestamp"]
        fields += [f"cpu{i}" for i in range(self.n_cores)] + ["cpu_avg"]
        fields += ["mem_percent", "mem_used_gb", "swap_percent"]
        fields += [f"{key}_percent" for key in DISK_MOUNTS]
        for name in NET_IFACES:
            fields += [f"net_{name}_rx_bps", f"net_{name}_tx_bps"]
        for key in DISK_DEVS:
            fields += [f"disk_{key}_read_bps", f"disk_{key}_write_bps"]
        fields += ["load1", "load5", "load15"]
        return fields

    # ---------------- CSV logging ----------------

    def _csv_path(self):
        day = datetime.now().strftime("%Y-%m-%d")
        return os.path.join(self.log_dir, f"system_stats_{day}.csv")

    def _log_row(self, row):
        try:
            os.makedirs(self.log_dir, exist_ok=True)
            path = self._csv_path()
            new_file = not os.path.exists(path)
            with open(path, "a", newline="") as f:
                w = csv.DictWriter(f, fieldnames=self._fieldnames())
                if new_file:
                    w.writeheader()
                w.writerow(row)
        except Exception as e:
            print(f"[system_stats_controller] CSV log failed: {e}")

    # ---------------- IPC / config ----------------

    def log(self, msg):
        """Timestamped, prefixed line for the system_stats_watcher tmux pane (and logs)."""
        print(f"{datetime.now().strftime('%H:%M:%S')} [system_stats_watcher] {msg}", flush=True)

    def _apply_config(self):
        """Pick up a sample-period change written to the config file and apply it
        (clamped). Called once per loop, so a change takes effect within one cycle.
        Missing/garbage file -> keep current."""
        try:
            with open(self.config_path) as f:
                cfg = json.load(f)
        except (FileNotFoundError, ValueError, OSError):
            return
        p = cfg.get("poll_s")
        if p is None:
            return
        p = clamp_period(p)
        if p != self.poll_s:
            self.log(f"sample period -> {p:.3f}s ({1.0 / p:.3f} Hz)")
            self.poll_s = p

    # ---------------- poll loop ----------------

    def _run(self):
        if psutil is None:
            self.log("psutil not installed — nothing to log, exiting")
            return
        # Prime psutil's per-CPU counter (first cpu_percent call always returns 0.0) and
        # the I/O baseline, so the first LOGGED row already carries real rates.
        self._sample()
        self._stop.wait(self.poll_s)
        while not self._stop.is_set():
            self._apply_config()
            try:
                self._log_row(self._sample())
            except Exception as e:
                self.log(f"sample failed: {e}")
            self._stop.wait(self.poll_s)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="sysstats-poll", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def run_blocking(self):
        """Run the poll/log loop in the current thread until SIGINT/SIGTERM. Used by
        system_stats_watcher.py."""
        signal.signal(signal.SIGINT, lambda *a: self._stop.set())
        signal.signal(signal.SIGTERM, lambda *a: self._stop.set())
        self.log(f"system stats watcher starting (poll {self.poll_s}s = "
                 f"{1.0 / self.poll_s:.3f} Hz, {self.n_cores} cores, "
                 f"log dir {self.log_dir})")
        _log('START', poll=f'{self.poll_s}s', cores=self.n_cores, csv_dir=self.log_dir)
        try:
            self._run()
        except Exception as e:  # noqa: BLE001 — durable copy, then re-raise
            _log('CRASH', error=repr(e), traceback=traceback.format_exc())
            raise
        self.log("system stats watcher stopped")
        _log('STOP')
