# Beam-off pulser characterisation of the DREAM/FEU DAQ — 2026-07-28

**Runs 90, 92, 94. Beam off throughout. ~20 minutes of data taking, 27 measured points.**

Slides: `~/beam_july/analysis/daq_pulser_2026-07-28/daq_pulser_2026-07-28.pdf`
Numbers: `~/beam_july/analysis/daq_pulser_2026-07-28/measurements.py` (single source of truth)
Data: `/mnt/data/x17/beam_july/runs/run_{90,92,94}`

---

## 0. Executive summary

| # | Finding | Consequence |
|---|---|---|
| 1 | The DAQ ceiling is a **per-FEU output cap of ~83 MB/s** (~0.66 Gb/s), invariant across 4× in event size and 8× in FEU count. Aggregate scales **linearly** with FEU count. | Not the host, not the 10 GbE aggregate, not the disk, not IPD. Adding FEUs adds capacity. |
| 2 | **IPD 5 → 1 buys nothing.** The readout floor shortens linearly (20 µs/step) but throughput is identical at every IPD; duty cycle falls 70.4% → 43.7% and cancels it exactly. | Leave IPD at 5. The wire, not the readout, sets the rate. |
| 3 | **The watermark sets the maximum burst size**, and Hwm 1 makes it *exactly 1* — zero exceptions in 3361 consecutive events under a 200 kHz drive. | Explains the Hwm 1 production setting adopted by run_82 on empirical grounds, and prices it. |
| 4 | **MultiPackThr 4888 is already optimal** despite looking mis-set against the MTU-9000 NIC. Raising it to the "correct" 6720 buys +1.7% and produces the only eventId loss measured all day. | **Do not "fix" the mismatch.** Needs a comment in the template. |
| 5 | `Main_Trig_LocThrot` (documented UNVERIFIED) **is** what makes OvrWrnHwm gate external TCM triggers. | Never set it to 0. It is the master switch under the whole Hwm mechanism. |
| 6 | **`/` was ~50 minutes from full** when cosmics were stopped; `/mnt/data` is a 122 MB/s spinning disk, not an SSD. | Standing outage risk. Needs a purge policy. |

Three plausible "improvements" were tested and **all three are wrong**: lower IPD, higher
MultiPackThr, LocThrot 0.

---

## 1. Method

### 1.1 Why a pulser at all

Production runs at ~100 Hz × 196 kB ≈ 20 MB/s aggregate — about **3% of the ceiling**. The
readout envelope is therefore invisible in normal running and can only be measured by
offering the DAQ more triggers than it can take.

### 1.2 The trigger

M6.D (`192.168.10.245`) internal generator, **deterministic, period 5000 ns = 200 kHz**,
width 100 ns, routed into M4.C lemo4 **alone**:

```bash
.venv/bin/python n1081b/set_pulser.py --fixed --period 5000 --width 100
.venv/bin/python n1081b/set_veto_open.py --lemos 4
```

- `--lemos 4` enables the pulser alone and drops singles, so there is **no cosmic or source
  contamination** in the trigger. It touches only M4.C — section D, the pulser and the
  1440 ns PS delay are untouched, so restoring C is the whole restore.
- **Why 200 kHz and not the 20 kHz of the 2026-07-22 recipe:** a *fixed* drive quantises the
  measured inter-event floor to the drive period. The effects being resolved are ~20 µs
  steps, so a 50 µs granularity (20 kHz) would have blurred them. 200 kHz gives 5 µs
  granularity and is ≥23× every ceiling in the study, so every point saturates. 200 kHz is
  not new — the 2026-07-20 burst study drove the FEUs at exactly this rate.

Restore: `set_pulser.py` (back to design Poisson 1.5 ms) then either
`./switch_mode.py cosmics --go` or `n1081b/setup_cosmics_singles_ungated.py` for routing only.

### 1.3 Held fixed

RAW readout, latency 27, `n_samples` 20, 60 ns sampling, Hwm 1 / Lwm 0, IPD 5, production HV
(drift 700, resist A540/B540/C525/D520 + the scintillator PMT set) — i.e. the run_84/run_89
production point, except where a point deliberately varies one knob.

