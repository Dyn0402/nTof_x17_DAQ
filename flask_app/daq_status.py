#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on September 29 9:36 PM 2025
Created in PyCharm
Created as Cosmic_Bench_DAQ_Control/daq_status.py

@author: Dylan Neff, Dylan
"""

import subprocess
import re


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
            ["tmux", "capture-pane", "-pS", "-500", "-t", "dream_daq:0.0"],
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

        m_ev = re.search(r"nb_of_events=(\d+)", output)
        if m_ev: fields.append({"label": "Events", "value": m_ev.group(1)})

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
    else:
        status = "UNKNOWN STATE"
        color = "danger"

    return {"status": status, "color": color, "fields": fields}


# def get_hv_control_status():
#     try:
#         output = subprocess.check_output(
#             ["tmux", "capture-pane", "-pS", "-50", "-t", "hv_control:0.0"],
#             text=True
#         )
#     except subprocess.CalledProcessError:
#         return {
#             "status": "ERROR",
#             "color": "danger",
#             "fields": [{"label": "Details", "value": "hv_control tmux not running"}]
#         }
#
#     # define your rules once, ordered by priority
#     rules = [
#         ("Listening on ", "WAITING", "secondary"),
#         ("Powering off HV", "HV Off", "secondary"),
#         ("HV Powered Off", "HV Off", "secondary"),
#         ("Monitoring HV", "Monitoring HV", "success"),
#         ("HV Ramped", "HV Ramped", "success"),
#         ("Setting HV", "Ramping HV", "warning"),
#         ("Checking HV ramp", "Ramping HV", "warning"),
#         ("Waiting for HV to ramp", "Ramping HV", "warning"),
#     ]
#
#     # scan bottom-to-top for the most recent matching line
#     for line in reversed(output.splitlines()):
#         for flag, status, color in rules:
#             if flag in line:
#                 return {"status": status, "color": color, "fields": []}
#
#     # fallback if nothing matched
#     return {"status": "UNKNOWN STATE", "color": "danger", "fields": []}


def get_hv_control_status():
    try:
        output = subprocess.check_output(
            ["tmux", "capture-pane", "-pS", "-50", "-t", "hv_control:0.0"],
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

    # Split by "Monitoring HV" and only take the last block
    # blocks = output.split("Monitoring HV")
    # if blocks:
    #     last_block = blocks[-1]
    # else:
    #     last_block = output

    # Parse individual channel lines in the last block
    fields = []
    # channel_pattern = re.compile(
    #     r"Slot\s+(\d+)\s+Channel\s+(\d+):\s+power=(on|off),\s+v set=([\d.]+),\s+v mon=([\d.]+),\s+i mon=([\d.]+)"
    # )
    # for line in last_block.splitlines():
    #     m = channel_pattern.search(line)
    #     if m:
    #         slot, ch, power, v_set, v_mon, i_mon = m.groups()
    #         fields.append({
    #             "label": f"{slot}:{ch}",
    #             "value": f"Vset={v_set}, V={v_mon}, I={i_mon}"
    #         })

    return {"status": status, "color": color, "fields": fields}



def get_daq_control_status():
    try:
        output = subprocess.check_output(
            ["tmux", "capture-pane", "-pS", "-10", "-t", "daq_control:0.0"],
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
        ("Finished with sub run ", "Finished Sub Run", "warning"),
        ("Dream DAQ taking pedestals", "Prepping DAQs", "warning"),
        ("Prepping DAQs for ", "Prepping DAQs", "warning"),
        ("Ramping HVs for ", "Ramping HV", "warning"),
        ("Starting DAQ Control", "STARTING", "warning"),
        ("Dream DAQ started", "RUNNING", "success"),
        ("Stopping DAQ process", "Stopping DAQ", "warning"),
    ]

    fields = []
    for line in reversed(output.splitlines()):  # Find most recent "Sent: Start ..." line
        if line.startswith("Sent: Start"):
            parts = line.split()
            if len(parts) >= 4:
                fields.append({"label": "Subrun", "value": parts[2]})
                fields.append({"label": "Runtime (min)", "value": parts[3]})
            break

    for line in reversed(output.splitlines()):
        for flag, status, color in rules:
            if flag in line:
                return {"status": status, "color": color, "fields": fields}

    return {"status": "UNKNOWN STATE", "color": "danger", "fields": fields}


def get_trigger_veto_control_status():
    try:
        output = subprocess.check_output(
            ["tmux", "capture-pane", "-pS", "-10", "-t", "trigger_veto_control:0.0"],
            text=True
        )
    except subprocess.CalledProcessError:
        return {
            "status": "ERROR",
            "color": "danger",
            "fields": [{"label": "Details", "value": "trigger_veto_control tmux not running"}]
        }

    rules = [
        ("Trigger switch turned on", "Triggering", "success"),
        ("Trigger switch turned off", "Triggers Held", "warning"),
        ("Trigger switch control connected", "STARTING", "warning"),
        ("Listening on ", "WAITING", "secondary"),
    ]

    for line in reversed(output.splitlines()):
        for flag, status, color in rules:
            if flag in line:
                return {"status": status, "color": color, "fields": []}

    return {"status": "UNKNOWN STATE", "color": "danger", "fields": []}


def get_trigger_gen_control_status():
    try:
        output = subprocess.check_output(
            ["tmux", "capture-pane", "-pS", "-10", "-t", "trigger_gen_control:0.0"],
            text=True
        )
    except subprocess.CalledProcessError:
        return {
            "status": "ERROR",
            "color": "danger",
            "fields": [{"label": "Details", "value": "trigger_gen_control tmux not running"}]
        }

    rules = [
        ("Trigger ", "RUNNING", "success"),
        ("Stopped sending triggers", "STOPPED", "warning"),
        ("Listening on ", "WAITING", "secondary"),
    ]

    # collect trigger number if present (e.g. `Trigger 103343`)
    fields = []
    trg_iter = list(re.finditer(r"\bTrigger\s+(\d+)\b", output))
    if trg_iter:
        last_trg = trg_iter[-1]
        tr_num = last_trg.group(1)
        # Only include trigger if there is no "Stopped sending triggers" after this match
        if "Stopped sending triggers" not in output[last_trg.end():]:
            fields.append({"label": "Trigger", "value": tr_num})

    for line in reversed(output.splitlines()):
        for flag, status, color in rules:
            if flag in line:
                return {"status": status, "color": color, "fields": fields}
    return {"status": "UNKNOWN STATE", "color": "danger", "fields": fields}


def get_banco_tracker_status():
    try:
        output = subprocess.check_output(
            ["tmux", "capture-pane", "-pS", "-10", "-t", "banco_tracker:0.0"],
            text=True
        )
    except subprocess.CalledProcessError:
        return {
            "status": "ERROR",
            "color": "danger",
            "fields": [{"label": "Details", "value": "banco_tracker tmux not running"}]
        }

    rules = [
        ('Moving data files...', "COPYING FILES", "info"),
        ('boost/v1.72.0-alice1-54(31):ERROR:105: Unable to locate a modulefile for ', "NOT READY YET", "danger"),
        ("Waiting for trigger...", "RUNNING", "success"),
        ("Triggers to the MOSAIC (trgCount)", "RUNNING", "success"),
        ("ROOT files ready to be closed", "RUNNING", "success"),
        ('- trains: ', "RUNNING", "success"),
        ("Banco DAQ stopped", "STOPPED", "warning"),
        ("Listening on ", "WAITING", "secondary"),
    ]

    fields = []

    # Find all trigger count matches
    trg_count_matches = re.findall(r"Triggers to the MOSAIC \(trgCount\)\s*:\s*(\d+)", output)

    # Take the last 4 if available
    if trg_count_matches:
        last_four = trg_count_matches[-4:]

        # Convert to integers and sort descending
        sorted_triggers = sorted(map(int, last_four), reverse=True)

        for i, val in enumerate(sorted_triggers, start=1):
            fields.append({"label": f"Trigger Count {i}", "value": str(val)})

    # Check status lines (unchanged)
    for line in reversed(output.splitlines()):
        for flag, status, color in rules:
            if flag in line:
                return {"status": status, "color": color, "fields": fields}

    return {"status": "UNKNOWN STATE", "color": "danger", "fields": fields}


# def get_banco_tracker_status():
#     try:
#         output = subprocess.check_output(
#             ["tmux", "capture-pane", "-pS", "-10", "-t", "banco_tracker:0.0"],
#             text=True
#         )
#     except subprocess.CalledProcessError:
#         return {
#             "status": "ERROR",
#             "color": "danger",
#             "fields": [{"label": "Details", "value": "banco_tracker tmux not running"}]
#         }
#
#     rules = [
#         ("Waiting for trigger...", "RUNNING", "success"),
#         ("Triggers to the MOSAIC (trgCount)", "RUNNING", "success"),
#         ("ROOT files ready to be closed", "RUNNING", "success"),
#         ("Banco DAQ stopped", "STOPPED", "warning"),
#         ("Listening on ", "WAITING", "secondary"),
#     ]
#
#     for line in reversed(output.splitlines()):
#         for flag, status, color in rules:
#             if flag in line:
#                 return {"status": status, "color": color, "fields": []}
#
#     return {"status": "UNKNOWN STATE", "color": "danger", "fields": []}


def get_decoder_status():
    try:
        output = subprocess.check_output(
            ["tmux", "capture-pane", "-pS", "-10", "-t", "decoder:0.0"],
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


def get_desync_monitor_status():
    try:
        output = subprocess.check_output(
            ["tmux", "capture-pane", "-pS", "-50", "-t", "desync_monitor:0.0"],
            text=True
        )
    except subprocess.CalledProcessError:
        return {
            "status": "ERROR",
            "color": "danger",
            "fields": [{"label": "Details", "value": "desync_monitor tmux not running"}]
        }

    lines = [l for l in output.splitlines() if l.strip()]
    last_line = lines[-1] if lines else ""

    fields = []

    # --- PRIORITY OVERRIDE: WAITING ---
    if "Listening on " in last_line or "Listening..." in last_line:
        return {
            "status": "WAITING",
            "color": "secondary",
            "fields": []
        }
    # -----------------------------------

    # find Δ line
    delta_line = ""
    for line in reversed(lines):
        if "Δ=" in line:
            delta_line = line.strip()
            break

    m = re.search(r"Δ=(-?\d+)", delta_line)
    diff_val = int(m.group(1)) if m else None
    m_time = re.search(r"([0-9.]+)s until stop", delta_line)
    time_left = float(m_time.group(1)) if m_time else None

    # Banco sync messages
    banco_sync = None
    for line in reversed(lines):
        if "Banco internal desync" in line:
            banco_sync = False
            break
        elif "Banco internal sync OK" in line:
            banco_sync = True
            break

    # Main status logic
    if "Persistent desync detected" in output or "⚠️" in output:
        status = "DESYNC DETECTED"
        color = "danger"
    elif diff_val is not None and diff_val != 0:
        if time_left is not None:
            if time_left < 10:
                status = "DESYNC"
                color = "warning"
            else:
                status = "MONITORING (DESYNC TREND)"
                color = "success"
        else:
            status = "MONITORING"
            color = "success"
    else:
        status = "SYNCED"
        color = "success"

    # Banco sync field
    if banco_sync is False:
        status = "BANCO DESYNCED"
        color = "warning"
        fields.append({"label": "Banco Sync", "value": "❌ Internal mismatch"})
    elif banco_sync is True:
        fields.append({"label": "Banco Sync", "value": "✅ All ladders aligned"})

    # Δ + time-left fields
    if diff_val is not None:
        fields.append({"label": "Δ (banco - dream)", "value": str(diff_val)})
    if time_left is not None:
        fields.append({"label": "Seconds until stop", "value": f"{time_left:.1f}"})

    return {
        "status": status,
        "color": color,
        "fields": fields or [{"label": "Details", "value": "No recent desync output"}]
    }


# def get_desync_monitor_status():
#     try:
#         output = subprocess.check_output(
#             ["tmux", "capture-pane", "-pS", "-30", "-t", "desync_monitor:0.0"],
#             text=True
#         )
#     except subprocess.CalledProcessError:
#         return {
#             "status": "ERROR",
#             "color": "danger",
#             "fields": [{"label": "Details", "value": "desync_monitor tmux not running"}]
#         }
#
#     # Default fields
#     fields = []
#
#     # Parse latest Δ and timing info
#     last_line = ""
#     for line in reversed(output.splitlines()):
#         if "Δ=" in line:
#             last_line = line.strip()
#             break
#
#     # Extract difference and countdown if present
#     m = re.search(r"Δ=(-?\d+)", last_line)
#     diff_val = int(m.group(1)) if m else None
#
#     m_time = re.search(r"([0-9.]+)s until stop", last_line)
#     time_left = float(m_time.group(1)) if m_time else None
#
#     # Determine state and color
#     if "Persistent desync detected" in output or "⚠️" in output:
#         status = "DESYNC DETECTED"
#         color = "danger"
#     elif diff_val is not None and diff_val != 0:
#         # Getting close to desync if countdown exists
#         if time_left is not None:
#             if time_left < 10:
#                 status = "IMMINENT DESYNC"
#                 color = "warning"
#             else:
#                 status = "MONITORING (DESYNC TREND)"
#                 color = "success"
#         else:
#             status = "MONITORING"
#             color = "success"
#     else:
#         status = "SYNCED"
#         color = "success"
#
#     # Build field details for display
#     if diff_val is not None:
#         fields.append({"label": "Δ (banco - dream)", "value": str(diff_val)})
#     if time_left is not None:
#         fields.append({"label": "Seconds until stop", "value": f"{time_left:.1f}"})
#
#     # Check if listening in two lines
#     last_lines = output.splitlines()[-3:]
#     if len(last_lines) >= 2 and any("Listening on " in line for line in last_lines):
#         status = "WAITING"
#         color = "secondary"
#
#     return {
#         "status": status,
#         "color": color,
#         "fields": fields or [{"label": "Details", "value": "No recent desync output"}]
#     }
