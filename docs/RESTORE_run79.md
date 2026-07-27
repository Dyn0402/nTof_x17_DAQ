# RESTORE run_79 — the production statistics point

**Written 2026-07-27 09:57, while run_79 was live on sub-run `stat090_0015`.**
Purpose: park run_79 for a beam stop (→ run_80 cosmics), then come back to it in a
couple of minutes without re-deriving anything.

Everything below is **as-built, read off the board snapshot `poll_modules` wrote at
09:14:09 today** (`n1081b/snapshots/run79_asbuilt_2026-07-27.json`, copied from
`/mnt/data/x17/beam_july/runs/run_79/stat090_0015/n1081b_config.json`). It is not a
recollection of what we intended — it is what the hardware reported.

---

> **2026-07-27 update — use `switch_mode.py` instead of the manual steps below.**
> This document is now mostly a record of *why* the switch is what it is. The changeover
> itself (guards, routing, read-back verification, launch) is one command:
> `./switch_mode.py beam --start` / `./switch_mode.py cosmics --start`.
> Also note run_79 was **not** resumed in the end — after the midday stop we went to run_80
> (cosmics) and then a fresh **run_81** at the same operating point. The resume config below
> still works if you ever do want to pick run_79 back up.

## TL;DR — coming back

```bash
# 1. stop the cosmic run (run_80) cleanly — let it finish/kill from daq_control, never SIGKILL
# 2. wait for a real beam pulse (daq_control has NO beam gating; see below)
# 3. put the trigger back:
.venv/bin/python n1081b/trigger_mode.py scint --singles --ps-pickup
# 4. verify (expect: C or_veto lemos=[0], D or lemos=[0,1], delay 1440):
.venv/bin/python n1081b/trigger_mode.py status
.venv/bin/python n1081b/set_ps_trigger_delay.py --show
# 5. relaunch run_79, skipping what it already took:
.venv/bin/python run_configs/run_config_stats_optimized.py   # (RESUME=1 already applied, see below)
./start_run.sh run_config_stats_optimized_resume.json
```

Steps 3–5 are the whole restore. Thresholds and mesh state come back **by themselves**
— `scan_control` re-asserts the `stat090` tag on every sub-run.

---

## What run_80 (cosmics) actually changes, and what it does not

| | run_79 (beam) | run_80 (cosmics) | restored by |
|---|---|---|---|
| M4.C (.243 SEC_C) | `or_veto`, lemos `[0]` — Singles gated by the N93B ~1→81 ms window | plain `or`, lemos `[0]` — veto **open** | `trigger_mode.py scint --singles --ps-pickup` |
| M4.D (.243 SEC_D) | `or`, lemos `[0,1]` — PS/flash leg + C-out | `or`, lemos `[1]` — C-out only | same command |
| M2 plastic thresholds | A−118 / B−139 / C−157 / D−134 mV (0.90 MIP) | A−65 / B−78 / C−86 / D−83 mV (0.5 MIP) | `stat090` tag, automatically, per sub-run |
| M6 mesh legs (.245 SEC_B out2/out3) | `status=False` (off) | untouched — **.245 is never contacted** | already off; `stat090` re-asserts anyway |
| **M4.D in0 G&D (PS delay)** | **1440 ns, gate 100, enable_gd=true** | **unchanged — the leg is de-selected from the OR, not re-programmed** | **nothing to do** |
| M1 wall thresholds | A+25 / B+35 / C+34 / D+36 mV | untouched | n/a |
| HV (drift 700 all, resist A540/B540/C525/D520) | — | **identical** | n/a |
| DREAM (RAW, lat 27, n 20, 60 ns, IPD 5, Hwm 2/Lwm 1) | — | **identical** | n/a |

**The important line is the PS delay.** `setup_cosmics_singles_ungated.py` touches only
the two section *functions* on .243 — it does not write any input gate&delay. So the
1440 ns flash framing that run_79 was calibrated on survives the cosmic run untouched,
and there is nothing to re-measure or re-apply when beam returns.

---

## As-built board state, run_79, 2026-07-27 09:14:09

Snapshot: `n1081b/snapshots/run79_asbuilt_2026-07-27.json`

```
.240 M1 walls      sections A/B/C/D = or          thresholds A+25 / B+35 / C+34 / D+36 mV
.241 M2 plastics   sections A/B/C/D = or          thresholds A-118 / B-139 / C-157 / D-134 mV  (0.90 MIP)
.242 M3            sections A/B/C/D = and
.243 M4 trigger    A=or  B=coincidence_gate  C=or_veto  D=or
                     SEC_C lemo_enables = [0]          (Singles, veto-gated)
                     SEC_D lemo_enables = [0, 1]       (PS/flash + C-out)
                     SEC_D in0: status=True enable_gd=True gate=100 delay=1440 ns invert=False
                     SEC_D in1: status=True enable_gd=False delay=0
.244 M5            walls monitoring only; one benign SEC_D.fn_results JSON decode error in the poll
.245 M6            A/B/C = fanout   D = pulse_generator
                     SEC_B out0 status=True   <-- ⚠ MUST STAY TRUE: it gates the SiPM enable on C out0
                     SEC_B out1 status=True   <-- ⚠ MUST STAY TRUE: same, C out1
                     SEC_B out2 status=False  (mesh injection, det A — off)
                     SEC_B out3 status=False  (mesh injection, det C — off)
```