### 1.4 Instruments

1. **Per-event timestamps** from `decoded_root` (`nt.timestamp`, **10 ns ticks**). The tick
   was re-verified on run_89 cosmics: median dt 27.0 ms at 24.9 Hz vs ln2/rate = 27.8 ms.
2. **FEU trigger counters** over slow-control UDP, logged live at 2 Hz through the whole run:
   ```bash
   dream_scripts/feu_trig_counters.py --latch --watch 0.5 --quiet --csv <path>
   ```
   `--csv`/`--quiet` were added for this campaign. Counters reset when RunCtrl starts a
   sub-run, so a straight-through log self-segments by sub-run with no coordination.
3. **Integrity gate** — the same criteria `lib_deadtime.py` and `raw_ipd_analysis.py` use, so
   verdicts are comparable with every earlier IPD study: eventId gap fraction > 0.1%, or
   cross-FEU count spread > max(2, 2%). **Throughput is never quoted for a point that fails
   this** — a saturated link does not error, it silently drops datagrams, and dropping events
   makes a distribution look *better*.

### 1.5 Definitions

| term | definition |
|---|---|
| floor | dt p10, µs. Under saturation this is the hard edge of the interval distribution = one serialised readout. |
| model | `t_ev = n × (4.83 + 0.998 × IPD)` µs — the pre-existing readout model. |
| duty | `n_events × floor / duration` — fraction of wall-clock spent reading out. |
| per-FEU | MB/s carried by **one** FEU = rate × kB/event/FEU. **The invariant.** |

### 1.6 ⚠ Counter-width traps

`accepted` is a full 32-bit register and is the only usable throughput quantity. `closeDrop`
and `fifoDrop` are **8-bit** and `maxFIFOocc` **6-bit** — they wrap within milliseconds under
saturation. Nonzero means "dropping", nothing more. Separately, **unlatched counters read 0**,
and the tool's closing line ("occupancy never reaches HWM") is then an artefact — always
`--latch`.

---

## 2. run_90 — the IPD ladder at Hwm 1 / Lwm 0

Config `run_configs/run_config_ipd_ladder_pulser.py`. Palindrome **5 4 3 2 1 | 1 2 3 4 5**
plus watermark tails. Both repeats of every IPD agreed to **0 µs** on the floor and 0.3% on
the rate.

| IPD | floor µs | model | excess | duty % | kHz | MB/s | gap% | spread |
|---|---|---|---|---|---|---|---|---|
| 5 | **210** | 196 | +14 | 70.4 | 3.35 | 657 | 0.000 | 0 |
| 4 | **190** | 176 | +14 | 63.8 | 3.36 | 658 | 0.000 | 0 |
| 3 | **170** | 156 | +14 | 57.1 | 3.36 | 658 | 0.000 | 0 |
| 2 | **150** | 137 | +13 | 50.4 | 3.36 | 658 | 0.000 | 0 |
| 1 | **130** | 117 | +13 | 43.7 | 3.36 | 658 | 0.000 | 0 |
| Hwm 2 / Lwm 1 | 195 | 196 | −1 | 66.0 | 3.38 | 663 | 0.000 | 0 |
| Hwm 2 / Lwm 0 | 5 | 196 | −191 | 1.7 | 3.36 | 659 | 0.000 | 0 |

### 2.1 The readout model holds to IPD 1, with a flat serialisation cost

`t_ev = n × (4.83 + 0.998 × IPD)` is exact, and the Hwm-1 handshake is a **flat +13/+14 µs**,
not a growing fraction. 20 µs per IPD step = 0.998 × n_samples, dead on. This closes the
question run_82 could only guess at from two points — it had wondered whether the handshake
grows as IPD falls, which would have meant diminishing returns. It does not.

The handshake is **Hwm-1-specific**: at Hwm 2 / Lwm 1 the floor is the bare model (−1 µs),
reproducing run_82's "exact at Hwm 2" to the microsecond.

### 2.2 ⚠ But throughput does not move — the prediction failed

