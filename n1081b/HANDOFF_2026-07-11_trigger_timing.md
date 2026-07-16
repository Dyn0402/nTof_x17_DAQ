# HANDOFF — Trigger timing optimization (2026-07-11)

Step-by-step plan for tightening the n_TOF X17 trigger coincidence timing.
Written to be executed by an agent with SDK access on `mx17-daq`. Follow the
tasks **in order**; each has a verify step and an abort/revert rule. Read
**Ground rules** and **Known gotchas** before touching anything.

## Context (read first)

Six CAEN N1081B boards, trigger "Module N" = `192.168.10.(239+N)`. Signal chain:

```
M1 (.240)  4x wall OR      (SiPM 428F sums, DISCR +30 mV, out mono 50 ns)
M2 (.241)  4x scint OR     (2 plastics/wall, DISCR -80 mV, out ch0->M3 mono 50,
                            ch1->M5 scaler mono 100)
M3 (.242)  4x sector AND   (in: wall=panel1, scint=panel2; out mono 200 ns,
                            copies -> M4.A, M4.B, M5.C)
M4 (.243)  A=Singles OR(0,1,3,4)  B=Doubles coincidence_gate(0,1,3,4; w=100)
           C=OR(0,1)=Singles|Doubles (TEMPORARY, see §Bookkeeping)  D=final OR (wedged)
M5 (.244)  4x counter scalers: A=walls B=scints C=sectors D=M4 taps
M6 (.245)  OFFLINE (pulser/γ-flash countermeasures) — do not touch
```

State as of 2026-07-11 ~00:30 (snapshot `snapshots/dump_2026-07-11_cg_doubles_c_or.json`):
wall-vs-scint skew measured 0–20 ns on all sectors (44 LA pairs); sector rates
5–23 Hz; Singles ~52 Hz; Doubles (coincidence gate, 100 ns window) ~0.75 Hz.
Cosmic-only (no beam). Rates drift with conditions — always normalize (see Task 3).

Docs: full user manual `docs/WEB_UM8139_x1081B_rev1.pdf` (UM8139). Key specs:
**monostable 15 ns–1 µs, 5 ns steps, ±1% ±3 ns; G&D 5 ns resolution but values
are approximated on-board with few-ns error** (verify with LA, don't trust the
setting); LA samples at 10 ns.

## Ground rules

1. **DAQ must be idle** before any config change: `tmux capture-pane -t daq_control -p | tail -3`
   must show a completed/stopped run (e.g. "donzo"). If a run is active, STOP and ask.
2. **Snapshot before and after each task:**
   `.venv/bin/python n1081b/dump_module_info.py > n1081b/snapshots/dump_<date>_<tag>.json`
3. **One change at a time, verify by readback** (`get_*` mirrors every `set_*`).
4. **Revert rule:** if a verify step fails or a rate collapses unexpectedly,
   restore the touched values from the pre-task snapshot and re-verify before
   continuing. Full restore reference: `dump_2026-07-11_cg_doubles_c_or.json`.
5. Run everything from this repo on mx17-daq: `.venv/bin/python`, SDK `n1081b_sdk`,
   login password `"password"`.
6. **Do not touch .245 (M6)** — offline hardware, old firmware.

## Known gotchas (violating these wastes hours)

- Panel numbers are 1-based, SDK lemos 0-based: **panel = lemo + 1**.
- `configure_or` / `configure_majority` / most `configure_*` **return None**
  (reply consumed internally) — verify via `get_function_configuration`, not
  the return value.
- **.243's LA input-trigger is broken**: `mode_in=OR` never fires. Trigger on
  OUTPUTS (`mode_out=LA_TRIGGER_OR`, out flags) — frames still contain all 24
  inputs. On .242 input-triggering works fine.
- LA data: `inputs[section*6 + panel-1]`, `outputs[section*4 + ch]`, 2048
  samples × 10 ns. Arm with `start_logic_analyzer()`, poll
  `get_logic_analyzer_data()` until `data.inputs` non-empty ("no_data" = not
  triggered yet). Working reference: `n1081b/la_skew_stats.py`.
- G&D semantics: input pulse is replaced by a **GATE-wide pulse delayed by
  DELAY** from the leading edge. `enable_gd=True` with `gate=0` = degenerate
  (kills the channel). Always set a sane gate when enabling.
- `set_input_channel_configuration(sec, ch, status, enable_gd, gate, delay, invert)` —
  always pass the existing values for fields you don't mean to change (read
  them first with `get_input_channel_configuration`).
- M5 counters cover **lemo 0–3 only**; time-tag covers all 6 (one section at a
  time — packets carry no section id and broadcast to every websocket client;
  never run a second SDK connection to a board that is streaming).
