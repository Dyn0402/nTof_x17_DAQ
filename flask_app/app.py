#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on January 15 2:03 PM 2026
Created in PyCharm
Created as nTof_x17_DAQ/app.py

@author: Dylan Neff, dylan
"""

from flask import Flask, render_template, jsonify, request
import subprocess

app = Flask(__name__)

TMUX_SESSION = "daq_run"

def tmux_session_exists(name):
    result = subprocess.run(
        ["tmux", "has-session", "-t", name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    return result.returncode == 0

def start_daq():
    if tmux_session_exists(TMUX_SESSION):
        return False, "DAQ already running"
    subprocess.run([
        "tmux", "new-session", "-d", "-s", TMUX_SESSION,
        "python3 /path/to/run_daq.py"
    ])
    return True, "DAQ started"

def stop_daq():
    if not tmux_session_exists(TMUX_SESSION):
        return False, "DAQ not running"
    subprocess.run(["tmux", "kill-session", "-t", TMUX_SESSION])
    return True, "DAQ stopped"


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def status():
    running = tmux_session_exists(TMUX_SESSION)
    return jsonify({"running": running})


@app.route("/api/start", methods=["POST"])
def start():
    ok, msg = start_daq()
    return jsonify({"success": ok, "message": msg})


@app.route("/api/stop", methods=["POST"])
def stop():
    ok, msg = stop_daq()
    return jsonify({"success": ok, "message": msg})


if __name__ == "__main__":
    app.run(debug=True)
