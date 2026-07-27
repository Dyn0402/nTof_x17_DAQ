#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on July 27 2026
Created in PyCharm
Created as nTof_x17_DAQ/stats_page/stats_collector.py

@author: Dylan Neff, dylan

Collector + pusher behind the public statistics page (see stats_page/README.md).

Builds a small JSON summary of the current run — events, trigger rate, sub-run
progress, beam state — and copies it into the EOS directory behind the webeos site
https://dylan-neff.web.cern.ch/x17/, which serves it outside CERN. Push-only:
nothing here opens a port and nothing outside can reach this machine, so no
inbound firewall hole is involved.

Read-only with respect to the DAQ. Every number comes from something that is
already computed and published:
  * http://localhost:5001/status  — the Flask GUI's own status endpoint (GET is
    never auth-gated), which already does the run/sub-run/event bookkeeping. We
    read it rather than re-deriving it, so the page can never disagree with the GUI.
  * config/beam_state.json, config/sps_state.json — the beam and SPS watchers.

Safe to start and stop at any time; it owns no hardware and commands nothing.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(REPO_DIR, "config", "stats_page_config.json")
HISTORY_PATH = os.path.join(REPO_DIR, "config", "stats_page_history.json")
BEAM_STATE = os.path.join(REPO_DIR, "config", "beam_state.json")
SPS_STATE = os.path.join(REPO_DIR, "config", "sps_state.json")
PAGE_HTML = os.path.join(os.path.dirname(os.path.abspath(__file__)), "page.html")

SCHEMA = 1
HISTORY_MAX = 240          # ~4 h at one sample per 60 s

PROJECTIONS_DIR = os.path.join(REPO_DIR, "projections")
LIVE_PLOT_PATH = os.path.join(PROJECTIONS_DIR, "plots", "progress_live.png")
# Static: run_79 is a fixed reference measurement, so these are built by hand
# (projections/ipc_yield.py) and shipped with the page rather than regenerated.
IPC_PLOT_PATHS = [os.path.join(PROJECTIONS_DIR, "plots", n)
                  for n in ("ipc_yield.png", "ipc_yield_linear.png",
                            "run82_comb.png")]

DEFAULTS = {
    "status_url": "http://localhost:5001/status",
    "eos_url": "root://eosuser.cern.ch",
    "eos_www_dir": "/eos/user/d/dneff/www/x17",
    "interval_s": 60,
    "timeout_s": 10,
    # Cumulative statistics + projection. Far slower-moving than the live status
    # (it only changes when a sub-run completes, ~hourly) and much more expensive
    # to compute — it imports pandas and walks every run directory — so it runs on
    # its own long cadence rather than every push.
    "projection_enabled": True,
    "projection_interval_s": 600,
    "projection_first_run": 79,
}

# {"t": last computed, "data": block, "png": path if freshly rendered}
_projection_cache = {"t": 0.0, "data": None, "png": None}

# Which tmux services are worth showing publicly, and the label to show them under.
# Deliberately a subset: the point is "is the DAQ taking data", not a control panel.
PUBLIC_SERVICES = [
    ("dream_daq", "DREAM DAQ"),
    ("daq_control", "Run control"),
    ("hv_control", "HV"),
    ("processor_watcher", "Processing"),
    ("backup_watcher", "Backup"),
    ("beam_watcher", "Beam"),
]

# Flask's bootstrap colour -> the dataviz status level the page paints the pill with.
LEVEL_FROM_COLOR = {
    "success": "good",
    "info": "",
    "warning": "warning",
    "danger": "critical",
    "secondary": "",
}


def load_config(path=CONFIG_PATH):
    cfg = dict(DEFAULTS)
    try:
        with open(path) as f:
            cfg.update(json.load(f))
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[stats_page] Bad config {path}: {e}", file=sys.stderr)
    return cfg


def _read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def _field(entry, label):
    """Value of a named field in one /status entry, or None."""
    for f in (entry or {}).get("fields", []):
        if f.get("label") == label:
            return f.get("value")
    return None


def _num(value, cast=float):
    """'25.39Hz' -> 25.39, '11935' -> 11935, junk -> None."""
    if value is None:
        return None
    try:
        return cast(str(value).strip().rstrip("Hz").strip())
    except (TypeError, ValueError):
        return None


