# HANDOFF 2026-07-22 — full analysis of the "recov" trigger scan (all facets)

> **⚠ Window note added 2026-07-22 (evening).** The N93B acceptance gate has SINCE been
> changed: its **start moved from 5 ms to ~1 ms** after the flash, to match the t > 1 ms
> thermal gate of the GEANT trigger study (`MX17_Full_Geant`
> `.claude/al_pair_background/PLASTIC_THRESHOLD.md`). **Every number and method in this
> document describes data taken under the OLD 5 ms start and is unchanged** — in
> particular the §0 flash-t0 extraction ("the panel-5 edge ... sitting ~5 ms *before* the
> gated cluster") is correct for THIS data and must NOT be rewritten to 1 ms. Only the
> `inwindow_frac` window bounds in §3 need re-deriving for any scan taken from 07-22
> evening onward. The gate is an external N93B front-panel setting, not software-readable
> — read it off the module rather than assuming either value.

**Goal.** Analyze last night's parasitic N1081B trigger scan (taken alongside run_65),
covering every facet the 07-17 `night_0717` analysis did **but deeper**, and — the
priority result — **coincidence count vs time-since-γ-flash for each threshold
combination**. This scan is the 07-19-front-end / wide-plastic re-take the
`PLAN_2026-07-21_wide_2d_tt_scan.md` called for, and it finally has a **real flash
reference** (see §2), which 07-17 did not.

Model the analysis on `~/beam_july/analysis/rate_scan_2d/night_0717/`
(`analyze_night.py` + `extra_plots.py` + `plastic_ladder_perdet.py` + `FINDINGS_*.md`);
put the new work in `~/beam_july/analysis/rate_scan_2d/recov_0722/`.

---

## 0. The one thing that's new vs night_0717 — READ THIS FIRST

07-17 anchored on **panel-4 gated-pulser clusters** and reconstructed a fake t0 from the
"first burst-start Singles tag," because the intended flash anchor (panel 3) was dead
(`anchor_edges = 0` every point — panel 3 is an *empty* front-panel input, rack-wide
convention). See `FINDINGS_2026-07-18.md` §1/§3.

**Last night's scan used `--anchor-panel 5` = the master trigger (M4.D out = OR(flash,
C-out)), which DOES carry the flash.** Verified in the data: per point,
`panel5 − panel4 ≈ 30–90` (e.g. AC_w1.0_p-80: panel5 2050 vs panel4 2011 → ~39 flashes;
consistent across the grid). So:

- **panel 4** (`gated pulser`, M4.C out) = the veto-gated pulser triggers only (5–85 ms
  window edges).
- **panel 5** (`master trigger`, M4.D out) = panel-4 set **plus the flash edge** at t≈0.
- **flash t0 = the panel-5 edge with no panel-4 partner** — equivalently the isolated
  leading edge of each beam cycle, sitting ~5 ms *before* the gated cluster (the N93B
  gate now opens at 5 ms). This is a **true** t0, not a heuristic.

This is what makes the coincidence-vs-time-since-flash result trustworthy this time.
(Fixed offset: the flash reaches M4.D ~9.6 µs late via the M6.A fan-out delay —
negligible vs the ms structure; ignore or subtract a constant 9.6 µs.)

---

## 1. The data

```
~/beam_july/rate_scan_2d/
  2026-07-21_22-48-25_recov_main/    walls 0.5 MIP (mult 1.0); plastic ladder
                                     -80,-95,-110,-125,-140,-155,-170,-190,-210,-230,-250
                                     passes AC + BD (2-set) → 22 points   <- MAIN
  2026-07-21_23-45-45_recov_trunc/   walls 1.0 & 1.5 MIP (mult 2.0, 3.0); plastic
                                     -80,-110,-150,-190,-250; AC + BD → 20 points
  2026-07-22_08-00-24_recov_mip_fine/   partial (1 usable point p-142); ignore
  2026-07-22_08-06-45_recov_mip_fine2/  crashed on preflight (M5 wedge); EMPTY, ignore
n1081b/snapshots/timing_scan_recov_delay.json    delay scan (see §4e)
n1081b/snapshots/timing_scan_recov_probe_probe.json   coarse probe (locator only)
```

Each scan dir: `config.json`, `points.jsonl`, `tt_passAC.csv`, `tt_passBD.csv`.

**`tt_pass{AC,BD}.csv`** — one row per M5 time-tag edge:
| col | meaning |
|---|---|
| `point_id` | e.g. `AC_w1.0_p-80` = pass_wallmult_plastic |
| `host_unix` | packet host-receive time (batched, repeats; wall-clock anchor only — never for spacing) |
| `panel` | 1=Singles(M4.A), 2=Doubles(M4.B, ~50 ns coinc), 4=gated pulser(M4.C out), **5=master trigger(M4.D out, has flash)** |
| `t_board_ns` | free-running board clock, 10 ns granularity — **all timing comes from this** |

Sort per channel before diffing; within a channel `t_board_ns` is strictly monotonic; do
NOT derive rate from `host_unix`. (Same rules as the 07-21 secC capture handoff.)

