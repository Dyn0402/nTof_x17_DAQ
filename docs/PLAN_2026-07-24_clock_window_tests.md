# PLAN — 25 MHz clock vs the ~10 ms window (beam-off), 2026-07-24

**Question (operator).** The 07-23 scan proved RdClk 6.0→4.0 buys **1.5× sustained readout rate**
by a saturating-pulser IntRate measurement. This run proves the **time-domain** version the
operator actually cares about: with the faster read clock, do events land **denser and earlier
inside the fixed ~10 ms pulse window** (where 39.8 % of in-gate IPC arrives before 4.46 ms)?

Two independent proofs + two controls, all beam-off:
- **A** saturating clock **curve** (RdClk 6.0/5.0/4.5/4.0) → ceiling vs clock (= window occupancy).
- **B** Poisson ~1 kHz **event spacing** → readout comb period vs clock (the operator's literal idea).
- **C** **latency** control (3/35/100 at RdClk 4.0) → confirm latency doesn't affect readout.
- **D** rested-**dump** A/B (RdClk 6.0 vs 4.0) → events in the first 10 ms after a flash-like dump.

**Status: built, NOT launched.** Launch only once beam is confirmed OFF. Everything is prepared:
- Run config: `run_configs/run_config_clock_window_test.py` → `run_config_clock_window_test.json` (run `clk_window`).
- Analysis: `~/beam_july/analysis/flash_comb/clock_window/clock_window_analysis.py`.
- Reused as-is: `n1081b/set_veto_open.py`, `n1081b/set_pulser.py`, `dream_scripts/feu_trig_counters.py`,
  and `~/beam_july/analysis/flash_comb/flashoff_pulser/rest_toggle.py` (block D driver).

## Design choices worth knowing before you run it
- **ZS, not RAW — deliberate.** We count events and read timestamps only, never amplitudes. RAW
  n32 at ~10 kHz = ~3 GB/s > 10 GbE, so RAW would be **network**-limited and mask the read clock.
  ZS (~30 kB/event) keeps the readout the true limit — same reason the 07-23 1.5× scan used ZS.
  This is a **diagnostic, not physics data, and NOT a return to ZS running**; the ZS/PedSub
  double-subtract concern (CLOSED 07-24) is irrelevant because no amplitudes are produced.
- **Clocks set explicitly** (`rdclk_div`+`wrclk_div`), because `sample_period:60` now already means
  RdClk 4.0. **WrClk 6.0 on every point** → the 60 ns / 1.92 µs sample window is held fixed, only
  read-out speed varies. RdClk 5.0/4.5 are **un-phased** (WrClk 6.0) — their ADC content may be off
  but IntRate + timestamps (all we use) are valid. RdClk 6.0/6.0 and 4.0/6.0 are clean/phased.
- **No server restart needed** — `rdclk_div`/`wrclk_div`/`latency` all already plumbed (07-23). Still
  grep one emitted cfg for the `RdClk`/`WrClk`/`Dream * 12` lines before trusting a point.

## Pre-flight (beam OFF)
1. Confirm beam off: `config/beam_state.json` (remember `beam_on: null` = UNKNOWN; verify via CSV
   rows/minute). The saturating pulser drops any beam triggers.
2. Board access: check `config/n1081b_access/` for a holder (ignore the known-stale `.244`
   quarantine but do not assume). Only `.245` (M6.D) and `.243` (M4.C) are touched.
3. Open the veto and set the saturating pulser (block A starts saturating):
   ```
   .venv/bin/python n1081b/set_veto_open.py --lemos 4
   .venv/bin/python n1081b/set_pulser.py --fixed --period 50000 --width 100    # M6.D 20 kHz
   ```
4. Launch:
   ```
   .venv/bin/python daq_control.py run_config_clock_window_test.json
   ```

## Pulser sequence — the ONLY manual thing during the run
The DAQ steps sub-runs automatically. The pulser changes at three block boundaries; each
boundary sub-run has a **15 s settle** so you have time to switch. Watch the `daq_control` status
line (`subrun=...`) for the boundary name.

| when `subrun=` | do this to the pulser | covers |
|---|---|---|
| `satA_*` (already set) | fixed 20 kHz (pre-flight step 3) | A: satA_rd6_a … satA_rd6_b |
| **`poisB_rd6`** (settle 15 s) | `set_pulser.py --period 1000000 --width 100` (Poisson ~1 kHz) | B: poisB_rd6, poisB_rd4 |
| **`satC_lat3`** (settle 15 s) | `set_pulser.py --fixed --period 50000 --width 100` (back to 20 kHz) | C: satC_lat3 … satC_lat35b |
| **`dumpD_rd6`** (settle 15 s) | start `rest_toggle.py` in a separate shell (see block D) | D: dumpD_rd6 |
| **`dumpD_rd4`** | `rest_toggle.py` again for the second clock | D: dumpD_rd4 |

Per saturating sub-run (A and C), latch the rate: `.venv/bin/python dream_scripts/feu_trig_counters.py --latch`.

### Block D detail (rested dump — optional; A+B+C already answer the question)
`rest_toggle.py` idles then bursts a 200 kHz deterministic pulser on M6.D, so each restart dumps
a rested SCA (~15 cells) into the window. Run it **during** each dumpD sub-run (it opens its own
`board_session`, so let the fixed pulser finish first):
```
cd ~/beam_july/analysis/flash_comb/flashoff_pulser && python rest_toggle.py
```
One pass is ~1 min; the 2 min sub-run gives margin. Do it once during `dumpD_rd6`, once during
`dumpD_rd4`. (No clock argument — the clock is set by the DAQ sub-run, not the pulser.)

## Data sanity (every non-nominal point, BEFORE quoting it)
The 07-23 AdcDel lesson: **tracers alone do NOT certify integrity** — pair with a hits/event bound.
For this run we only need counts/timestamps, but still sanity-check the phased points:
- RdClk 6.0/6.0 and 4.0/6.0: expect ~96 hits/ev, tracers 0/224/511 ~100 %, baseline ~263.
- RdClk 5.0/4.5 (un-phased): content may be off — **fine, ignore amplitudes**, use only rate/timing.
- If a point reads FAST but has lost timestamps/events, it is broken, not fast.

## Read it out
```
.venv/bin/python ~/beam_july/analysis/flash_comb/clock_window/clock_window_analysis.py \
    /mnt/data/x17/beam_july/runs/clk_window --dump
```
Expected:
- **[A]** ceiling ~7.2 k → ~10.8 kHz, ratio ~1.50× (monotone across 6.0→4.0).
- **[B]** comb period ~9.6 ms (16.7 MHz) → ~6.4 ms (25 MHz) — the gap moves **out** of the 10 ms window.
- **[C]** rates flat across latency 3/35/100 (spread < 3 %) → latency doesn't affect readout.
- **[D]** events-in-first-10 ms ~1.5× higher at RdClk 4.0.

A null in [A]/[B]/[D] (clock buys nothing in the window) or a non-flat [C] is itself a real,
reportable result — bracket points (satA_rd6_a/b, satC_lat35/35b) are the drift check.

## RESTORE — mandatory, and the instant beam returns
```
.venv/bin/python n1081b/trigger_mode.py scint --singles --ps-pickup
.venv/bin/python n1081b/set_pulser.py                       # back to Poisson 1.5 ms
.venv/bin/python n1081b/set_veto_open.py --show             # expect C = or_veto[0]
.venv/bin/python n1081b/set_ps_trigger_delay.py --show      # expect delay 1800
```
Also confirm the next production cfg carries no leftover `rdclk_div`/`wrclk_div`/`latency`
override (the `clk_window` sub-runs set them per-sub-run only; production `run_config_mesh_*`
inherits the 25 MHz default and latency 35 from the template).

## Note on FEU state after the run
FEUs retain the last write, so after `dumpD_rd4` they hold RdClk 4.0 / WrClk 6.0 / latency 35 —
which **is** the production default, so this self-heals. `satC_lat100` leaves latency 100 only if
the run aborts mid-block-C; the next production configure rewrites `Dream * 12` from the template.
