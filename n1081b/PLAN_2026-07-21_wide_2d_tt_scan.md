# PLAN 2026-07-21 — wide-plastic 2D scan **with time-tags** (07-17 detail × 07-19 settings)

**Goal.** Re-run the 07-17-style scan — which recorded per-tag **timestamps** (TT) — but at
the **current 07-19 front-end** (Y88-equalized plastic HV, half-MIP walls) and over the
**07-19 wide plastic range** (−10 … −250 mV). This is the only way to regenerate the
time-resolved figures at the present configuration.

## 1. Why this run is necessary

The 07-19 wide ladder (`threshold_ladder/2026-07-19_*_wide_*`) has the right settings and
range but writes **only `config.json` + `points.jsonl`** — scaler rates, no timestamps,
and no second (wall) axis. So from it we can build rate-vs-threshold curves and nothing
else. The five figures in `analysis/rate_scan_2d/night_0717/figures/` need data it does
not contain:

| figure | needs | in 07-19 ladder? |
|---|---|---|
| `heat_singles`, `heat_doubles_total`, `heat_doubles_tail` | 2D wall × plastic grid | ✗ 1D only |
| `profiles`, `inwindow_frac` | per-tag timestamps (TT) | ✗ no TT files |

`rate_scan_2d.py` produces **both** (grid + `tt_pass{AC,BD}.csv`), which is why it is the
tool for this. Note the 07-19 artifact's own flash-profile figure had to fall back on the
07-17 TT run for exactly this reason.

---

## 2. ⛔ HARD BLOCKER — trigger mode

`rate_scan_2d.py` enforces `SAFE_MODES = {"flash", "flash_random"}` and will **abort** with
`RuntimeError: trigger mode '...' not in safe set` otherwise (`rate_scan_2d.py:119`).

**Current mode is `scint(singles+ps)`** (set 2026-07-21 12:16:10 — run_64 is live).
The scan **cannot run now.**

This guard is correct, not an obstacle to work around: in scint modes **M1 (walls) and M2
(plastics) _are_ the physics trigger**, and this scan rewrites their thresholds at every
grid point. Running it during run_64 would corrupt the physics trigger. In
`flash`/`flash_random`, M1/M2 are outside the trigger path, so the scan is parasitic-safe
(validated 2026-07-14/15).

**Requirement:** a window where the trigger can sit in `flash_random` — i.e. between
physics runs, or a dedicated slot. Budget ~3.5 h (§5).

---

## 3. Preconditions checklist

- [ ] **Trigger mode** switched to `flash_random` (`trigger_mode.py flash_random`). Record
      the current mode first so it can be restored (see `n1081b/RUN_MODES_2026-07.md`).
- [ ] **No live physics run** depending on the scint trigger; `daq_control` state noted.
- [ ] **Board access clear** — check `config/n1081b_access/` for `*.holder.json` /
      `*.quarantine.json`. As of writing there are only `.lock` files (no holder, no
      quarantine), i.e. boards free. `poll_modules` and `n1081b_scan_watcher` talk to
      boards on their own schedule; the scan's flock handles them, but confirm the Trigger
      tab's **Board Access** card before launching. Read `n1081b/CLAUDE.md` first.
- [ ] **Anchor panel** decided — see §4c. This is the single biggest data-quality item.
- [ ] Beam up and reasonably steady (the scan self-gates on `config/beam_state.json`).

---

## 4. ⚠ Stale defaults — flags you MUST pass

`rate_scan_2d.py` still carries its 07-16/07-17 defaults. Launching it bare would silently
scan the **old** front-end and, worse, **overwrite the standing plastic HV**.

### a) HV — `--skip-hv` is MANDATORY
Without it the script applies
`calibrations/pss/hv_equalization_run224466.json` (`rate_scan_2d.py:91`) — the **old
run224466 set**. The standing HV is the **Y88-equalized** set. Omitting `--skip-hv` would
clobber the current calibration. **Always pass `--skip-hv`.**

### b) Thresholds — all three defaults are stale

| constant | file default | must pass |
|---|---|---|
| `WALL_NOMINAL` | `A:15,B:16,C:15,D:16` | `--wall-nominal "A:25,B:35,C:34,D:36"` (half-MIP) |
| `PLASTIC_LADDER` | `-20,-30,-44,-66` | wide ladder, §5 |
| `PLASTIC_BASELINE` | `-30` | `--plastic-baseline=-80` |

