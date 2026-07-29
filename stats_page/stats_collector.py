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

sys.path.insert(0, REPO_DIR)
from common_functions import log_event

# Durable event log for the stats_page_watcher process. Lifecycle + push failures /
# recoveries; the per-push payload lives in STATE_PATH below, not here.
STATS_PAGE_EVENT_LOG = os.path.join(REPO_DIR, "logs", "stats_page_watcher.log")


def _log(event, **details):
    log_event(STATS_PAGE_EVENT_LOG, event, 'stats_page', **details)


CONFIG_PATH = os.path.join(REPO_DIR, "config", "stats_page_config.json")
HISTORY_PATH = os.path.join(REPO_DIR, "config", "stats_page_history.json")
# Last-push outcome, for the Flask dashboard's status card (flask_app/daq_status.py
# get_stats_page_watcher_status). Separate from HISTORY_PATH because history keeps
# updating locally even when the EOS push itself is failing (e.g. an expired
# Kerberos ticket) — the dashboard needs to see THAT, not just "the loop is alive".
STATE_PATH = os.path.join(REPO_DIR, "config", "stats_page_state.json")
BEAM_STATE = os.path.join(REPO_DIR, "config", "beam_state.json")
BEAM_CSV_DIR = "/home/mx17/beam_july/slow_control/beam_intensity"
SPS_STATE = os.path.join(REPO_DIR, "config", "sps_state.json")
PAGE_HTML = os.path.join(os.path.dirname(os.path.abspath(__file__)), "page.html")

SCHEMA = 1
HISTORY_MAX = 1440         # ~24 h at one sample per 60 s — the sparkline now draws
                           # the full day, with the headline number quoted from a
                           # highlighted trailing 1 h window rather than this whole span

PROJECTIONS_DIR = os.path.join(REPO_DIR, "projections")
LIVE_PLOT_PATH = os.path.join(PROJECTIONS_DIR, "plots", "progress_live.png")
# Static: run_79 is a fixed reference measurement, so these are built by hand
# (projections/ipc_yield.py) and shipped with the page rather than regenerated.
IPC_PLOT_PATHS = [os.path.join(PROJECTIONS_DIR, "plots", "ipc_yield.png")]

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
    # null = count every run in the ledger, including ones already deleted from
    # disk. This must stay consistent with what the frozen projection was anchored
    # on, or the "against projection" tile compares two different totals.
    "projection_first_run": None,
    # Beam-intensity trace for its own card. The daily CSV is ~1.3 MB and grows all
    # day, so it is tail-read and recomputed on its own slower cadence. 24 h at a
    # 10 min bucket is 144 points — same "full day, quote the last hour" treatment
    # as the trigger-rate history above.
    "beam_history_hours": 24.0,
    "beam_history_bucket_min": 10.0,
    "beam_history_interval_s": 300,
}

_beam_history_cache = {"t": 0.0, "data": None}
_run_kind_cache = {"run": None, "beam_type": None}

RUNS_ROOT = "/home/mx17/beam_july/runs"
REFERENCE_PATH = os.path.join(REPO_DIR, "projections", "reference_maxima.json")

# beam_type in run_config.json -> (kind shown on the page, status level)
RUN_KINDS = {
    "neutrons": ("BEAM", "good"),
    "cosmics": ("COSMICS", "serious"),
}


def run_kind(run_name, running):
    """(label, status level) for the current run.

    Reads beam_type from the run's own config — the same field the statistics split
    on, so the page can never disagree with the ledger about what a run was. Cached
    per run name; it is one small file but this is called every push."""
    if not running:
        return "NOT RUNNING", "critical"
    if not run_name:
        return "OTHER", ""
    if _run_kind_cache["run"] != run_name:
        bt = None
        try:
            with open(os.path.join(RUNS_ROOT, run_name, "run_config.json")) as f:
                bt = (json.load(f).get("beam_type") or "").lower()
        except Exception:
            pass
        _run_kind_cache.update(run=run_name, beam_type=bt)
    return RUN_KINDS.get(_run_kind_cache["beam_type"], ("OTHER", ""))

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
        fr = cfg.get("projection_first_run")
        data = projection_live.summary(first_run=int(fr) if fr else None)
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
            cmd = [sys.executable, "plot_progress.py", "--out", LIVE_PLOT_PATH]
            if cfg.get("projection_first_run"):
                cmd += ["--first-run", str(cfg["projection_first_run"])]
            r = subprocess.run(cmd, cwd=PROJECTIONS_DIR, capture_output=True,
                               text=True, timeout=180)
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


def _tail_lines(path, max_bytes):
    """Last ~max_bytes of a text file as complete lines.

    The beam CSV is one row per poll and reaches ~1.3 MB by end of day. Seeking to
    the tail keeps this O(window) instead of O(day), so the cost does not creep up
    as the day goes on."""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - max_bytes))
        chunk = f.read()
    text = chunk.decode("utf-8", errors="replace")
    lines = text.splitlines()
    return lines[1:] if size > max_bytes else lines[1:]   # drop partial/header line