Delivered throughput is **identical at every IPD**. Duty cycle falls 70.4% → 43.7%, exactly
cancelling the faster readout.

The prediction recorded before the run was a plateau at **IPD 3**, where the model ceiling
(6.39 kHz × 196 kB) crosses 10.0 Gb/s. That was wrong in two ways: the real ceiling is lower
(5.3 Gb/s aggregate) **and** it is IPD-independent, so there is no plateau — there is a flat
line. run_92 was written to find out why.

### 2.3 IPD 1 is integrity-clean

0.000% eventId gaps and zero cross-FEU spread at **every** IPD down to 1, 8/8 FEUs, with zero
`closeDrop`/`fifoDrop` and `maxFIFOocc` pinned at 1 = Hwm (so the watermark is biting). This
extends run_82's "IPD 2 is safe" to IPD 1 — the first time it has been measured. It is
**still not a reason to lower IPD on beam**: it buys no throughput, and run_82 showed IPD 2
makes the acceptance comb *worse*.

---

## 3. run_92 — what is the clamp?

Config `run_configs/run_config_host_clamp_pulser.py`. Two knobs change bytes/event **without**
changing the trigger rate.

| point | kHz | kB/ev/FEU | aggregate MB/s | **per-FEU MB/s** |
|---|---|---|---|---|
| n10, 8 FEU | 6.75 | 12.25 | 662 | **82.7** |
| n20, 8 FEU | 3.35 | 24.50 | 657 | **82.1** |
| n40, 8 FEU | 1.68 | 49.00 | 659 | **82.3** |
| n20, 4 FEU | 3.39 | 24.50 | 332 | **83.1** |
| n20, 2 FEU | 3.40 | 24.50 | 167 | **83.3** |
| n20, 1 FEU | 3.41 | 24.50 | 84 | **83.5** |

### 3.1 The n_samples ladder alone is ambiguous — and nearly produced a wrong answer

Rate ∝ 1/bytes with aggregate pinned at ~659 MB/s looks *exactly* like an aggregate byte
budget, and that is what it was initially read as. n40 is the control that confirms the model
still governs when the FEU is the slow element: its FEU ceiling (2.46 kHz) is *below* the
clamp, and it landed on 1.68 kHz — the byte prediction, not the FEU one.

### 3.2 The FEU-count ladder is decisive

Dropping FEUs cuts aggregate bytes per event while leaving **each FEU's own readout, and
hence the 210 µs floor, untouched**. So:

- aggregate-byte clamp ⇒ rate rises toward the floor limit 1/210 µs = 4.76 kHz
- per-FEU clamp ⇒ rate stays ~3.4 kHz and aggregate falls

Measured: the rate **did not rise** (3.39 / 3.40 / 3.41 kHz) and aggregate fell 332 → 167 →
84. **Only per-FEU MB/s is invariant — 82–84 across 4× in event size and 8× in FEU count.**
The 658 MB/s aggregate was simply 8 × 83.

### 3.3 Why IPD does nothing

At n20 one FEU ships 24.5 kB/event; at 83 MB/s that is **~295 µs of wire time**, and the
measured mean spacing is 298 µs. The SCA readout floor is 210 µs, falling to 130 µs at IPD 1
— **always shorter than the wire**. IPD shortens the fast half of a pipeline whose slow half
never moves, so duty cycle falls and throughput does not.

This is confirmed independently in run_94: with MultiPack off the wire time rises past the
readout and **the floor itself moves, 210 → 555 µs** — the only time anything but n or IPD has
moved it.

### 3.4 LocThrot — a register documented UNVERIFIED

`dream_daq_control.py` records `Main_Trig_LocThrot` (0x100008 bit 30) as "UNVERIFIED whether
this gates EXTERNAL (TCM) triggers or only self-triggers".

| LocThrot | events kept | dt floor | accepted rate |
|---|---|---|---|
| 1 (default) | 3358 | 210 µs | 3.35 kHz |
| **0** | **28** | **5 µs** | **200.00 kHz** = the exact drive rate |

