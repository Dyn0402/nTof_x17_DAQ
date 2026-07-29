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
import hmac
import uuid
import ipaddress
from datetime import datetime, timedelta
import pandas as pd
from urllib.parse import quote
from flask import Flask, render_template, jsonify, request, send_from_directory, abort, session
from flask_socketio import SocketIO, emit

from daq_status import (get_dream_daq_status, get_hv_control_status,
                        get_daq_control_status, get_processor_watcher_status,
                        get_qa_watcher_status, get_backup_watcher_status,
                        get_pedestal_watcher_status,
                        get_gas_watcher_status, get_he3_pressure_watcher_status,
                        get_beam_watcher_status, get_n1081b_timetag_watcher_status,
                        get_stream1_watcher_status, get_stats_page_watcher_status,
                        get_n1081b_access_status, N1081B_ACCESS_DIR)

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # Add parent dir to path
_N1081B_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "n1081b")
if _N1081B_DIR not in sys.path:
    sys.path.append(_N1081B_DIR)  # so the N1081B design-model module imports cleanly
from run_config_beam import Config, BASE_DATA_DIR
from get_run_events import get_total_events_for_run
from monitor import DaqMonitor, fetch_chat_id, get_bot_username
from gas_mixer_control.flow_controller import GAS_LOG_DIR, GAS_STATE_PATH, GAS_COMMAND_PATH
from he3_pressure_reader.he3_pressure_controller import (HE3_PRESSURE_LOG_DIR,
                                                         HE3_PRESSURE_STATE_PATH,
                                                         HE3_PRESSURE_CONFIG_PATH,
                                                         PRESS_UNIT, clamp_period,
                                                         MIN_SAMPLE_PERIOD_S,
                                                         MAX_SAMPLE_PERIOD_S)
from beam_monitor.beam_intensity_controller import (BEAM_LOG_DIR, BEAM_STATE_PATH,
                                                    NXCALS_PYTHON, BEAM_UNIT,
                                                    PULSE_THRESHOLD_E10)
# TEMPORARY (2026-07-23) — SPS spill test tab. Remove with the sps_monitor package.
from sps_monitor.sps_spill_controller import (SPS_LOG_DIR, SPS_STATE_PATH, SPS_UNIT,
                                              EXTRACTED_DEST, H4_TAX_VAR,
                                              H4_TAX_OPEN_MAX, H4_TAX_BLOCK_MIN,
                                              tax_blocked_intervals)
from n1081b.timetag_watcher_controller import (N1081B_TT_LOG_DIR, N1081B_TT_STATE_PATH,
                                               N1081B_TT_CONFIG_PATH)
from stream1_monitor.stream1_size_controller import (STREAM1_LOG_DIR, STREAM1_STATE_PATH,
                                                     STREAM1_CONFIG_PATH,
                                                     STREAM1_NOMINAL_PATH,
                                                     STREAM1_COMMAND_PATH)
from system_monitor.system_stats_controller import SYSTEM_STATS_LOG_DIR
import space_manager

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
# Post-sub-run pause flag; presence tells daq_control to wait at the next sub-run
# boundary. Path must match PAUSE_FLAG in daq_control.py (repo root).
PAUSE_FLAG_PATH = f"{BASE_DIR}/.pause_run"
# N1081B trigger-diagram tab: the live board state is read (no extra board
# traffic) from the newest per-sub-run n1081b_config.json daq_control drops in each
# run dir, falling back to the newest manual snapshots/dump_*.json when idle. The
# scan watcher publishes the currently-applied scan config here.
N1081B_SNAP_DIR = f"{BASE_DIR}/n1081b/snapshots"
N1081B_SCAN_ACTIVE_PATH = f"{BASE_DIR}/config/n1081b_scan_active.json"
# ANALYSIS_DIR = "/media/dylan/data/x17"
# RUN_DIR = "/media/dylan/data/x17/dream_run_test"
# Online QA Viewer tab serves the per-run QA plots detector_qa.py writes under
# analysis/online_qa/<run>/<subrun>/<detector>/.
ANALYSIS_DIR = f'{BASE_DATA_DIR}analysis/online_qa'
RUN_DIR = f'{BASE_DATA_DIR}runs'
# Analysis Browser tab browses the whole analysis tree (manual/offline analyses
# plus the online_qa/ subtree).
GENERAL_ANALYSIS_DIR = f'{BASE_DATA_DIR}analysis'
HV_TAIL = 1000  # number of most recent rows to show

LOG_DIR = f"{BASE_DIR}/logs"
LOG_FILE = f"{LOG_DIR}/daq_events.log"

MONITOR_CONFIG_PATH = f"{BASE_DIR}/config/monitor_config.json"
monitor = DaqMonitor(MONITOR_CONFIG_PATH)

# Nominal targets + tolerances for the Shift Overview page (editable JSON).
SHIFT_EXPECTED_PATH = f"{BASE_DIR}/config/shift_expected.json"

# Gas mixer: the serial bus is owned by a SEPARATE process (gas_watcher.py / the
# gas_watcher tmux session), not by Flask. Flask reads the watcher's published state
# from GAS_STATE_PATH and sends setpoint commands by writing GAS_COMMAND_PATH; the
# watcher applies them within one poll (~2 s). This keeps a single owner on the bus
# and lets logging survive Flask restarts. See gas_mixer_control/README.md.


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

# ===========================================================================
# View-only access control
# ---------------------------------------------------------------------------
# The GUI is served on 0.0.0.0:5001, so anyone on the network can load it. To
# let people WATCH without being able to click control actions, every state-
# changing request (all control routes are POSTs) is gated. A caller may
# control if EITHER its IP is whitelisted (silent, no login) OR it has
# unlocked this browser session with the control password. Everyone else gets
# a live, read-only view (all GET routes stay open) and a 403 on any control
# attempt.
#
# Real config lives in access_config.py (gitignored); see
# access_config.example.py. If that file is missing we fail SAFE: control is
# allowed only from localhost until you configure it.
# ===========================================================================
try:
    from access_config import (WHITELIST_IPS, WHITELIST_CIDRS,
                                CONTROL_PASSWORD, SECRET_KEY)
except Exception as _access_err:
    print(f"[access] access_config.py not loaded ({_access_err}); control "
          f"restricted to localhost until configured. See access_config.example.py.")
    WHITELIST_IPS = ["127.0.0.1", "::1"]
    WHITELIST_CIDRS = []
    CONTROL_PASSWORD = ""          # empty string → password unlock disabled
    SECRET_KEY = "dev-only-change-me"

app.secret_key = SECRET_KEY
app.permanent_session_lifetime = timedelta(days=30)

# Endpoints reachable even in view-only mode: the auth handshake and static
# assets. Any other endpoint using a non-GET method is treated as control.
# gas_zero and emergency_stop are safety actions — allowed even in view-only mode.
# space_job_check and space_preflight are POSTs only because they start a job /
# take a selection body: both are strictly read-only (a scan and a dry run), and
# the Disk Space tab is useless to a viewer without them.
_AUTH_EXEMPT_ENDPOINTS = {"control_login", "control_logout", "auth_status", "static",
                          "gas_zero", "emergency_stop",
                          "space_job_check", "space_preflight"}


def _client_ip():
    return request.remote_addr or ""


def _ip_whitelisted(ip):
    """True if ip is in WHITELIST_IPS or falls inside any WHITELIST_CIDRS range."""
    if ip in WHITELIST_IPS:
        return True
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for cidr in WHITELIST_CIDRS:
        try:
            if addr in ipaddress.ip_network(cidr, strict=False):
                return True
        except ValueError:
            continue
    return False


def control_via():
    """How the current request is allowed to control: 'ip', 'session', or None."""
    if _ip_whitelisted(_client_ip()):
        return "ip"
    if session.get("control_authed"):
        return "session"
    return None


def is_authorized():
    return control_via() is not None


@app.context_processor
def _inject_auth():
    """Expose auth state to every template (base.html topbar, index controls)."""
    via = control_via()
    return {"authorized": via is not None, "auth_via": via,
            "password_enabled": bool(CONTROL_PASSWORD)}


@app.before_request
def _gate_control_actions():
    # SocketIO transport (currently unused) must never be gated.
    if request.path.startswith("/socket.io"):
        return
    if request.endpoint in _AUTH_EXEMPT_ENDPOINTS:
        return
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    if is_authorized():
        return
    log_event("DENIED", "view_only", remote_addr=_client_ip(), path=request.path)
    return jsonify({"success": False, "view_only": True,
                    "message": "View-only mode — control is locked. Unlock "
                               "with the control password to make changes."}), 403


@app.route("/auth/login", methods=["POST"])
def control_login():
    if not CONTROL_PASSWORD:
        return jsonify({"success": False,
                        "message": "Password unlock is disabled on this server."}), 400
    data = request.get_json(silent=True) or {}
    if hmac.compare_digest(str(data.get("password", "")), str(CONTROL_PASSWORD)):
        session.permanent = True
        session["control_authed"] = True
        log_event("AUTH_UNLOCK", "flask_login", remote_addr=_client_ip())
        return jsonify({"success": True})
    log_event("AUTH_FAIL", "flask_login", remote_addr=_client_ip())
    return jsonify({"success": False, "message": "Incorrect password."}), 401


@app.route("/auth/logout", methods=["POST"])
def control_logout():
    session.pop("control_authed", None)
    log_event("AUTH_LOCK", "flask_login", remote_addr=_client_ip())
    return jsonify({"success": True})


@app.route("/auth/status")
def auth_status():
    via = control_via()
    return jsonify({"authorized": via is not None, "via": via,
                    "ip": _client_ip(), "password_enabled": bool(CONTROL_PASSWORD)})


TMUX_SESSIONS = ["daq_control", "dream_daq", "hv_control", "processor_watcher", "qa_watcher", "backup_watcher",
                 "pedestal_watcher", "gas_watcher", "he3_pressure_watcher",
                 "beam_watcher", "stream1_watcher", "n1081b_timetag_watcher", "stats_page_watcher"]
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


def _status_field(info, label):
    """Value of a named field in a get_*_status() result, or None."""
    for f in (info or {}).get("fields", []):
        if f.get("label") == label:
            return f.get("value")
    return None


def _hms_to_min(s):
    """'0h 1m 47s' -> minutes (float). Missing/garbage -> 0.0."""
    if not s:
        return 0.0
    m = re.search(r'(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?\s*(?:(\d+)\s*s)?', s)
    if not m:
        return 0.0
    h, mm, ss = (int(g) if g else 0 for g in m.groups())
    return h * 60 + mm + ss / 60.0


