#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on September 29 9:36 PM 2025
Created in PyCharm
Created as Cosmic_Bench_DAQ_Control/daq_status.py

@author: Dylan Neff, Dylan
"""

import os
import re
import json
import subprocess
from datetime import datetime

# Gas watcher publishes its state here (see gas_mixer_control/flow_controller.py).
_REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GAS_STATE_FILE = os.path.join(_REPO_DIR, "config", "gas_state.json")
# 3He pressure watcher publishes its state here (see
# he3_pressure_reader/he3_pressure_controller.py).
HE3_PRESSURE_STATE_FILE = os.path.join(_REPO_DIR, "config", "he3_pressure_state.json")
# Beam-intensity watcher publishes its state here (see
# beam_monitor/beam_intensity_controller.py).
BEAM_STATE_FILE = os.path.join(_REPO_DIR, "config", "beam_state.json")
# N1081B time-tag watcher publishes its state here (see
# n1081b/timetag_watcher_controller.py).
N1081B_TIMETAG_STATE_FILE = os.path.join(_REPO_DIR, "config", "n1081b_timetag_state.json")

# Per-run output directory: each run's schedule lives at <RUN_DIR>/<run>/run_config.json.
# Resolved lazily (see _run_dir) because this module is imported before app.py adds the
# repo root to sys.path, so run_config_beam isn't importable at module-load time.
_RUN_DIR_CACHE = None


def _run_dir():
    """Return the runs directory (BASE_DATA_DIR/runs), or None if unresolvable.
    Cached after the first successful lookup."""
    global _RUN_DIR_CACHE
    if _RUN_DIR_CACHE is not None:
        return _RUN_DIR_CACHE
    try:
        import sys
        if _REPO_DIR not in sys.path:
            sys.path.append(_REPO_DIR)
        from run_config_beam import BASE_DATA_DIR
        _RUN_DIR_CACHE = f"{BASE_DATA_DIR}runs"
    except Exception:
        return None
    return _RUN_DIR_CACHE


""" Colors:
- danger (red)
- warning (yellow)
- success (green)
- info (blue)
- primary (dark blue)
- secondary (grey)
- light (light grey)
- dark (black)
"""

def get_dream_daq_status():
    try:
        output = subprocess.check_output(
            ["tmux", "capture-pane", "-pJS", "-500", "-t", "dream_daq:0.0"],
            text=True
        )
    except subprocess.CalledProcessError:
        return {
            "status": "ERROR",
            "color": "danger",
            "fields": [{"label": "Details", "value": "dream_daq tmux not running"}]
        }

    fields = []
    if "_TakePedThr" in output:
        status = "Taking Pedestals"
        color = "warning"
    elif "Scan trigger thresholds in process" in output:
        status = "Scanning Trigger Thresholds"
        color = "warning"
    elif "_TakeData:" in output:
        status = "RUNNING"
        color = "success"
        m_rt = re.search(r"RunTime\s+(\d+h\s+\d+m\s+\d+s)", output)
        if m_rt: fields.append({"label": "Run Time", "value": m_rt.group(1)})

        m_ir = re.search(r"IntRate=\s*([\d.]+\s*[A-Za-z]+)", output)
        if m_ir: fields.append({"label": "Int Rate", "value": m_ir.group(1)})

        # Live per-subrun event count (nb_of_events). Labeled "Subrun Events" to
        # distinguish it from the cumulative "Events this run" total shown up top.
        m_ev = re.search(r"nb_of_events=(\d+)", output)
        if m_ev: fields.append({"label": "Subrun Events", "value": m_ev.group(1)})

        # m_wait = re.search(
        #     r"wait for\s*((?:(\d+)h\s*)?(?:(\d+)m\s*)?(?:(\d+)s)?)", output
        # )
        # if m_wait:
        #     h, m, s = m_wait.groups()
        #     # Fill in missing values as 0
        #     h = h or "0"
        #     m = m or "0"
        #     s = s or "0"
        #     fields.append({"label": "Wait For", "value": f"{h}h {m}m {s}s"})
    elif "Listening on " in output:
        status = "WAITING"
        color = "secondary"
    elif "Moving data files." in output or "Waiting for on-the-fly copy thread to finish" in output:
        status = "Copying fdfs"
        color = "info"
    elif "Sent: Dream DAQ stopped" in output:
        status = "DAQ Stopped"
        color = "info"
    else:
        status = "UNKNOWN STATE"
        color = "danger"

    return {"status": status, "color": color, "fields": fields}


def get_hv_control_status():
    try:
        output = subprocess.check_output(
            ["tmux", "capture-pane", "-pJS", "-50", "-t", "hv_control:0.0"],
            text=True
        )
    except subprocess.CalledProcessError:
        return {
            "status": "ERROR",
            "color": "danger",
            "fields": [{"label": "Details", "value": "hv_control tmux not running"}]
        }

    # Default status/color rules
    rules = [
        ("Listening on ", "WAITING", "secondary"),
        ("Powering off HV", "HV Off", "secondary"),
        ("HV Powered Off", "HV Off", "secondary"),
        ("Monitoring HV", "Monitoring HV", "success"),
        ("HV Ramped", "HV Ramped", "success"),
        ("Setting HV", "Ramping HV", "warning"),
        ("Checking HV ramp", "Ramping HV", "warning"),
        ("Waiting for HV to ramp", "Ramping HV", "warning"),
    ]

    # Determine overall status/color from most recent matching line
    status, color = "UNKNOWN STATE", "danger"
    for line in reversed(output.splitlines()):
        for flag, s, c in rules:
            if flag in line:
                status, color = s, c
                break
        if status != "UNKNOWN STATE":
            break

    # Parse individual channel lines in the last block
    fields = []

    return {"status": status, "color": color, "fields": fields}



def get_daq_control_status():
    try:
        output = subprocess.check_output(
            ["tmux", "capture-pane", "-pJS", "-50", "-t", "daq_control:0.0"],
            text=True
        )
    except subprocess.CalledProcessError:
        return {
            "status": "ERROR",
            "color": "danger",
            "fields": [{"label": "Details", "value": "daq_control tmux not running"}]
        }

    rules = [
        ("Daq control session started", "WAITING", "secondary"),
        ("Run complete", "Run Complete", "info"),
        ("donzo", "Run Complete", "info"),
        ("[pause] Paused after sub-run", "Paused", "info"),
        ("[pause] Post-sub-run pause: waiting", "Paused", "info"),
        ("Finished with sub run ", "Finished Sub Run", "warning"),
        ("Dream DAQ taking pedestals", "Prepping DAQs", "warning"),
        ("Prepping DAQs for ", "Prepping DAQs", "warning"),
        ("Ramping HVs for ", "Ramping HV", "warning"),
        ("Starting DAQ Control", "STARTING", "warning"),
        ("Dream DAQ starting", "RUNNING", "success"),
        ("Stopping DAQ process", "Stopping DAQ", "warning"),
    ]

    fields = []
    for line in reversed(output.splitlines()):
        m = re.search(r'\[status\] run=(\S+)\s+subrun=(\S+)\s+run_time=(\S+)', line)
        if m:
            fields.append({"label": "Run",     "value": m.group(1)})
            fields.append({"label": "Subrun",  "value": m.group(2)})
            fields.append({"label": "Run Time", "value": m.group(3)})
            break

    for line in reversed(output.splitlines()):
        for flag, status, color in rules:
            if flag in line:
                return {"status": status, "color": color, "fields": fields}

    return {"status": "UNKNOWN STATE", "color": "danger", "fields": fields}


def get_processor_watcher_status():
    try:
        output = subprocess.check_output(
            ["tmux", "capture-pane", "-pJS", "-50", "-t", "processor_watcher:0.0"],
            text=True
        )
    except subprocess.CalledProcessError:
        return {"status": "STOPPED", "color": "secondary", "fields": []}

    lines = [l for l in output.splitlines() if l.strip()]

    # Find the most recent run/subrun/file_num context line
    # Format: [watcher] run_name/subrun_name  file_num=000  (N FEU(s))
    fields = []
    for line in reversed(lines):
        m = re.search(r'\[watcher\] (\S+)/(\S+)\s+file_num=(\d+)', line)
        if m:
            fields = [
                {"label": "Run",    "value": m.group(1)},
                {"label": "Subrun", "value": m.group(2)},
                {"label": "File #", "value": str(int(m.group(3)))},
            ]
            break

    # Lines that carry no status signal on their own — skip when determining state
    _noise = ("[watcher] Marked stale", "[analyze] Using pedestal",
              "[analyze] No pedestal",  "[analyze] Multiple pedestals",
              "[analyze] Cannot extract")

    for line in reversed(lines):
        if any(n in line for n in _noise):
            continue
        if "[watcher] Decoding pedestal:" in line:
            return {"status": "Ped. Decoding", "color": "success",  "fields": fields}
        if "[decode]" in line:
            return {"status": "Decoding",      "color": "success",  "fields": fields}
        if "[analyze]" in line:
            # analyze work lines carry the detector on the FEU being analyzed:
            #   [analyze] det=mx17_A feu=03  <file>   (det= absent if unmapped)
            dm = re.search(r'\[analyze\] det=(\S+)', line)
            det_field = [{"label": "Detector", "value": dm.group(1)}] if dm else []
            return {"status": "Analyzing",     "color": "success",  "fields": fields + det_field}
        if "[combine]" in line:
            return {"status": "Combining",     "color": "success",  "fields": fields}
        if "[cleanup]" in line:
            return {"status": "Cleaning Up",   "color": "success",  "fields": fields}
        if "[watcher]" in line and " idle " in line:
            return {"status": "IDLE",          "color": "info",     "fields": fields}
        if "[watcher]" in line and "waiting for runs_dir" in line:
            return {"status": "Waiting for Dir", "color": "warning", "fields": []}
        if "[watcher]" in line:
            return {"status": "RUNNING",       "color": "info",     "fields": fields}

    return {"status": "UNKNOWN", "color": "danger", "fields": []}


def get_qa_watcher_status():
    try:
        output = subprocess.check_output(
            ["tmux", "capture-pane", "-pJS", "-50", "-t", "qa_watcher:0.0"],
            text=True
        )
    except subprocess.CalledProcessError:
        return {"status": "STOPPED", "color": "secondary", "fields": []}

    lines = [l for l in output.splitlines() if l.strip()]

    # Extract most recent run/subrun context from a [qa_watcher] run/subrun line
    fields = []
    for line in reversed(lines):
        m = re.search(r'\[qa_watcher\] (\S+)/(\S+)', line)
        if m and 'idle' not in line and 'Marked stale' not in line \
                and 'waiting' not in line and 'runs_dir' not in line:
            fields = [
                {"label": "Run",    "value": m.group(1)},
                {"label": "Subrun", "value": m.group(2)},
            ]
            break

    _noise = ("[qa_watcher] Marked stale",)
    for line in reversed(lines):
        if any(n in line for n in _noise):
            continue
        m = re.search(r'\[qa\] (\S+) —', line)
        if m:
            return {"status": "Running QA",  "color": "success", "fields": fields + [{"label": "Detector", "value": m.group(1)}]}
        if "[qa_watcher]" in line and " idle " in line:
            return {"status": "IDLE",        "color": "info",    "fields": fields}
        if "[qa_watcher]" in line and "waiting for runs_dir" in line:
            return {"status": "Waiting for Dir", "color": "warning", "fields": []}
        if "[qa_watcher]" in line:
            return {"status": "RUNNING",     "color": "info",    "fields": fields}

    return {"status": "UNKNOWN", "color": "danger", "fields": []}


def get_backup_watcher_status():
    try:
        output = subprocess.check_output(
            ["tmux", "capture-pane", "-pJS", "-50", "-t", "backup_watcher:0.0"],
            text=True
        )
    except subprocess.CalledProcessError:
        return {"status": "STOPPED", "color": "secondary", "fields": []}

    lines = [l for l in output.splitlines() if l.strip()]

    fields = []
    for line in reversed(lines):
        m = re.search(r'\[backup\] (\S+)/(\S+)\s+size=', line)
        if m:
            fields = [
                {"label": "Run",    "value": m.group(1)},
                {"label": "Subrun", "value": m.group(2)},
            ]
            break

    # Pull rsync --info=progress2 line if present: "  1,234,567  45%  12.3MB/s  0:00:42 (xfr#1, to-chk=123/456)"
    progress_fields = []
    for line in lines:
        mp = re.search(r'([\d,]+)\s+(\d+)%\s+([\d.]+\s*\S+/s)\s+([\d:]+)(?:.*?to-chk=(\d+)/(\d+))?', line)
        if mp:
            pct, speed, eta = mp.group(2), mp.group(3).strip(), mp.group(4)
            progress_fields = [{"label": "Progress", "value": f"{pct}%  {speed}  eta {eta}"}]
            if mp.group(5) and mp.group(6):
                remaining, total = int(mp.group(5)), int(mp.group(6))
                progress_fields.append({"label": "Files", "value": f"{total - remaining}/{total}"})

    for line in reversed(lines):
        if "[backup] rsync ->" in line or "[backup] rsync done" in line:
            return {"status": "Syncing",         "color": "success", "fields": fields + progress_fields}
        m = re.search(r'\[backup\] extra sync(?! done| FAILED): (\S+)', line)
        if m:
            folder = m.group(1)
            return {"status": "Syncing",         "color": "success",
                    "fields": [{"label": "Folder", "value": folder}] + progress_fields}
        if "[backup] extra sync done" in line or "[backup] extra sync FAILED" in line:
            pass  # don't use these as the current status — keep scanning
        if "AUTH ERROR" in line or "Kerberos FAILED" in line:
            return {"status": "Auth Error",      "color": "danger",  "fields": fields}
        if "[backup] rsync FAILED" in line:
            return {"status": "rsync Error",     "color": "danger",  "fields": fields}
        if "[backup] Kerberos OK" in line and " idle " not in line:
            return {"status": "IDLE",            "color": "info",    "fields": fields}
        if "[backup]" in line and " idle " in line:
            return {"status": "IDLE",            "color": "info",    "fields": fields}
        if "[backup]" in line and "waiting for source_dir" in line:
            return {"status": "Waiting for Dir", "color": "warning", "fields": []}
        if "[backup]" in line:
            return {"status": "RUNNING",         "color": "info",    "fields": fields}

    return {"status": "UNKNOWN", "color": "danger", "fields": []}


def get_pedestal_watcher_status():
    """
    Minimal status for the pedestal QA watcher — rendered as a compact card
    ("small": True), so only running/analyzing/stopped, no detail fields.
    """
    try:
        output = subprocess.check_output(
            ["tmux", "capture-pane", "-pJS", "-50", "-t", "pedestal_watcher:0.0"],
            text=True
        )
    except subprocess.CalledProcessError:
        return {"status": "STOPPED", "color": "secondary", "small": True, "fields": []}

    def _st(status, color):
        return {"status": status, "color": color, "small": True, "fields": []}

    for line in reversed([l for l in output.splitlines() if l.strip()]):
        if "[ped_watcher]" in line and (" done" in line or " idle " in line):
            return _st("IDLE", "info")
        if "[ped_watcher]" in line and "waiting for pedestals_dir" in line:
            return _st("No Dir", "warning")
        if "[ped_watcher]" in line and " failed " in line:
            return _st("Failed", "warning")
        if "[ped_watcher]" in line and "n_pedthr=" in line:
            return _st("Analyzing", "success")
        if "[ped_watcher]" in line:
            return _st("RUNNING", "info")
        if "[decode]" in line or "[analyze]" in line or "[info]" in line or "[done]" in line:
            return _st("Analyzing", "success")

    # Session alive but no recognized line in the capture window (e.g. a long
    # unprefixed analysis printout scrolled everything out) — not an error.
    return _st("RUNNING", "info")


def get_decoder_status():
    try:
        output = subprocess.check_output(
            ["tmux", "capture-pane", "-pJS", "-10", "-t", "decoder:0.0"],
            text=True
        )
    except subprocess.CalledProcessError:
        return {
            "status": "ERROR",
            "color": "danger",
            "fields": [{"label": "Details", "value": "decoder tmux not running"}]
        }

    rules = [
        ("Decoder started", "RUNNING", "success"),
        ("Starting Decoder", "STARTING", "warning"),
        ("Decoder stopped", "STOPPED", "warning"),
        ("Listening on ", "WAITING", "secondary"),
    ]

    for line in reversed(output.splitlines()):
        for flag, status, color in rules:
            if flag in line:
                return {"status": status, "color": color, "fields": []}

    return {"status": "UNKNOWN STATE", "color": "danger", "fields": []}


def get_gas_watcher_status():
    """Compact card for the gas-mixer watcher (the separate process that owns the
    FLOW-BUS). Derived from tmux session existence + the state file the watcher
    publishes (connection + freshness), rendered as a small status-only card."""
    def small(status, color):
        return {"status": status, "color": color, "small": True, "fields": []}

    # No tmux session -> the watcher isn't running (logging + gas control are down).
    alive = subprocess.run(["tmux", "has-session", "-t", "gas_watcher"],
                           capture_output=True).returncode == 0
    if not alive:
        return small("STOPPED", "secondary")

    try:
        with open(GAS_STATE_FILE) as f:
            st = json.load(f)
    except Exception:
        return small("Starting", "info")   # session up, no state published yet

    if not st.get("connected"):
        return small("No Gas", "warning")   # bus up but controllers not found

    # Session + connection OK: flag a stale card if the state file isn't being updated.
    try:
        age = (datetime.now() - datetime.fromisoformat(st["timestamp"])).total_seconds()
    except Exception:
        age = None
    if age is not None and age > 15:
        return small("Stale", "warning")
    return small("Logging", "success")


def get_beam_watcher_status():
    """Compact card for the n_TOF beam-intensity watcher (the separate process that
    owns the NXCALS/Spark session). Derived from tmux session existence + the state
    file the watcher publishes (connection + freshness), rendered as a small
    status-only card."""
    def small(status, color):
        return {"status": status, "color": color, "small": True, "fields": []}

    # No tmux session -> the watcher isn't running (beam logging is down).
    alive = subprocess.run(["tmux", "has-session", "-t", "beam_watcher"],
                           capture_output=True).returncode == 0
    if not alive:
        return small("STOPPED", "secondary")

    try:
        with open(BEAM_STATE_FILE) as f:
            st = json.load(f)
    except Exception:
        return small("Starting", "info")   # session up, no state published yet

    if not st.get("connected"):
        return small("No NXCALS", "warning")   # session up but queries failing

    # NXCALS polls every ~30 s (plus ~1 min Spark restart after a blip), so the
    # staleness cutoff is much looser than the hardware watchers'.
    try:
        age = (datetime.now() - datetime.fromisoformat(st["timestamp"])).total_seconds()
    except Exception:
        age = None
    if age is not None and age > 180:
        return small("Stale", "warning")
    return small("Logging", "success")


# ---------------------------------------------------------------------------
# Run-progress helpers (used by the monitor's run-ended / long-run rules)
# ---------------------------------------------------------------------------

def status_field(info, label):
    """Value of a named field in a get_*_status() result, or None."""
    for f in (info or {}).get("fields", []):
        if f.get("label") == label:
            return f.get("value")
    return None


def hms_to_min(s):
    """'0h 1m 47s' -> minutes (float). Missing/garbage -> 0.0."""
    if not s:
        return 0.0
    m = re.search(r'(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?\s*(?:(\d+)\s*s)?', s)
    if not m:
        return 0.0
    h, mm, ss = (int(g) if g else 0 for g in m.groups())
    return h * 60 + mm + ss / 60.0


def get_run_progress(daq_info=None, dream_info=None):
    """{run, subrun_idx, subrun_total, elapsed_min, total_min} for the current run,
    from its run_config.json sub_runs + the live subrun name/elapsed. {} if unavailable.

    Self-contained mirror of flask_app.app._run_progress: reads the run name straight
    from the daq_control status card instead of the app's current-run cache, so the
    monitor thread can use it without importing the Flask app. `elapsed_min`/`subrun_idx`
    are only present when the live subrun matches a scheduled one (absent during ramp/prep).
    """
    if daq_info is None:
        daq_info = get_daq_control_status()
    if dream_info is None:
        dream_info = get_dream_daq_status()

    run_name = status_field(daq_info, "Run")
    run_dir = _run_dir()
    if not run_name or run_name in ("?", "None") or not run_dir:
        return {}
    try:
        with open(os.path.join(run_dir, run_name, "run_config.json")) as f:
            subs = json.load(f).get("sub_runs", [])
    except Exception:
        return {}
    if not subs:
        return {}

    names = [s.get("sub_run_name") for s in subs]
    durs  = [float(s.get("run_time", 0) or 0) for s in subs]  # minutes
    prog  = {"run": run_name, "subrun_total": len(subs), "total_min": sum(durs)}
    subrun = status_field(daq_info, "Subrun")
    if subrun in names:
        i = names.index(subrun)
        cur = min(hms_to_min(status_field(dream_info, "Run Time")), durs[i])
        prog["subrun_idx"]  = i + 1
        prog["elapsed_min"] = sum(durs[:i]) + cur
    return prog


def get_he3_pressure_watcher_status():
    """Compact card for the 3He target pressure-gauge watcher (the separate process that
    owns the Keithley 2000 GPIB link). Derived from tmux session existence + the state
    file the watcher publishes (connection + freshness), rendered as a small status-only
    card."""
    def small(status, color):
        return {"status": status, "color": color, "small": True, "fields": []}

    # No tmux session -> the watcher isn't running (pressure logging is down).
    alive = subprocess.run(["tmux", "has-session", "-t", "he3_pressure_watcher"],
                           capture_output=True).returncode == 0
    if not alive:
        return small("STOPPED", "secondary")

    try:
        with open(HE3_PRESSURE_STATE_FILE) as f:
            st = json.load(f)
    except Exception:
        return small("Starting", "info")   # session up, no state published yet

    if not st.get("connected"):
        return small("No Gauge", "warning")   # GPIB link up but Keithley not found

    # Session + connection OK: flag a stale card if the state file isn't being updated.
    try:
        age = (datetime.now() - datetime.fromisoformat(st["timestamp"])).total_seconds()
    except Exception:
        age = None
    if age is not None and age > 15:
        return small("Stale", "warning")
    return small("Logging", "success")


def get_n1081b_timetag_watcher_status():
    """Compact card for the N1081B Module-5 time-tag watcher (owns .244; streams the four
    scintillator walls as per-edge timestamps to a daily CSV). Derived from tmux session
    existence + the published state file (connection + freshness)."""
    def small(status, color):
        return {"status": status, "color": color, "small": True, "fields": []}

    alive = subprocess.run(["tmux", "has-session", "-t", "n1081b_timetag_watcher"],
                           capture_output=True).returncode == 0
    if not alive:
        return small("STOPPED", "secondary")

    try:
        with open(N1081B_TIMETAG_STATE_FILE) as f:
            st = json.load(f)
    except Exception:
        return small("Starting", "info")   # session up, no state published yet

    if not st.get("connected"):
        return small("No Board", "warning")   # session up but .244 not reachable

    # Session + connection OK: flag stale if the state file isn't being updated. The poll
    # cycle is ~1-2 s, so 15 s of silence means the loop is wedged.
    try:
        age = (datetime.now() - datetime.fromisoformat(st["timestamp"])).total_seconds()
    except Exception:
        age = None
    if age is not None and age > 15:
        return small("Stale", "warning")
    return small("Logging", "success")
