#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
export_trigger_diagram — write a SELF-CONTAINED HTML snapshot of the N1081B trigger
diagram to ~/Documents/ntof_trigger_logic/ntof_trigger_diagram.html.

Same picture as the DAQ-GUI "Trigger" tab, but frozen to a single file that opens
with no server: it inlines the shared renderer (flask_app/static/n1081b_diagram.js)
and a ``const STATE = {...}`` built by n1081b_module_map.build_state, so the two
views can never drift.

Live overlay source (no board access of its own — reads files only):
  * ``--snapshot PATH``            explicit dump / n1081b_config.json, else
  * newest snapshots/dump_*.json   (manual dumps), else
  * design-only (no read-back).

Usage:
    .venv/bin/python n1081b/export_trigger_diagram.py               # auto snapshot
    .venv/bin/python n1081b/export_trigger_diagram.py --snapshot n1081b/snapshots/dump_2026-07-11.json
    .venv/bin/python n1081b/export_trigger_diagram.py --out /tmp/diagram.html

Run it from a DAQ cron / the backup watcher to keep the docs copy fresh.
"""
import argparse
import glob
import json
import os
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
SNAP_DIR = os.path.join(HERE, "snapshots")
RENDERER_JS = os.path.join(REPO_ROOT, "flask_app", "static", "n1081b_diagram.js")
SCAN_ACTIVE_PATH = os.path.join(REPO_ROOT, "config", "n1081b_scan_active.json")
DEFAULT_OUT = os.path.expanduser("~/Documents/ntof_trigger_logic/ntof_trigger_diagram.html")

import sys
sys.path.insert(0, HERE)
from n1081b_module_map import build_state  # noqa: E402


def newest_snapshot():
    """Newest run snapshot (if the runs tree is reachable) or manual dump, else None."""
    cands = []
    try:  # per-sub-run snapshots, if the data tree is mounted
        from run_config_beam import BASE_DATA_DIR
        run_dir = f"{BASE_DATA_DIR}runs"
        for p in glob.glob(os.path.join(run_dir, "*", "*", "n1081b_config.json")):
            cands.append((os.path.getmtime(p), p))
    except Exception:
        pass
    for p in glob.glob(os.path.join(SNAP_DIR, "dump_*.json")):
        try:
            cands.append((os.path.getmtime(p), p))
        except OSError:
            pass
    if not cands:
        return None
    cands.sort(reverse=True)
    return cands[0][1]


HTML_TEMPLATE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>n_TOF X17 — N1081B Trigger Diagram</title>
<style>
  html,body {{ margin:0; background:#0e1116; color:#e2e8f0;
    font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif; }}
  .wrap {{ max-width:1180px; margin:0 auto; padding:1rem 1.1rem 3rem; }}
  h1 {{ font-size:1.15rem; font-weight:700; margin:.2rem 0 .1rem; }}
  h1 .dot {{ display:inline-block; width:9px; height:9px; border-radius:50%;
    background:#c02434; box-shadow:0 0 8px #c02434; margin-right:.5rem; vertical-align:middle; }}
  .meta {{ color:#8b96a5; font-size:.78rem; margin-bottom:.9rem; font-family:ui-monospace,monospace; }}
</style>
</head><body>
<div class="wrap">
  <h1><span class="dot"></span>n_TOF X17 — N1081B Trigger Diagram</h1>
  <div class="meta">standalone snapshot · generated {generated} · live overlay: {srcpath}</div>
  <div id="n1081b-diagram"></div>
</div>
<script>
const STATE = {state_json};
</script>
<script>
{renderer_js}
</script>
<script>
window.N1081B.render(STATE, document.getElementById("n1081b-diagram"));
</script>
</body></html>
"""


def main():
    ap = argparse.ArgumentParser(description="Export the N1081B trigger diagram to a self-contained HTML file.")
    ap.add_argument("--snapshot", help="explicit snapshot/dump JSON (default: newest available)")
    ap.add_argument("--out", default=DEFAULT_OUT, help=f"output HTML path (default: {DEFAULT_OUT})")
    args = ap.parse_args()

    snap_path = args.snapshot or newest_snapshot()
    snapshot = None
    if snap_path and os.path.exists(snap_path):
        with open(snap_path) as f:
            snapshot = json.load(f)

    scan_active = None
    if os.path.exists(SCAN_ACTIVE_PATH):
        try:
            with open(SCAN_ACTIVE_PATH) as f:
                sa = json.load(f)
            if sa.get("active"):
                scan_active = sa
        except Exception:
            pass

    src_meta = {
        "path": os.path.relpath(snap_path, REPO_ROOT) if snap_path else None,
        "kind": "dump" if snap_path else None,
        "polled_at": (datetime.fromtimestamp(os.path.getmtime(snap_path)).strftime("%Y-%m-%d %H:%M:%S")
                      if snap_path and os.path.exists(snap_path) else None),
    }
    state = build_state(snapshot, scan_active, source_meta=src_meta)

    with open(RENDERER_JS) as f:
        renderer_js = f.read()

    html = HTML_TEMPLATE.format(
        generated=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        srcpath=(snap_path or "design only (no board read-back)"),
        state_json=json.dumps(state, default=str),
        renderer_js=renderer_js,
    )
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w") as f:
        f.write(html)
    online = sum(1 for m in state["modules"] if m.get("online"))
    print(f"wrote {args.out}  ({len(html)//1024} KB, {online}/{len(state['modules'])} modules with live read-back, "
          f"source: {snap_path or 'design-only'})")


if __name__ == "__main__":
    main()