With it off the FEU accepts **every** trigger the TCM offers, fills the ~24-deep DREAM
derandomiser ((512−27)/20 = 24; 28 survived) at wire speed, and loses the rest.

⇒ **LocThrot = 1 is the mechanism by which OvrWrnHwm gates external triggers.** It is the
master switch under the entire Hwm 1 production setting. **Never set it to 0 on a real run.**

---

## 4. The mechanism behind Hwm — settled

run_82 adopted Hwm 1 on measured evidence but explicitly called its mechanism "empirical, not
modelled". Under a 200 kHz drive — a trigger on offer every 5 µs — the FEU *will* burst if the
watermark logic permits it.

| config | events | min dt | intervals < 50 µs | **max burst** |
|---|---|---|---|---|
| Hwm 1 / Lwm 0, IPD 5 | 3361 | 210 µs | **0** | **1** |
| Hwm 1 / Lwm 0, IPD 1 | 3368 | 130 µs | **0** | **1** |
| Hwm 2 / Lwm 1, IPD 5 | 3398 | 5 µs | 1 | 2 |
| Hwm 2 / Lwm 0, IPD 5 | 3370 | 5 µs | 1685 (50%) | **22** |

**At Hwm 1 there is not one single sub-floor interval in 3361 (or 3368) consecutive events.**
Strict serialisation, no exceptions.

So the chain behind the whole comb story is now measured end to end:

- **Hwm ~11** (the old RunCtrl-clamped cap) let the flash dump ~11 triggers into tooth-0 —
  the origin of the comb described in `dream-flash-comb-mechanism` / `ipc-arrival-vs-comb`.
- **Hwm 2 / Lwm 1** caps the burst at 2.
- **Hwm 1 / Lwm 0** caps it at **1**.

That is *why* Hwm 1 flattened the comb, and the −12% whole-gate trigger loss run_82 measured
is its exact price: with no burst absorption, any trigger arriving during a readout is refused.

**Consequence for the flash:** at Hwm 1 the ~11 simultaneous flash triggers are taken one at a
time at the ~295 µs wire-limited cost, so the flash needs ~3.2 ms to absorb and contributes
**one** event to tooth-0 instead of eleven.

### 4.1 ⚠ Unresolved: why Hwm 2 / Lwm 0 bursts to 22

Draining to *empty* before BUSY clears opens a window the BUSY feedback loop is evidently too
slow to close — 22 is close to the derandomiser depth of 24. A naive reading of the hysteresis
predicts a burst of 2, not 22. **The mechanism was not chased.** Production is unaffected
(Hwm 1 asserts BUSY on the first event and the measured max burst is 1), but anyone
considering Lwm as a comb lever needs to understand this first.

---

## 5. run_94 — the MultiPack trap

Config `run_configs/run_config_multipack_pulser.py`. The template carries:

```
# UdpChan_MultiPackThr = Eth_MTU - MaxSmpData - 60
# MaxSmpData = 2220 bytes
# Eth_Mtu depends on Eth NIC but is less 8kbytes
# E.g. for MTU 7k th parameter is 4888
Feu * UdpChan_MultiPackEnb 1
Feu * UdpChan_MultiPackThr 4888
```

4888 + 2220 + 60 = 7168 = 7 KiB — so **the standing value is sized for a 7168-byte frame**,
while the host NIC `enp4s0` is at **MTU 9000**. Every datagram closes ~1800 bytes short. This
looks exactly like a setting left un-updated by the 10 GbE switch swap.

| point | Thr | implied frame | floor µs | kHz | **MB/s per FEU** | gap% |
|---|---|---|---|---|---|---|
| MultiPack **OFF** | — | — | **555** | 1.51 | **36.9** | 0.000 |
| Thr 2888 | 2888 | 5168 | 210 | 2.82 | 69.1 | 0.000 |
| **Thr 4888 (standing)** | 4888 | 7168 | 210 | 3.35 | **82.2** | 0.000 |
| Thr 5912 | 5912 | 8192 | 210 | 3.39 | 83.0 | 0.000 |
| Thr 6720 | 6720 | 9000 | 210 | 3.41 | 83.6 | **0.029** |
| Thr 4888 (close bracket) | 4888 | 7168 | 210 | 3.35 | **82.1** | 0.000 |

