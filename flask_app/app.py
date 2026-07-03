#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on September 29 3:45 PM 2025
Created in PyCharm
Created as Cosmic_Bench_DAQ_Control/app.py

@author: Dylan Neff, Dylan
"""

import os
import re
import sys
import subprocess
import pty
import select
import threading
import time
import json
from datetime import datetime
import pandas as pd
from urllib.parse import quote
from flask import Flask, render_template, jsonify, request, send_from_directory, abort
from flask_socketio import SocketIO, emit

from daq_status import (get_dream_daq_status, get_hv_control_status,
                        get_daq_control_status, get_processor_watcher_status,
                        get_qa_watcher_status, get_backup_watcher_status,
                        get_pedestal_watcher_status)

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # Add parent dir to path
from run_config_beam import Config, BASE_DATA_DIR
from get_run_events import get_total_events_for_run
from monitor import DaqMonitor, fetch_chat_id, get_bot_username

# BASE_DIR = "/home/dylan/PycharmProjects/nTof_x17_DAQ"
BASE_DIR = "/home/mx17/PycharmProjects/nTof_x17_DAQ"
CONFIG_TEMPLATE_DIR = f"{BASE_DIR}/config/json_templates"
CONFIG_RUN_DIR = f"{BASE_DIR}/config/json_run_configs"
CONFIG_PY_PATH = f"{BASE_DIR}/run_config_beam.py"
BASH_DIR = f"{BASE_DIR}/bash_scripts"
PROCESSOR_CONFIG_PATH = f"{BASE_DIR}/config/processor_config.json"
PROCESSOR_TMUX = "processor_watcher"
QA_CONFIG_PATH = f"{BASE_DIR}/config/qa_config.json"
QA_RESET_PATH  = f"{BASE_DIR}/config/qa_reset.json"
QA_TMUX = "qa_watcher"
BACKUP_CONFIG_PATH = f"{BASE_DIR}/config/backup_config.json"
BACKUP_TMUX = "backup_watcher"
PED_QA_CONFIG_PATH = f"{BASE_DIR}/config/pedestal_qa_config.json"
PED_QA_TMUX = "pedestal_watcher"
# Last run name seen in the daq_control log; persisted so "Current run" survives
# the status line scrolling out of the tmux pane / between runs / server restarts.
CURRENT_RUN_STATE_PATH = f"{BASE_DIR}/config/current_run_state.json"
# ANALYSIS_DIR = "/media/dylan/data/x17"
# RUN_DIR = "/media/dylan/data/x17/dream_run_test"
ANALYSIS_DIR = f'{BASE_DATA_DIR}analysis'
RUN_DIR = f'{BASE_DATA_DIR}runs'
GENERAL_ANALYSIS_DIR = f'{BASE_DATA_DIR}analysis'
HV_TAIL = 1000  # number of most recent rows to show

LOG_DIR = f"{BASE_DIR}/logs"
LOG_FILE = f"{LOG_DIR}/daq_events.log"

MONITOR_CONFIG_PATH = f"{BASE_DIR}/config/monitor_config.json"
monitor = DaqMonitor(MONITOR_CONFIG_PATH)


def log_event(event, source, **details):
    """Append one line to the DAQ event log."""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        detail_str = ' | '.join(f'{k}={v}' for k, v in details.items())
        line = f"{ts} | {event:<14} | {source:<12} | {detail_str}\n"
        with open(LOG_FILE, 'a') as f:
            f.write(line)
    except Exception as e:
        print(f"Warning: could not write to event log: {e}")


app = Flask(__name__)
socketio = SocketIO(app)

TMUX_SESSIONS = ["daq_control", "dream_daq", "hv_control", "processor_watcher", "qa_watcher", "backup_watcher",
                 "pedestal_watcher"]
sessions = {}

@app.route("/")
def index():
    configs = [f for f in os.listdir(CONFIG_RUN_DIR) if f.endswith(".json")]
    return render_template("index.html", screens=TMUX_SESSIONS, run_configs=configs)


# --- Current run tracking (from daq_control log, with persistence) ---
def _load_current_run():
    """Load the last-seen run name from disk (survives server restarts)."""
    try:
        with open(CURRENT_RUN_STATE_PATH) as f:
            return json.load(f).get("run_name")
    except Exception:
        return None


_current_run_cache = _load_current_run()


def _extract_daq_run(daq_info):
    """Pull the Run value out of a get_daq_control_status() result, or None."""
    for field in daq_info.get("fields", []):
        if field.get("label") == "Run":
            value = field.get("value")
            if value and value not in ("?", "None"):
                return value
    return None


def _save_current_run(run_name):
    """Persist run_name as the current run if it changed from what we have."""
    global _current_run_cache
    if not run_name or run_name == _current_run_cache:
        return
    _current_run_cache = run_name
    try:
        with open(CURRENT_RUN_STATE_PATH, "w") as f:
            json.dump({"run_name": run_name, "updated": datetime.now().isoformat()}, f)
    except Exception as e:
        print(f"[current_run] Failed to persist run name: {e}")


@app.route("/get_current_run")
def get_current_run():
    """Current run as last seen in the daq_control log, falling back to the
    persisted value so it doesn't blank out between runs."""
    return jsonify({"success": True, "run_name": _current_run_cache or "None"})