def _fmt_min(minutes):
    """Minutes -> '50m' or '3h45m'."""
    t = int(round(minutes))
    h, m = divmod(t, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m"


# On-disk event total only changes when a subrun completes, so cache it briefly:
# /status is polled every 1s and get_total_events_for_run walks every subrun's logs.
_events_cache = {"run": None, "t": 0.0, "total": 0}


def _ondisk_run_events(run_name):
    now = time.time()
    c = _events_cache
    if c["run"] == run_name and now - c["t"] < 4.0:
        return c["total"]
    try:
        total, _ = get_total_events_for_run(run_dir=RUN_DIR, run_name=run_name)
    except Exception:
        total = 0
    c.update(run=run_name, t=now, total=total)
    return total


def _live_events_from(dream_info):
    """Live nb_of_events from an already-fetched dream_daq status (no re-capture)."""
    if (dream_info or {}).get("status") != "RUNNING":
        return 0
    try:
        return int(str(_status_field(dream_info, "Subrun Events")).strip())
    except (TypeError, ValueError):
        return 0


def _run_progress(daq_info, dream_info):
    """{subrun_idx, subrun_total, elapsed_min, total_min} for the current run, from
    its run_config.json sub_runs + the live subrun name/elapsed. {} if unavailable.
    Elapsed = completed subruns' planned time + the current subrun's elapsed (capped
    at its planned length), so it pairs with the subrun index and never exceeds total."""
    run_name = _current_run_cache
    if not run_name:
        return {}
    try:
        with open(os.path.join(RUN_DIR, run_name, "run_config.json")) as f:
            subs = json.load(f).get("sub_runs", [])
    except Exception:
        return {}
    if not subs:
        return {}
    names = [s.get("sub_run_name") for s in subs]
    durs  = [float(s.get("run_time", 0) or 0) for s in subs]  # minutes
    prog  = {"subrun_total": len(subs), "total_min": sum(durs)}
    subrun = _status_field(daq_info, "Subrun")
    if subrun in names:
        i = names.index(subrun)
        cur = min(_hms_to_min(_status_field(dream_info, "Run Time")), durs[i])
        prog["subrun_idx"]  = i + 1
        prog["elapsed_min"] = sum(durs[:i]) + cur
    return prog


@app.route("/status")
def status_all():
    statuses = []
    by_name = {}

    for s in TMUX_SESSIONS:
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
        elif s == "gas_watcher":
            info = get_gas_watcher_status()
        elif s == "he3_pressure_watcher":
            info = get_he3_pressure_watcher_status()
        elif s == "beam_watcher":
            info = get_beam_watcher_status()
        elif s == "stream1_watcher":
            info = get_stream1_watcher_status()
        elif s == "n1081b_timetag_watcher":
            info = get_n1081b_timetag_watcher_status()
        elif s == "stats_page_watcher":
            info = get_stats_page_watcher_status()
        else:
            info = {"status": "READY", "color": "secondary", "fields": []}

        entry = {"name": s, **info}
        statuses.append(entry)
        by_name[s] = entry

    # Enrich the dream_daq card with run progress (subrun x/N, elapsed/total time)
    # and the live "Events this run" total, so both refresh with the 1s /status poll
    # (instead of a separate slower timer).
    dream = by_name.get("dream_daq")
    if dream is not None:
        prog = _run_progress(by_name.get("daq_control"), dream)
        if prog.get("subrun_idx"):
            dream.setdefault("fields", []).append(
                {"label": "Subrun", "value": f'{prog["subrun_idx"]}/{prog["subrun_total"]}'})
            dream["fields"].append(
                {"label": "Progress",
                 "value": f'{_fmt_min(prog["elapsed_min"])} / {_fmt_min(prog["total_min"])}'})
        elif prog.get("subrun_total"):
            dream.setdefault("fields", []).append(
                {"label": "Subrun", "value": f'–/{prog["subrun_total"]}'})
        if _current_run_cache:
            dream["run_events"] = _ondisk_run_events(_current_run_cache) + _live_events_from(dream)

    # Surface whether a post-sub-run pause is armed so the button reflects it.
    daq = by_name.get("daq_control")
    if daq is not None:
        daq["pause_armed"] = os.path.exists(PAUSE_FLAG_PATH)

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
        # Seed "Current run" immediately, as the retired /run_config_py path used to.
        # Without this the GUI shows the previous run until daq_control's log catches up.
        try:
            with open(config_path) as f:
                _save_current_run(json.load(f).get("run_name", "Unknown"))
        except Exception:  # noqa: BLE001
            pass
        return jsonify({"success": True, "message": f"Run started with {config_file}"})
    else:
        return jsonify({"success": False, "message": f"Error: {result.stderr}"}), 500

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
        # Always stop the WHOLE run. stop_run.sh drops the .stop_run flag that
        # daq_control honors at its next checkpoint (before the next sub-run, or
        # before (re)starting the DAQ), so the run ends and HV powers off even when
        # we're mid HV-ramp / file-copy / between sub-runs — states where the DAQ
        # isn't "running". stop_dream.sh safely no-ops if RunCtrl isn't running.
        # (Previously this fell back to stop_sub_run.sh when the DAQ wasn't actively
        # taking data, which only stopped the current sub-run and let the run go on.)
        dream_running = is_dream_daq_running()
        log_event('STOP_RUN', 'flask_button', remote_addr=request.remote_addr,
                  dream_running=dream_running)
        subprocess.Popen([f"{BASH_DIR}/stop_run.sh"])
        return jsonify({"success": True, "message": "Stopping Run"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/toggle_pause_run", methods=["POST"])
def toggle_pause_run():
    """Arm/clear the post-sub-run pause. Presence of the flag file tells daq_control
    to wait at the next sub-run boundary; removing it resumes (one-shot)."""
    try:
        if os.path.exists(PAUSE_FLAG_PATH):
            os.remove(PAUSE_FLAG_PATH)
            log_event('RESUME_RUN', 'flask_button', remote_addr=request.remote_addr)
            return jsonify({"success": True, "paused": False,
                            "message": "Pause cleared — run continues"})
        else:
            open(PAUSE_FLAG_PATH, "w").close()
            log_event('PAUSE_RUN', 'flask_button', remote_addr=request.remote_addr)
            return jsonify({"success": True, "paused": True,
                            "message": "Will pause after the current sub-run"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/restart_all", methods=["POST"])
def restart_all():
    try:
        subprocess.Popen([f"{BASH_DIR}/restart_daq_tmux_processes.sh"])
        return jsonify({"success": True, "message": "All processes restarted"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/restart_flask", methods=["POST"])
def restart_flask():
    """Restart ONLY the Flask GUI server (tmux `flask_server`), leaving the DAQ,
    HV, and watcher sessions running. The restart runs detached (screen) since it
    kills the process serving this request; the GUI drops for ~3 s and returns."""
    try:
        subprocess.Popen([f"{BASH_DIR}/restart_flask.sh"])
        return jsonify({"success": True, "message": "GUI restarting — reconnecting in a few seconds…"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/run/prepare", methods=["POST"])
def run_prepare():
    """Allocate the next run number, regenerate the config AT that number, report the name.

    This is one atomic step on purpose. The old flow was three requests —
    /update_run_config_py fired `iterate_run_num.py` with Popen + sleep(0.2), /get_config_py
    read the name back, /run_config_py regenerated and started — so the number could change
    between the popup and the launch, and the confirmation could name a run that never ran.
    Worse, iterate_run_num.py achieved the increment by REWRITING the `self.run_name` line
    in the tracked run_config_beam.py source, which only ever worked for the base config
    (every run_configs/ generator overrides run_name) and left the repo dirty every start.

    Now: run_num.allocate() picks the number (the same allocator switch_mode --go uses), and
    the generator is run with RUN_NUM in its environment, so no source file is touched. The
    caller gets back the run name that WILL be used and the exact config to launch.

    ⚠ Allocation is eager — cancelling the confirmation burns a run number. That is the
    safe direction: a gap costs nothing, a reused number can put two runs in one EOS
    directory. See run_num.py.
    """
    try:
        sys.path.insert(0, BASE_DIR)
        import run_num as _rn
        import importlib
        _rn = importlib.reload(_rn)

        # Don't prepare a run while something else is already starting one. The allocator
        # itself is race-safe, so this is not about the NUMBER — it is about two runs being
        # launched at once. switch_mode --go stops the live run and starts a new one; a
        # Start Run landing in that window would fight it.
        try:
            mw = _mode_mod()
            if mw.sm.read_changeover_lock():
                return jsonify({"success": False,
                                "message": "A beam/cosmics changeover is in progress — "
                                           "wait for it to finish."}), 409
            if mw.sm.live_run_pids():
                return jsonify({"success": False,
                                "message": "A run is already running. Stop it first."}), 409
        except Exception:  # noqa: BLE001
            pass  # advisory only — never block a start because the check itself broke

        n = _rn.allocate()
        env = {**os.environ, "RUN_NUM": str(n)}
        gen = subprocess.run([VENV_PY, f"{BASE_DIR}/run_config_beam.py"],
                             cwd=BASE_DIR, env=env, capture_output=True, text=True,
                             timeout=180)
        cfg_file = "run_config_beam.json"
        cfg_path = os.path.join(CONFIG_RUN_DIR, cfg_file)
        if gen.returncode != 0 or not os.path.exists(cfg_path):
            return jsonify({"success": False,
                            "message": f"Config generation failed for run_{n}: "
                                       f"{(gen.stderr or gen.stdout or '')[-400:]}"}), 500

        # Trust the generated file, not our own arithmetic.
        with open(cfg_path) as f:
            written = json.load(f).get("run_name")
        if written != _rn.run_name(n):
            return jsonify({"success": False,
                            "message": f"Generated config says {written!r} but we "
                                       f"allocated {_rn.run_name(n)!r} — refusing."}), 500

        log_event("RUN_PREPARE", "run_control", run_name=written, remote_addr=_client_ip())
        return jsonify({"success": True, "run_name": written, "config": cfg_file,
                        "message": f"Prepared {written}"})
    except Exception as e:  # noqa: BLE001
        # AllocationBusy lands here: the lock was held too long. Failing to start is
        # recoverable; a duplicate run number is not.
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/run/next_num")
def run_next_num():
    """What the next run number would be, without claiming it. Read-only, for display."""
    try:
        sys.path.insert(0, BASE_DIR)
        import run_num as _rn
        import importlib
        _rn = importlib.reload(_rn)
        return jsonify({"success": True, "run_num": _rn.peek(),
                        "run_name": _rn.run_name(_rn.peek())})
    except Exception as e:  # noqa: BLE001
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/update_run_config_py", methods=['POST'])
def update_run_config_py():
    """DEPRECATED — superseded by /run/prepare.

    It used to shell out to iterate_run_num.py, which rewrote the run_name line in the
    tracked run_config_beam.py source. That is gone: nothing should mutate a source file to
    pick a run number. Kept only so a stale browser tab gets a clear error instead of
    silently starting a run with the wrong number.
    """
    return jsonify({"success": False,
                    "message": "This endpoint is retired — reload the page. Run numbers "
                               "are now allocated by /run/prepare (run_num.py)."}), 410

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


@app.route("/start_gas_watcher", methods=["POST"])
def start_gas_watcher():
    """Start the gas-mixer watcher (sole owner of the FLOW-BUS: reads, logs, and applies
    setpoints). Normally auto-started at boot by start_servers.sh; this button is for
    restarting it from the GUI."""
    try:
        subprocess.run(["tmux", "kill-session", "-t", "gas_watcher"], capture_output=True)
        # sys.executable = flask's venv python, so the propar lib resolves in the new
        # tmux session (a bare "python" would drop the venv).
        subprocess.Popen([
            "tmux", "new-session", "-d", "-s", "gas_watcher",
            sys.executable, f"{BASE_DIR}/gas_watcher.py"
        ])
        return jsonify({"success": True, "message": "Gas watcher started"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/stop_gas_watcher", methods=["POST"])
def stop_gas_watcher():
    """Stop the gas watcher. Gas keeps flowing at its current setpoint (the controllers
    hold it in hardware); logging and new setpoint commands pause until it restarts."""
    try:
        subprocess.run(["tmux", "kill-session", "-t", "gas_watcher"], capture_output=True)
        return jsonify({"success": True, "message": "Gas watcher stopped (gas still flowing)"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/start_he3_pressure_watcher", methods=["POST"])
def start_he3_pressure_watcher():
    """Start the 3He target pressure-gauge watcher (sole owner of the Keithley 2000 GPIB
    link: reads, converts, and logs pressure). Normally auto-started at boot by
    start_servers.sh; this button is for restarting it from the GUI."""
    try:
        subprocess.run(["tmux", "kill-session", "-t", "he3_pressure_watcher"], capture_output=True)
        # sys.executable = flask's venv python, so the linux-gpib binding resolves in the
        # new tmux session (a bare "python" would drop the venv).
        subprocess.Popen([
            "tmux", "new-session", "-d", "-s", "he3_pressure_watcher",
            sys.executable, f"{BASE_DIR}/he3_pressure_watcher.py"
        ])
        return jsonify({"success": True, "message": "3He pressure watcher started"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/stop_he3_pressure_watcher", methods=["POST"])
def stop_he3_pressure_watcher():
    """Stop the 3He pressure watcher. Pressure logging pauses until it restarts."""
    try:
        subprocess.run(["tmux", "kill-session", "-t", "he3_pressure_watcher"], capture_output=True)
        return jsonify({"success": True, "message": "3He pressure watcher stopped"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/start_beam_watcher", methods=["POST"])
def start_beam_watcher():
    """Start the n_TOF beam-intensity watcher (sole owner of the NXCALS/Spark session:
    pulls F16.BCT372.TOF:INTENSITY, logs it, and publishes beam on/off). Needs a valid
    Kerberos ticket (kinit dneff@CERN.CH — same one as the EOS backup)."""
    try:
        # NOT sys.executable: pytimber + PySpark live in their own venv, not flask's.
        if not os.path.exists(NXCALS_PYTHON):
            return jsonify({"success": False,
                            "message": f"NXCALS venv missing: {NXCALS_PYTHON} "
                                       f"(see beam_monitor/README.md)"}), 500
        subprocess.run(["tmux", "kill-session", "-t", "beam_watcher"], capture_output=True)
        subprocess.Popen([
            "tmux", "new-session", "-d", "-s", "beam_watcher",
            NXCALS_PYTHON, f"{BASE_DIR}/beam_watcher.py"
        ])
        return jsonify({"success": True,
                        "message": "Beam watcher started (first NXCALS query takes ~1 min)"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/stop_beam_watcher", methods=["POST"])
def stop_beam_watcher():
    """Stop the beam watcher. Beam-intensity logging pauses until it restarts."""
    try:
        subprocess.run(["tmux", "kill-session", "-t", "beam_watcher"], capture_output=True)
        return jsonify({"success": True, "message": "Beam watcher stopped"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/start_stream1_watcher", methods=["POST"])
def start_stream1_watcher():
    """Start the n_TOF stream1 file-size watcher (polls EOS listings for raw-file sizes
    and flags reduced-size episodes — the SiPM-wall dropout proxy). Read-only: it owns
    no hardware, so it is safe to restart at any time. Needs a valid Kerberos ticket
    (the keytab-seeded one the EOS backup uses)."""
    try:
        subprocess.run(["tmux", "kill-session", "-t", "stream1_watcher"], capture_output=True)
        subprocess.Popen([
            "tmux", "new-session", "-d", "-s", "stream1_watcher",
            sys.executable, f"{BASE_DIR}/stream1_watcher.py"
        ])
        return jsonify({"success": True, "message": "Stream1 file-size watcher started"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/stop_stream1_watcher", methods=["POST"])
def stop_stream1_watcher():
    """Stop the stream1 file-size watcher. Nothing else is affected — the n_TOF DAQ
    keeps writing; only the size logging/alerting pauses (it backfills on restart)."""
    try:
        subprocess.run(["tmux", "kill-session", "-t", "stream1_watcher"], capture_output=True)
        return jsonify({"success": True, "message": "Stream1 file-size watcher stopped"})
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

def _run_has_hv_data(run_dir, hv_file="hv_monitor.csv"):
    """True if any subrun directory under run_dir has an HV monitor CSV."""
    if not run_dir or not os.path.isdir(run_dir):
        return False
    for sub in os.listdir(run_dir):
        if os.path.isfile(os.path.join(run_dir, sub, hv_file)):
            return True
    return False


def _hv_run_dir(cfg, hv_file="hv_monitor.csv"):
    """Run directory the HV plot should read: normally the config's run_out_dir, but
    when that run has no HV data yet (a run just started, or between runs while the
    config already points at the next one) fall back to the most recent run under
    RUN_DIR that does — so the plot shows the previous run instead of going blank at
    run boundaries. None if nothing has HV data."""
    primary = cfg.get("run_out_dir")
    if _run_has_hv_data(primary, hv_file):
        return primary
    try:
        candidates = sorted((os.path.join(RUN_DIR, d) for d in os.listdir(RUN_DIR)),
                            key=os.path.getmtime, reverse=True)
    except OSError:
        candidates = []
    for d in candidates:
        if _run_has_hv_data(d, hv_file):
            return d
    return primary if (primary and os.path.isdir(primary)) else None


def _current_run_cfg():
    """cfg for the run _current_run_cache points at, read from that run's own
    run_config.json snapshot (written into run_out_dir by daq_control.py at run
    start). This is what the HV Monitor should key off: every run — whether
    started manually or by the beam<->cosmics auto-changeover — gets its own
    freshly-named config file in CONFIG_RUN_DIR (see /run/prepare), so pinning
    the panel to one fixed filename goes stale the moment anything else starts a
    run, and the panel looks "stuck" on whichever run last actually used that
    file. _current_run_cache is already kept in sync from the daq_control log
    regardless of which config started the run, so it doesn't have this problem.
    None if there's no current run yet, or its snapshot hasn't been written."""
    run_name = _current_run_cache
    if not run_name:
        return None
    cfg_path = os.path.join(RUN_DIR, run_name, "run_config.json")
    try:
        with open(cfg_path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _legacy_hv_cfg():
    """Fallback only: resolve cfg from a config/json_run_configs/<file> named by the
    'run' query param, for the (now rare) case _current_run_cfg() has nothing yet,
    e.g. right after a server restart before any run has been seen."""
    run_name = request.args.get("run")
    if not run_name:
        return None
    config_path = os.path.join(CONFIG_RUN_DIR, run_name)
    if not os.path.isfile(config_path):
        return None
    try:
        with open(config_path) as f:
            return json.load(f)
    except Exception:
        return None


@app.route("/get_subruns")
def get_subruns():
    cfg = _current_run_cfg() or _legacy_hv_cfg()
    if cfg is None:
        return jsonify([])

    try:
        run_dir = _hv_run_dir(cfg)
        if not run_dir:
            return jsonify([])

        # Only offer subruns that actually have an HV monitor CSV, so the selector
        # never lands on an empty subrun (what blanks the plot at run boundaries).
        # This replaces the old cfg['sub_runs'] name match, which returned nothing
        # when run_out_dir and sub_runs briefly disagreed during a run transition.
        subruns = [d for d in os.listdir(run_dir)
                   if os.path.isfile(os.path.join(run_dir, d, "hv_monitor.csv"))]
        subruns.sort(key=lambda f: os.path.getmtime(os.path.join(run_dir, f)), reverse=True)
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


def _hv_channel_labels(cfg):
    """{'slot:channel' -> (label, category)} from a run config's detectors[].hv_channels.

    category 'mm' = micromegas (det_type 'mx17'): label = detector letter + capitalized
    electrode, e.g. 'A_Drift', 'A_Resist' (two electrodes worth distinguishing).

    category 'scint' = scintillator PMTs (det_type 'scintillator_PMT', names
    'liquid_<Wall>' / 'plastic_<Wall>_<Side>'): label = 'Wall_Type_Side', e.g. 'A_L'
    for liquid A, 'A_P_R' for plastic A right. Only one electrode (bias) exists so
    it's dropped from the label rather than always appending '_Bias'.

    Anything else falls back to the old generic 'suffix_Electrode' scheme under
    category 'other', grouped with 'mm' in the HV response."""
    labels = {}
    for det in cfg.get("detectors", []):
        name = str(det.get("name", ""))
        det_type = str(det.get("det_type", ""))
        parts = name.split("_")
        hv_channels = det.get("hv_channels") or {}

        base = None
        if det_type == "mx17":
            short = parts[-1] or name
            category = "mm"
        elif det_type == "scintillator_PMT" and parts[:1] == ["liquid"] and len(parts) >= 2:
            category = "scint"
            base = f"{parts[1]}_L"
        elif det_type == "scintillator_PMT" and parts[:1] == ["plastic"] and len(parts) >= 3:
            category = "scint"
            base = f"{parts[1]}_P_{parts[2]}"
        else:
            short = parts[-1] or name
            category = "other"

        for electrode, ch in hv_channels.items():
            try:
                slot, channel = ch
            except (TypeError, ValueError):
                continue
            label = base if base is not None else f"{short}_{str(electrode).title()}"
            labels[f"{slot}:{channel}"] = (label, category)
    return labels


@app.route("/hv_data")
def hv_data():
    try:
        subrun_name = request.args.get("subrun")
        hv_file_name = request.args.get("hv_file", "hv_monitor.csv")

        cfg = _current_run_cfg() or _legacy_hv_cfg()
        if cfg is None:
            return jsonify([])

        # Resolve the same run dir as /get_subruns (with the previous-run fallback),
        # so the subrun the selector offers is found here too.
        output_dir = _hv_run_dir(cfg, hv_file_name)
        if not output_dir:
            return jsonify([])
        hv_csv_path = os.path.join(output_dir, subrun_name, hv_file_name)

        df = pd.read_csv(hv_csv_path)
        df = df.tail(HV_TAIL)

        # Extract timestamps
        time = df["timestamp"].astype(str).tolist()

        # Map "slot:channel" -> (label, category) from the run config's
        # detectors[].hv_channels — see _hv_channel_labels for the naming scheme.
        # Channels absent from the config keep their raw "slot:channel" name and are
        # grouped with the MM plot (category "other").
        chan_label = _hv_channel_labels(cfg)

        voltage_data, current_data = {}, {}
        voltage_scint, current_scint = {}, {}

        # Loop through columns to find slot:channel prefixes
        for col in df.columns:
            if "vmon" in col:
                key = col.replace(" vmon", "")
                label, category = chan_label.get(key, (key, "other"))
                (voltage_scint if category == "scint" else voltage_data)[label] = df[col].tolist()
            elif "imon" in col:
                key = col.replace(" imon", "")
                label, category = chan_label.get(key, (key, "other"))
                (current_scint if category == "scint" else current_data)[label] = df[col].tolist()

        # Sort by label so each detector's traces group together (A_Drift, A_Resist, …)
        voltage_data = dict(sorted(voltage_data.items()))
        current_data = dict(sorted(current_data.items()))
        voltage_scint = dict(sorted(voltage_scint.items()))
        current_scint = dict(sorted(current_scint.items()))

        return jsonify({
            "success": True,
            "time": time,
            "voltage": voltage_data,
            "current": current_data,
            "voltage_scint": voltage_scint,
            "current_scint": current_scint
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


def _live_dream_events():
    """Live per-FEU event count of the in-progress subrun (nb_of_events ≈ the per-FEU
    physics count), captured fresh from the dream_daq pane. Only while RUNNING; 0
    otherwise. The in-progress subrun has no RunCtrl log yet, so get_total_events_for_run()
    excludes it; adding this keeps 'Events this run' live without double-counting —
    once the subrun finishes, status leaves RUNNING and the count appears on disk."""
    return _live_events_from(get_dream_daq_status())


@app.route("/get_run_events", methods=['GET'])
def get_run_events():
    try:
        # Count events for the run daq_control is actually running (not the
        # possibly-edited run_config_beam.py). Falls back to the persisted value.
        run_name = _current_run_cache
        if not run_name:
            return jsonify({"success": True, "total_events": 0,
                            "live_events": 0, "subrun_details": {}})
        total_events, subrun_details = get_total_events_for_run(
            run_dir=RUN_DIR,
            run_name=run_name
        )
        # Add the in-progress subrun's live events (not yet on disk) so the total
        # reflects the live count shown in the dream_daq card.
        live_events = _live_dream_events()
        return jsonify({
            "success": True,
            "total_events": total_events + live_events,
            "live_events": live_events,
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


@app.route("/monitor/rules")
def monitor_rules():
    return jsonify({
        "rules": monitor.list_rules(),
        "default_resend_minutes": monitor.default_resend_minutes,
    })


@app.route("/monitor/rule_toggle", methods=["POST"])
def monitor_rule_toggle():
    data = request.get_json(silent=True) or {}
    name = data.get("name")
    enabled = data.get("enabled")
    if name is None or enabled is None:
        return jsonify({"success": False, "message": "name and enabled required."})
    ok, err = monitor.set_rule_enabled(name, enabled)
    if not ok:
        return jsonify({"success": False, "message": err})
    return jsonify({"success": True, "name": name, "enabled": bool(enabled)})


@app.route("/monitor/rule_resend", methods=["POST"])
def monitor_rule_resend():
    data = request.get_json(silent=True) or {}
    name = data.get("name")
    mode = data.get("mode")
    minutes = data.get("minutes")
    if name is None or mode is None:
        return jsonify({"success": False, "message": "name and mode required."})
    ok, err = monitor.set_rule_resend(name, mode, minutes)
    if not ok:
        return jsonify({"success": False, "message": err})
    return jsonify({"success": True})


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


# Network interfaces and physical disks to report I/O rates for.
# NICs are labelled by interface name; disks map to the SSD/HDD usage bars.
#
# ⚠ Since the 2026-07-22 NIC swap these two names mean the opposite of what they used to:
# enp4s0 is now the 10 GbE AQC113 carrying DREAM/FEU readout (was CERN), and eno1 is now
# the onboard I219-LM carrying CERN (was DREAM). The list itself needs no edit; the
# interpretation of the plots does. See docs/network_upgrade_10g/05_as_built_2026-07-22.md §3
# and KEEP IN SYNC with system_monitor/system_stats_controller.py.
_NET_IFACES = ["enp4s0", "eno1"]
_DISK_DEVS = {"ssd": "sdb", "hdd": "sda"}  # sdb2 -> /, sda4 -> /mnt/data

# Previous I/O counter sample, kept between /system_stats calls to derive rates.
_io_prev = {"t": None, "net": None, "disk": None}


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

        # ---- I/O rates (bytes/sec) derived from the previous sample ----
        now = time.monotonic()
        net_ctr = psutil.net_io_counters(pernic=True)
        disk_ctr = psutil.disk_io_counters(perdisk=True)
        prev = _io_prev
        dt = (now - prev["t"]) if prev["t"] else None

        def rate(cur, prev_val):
            if dt and dt > 0 and prev_val is not None:
                return max(0.0, (cur - prev_val) / dt)
            return 0.0

        net_rates = {}
        for name in _NET_IFACES:
            cur = net_ctr.get(name)
            p = (prev["net"] or {}).get(name)
            if cur:
                net_rates[name] = {
                    "rx": rate(cur.bytes_recv, p.bytes_recv if p else None),
                    "tx": rate(cur.bytes_sent, p.bytes_sent if p else None),
                }
            else:
                net_rates[name] = None

        disk_rates = {}
        for key, dev in _DISK_DEVS.items():
            cur = disk_ctr.get(dev)
            p = (prev["disk"] or {}).get(dev)
            if cur:
                disk_rates[key] = {
                    "read":  rate(cur.read_bytes,  p.read_bytes if p else None),
                    "write": rate(cur.write_bytes, p.write_bytes if p else None),
                }
            else:
                disk_rates[key] = None

        _io_prev["t"] = now
        _io_prev["net"] = net_ctr
        _io_prev["disk"] = disk_ctr

        return jsonify({
            "success": True,
            "cpu_cores": cpu_pcts,
            "memory": {"total": mem.total, "used": mem.used, "percent": mem.percent},
            "swap":   {"total": swap.total, "used": swap.used, "percent": swap.percent},
            "ssd":    ssd,
            "hdd":    hdd,
            "net":    net_rates,
            "disk_io": disk_rates,
            "load_avg": list(load),
        })
    except ImportError:
        return jsonify({"success": False, "message": "psutil not installed"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/system_stats/history")
def system_stats_history():
    """Logged system-resource history from the per-day CSV(s) the system_stats_watcher
    writes, so the Overview plots come up already populated instead of filling in live.
    `minutes` trims to a recent window; the result is downsampled to keep the payload
    light. Net/disk rates are summed across interfaces/devices to match the live plots."""
    import glob
    minutes = request.args.get("minutes", default=30.0, type=float)
    max_points = request.args.get("max_points", default=600, type=int)
    empty = {"success": True, "time": [], "cpu": [], "cpu_avg": [], "mem": [],
             "swap": [], "net_rx": [], "net_tx": [], "disk_r": [], "disk_w": []}
    try:
        files = sorted(glob.glob(os.path.join(SYSTEM_STATS_LOG_DIR, "system_stats_*.csv")))
        if not files:
            return jsonify(empty)
        # Read the last couple of day-files so a window spanning midnight still works.
        df = pd.concat([pd.read_csv(f) for f in files[-2:]], ignore_index=True)
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
        df = df.dropna(subset=["timestamp"])
        if minutes and minutes > 0:
            df = df[df["timestamp"] >= datetime.now() - timedelta(minutes=minutes)]
        if df.empty:
            return jsonify(empty)
        # Downsample by striding so the trace stays light but keeps its shape.
        if len(df) > max_points:
            df = df.iloc[:: (len(df) // max_points) + 1]

        def sum_cols(names):
            """Sum a set of (possibly absent) numeric columns into one series."""
            s = None
            for n in names:
                if n in df.columns:
                    col = pd.to_numeric(df[n], errors="coerce").fillna(0)
                    s = col if s is None else s + col
            return (s if s is not None else pd.Series(0.0, index=df.index)).tolist()

        core_cols = sorted(
            [c for c in df.columns if c.startswith("cpu") and c != "cpu_avg"],
            key=lambda c: int(c[3:]))
        return jsonify({
            "success": True,
            "time": df["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%S").tolist(),
            "cpu": df[core_cols].round(1).values.tolist() if core_cols else [],
            "cpu_avg": df["cpu_avg"].round(1).tolist() if "cpu_avg" in df else [],
            "mem": df["mem_percent"].round(1).tolist() if "mem_percent" in df else [],
            "swap": df["swap_percent"].round(1).tolist() if "swap_percent" in df else [],
            "net_rx": sum_cols([f"net_{i}_rx_bps" for i in _NET_IFACES]),
            "net_tx": sum_cols([f"net_{i}_tx_bps" for i in _NET_IFACES]),
            "disk_r": sum_cols([f"disk_{k}_read_bps" for k in _DISK_DEVS]),
            "disk_w": sum_cols([f"disk_{k}_write_bps" for k in _DISK_DEVS]),
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


# --- Gas mixer (Bronkhorst MFC) control ---
# The serial bus is owned by the separate gas_watcher process (see the note by the
# imports). Flask reads the watcher's published state and sends commands via files;
# it never touches the bus. All flows in ln/h; isobutane is a % of the total mixture.

def _gas_read_state():
    """The watcher's latest published state, or a disconnected stub if it isn't
    running yet / hasn't written the file."""
    try:
        with open(GAS_STATE_PATH) as f:
            return json.load(f)
    except Exception:
        return {"connected": False, "last_error": "gas watcher not running"}


def _gas_send_command(cmd):
    """Write a setpoint command for the watcher and wait briefly for its ack (the
    watcher records the result back into the state file, keyed by a unique id). Returns
    (result_dict, http_code). Falls back to 'queued' if the watcher doesn't confirm."""
    cid = time.time()
    try:
        with open(GAS_COMMAND_PATH, "w") as f:
            json.dump({**cmd, "id": cid}, f)
    except Exception as e:
        return {"success": False, "message": f"could not queue command: {e}"}, 500
    # The watcher applies within one poll (~2 s); poll the state file for the matching ack.
    deadline = time.time() + 3.0
    while time.time() < deadline:
        lc = _gas_read_state().get("last_command")
        if lc and lc.get("id") == cid:
            return lc, (200 if lc.get("success") else 500)
        time.sleep(0.15)
    return ({"success": True, "queued": True,
             "message": "queued — gas_watcher did not confirm (is it running?)"}, 202)


@app.route("/gas/status")
def gas_status():
    """Latest readback published by the gas_watcher process. All flows in ln/h."""
    return jsonify(_gas_read_state())


@app.route("/gas/apply", methods=["POST"])
def gas_apply():
    """Command a mixture: argon flow (ln/h) + isobutane percent of total."""
    data = request.get_json(silent=True) or {}
    try:
        argon_lnh = float(data.get("argon_lnh"))
        iso_percent = float(data.get("iso_percent"))
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "argon_lnh and iso_percent required"}), 400
    if argon_lnh < 0 or not (0 <= iso_percent < 100):
        return jsonify({"success": False, "message": "argon_lnh >= 0 and 0 <= iso_percent < 100"}), 400

    result, code = _gas_send_command({"cmd": "apply", "argon_lnh": argon_lnh,
                                      "iso_percent": iso_percent})
    log_event('GAS_SET', 'flask_button', remote_addr=request.remote_addr,
              argon_lnh=argon_lnh, iso_percent=iso_percent, ok=result.get("success"))
    return jsonify(result), code


@app.route("/gas/zero", methods=["POST"])
def gas_zero():
    """Emergency stop: drive both controllers to zero flow."""
    result, code = _gas_send_command({"cmd": "zero"})
    log_event('GAS_ZERO', 'flask_button', remote_addr=request.remote_addr,
              ok=result.get("success"))
    return jsonify(result), code


@app.route("/gas/history")
def gas_history():
    """Logged flow history from the per-day CSV(s) for the plot. `hours` trims to a
    recent window; the result is downsampled to keep the payload light. Reads the CSVs
    the gas_watcher writes, so this is real persisted history."""
    import glob
    hours = request.args.get("hours", default=6.0, type=float)
    max_points = request.args.get("max_points", default=1500, type=int)
    try:
        files = sorted(glob.glob(os.path.join(GAS_LOG_DIR, "gas_flow_*.csv")))
        if not files:
            return jsonify({"success": True, "time": [], "argon_flow": [],
                            "iso_flow": [], "total_flow": [], "iso_pct": []})
        # Read the last couple of day-files so a window spanning midnight still works.
        df = pd.concat([pd.read_csv(f) for f in files[-2:]], ignore_index=True)
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
        df = df.dropna(subset=["timestamp"])
        if hours and hours > 0:
            df = df[df["timestamp"] >= datetime.now() - timedelta(hours=hours)]
        # Downsample by striding so the trace stays light but keeps its shape.
        if len(df) > max_points:
            df = df.iloc[:: (len(df) // max_points) + 1]
        return jsonify({
            "success": True,
            "time": df["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S").tolist(),
            "argon_flow": df["argon_flow_lnh"].round(4).tolist(),
            "iso_flow": df["iso_flow_lnh"].round(4).tolist(),
            "total_flow": df["total_flow_lnh"].round(4).tolist(),
            "iso_pct": df["iso_pct_meas"].round(3).tolist(),
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/gas/usage")
def gas_usage():
    """Estimated bottle usage: integrate the whole logged flow history to get the
    normal litres drawn from each bottle, then convert to remaining argon pressure
    / isobutane liquid and an extrapolated time-to-empty. Starting assumptions
    (bottle size, argon 220 bar, iso 80 % liquid) live in bottle_usage.py."""
    from gas_mixer_control.bottle_usage import compute_bottle_usage
    try:
        return jsonify(compute_bottle_usage(GAS_LOG_DIR))
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


# --- 3He target pressure gauge (Keithley 2000 over GPIB) ---
# The GPIB link is owned by the separate he3_pressure_watcher process (see
# he3_pressure_reader/he3_pressure_controller.py). Flask only reads the watcher's
# published state and CSV history; it never touches the bus. Pressure is in bar
# (PRESS_UNIT).

def _he3_pressure_read_state():
    """The watcher's latest published state, or a disconnected stub if it isn't running
    yet / hasn't written the file."""
    try:
        with open(HE3_PRESSURE_STATE_PATH) as f:
            return json.load(f)
    except Exception:
        return {"connected": False, "last_error": "3He pressure watcher not running",
                "unit": PRESS_UNIT}


@app.route("/he3_pressure/status")
def he3_pressure_status():
    """Latest pressure reading published by the he3_pressure_watcher process (in bar)."""
    return jsonify(_he3_pressure_read_state())


# The 3He gauge was installed Wed 2026-07-15 and settled by ~18:00; earlier CSV rows are
# pre-install garbage (negative/out-of-range). Plots anchor here and extend with time.
HE3_PLOT_START = "2026-07-15 18:00:00"


@app.route("/he3_pressure/history")
def he3_pressure_history():
    """Logged 3He pressure history from the per-day CSV(s) for the plot. Anchored at the
    gauge install time (HE3_PLOT_START) and extends to now, so the window grows with the
    measurement. Pass `start=<ISO>` to override the anchor or `hours=<N>` for a recent
    rolling window instead. To keep the payload light as the range grows, points are
    binned into a moving average (mean per time-bin), with the bin width scaled so the
    trace stays near `max_points`. Reads the CSVs the he3_pressure_watcher writes, so this
    is real persisted history."""
    import glob
    import re
    start = request.args.get("start", default=HE3_PLOT_START, type=str)
    hours = request.args.get("hours", default=None, type=float)
    max_points = max(1, request.args.get("max_points", default=1500, type=int))
    try:
        files = sorted(glob.glob(os.path.join(HE3_PRESSURE_LOG_DIR, "he3_pressure_*.csv")))
        if not files:
            return jsonify({"success": True, "time": [], "pressure": [], "unit": PRESS_UNIT})
        # Window start: a rolling `hours` window if asked, else the fixed install anchor.
        if hours and hours > 0:
            start_ts = pd.Timestamp(datetime.now() - timedelta(hours=hours))
        else:
            start_ts = pd.to_datetime(start)
        # Only read day-files on/after the window start (keeps the read cheap as the
        # campaign's history grows). Files are named he3_pressure_YYYY-MM-DD.csv.
        def _file_date(f):
            m = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(f))
            return pd.to_datetime(m.group(1)).date() if m else None
        start_date = start_ts.date()
        keep = [f for f in files if (_file_date(f) is None or _file_date(f) >= start_date)]
        if not keep:
            keep = files[-1:]
        df = pd.concat([pd.read_csv(f) for f in keep], ignore_index=True)
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
        df = df.dropna(subset=["timestamp"])
        df = df[df["timestamp"] >= start_ts].sort_values("timestamp")
        if df.empty:
            return jsonify({"success": True, "time": [], "pressure": [], "unit": PRESS_UNIT})
        # Bin into a moving average: bin width grows with the span so the point count
        # stays near max_points and each plotted point is the mean over its bin.
        span_s = (df["timestamp"].iloc[-1] - df["timestamp"].iloc[0]).total_seconds()
        bin_s = max(2, int(span_s / max_points) + 1)
        s = (df.set_index("timestamp")["pressure_bar"]
               .resample(f"{bin_s}s").mean().dropna())
        return jsonify({
            "success": True,
            "time": s.index.strftime("%Y-%m-%d %H:%M:%S").tolist(),
            "pressure": s.round(5).tolist(),
            "unit": PRESS_UNIT,
            "bin_s": bin_s,
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


# --- N1081B Module-5 time-tag watcher (owns .244; streams the 4 scint walls as
# per-edge timestamps). Flask only reads the watcher's published state + CSV history;
# it must NOT open .244 itself while the watcher runs (the board broadcasts its stream
# to every client). See n1081b/timetag_watcher_controller.py + n1081b/TIMETAG_WATCHER.md.
def _n1081b_tt_read_state():
    """The watcher's latest published state, or a disconnected stub if not running."""
    try:
        with open(N1081B_TT_STATE_PATH) as f:
            return json.load(f)
    except Exception:
        return {"connected": False, "last_error": "N1081B time-tag watcher not running"}


@app.route("/n1081b/status")
def n1081b_status():
    """Latest health + per-section edge rates published by the n1081b_timetag_watcher."""
    return jsonify(_n1081b_tt_read_state())


@app.route("/n1081b/access")
def n1081b_access():
    """Per-board N1081B access state (IN USE / QUARANTINED / free) for the dashboard
    collision-guard card. Read-only view of config/n1081b_access/."""
    return jsonify(get_n1081b_access_status())


@app.route("/n1081b/access/clear_quarantine", methods=["POST"])
def n1081b_clear_quarantine():
    """Manually clear a board's post-wedge quarantine marker. Guarded behind a
    confirm in the UI: only clear AFTER the board has been verified healthy (e.g.
    physically rebooted). This only removes the marker file — it does NOT touch the
    board — so the next board_session is free to reconnect. Mirrors
    n1081b_session.clear_quarantine() without importing the SDK into Flask."""
    ip = (request.get_json(silent=True) or request.form or {}).get("ip", "")
    valid = {b[0] for b in [(f"192.168.10.{240 + i}",) for i in range(6)]}
    if ip not in valid:
        return jsonify({"success": False, "message": f"unknown board ip {ip!r}"}), 400
    path = os.path.join(N1081B_ACCESS_DIR, ip.replace(".", "_") + ".quarantine.json")
    try:
        os.remove(path)
        return jsonify({"success": True, "ip": ip, "message": "quarantine cleared"})
    except FileNotFoundError:
        return jsonify({"success": True, "ip": ip, "message": "no quarantine to clear"})
    except OSError as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/n1081b/history")
def n1081b_history():
    """Per-channel edge RATE history for the Module-5 card.

    Reads the stream's daily RATE ROLLUP (`n1081b_tt_rates_*.csv`, one row per
    channel per 10 s bin, written by tt_stream_qualify.RateRollup) rather than the
    per-edge `edges.csv`, which runs ~400 MB/day and must never be parsed on a web
    request. Falls back to the legacy per-edge daily CSVs of the retired round-robin
    watcher if no rollup exists yet.

    Series are keyed "<section><channel>" (e.g. "C1") — with one section streamed
    continuously, the per-channel split is the informative view."""
    import glob
    hours = request.args.get("hours", default=6.0, type=float)
    bin_s = max(1.0, request.args.get("bin_s", default=10.0, type=float))
    empty = {"success": True, "time": [], "sections": {}, "bin_s": bin_s}
    try:
        # Work in EPOCH SECONDS end to end. `pd.to_datetime(host_unix, unit="s")` is
        # naive UTC while `datetime.now()` is local (CEST), so comparing the two drops
        # everything inside the last 2 h — the same UTC/local trap as the EOS mtimes.
        cutoff = (time.time() - hours * 3600.0) if hours and hours > 0 else None
        # Pick files by the DAY IN THE NAME, not a fixed tail slice: a `files[-2:]`
        # cap here silently hid data on the beam/spill plots (see the backfill fix).
        days = sorted(glob.glob(os.path.join(N1081B_TT_LOG_DIR, "n1081b_tt_rates_*.csv")))
        if cutoff:
            first_day = datetime.fromtimestamp(cutoff - 86400).strftime("%Y-%m-%d")
            days = [f for f in days if f[-14:-4] >= first_day]
        if days:
            df = pd.concat([pd.read_csv(f) for f in days], ignore_index=True)
            df["series"] = df["section"].astype(str) + df["channel"].astype(str)
            value_col = "hz"
        else:
            legacy = sorted(glob.glob(os.path.join(N1081B_TT_LOG_DIR,
                                                   "n1081b_timetag_*.csv")))
            if not legacy:
                return jsonify(empty)
            df = pd.concat([pd.read_csv(f) for f in legacy[-2:]], ignore_index=True)
            df["series"] = df["section"].astype(str) + df["channel"].astype(str)
            df["hz"] = 1.0          # one row per edge; summing then /bin_s gives Hz
            value_col = "edges"
            df["edges"] = 1

        df["host_unix"] = pd.to_numeric(df["host_unix"], errors="coerce")
        df = df.dropna(subset=["host_unix"])
        if cutoff is not None:
            df = df[df["host_unix"] >= cutoff]
        if df.empty:
            return jsonify(empty)

        df["bin"] = (df["host_unix"] // bin_s * bin_s).astype("int64")
        if value_col == "hz":
            # rollup bins may be finer than the requested bin: average within it
            grp = df.groupby(["bin", "series"])["hz"].mean().unstack()
        else:
            grp = df.groupby(["bin", "series"])["edges"].sum().unstack() / bin_s
        grp = grp.sort_index()
        times = [datetime.fromtimestamp(float(b)).strftime("%Y-%m-%d %H:%M:%S")
                 for b in grp.index.to_numpy()]
        sections = {col: [None if pd.isna(v) else round(float(v), 2)
                          for v in grp[col]] for col in grp.columns}
        return jsonify({"success": True, "time": times, "sections": sections,
                        "bin_s": bin_s})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/start_n1081b_timetag_watcher", methods=["POST"])
def start_n1081b_timetag_watcher():
    """Start the N1081B trigger-timestamp stream (sole owner of .244: holds ONE section
    in Time-Tag and streams per-edge timestamps continuously, in chained 6 h segments).
    While it runs, poll_modules auto-skips .244; on stop it restores the counter state.

    This deliberately launches tt_stream_supervisor.py, NOT n1081b_timetag_watcher.py:
    the round-robin watcher's TT start/stop churn is what wedged .244 on 2026-07-15/17.
    The tmux session name must stay `n1081b_timetag_watcher` — poll_modules keys its
    .244 skip off that name."""
    try:
        subprocess.run(["tmux", "kill-session", "-t", "n1081b_timetag_watcher"], capture_output=True)
        subprocess.Popen([
            "tmux", "new-session", "-d", "-s", "n1081b_timetag_watcher",
            sys.executable, f"{BASE_DIR}/n1081b/tt_stream_supervisor.py", "--section", "C"
        ])
        return jsonify({"success": True,
                        "message": "N1081B trigger-timestamp stream started (section C)"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/stop_n1081b_timetag_watcher", methods=["POST"])
def stop_n1081b_timetag_watcher():
    """Stop the N1081B trigger-timestamp stream, gracefully.

    Order matters here. Dropping the websocket without closing the stream is the
    dirty disconnect that wedges these boards, and killing the tmux session first
    would take the pane's pty out from under a supervisor that still has to close
    the stream and put .244 back to counters. So: write the stop-file, give the
    chain up to STOP_GRACE_S to wind down (a segment notices within ~10 s, then the
    harness needs ~30-70 s for stop_tt_data + the verified restore), and only then
    reap the tmux session. Runs on a background thread so the button returns at
    once; the Module-5 card reports the real state as it progresses."""
    STOP_GRACE_S = 150.0
    stop_file = os.path.join(BASE_DIR, "config", "tt_stream_supervisor.stop")

    def _graceful_stop():
        deadline = time.time() + STOP_GRACE_S
        while time.time() < deadline:
            if not subprocess.run(["pgrep", "-f", "tt_stream_supervisor.py"],
                                  capture_output=True).stdout.strip():
                break
            time.sleep(2)
        subprocess.run(["tmux", "kill-session", "-t", "n1081b_timetag_watcher"],
                       capture_output=True)
        try:
            os.remove(stop_file)
        except OSError:
            pass

    try:
        with open(stop_file, "w") as f:
            f.write(f"stop requested via GUI {datetime.now().isoformat()}\n")
        threading.Thread(target=_graceful_stop, daemon=True).start()
        return jsonify({"success": True,
                        "message": "Stopping: closing the stream and restoring .244 to "
                                   f"counters (up to {STOP_GRACE_S:.0f}s)"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


# The NXCALS session is owned by the separate beam_watcher process (see
# beam_monitor/beam_intensity_controller.py). Flask only reads the watcher's
# published state and CSV history. Intensity is in 1e10 protons per pulse.

def _beam_read_state():
    """The beam watcher's latest published state, or a disconnected stub if it isn't
    running yet / hasn't written the file."""
    try:
        with open(BEAM_STATE_PATH) as f:
            return json.load(f)
    except Exception:
        return {"connected": False, "last_error": "beam watcher not running",
                "unit": BEAM_UNIT, "beam_on": None}


@app.route("/beam/status")
def beam_status():
    """Latest n_TOF beam-intensity summary published by the beam_watcher process."""
    return jsonify(_beam_read_state())


@app.route("/beam/history")
def beam_history():
    """Logged beam-pulse history from the per-day CSV(s) for a plot. Same shape as
    /he3_pressure/history: `hours` trims the window, striding keeps the payload light."""
    import glob
    hours = request.args.get("hours", default=6.0, type=float)
    max_points = request.args.get("max_points", default=1500, type=int)
    # Rolling-average window, minutes. 2 min is the default: measured on a 3 h
    # beam-on stretch (2026-07-27, ~19 pulses/min), the window-to-window scatter of
    # the delivery rate is 7.1 % at 10 min, 8.4 % at 2 min, 11 % at 1 min and 24 %
    # at 15 s — i.e. 2 min responds 5x faster than the old 10 min for ~1 % more
    # noise, while below ~1 min the pulse count per window starts to dominate.
    # Floor of 0.5 min: a shorter window can contain zero pulses and read as a gap.
    avg_window_min = max(0.5, request.args.get("avg_min", default=2.0, type=float))
    try:
        files = sorted(glob.glob(os.path.join(BEAM_LOG_DIR, "beam_intensity_*.csv")))
        if not files:
            return jsonify({"success": True, "time": [], "intensity": [], "unit": BEAM_UNIT})
        df = pd.concat([pd.read_csv(f) for f in files[-2:]], ignore_index=True)
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
        df = df.dropna(subset=["timestamp"])
        # Early watcher versions could re-log the lookback window on restart:
        # sort + dedup so old files still plot cleanly.
        df = df.sort_values("timestamp").drop_duplicates(subset=["timestamp"])
        # pandas wants an offset alias; seconds keeps sub-minute windows valid.
        win = f"{int(round(avg_window_min * 60))}s"
        # Compute the rolling series on the FULL loaded frame and trim to the
        # display window afterwards: a trailing sum/mean can't see cycles before
        # the left edge, so trimming first would undercount the first
        # avg_window_min of the window.
        #
        # Two complementary measures, same window:
        #  * avg — rolling mean of REAL pulses only (empty cycles excluded). Beam
        #    QUALITY: how hot each pulse is when beam is on. Blind to duty cycle.
        #  * delivery — rolling SUM over ALL cycles (empty ones included, so they
        #    count as zero). Protons on target in the trailing window; this DROPS
        #    to zero during beam-off, so it reflects duty cycle, not just quality.
        pulses = df[df["intensity_e10"] >= PULSE_THRESHOLD_E10]
        avg = (pulses.set_index("timestamp")["intensity_e10"]
               .rolling(win).mean().reset_index())
        delivery = (df.set_index("timestamp")["intensity_e10"]
                    .rolling(win).sum().reset_index())
        if hours and hours > 0:
            cutoff = datetime.now() - timedelta(hours=hours)
            df = df[df["timestamp"] >= cutoff]
            avg = avg[avg["timestamp"] >= cutoff]
            delivery = delivery[delivery["timestamp"] >= cutoff]
        if len(avg) > max_points:
            avg = avg.iloc[:: (len(avg) // max_points) + 1]
        if len(delivery) > max_points:
            delivery = delivery.iloc[:: (len(delivery) // max_points) + 1]
        if len(df) > max_points:
            df = df.iloc[:: (len(df) // max_points) + 1]
        return jsonify({
            "success": True,
            "time": df["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S").tolist(),
            "intensity": df["intensity_e10"].round(3).tolist(),
            "avg_time": avg["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S").tolist(),
            "avg_intensity": avg["intensity_e10"].round(3).tolist(),
            "avg_window_min": avg_window_min,
            "delivery_time": delivery["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S").tolist(),
            "delivery_intensity": delivery["intensity_e10"].round(1).tolist(),
            "delivery_window_min": avg_window_min,
            "unit": BEAM_UNIT,
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


# ---------------------------------------------------------------------------
# SPS slow-extraction spill — TEMPORARY TEST TAB (added 2026-07-23)
# ---------------------------------------------------------------------------
# Companion to the n_TOF beam monitor, answering "is the SPS pause/spill/pause
# structure visible the way the n_TOF pulse train is?". The NXCALS polling is
# done inside the SAME beam_watcher process (it borrows that Spark session);
# Flask only reads the published state file and the per-cycle CSVs.
#
# TO REMOVE: delete this section, the two routes, the sps_monitor import, the
# #sps tab in base.html + index.html, and the _sps hooks in
# beam_monitor/beam_intensity_controller.py.

def _sps_read_state():
    """The SPS monitor's latest published state, or a disconnected stub."""
    try:
        with open(SPS_STATE_PATH) as f:
            return json.load(f)
    except Exception:
        return {"connected": False, "last_error": "beam watcher not running "
                                                  "(the SPS monitor rides along with it)",
                "unit": SPS_UNIT, "spill_on": None}


@app.route("/sps/status")
def sps_status():
    """Latest SPS spill summary, including the stitched extraction-rate timeline
    and the newest single-cycle spill profile."""
    return jsonify(_sps_read_state())


@app.route("/sps/history")
def sps_history():
    """Per-cycle spill history from the CSVs: extracted intensity, effective
    spill length and duty factor, one row per SPS cycle."""
    import glob
    hours = request.args.get("hours", default=6.0, type=float)
    max_points = request.args.get("max_points", default=3000, type=int)
    try:
        files = sorted(glob.glob(os.path.join(SPS_LOG_DIR, "sps_spill_*.csv")))
        if not files:
            return jsonify({"success": True, "time": [], "extracted": [],
                            "spill_len_ms": [], "duty": [], "unit": SPS_UNIT})
        df = pd.concat([pd.read_csv(f) for f in files[-2:]], ignore_index=True)
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
        df = df.dropna(subset=["timestamp"])
        df = df.sort_values("timestamp").drop_duplicates(subset=["timestamp"])
        if hours and hours > 0:
            df = df[df["timestamp"] >= datetime.now() - timedelta(hours=hours)]
        # Only extracting cycles carry a spill; dump/other cycles are plotted as
        # zero-intensity markers so the supercycle gaps stay visible.
        if len(df) > max_points:
            df = df.iloc[:: (len(df) // max_points) + 1]
        ext = df[df["destination"] == EXTRACTED_DEST]
        return jsonify({
            "success": True,
            "time": df["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S").tolist(),
            "extracted": df["extracted_e10"].fillna(0).round(1).tolist(),
            "spill_time": ext["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S").tolist(),
            "spill_len_ms": ext["spill_len_ms"].round(0).where(
                ext["spill_len_ms"].notna(), None).tolist(),
            "duty": ext["duty_factor"].round(4).where(
                ext["duty_factor"].notna(), None).tolist(),
            "unit": SPS_UNIT,
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/sps/tax_history")
def sps_tax_history():
    """H4 barrier position over time, plus the spans where H4 was not open.

    Those spans are ACCESS CANDIDATES, not confirmed accesses: the TAX position
    says the line is blocked, it does not say why. Confirmation needs the H4
    flux counters (see docs/H4_ACCESS_INFERENCE.md)."""
    import glob
    hours = request.args.get("hours", default=24.0, type=float)
    max_points = request.args.get("max_points", default=4000, type=int)
    try:
        files = sorted(glob.glob(os.path.join(SPS_LOG_DIR, "h4_tax_*.csv")))
        if not files:
            return jsonify({"success": True, "time": [], "position_mm": [],
                            "intervals": [], "var": H4_TAX_VAR,
                            "open_max_mm": H4_TAX_OPEN_MAX,
                            "block_min_mm": H4_TAX_BLOCK_MIN,
                            "note": "no h4_tax CSVs yet — the watcher writes "
                                    "them once it has polled"})
        # a day of TAX samples is ~20 k rows; keep enough files to cover `hours`
        keep = max(2, int(hours // 24) + 2)
        df = pd.concat([pd.read_csv(f) for f in files[-keep:]], ignore_index=True)
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
        df = df.dropna(subset=["timestamp", "position_mm"])
        df = df.sort_values("timestamp").drop_duplicates(subset=["unix_ts"])
        if hours and hours > 0:
            df = df[df["timestamp"] >= datetime.now() - timedelta(hours=hours)]
        # Derive the intervals from the FULL series, then thin only for plotting
        # — decimating first can step straight over a stroke and lose a span.
        ivs = tax_blocked_intervals(list(zip(df["unix_ts"].astype(float),
                                             df["position_mm"].astype(float))))
        for iv in ivs:
            iv["start_str"] = datetime.fromtimestamp(iv["start"]).strftime("%Y-%m-%d %H:%M")
            iv["end_str"] = datetime.fromtimestamp(iv["end"]).strftime("%Y-%m-%d %H:%M")
            iv["minutes"] = round(iv["seconds"] / 60.0, 1)
        plot = df.iloc[:: (len(df) // max_points) + 1] if len(df) > max_points else df
        return jsonify({
            "success": True,
            "var": H4_TAX_VAR,
            "time": plot["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S").tolist(),
            "position_mm": plot["position_mm"].round(2).tolist(),
            "intervals": ivs,
            "open_max_mm": H4_TAX_OPEN_MAX,
            "block_min_mm": H4_TAX_BLOCK_MIN,
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


# ---------------------------------------------------------------------------
# n_TOF stream1 raw-file sizes (SiPM-wall dropout proxy)
# ---------------------------------------------------------------------------
# EOS is polled by the SEPARATE stream1_watcher process; Flask only reads its
# published state file and the per-day CSVs. See stream1_monitor/.

def _stream1_read_state():
    """The stream1 watcher's latest published state, or a disconnected stub if it
    isn't running yet / hasn't written the file."""
    try:
        with open(STREAM1_STATE_PATH) as f:
            return json.load(f)
    except Exception:
        return {"connected": False, "state": "no_data",
                "last_error": "stream1 watcher not running"}


@app.route("/stream1/status")
def stream1_status():
    """Latest stream1 file-size summary published by the stream1_watcher process."""
    return jsonify(_stream1_read_state())


@app.route("/stream1/history")
def stream1_history():
    """Logged per-file sizes from the per-day CSV(s) for the plot. `hours` trims the
    window; the reduced/full split is the classification the watcher recorded when
    each file arrived (not recomputed here, so the plot matches the alerting)."""
    import glob
    hours = request.args.get("hours", default=24.0, type=float)
    max_points = request.args.get("max_points", default=3000, type=int)
    try:
        files = sorted(glob.glob(os.path.join(STREAM1_LOG_DIR, "stream1_filesize_*.csv")))
        if not files:
            return jsonify({"success": True, "time": [], "size_gib": [], "grade": [],
                            "run": [], "seq": [], "baseline_gib": []})
        df = pd.concat([pd.read_csv(f) for f in files[-3:]], ignore_index=True)
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
        df = df.dropna(subset=["timestamp"])
        df = df.sort_values("timestamp").drop_duplicates(subset=["run", "seq"])
        if hours and hours > 0:
            df = df[df["timestamp"] >= datetime.now() - timedelta(hours=hours)]
        if len(df) > max_points:
            df = df.iloc[:: (len(df) // max_points) + 1]
        # `grade` / the explicit cut columns are absent in rows written by earlier
        # versions of the watcher; fall back so old CSVs still plot (the watcher
        # migrates a day file in place the next time it appends to it).
        st = _stream1_read_state()
        if "grade" not in df.columns:
            df["grade"] = ""
        df["grade"] = df["grade"].fillna("").replace(
            "", pd.NA).fillna(df["reduced"].map({1: "bad", 0: "good"}))
        for col, ratio in (("quest_cut_gib", st.get("questionable_ratio", 0.9)),
                           ("bad_cut_gib", st.get("reduced_ratio", 0.75))):
            if col not in df.columns:
                df[col] = pd.NA
            df[col] = df[col].fillna(df["baseline_gib"] * ratio)
        return jsonify({
            "success": True,
            "time": df["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S").tolist(),
            "size_gib": df["size_gib"].round(4).tolist(),
            "baseline_gib": df["baseline_gib"].fillna(0).round(4).tolist(),
            "quest_cut_gib": df["quest_cut_gib"].fillna(0).round(4).tolist(),
            "bad_cut_gib": df["bad_cut_gib"].fillna(0).round(4).tolist(),
            "grade": df["grade"].astype(str).tolist(),
            "run": df["run"].astype(int).tolist(),
            "seq": df["seq"].astype(int).tolist(),
            # So the plot can draw the same cuts the watcher classified against.
            "reduced_ratio": st.get("reduced_ratio", 0.75),
            "questionable_ratio": st.get("questionable_ratio", 0.9),
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/stream1/waveform_history")
def stream1_waveform_history():
    """Per-detector gamma-flash amplitude (as a fraction of nominal) over time, from
    the waveform CSV — the "which detector, since when" view that file size cannot
    give. One point per detector per sampled file."""
    import glob
    hours = request.args.get("hours", default=24.0, type=float)
    try:
        files = sorted(glob.glob(os.path.join(STREAM1_LOG_DIR, "stream1_waveform_*.csv")))
        if not files:
            return jsonify({"success": True, "detectors": {}, "n_samples": 0})
        df = pd.concat([pd.read_csv(f) for f in files[-3:]], ignore_index=True)
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
        df = df.dropna(subset=["timestamp"]).sort_values("timestamp")
        if hours and hours > 0:
            df = df[df["timestamp"] >= datetime.now() - timedelta(hours=hours)]
        # Beam-less samples have no flash to measure, so plotting their ~0 ratios would
        # draw a facility-wide collapse that never happened. Drop them from the trend.
        df = df[df["grade"].fillna("") != "no_beam"]
        out = {}
        for det, g in df.groupby("det"):
            out[str(det)] = {
                "time": g["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S").tolist(),
                "flash_ratio": g["flash_ratio"].astype(float).round(4).tolist(),
                "grade": g["grade"].fillna("").astype(str).tolist(),
            }
        return jsonify({"success": True, "detectors": out,
                        "n_samples": int(df["seq"].nunique())})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/stream1/set_nominal", methods=["POST"])
def stream1_set_nominal():
    """Ask the watcher to re-freeze the waveform reference from the newest file.

    Flask never decodes: it drops a command file the watcher picks up on its next
    poll (same split as the gas watcher). The watcher refuses if the walls are down
    in that file, so this cannot bless a dropout as normal."""
    try:
        with open(STREAM1_COMMAND_PATH, "w") as f:
            json.dump({"cmd": "set_nominal",
                       "requested": datetime.now().isoformat(timespec="seconds")}, f)
        log_event("STREAM1_SET_NOMINAL", "flask_button", remote_addr=request.remote_addr)
        return jsonify({"success": True,
                        "message": "Re-baseline queued — applied within one poll (~2 min)"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/stream1/set_size_nominal", methods=["POST"])
def stream1_set_size_nominal():
    """Freeze the file-size benchmark (bytes per proton pulse).

    Body may carry {"per_pulse_gib": x} to set an explicit value; omitted means "use
    what the recent data suggests". As with the waveform nominal, Flask only queues
    the command — the watcher applies it and refuses if the waveform layer says a
    detector is currently down, so a benchmark can't be frozen through a dropout."""
    data = request.get_json(silent=True) or {}
    cmd = {"cmd": "set_size_nominal",
           "requested": datetime.now().isoformat(timespec="seconds")}
    if data.get("per_pulse_gib"):
        try:
            cmd["per_pulse_bytes"] = float(data["per_pulse_gib"]) * (1024 ** 3)
        except (TypeError, ValueError):
            return jsonify({"success": False, "message": "per_pulse_gib must be a number"}), 400
    try:
        with open(STREAM1_COMMAND_PATH, "w") as f:
            json.dump(cmd, f)
        log_event("STREAM1_SET_SIZE_NOMINAL", "flask_button",
                  remote_addr=request.remote_addr, value=cmd.get("per_pulse_bytes"))
        return jsonify({"success": True,
                        "message": "Size benchmark queued — applied within one poll (~2 min)"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/stream1/nominal")
def stream1_nominal():
    """The frozen per-detector reference the waveform layer grades against."""
    try:
        with open(STREAM1_NOMINAL_PATH) as f:
            return jsonify({"success": True, "nominal": json.load(f)})
    except Exception:
        return jsonify({"success": True, "nominal": None})


def _n1081b_find_snapshot():
    """Newest live board read-back available WITHOUT touching the boards: the most
    recent per-sub-run n1081b_config.json daq_control writes, else the newest manual
    snapshots/dump_*.json. Returns (path, kind) or (None, None)."""
    import glob
    cands = []  # (mtime, path, kind)
    try:
        for p in glob.glob(os.path.join(RUN_DIR, "*", "*", "n1081b_config.json")):
            try:
                cands.append((os.path.getmtime(p), p, "run_snapshot"))
            except OSError:
                pass
    except Exception:
        pass
    try:
        for p in glob.glob(os.path.join(N1081B_SNAP_DIR, "dump_*.json")):
            try:
                cands.append((os.path.getmtime(p), p, "dump"))
            except OSError:
                pass
    except Exception:
        pass
    if not cands:
        return None, None
    cands.sort(reverse=True)
    return cands[0][1], cands[0][2]


@app.route("/n1081b/state")
def n1081b_state():
    """Merged N1081B trigger diagram: the static design model (roles, routing,
    intended thresholds/monos) overlaid with the newest available live read-back and
    the currently-applied scan. No board access — reads only files on disk."""
    from n1081b_module_map import build_state
    snapshot, path, kind, age_s, polled_at = None, None, None, None, None
    try:
        path, kind = _n1081b_find_snapshot()
        if path:
            with open(path) as f:
                snapshot = json.load(f)
            age_s = int(time.time() - os.path.getmtime(path))
            polled_at = datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception as e:  # noqa: BLE001
        return jsonify({"success": False, "message": f"snapshot load failed: {e}"}), 500

    scan_active = None
    try:
        if os.path.exists(N1081B_SCAN_ACTIVE_PATH):
            with open(N1081B_SCAN_ACTIVE_PATH) as f:
                sa = json.load(f)
            # only surface it while a scan is actually applied
            if sa.get("active"):
                sa["age_s"] = None
                try:
                    sa["age_s"] = int(time.time() - os.path.getmtime(N1081B_SCAN_ACTIVE_PATH))
                except OSError:
                    pass
                scan_active = sa
    except Exception:
        scan_active = None

    try:
        state = build_state(snapshot, scan_active, source_meta={
            "path": os.path.relpath(path, BASE_DIR) if path else None,
            "kind": kind, "age_s": age_s, "polled_at": polled_at,
        })
    except Exception as e:  # noqa: BLE001
        return jsonify({"success": False, "message": f"build_state failed: {e}"}), 500
    return jsonify(state)


@app.route("/he3_pressure/config", methods=["GET", "POST"])
def he3_pressure_config():
    """Get/set the pressure sample rate. The value is written to a config file the
    he3_pressure_watcher reads each loop, so a change takes effect within one cycle (and
    persists across watcher restarts). Rate is bounded to a range that's safe for the
    Keithley GPIB link and this multi-process box (see the controller's limit comment)."""
    min_hz = round(1.0 / MAX_SAMPLE_PERIOD_S, 4)
    max_hz = round(1.0 / MIN_SAMPLE_PERIOD_S, 4)

    if request.method == "GET":
        try:
            with open(HE3_PRESSURE_CONFIG_PATH) as f:
                poll_s = json.load(f).get("poll_s")
        except Exception:
            poll_s = None
        return jsonify({"success": True, "poll_s": poll_s,
                        "sample_hz": (round(1.0 / poll_s, 4) if poll_s else None),
                        "min_hz": min_hz, "max_hz": max_hz})

    data = request.get_json(silent=True) or {}
    # Accept sample_hz (what the GUI sends) or a raw poll_s period.
    if data.get("sample_hz") is not None:
        try:
            hz = float(data["sample_hz"])
            if hz <= 0:
                raise ValueError
        except (TypeError, ValueError):
            return jsonify({"success": False, "message": "sample_hz must be a positive number"}), 400
        period = 1.0 / hz
    elif data.get("poll_s") is not None:
        try:
            period = float(data["poll_s"])
        except (TypeError, ValueError):
            return jsonify({"success": False, "message": "poll_s must be a number"}), 400
    else:
        return jsonify({"success": False, "message": "sample_hz required"}), 400

    clamped = clamp_period(period)
    try:
        os.makedirs(os.path.dirname(HE3_PRESSURE_CONFIG_PATH), exist_ok=True)
        with open(HE3_PRESSURE_CONFIG_PATH, "w") as f:
            json.dump({"poll_s": clamped}, f)
    except Exception as e:
        return jsonify({"success": False, "message": f"could not write config: {e}"}), 500

    warning = None
    if abs(clamped - period) > 1e-6:
        warning = f"clamped to {round(1.0 / clamped, 3)} Hz (allowed {min_hz}–{max_hz} Hz)"
    log_event('HE3_PRESS_RATE', 'flask_button', remote_addr=request.remote_addr,
              poll_s=round(clamped, 3))
    return jsonify({"success": True, "poll_s": round(clamped, 3),
                    "sample_hz": round(1.0 / clamped, 4),
                    "min_hz": min_hz, "max_hz": max_hz, "warning": warning})


# --- Shift Overview ---
# One aggregate endpoint (/shift/status) so the shifter page needs a single poll,
# plus the Emergency Stop action. Expected values: gas nominals come from
# shift_expected.json (so a mistyped setpoint is flagged too); HV expected = the
# v0 the HV monitor itself logs for the current subrun.

_SHIFT_EXPECTED_DEFAULTS = {
    "gas": {"argon_lnh": 7.0, "iso_pct": 5.0, "flow_tol_lnh": 0.25, "iso_tol_pct": 0.5},
    "hv": {"tol_v": 5.0, "stale_s": 30},
    "he3": {"expected_bar": None, "tol_bar": None},
}

# "Taking data happily" tracking: last time dream_daq was seen RUNNING. Between
# subruns (HV ramp, file copy) this goes stale for a few minutes — that's normal,
# so the page only alarms after a 5 min cushion. Baseline = server start, so a
# fresh flask restart mid-transition doesn't immediately alarm either.
SHIFT_DATA_GAP_OK_S = 300
_shift_last_data_ts = None
_shift_baseline_ts = time.time()


def _shift_expected():
    exp = json.loads(json.dumps(_SHIFT_EXPECTED_DEFAULTS))  # deep copy
    try:
        with open(SHIFT_EXPECTED_PATH) as f:
            user = json.load(f)
        for key, val in user.items():
            if isinstance(val, dict) and isinstance(exp.get(key), dict):
                exp[key].update(val)
            elif not key.startswith("_"):
                exp[key] = val
    except Exception:
        pass
    return exp


def _csv_last_row(path, max_tail=16384):
    """(header_cols, last_complete_row_cols) of a CSV, reading only the tail.
    Row is None if no complete data line is found. Tolerates a partial last
    line (file mid-write) by requiring the field count to match the header."""
    with open(path, 'rb') as f:
        header = f.readline().decode(errors='replace')
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(len(header.encode()), size - max_tail))
        tail = f.read().decode(errors='replace').strip().splitlines()
    cols = [c.strip() for c in header.strip().split(',')]
    for line in reversed(tail):
        vals = line.split(',')
        if len(vals) == len(cols) and vals[0] != cols[0]:
            return cols, vals
    return cols, None


def _shift_latest_hv_csv(run_name, daq_subrun):
    """Path of the hv_monitor.csv to read: the daq-reported subrun's if it exists,
    else the most recently modified one in the current run. None if nothing."""
    if not run_name:
        return None
    run_dir = os.path.join(RUN_DIR, run_name)
    if not os.path.isdir(run_dir):
        return None
    if daq_subrun:
        p = os.path.join(run_dir, daq_subrun, "hv_monitor.csv")
        if os.path.isfile(p):
            return p
    newest, newest_t = None, 0
    try:
        for sub in os.listdir(run_dir):
            p = os.path.join(run_dir, sub, "hv_monitor.csv")
            if os.path.isfile(p):
                t = os.path.getmtime(p)
                if t > newest_t:
                    newest, newest_t = p, t
    except OSError:
        pass
    return newest


def _shift_hv(run_name, daq_subrun, hv_status, exp):
    """Per-channel expected-vs-measured HV from the latest hv_monitor.csv row.
    Expected = the v0 the monitor logs (what hv_control is holding); tolerance and
    staleness from shift_expected.json. level: ok / warn / bad / stale / none."""
    tol = float(exp["hv"].get("tol_v", 5.0))
    stale_s = float(exp["hv"].get("stale_s", 30))
    out = {"channels": [], "age_s": None, "level": "none", "csv": None,
           "state": hv_status.get("status", "?"), "tol_v": tol}

    csv_path = _shift_latest_hv_csv(run_name, daq_subrun)
    if not csv_path:
        return out
    out["csv"] = os.path.basename(os.path.dirname(csv_path))
    try:
        cols, row = _csv_last_row(csv_path)
        out["age_s"] = round(time.time() - os.path.getmtime(csv_path), 1)
    except Exception:
        return out
    if not row:
        return out

    # Map "slot:channel" -> "A_Drift" labels from the run's config json.
    labels = {}
    try:
        with open(os.path.join(RUN_DIR, run_name, "run_config.json")) as f:
            labels = _hv_channel_labels(json.load(f))
    except Exception:
        pass

    def fnum(s):
        try:
            return float(s)
        except (TypeError, ValueError):
            return None

    vals = dict(zip(cols, row))
    keys = sorted({c.rsplit(' ', 1)[0] for c in cols if c.endswith(' vmon')})
    ramping = hv_status.get("status") == "Ramping HV"
    # category -> display suffix, so "A_Drift" reads "A_Drift MM" and "A_P_L" reads
    # "A_P_L Scint" instead of the raw ('label', 'category') tuple leaking into the
    # page as "A_Drift,mm" (Array.toString() joins with a comma in the JS template).
    cat_suffix = {"mm": "MM", "scint": "Scint"}
    n_bad = 0
    for key in keys:
        v0 = fnum(vals.get(f"{key} v0"))
        vmon = fnum(vals.get(f"{key} vmon"))
        imon = fnum(vals.get(f"{key} imon"))
        power = vals.get(f"{key} power", "").strip()
        powered = power in ("1", "1.0", "True")
        if v0 is None or vmon is None:
            ok = None          # manual / unreadable channel: shown, not judged
        elif v0 == 0:
            ok = (not powered) or abs(vmon) <= tol
        else:
            ok = powered and abs(vmon - v0) <= tol
        if ok is False:
            n_bad += 1
        base_label, category = labels.get(key, (key, "other"))
        suffix = cat_suffix.get(category, "")
        wall_m = re.match(r'^([A-Za-z])_', base_label)
        out["channels"].append({
            "key": key, "label": f"{base_label} {suffix}".strip() if suffix else base_label,
            "wall": wall_m.group(1).upper() if wall_m else "Other",
            "v0": v0, "vmon": vmon, "imon": imon, "powered": powered, "ok": ok,
        })
    cat_by_key = {k: labels.get(k, (k, "other"))[1] for k in keys}
    cat_rank = {"mm": 0, "scint": 1, "other": 2}
    out["channels"].sort(key=lambda c: (
        c["wall"] == "Other", c["wall"], cat_rank.get(cat_by_key.get(c["key"]), 2), c["label"]))

    if out["age_s"] is not None and out["age_s"] > stale_s:
        # Monitor only writes during a subrun — stale between subruns is normal.
        out["level"] = "stale"
    elif n_bad == 0:
        out["level"] = "ok"
    elif ramping:
        out["level"] = "warn"   # differences expected mid-ramp
    else:
        out["level"] = "bad"
    return out


def _shift_gas(exp):
    """Gas measured vs setpoint vs nominal, with pass/fail per comparison."""
    st = _gas_read_state()
    g = exp["gas"]
    flow_tol = float(g.get("flow_tol_lnh", 0.25))
    iso_tol = float(g.get("iso_tol_pct", 0.5))
    nom_argon, nom_iso = g.get("argon_lnh"), g.get("iso_pct")

    out = {"connected": bool(st.get("connected")), "level": "bad",
           "nominal": {"argon_lnh": nom_argon, "iso_pct": nom_iso},
           "tol": {"flow_lnh": flow_tol, "iso_pct": iso_tol},
           "last_error": st.get("last_error")}
    if not out["connected"]:
        return out

    argon, iso, der = st.get("argon", {}), st.get("iso", {}), st.get("derived", {})

    def near(a, b, tol):
        if a is None or b is None:
            return None
        return abs(a - b) <= tol

    checks = {
        "argon_meas_ok": near(argon.get("flow_lnh"), argon.get("set_lnh"), flow_tol),
        "argon_set_ok":  near(argon.get("set_lnh"), nom_argon, flow_tol),
        "iso_meas_ok":   near(der.get("iso_pct_meas"), der.get("iso_pct_set"), iso_tol),
        "iso_set_ok":    near(der.get("iso_pct_set"), nom_iso, iso_tol),
    }
    try:
        age = (datetime.now() - datetime.fromisoformat(st["timestamp"])).total_seconds()
    except Exception:
        age = None

    out.update({
        "argon": {"flow_lnh": argon.get("flow_lnh"), "set_lnh": argon.get("set_lnh")},
        "iso": {"flow_lnh": iso.get("flow_lnh"), "set_lnh": iso.get("set_lnh")},
        "total_flow_lnh": der.get("total_flow_lnh"),
        "iso_pct_meas": der.get("iso_pct_meas"),
        "iso_pct_set": der.get("iso_pct_set"),
        "age_s": age, "checks": checks,
    })
    if age is not None and age > 15:
        out["level"] = "stale"
    elif all(v is not False for v in checks.values()):
        out["level"] = "ok"
    else:
        out["level"] = "bad"
    return out


def _shift_beam():
    """Beam on/off + intensity summary for the shift card, from the beam watcher's
    published state. level: ok (beam on) / warn (beam off — facility, not us) /
    stale (watcher down or data old) / none."""
    st = _beam_read_state()
    out = {"connected": bool(st.get("connected")), "level": "stale",
           "beam_on": st.get("beam_on"),
           "last_pulse_time": st.get("last_pulse_time"),
           "last_pulse_e10": st.get("last_pulse_e10"),
           "seconds_since_pulse": st.get("seconds_since_pulse"),
           "pulses_10min": st.get("pulses_10min"),
           "protons_10min_e10": st.get("protons_10min_e10"),
           "avg_pulse_e10": st.get("avg_pulse_e10"),
           "unit": st.get("unit", BEAM_UNIT),
           "krb_valid_until": st.get("krb_valid_until"),
           "last_error": st.get("last_error")}
    if not out["connected"]:
        return out
    try:
        age = (datetime.now() - datetime.fromisoformat(st["timestamp"])).total_seconds()
    except Exception:
        age = None
    out["age_s"] = age
    # Kerberos running out is the one silent failure mode (queries start failing when
    # the ticket dies) — surface it while there is still time to reseed.
    krb_hours_left = None
    try:
        krb_hours_left = (datetime.fromisoformat(st["krb_valid_until"])
                          - datetime.now()).total_seconds() / 3600.0
    except Exception:
        pass
    out["krb_hours_left"] = round(krb_hours_left, 1) if krb_hours_left is not None else None
    if age is not None and age > 300:
        out["level"] = "stale"
    elif st.get("beam_on"):
        out["level"] = "ok"
    else:
        out["level"] = "warn"
    return out


# Projections only move when a sub-run completes (~hourly) or a new one is
# frozen, so recomputing on every 15 s poll (it walks every run directory) would
# be pure waste. No matplotlib involved here — this is data, not a rendered
# plot — so it's cheap enough to import and cache in-process rather than shell out.
PROGRESS_DATA_MAX_AGE_S = 120
_progress_data_cache = {"t": 0.0, "data": None}
_progress_data_lock = threading.Lock()


@app.route("/shift/progress_data")
def shift_progress_data():
    """Cumulative-triggers-vs-projection series for the Shift tab's Plotly plot —
    the same comparison pushed to the public CERN page, as data rather than a
    pre-rendered image, so it can be styled to match the rest of the dashboard."""
    now = time.time()
    if now - _progress_data_cache["t"] > PROGRESS_DATA_MAX_AGE_S:
        with _progress_data_lock:
            if now - _progress_data_cache["t"] > PROGRESS_DATA_MAX_AGE_S:
                try:
                    projections_dir = os.path.join(BASE_DIR, "projections")
                    if projections_dir not in sys.path:
                        sys.path.insert(0, projections_dir)
                    import live as projection_live
                    data = projection_live.cumulative_series()
                    _progress_data_cache.update(t=now, data=data)
                except Exception as e:
                    print(f"[shift] progress data failed: {e}")
    return jsonify({"success": _progress_data_cache["data"] is not None,
                    "data": _progress_data_cache["data"]})


@app.route("/shift/status")
def shift_status():
    """Everything the Shift Overview page shows, in one poll."""
    global _shift_last_data_ts
    now = time.time()
    exp = _shift_expected()

    daq_info = get_daq_control_status()
    dream_info = get_dream_daq_status()
    hv_status = get_hv_control_status()
    _save_current_run(_extract_daq_run(daq_info))

    run_name = _current_run_cache
    daq_state = daq_info.get("status", "?")
    taking_data = dream_info.get("status") == "RUNNING"
    run_active = daq_state not in ("WAITING", "Run Complete", "ERROR")
    if taking_data:
        _shift_last_data_ts = now
    gap_s = now - (_shift_last_data_ts or _shift_baseline_ts)

    if taking_data:
        state, label, color = "TAKING_DATA", "Taking data", "ok"
    elif run_active and gap_s < SHIFT_DATA_GAP_OK_S:
        state, label, color = "TRANSITION", "Between sub-runs", "warn"
    elif run_active:
        state, label, color = "STALLED", "NOT taking data", "bad"
    else:
        state, label, color = "NO_RUN", "No run in progress", "idle"

    prog = _run_progress(daq_info, dream_info)
    events = (_ondisk_run_events(run_name) + _live_events_from(dream_info)) if run_name else 0

    # Same classifier the Disk Space tab uses (beam/cosmics/pulser/pedestal/...)
    # so "what are we taking" reads identically in both places.
    run_class = space_manager.classify_run(run_name) if run_name else None

    run = {
        "state": state, "label": label, "color": color,
        "run_name": run_name or "None",
        "daq_status": daq_state,
        "dream_status": dream_info.get("status", "?"),
        "subrun": _status_field(daq_info, "Subrun"),
        "subrun_idx": prog.get("subrun_idx"),
        "subrun_total": prog.get("subrun_total"),
        "elapsed_min": prog.get("elapsed_min"),
        "total_min": prog.get("total_min"),
        "events": events,
        "int_rate": _status_field(dream_info, "Int Rate"),
        "gap_s": round(gap_s, 1),
        "gap_ok_s": SHIFT_DATA_GAP_OK_S,
        "class": (run_class or {}).get("class"),
        "class_label": (run_class or {}).get("class_label"),
    }

    he3 = _he3_pressure_read_state()
    he3_out = {"connected": bool(he3.get("connected")),
               "pressure": he3.get("pressure"), "unit": he3.get("unit", "bar"),
               "expected": exp["he3"].get("expected_bar")}

    system = {}
    try:
        import psutil
        mem = psutil.virtual_memory()

        def d(path):
            try:
                u = psutil.disk_usage(path)
                return {"total": u.total, "used": u.used, "percent": u.percent}
            except Exception:
                return None
        system = {"mem": {"total": mem.total, "used": mem.used, "percent": mem.percent},
                  "ssd": d('/'), "hdd": d('/mnt/data')}
    except Exception:
        pass

    return jsonify({
        "success": True,
        "time": datetime.now().strftime('%H:%M:%S'),
        "run": run,
        "gas": _shift_gas(exp),
        "hv": _shift_hv(run_name, _status_field(daq_info, "Subrun"), hv_status, exp),
        "he3": he3_out,
        "beam": _shift_beam(),
        "system": system,
    })


@app.route("/shift/emergency_stop", methods=["POST"])
def emergency_stop():
    """EMERGENCY STOP (shifter-accessible, no login — like /gas/zero):
    1. zero the gas mixer, 2. stop the run, 3. power off the controlled HV
    channels directly on the CAEN crate (emergency_hv_off.py, own session)."""
    log_event('EMERGENCY_STOP', 'shift_page', remote_addr=_client_ip())
    results = {}

    # 1. Gas to zero (waits up to ~3 s for the gas_watcher's ack).
    try:
        r, _ = _gas_send_command({"cmd": "zero"})
        results["gas"] = {"ok": bool(r.get("success")), "detail": r.get("message", "zeroed")}
    except Exception as e:
        results["gas"] = {"ok": False, "detail": str(e)}

    # 2. Stop the whole run (flag + stop_dream; daq_control shuts down cleanly).
    try:
        subprocess.Popen([f"{BASH_DIR}/stop_run.sh"])
        results["run"] = {"ok": True, "detail": "stop_run dispatched"}
    except Exception as e:
        results["run"] = {"ok": False, "detail": str(e)}

    # 3. HV off, direct to the crate. Runs in the background (needs a CAEN login);
    # output goes to logs/emergency_hv_off.log. cwd=BASE_DIR: Config() reads
    # hv_creds.txt by relative path.
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        hv_log = open(f"{LOG_DIR}/emergency_hv_off.log", "a")
        hv_log.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S} from "
                     f"{_client_ip()} =====\n")
        hv_log.flush()
        subprocess.Popen([sys.executable, f"{BASE_DIR}/emergency_hv_off.py"],
                         cwd=BASE_DIR, stdout=hv_log, stderr=subprocess.STDOUT)
        results["hv"] = {"ok": True, "detail": "HV power-off dispatched"}
    except Exception as e:
        results["hv"] = {"ok": False, "detail": str(e)}

    ok = all(v["ok"] for v in results.values())
    log_event('EMERGENCY_DONE', 'shift_page', remote_addr=_client_ip(),
              gas=results["gas"]["ok"], run=results["run"]["ok"], hv=results["hv"]["ok"])
    return jsonify({"success": ok, "results": results,
                    "message": "Emergency stop: gas zeroed, run stopping, HV powering off"
                               if ok else "Emergency stop dispatched with errors — check details"})


@app.route("/run/hv_off", methods=["POST"])
def hv_off():
    """HV off for the Overview page's Run Control box: power off only the HV
    channels belonging to the detectors listed in the current run config's
    included_detectors (direct to the CAEN crate, own session — reuses
    emergency_hv_off.py, the same script the Shift Overview Emergency Stop
    uses for its HV step)."""
    log_event('HV_OFF', 'overview_page', remote_addr=_client_ip())
    try:
        included = list(Config().included_detectors)
    except Exception:
        included = []

    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        hv_log = open(f"{LOG_DIR}/hv_off.log", "a")
        hv_log.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S} from "
                     f"{_client_ip()} =====\n")
        hv_log.flush()
        subprocess.Popen([sys.executable, f"{BASE_DIR}/emergency_hv_off.py"],
                         cwd=BASE_DIR, stdout=hv_log, stderr=subprocess.STDOUT)
        detail = (f"HV power-off dispatched for: {', '.join(included)}" if included
                  else "HV power-off dispatched (no detectors listed in run config)")
        return jsonify({"success": True, "message": detail, "detectors": included})
    except Exception as e:
        return jsonify({"success": False, "message": f"HV off failed: {e}",
                        "detectors": included})


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
            ["tmux", "capture-pane", "-pJS", "-20", "-t", "daq_control:0.0"],
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


# ===========================================================================
# Disk Space tab — free space by clearing DREAM runs that are provably backed up
# ---------------------------------------------------------------------------
# All the safety logic lives in space_manager.py: a run is "safe to delete" only
# when its data is verified elsewhere (HDD run -> EOS; SSD raw run -> HDD -> EOS).
# /space/scan is read-only (open to viewers); /space/delete is a POST and so is
# gated by the view-only guard, AND space_manager re-verifies every run itself
# before removing it (never trusts the client).
# ===========================================================================

@app.route("/space/usage")
def space_usage():
    return jsonify(space_manager.disk_usage())


@app.route("/space/forecast")
def space_forecast():
    """Time-to-full forecast for the SSD (/) and HDD (/mnt/data), from the fill rate
    measured off the system_stats history (see disk_forecast.py). `hours` sets the
    trailing window. Imported lazily so a missing pandas never breaks the tab that
    only wants the usage bars from /space/usage."""
    hours = request.args.get("hours", default=6.0, type=float)
    try:
        import disk_forecast
        return jsonify(disk_forecast.forecast(hours))
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/space/local")
def space_local():
    """What is on the disk right now — local stat() only. Instant, and works with
    EOS/Kerberos down — it shows WHAT is on the disk; only /space/scan can say
    what is safe to delete."""
    disk = request.args.get("disk", "hdd")
    if disk not in space_manager.DISKS:
        return jsonify({"success": False, "message": f"unknown disk {disk}"}), 400
    try:
        return jsonify(space_manager.local_scan(disk))
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/space/scan")
def space_scan():
    """Per-run EOS verdicts for a disk.

    cached=1 replays the LAST EOS listing at whatever age it has instead of
    re-listing, so a page reload can restore the verified view (with its age)
    rather than discarding it. `unverifiable` comes back True when there is no
    listing to replay, and the caller should fall back to /space/local.
    """
    disk = request.args.get("disk", "hdd")
    cached = request.args.get("cached", "0") in ("1", "true", "yes")
    if disk not in space_manager.DISKS:
        return jsonify({"success": False, "message": f"unknown disk {disk}"}), 400
    try:
        return jsonify(space_manager.scan(disk, force=not cached, allow_stale=cached))
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/space/labels")
def space_labels():
    """Every known run's class (beam / cosmics / pulser / …) and where it came
    from. The scans already carry this per run; this is the standalone view, and
    it also covers runs no longer on disk."""
    try:
        runs = sorted(set(space_manager.list_runs("hdd"))
                      | set(space_manager.list_runs("ssd"))
                      | set(space_manager._read_run_labels()))
        return jsonify({
            "runs": space_manager.run_classes(runs),
            "classes": {k: dict(space_manager.RUN_CLASSES[k], key=k)
                        for k in space_manager.RUN_CLASS_ORDER},
            "class_order": space_manager.RUN_CLASS_ORDER,
            "path": space_manager.RUN_LABELS_PATH,
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/space/label", methods=["POST"])
def space_label():
    """Pin a run's class by hand when run_config.json's beam_type does not match
    what the run was really for. class='auto' drops the override. Writes only to
    config/run_labels.json — never to the run data itself."""
    data = request.get_json(silent=True) or {}
    run = data.get("run")
    klass = data.get("class")
    try:
        res = space_manager.set_run_label(run, klass)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    if not res.get("success"):
        return jsonify(res), 400
    log_event("SPACE_LABEL", "disk_space", run=run, cls=klass)
    return jsonify(res)


# --- Background jobs for the long space operations -------------------------
# The EOS-backed operations take ~25 s (one whole-tree listing plus the local
# walk) to minutes (deleting), which is too long to hold a request open and gives
# the GUI nothing to draw. Each one runs on a worker thread that publishes
# progress into _space_jobs; the browser starts a job, then polls /space/job/<id>.
#
# Honesty note on the bar: the `xrdfs ls -R` listing CANNOT be tracked. It emits
# nothing for tens of seconds and then dumps every line at once, because the cost
# is connect + Kerberos + the server-side walk. So the listing phase reports
# indeterminate and the GUI animates it against the previous run's duration
# (space_manager.listing_estimate_s), clearly labelled as an estimate. The phases
# after it — per-run verification, per-item deletion — are counted for real.
_space_jobs = {}
_space_jobs_lock = threading.Lock()
_SPACE_JOB_TTL = 900        # forget finished jobs after 15 min


def _space_job_prune():
    now = time.time()
    for jid in [j for j, v in _space_jobs.items()
                if v.get("finished_at") and now - v["finished_at"] > _SPACE_JOB_TTL]:
        _space_jobs.pop(jid, None)


def _space_job_start(kind, fn):
    """Run fn(progress) on a worker thread; return the new job id."""
    jid = uuid.uuid4().hex[:12]
    job = {"id": jid, "kind": kind, "phase": "starting", "done": 0, "total": None,
           "msg": "", "items": [], "running": True, "result": None, "error": None,
           "started_at": time.time(), "finished_at": None,
           "listing_estimate_s": space_manager.listing_estimate_s()}
    with _space_jobs_lock:
        _space_job_prune()
        _space_jobs[jid] = job

    def progress(phase, done, total, msg, item=None):
        with _space_jobs_lock:
            job.update(phase=phase, done=done, total=total, msg=msg)
            if item is not None:
                job["items"].append(item)

    def run():
        try:
            out = fn(progress)
            with _space_jobs_lock:
                job["result"] = out
        except Exception as e:
            with _space_jobs_lock:
                job["error"] = str(e)
        finally:
            with _space_jobs_lock:
                job["running"] = False
                job["finished_at"] = time.time()

    threading.Thread(target=run, daemon=True, name=f"space-{kind}-{jid}").start()
    return jid


@app.route("/space/estimate")
def space_estimate():
    """How long the last EOS listing took. The GUI animates the untrackable
    listing phase against this, so the bar is calibrated to the real link."""
    return jsonify({"listing_s": space_manager.listing_estimate_s()})


@app.route("/space/job/<job_id>")
def space_job_status(job_id):
    """Poll a running space job. `since` trims the per-item log to what the caller
    has not seen yet, so polling stays cheap on long deletes."""
    with _space_jobs_lock:
        job = _space_jobs.get(job_id)
        if job is None:
            return jsonify({"success": False, "message": "unknown or expired job"}), 404
        try:
            since = int(request.args.get("since", 0))
        except ValueError:
            since = 0
        out = {k: v for k, v in job.items() if k != "items"}
        out["items"] = job["items"][since:]
        out["n_items"] = len(job["items"])
        out["elapsed"] = round(time.time() - job["started_at"], 2)
    return jsonify(out)


@app.route("/space/job/check", methods=["POST"])
def space_job_check():
    """Start an EOS safety check. mode=prune runs the per-component scan across
    both disks, any other value runs the whole-run scan for one disk."""
    data = request.get_json(silent=True) or {}
    mode = data.get("mode", "prune")
    if mode == "prune":
        fn = lambda p: space_manager.component_scan(verify=True, force=True, progress=p)
    else:
        disk = data.get("disk", "hdd")
        if disk not in space_manager.DISKS:
            return jsonify({"success": False, "message": f"unknown disk {disk}"}), 400
        fn = lambda p: space_manager.scan(disk, force=True, progress=p)
    return jsonify({"job": _space_job_start("check", fn)})


@app.route("/space/job/delete_components", methods=["POST"])
def space_job_delete_components():
    """Start a component delete. Same guards as the synchronous route —
    space_manager re-verifies every piece against a fresh EOS listing."""
    data = request.get_json(silent=True) or {}
    items = data.get("items") or []
    confirm = data.get("confirm")
    if not isinstance(items, list) or not items:
        return jsonify({"success": False, "message": "nothing selected"}), 400
    if confirm != "DELETE":
        return jsonify({"success": False, "message": "confirmation text did not match"}), 400
    comps = sorted({i.get("component") for i in items if isinstance(i, dict)} - {None})
    runs = sorted({i.get("run") for i in items if isinstance(i, dict)} - {None})

    def fn(p):
        out = space_manager.delete_components(items, progress=p)
        log_event("SPACE_DELETE_COMPONENTS", "disk_space",
                  runs=",".join(runs), components=",".join(comps),
                  items=len(items), freed=out["freed_h"],
                  ok=out["n_deleted"], failed=out["n_failed"])
        out["success"] = out["n_failed"] == 0
        out["usage"] = space_manager.disk_usage()
        return out

    return jsonify({"job": _space_job_start("delete", fn)})


@app.route("/space/components")
def space_components():
    """The run -> subrun -> component tree with a delete verdict per component.

    verify=0 skips EOS entirely (instant, works offline) so the tab can paint the
    breakdown immediately; verify=1 issues ONE recursive EOS listing for the whole
    tree and marks each component safe/unsafe from it.

    verify=cached is what a page reload should use: it replays the LAST listing at
    whatever age it has, without touching EOS, and returns checked_age_h so the tab
    can say "good as of 2 minutes ago". A fresh listing costs tens of seconds and
    the verdicts only move when the backup watcher pushes, so paying that on every
    reload buys nothing. Deletion is unaffected — it always re-lists and re-verifies.
    """
    v = request.args.get("verify", "1")
    allow_stale = v in ("cached", "stale")
    verify = allow_stale or v not in ("0", "false", "no")
    force = request.args.get("force", "0") in ("1", "true", "yes")
    try:
        return jsonify(space_manager.component_scan(
            verify=verify, force=force, allow_stale=allow_stale))
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/space/preflight", methods=["POST"])
def space_preflight():
    """Dry-run a component selection: bytes freed, what is refused and why, and
    which subruns the processor would reprocess. Read-only — deletes nothing."""
    data = request.get_json(silent=True) or {}
    items = data.get("items") or []
    if not isinstance(items, list):
        return jsonify({"success": False, "message": "items must be a list"}), 400
    try:
        return jsonify(space_manager.preflight_components(items))
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/space/delete_components", methods=["POST"])
def space_delete_components():
    """Delete selected (run, subrun, component) triples synchronously — the
    scriptable sibling of /space/job/delete_components. space_manager re-verifies
    every piece against a fresh EOS listing before removing anything."""
    data = request.get_json(silent=True) or {}
    items = data.get("items") or []
    confirm = data.get("confirm")
    if not isinstance(items, list) or not items:
        return jsonify({"success": False, "message": "nothing selected"}), 400
    if confirm != "DELETE":
        return jsonify({"success": False, "message": "confirmation text did not match"}), 400
    out = space_manager.delete_components(items)
    comps = sorted({i.get("component") for i in items if isinstance(i, dict)} - {None})
    runs = sorted({i.get("run") for i in items if isinstance(i, dict)} - {None})
    log_event("SPACE_DELETE_COMPONENTS", "disk_space",
              runs=",".join(runs), components=",".join(comps),
              items=len(items), freed=out["freed_h"],
              ok=out["n_deleted"], failed=out["n_failed"])
    out["success"] = out["n_failed"] == 0
    out["usage"] = space_manager.disk_usage()
    return jsonify(out)


@app.route("/space/delete", methods=["POST"])
def space_delete():
    data = request.get_json(silent=True) or {}
    disk = data.get("disk")
    runs = data.get("runs") or []
    confirm = data.get("confirm")
    if disk not in space_manager.DISKS:
        return jsonify({"success": False, "message": f"unknown disk {disk}"}), 400
    if not isinstance(runs, list) or not runs:
        return jsonify({"success": False, "message": "no runs selected"}), 400
    # Typed confirmation must match exactly, so a stray click can't delete.
    if confirm != "DELETE":
        return jsonify({"success": False, "message": "confirmation text did not match"}), 400
    out = space_manager.delete_runs(disk, runs)
    log_event("SPACE_DELETE", "disk_space", disk=disk,
              runs=",".join(runs), freed=out["freed_h"],
              ok=out["n_deleted"], failed=out["n_failed"])
    out["success"] = out["n_failed"] == 0
    out["usage"] = space_manager.disk_usage().get(disk, {})
    return jsonify(out)


@app.route("/space/scan_subruns")
def space_scan_subruns():
    """Per-subrun verdicts for one run (read-only) — backs the run-row dropdown that
    lets an operator prune individual subruns of a long run."""
    disk = request.args.get("disk", "ssd")
    run = request.args.get("run", "")
    if disk not in space_manager.DISKS:
        return jsonify({"success": False, "message": f"unknown disk {disk}"}), 400
    try:
        return jsonify(space_manager.scan_subruns(disk, run))
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/space/delete_subruns", methods=["POST"])
def space_delete_subruns():
    data = request.get_json(silent=True) or {}
    disk = data.get("disk")
    run = data.get("run")
    subruns = data.get("subruns") or []
    confirm = data.get("confirm")
    if disk not in space_manager.DISKS:
        return jsonify({"success": False, "message": f"unknown disk {disk}"}), 400
    if not space_manager.RUN_NAME_RE.match(run or ""):
        return jsonify({"success": False, "message": f"invalid run {run!r}"}), 400
    if not isinstance(subruns, list) or not subruns:
        return jsonify({"success": False, "message": "no subruns selected"}), 400
    # Typed confirmation must match exactly, so a stray click can't delete.
    if confirm != "DELETE":
        return jsonify({"success": False, "message": "confirmation text did not match"}), 400
    out = space_manager.delete_subruns(disk, run, subruns)
    log_event("SPACE_DELETE_SUBRUNS", "disk_space", disk=disk, run=run,
              subruns=",".join(subruns), freed=out["freed_h"],
              ok=out["n_deleted"], failed=out["n_failed"])
    out["success"] = out["n_failed"] == 0
    out["usage"] = space_manager.disk_usage().get(disk, {})
    return jsonify(out)


# --- Automatic SSD clearing (space_watcher) --------------------------------
# On/Off + the free-space buffer for the background watcher that prunes the SSD
# raw staging disk. The watcher re-reads its config file every poll, so a buffer
# change here takes effect without a restart.
#
# The safety model is unchanged and is NOT weakened by anything here: the watcher
# deletes only via space_manager.delete_run, which re-verifies SSD -> HDD -> EOS
# itself and refuses the active run. These endpoints can change WHEN it acts and
# HOW MUCH it frees, never WHAT counts as safe.

SPACE_TMUX = "space_watcher"
SPACE_CONFIG_PATH = f"{BASE_DIR}/config/space_config.json"

# Bounds on the operator-settable buffer. The floor must leave room for the OS and
# still be big enough to be worth acting on; the ceiling stops a typo (e.g. 4000)
# from making the watcher try to delete every run on the disk.
SPACE_MIN_GB = 20
SPACE_MAX_GB = 400


def _space_config():
    with open(SPACE_CONFIG_PATH) as f:
        return json.load(f)


@app.route("/space/auto")
def space_auto_status():
    """Current on/off state + settings of the automatic clearer (read-only)."""
    running = subprocess.run(["tmux", "has-session", "-t", SPACE_TMUX],
                             capture_output=True).returncode == 0
    out = {"running": running, "min_gb": SPACE_MIN_GB, "max_gb": SPACE_MAX_GB}
    try:
        cfg = _space_config()
        out["config"] = {k: cfg.get(k) for k in
                         ("low_water_gb", "target_free_gb", "keep_recent_runs",
                          "min_age_hours", "emergency_gb", "dry_run")}
    except Exception as e:
        out["config"] = None
        out["message"] = f"could not read space config: {e}"
    # Live state published by the watcher (what it last did, and why).
    try:
        with open(f"{BASE_DIR}/config/space_watcher_state.json") as f:
            out["state"] = json.load(f)
    except Exception:
        out["state"] = None
    out["usage"] = space_manager.disk_usage().get("ssd", {})
    return jsonify(out)


@app.route("/space/auto/config", methods=["POST"])
def space_auto_config():
    """Set the free-space buffer. Validated here, not in the browser: the numbers
    decide when data gets deleted, so a hand-crafted POST must not be able to set
    a nonsensical floor."""
    data = request.get_json(silent=True) or {}
    try:
        cfg = _space_config()
    except Exception as e:
        return jsonify({"success": False, "message": f"could not read space config: {e}"}), 500

    try:
        low = float(data.get("low_water_gb", cfg.get("low_water_gb")))
        target = float(data.get("target_free_gb", cfg.get("target_free_gb")))
        emerg = float(data.get("emergency_gb", cfg.get("emergency_gb", low / 2)))
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "buffer values must be numbers"}), 400

    if not (SPACE_MIN_GB <= low <= SPACE_MAX_GB):
        return jsonify({"success": False,
                        "message": f"keep-free must be between {SPACE_MIN_GB} and {SPACE_MAX_GB} GB"}), 400
    if not (SPACE_MIN_GB <= target <= SPACE_MAX_GB):
        return jsonify({"success": False,
                        "message": f"free-up-to must be between {SPACE_MIN_GB} and {SPACE_MAX_GB} GB"}), 400
    if target < low:
        return jsonify({"success": False,
                        "message": "free-up-to must be at least the keep-free floor"}), 400
    # The emergency level drops the newest-run/min-age guards, so it must sit BELOW
    # the floor: at or above it, the guards would effectively never apply.
    if not (SPACE_MIN_GB <= emerg <= SPACE_MAX_GB):
        return jsonify({"success": False,
                        "message": f"emergency level must be between {SPACE_MIN_GB} and {SPACE_MAX_GB} GB"}), 400
    if emerg > low:
        return jsonify({"success": False,
                        "message": "emergency level must be at or below the keep-free floor"}), 400

    # Guard against a buffer that cannot physically be satisfied — the watcher
    # would otherwise delete every eligible run and still report "cannot free".
    try:
        total_gb = space_manager.disk_usage()["ssd"]["total"] / (1024 ** 3)
        if target > 0.9 * total_gb:
            return jsonify({"success": False,
                            "message": (f"free-up-to ({target:.0f} GB) is more than 90% of the "
                                        f"{total_gb:.0f} GB disk — unreachable")}), 400
    except Exception:
        pass

    if "dry_run" in data:
        cfg["dry_run"] = bool(data["dry_run"])
    cfg["low_water_gb"] = low
    cfg["target_free_gb"] = target
    cfg["emergency_gb"] = emerg
    try:
        tmp = SPACE_CONFIG_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(cfg, f, indent=4)
        os.replace(tmp, SPACE_CONFIG_PATH)   # atomic: the watcher may read mid-write
    except Exception as e:
        return jsonify({"success": False, "message": f"could not write config: {e}"}), 500

    log_event("SPACE_AUTO_CONFIG", "disk_space", low_water_gb=low,
              target_free_gb=target, emergency_gb=emerg, dry_run=cfg.get("dry_run"))
    return jsonify({"success": True, "config": cfg,
                    "message": (f"Buffer set: keep {low:g} GB free, free up to {target:g} GB, "
                                f"guards off below {emerg:g} GB")})


@app.route("/space/auto/start", methods=["POST"])
def space_auto_start():
    if not os.path.exists(SPACE_CONFIG_PATH):
        result = subprocess.run([sys.executable, f"{BASE_DIR}/space_config.py"],
                                capture_output=True, text=True)
        if result.returncode != 0:
            return jsonify({"success": False,
                            "message": f"Config generation failed: {result.stderr}"}), 500
    try:
        subprocess.run(["tmux", "kill-session", "-t", SPACE_TMUX], capture_output=True)
        # sys.executable (flask's venv python), not bare "python": the tmux login
        # shell resets PATH and drops the venv.
        subprocess.Popen([
            "tmux", "new-session", "-d", "-s", SPACE_TMUX,
            sys.executable, f"{BASE_DIR}/space_watcher.py", SPACE_CONFIG_PATH
        ])
        log_event("SPACE_AUTO_START", "disk_space")
        return jsonify({"success": True, "message": "Automatic SSD clearing ON"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/space/auto/stop", methods=["POST"])
def space_auto_stop():
    try:
        subprocess.run(["tmux", "kill-session", "-t", SPACE_TMUX], capture_output=True)
        log_event("SPACE_AUTO_STOP", "disk_space")
        return jsonify({"success": True, "message": "Automatic SSD clearing OFF"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/space/restore_scan")
def space_restore_scan():
    """List runs on EOS and how each compares to the local HDD (read-only)."""
    try:
        return jsonify(space_manager.scan_restore())
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/space/restore", methods=["POST"])
def space_restore():
    """Pull runs back from EOS onto the HDD. Auth-gated (POST). Non-destructive:
    only files missing or size-mismatched locally are fetched. Sent one run per
    request by the UI so it can show per-run progress."""
    data = request.get_json(silent=True) or {}
    runs = data.get("runs") or []
    if not isinstance(runs, list) or not runs:
        return jsonify({"success": False, "message": "no runs selected"}), 400
    out = space_manager.restore_runs(runs)
    log_event("SPACE_RESTORE", "disk_space", runs=",".join(runs),
              fetched=out["fetched_h"], ok=out["n_restored"], failed=out["n_failed"])
    out["success"] = out["n_failed"] == 0
    out["usage"] = space_manager.disk_usage().get("hdd", {})
    return jsonify(out)


# ------------------------------------------------------------------ Run mode
# Beam <-> cosmics changeover, and the watcher that does it unattended.
#
# ⚠ Everything here delegates to switch_mode.py --go, which is the ONE changeover
# implementation (it stops the run, allocates the run number, regenerates the config,
# re-triggers, verifies, starts the run and beam_gate, and asserts the applied cfg).
# The GUI must never grow a second, subtly-different copy of that sequence.
MODE_TMUX = "mode_watcher"
MODE_DISARM_FLAG = f"{BASE_DIR}/config/.mode_watcher_disarmed"
VENV_PY = f"{BASE_DIR}/.venv/bin/python"


def _mode_mod():
    """Import mode_watcher lazily so a syntax error there cannot take down the GUI."""
    import importlib
    import mode_watcher
    return importlib.reload(mode_watcher)


@app.route("/mode/status")
def mode_status():
    """Which trigger the DAQ is on, what the beam is doing, and what the watcher would do.

    Read-only and cheap on purpose: it reads /proc and beam_state.json. It deliberately
    does NOT poll an N1081B board — a GUI polling the boards every few seconds is exactly
    the traffic pattern that wedges them (n1081b/CLAUDE.md).
    """
    out = {"watcher_running": subprocess.run(
        ["tmux", "has-session", "-t", MODE_TMUX], capture_output=True).returncode == 0,
        "disarmed": os.path.exists(MODE_DISARM_FLAG)}
    try:
        mw = _mode_mod()
        mode, mdetail = mw.current_mode()
        beam, since, bdetail = mw.beam_view()
        target, reason = mw.decide(mode, beam, since, mw.BEAM_DOWN_MIN, mw.COOLDOWN_MIN)
        st = mw.load_state()
        out.update({
            "mode": mode, "mode_detail": mdetail,
            "beam": beam, "beam_detail": bdetail,
            "seconds_since_pulse": since,
            "would_switch_to": target, "reason": reason,
            "changeover_in_progress": bool(mw.sm.read_changeover_lock()),
            "changeover_holder": mw.sm.read_changeover_lock(),
            "beam_down_min": mw.BEAM_DOWN_MIN, "cooldown_min": mw.COOLDOWN_MIN,
            "last_changeover_at": st.get("last_changeover_at"),
            "last_target": st.get("last_target"),
            "last_result": st.get("last_result"),
        })
    except Exception as e:  # noqa: BLE001
        out["message"] = f"mode_watcher unavailable: {e}"
    return jsonify(out)


def _run_changeover(mode):
    """Background worker: the changeover takes ~60 s, far longer than a request."""
    try:
        subprocess.run([VENV_PY, f"{BASE_DIR}/switch_mode.py", mode, "--go"],
                       cwd=BASE_DIR, capture_output=True, text=True, timeout=900)
    except Exception:  # noqa: BLE001
        pass


@app.route("/mode/switch", methods=["POST"])
def mode_switch():
    """Manual changeover. STOPS THE LIVE RUN — auth-gated like every other POST.

    Returns as soon as the changeover is launched; the UI then polls /mode/status, where
    `changeover_in_progress` is driven by switch_mode's own lock rather than by anything
    this route remembers.
    """
    data = request.get_json(silent=True) or {}
    mode = str(data.get("mode", ""))
    if mode not in ("beam", "cosmics"):
        return jsonify({"success": False, "message": "mode must be 'beam' or 'cosmics'"}), 400
    try:
        if _mode_mod().sm.read_changeover_lock():
            return jsonify({"success": False,
                            "message": "a changeover is already in progress"}), 409
    except Exception:  # noqa: BLE001
        pass
    log_event("MODE_SWITCH", "run_mode", mode=mode, remote_addr=_client_ip())
    threading.Thread(target=_run_changeover, args=(mode,), daemon=True).start()
    return jsonify({"success": True,
                    "message": f"Switching to {mode} — stopping the run, re-triggering, "
                               f"and starting the new one. Takes ~1 min; watch this card."})


@app.route("/mode/watcher", methods=["POST"])
def mode_watcher_toggle():
    """Start/stop the automatic both-ways watcher in its own tmux session."""
    data = request.get_json(silent=True) or {}
    action = str(data.get("action", ""))
    if action == "start":
        subprocess.run(["tmux", "kill-session", "-t", MODE_TMUX], capture_output=True)
        subprocess.Popen(["tmux", "new-session", "-d", "-s", MODE_TMUX,
                          VENV_PY, f"{BASE_DIR}/mode_watcher.py"], cwd=BASE_DIR)
        log_event("MODE_WATCHER", "run_mode", action="start", remote_addr=_client_ip())
        return jsonify({"success": True, "message": "Auto mode-switch watcher started"})
    if action == "stop":
        subprocess.run(["tmux", "kill-session", "-t", MODE_TMUX], capture_output=True)
        log_event("MODE_WATCHER", "run_mode", action="stop", remote_addr=_client_ip())
        return jsonify({"success": True, "message": "Auto mode-switch watcher stopped"})
    if action in ("arm", "disarm"):
        # Keeps the watcher running and logging, but stops it acting. Useful during an
        # intervention when you do not want to lose its state or its log continuity.
        try:
            if action == "disarm":
                open(MODE_DISARM_FLAG, "w").write(
                    f"disarmed via GUI {datetime.now():%Y-%m-%d %H:%M:%S}\n")
            else:
                os.remove(MODE_DISARM_FLAG)
        except FileNotFoundError:
            pass
        except Exception as e:  # noqa: BLE001
            return jsonify({"success": False, "message": str(e)}), 500
        log_event("MODE_WATCHER", "run_mode", action=action, remote_addr=_client_ip())
        return jsonify({"success": True, "message": f"Watcher {action}ed"})
    return jsonify({"success": False, "message": "action must be start/stop/arm/disarm"}), 400


# Process-level start marker.
#
# It has to be written at IMPORT time, not from the __main__ block below:
# flask_app/start_flask.sh launches this with `flask run`, which imports this module
# and never executes __main__, so a line down there would never be written in
# production. Reaching this point also means the module imported cleanly.
#
# But `import app` is NOT the same thing as "the server started" — test harnesses,
# screenshot servers and one-off tooling import this module too, and each of those
# would otherwise forge a restart marker in the audit log (observed: 4 phantom
# STARTs in 90 s). A false restart marker is precisely the confusion this logging
# pass exists to remove, so gate it on actually being the server process.
# FLASK_RUN_FROM_CLI is set by flask.cli before the app module is loaded.
#
# It reuses log_event/daq_events.log rather than opening a logs/flask_server.log:
# daq_events.log is already this process's own log, and adding no new machinery to
# the process that serves every button is the conservative choice. Button auditing
# (STOP_RUN, GAS_SET, ...) is unchanged.
def _log_process_start(launched_by):
    log_event("START", "flask_server", pid=os.getpid(), launched_by=launched_by,
              base_dir=BASE_DIR)


if os.environ.get("FLASK_RUN_FROM_CLI"):
    _log_process_start("flask-cli")

if __name__ == "__main__":
    _log_process_start("socketio.run")
    socketio.run(app, host="0.0.0.0", port=5001)