def _fetch_status(url, timeout):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def projection_block(cfg, force=False):
    """Cumulative statistics + the frozen projection, recomputed at most every
    `projection_interval_s`. Returns (data, fresh_png_path_or_None).

    Wrapped end to end: this is the one part of the payload that imports pandas and
    shells out to matplotlib, and a live status page must not go dark because a
    plotting dependency broke. On failure the last good block is kept and the page
    simply shows slightly older statistics."""
    if not cfg.get("projection_enabled", True):
        return None, None

    now = time.time()
    if not force and _projection_cache["data"] is not None \
            and now - _projection_cache["t"] < float(cfg["projection_interval_s"]):
        return _projection_cache["data"], None

    try:
        if PROJECTIONS_DIR not in sys.path:
            sys.path.insert(0, PROJECTIONS_DIR)
        import live as projection_live
        data = projection_live.summary(first_run=int(cfg["projection_first_run"]))
    except Exception as e:
        print(f"[stats_page] Projection summary failed: {e}", file=sys.stderr)
        return _projection_cache["data"], None

    # The plot only changes when a sub-run completes (~hourly) or a new projection
    # is frozen — not on the 10-minute recompute cadence. Re-rendering and
    # re-uploading an identical 135 kB PNG six times an hour would burn EOS writes
    # and pile up versioned copies for nothing, so gate it on the inputs.
    stamp = (data.get("last_subrun_end"), (data.get("projection") or {}).get("created"))
    png = None
    if stamp != _projection_cache.get("stamp") or not os.path.exists(LIVE_PLOT_PATH):
        try:
            os.makedirs(os.path.dirname(LIVE_PLOT_PATH), exist_ok=True)
            r = subprocess.run(
                [sys.executable, "plot_progress.py", "--out", LIVE_PLOT_PATH,
                 "--first-run", str(cfg["projection_first_run"])],
                cwd=PROJECTIONS_DIR, capture_output=True, text=True, timeout=180)
            if r.returncode == 0 and os.path.exists(LIVE_PLOT_PATH):
                png = LIVE_PLOT_PATH
                _projection_cache["stamp"] = stamp
            else:
                print(f"[stats_page] Plot failed: {(r.stderr or r.stdout).strip()[:200]}",
                      file=sys.stderr)
        except Exception as e:
            print(f"[stats_page] Plot failed: {e}", file=sys.stderr)

    _projection_cache.update(t=now, data=data, png=png)
    return data, png


def collect(cfg):
    """Build the payload. Every source is optional — a missing or broken one costs
    its own fields, never the whole push."""
    now = datetime.now()
    payload = {
        "schema": SCHEMA,
        "generated": now.isoformat(timespec="seconds"),
        "generated_epoch": time.time(),
        "run": {},
        "triggers": {},
        "beam": {},
        "sps": {},
        "tracks": None,      # placeholder — see README "Tracks"
        "services": [],
    }

    try:
        status = _fetch_status(cfg["status_url"], cfg["timeout_s"])
    except Exception as e:
        payload["error"] = f"status endpoint unreachable: {e}"
        status = []

    by_name = {e.get("name"): e for e in status}
    dream = by_name.get("dream_daq", {})
    daq = by_name.get("daq_control", {})

    subrun_idx = subrun_total = None
    subrun = _field(dream, "Subrun")            # "2/8"
    if subrun and "/" in subrun:
        a, _, b = subrun.partition("/")
        subrun_idx, subrun_total = _num(a, int), _num(b, int)

    payload["run"] = {
        "name": _field(daq, "Run"),
        "status": dream.get("status"),
        "subrun": _field(daq, "Subrun"),
        "subrun_idx": subrun_idx,
        "subrun_total": subrun_total,
        "progress": _field(dream, "Progress"),   # "23m / 2h00m"
        "subrun_elapsed": _field(dream, "Run Time"),
    }
    payload["triggers"] = {
        "events_run": dream.get("run_events"),
        "events_subrun": _num(_field(dream, "Subrun Events"), int),
        "rate_hz": _num(_field(dream, "Int Rate")),
    }

    for name, label in PUBLIC_SERVICES:
        entry = by_name.get(name)
        if not entry:
            continue
        payload["services"].append({
            "name": label,
            "status": entry.get("status", "?"),
            "level": LEVEL_FROM_COLOR.get(entry.get("color"), ""),
        })

    beam = _read_json(BEAM_STATE)
    payload["beam"] = {
        # beam_on is tri-state: null means UNKNOWN (watcher transient), not OFF.
        "on": beam.get("beam_on"),
        "pulses_10min": beam.get("pulses_10min"),
        "protons_10min_e10": beam.get("protons_10min_e10"),
        "last_pulse_e10": beam.get("last_pulse_e10"),
        "seconds_since_pulse": beam.get("seconds_since_pulse"),
    }

    sps = _read_json(SPS_STATE)
    payload["sps"] = {                            # note: never the timeline arrays
        "spill_on": sps.get("spill_on"),
        "spills_10min": sps.get("spills_10min"),
        "last_extracted_e10": sps.get("last_extracted_e10"),
        "supercycle_period_s": sps.get("supercycle_period_s"),
    }

    payload["stats"], _ = projection_block(cfg)

    return payload