def beam_history(cfg):
    """[{t, pulses, e10_mean, p_day}] per bucket over the recent window.

    `p_day` is protons/day: the protons actually delivered in the bucket, scaled to
    a full day. That is the figure of merit — it folds pulse intensity and pulse
    rate together, so a machine delivering the same intensity at half the repetition
    rate correctly reads as half the protons. Mean intensity per pulse alone would
    look unchanged.

    Buckets with no pulses report 0 rather than being omitted, so a beam stop reads
    as a drop to the floor instead of being silently compressed out of the trace."""
    now = time.time()
    c = _beam_history_cache
    if c["data"] is not None and now - c["t"] < float(cfg["beam_history_interval_s"]):
        return c["data"]

    hours = float(cfg["beam_history_hours"])
    bucket_s = float(cfg["beam_history_bucket_min"]) * 60.0
    t0 = now - hours * 3600.0
    threshold = _read_json(BEAM_STATE).get("pulse_threshold_e10") or 50.0

    sums, counts = {}, {}
    try:
        # Two days, so a window spanning local midnight is not cut in half.
        for day_offset in (1, 0):
            day = datetime.fromtimestamp(now - day_offset * 86400).strftime("%Y-%m-%d")
            path = os.path.join(BEAM_CSV_DIR, f"beam_intensity_{day}.csv")
            if not os.path.exists(path):
                continue
            for line in _tail_lines(path, 4_000_000):
                parts = line.split(",")
                if len(parts) < 3:
                    continue
                try:
                    ts, e10 = float(parts[1]), float(parts[2])
                except ValueError:
                    continue
                if ts < t0 or e10 <= threshold:
                    continue
                b = int((ts - t0) // bucket_s)
                sums[b] = sums.get(b, 0.0) + e10
                counts[b] = counts.get(b, 0) + 1
    except Exception as e:
        print(f"[stats_page] Beam history failed: {e}", file=sys.stderr)
        return c["data"]

    n_buckets = max(1, int(round(hours * 3600.0 / bucket_s)))
    per_day = 86400.0 / bucket_s          # bucket -> full day
    out = [{"t": round(t0 + (b + 0.5) * bucket_s),
            "pulses": counts.get(b, 0),
            "e10_mean": round(sums[b] / counts[b], 1) if counts.get(b) else 0.0,
            # e10 units in the CSV, so x1e10 to get protons.
            "p_day": round(sums.get(b, 0.0) * 1e10 * per_day)}
           for b in range(n_buckets)]
    c.update(t=now, data=out)
    return out


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

    run_name = _field(daq, "Run")
    kind, kind_level = run_kind(run_name, dream.get("status") == "RUNNING")
    payload["run"] = {
        "name": run_name,
        "status": dream.get("status"),
        "beam_type": _run_kind_cache.get("beam_type"),
        "kind": kind,
        "kind_level": kind_level,
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

    payload["beam_history"] = beam_history(cfg)
    # Best-achieved references the page grades against. Regenerate with
    # projections/reference_maxima.py; they only ever ratchet upward.
    payload["reference"] = _read_json(REFERENCE_PATH) or None
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
        # BEAM / COSMICS / OTHER / NOT RUNNING — so the trigger-rate sparkline can
        # colour each stretch by what was actually running instead of implying one
        # continuous beam rate across mode-watcher's beam<->cosmics changeovers.
        "kind": (payload.get("run") or {}).get("kind"),
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


def _write_state(connected, msg):
    """Persist the last push outcome, atomically, for the dashboard card."""
    try:
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"connected": connected, "msg": msg, "timestamp": datetime.now().isoformat()}, f)
        os.replace(tmp, STATE_PATH)
    except Exception as e:
        print(f"[stats_page] Could not persist state: {e}", file=sys.stderr)


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
    _log('START', eos_www_dir=cfg['eos_www_dir'], interval=f'{interval:g}s')

    ok, msg = ensure_eos_dir(cfg)
    if not ok:
        print(f"[stats_page] Could not create {cfg['eos_www_dir']}: {msg}", file=sys.stderr)
        _log('EOS_MKDIR_FAILED', eos_www_dir=cfg['eos_www_dir'], detail=msg)

    history = load_history()
    fails = 0
    first = True
    while True:
        payload = collect(cfg)
        history = append_history(payload, history)
        # Re-upload the page itself on the first push of a session, so an edit to
        # page.html goes live on restart without a separate deploy step.
        ok, msg = push_eos(cfg, payload, history, with_page=first, png=take_fresh_png())
        _write_state(ok, msg)
        if ok:
            if fails:
                print(f"[stats_page] Recovered after {fails} failed push(es)")
                _log('PUSH_RECOVERED', after_failures=fails)
            fails, first = 0, False
        else:
            fails += 1
            # Noisy for the first few, then back off the logging — a long beam-off
            # network outage should not fill the pane. The event log follows the same
            # back-off for the same reason.
            if fails <= 3 or fails % 20 == 0:
                print(f"[stats_page] Push failed ({fails}): {msg}", file=sys.stderr)
                _log('PUSH_FAILED', consecutive=fails, detail=str(msg)[:300])
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
