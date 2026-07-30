# Beam-off pulser DAQ tests — 2026-07-28

Prepared 2026-07-28 ~10:00 while **run_89 cosmics was live and untouched**. Nothing here has
been applied. Beam has been off since 05:31 (`beam_state.json`, gap ~4.3 h).

Everything below runs with **beam off**. The headline item is the one that was asked for:
**IPD 5/4/3/2/1 at the adopted Hwm 1 / Lwm 0 point**. The rest is a menu, ordered by value
per minute, at the end.

---

## 0. What we already know, so we do not re-measure it

| settled | where |
|---|---|
| Hwm 1 / Lwm 0 is the comb lever: starved 1-10 ms bins 26.7% → 3.3% for −6.5% in-window triggers | run_82, ADOPTED 07-27 |
| **IPD is NOT the evenness lever** — IPD 5→2 made the comb *worse* at both watermarks (26.7→41.1 at Hwm 2, 3.3→6.7 at Hwm 1) | run_82 |
| IPD 2 is integrity-safe on beam: 0.0000% eventId loss, 8/8 FEUs | run_82 |
| `t_ev = n × (4.83 + 0.998 × IPD)` is exact at Hwm 2 (−1%) but reads **~10% low** at Hwm 1 — serialisation adds a per-event handshake | run_82 |
| `Trig_Conf_TrigVetoLen` is **inert** under external (TCM) triggers | comb study 07-19 |
| Raising Hwm above the RunCtrl cap does nothing — RunCtrl clamps it | 07-22 |

**So a flashless pulser cannot and should not re-litigate comb evenness.** What it *can*
measure is the readout/transport envelope, which run_82 left open.

### Lwm 1 is not an option at Hwm 1

The pair is a hysteresis: BUSY asserts at occupancy ≥ Hwm and clears at ≤ Lwm, so **Lwm must
sit strictly below Hwm**. At Hwm 1 the only valid partner is **Lwm 0**, which is what is
running now (verified on hardware this morning — see §1). The Lwm axis is only free at
Hwm ≥ 2, and it has *never* been varied; the ladder carries a point for it.

---

## 1. Verified on live hardware this morning (read-only, no disturbance)

```
$ python3 ~/daq/dream_scripts/feu_trig_counters.py     # pure peeks, safe during a run
slot   id  Hwm  Lwm  Thr  LTh
   1   32    1    0   28    1      ... all 8 FEUs identical
```

**Hwm 1 / Lwm 0 reached the hardware on all 8 FEUs**, `LocThrot = 1`, `OvrThresh = 28`.

⚠ The `accepted / closeDrop / fifoDrop / maxOcc` columns all read 0 in that snapshot and the
tool's closing line ("occupancy never reaches HWM") is **an artefact of not latching** —
statistics are only coherent after the `LatchStat` poke (`--latch`, a write). Do not quote
an unlatched snapshot.

Also re-verified from run_89 data, and used for every estimate below:

- decoded `timestamp` tick = **10 ns** (median dt 27.0 ms at 24.9 Hz vs 27.8 ms expected)
- **min dt = 205 µs** in live cosmics — the Hwm-1 serialised floor, consistent with run_82's 215 µs
- **event size = 196 kB** across 8 FEUs at RAW n20 (4.4 GB of FDF / 22 400 events)

---

## 2. The IPD ladder — `run_configs/run_config_ipd_ladder_pulser.py`