- Counter-rate reading pattern (used throughout):
  ```python
  r0 = {s.name: {i: c['value'] for i, c in enumerate((d.get_function_results(s).get('data') or {}).get('counters', []))} for s in N1081B.Section}
  time.sleep(T); r1 = ...same...   # rate = (r1-r0)/T per channel
  ```
- Coincidence-gate counters (M4.B `get_function_results`): 5 values =
  `[TOTAL, CH1, CH2, CH4, CH5]`; in FIRST mode per-channel = which input
  *opened* the window.

---

## Task 1 — Verify G&D behaviour on one channel (15 min)

Purpose: the delay scan (Task 3) rests on the G&D stage; prove it does what we
think on this firmware, and calibrate its real error.

1. On **.242 SEC_A input ch0** (wall leg, ~200 Hz): read current in-ch config
   (expect status=True, gd off). Set `enable_gd=True, gate=20, delay=0`.
2. LA on .242 (input-trigger works there): trigger on A panel 1 rising, 20
   frames → pulse width should now be ~20 ns (was 50).
3. Set `delay=50` (keep gate=20). LA again, but trigger on **A panel 2 (scint)**
   and use coincident frames: the wall edge should sit ~50 ns later relative to
   the scint edge than the Task-0 baseline (baseline skew ≈ 0–10 ns). Record the
   *measured* shift vs the requested 50 ns — this is the G&D error (expect few ns).
4. **Restore ch0**: `enable_gd=False, gate=0, delay=0` (status True, invert False).
   Verify skew back to baseline.

Abort if: width doesn't follow gate, or delay shift is wildly off (>15 ns error)
→ G&D unusable for the scan; report and stop (fallback: scan by delaying via
cable at the rack, out of scope).

## Task 2 — Thin the leg monostables to 20 ns (20 min)

1. Baseline: read M5.C rates (sectors) + M5.A/M5.B (walls/scints) over 120 s.
2. Set **M1 (.240) all four sections, output ch0–3 mono 50 → 20** — EXCEPT
   `SEC_A ch3` (stray inverted RAW copy, leave untouched).
   `set_output_channel_configuration(sec, ch, True, True, 20, False)`.
3. Set **M2 (.241) all four sections, output ch0 mono 50 → 20** (leave ch1 at 100).
4. LA on .242: trigger A panel 1, then A panel 2 — both legs should show ~20 ns
   widths (LA quantizes to 10–30 ns; ±1%±3 ns spec).
5. Re-read M5.C + M5.A/B over 120 s. Compute per-sector
   `eff = (C_rate/√(A_rate·B_rate))_after / (same)_before` — crude but drift-robust.
   **Accept if eff ≥ 0.9 per sector.** If a sector drops more, its skew is
   biting: proceed to Task 3 anyway (the scan will find it), but note it.

Note: do NOT go to 15 ns yet — 15 is the hardware floor and ±1%±3 ns can eat
into it; decide after the scan (Task 4).

## Task 3 — Delay curve scan, wall vs scint per sector (~45 min)

Goal: map coincidence rate vs relative leg delay with the thin monos; find the
plateau center per sector. Delay is applied at **M3 (.242) input channels**
(wall = ch0, scint = ch1 of each section), gate fixed = 20 ns.

Sweep design (both signs by swapping which leg is delayed):
- Points: delay ∈ {0, 5, 10, 15, 20, 30, 40, 60} ns.
- Leg A sweep: apply delay to **scint** inputs (all 4 sections at once,
  `set_input_channel_configuration(sec, 1, True, True, 20, D, False)`), wall
  inputs untouched. This probes "scint later" = negative wall−scint offsets.
- Leg B sweep: reset scint (gd off), apply the same points to **wall** inputs
  (ch0). Probes "wall later".
- At each point: sleep 2 s (settle), then count **M5.C** rates for **60 s**;
  also record M5.A and M5.B rates in the same interval for normalization.
  Expected stats: 5–25 Hz → 300–1500 counts/point (√N ≤ 6%).
- Between the two sweeps and at the end, retake the D=0 point — this tracks
  cosmic-rate drift; normalize each sweep to its bracketing zeros.

Analysis per sector: plot normalized C-rate vs signed delay (scint-delayed =
negative axis, wall-delayed = positive). Expect a plateau of width ≈ w1+w2
(≈40 ns) with edges falling over ~10–20 ns (jitter+walk). The **plateau center**
is the residual skew.