def take_fresh_png():
    """The plot path if it was re-rendered since the last call, else None. Clearing
    it here means the (large) PNG is uploaded only when it actually changed, not on
    every 60 s push."""
    png = _projection_cache.get("png")
    _projection_cache["png"] = None
    return png


def load_history():
    try:
        with open(HISTORY_PATH) as f:
            h = json.load(f)
        return h if isinstance(h, list) else []
    except Exception:
        return []


def append_history(payload, history):
    """Roll one sample into the history used by the sparkline. The webeos site is a
    plain file drop with nowhere to keep state, so the authoritative history lives
    here and gets published alongside the payload."""
    history = list(history)
    history.append({
        "t": payload.get("generated_epoch"),
        "rate": (payload.get("triggers") or {}).get("rate_hz"),
        "events": (payload.get("triggers") or {}).get("events_run"),
    })
    history = history[-HISTORY_MAX:]
    try:
        tmp = HISTORY_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(history, f)
        os.replace(tmp, HISTORY_PATH)     # atomic; a torn history file breaks the page
    except Exception as e:
        print(f"[stats_page] Could not persist history: {e}", file=sys.stderr)
    return history


def _xrdcp(local_path, remote_path, cfg):
    """Copy one file to EOS, overwriting. Returns (ok, message)."""
    # Double slash is required: root://host//abs/path. A single slash makes xrootd
    # read the path as relative and reject it outright.
    dest = cfg["eos_url"].rstrip("/") + "//" + remote_path.lstrip("/")
    try:
        r = subprocess.run(["xrdcp", "-f", "-s", local_path, dest],
                           capture_output=True, text=True, timeout=cfg["timeout_s"] * 3)
    except FileNotFoundError:
        return False, "xrdcp not installed"
    except subprocess.TimeoutExpired:
        return False, f"xrdcp timed out copying {os.path.basename(remote_path)}"
    if r.returncode != 0:
        return False, f"xrdcp {os.path.basename(remote_path)}: {(r.stderr or r.stdout).strip()[:200]}"
    return True, "ok"


