# GUI refactor notes

Observations collected while adding the Pedestal QA watcher/tab (2026-07-02).
None of these are urgent; they're suggestions for a future cleanup pass of the
flask GUI.

## Layout / UX

- **Status cards are outgrowing the overview.** Seven cards now (one already
  compact). Consider one horizontal status strip of colored badges — name +
  state each, click to expand details (run/subrun/rates) — instead of a grid of
  cards. Would free most of the overview's vertical space.
- **Button rows keep growing.** Start/Stop pairs for processor, QA, backup
  (and now a Ped QA toggle living in the Pedestals tab). A single toggle per
  watcher (like the Monitoring on/off button and the new Ped QA toggle) halves
  the button count; grouping watcher controls into one "Watchers" panel would
  declutter further.
- The Online QA tab's Run/Subrun/Detector/Subdir cascade requires 3-4 clicks
  before showing anything. Auto-selecting the newest run/subrun (as the
  Pedestals tab now does) would make the common case zero-click.

## Code structure

- `templates/index.html` is ~1400 lines with all JS inline. Split into
  `static/js/overview.js`, `qa.js`, `pedestals.js`, `analysis.js`, `sysmon.js`.
- `showPopup()` exists, but the take-pedestals, git-reset, and run-config-py
  handlers still build their popup divs by hand (copy-paste of the same ~20
  lines). Use `showPopup` everywhere.
- **Dead code:** in `updateStatus()`, `data["daq_control"]` indexes an *array*
  by string → always `undefined`, so the subrun-change detection for the HV
  dropdown never fires (loadSubruns' 5s poll masks it). Also several large
  commented-out blocks in `index.html` and `app.py` that can be deleted.
- `app.py` start/stop routes for processor/QA/backup/ped-QA are four copies of
  the same pattern (regen config → kill tmux → new tmux). One generic
  `/watcher/<name>/start|stop` route driven by a small registry
  (config script, config json, tmux name) would collapse ~100 lines.
- Watcher launch uses bare `"python"` inside `tmux new-session`. The tmux
  server env doesn't reliably carry the venv PATH (login shells reset it).
  `start_ped_qa` now uses `sys.executable`; the other watcher routes should
  too.
- `daq_status.py` functions all share the capture-pane + reversed-line-scan
  pattern; a small helper (session name, rules list) would shrink the file a
  lot. (Done ad hoc per watcher today.)

## Performance

- `/status` is polled every **1 s** and each call shells out to
  `tmux capture-pane` 7×. Plus separate 5 s polls for run name, events,
  subruns, HV data and 2 s for system stats. Consider a single `/overview_poll`
  endpoint (one round trip) at 2–3 s, or pushing via the already-loaded
  socket.io.
- Status cards are rebuilt with `innerHTML` every second even when nothing
  changed — causes flicker/GC churn. Diff before writing, or only update
  changed cards.

## Security / robustness (LAN tool, so low priority)

- `/serve_png` serves any file from any absolute directory passed in the query
  string (path traversal by design). Restrict to a whitelist of roots
  (analysis dir, runs dir, pedestals dir).
- `BASE_DIR` is hardcoded in `app.py` (with the old laptop path commented out);
  derive from `__file__` like the watchers do.
- `hv_creds.txt` sits untracked in the repo root — keep it out of any future
  git add -A (add to .gitignore).