**`points.jsonl`** — one JSON/point with: `pass`, `wall_mult`, `plastic_mv`, `wall_mv`
(active sectors), `held` (sectors + their thresholds — the beam reference), `counters_hz`
(SEC_A walls / SEC_B scint / SEC_C sector-coincidence, per section per channel),
`counter_dwell_s`, `tt.tags_per_panel`, `tt.anchor_edges`, `point_wall_s`, `beam_e10`,
`beam_state`. This is where per-point normalization + QA numbers live.

**Threshold grid covered:** walls {0.5, 1.0, 1.5 MIP} × plastic {−80 … −250 mV, per
pass}. Two-set: pass **AC** scans A+C holding B+D; pass **BD** scans B+D holding A+C.

---

## 2. PRIORITY RESULT — coincidence vs time-since-flash, per threshold combination

"Coincidence" here = **Doubles = panel 2 = M4.B** (the two-sector coincidence trigger).
(The per-detector wall∧liq *sector* coincidences are the SEC_C **counters** — per-point
scalars only, not time-resolved; use them for normalization/context, not for the
time profile. The flash line is not tapped into SEC_C, so only panel-5 gives a t0.)

**Per threshold point:**
1. Load that point's tags (both passes keep the point in their own CSV; a point lives in
   exactly one pass file). Sort by `t_board_ns`.
2. **Flash t0 set**: cluster panel-5 edges; the flash edges are the panel-5 tags with no
   panel-4 tag within ±ε and sitting ~5 ms before each gated cluster. Build the list of
   flash `t_board_ns`.
3. For every **Doubles** (panel-2) tag, `dt = t_D − (nearest preceding flash t0)`.
   Keep dt in, say, [−1, +120] ms.
4. Histogram dt **per flash-normalized** (divide by #flashes at that point so
   thresholds/beam-intensity compare), in BOTH a fine linear axis (0–5 ms, 100 µs bins)
   and a log axis out to ~100 ms (to see the comb teeth).
5. Overlay the threshold combinations: one curve per (wall_mult, plastic_mv). Also plot
   Singles (panel 1) the same way as context.

**What to expect / how to read it** (memories `run61-tracking-efficiency`,
`ipc-arrival-vs-comb`, `dream-flash-comb-mechanism`):
- A prompt spike in the first ~1–10 µs of tooth 0 (IPC), then the **post-flash comb**:
  teeth near ~4 ms / ~13 ms / ~27+ ms. **Split by tooth group; never pool them** — run_61
  showed the optimum HV/threshold MOVES with time since flash.
- The headline question: **does the coincidence-vs-time curve change shape with plastic
  or wall threshold?** i.e. does a tighter trigger cut the prompt peak, the tail, or shift
  the recovery? That's the deliverable — a family of curves + a summary of how
  first-ms-fraction and tail yield move across the grid.
- Cross-check the panel-5 t0 against the 07-17 panel-4-cluster method (port
  `windows_and_t0`); they should agree to a fixed ~5 ms offset. If panel-5 flash
  extraction is noisy at some points (beam gaps), fall back to panel-4 for those and note it.

Deliverable figures: `coinc_vs_tsf_linear.png` (0–5 ms), `coinc_vs_tsf_log.png`
(0–100 ms), plus a small-multiples grid (one panel per wall_mult, curves = plastic ladder).

---

## 3. All the other facets (match night_0717, extend where noted)

- **Heat maps** `heat_singles`, `heat_doubles_total`, `heat_doubles_tail` (>1 ms after
  flash) over the full **wall {0.5/1.0/1.5} × plastic** grid — 07-17 only had the 1.0-MIP
  row of walls at full plastic; we now have three wall rows (0.5 at full ladder, 1.0/1.5
  truncated), so the wall axis is real. Merge passes for the maps (07-17 convention).
- **`inwindow_frac`** — fraction of Singles/Doubles inside the (now 5–85 ms) N93B window
  vs out. NB the window widened from ~30 ms to ~5–85 ms since 07-17 — recompute the
  window bounds; don't reuse the 30 ms number.
- **`profiles` + `profiles_tail_linear`** — the 1 ms-bin time-since-flash profiles
  (now panel-5 anchored), Singles + Doubles, nominal + across threshold.
- **Per-sector normalized response** (`plastic_ladder_perdet.py`) — rate vs plastic per
  detector, **held-sector normalized** (see §5). This is where the MIP turn-on shows up.

**Deeper than 07-17 (new asks):**
- **MIP turn-on**: the −80…−250 plastic ladder crosses the *predicted* 152 mV MIP peak
  (`mip_thresholds_y88.json`, per-sector 127–177). Fit/inspect the coincidence-rate
  knee per sector; this is the standing "152 mV MIP unmeasured" open item — a second look.
- **Purity vs efficiency**: Doubles/Singles ratio and clean-tail-Doubles vs threshold —
  the actual trigger-tuning tradeoff, mapped over the 3-wall × plastic grid.
- **Time-resolved purity**: in-window-fraction and Doubles/Singles as functions of
  time-since-flash (does purity recover faster/slower than raw rate?).