@app.route("/status")
def status_all():
    statuses = []

    ordered_sessions = TMUX_SESSIONS  # Fix ordering

    for s in ordered_sessions:
        if s == "dream_daq":
            info = get_dream_daq_status()
        elif s == "hv_control":
            info = get_hv_control_status()
        elif s == "daq_control":
            info = get_daq_control_status()
            _save_current_run(_extract_daq_run(info))  # keep Current run in sync
        elif s == "processor_watcher":
            info = get_processor_watcher_status()
        elif s == "qa_watcher":
            info = get_qa_watcher_status()
        elif s == "backup_watcher":
            info = get_backup_watcher_status()
        elif s == "pedestal_watcher":
            info = get_pedestal_watcher_status()
        else:
            info = {"status": "READY", "color": "secondary", "fields": []}

        statuses.append({"name": s, **info})

    return jsonify(statuses)


@app.route("/start_run", methods=["POST"])
def start_run():
    data = request.get_json()
    config_file = data.get("config")

    if not config_file:
        return jsonify({"message": "No config selected"}), 400

    config_path = os.path.join(CONFIG_RUN_DIR, config_file)
    if not os.path.exists(config_path):
        return jsonify({"message": f"Config not found: {config_path}"}), 404

    script_path = f"{BASH_DIR}/start_run.sh"
    result = subprocess.run(
        [script_path, config_path],
        capture_output=True,
        text=True
    )

    if result.returncode == 0:
        return jsonify({"message": f"Run started with {config_file}"})
    else:
        return jsonify({"message": f"Error: {result.stderr}"}), 500

