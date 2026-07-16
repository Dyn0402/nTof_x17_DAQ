# N1081B trigger diagram — live GUI tab + standalone HTML

A live, self-updating diagram of all six N1081B trigger boards (.240–.245),
drawn like the physical CAEN-red modules: one scrolling page per module, sections
A–D with their LEMO inputs/outputs, function/mode, thresholds, monostables,
gate&delay, signal routing, and a per-channel table. The **design model** (what
each board is *meant* to do) is overlaid with the **newest live read-back** and the
**currently-applied scan**.

## Where it shows up

- **DAQ GUI → "Trigger" tab** (`flask_app`). Auto-refreshes every 15 s while open.
- **Standalone file** `~/Documents/ntof_trigger_logic/ntof_trigger_diagram.html` —
  a frozen, self-contained snapshot that opens with no server.

Both render from the **same** code, so they can't drift.

## How it stays current (no extra board traffic)

The diagram never polls the boards itself. It reads, in priority order:

1. the newest per-sub-run `n1081b_config.json` that `daq_control` already drops in
   each run dir (via `poll_modules`), else
2. the newest manual `n1081b/snapshots/dump_*.json`, else
3. design-only (no read-back).

The scan watcher (`n1081b_scan_watcher.py`) publishes the active scan to
`config/n1081b_scan_active.json` on every apply/switch (and clears it on exit), so
the banner + the amber "driven by scan" channels update as the DAQ modulates the
boards per sub-run.

## Pieces

| File | Role |
|------|------|
| `n1081b/n1081b_module_map.py` | The design model of all 6 boards + `build_state(snapshot, scan_active)` that merges live over design. **Single source of truth** for the board knowledge (derived from `~/Documents/ntof_trigger_logic/TRIGGER_SETUP_2026-07.md`). |
| `flask_app/static/n1081b_diagram.js` | Self-contained renderer (`window.N1081B.render(state, root)`), injects its own CSS. |
| `flask_app/app.py` → `/n1081b/state` | Finds the newest snapshot, loads the scan-active file, returns `build_state(...)`. |
| `flask_app` templates | "Trigger" nav tab (`base.html`) + `#trigger` pane & fetch loop (`index.html`). |
| `n1081b/export_trigger_diagram.py` | Writes the standalone HTML (inlines the renderer + a `const STATE`). |

## Updating the design model

Board roles/routing changed? Edit the `_moduleN()` functions in
`n1081b_module_map.py` (they mirror TRIGGER_SETUP §0.5 / the fix list). The live
overlay always reflects whatever the boards actually read back, so mismatches
between design and live show up as red/⚠ in the diagram — that's a feature.

## Refreshing the standalone file

```bash
.venv/bin/python n1081b/export_trigger_diagram.py                    # newest snapshot
.venv/bin/python n1081b/export_trigger_diagram.py --snapshot <dump>  # a specific dump
```

To keep the docs copy fresh automatically, run that on a cron (or ask to hook it
into `daq_control`'s per-sub-run snapshot step). The GUI tab is already live and
needs no cron.

## Legend

green ring = live ON · dashed = design-only (no read-back) · red = off/mismatch/veto
· amber = driven by active scan · ⃠ = inverted · `*` on a value = intended (design)
number, no live read-back.