Opening and closing brackets agree to 0.1%. Every override was verified present in the applied
per-sub-run cfg before any data was read.

### 5.1 Verdict: keep 4888

- **The knob is live** — MultiPack OFF costs **55%**, Thr 2888 costs 16%. Those two controls
  were included precisely so a null at the top of the ladder could not be mistaken for an
  **inert** knob, which is the trap the 2019 "raise the HWM" sweep fell into (RunCtrl silently
  clamped it, so nothing was ever varied and the null meant nothing).
- **But the curve has already saturated at 4888.** 5168 → 7168 buys +19%; 7168 → 9000 buys
  **+1.7%**. There is a fixed per-datagram cost fully amortised by ~7 KiB.
- ⚠ **Thr 6720 is the only point in runs 90/92/94 with any eventId loss (0.029%)** — under the
  0.1% threshold, but every other point is exactly 0.000%, and 6720 is precisely the value
  that pushes the datagram to the host MTU. This matches the documented hazard (above
  MTU − MaxDataPacketSize the last add overruns the frame and the last packet is dropped) and
  suggests the FEU transmit side caps below 9000, as the template's own note implies.
  **+1.7% for the only nonzero packet loss is a bad trade.**

⇒ **The per-FEU 83 MB/s ceiling is not frame under-filling.** It is intrinsic to the FEU
transmit path. Note also that jumbo cannot be verified by ping on this subnet (a jumbo ping is
a known false failure), so this run *is* the end-to-end jumbo path test.

---

## 6. Infrastructure findings

These are unrelated to the physics and more urgent than any of it.

- ⚠ **`/` (sdb2, 477 GB SK hynix SSD) was at 97% — 15 GB free.** RunCtrl stages *every*
  sub-run to `~/july_dream/dream_run/` there at **924 MB per sub-run** (datrun + pedthr), of
  which only the ~305 MB datrun part is copied to `/mnt/data`. Cosmics were adding 4.4 GB per
  15 min: **the root filesystem was ~50 minutes from full when the run was stopped.** 335 GB
  of old staging (run_85…run_89) had accumulated and **nothing purges it automatically.**
- run_90 filled `/` to 100% and **starved its last three sub-runs**. Recovered by
  byte-size-verifying every datrun FDF against `/mnt/data` before purging run_90's staging.
- ⚠ **`/mnt/data` is a spinning ST4000NE001 IronWolf, not an SSD** — `dd oflag=direct`
  measures **122 MB/s**. Any note saying "SSD → HDD → EOS" means sdb2 → sda4.
- RAM is 15 GB ⇒ `vm.dirty_ratio` 20% ⇒ **~3 GB dirty ceiling**. The campaign only worked
  because each point was capped near 660 MB, which lands in page cache and flushes during the
  inter-sub-run gap — so the **disk never backpressured the DAQ mid-point while the wire still
  ran at full rate**. This is why the measurement is valid despite a 122 MB/s disk.
- ⚠ **`Sys DaqRun Events` is not exact**: 2000/FEU requested delivered 2986–3420. Budget 1.7×.

---

## 7. Recommendations

**Change nothing in the readout configuration.** Hwm 1 / Lwm 0, IPD 5, n = 20,
MultiPackThr 4888 and LocThrot 1 are all either optimal or immaterial at the measured
operating point.

Do act on:

1. **A purge policy for `~/july_dream/dream_run`.** This is a standing outage risk, not a
   one-off. Candidate rule: drop a run's staging once every `datrun` FDF is size-verified on
   `/mnt/data` (that is exactly the check used to recover run_90).
2. **A comment in `Tcm_Mx17_July.cfg`** recording that 4888-vs-MTU-9000 is *deliberate and
   measured*, so nobody "corrects" it later and loses data for +1.7%.
3. **If more event rate is ever needed:** reduce `n_samples` or move to ZS. Nothing else will
   do it — not IPD, not the host, not the switch, not the disk.

### Open questions