@app.route("/stop_sub_run", methods=["POST"])
def stop_sub_run():
    try:
        if is_dream_daq_running():
            log_event('STOP_SUB_RUN', 'flask_button', remote_addr=request.remote_addr)
            subprocess.Popen([f"{BASH_DIR}/stop_sub_run.sh"])
            return jsonify({"success": True, "message": "Stopping Sub-Run"})
        else:
            return jsonify({"success": False, "message": "Dream DAQ is not running"}), 400
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/stop_run", methods=["POST"])
def stop_run():
    try:
        dream_running = is_dream_daq_running()
        if dream_running:
            log_event('STOP_RUN', 'flask_button', remote_addr=request.remote_addr, dream_running=True)
            subprocess.Popen([f"{BASH_DIR}/stop_run.sh"])
            return jsonify({"success": True, "message": "DAQ Running, Stopping Run"})
        else:
            log_event('STOP_RUN', 'flask_button', remote_addr=request.remote_addr, dream_running=False)
            subprocess.Popen([f"{BASH_DIR}/stop_sub_run.sh"])  # Only 1 ctrl-c needed if not running
            return jsonify({"success": True, "message": "No DAQ Running, Stopping Run"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/restart_all", methods=["POST"])
def restart_all():
    try:
        subprocess.Popen([f"{BASH_DIR}/restart_daq_tmux_processes.sh"])
        return jsonify({"success": True, "message": "All processes restarted"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/update_run_config_py", methods=['POST'])
def update_run_config_py():
    try:
        subprocess.Popen(["python", f"{BASE_DIR}/iterate_run_num.py"])
        time.sleep(0.2)  # Give it a moment to complete

        return jsonify({"success": True, "message": f"Run number iterated"})

    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/run_config_py", methods=['POST'])
def run_config_py():
    try:
        subprocess.Popen(["python", f"{BASE_DIR}/run_config_beam.py"])
        time.sleep(1)
        config_path = os.path.join(CONFIG_RUN_DIR, 'run_config_beam.json')
        if not os.path.exists(config_path):
            return jsonify({"message": f"Config not found: {config_path}"}), 404

        script_path = f"{BASH_DIR}/start_run.sh"
        result = subprocess.run(
            [script_path, config_path],
            capture_output=True,
            text=True
        )

        # Load config path json to get run name
        try:
            with open(config_path) as f:
                cfg = json.load(f)
            run_name = cfg.get("run_name", "Unknown")
        except Exception as e:
            run_name = "Error loading run name"

        if result.returncode == 0:
            _save_current_run(run_name)  # seed Current run immediately
            return jsonify({"success": True, "message": f"Run started with loaded run_config_beam.py", "run_name": run_name})
        else:
            return jsonify({"message": f"Error: {result.stderr}"}), 500
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/take_pedestals", methods=["POST"])
def take_pedestals():
    try:
        subprocess.Popen([f"{BASH_DIR}/run_pedestals.sh"])
        return jsonify({"success": True, "message": "Taking pedestals"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/git_reset", methods=["POST"])
def git_reset():
    try:
        subprocess.Popen([f"{BASH_DIR}/git_reset.sh"])
        return jsonify({"success": True, "message": "Git now up to date"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/start_processor", methods=["POST"])
def start_processor():
    try:
        # Regenerate processor_config.json from processor_config.py
        result = subprocess.run(
            [sys.executable, f"{BASE_DIR}/processor_config.py"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            return jsonify({"success": False, "message": f"Config generation failed: {result.stderr}"}), 500

        # Kill any existing session first (ignore errors if not running)
        subprocess.run(["tmux", "kill-session", "-t", PROCESSOR_TMUX], capture_output=True)
        # sys.executable (flask's venv python), not bare "python": the tmux
        # login shell resets PATH and drops the venv, so "python" may not resolve.
        subprocess.Popen([
            "tmux", "new-session", "-d", "-s", PROCESSOR_TMUX,
            sys.executable, f"{BASE_DIR}/processor_watcher.py", PROCESSOR_CONFIG_PATH
        ])
        return jsonify({"success": True, "message": "Processor watcher started"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/stop_processor", methods=["POST"])
def stop_processor():
    try:
        subprocess.run(["tmux", "kill-session", "-t", PROCESSOR_TMUX], capture_output=True)
        return jsonify({"success": True, "message": "Processor watcher stopped"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/start_qa", methods=["POST"])
def start_qa():
    try:
        result = subprocess.run(
            [sys.executable, f"{BASE_DIR}/qa_config.py"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            return jsonify({"success": False, "message": f"Config generation failed: {result.stderr}"}), 500
        subprocess.run(["tmux", "kill-session", "-t", QA_TMUX], capture_output=True)
        # sys.executable (flask's venv python), not bare "python": the tmux
        # login shell resets PATH and drops the venv, so "python" may not resolve.
        subprocess.Popen([
            "tmux", "new-session", "-d", "-s", QA_TMUX,
            sys.executable, f"{BASE_DIR}/qa_watcher.py", QA_CONFIG_PATH
        ])
        return jsonify({"success": True, "message": "QA watcher started"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/stop_qa", methods=["POST"])
def stop_qa():
    try:
        subprocess.run(["tmux", "kill-session", "-t", QA_TMUX], capture_output=True)
        return jsonify({"success": True, "message": "QA watcher stopped"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/start_backup", methods=["POST"])
def start_backup():
    try:
        result = subprocess.run(
            [sys.executable, f"{BASE_DIR}/backup_config.py"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            return jsonify({"success": False, "message": f"Config generation failed: {result.stderr}"}), 500
        subprocess.run(["tmux", "kill-session", "-t", BACKUP_TMUX], capture_output=True)
        # sys.executable (flask's venv python), not bare "python": the tmux
        # login shell resets PATH and drops the venv, so "python" may not resolve.
        subprocess.Popen([
            "tmux", "new-session", "-d", "-s", BACKUP_TMUX,
            sys.executable, f"{BASE_DIR}/backup_watcher.py", BACKUP_CONFIG_PATH
        ])
        return jsonify({"success": True, "message": "Backup watcher started"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/stop_backup", methods=["POST"])
def stop_backup():
    try:
        subprocess.run(["tmux", "kill-session", "-t", BACKUP_TMUX], capture_output=True)
        return jsonify({"success": True, "message": "Backup watcher stopped"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/start_ped_qa", methods=["POST"])
def start_ped_qa():
    try:
        result = subprocess.run(
            [sys.executable, f"{BASE_DIR}/pedestal_qa_config.py"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            return jsonify({"success": False, "message": f"Config generation failed: {result.stderr}"}), 500
        subprocess.run(["tmux", "kill-session", "-t", PED_QA_TMUX], capture_output=True)
        # sys.executable (flask's venv python), not bare "python": the tmux
        # server env doesn't always carry the venv PATH, so name resolution
        # inside new sessions is unreliable.
        subprocess.Popen([
            "tmux", "new-session", "-d", "-s", PED_QA_TMUX,
            sys.executable, f"{BASE_DIR}/pedestal_watcher.py", PED_QA_CONFIG_PATH
        ])
        return jsonify({"success": True, "message": "Pedestal QA watcher started"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/stop_ped_qa", methods=["POST"])
def stop_ped_qa():
    try:
        subprocess.run(["tmux", "kill-session", "-t", PED_QA_TMUX], capture_output=True)
        return jsonify({"success": True, "message": "Pedestal QA watcher stopped"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


def _ped_qa_cfg():
    """(pedestals_dir, output_inner_dir) from the ped QA config, with the same
    defaults pedestal_qa_config.py writes (config may not exist yet)."""
    try:
        with open(PED_QA_CONFIG_PATH) as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    return (cfg.get("pedestals_dir", f"{BASE_DATA_DIR}pedestals/"),
            cfg.get("output_inner_dir", "ped_qa"))


@app.route("/list_ped_runs")
def list_ped_runs():
    """Pedestal run dirs (newest first) with whether QA output exists yet."""
    ped_dir, inner_dir = _ped_qa_cfg()

    if not os.path.isdir(ped_dir):
        return jsonify(success=False, message=f"Pedestals dir not found: {ped_dir}")

    def run_sort_key(name, full):
        # Prefer the datetime in the dir name (pedestals_MM-DD-YY_HH-MM-SS);
        # dir mtime is unreliable since QA output writes touch the dir.
        # Both key kinds are epoch floats so they compare consistently.
        m = re.search(r'(\d{2})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})', name)
        if m:
            try:
                mo, d, y, h, mi, s = (int(g) for g in m.groups())
                return datetime(2000 + y, mo, d, h, mi, s).timestamp()
            except ValueError:
                pass
        return os.path.getmtime(full)

    runs = []
    for d in os.listdir(ped_dir):
        full = os.path.join(ped_dir, d)
        if not os.path.isdir(full):
            continue
        runs.append({
            "name": d,
            "sort_key": run_sort_key(d, full),
            "has_qa": os.path.isfile(os.path.join(full, inner_dir, "summary.json")),
        })
    runs.sort(key=lambda r: r["sort_key"], reverse=True)
    return jsonify(success=True, runs=runs, inner_dir=inner_dir, ped_dir=ped_dir)


@app.route("/ped_qa_data")
def ped_qa_data():
    """Summary JSON + image/PDF URLs for one pedestal run's QA output."""
    run_name = request.args.get("run", "")
    ped_dir, inner_dir = _ped_qa_cfg()

    # Plain directory names only — no separators, no '.'/'..' path tricks
    if not re.fullmatch(r'(?!\.+$)[\w.\-]+', run_name):
        return jsonify(success=False, message="Invalid run name"), 400
    qa_dir = os.path.join(ped_dir, run_name, inner_dir)
    if not os.path.isdir(qa_dir):
        return jsonify(success=True, has_qa=False, summary=None, images=[], pdf=None)

    summary = None
    summary_path = os.path.join(qa_dir, "summary.json")
    if os.path.isfile(summary_path):
        try:
            with open(summary_path) as f:
                summary = json.load(f)
        except Exception:
            pass

    dir_q  = quote(qa_dir, safe='')
    images = [f"/serve_png?dir={dir_q}&file={quote(f, safe='')}"
              for f in sorted(os.listdir(qa_dir)) if f.lower().endswith(".png")]
    pdf = None
    if os.path.isfile(os.path.join(qa_dir, "pedestal_strip_check.pdf")):
        pdf = f"/serve_png?dir={dir_q}&file=pedestal_strip_check.pdf"

    return jsonify(success=True, has_qa=summary is not None,
                   summary=summary, images=images, pdf=pdf)


@app.route("/rerun_qa", methods=["POST"])
def rerun_qa():
    try:
        data = request.get_json(silent=True) or {}
        runs = data.get('runs') or None  # null/missing/empty → all runs
        with open(QA_RESET_PATH, 'w') as f:
            json.dump({"runs": runs}, f)
        if runs:
            msg = f"QA rerun queued for: {', '.join(runs)}"
        else:
            msg = "QA rerun queued for all runs"
        return jsonify({"success": True, "message": msg})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/get_runs")
def get_runs():
    runs = []
    for f in os.listdir(CONFIG_RUN_DIR):
        if f.endswith(".json"):
            runs.append(f)
    return jsonify(runs)

@app.route("/get_subruns")
def get_subruns():
    run_name = request.args.get("run")
    if not run_name:
        return jsonify([])

    config_path = os.path.join(CONFIG_RUN_DIR, run_name)
    if not os.path.isfile(config_path):
        return jsonify([])

    try:
        with open(config_path) as f:
            cfg = json.load(f)
        output_dir = cfg.get("run_out_dir")
        if not output_dir or not os.path.isdir(output_dir):
            return jsonify([])

        subruns = sorted(
            os.listdir(output_dir),
            key=lambda f: os.path.getmtime(os.path.join(output_dir, f)),
            reverse=True
        )

        # Ensure it matches item in cfg['subruns'][i]['sub_run_name'] if that key exists
        if "sub_runs" in cfg:
            valid_subruns = {sr.get("sub_run_name") for sr in cfg["sub_runs"] if "sub_run_name" in sr}
            subruns = [sr for sr in subruns if sr in valid_subruns]

        return jsonify(subruns)
    except Exception as e:
        print("Error reading subruns:", e)
        return jsonify([])

@app.route("/get_run_name")
def get_run_name():
    run_name = request.args.get("run")
    if not run_name:
        return jsonify({"success": False, "message": "No run specified"}), 400

    config_path = os.path.join(CONFIG_RUN_DIR, run_name)
    if not os.path.isfile(config_path):
        return jsonify({"success": False, "message": "Run config not found"}), 404

    try:
        with open(config_path) as f:
            cfg = json.load(f)
        actual_run_name = cfg.get("run_name", "Unknown")
        return jsonify({"success": True, "run_name": actual_run_name})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/hv_data")
def hv_data():
    try:
        run_name = request.args.get("run")
        subrun_name = request.args.get("subrun")
        hv_file_name = request.args.get("hv_file", "hv_monitor.csv")

        config_path = os.path.join(CONFIG_RUN_DIR, run_name)
        if not os.path.isfile(config_path):
            return jsonify([])

        with open(config_path) as f:
            cfg = json.load(f)
        output_dir = cfg.get("run_out_dir")
        hv_csv_path = os.path.join(output_dir, subrun_name, hv_file_name)

        df = pd.read_csv(hv_csv_path)
        df = df.tail(HV_TAIL)

        # Extract timestamps
        time = df["timestamp"].astype(str).tolist()

        voltage_data = {}
        current_data = {}

        # Loop through columns to find slot:channel prefixes
        for col in df.columns:
            if "vmon" in col:
                key = col.replace(" vmon", "")
                voltage_data[key] = df[col].tolist()
            elif "imon" in col:
                key = col.replace(" imon", "")
                current_data[key] = df[col].tolist()

        return jsonify({
            "success": True,
            "time": time,
            "voltage": voltage_data,
            "current": current_data
        })

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/list_analysis_dirs")
def list_analysis_dirs():
    subdir = request.args.get("subdir", "")
    target_dir = os.path.join(ANALYSIS_DIR, subdir)

    if not os.path.isdir(target_dir):
        return jsonify(success=False, message=f"Invalid directory: {target_dir}")

    dirs = [d for d in os.listdir(target_dir)
            if os.path.isdir(os.path.join(target_dir, d))]
    dirs.sort()

    return jsonify(success=True, subdirs=dirs)

@app.route("/list_pngs")
def list_pngs():
    directory = request.args.get("dir")
    directory = os.path.join(ANALYSIS_DIR, directory)
    if not directory:
        return jsonify(success=False, message="No directory specified")
    if not os.path.isdir(directory):
        return jsonify(success=False, message=f"Invalid directory: {directory}")

    pngs = sorted(f for f in os.listdir(directory) if f.lower().endswith(".png"))
    if not pngs:
        return jsonify(success=True, images=[])

    # Create static-serving routes for these files
    image_urls = [f"/serve_png?dir={directory}&file={f}" for f in pngs]
    return jsonify(success=True, images=image_urls)


@app.route("/serve_png")
def serve_png():
    directory = request.args.get("dir")
    filename = request.args.get("file")
    if not directory or not filename:
        abort(400, "Missing parameters")
    if not os.path.isfile(os.path.join(directory, filename)):
        abort(404, "File not found")
    return send_from_directory(directory, filename)


@app.route("/browse_analysis")
def browse_analysis():
    rel_path = request.args.get("path", "").strip("/")
    target = os.path.normpath(os.path.join(GENERAL_ANALYSIS_DIR, rel_path)) if rel_path \
             else os.path.normpath(GENERAL_ANALYSIS_DIR)

    # Prevent path traversal outside the analysis directory
    if not target.startswith(os.path.abspath(GENERAL_ANALYSIS_DIR)):
        return jsonify(success=False, message="Invalid path"), 403
    if not os.path.isdir(target):
        return jsonify(success=False, message=f"Directory not found: {target}")

    subdirs = sorted(d for d in os.listdir(target)
                     if os.path.isdir(os.path.join(target, d)))
    images  = [f"/serve_png?dir={quote(target, safe='')}&file={quote(f, safe='')}"
               for f in sorted(os.listdir(target))
               if f.lower().endswith(".png")]

    return jsonify(success=True, subdirs=subdirs, images=images, path=rel_path)


@app.route("/get_config_py", methods=['GET'])
def get_config_py():
    try:
        # Call get_config function from run_config_beam.py
        result = subprocess.run(
            ["python", f"{BASE_DIR}/get_config_py.py"],
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            return jsonify({"success": False, "message": f"Error: {result.stderr}"}), 500
        output = result.stdout.strip()
        config_data = json.loads(output)
        run_name = config_data.get("run_name", "Unknown")

        return jsonify({
            "success": True,
            "run_name": run_name,
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/get_run_events", methods=['GET'])
def get_run_events():
    try:
        # Count events for the run daq_control is actually running (not the
        # possibly-edited run_config_beam.py). Falls back to the persisted value.
        run_name = _current_run_cache
        if not run_name:
            return jsonify({"success": True, "total_events": 0, "subrun_details": {}})
        total_events, subrun_details = get_total_events_for_run(
            run_dir=RUN_DIR,
            run_name=run_name
        )
        return jsonify({
            "success": True,
            "total_events": total_events,
            "subrun_details": subrun_details
        })
    except Exception as e:
        return jsonify({"success": False, "message": f"Error getting run events: {str(e)}"}), 500


@app.route("/monitor/toggle", methods=["POST"])
def monitor_toggle():
    monitor.toggle()
    return jsonify({"running": monitor.is_running})


@app.route("/monitor/status")
def monitor_status():
    return jsonify(monitor.status_dict())


@app.route("/monitor/fetch_chat_id", methods=["POST"])
def monitor_fetch_chat_id():
    if not monitor.token:
        return jsonify({"success": False, "message": "No Telegram token configured."})
    chat_id, err = fetch_chat_id(monitor.token)
    if err:
        return jsonify({"success": False, "message": err})
    monitor.set_chat_id(chat_id)
    return jsonify({"success": True, "chat_id": chat_id})


@app.route("/monitor/set_chat_id", methods=["POST"])
def monitor_set_chat_id():
    data = request.get_json(silent=True) or {}
    chat_id = data.get("chat_id")
    if chat_id is None:
        return jsonify({"success": False, "message": "No chat_id provided."})
    monitor.set_chat_id(int(chat_id))
    return jsonify({"success": True, "chat_id": monitor.chat_id})


@app.route("/monitor/test", methods=["POST"])
def monitor_test():
    ok, err = monitor.send_test_alert()
    if ok:
        return jsonify({"success": True, "message": "Test alert sent."})
    return jsonify({"success": False, "message": err or "Unknown error"})


@app.route("/monitor/bot_info")
def monitor_bot_info():
    if not monitor.token:
        return jsonify({"success": False})
    username, err = get_bot_username(monitor.token)
    if err:
        return jsonify({"success": False, "message": err})
    return jsonify({"success": True, "username": username})


@app.route("/system_stats")
def system_stats():
    try:
        import psutil
        cpu_pcts = psutil.cpu_percent(percpu=True)
        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()
        load = os.getloadavg()

        def disk_stats(path):
            try:
                d = psutil.disk_usage(path)
                return {"total": d.total, "used": d.used, "percent": d.percent}
            except Exception:
                return None

        ssd = disk_stats('/')          # OS/system SSD
        hdd = disk_stats('/mnt/data')  # data HDD
        return jsonify({
            "success": True,
            "cpu_cores": cpu_pcts,
            "memory": {"total": mem.total, "used": mem.used, "percent": mem.percent},
            "swap":   {"total": swap.total, "used": swap.used, "percent": swap.percent},
            "ssd":    ssd,
            "hdd":    hdd,
            "load_avg": list(load),
        })
    except ImportError:
        return jsonify({"success": False, "message": "psutil not installed"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


def is_dream_daq_running():
    """
    Checks tmux session 'daq_control' and returns True if Dream DAQ is running.

    Running = "Received: Dream DAQ starting" appears in recent output
              AND
              "Dream Subrun complete." has NOT appeared.
    """
    try:
        # Increase the buffer slightly to ensure we don't miss the transition
        output = subprocess.check_output(
            ["tmux", "capture-pane", "-pS", "-20", "-t", "daq_control:0.0"],
            text=True
        )
    except subprocess.CalledProcessError:
        return False

    lines = output.splitlines()

    # We iterate backwards (from most recent to oldest)
    for line in reversed(lines):
        if "Received: Dream DAQ starting" in line:
            return True
        if "Dream Subrun complete." in line:
            return False

    return False  # Neither found in recent history
    # try:
    #     # Grab last ~10 lines of the pane
    #     output = subprocess.check_output(
    #         ["tmux", "capture-pane", "-pS", "-10", "-t", "daq_control:0.0"],
    #         text=True
    #     )
    # except subprocess.CalledProcessError:
    #     # If tmux session doesn't exist or some error occurs
    #     return False
    #
    # # Normalize
    # lines = output.splitlines()
    #
    # # State checks
    # saw_start = any("Received: Dream DAQ starting" in line for line in lines)
    # saw_complete = any("Dream Subrun complete." in line for line in lines)
    #
    # # Running only if started AND not complete
    # return saw_start and not saw_complete


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5001)