⚠ The `.245` SEC_B out0/out1 caveat is the B→C enable aliasing (see `n1081b/CLAUDE.md`).
Nothing in run_80 goes near .245, but do not "clean up" those legs while the beam is off.

## Operating point (unchanged across both runs)

```
HV     drift  700 V all four (CAEN card 9 ch0-3)
       resist A 540 / B 540 / C 525 / D 520 V (card 5 ch1-4)
       scint PMT bias: 07-19 Y88 equalised set (card 7: 1237/1177/1440/1248/1214/1312/1331/1448;
                                                card 8: 2000 x4)
DREAM  RAW full readout (zero_suppress=False), template Tcm_Mx17_July.cfg
       latency 27, n_samples 20, sample_period 60 ns, IPD 5
       OvrWrnHwm 2 / OvrWrnLwm 1   (RunCtrl cap here is (512-27)//20 = 24 -> 20, so 2 passes through)
gas    Ar/Iso 90/10   target 3He   filter none
```

---

## The resume config

`run_configs/run_config_stats_optimized.py` now honours a `RESUME` env var (added
2026-07-27). It changes exactly one thing — `self.resume` — and writes to a **separate**
json so the original fresh-start config is never clobbered:

```bash
RESUME=1 .venv/bin/python run_configs/run_config_stats_optimized.py
#   -> config/json_run_configs/run_config_stats_optimized_resume.json   (resume=True)
# without RESUME it still writes run_config_stats_optimized.json        (resume=False)
```

That file is **already generated** (2026-07-27 09:57) and verified to differ from the
live config in the single key `resume`. With `resume=True`, `daq_control` skips every
sub-run directory that carries a `.subrun_complete` marker, so run_79 picks up at the
first one it has not finished. At the time of writing, `stat090_0000`–`0014` were
complete and `0015` was in progress.

### ⚠ The interrupted sub-run

> **CHANGED 2026-07-27.** daq_control now marks a manually stopped sub-run complete as
> long as it recorded data, so resume **skips** it rather than re-taking it. The paragraph
> below describes the old behaviour and applies only to runs stopped before that change
> (run_79's `stat090_0015` among them — it was marked by hand on 07-27). To re-take a short
> sub-run now, delete its directory in all three places first; deleting is what makes
> resume re-take it.

Historically, whichever sub-run was live when a run was stopped had **no**
`.subrun_complete` marker, so resume re-took it. Either way a re-take does **not** clean up
on its own — purge the partial sub-run in **all three** places or it collides with its own
leftovers:

```bash
SUB=stat090_0015      # <-- set to whichever sub-run was interrupted
rm -rf /mnt/data/x17/beam_july/runs/run_79/$SUB
rm -rf /home/mx17/july_dream/dream_run/run_79/$SUB
# EOS: xrdfs has NO recursive rm — list, then batch per-file rm through one stdin session
#      (endpoint + path from backup_config.py: root://eospublic.cern.ch,
#       /eos/experiment/ntof/data/x17/july_beam/runs/)
xrdfs root://eospublic.cern.ch ls -u \
  /eos/experiment/ntof/data/x17/july_beam/runs/run_79/$SUB
```

(Look at the directory before deleting it. If the interrupted sub-run ran nearly its full
hour and you would rather keep it, do nothing — since 2026-07-27 daq_control has already
marked it complete and resume will skip it.)

### ⚠ Do not start run_79 with the beam still off

`daq_control` has **no beam gating**. Starting or resuming a beam run while the beam is
off produces empty sub-runs that are still marked complete, and resume will then skip
them forever — permanent holes in the run. **Wait for a real pulse** before step 5:

```bash
cat config/beam_state.json     # want beam_on: true and a small seconds_since_pulse
```

`beam_on: null` means *unknown*, not off.

---

## Files

| file | what it is |
|---|---|
| `run_configs/run_config_stats_optimized.py` | run_79 generator; now takes `RESUME=1` |
| `config/json_run_configs/run_config_stats_optimized.json` | the live run_79 config, fresh-start (untouched) |
| `config/json_run_configs/run_config_stats_optimized_resume.json` | **launch this one when beam returns** |
| `n1081b/snapshots/run79_asbuilt_2026-07-27.json` | as-built 6-board state while run_79 was running |
| `docs/METHOD_readout_window_optimization.md` | how latency 27 / n 20 / delay 1440 were measured |
| `run_configs/run_config_cosmics_optimal_80.py` | the beam-stop cosmic run at this same point |