Outcome rules:
- |center| ≤ 10 ns → set **no delay** (G&D error isn't worth it); clean up:
  all M3 input channels back to `enable_gd=False, gate=0, delay=0`.
- |center| > 10 ns on a sector → set that sector's **earlier leg** delay to the
  center value (gate=20), leave the other leg gd-off. Re-verify with a D=0-style
  60 s count: rate must match the plateau top.
- No plateau / rate ≪ expected everywhere → mono too thin for the true spread:
  go back to 30 ns monos on both legs and repeat the scan once. If still no
  plateau, restore 50 ns and report.

**Always finish by removing scan delays** except those chosen deliberately.
Snapshot.

## Task 4 — Decide 15 ns (optional, 15 min)

Only if Task 3 shows plateau width ≥ 35 ns with sharp edges and |center| ≤ 5 ns:
set both legs' monos to 15, re-run the 3-point check {plateau center, ±10 ns}
at 60 s each. Accept 15 ns if the center rate stays within 10% of the 20 ns
plateau top; otherwise stay at 20 ns.

False-positive context (why thinner ≈ better but not urgent offline): random
rate per sector ≈ r_wall·r_scint·(w1+w2) ≈ 200·25·40e-9 ≈ **2×10⁻⁴ Hz** at
20 ns monos — negligible now; matters at beam-burst instantaneous rates.

## Task 5 — Inter-wall timing check via Module 5 time-tag (~30 min)

Purpose: confirm the four *walls* are mutually aligned (matters for the Doubles
coincidence gate at M4.B). No recabling needed — M5.A already has all four wall
copies in ONE section, so one time-tag stream gives all four on one clock.

1. Confirm nothing else talks to .244, then:
   `.venv/bin/python n1081b/mod5_timetag_logger.py --section A --duration 900 -o n1081b/walls_tt.csv`
   (~900 s at 650–900 Hz/wall ≈ 2–3 M rows, fine.)
2. Offline: for each wall pair (i,j) histogram Δt = t_i − t_j for |Δt| ≤ 2 µs
   (channels are panels 1,2,4,5; timestamps ns, 10 ns steps). True double-wall
   cosmics give a peak on a flat random background; the **peak position** is the
   inter-wall skew.
3. Accept if every pair's |peak| ≤ 20 ns (expected — identical cabling).
   If a wall is off by more: trim it at **M4.A and M4.B input G&D** (delay the
   early walls to the latest one, gate = incoming width), NOT at M1/M3 (those
   would move the wall-vs-scint alignment done in Task 3).
4. Same method optionally for scints (`--section B`).

## Task 6 — Thin M3 output monos + tighten the Doubles window (~20 min)

After Tasks 3+5 pass:
1. M3 (.242) outputs: mono 200 → **30 ns**, all four sections ch0–3.
   (These feed M4.A Singles, M4.B coincidence gate, M5.C scaler.)
2. Verify: M5.C rates unchanged (60 s, normalized); M4.A Singles rate on M5.D
   lemo0 unchanged.
3. M4.B coincidence gate: width 100 → **50 ns**
   (`configure_coincidence_gate(B, True,True,False,True,True, True,True,True,True,True, False, False, 0, 50, TRIGGER_FIRST)`).
4. Verify: B TOTAL counter over 10 min before/after — expect the rate to stay
   within statistics (real doubles are correlated within tens of ns) while any
   accidental component drops. If the rate falls by >30%, inter-wall alignment
   is worse than Task 5 suggested — widen back to 100 and flag.

## Task 7 — Final verification + docs (~20 min)

1. Full rate table (M5 A/B/C/D + M4.B counters), 120 s.
2. Final snapshot `dump_<date>_timing_final.json`.
3. Update `n1081b/README.md` (as-built table) and
   `~/Documents/ntof_trigger_logic/TRIGGER_SETUP_2026-07.md` §0.5 with final
   mono widths, delays, window, and the delay-curve results (per-sector center
   and plateau width).

## Bookkeeping — deferred items (do NOT execute, keep visible)

- **M4.C is TEMPORARILY a plain OR(Singles, Doubles).** When the N93B timer +
  veto hardware return: switch back to `or_veto`, veto on **CH6 (lemo 5)** —
  manual: CH1–5 are OR inputs, CH6 is the reserved veto — and verify polarity
  on the LA (veto must assert OUTSIDE the PS window).
- **M4.B output cabling:** coincidence-gate outputs CH1,3 = per-coincidence
  pulse; CH2,4 = window copy. The Doubles cable into C p2 is on out panel 1 ✓;
  the **out panel 2 cable (to M5.D scaler) carries the window signal and should
  move to panel 3** — rates on that scaler read window counts ≈ TOTAL, so low
  priority.
- **M4.D p1 is stuck constantly HIGH** from an unidentified line (NOT C's
  output — verified while C fires). Identify at the rack (likely the offline
  N93B gate). Until fixed, section D is not usable as the final OR; if D must
  be used, disable its lemo 0.
- **M6 (.245) offline**: pulser (→ M4.C input, exact panel unknown), mesh
  charge injection (SecB), SiPM enable (SecC) all dead in the water; also still
  on old fw 2022.3.0.0 → upgrade via web GUI when back and safe.
- M1 SEC_A out ch3: stray inverted RAW copy, unknown cable — identify at rack.
- .243 SEC_C in-ch2 leftover `enable_gd=True, gate=0` — harmless while unused;
  clean up on next .243 pass.