---

## 4. Delay + accidental-background (recov_delay)

`n1081b/snapshots/timing_scan_recov_delay.json`: two-set (sweep A/B hold C/D at +20, then
swap), signed wall-vs-scint delay at M3, `count_s`=60, `gate_ns`=20, `hold_delay`=20.
Analyze with the existing `n1081b/analyze_timing_scan.py`.
- Gives the **coincidence-window plateau** (verified ~ +10..+25 ns, sharp cliff at +30)
  and the **accidental floor** (the far points +150/+300 ns read ~0 → accidentals
  negligible). Use the floor to justify no accidental subtraction, or subtract it.
- **QA:** exclude delay set2 points 5–7 (C/D delay 0/+10/+20, ~22:39–22:42): a ~3-min
  beam gap collapsed the held reference to ~0 there → garbage normalization.

---

## 5. Normalization — MANDATORY (memory `scint-recal-0719-wide-ladder`)

Use the **held-sector reference**: `rate_norm = raw / (ref / mean(ref))`, where `ref` is
the beam-normalized rate of the sectors HELD at nominal during that point (from
`points.jsonl` `held` + `counters_hz`).
- Do **NOT** beam-normalize with `beam_e10` / `last_pulse_e10` alone — it's a single-pulse
  snapshot; wall singles bounce 900→2800 across a ladder with it.
- Do **NOT** use same-point counter ratios as if simultaneous — **M5 sections are sampled
  sequentially**, so raw SEC_A/B/C same-point ratios scatter ±50 %.
- Per-window maps: 07-17 left them un-beam-normalized (convention); the per-sector
  response table IS normalized. Keep that split.

---

## 6. Pitfalls / QA checklist

1. **Beam super-cycle gaps** produce whole points reading ~0 coincidences (walls
   beam-dominated). Seen live 08:00 (mip_fine p-134 read 0, p-142 normal). **Flag/exclude
   any point whose `beam_e10` or held-sector rate is far below its neighbours** before
   trusting it. Read 2–3 neighbouring points before calling anything "dead."
2. **Tag-loss at shallow plastic / high Singles**: panel-1 (Singles) can hit the ~700 Hz/ch
   M5 TT ceiling and *under*-count. Cross-check panel-1 TT rate vs the SEC_A/SEC_C scaler
   in `counters_hz` and apply the 07-17-style tag-loss factor if needed (median ×1.13 there).
3. **Per-sector thresholds**: walls sit at per-sector half-MIP (A 25 / B 35 / C 34 / D 36 —
   arm A ~1.4× low, geometric). Plastic ladder is uniform across the swept pair. **D1 is
   now repaired** (unlike earlier runs), so wall D should respond below −24 mV — but this
   scan is walls-at-half-MIP; note D behaviour rather than assuming the old "D dead" rule.
4. **Two-set bookkeeping**: a threshold point exists in only one pass (AC or BD). To get
   all four sectors at a given (wall,plastic) you need both passes; the held sectors of one
   pass are the scanned sectors of the other.
5. **Exclude beam-compromised points** the way 07-17 did (it dropped 3): auto-detect via
   per-point beam_e10 / held-rate outliers, not just the prescribed list.

---

## 7. How to build it

- Copy `night_0717/analyze_night.py` → `recov_0722/analyze_recov.py`; set `MAIN` to the
  two recov dirs (main + trunc), and **rewrite `windows_and_t0` to use panel 5**: extract
  flash edges = panel-5 minus panel-4, t0 = flash edge; keep the panel-4 method as a
  cross-check path. Everything downstream (heat/profiles/inwindow/per-sector) then reuses.
- `extra_plots.py` (linear tail + timing overlays) and `plastic_ladder_perdet.py` adapt
  with path + panel edits.
- New module `coinc_vs_tsf.py` for §2 (the priority result).
- Write `FINDINGS_2026-07-22.md` with a headline table like night_0717's.

## 8. Physics questions to answer (rank the write-up around these)

1. **Coincidence recovery vs time-since-flash — does its shape depend on threshold?**
   (the ask). Per tooth group (4/13/27+ ms), plus prompt (<1 ms).
2. Where does each sector's plastic MIP turn-on sit vs the predicted 152 mV?
3. Best trigger point for purity (Doubles/Singles) at fixed recovered yield, over the
   3-wall × plastic grid.
4. Is Singles-gated still infeasible (07-17: 70–90× DREAM budget), and does the wider
   5–85 ms window change the per-window event count vs the old 30 ms?

---

### Notes carried from the run
- Anchor is panel **5** for this scan (real flash) — the whole reason to redo it; don't
  regress to panel-4-only.
- `.244` (M5) was wedged 08:07 (Ctrl-C during a later mip_fine scan) and quarantined ~6 h —
  irrelevant to *analysis* (data already on disk), but M5 is monitoring-only anyway.
- Provenance of thresholds/HV: run_65 was flash_random (mesh A+C on/off), the trigger scan
  is parasitic on M1/M2 only; front-end = 07-19 Y88 walls half-MIP + plastic ladder.