**Held-sector baseline note.** The adopted 0.5-MIP points are *per-sector*
(−65/−78/−86/−83) but `--plastic-baseline` takes **one uniform value** — held sectors are
only a beam reference, so what matters is that it is *fixed*, not its exact value. `-80`
sits within a few mV of B/C/D. (The swept ladder is likewise uniform across the swept
pair — the 07-19 wide ladder was uniform too, so this is faithful to it.)

### c) 🔴 Anchor panel — panel 3 is DEAD
Verified across every point of both 07-17 passes: `anchor_edges = 0`. Panels that actually
fired: **1** = Singles (589k), **2** = Doubles (23k), **4** = gated pulser (6.5k),
**5** = master trigger (6.8k). **Panel 3 (PS/γ-flash, M5 SEC_D lemo2) produced nothing.**

Consequences if left unfixed: the TT phase never reaches `--tt-pulses`, so **every point
runs to the `--tt-max` cap** (wasting ~25 s/point), and there is no true flash anchor.

Two options, in order of preference:
1. **Fix the panel-3 cabling** (M5 `.244` SEC_D lemo2 ← PS/γ-flash line) while there is
   physical access, then keep `--anchor-panel 3`. Gives a real flash t0.
2. **Fall back to `--anchor-panel 4`** (gated pulser). This is exactly what the offline
   analysis already does — it clusters panel-4 tags into beam windows — and panel 4 gave
   ~200 tags/point on 07-17, plenty for the 12-edge target. Only valid in `flash_random`
   (which we are in anyway).

**Verify before committing the whole scan** with a `--dry-run` plus a single short point.

---

## 5. Grid design & time budget

Measured cost from 07-17: ≈ **95 s/point** (20 s counter + up to 65 s TT + writes/settling).
With a working anchor, TT can end at `--tt-min` → ≈ **70 s/point**.

Full 07-19 granularity (49 plastic values) × 4 wall mults × 2 passes = 392 points ≈ **11 h**
— not viable. Split into two stages; **Stage A is the priority** and delivers the
time-resolved figures.

### Stage A — wide plastic ladder, walls fixed at half-MIP (~1.5 h)
21 plastic values (finer where the structure is), 1 wall mult, 2 passes = **42 points**.

```
-10,-15,-20,-25,-30,-35,-40,-50,-60,-70,-80,-95,-110,-125,-140,-155,-170,-190,-210,-230,-250
```

Delivers: `profiles`, `profiles_tail_linear`, `inwindow_frac`, and every rate-vs-plastic
curve — now with timestamps, across the full 07-19 range.

### Stage B — coarse 2D for the heat maps (~2 h)
4 wall mults × 6 plastic × 2 passes = **48 points**. Delivers the three `heat_*` maps.

Wall mults **bracket** the adopted point rather than only going up: `0.6,0.8,1.0,1.3`.
(The walls now sit at the physics-referenced half-MIP, so the old 1.0–3.3 upward ladder
would run far above MIP. At ×0.6 the lowest channel is A 15 mV — clear of the |10| mV
hardware floor.)

---

## 6. Commands

Run from the repo root, in the venv, inside a **tmux** pane (never SIGKILL it — a dirty
disconnect is what wedges these boards; let it exit on SIGINT/SIGTERM).

**Dry run first — always:**
```bash
.venv/bin/python n1081b/rate_scan_2d.py --dry-run --skip-hv \
  --wall-nominal "A:25,B:35,C:34,D:36" --wall-mults "1.0" \
  --plastic-ladder="-10,-15,-20,-25,-30,-35,-40,-50,-60,-70,-80,-95,-110,-125,-140,-155,-170,-190,-210,-230,-250" \
  --plastic-baseline=-80 --anchor-panel 4 --label wide_tt_stageA
```

**Stage A:**
```bash
.venv/bin/python n1081b/rate_scan_2d.py --skip-hv \
  --wall-nominal "A:25,B:35,C:34,D:36" --wall-mults "1.0" \
  --plastic-ladder="-10,-15,-20,-25,-30,-35,-40,-50,-60,-70,-80,-95,-110,-125,-140,-155,-170,-190,-210,-230,-250" \
  --plastic-baseline=-80 --anchor-panel 4 \
  --dwell 20 --tt-pulses 12 --tt-min 40 --tt-max 65 \
  --label wide_tt_stageA
```