def push_eos(cfg, payload, history, with_page=True, png=None):
    """Publish to the CERN webeos site by copying the data (and, on the first push
    of a session, the page itself) into its EOS www directory.

    Needs a valid Kerberos ticket — the same keytab-seeded one the backup watcher
    uses. Writes are not atomic on EOS: a reader can catch a partial data.json and
    will simply retry on its next poll, which is why the page tolerates a failed
    fetch rather than blanking."""
    www = cfg["eos_www_dir"].rstrip("/")
    tmpdir = tempfile.mkdtemp(prefix="stats_page_")
    try:
        data_path = os.path.join(tmpdir, "data.json")
        with open(data_path, "w") as f:
            json.dump({"latest": payload, "history": history}, f)
        ok, msg = _xrdcp(data_path, f"{www}/data.json", cfg)
        if not ok:
            return False, msg
        if with_page:
            page_path = os.path.join(tmpdir, "index.html")
            with open(PAGE_HTML) as src, open(page_path, "w") as dst:
                dst.write("<!doctype html>\n" + src.read())
            ok, msg = _xrdcp(page_path, f"{www}/index.html", cfg)
            if not ok:
                return False, msg
            for p in IPC_PLOT_PATHS:
                if not os.path.exists(p):
                    continue
                ok, msg = _xrdcp(p, f"{www}/{os.path.basename(p)}", cfg)
                if not ok:
                    print(f"[stats_page] IPC plot upload failed: {msg}", file=sys.stderr)
        if png and os.path.exists(png):
            ok, msg = _xrdcp(png, f"{www}/progress.png", cfg)
            if not ok:
                # A failed plot upload leaves the previous one in place; the data
                # is already published, so this is a warning, not a failed push.
                print(f"[stats_page] Plot upload failed: {msg}", file=sys.stderr)
        return True, f"published to {www}"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def ensure_eos_dir(cfg):
    """mkdir -p the www subdirectory. EOS FUSE cannot mkdir, so go through xrdfs."""
    try:
        r = subprocess.run(
            ["xrdfs", cfg["eos_url"].replace("root://", "").rstrip("/"),
             "mkdir", "-p", cfg["eos_www_dir"]],
            capture_output=True, text=True, timeout=cfg["timeout_s"] * 3)
    except Exception as e:
        return False, str(e)
    # Already-exists is success for our purposes.
    if r.returncode != 0 and "exists" not in (r.stderr + r.stdout).lower():
        return False, (r.stderr or r.stdout).strip()[:200]
    return True, "ok"


def render_preview(payload, out_path, history=None):
    """Write a standalone copy of the page with a snapshot inlined, so the layout
    can be eyeballed in a browser without deploying anything."""
    with open(PAGE_HTML) as f:
        html = f.read()
    snapshot = {"latest": payload, "history": history or []}
    inject = ("<script>window.__PREVIEW__ = "
              + json.dumps(snapshot) + ";</script>\n")
    html = html.replace("<script>\n(function ()", inject + "<script>\n(function ()", 1)
    with open(out_path, "w") as f:
        f.write("<!doctype html>\n" + html)
    return out_path


def run_blocking(cfg):
    interval = float(cfg["interval_s"])
    print(f"[stats_page] Publishing to {cfg['eos_www_dir']} every {interval:g}s")

    ok, msg = ensure_eos_dir(cfg)
    if not ok:
        print(f"[stats_page] Could not create {cfg['eos_www_dir']}: {msg}", file=sys.stderr)

    history = load_history()
    fails = 0
    first = True
    while True:
        payload = collect(cfg)
        history = append_history(payload, history)
        # Re-upload the page itself on the first push of a session, so an edit to
        # page.html goes live on restart without a separate deploy step.
        ok, msg = push_eos(cfg, payload, history, with_page=first, png=take_fresh_png())
        if ok:
            if fails:
                print(f"[stats_page] Recovered after {fails} failed push(es)")
            fails, first = 0, False
        else:
            fails += 1
            # Noisy for the first few, then back off the logging — a long beam-off
            # network outage should not fill the pane.
            if fails <= 3 or fails % 20 == 0:
                print(f"[stats_page] Push failed ({fails}): {msg}", file=sys.stderr)
        time.sleep(interval)


def main():
    ap = argparse.ArgumentParser(description="Publish DAQ statistics to the public page.")
    ap.add_argument("--once", action="store_true", help="collect and publish a single time, then exit")
    ap.add_argument("--dry-run", action="store_true", help="print the payload, publish nothing")
    ap.add_argument("--html", metavar="PATH", help="render a standalone preview page and exit")
    ap.add_argument("--config", default=CONFIG_PATH)
    args = ap.parse_args()

    cfg = load_config(args.config)
    payload = collect(cfg)

    if args.html:
        # Use whatever real history we have; synthesise nothing.
        path = render_preview(payload, args.html, history=load_history())
        print(f"[stats_page] Preview written to {path}")
        return

    if args.dry_run:
        print(json.dumps(payload, indent=2))
        return

    if args.once:
        ok, msg = ensure_eos_dir(cfg)
        if not ok:
            print(f"[stats_page] mkdir failed: {msg}", file=sys.stderr)
        history = append_history(payload, load_history())
        ok, msg = push_eos(cfg, payload, history, with_page=True, png=take_fresh_png())
        print(f"[stats_page] {'OK' if ok else 'FAILED'}: {msg}")
        sys.exit(0 if ok else 1)

    run_blocking(cfg)


if __name__ == "__main__":
    main()
