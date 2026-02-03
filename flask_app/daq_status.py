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

    # Parse individual channel lines in the last block
    fields = []

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
        ("Dream DAQ starting", "RUNNING", "success"),
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