Generated and validated (`config/json_run_configs/run_config_ipd_ladder_pulser.json`,
13 sub-runs, HV identical to run_89's).

```
sub-run               Hwm  Lwm  LTh  IPD  t_ev us   ceiling   GB/s   Gb/s  wire
pulsipd_i5_0000         1    0    -    5      196    5.09 kHz   1.00    8.0  fits
pulsipd_i4_0001         1    0    -    4      176    5.67 kHz   1.11    8.9  fits
pulsipd_i3_0002         1    0    -    3      156    6.39 kHz   1.25   10.0  AT the 10 GbE line rate
pulsipd_i2_0003         1    0    -    2      137    7.32 kHz   1.44   11.5  OVER the link
pulsipd_i1_0004         1    0    -    1      117    8.58 kHz   1.68   13.5  OVER the link
   ... palindrome back up 1 2 3 4 5 ...
pulsipd_h2i5_0010       2    1    -    5      196    5.09 kHz   1.00    8.0   Hwm cost at saturation
pulsipd_h2l0i5_0011     2    0    -    5      196    5.09 kHz   1.00    8.0   the untested Lwm axis
pulsipd_lt0i5_0012      1    0    0    5      196    5.09 kHz   1.00    8.0   LocThrot off
13 points  ~51 GB raw  ~6.6 min wall-clock
```

**The prediction, stated before the run:** throughput plateaus at **IPD ≈ 3** and IPD 2/1 lose
events **on the wire, not in the FEU**. At 196 kB/event the model ceiling puts IPD 3 at exactly
10.0 Gb/s aggregate — the line rate — and IPD 2/1 at 11.5/13.5 Gb/s.

That verdict is only meaningful if FEU-side and host-side loss can be told apart, which is
what the `accepted` counter log is for (§4). And note the standing caveat: **this measures
the sustained envelope, not a licence for beam.** Beam dumps ~11 events into a rested buffer
and then idles; it never sustains kHz. A point that fails here may still be fine on beam,
and a point that passes is not thereby *better* on beam — run_82 says the opposite.

Sizing: points are **event-capped** (`daq_run_events = 20 000/FEU`, ~4 s, ~3.9 GB each) with
`run_time = 0.5 min` only as a backstop, because a saturating point writes up to 1.7 GB/s.

---

## 3. Procedure

### 3a. Before touching anything

```bash
cd ~/PycharmProjects/nTof_x17_DAQ
cat config/beam_state.json | python3 -m json.tool | grep -E 'beam_on|seconds_since_pulse'
df -h /mnt/data                       # need ≳ 120 GB free (51 GB raw + decode products)
ls config/n1081b_access/*.lock        # no other process on .243 / .245
```

**Disarm the automatic changeover** — if beam returns mid-ladder, `mode_watcher` would stop
the run and start a beam run underneath you:

```bash
touch config/.mode_watcher_disarmed          # Flask "Run Mode" card shows this too
```

**Keep 51 GB of pulser junk off EOS** — set `exclude_runs` in `config/backup_config.json` to
the run number you are about to allocate (leave `processor_config.json` alone: the analysis
needs `decoded_root`).

**Settle the disk-vs-wire question first** (2 min, and it changes how §2's result is read —
if the SSD cannot take 1.25 GB/s then a plateau at IPD 3 is the *disk*, not the link):

```bash
dd if=/dev/zero of=/mnt/data/x17/.speedtest bs=1M count=20000 oflag=direct status=progress
rm /mnt/data/x17/.speedtest
```

Run it **after** the cosmics run is stopped, so it is not competing with a live writer.

### 3b. Stop cosmics, apply the pulser trigger

```bash
./bash_scripts/stop_run.sh                       # wait for daq_control to actually exit

# capture the pre-state (both read-only) so restore can be checked against it
.venv/bin/python n1081b/trigger_mode.py status
.venv/bin/python n1081b/set_pulser.py --show
.venv/bin/python n1081b/set_veto_open.py --show

# APPLY — M6 (.245) then M4 (.243), sequentially, never in parallel
.venv/bin/python n1081b/set_pulser.py --fixed --period 5000 --width 100
.venv/bin/python n1081b/set_veto_open.py --lemos 4
```

- `--fixed --period 5000` = deterministic **200 kHz**, 23–40× every ceiling in the ladder, so
  every point saturates. The 5 µs drive granularity quantises the measured dt floor, and the
  steps being resolved are ~20 µs — 4:1, adequate. 200 kHz is not new: the 07-20 burst study
  drove the FEUs at exactly this rate.
  **Fallback if the read-back disagrees:** `--period 25000` (40 kHz) still saturates every
  point but quantises at 25 µs, which is *coarser than the effect* — a fallback, not a preference.
- `--lemos 4` enables the pulser **alone**, dropping singles, so there is no cosmic
  contamination. It touches only M4.C — D, the pulser and the 1440 ns PS delay are untouched.

### 3c. Allocate, generate, launch

```bash
RUN_NUM=$(.venv/bin/python run_num.py --allocate | tail -1)   # peeked at 90 this morning
echo "run_$RUN_NUM"                                   # tail -1: a degraded allocation warns on stdout
RUN_NUM=$RUN_NUM .venv/bin/python run_configs/run_config_ipd_ladder_pulser.py

# start the FEU counter log FIRST, in its own window — it is the instrument, not a monitor
mkdir -p ~/beam_july/analysis/flash_comb/pulser_ipd
python3 ~/daq/dream_scripts/feu_trig_counters.py --latch --watch 0.5 --quiet \
    --csv ~/beam_july/analysis/flash_comb/pulser_ipd/counters_run${RUN_NUM}.csv

./bash_scripts/start_run.sh run_config_ipd_ladder_pulser.json   # BARE filename
```

### 3d. Watch the first point, then let it run

The first sub-run is IPD 5 = the standing production point, so it **must** come out clean. If
it does not, the setup is wrong, not the IPD.

⚠ **Confirm the event cap took, on the first sub-run.** The whole size budget rests on it:
if `Sys DaqRun Events` is ignored, each point runs the 30 s backstop at up to 1.7 GB/s and
13 points would be ~660 GB — the disk. The template does carry the key
(`Sys DaqRun Events 0`), so the substitution has somewhere to land, but verify rather than
assume:

```bash
grep -E 'DaqRun (Events|Time)|InterPacket|OvrWrn' \
    /mnt/data/x17/beam_july/runs/run_$RUN_NUM/pulsipd_i5_0000/raw_daq_data/Tcm_*.cfg
# expect: Events 20000, Time 30, InterPacket_Delay 5, Hwm 1 / Lwm 0
```

The first point should end after **~4 s** of data (~3.9 GB, ~20 files), not 30 s.

```bash
watch -n5 'df -h /mnt/data | tail -1; ls /mnt/data/x17/beam_july/runs/run_'$RUN_NUM'/*/raw_daq_data/*.fdf 2>/dev/null | wc -l'
```

**Abort** (`./bash_scripts/stop_run.sh`) if: the first point writes ≫4 GB (cap ignored);
`/mnt/data` free drops below ~60 GB; every point runs the full 30 s backstop (nothing is
saturating → the trigger did not land); or the counter log shows all-zero `accepted` (latch or
routing wrong).

### 3e. Restore — one command, plus the pulser

```bash
# Ctrl-C the counter logger first (it releases nothing, but keep the log intact)
.venv/bin/python n1081b/set_pulser.py                 # back to design Poisson 1.5 ms
./switch_mode.py cosmics --go                         # or: ./switch_mode.py beam --go
rm config/.mode_watcher_disarmed
# revert exclude_runs in config/backup_config.json
```

`--go` stops the run, allocates a number, regenerates the config, re-applies the routing
(`setup_cosmics_singles_ungated.py`, which puts M4.C back), **reads it back and checks it**,
launches, and verifies the cfg RunCtrl received. It refuses if a board lock is held.

Delete the pulser run once §4 has been run — it is 51 GB of characterization data with no
physics in it.

---

## 4. Analysis

```bash
/home/mx17/ana/.venv/bin/python \
  ~/beam_july/analysis/flash_comb/tools/pulser_ladder_analysis.py \
  --run run_$RUN_NUM \
  --counters ~/beam_july/analysis/flash_comb/pulser_ipd/counters_run${RUN_NUM}.csv
```

Written and smoke-tested against a real run_89 sub-run. One row per point: achieved rate vs
the model ceiling, the dt floor vs `t_ev`, achieved Gb/s vs the line rate, eventId gap
fraction, cross-FEU spread, and the FEU-side `accepted` count. Corruption criteria are the
same ones `lib_deadtime.py` and `raw_ipd_analysis.py` use (gap > 0.1%, spread > max(2, 2%)),
so verdicts are comparable with every earlier IPD study.

**How to read it.** The run is event-capped, so each FEU should accept exactly 20 000
triggers:

| `accepted` | `decoded_root` events | meaning |
|---|---|---|
| 20 000 | 20 000 | clean — nothing lost |
| 20 000 | **fewer** | the FEU took the trigger and the event died **downstream** (wire/host/disk) |
| **fewer** | matches | the throttle is **upstream** — BUSY/watermark, the FEU never took it |

The pooled table also prints `excess = measured floor − model`. Flat across the ladder ⇒ the
Hwm-1 handshake is a fixed per-event cost and lower IPD keeps paying off. Growing as IPD
falls ⇒ something else binds; check the Gb/s column against 10 before blaming the FEU.

⚠ `closeDrop`/`fifoDrop` are 8-bit and `maxFIFOocc` 6-bit — they **wrap** under saturation.
Nonzero means "dropping", nothing more. Only `accepted` (32-bit) is a quantity.

---

## 5. Other pulser tests worth the beam-off window

Ordered by value per minute. A–B are the ones I would actually spend today's window on.

| # | test | cost | why |
|---|---|---|---|
| **A** | **the IPD ladder above** | ~10 min | asked for; settles the envelope below IPD 5 and finds whether the wall is the FEU or the link |
| **B** | **rested-buffer burst size at Hwm 1** (`rest_toggle.py`) | ~10 min | **the best of the rest.** At Hwm ~11 a rested buffer accepted **11–12** events in one burst and a busy one exactly **3** — that 11 *is* the flash's tooth-0. At Hwm 1 it should collapse to **1**. That is the mechanism behind run_82's −12% whole-gate loss, and run_82 explicitly called Hwm "empirical, not modelled". This turns the adopted production setting from a measured effect into an understood one, with no beam needed. |
| **C** | **paced-rate threshold at the production point** | ~12 min | fixed 100/200/500/1000 Hz at IPD 5. The 07-20 study found paced ≤200 Hz gives 100% accept and **no comb at all** (at n32/IPD10). Re-measuring the pacing threshold at n20/Hwm 1 gives the number that decides whether an N1081B per-trigger hold-off is worth building — the one comb optimisation that was validated in principle and never implemented. |
| **D** | **n_samples ceiling scaling at Hwm 1** (n16/20/32 at IPD 5) | ~5 min | `t_ev ∝ n`, so n20→n16 buys ~20% ceiling and 20% less data for a 20% shorter window. We know the IPD term is handshake-limited at Hwm 1; whether the *n* term is too is unmeasured, and it prices the window length. |
| **E** | **Hwm × Lwm 2×2** (Hwm 2/3 × Lwm 0..2) | ~8 min | only if the ladder's `h2l0i5` tail point moves. The Lwm axis has never been varied. |
| **F** | **MultiPack / jumbo tuning at RAW n20** | ~5 min | only if the ladder shows the **link** is the wall. Fuller datagrams could bring IPD 3 under the line rate. ⚠ known hazard: above MTU − MaxDataPacketSize the last add overruns the frame and the last packet is dropped. |

Deliberately **not** on the list: `TrigVetoLen` (proved inert under external triggers), Hwm
above the RunCtrl cap (clamped — null by construction), and any pulser re-run of the comb
evenness question (run_82 settled it, and there is no flash to anchor to).

---

## 6. Files added/changed for this (all inert until launched)

| file | what |
|---|---|
| `run_configs/run_config_ipd_ladder_pulser.py` | the ladder generator (new) |
| `config/json_run_configs/run_config_ipd_ladder_pulser.json` | generated, run_90, 13 sub-runs |
| `~/beam_july/analysis/flash_comb/tools/pulser_ladder_analysis.py` | the read-out (new, smoke-tested) |
| `~/daq/dream_scripts/feu_trig_counters.py` | `--csv` / `--quiet` logging + a counter-width warning |
| `projections/run_stats.py` | `NON_PHYSICS_BEAM_TYPES` — `beam_type: pulser` is skipped, so 260 k pulser events stay out of the physics projection |
