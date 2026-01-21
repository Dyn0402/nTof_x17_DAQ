#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on September 29 3:45 PM 2025
Created in PyCharm
Created as Cosmic_Bench_DAQ_Control/app.py

@author: Dylan Neff, Dylan
"""

import os
import sys
import subprocess
import pty
import select
import threading
import time
import json
import pandas as pd
from flask import Flask, render_template, jsonify, request, send_from_directory, abort
from flask_socketio import SocketIO, emit

from daq_status import *

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # Add parent dir to path
from run_config_beam import Config
from get_run_events import get_total_events_for_run

BASE_DIR = "/home/dylan/PycharmProjects/nTof_x17_DAQ"
CONFIG_TEMPLATE_DIR = f"{BASE_DIR}/config/json_templates"
CONFIG_RUN_DIR = f"{BASE_DIR}/config/json_run_configs"
CONFIG_PY_PATH = f"{BASE_DIR}/run_config_beam.py"
BASH_DIR = f"{BASE_DIR}/bash_scripts"
ANALYSIS_DIR = "/media/dylan/data/x17"
RUN_DIR = "/media/dylan/data/x17/dream_run_test"
HV_TAIL = 1000  # number of most recent rows to show


app = Flask(__name__)
socketio = SocketIO(app)

TMUX_SESSIONS = ["daq_control", "dream_daq", "hv_control"]
sessions = {}

@app.route("/")
def index():
    configs = [f for f in os.listdir(CONFIG_RUN_DIR) if f.endswith(".json")]
    return render_template("index.html", screens=TMUX_SESSIONS, run_configs=configs)


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
        elif s == "trigger_veto_control":
            info = get_trigger_veto_control_status()
        elif s == "trigger_gen_control":
            info = get_trigger_gen_control_status()
        elif s == "banco_tracker":
            info = get_banco_tracker_status()
        elif s == "desync_monitor":
            info = get_desync_monitor_status()
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
            subprocess.Popen([f"{BASH_DIR}/stop_sub_run.sh"])
            return jsonify({"success": True, "message": "Stopping Sub-Run"})
        else:
            return jsonify({"success": False, "message": "Dream DAQ is not running"}), 400
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/stop_run", methods=["POST"])
def stop_run():
    try:
        if is_dream_daq_running():
            subprocess.Popen([f"{BASH_DIR}/stop_run.sh"])
            return jsonify({"success": True, "message": "DAQ Running, Stopping Run"})
        else:
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

        data = request.get_json()
        new_position = data.get("banco_position")

        if new_position is None:
            return jsonify({"success": False, "message": "Missing banco_position"}), 400

        config_file = CONFIG_PY_PATH

        # Read file lines
        with open(config_file, "r") as f:
            lines = f.readlines()

        # Replace banco_moveable_y_position value
        updated = False
        for i, line in enumerate(lines):
            if "'banco_moveable_y_position'" in line:
                # Replace the value in this line (handle comments cleanly)
                prefix = line.split(":")[0]
                comment = ""
                if "#" in line:
                    parts = line.split("#", 1)
                    prefix = parts[0]
                    comment = "#" + parts[1].rstrip("\n")
                lines[i] = f"            'banco_moveable_y_position': {float(new_position) / 10},  {comment}\n"
                updated = True
                break

        if not updated:
            return jsonify({"success": False, "message": "banco_moveable_y_position not found"}), 404

        # Write updated file
        with open(config_file, "w") as f:
            f.writelines(lines)

        return jsonify({"success": True, "message": f"Banco position updated to {new_position}"})

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


@app.route("/json_templates")
def list_json_templates():
    try:
        files = [f for f in os.listdir(CONFIG_TEMPLATE_DIR) if f.endswith(".json")]
        return jsonify({"success": True, "templates": files})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/json_templates/<template_name>")
def load_json_template(template_name):
    try:
        path = os.path.join(CONFIG_TEMPLATE_DIR, template_name)
        if not os.path.isfile(path):
            return jsonify({"success": False, "message": "File not found"}), 404
        with open(path, "r") as f:
            data = json.load(f)
        return jsonify({"success": True, "content": data})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/save_run_config", methods=["POST"])
def save_run_config():
    try:
        data = request.get_json()
        content = data.get("content")

        if not isinstance(content, dict):
            return jsonify({"success": False, "message": "Invalid JSON content"}), 400

        run_name = content.get("run_name")
        if not run_name:
            return jsonify({"success": False, "message": "Missing 'run_name' field in config"}), 400

        os.makedirs(CONFIG_RUN_DIR, exist_ok=True)

        filename = f"{run_name}.json"
        path = os.path.join(CONFIG_RUN_DIR, filename)
        with open(path, "w") as f:
            json.dump(content, f, indent=4)

        return jsonify({"success": True, "message": f"Run config saved as {filename}"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route('/list_json_configs')
def list_json_configs():
    folder = "./config/json_run_configs"
    files = [f for f in os.listdir(folder) if f.endswith('.json')]
    return jsonify(files)

@app.route('/load_json_config/<filename>')
def load_json_config(filename):
    path = os.path.join("./config/json_run_configs", filename)
    with open(path) as f:
        return jsonify(json.load(f))

@app.route('/save_json_config', methods=['POST'])
def save_json_config():
    data = request.get_json()
    run_name = data.get('run_name', 'unnamed')
    path = f"./config/json_run_configs/{run_name}.json"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f, indent=4)
    return f"Saved to {path}"

@app.route('/save_json_template', methods=['POST'])
def save_json_template():
    req = request.get_json()
    name = req.get('name')
    data = req.get('data')
    path = f"./config/json_templates/{name}"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f, indent=4)
    return f"Template saved to {path}"


@socketio.on("start")
def start(data):
    name = data.get("name")
    if name in sessions:
        return  # already attached

    pid, fd = pty.fork()
    if pid == 0:
        # Child: attach to tmux session
        os.execvp("tmux", ["tmux", "attach-session", "-t", name])
    else:
        # Parent: keep FD for reading/writing
        sessions[name] = fd

        def read_fd(fd, session_name):
            while True:
                try:
                    r, _, _ = select.select([fd], [], [], 0.1)
                    if fd in r:
                        output = os.read(fd, 1024).decode(errors="ignore")
                        socketio.emit(f"output-{session_name}", output)
                except OSError:
                    break

        threading.Thread(target=read_fd, args=(fd, name), daemon=True).start()


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

    pngs = [f for f in os.listdir(directory) if f.lower().endswith(".png")]
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


@app.route("/get_config_py", methods=['GET'])
def get_config_py():
    try:
        # config = Config()
        # run_name = config.run_name
        # banco_position = config.bench_geometry['banco_moveable_y_position']
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
        banco_position = config_data.get("banco_position", "Unknown")

        # If banco_possition is a number, multiply by 10 to convert back to motor units
        try:
            banco_position = float(banco_position) * 10
        except ValueError:
            pass

        return jsonify({
            "success": True,
            "run_name": run_name,
            "banco_position": banco_position
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/get_run_events", methods=['GET'])
def get_run_events():
    try:
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
        run_number = int(run_name.replace("run_", ""))
        total_events, subrun_details = get_total_events_for_run(
            run_dir=RUN_DIR,
            run_number=run_number
        )
        return jsonify({
            "success": True,
            "total_events": total_events,
            "subrun_details": subrun_details
        })
    except Exception as e:
        return jsonify({"success": False, "message": f"Error getting run name: {str(e)}"}), 500


def is_dream_daq_running():
    """
    Checks tmux session 'daq_control' and returns True if Dream DAQ is running.

    Running = "Received: Dream DAQ starting" appears in recent output
              AND
              "Dream Subrun complete." has NOT appeared.
    """
    try:
        # Grab last ~10 lines of the pane
        output = subprocess.check_output(
            ["tmux", "capture-pane", "-pS", "-10", "-t", "daq_control:0.0"],
            text=True
        )
    except subprocess.CalledProcessError:
        # If tmux session doesn't exist or some error occurs
        return False

    # Normalize
    lines = output.splitlines()

    # State checks
    saw_start = any("Received: Dream DAQ starting" in line for line in lines)
    saw_complete = any("Dream Subrun complete." in line for line in lines)

    # Running only if started AND not complete
    return saw_start and not saw_complete


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5001)
