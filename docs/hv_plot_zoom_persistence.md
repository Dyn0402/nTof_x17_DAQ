# HV plot: preserve zoom/pan across auto-refresh (implemented)

**Status (2026-06-30):** Implemented and deployed here (commit `8a36c5a`); pending live
verification on the DAQ machine. Once confirmed working, port the same change to
`Cosmic_Bench_DAQ_Control` — see its `docs/hv_plot_zoom_persistence.md` for the exact edit.

## What changed
`flask_app/templates/index.html` → `updateHVPlots()` previously called `Plotly.newPlot`
on every 5 s auto-refresh, which rebuilt the plot and discarded the user's zoom/pan.
Now it uses `Plotly.react` with a `uirevision` keyed to the selected `run/subrun`:
- zoom/pan/legend state is preserved across the 5 s refreshes (same key), and
- the view resets only when a different subrun is selected (new key).
Also added `hovermode: 'x unified'` (all channels at a hovered timestamp) and a
`{ responsive: true }` config. No change of plotting library — Plotly already provides
the hover behavior we like.

## Deploy note
`flask run` caches Jinja templates in memory (no auto-reload), so template edits require
restarting the `flask_server` process before they are served.