- **What sets 83 MB/s per FEU?** It is ~66% of a 1 GbE port and is not framing. Next
  candidates are the FEU NIC/firmware transmit path. If the FEU ports are 1 GbE this is a hard
  wall and no host- or switch-side upgrade can move it.
- **Why does Hwm 2 / Lwm 0 burst to 22** rather than 2 (§4.1).
- **ZS at the current operating point** is unmeasured. ⚠ Note that with Y88 and Cs137 sources
  near the MMs, a ZS measurement taken now would be occupancy-inflated and pessimistic.

---

## 8. Reproducing this

```bash
# 1. preconditions: beam off, no run active, ≥20 GB free on / and /mnt/data
touch config/.mode_watcher_disarmed          # or a beam return will start a run underneath you
./bash_scripts/stop_run.sh                   # if a run is live; wait for daq_control to exit

# 2. trigger
.venv/bin/python n1081b/set_pulser.py --fixed --period 5000 --width 100
.venv/bin/python n1081b/set_veto_open.py --lemos 4

# 3. counter log (the instrument that separates FEU-side from wire-side loss)
tmux new-session -d -s feu_counters \
  "cd ~/daq && python3 dream_scripts/feu_trig_counters.py --latch --watch 0.5 --quiet \
   --csv ~/beam_july/analysis/flash_comb/pulser_ipd/counters_runNN.csv"

# 4. run
RUN_NUM=$(.venv/bin/python run_num.py --allocate | tail -1)
RUN_NUM=$RUN_NUM .venv/bin/python run_configs/run_config_ipd_ladder_pulser.py
# add run_NN to exclude_runs in config/backup_config.json — this is junk data, keep it off EOS
./bash_scripts/start_run.sh run_config_ipd_ladder_pulser.json

# 5. analyse
/home/mx17/ana/.venv/bin/python \
  ~/beam_july/analysis/flash_comb/tools/pulser_ladder_analysis.py \
  --run run_NN --counters .../counters_runNN.csv

# 6. restore
tmux kill-session -t feu_counters
.venv/bin/python n1081b/set_pulser.py                       # design Poisson 1.5 ms
./switch_mode.py cosmics --go                               # or: beam --go
rm config/.mode_watcher_disarmed
```

⚠ If a sub-run's staging must be reclaimed mid-campaign, verify first:
compare every `*_datrun_*.fdf` in `~/july_dream/dream_run/run_NN/<subrun>/` against
`/mnt/data/x17/beam_july/runs/run_NN/<subrun>/raw_daq_data/` by **size**, and only then
delete. The pedthr FDFs in staging are never copied out and are disposable.

⚠ Do **not** attempt a rested-buffer burst test by driving `rest_toggle.py` in a tight
stop/start loop on .245 during a live run. Rapid board writes are the pattern that has wedged
N1081B modules before, and the burst question was answerable from the saturating data anyway
(§4).

---

## 9. Artefacts

| path | what |
|---|---|
| `run_configs/run_config_ipd_ladder_pulser.py` | run_90 — IPD ladder + watermark tails |
| `run_configs/run_config_host_clamp_pulser.py` | run_92 — n_samples × FEU-count discriminator + LocThrot |
| `run_configs/run_config_multipack_pulser.py` | run_94 — MultiPack / jumbo ladder |
| `docs/PLAN_2026-07-28_pulser_ipd_ladder.md` | the pre-run plan, procedure and abort criteria |
| `~/beam_july/analysis/flash_comb/tools/pulser_ladder_analysis.py` | per-point rate / floor / integrity / accepted |
| `~/beam_july/analysis/daq_pulser_2026-07-28/` | slides, figures, `measurements.py` |
| `dream_scripts/feu_trig_counters.py` | gained `--csv` / `--quiet` + the counter-width warnings |
| `projections/run_stats.py` | gained `NON_PHYSICS_BEAM_TYPES` so `beam_type: pulser` stays out of the physics projection |

**Housekeeping:** `config/backup_config.json` excludes `run_90`, `run_92`, `run_94` from EOS.
Revert that when those runs (~15 GB of junk pulser data) are deleted.