**Stage B:**
```bash
.venv/bin/python n1081b/rate_scan_2d.py --skip-hv \
  --wall-nominal "A:25,B:35,C:34,D:36" --wall-mults "0.6,0.8,1.0,1.3" \
  --plastic-ladder="-20,-40,-80,-125,-170,-250" \
  --plastic-baseline=-80 --anchor-panel 4 \
  --dwell 20 --tt-pulses 12 --tt-min 40 --tt-max 65 \
  --label wide_tt_stageB
```

> Note the `--plastic-ladder=-...` **`=` form**: argparse otherwise reads a leading-minus
> list as a flag.

Output → `~/beam_july/rate_scan_2d/<timestamp>_wide_tt_stageA/` containing
`config.json`, `points.jsonl`, `tt_passAC.csv`, `tt_passBD.csv`.

---

## 7. During the scan

- Watch that `tags_per_panel` is non-zero **and** `anchor_edges > 0` on the first few
  points. If `anchor_edges` is 0 again, stop and revisit §4c — the run is still usable but
  every point will burn the full `--tt-max`.
- Points caught by a beam transition must be excluded offline (three were on 07-17:
  `BD_w1.0_p-44`, `BD_w1.0_p-66`, `BD_w1.0_p-30`). The scan self-gates on beam, but a
  mid-point beam death still spoils that point — note the times.
- D reads **0** at plastic shallower than ~−24 mV (broken D1 input). Expected, not a fault.
- Thresholds at the **|10| mV floor** noise-saturate; treat −10 and −15 as diagnostic only
  (keep ≥1.5× floor for anything load-bearing).

---

## 8. 🔴 Restore & verification (do not skip)

The scan's exit restore writes the **uniform** `--plastic-baseline` to all four sectors —
it does **not** know the per-sector 0.5-MIP set. (On 07-17 this exact class of bug left D
at −30 instead of −38.)

**While still in a safe trigger mode** (apply-mode also calls `check_safe_mode()`):

```bash
# per-sector 0.5-MIP plastics
.venv/bin/python n1081b/threshold_ladder.py --apply-plastic "A:-65,B:-78,C:-86,D:-83"
# half-MIP walls
.venv/bin/python n1081b/threshold_ladder.py --apply-wall "A:25,B:35,C:34,D:36"
```

Then, in order:
1. Verify read-back of M1 and M2 thresholds matches the above.
2. Restore the trigger mode that was running before (`scint(singles+ps)` for run_64).
3. Confirm M3 wall-leg G&D delay is still **+20 ns** all sectors (the scan never writes M3,
   but verify — it is easy to lose and there is no alarm on it).
4. Update `n1081b/CLAUDE.md` "Current board state" only if something actually changed.

---

## 9. Analysis

Both stages drop into the existing chain with only path edits:

- Copy `analysis/rate_scan_2d/night_0717/analyze_night.py` → a new
  `analysis/rate_scan_2d/wide_tt_0721/`; point `MAIN` at the Stage A/B directories and set
  the anchor panel to whatever was used. It already produces all five figures.
- `extra_plots.py` (linear tail + timing overlays) and
  `analysis/scint_recal_0719/wide_ladder_perdet.py` (per-detector ratio panels) then run
  unchanged against the new data.
- **Normalization reminder:** do not beam-normalize with `last_pulse_e10` — it is a
  single-pulse snapshot and wall singles bounce 900→2800 across a ladder with it. Use the
  held-sector reference (`rate = raw / (ref/mean(ref))`), and remember the M5 sections are
  **not sampled simultaneously**, so same-point counter ratios scatter ±50 %.

## 10. Open items

- Panel-3 (PS/γ-flash) cabling — root cause still unknown since 07-17.
- The predicted **152 mV MIP peak** is still unmeasured; the 07-19 ladder could not resolve
  it. A dedicated prompt-beam MIP run remains the clean way to confirm it — this scan will
  not settle it either, though Stage A's deep points give a second look.
