# Where the remaining IPC yield actually is — DAQ × HV joint analysis, 2026-07-23

> ## ⚠ CORRECTION 2026-07-23 (later) — §3 is REFUTED by run_67 data. Read this first.
>
> §3 below models a "front edge" at `gate + N_buf x cycle` = 4.45 ms, on the assumption (taken
> from the 07-22 `hwm_10g` write-up, which asserted it rather than measured it) that the rested
> SCA buffer dumps ~11 flash-blind events at the 1 ms gate and blocks the TCM until it drains.
> **Measured directly in run_67, that does not happen.**
>
> Three run_67 subruns (r550 / r530 / r520, drift 600), flash-anchored:
>
> | | 1–2 ms | 2–4 ms | 4–10 ms | 10–20 ms | 20–80 ms |
> |---|---|---|---|---|---|
> | ev/ms/spill | **3.8** | 2.00 | 2.00 | 1.92 | 0.71 |
>
> - **First event lands at 0.996–1.007 ms** — i.e. the DAQ is live the instant the N93B gate
>   opens. There is no 3.45 ms dead block and no front edge to move.
> - The reason: the ~11-event "flash burst" is the **discriminator ringing at t≈0**, which the
>   1 ms gate vetoes. It never enters the FEU, so there is no rested-buffer dump to drain.
> - **The 2.0 ev/ms plateau is the TRIGGER, not a DAQ ceiling** — the DAQ demonstrably delivers
>   **3.8 ev/ms** one millisecond earlier, which is *above* the 3.18 ev/ms single-event cycle
>   (SCA buffering absorbs it). So there is no cap to raise anywhere in the IPC window.
>
> **Consequences.** The claim in §3 that the read-clock change is worth "+10 % of IPC yield" is
> **wrong** — the original "headroom, not yield" framing in `CLOCK_RATE_SCAN_2026-07-23.md` was
> right, and I was wrong to correct it. Likewise the ZS/IPD-2 front-edge gain (60 %→84 %) does
> not exist: coverage is already ~100 % from 1 ms. **No DAQ knob in this document buys IPC.**
>
> **What survives:** §1 (DreamMask is dead — Dreams read in parallel), §3's *invariance* result
> (NbOfSamples cannot move the front edge — now doubly moot), and **§4, the HV analysis, which
> is untouched and is where the remaining yield is.** §5a (Hwm front-edge test) is withdrawn.
> §7's "do not move the 1 ms gate later" advice stands, for a better reason: the gate is what
> vetoes the flash ringing.

**Question asked:** with the read clock now maxed, is there more juice in the DREAM acquisition +
detector HV for *tracks near the IPC peak*?

**Answer in one line:** yes, but not where the DAQ work has been looking. Sustained rate is solved
and irrelevant. **The only DAQ quantity that still buys IPC is the *front edge* of the live
window** — the time after the flash at which the DAQ becomes continuously live — and it is
currently set by the DAQ, not the detector. Moving it from 4.45 ms to ~2 ms is worth **+40 %**
of in-gate IPC, and the HV that maximises IPC-weighted track probability is **525 V, not the 530 V
we run** (+18 %).

Sources: measured cycle times (`docs/CLOCK_RATE_SCAN_2026-07-23.md`, `hwm_10g` section of
`docs/network_upgrade_10g/results_2026-07-22_switch_swap.md`), the flash-recovery HV map
(`docs/flash_recovery_run57_HV_map_2026-07-20.md`), run_61 tracking efficiency (memory
`run61-tracking-efficiency`), and the Geant IPC arrival spectrum
(`MX17_Full_Geant/analysis/reweight/ipc_ingate_spectrum.npz`, 4.1e8 effective counts).

---

## 1. First, the DreamMask question — the answer is "no", for a structural reason

Dead strips top and bottom affecting 2 cables on every other FEU: correct that this is not worth
doing, but the reason is stronger than the count.

**The 8 Dreams on an FEU read out in parallel** — each ASIC multiplexes onto its own output into
its own ADC. Within a Dream the 64 channels are multiplexed **serially** at RCk. So

```
per-event readout time = N_columns x (64 channels / RCk + overhead)      <- no N_dreams term
```

This is confirmed numerically by our own measurements: 64 ch / 16.7 MHz = 3.83 µs vs the measured
4.31 µs/column at RdClk 6.0, and 64 / 25 MHz = 2.56 µs vs the measured 2.88 µs at RdClk 4.0 —
same ~0.35 µs/column overhead both times.

⇒ **`Main_Conf_DreamMask` cannot shorten readout at all.** It is a data-volume knob, not a time
knob. Masking whole Dreams changes nothing about deadtime even if half of them were dead.

The only channel-count lever that *would* touch the time is the **per-channel SCA read-enable,
DREAM slow-control registers 10 and 11** — and it is unusable: `FeuConfigParams.c:96-105` forces
them to `0xFFFF/0xFFFF` with the comment *"Never touch Dream registers 10 and 11 / All 64 Dream
channels must be enabled for readout"*, and the FEU readout logic clocks a fixed 64-channel frame
regardless. Even if it worked, the dead strips are at detector edges → different channel indices on
different Dreams, and the slowest Dream/FEU gates event completion, so you would have to mask the
*same* indices everywhere to gain anything. **Closed.**

---

## 2. The reframing: sustained rate is solved; only the front edge matters

Established and not worth revisiting:

- On 10 GbE the comb is **entirely gone**: live coverage 100 %, and in the 4–10 ms band we record
  **12.0** events/spill against **12.4** triggers offered — we capture essentially every trigger
  (`hwm_10g`, 07-22). Readout capacity there was 19.1 events; we used 12.0.
- The `OvrWrnHwm` ladder 11→2 was a **hard null** on the band with the register verified per
  sub-run. Not a lever *in that regime* (see §5 for why that qualifier matters).

So more sustained rate buys nothing. What still costs us is **when the live window opens.**

### The IPC arrival spectrum is far more front-loaded than "the 5.3 ms thermal peak" suggests

From `ipc_ingate_spectrum.npz` (in-gate total = 6.578e-05/pulse = **1.269 IPC/day**):

| cumulative | fraction of in-gate IPC |
|---|---|
| before 2.0 ms | 15.5 % |
| before 3.0 ms | 24.9 % |
| **before 4.46 ms** | **39.8 %** |
| before 5.3 ms | 51.5 % |
| before 10 ms | 90.8 % |
| before 13 ms | 96.4 % |
| after 20 ms | **0.64 %** |

Two things follow immediately:

1. **~40 % of all in-gate IPC arrives before the DAQ is currently live.** The density is maximal
   right at the 1 ms gate and falls steeply; the "5.3 ms thermal peak" is a peak in a *decaying*
   distribution, not the centre of mass.
2. **The 20–80 ms tail is worth 0.6 %.** The instruction to "take whatever is free out to 80 ms but
   don't optimise for it" is exactly right — and it means *any* configuration choice that trades
   late-window performance for early-window performance is almost free.

---

## 3. What sets the front edge — and the one lever that does NOT

At the 1 ms gate the SCA buffer is **rested** (flashes are ~1.2 s apart, cells fully released), so
the DAQ instantly accepts its full buffer depth — measured **~11 events** — in ~60 µs, then spends
`N_buf x cycle` reading them out with the TCM vetoing everything. Those 11 land while the
detectors are still flash-blind, so they are junk; the front edge is where that drain ends.

```
front edge  =  gate_start + N_buf x cycle_time
```

Measured cycle times, and what each implies (IPC kept = fraction arriving after the front edge):

| config | cycle | dump | DAQ live from | IPC kept |
|---|---|---|---|---|
| RAW IPD 5, RdClk 6.0 — *the 07-22 as-measured point* | 314 µs | 3.45 ms | **4.45 ms** | **60.3 %** |
| RAW IPD 5, RdClk 4.0 — **today's new default** | 268 µs | 2.95 ms | 3.95 ms | **66.3 %** |
| RAW IPD 2, RdClk 4.0 | 163 µs | 1.79 ms | 2.79 ms | 76.8 % |
| **ZS IPD 2, RdClk 4.0** — *cycle measured 92 µs* | 92 µs | 1.01 ms | **2.01 ms** | **84.3 %** |

(Only the SCA term scales with the read clock: it is 32 x 4.31 = 138 µs at RdClk 6.0 and
32 x 2.88 = 92 µs at RdClk 4.0, both measured. The RAW rows carry a further ~176 µs of
packets x IPD + transfer, obtained by subtracting the SCA term from the measured 314 µs.)

**So today's read-clock change is not "headroom only" after all.** In the metric that matters it is
worth **60.3 % → 66.3 %**, a **+10 % relative gain in IPC yield**, delivered the moment it was made
default. That should be stated in the clock-scan write-up, which currently says it buys no yield —
true for spill-averaged rate, wrong for the IPC window.

### The lever that does NOT work: NbOfSamples

This corrects the standing advice in `docs/DREAM_flash_comb_study_2026-07-19.md` ("n16 ≈ doubles the
yield"). The buffer depth and the cycle time scale *oppositely* in n:

```
N_buf = (512 - latency)/n        cycle_SCA = n x (64/RCk + overhead)
dump  = N_buf x cycle            =>  n cancels
```

| n | N_buf | cycle (ZS, RdClk 4.0) | dump |
|---|---|---|---|
| 32 | ~11 | 92 µs | **1.02 ms** |
| 16 | ~22 | 46 µs | **1.02 ms** |
| 8 | ~44 | 23 µs | **1.02 ms** |

**Shrinking the window does not move the front edge by one microsecond.** It only raises the
sustained-rate ceiling, which we do not need. The old n16 advice was written when the sustained
ceiling *was* the binding constraint (1 GbE, IPD 90); on 10 GbE it is obsolete. **Keep n32 and the
full 1.92 µs drift window** — the trade the comb study warned about does not have to be made.

⇒ The complete list of front-edge levers is: **read clock (banked), IPD, ZS (which is what lets IPD
go low), and the buffer depth itself (§5).** Nothing else in the DREAM config touches it.

---

## 4. The HV side — the IPC-weighted optimum is 525 V, and it is *not* the late-window optimum

run_61 measured P(3D pair) vs resist HV in three time-since-flash groups. Weighting those groups by
the actual IPC arrival spectrum (early 1–8 ms carries **81.1 %**, mid 8–20 ms **18.2 %**, late
20–95 ms **0.64 %**):

| resist V | 4 ms | 13 ms | 27+ ms | **IPC-weighted** | vs 530 V |
|---|---|---|---|---|---|
| 515 | 29 | 15 | 14 | 26.4 | 0.80 |
| 520 | 34 | 17 | 13 | 30.8 | 0.94 |
| **525** | **43** | 20 | 19 | **38.7** | **1.18** |
| 530 *(current)* | 36 | 19 | 21 | 32.8 | 1.00 |
| 535 | 32 | 22 | 23 | 30.1 | 0.92 |
| 540 | 24 | 32 | 30 | 25.5 | 0.78 |
| 550 | 12 | 26 | 38 | 14.7 | 0.45 |
| 560 | 2 | 18 | 53 | 5.2 | 0.16 |

**The "no single HV optimum, it moves with time" result resolves once you weight by IPC:** the late
window that prefers 545–560 V is worth 0.6 % of the signal, so it should have essentially no vote.
The IPC-weighted optimum is **525 V, +18 % over the 530 V we run**, and the curve is sharply peaked
— 540 V is already a 22 % loss and 550 V loses half.

This is a *free* +18 %: it is a setpoint change, not a hardware change.

### The two effects reinforce each other

Lower HV recovers faster (`run57` map, det C is the slowest of A/B/C): C recovers at 3.7 ms at
530 V but **2.4 ms at 525 V** and 0.9 ms at 520 V. So dropping to 525 V both (a) raises early-time
track probability and (b) pulls the *detector* front edge in behind the DAQ's.

Effective live edge = max(DAQ edge, detector edge):

| config | DAQ edge | det C edge | effective | IPC kept |
|---|---|---|---|---|
| RAW IPD5 RdClk6.0 @ 530 V *(the 07-22 point)* | 4.45 | 3.7 | **4.45** | 60.3 % |
| RAW IPD5 RdClk4.0 @ 530 V *(today)* | 3.95 | 3.7 | 3.95 | 66.3 % |
| ZS IPD2 RdClk4.0 @ 530 V | 2.01 | 3.7 | 3.70 | 68.9 % |
| **ZS IPD2 RdClk4.0 @ 525 V** | 2.01 | 2.4 | **2.40** | **80.3 %** |
| ZS IPD2 RdClk4.0 @ 520 V | 2.01 | 0.9 | 2.01 | 84.3 % |

Note the structure: **at 530 V the DAQ is the binding constraint today; after the ZS/IPD move the
detector becomes binding; at 525 V they are balanced at ~2.0–2.4 ms.** That balance point is the
right operating target, and 525 V is *also* the peak of the efficiency table — the two arguments
agree, which is the strongest thing in this document.

Combining the two factors gives an indicative **~1.5–1.6x on IPC track yield** vs today
(0.803/0.603 x 38.7/32.8). Treat that number as indicative only — it double-counts slightly,
because run_61's "early" bin already averages over times when the detector was dead. §6 says how to
do it properly with data we already own.

---

## 5. Two things worth testing that the existing measurements could not see

### (a) Deliberately *shrink* the buffer to open the front edge — `OvrWrnHwm` revisited

`dump = N_buf x cycle`. The 07-22 Hwm ladder verified that `maxFIFOocc` tracks `OvrWrnHwm` exactly
(11/8/6/4/3/2 on all 8 FEUs) and that total yield falls monotonically with it — the knob works. It
read as a null **only because at RAW/IPD 5 the dump landed in the flash-blind region either way**,
which the doc itself flags ("always state the window start alongside any band number").

Once the cycle is 92 µs, that stops being true:

| Hwm | dump (ZS, IPD 2, RdClk 4.0) | DAQ live from | IPC kept |
|---|---|---|---|
| 11 *(default)* | 1.01 ms | 2.01 ms | 84.3 % |
| 4 | 0.37 ms | 1.37 ms | ~91 % |
| 2 | 0.18 ms | 1.18 ms | ~93 % |

Cost: the 07-22 ladder measured **−12 % total ev/spill** at Hwm 2 — and that loss is concentrated in
the late window, which carries 0.6 % of the IPC. **Prediction: at ZS/IPD 2/RdClk 4.0, lowering Hwm
should move the front edge earlier for almost no IPC cost — the exact opposite of the 07-22 null.**
This is the single cleanest beam test to run, because it is bracketed, register-verifiable, and has
a sharp quantitative prediction that distinguishes it from the previous null.

### (b) Does ZS preserve tracking?

Everything above routes through ZS (it is what allows IPD 2, worth 1.9 ms of front edge). But
run_67 and the whole tracking chain ran **RAW**. ZS at k8 with `ZsChkSmp 4` has never been validated
against reconstructed tracks. **This is the one genuine blocker** and deserves a dedicated
comparison — same HV, same trigger, ZS vs RAW, compare P(3D pair). If ZS costs track efficiency,
the fallback is RAW at IPD 2 (front edge 2.79 ms, still worth +27 % over today).

---

## 6. Recommended order of work

**Beam-free, do first (all data already on disk):**

1. **Redo the run_61 HV analysis with fine time bins and IPC weighting.** The data has ~1300 late
   events/cell and a full 6 drift x 10 resist grid; the three-bin summary is throwing away the time
   resolution that this optimisation needs. Deliverable: `IPC-weighted P(track) vs (resist, drift)`,
   i.e. `∫ IPC(t) P(track|HV,t) dt`, which replaces the whole table in §4 with a proper number and
   also settles the drift axis (§4 covers resist only; run_61 showed drift reverses the same way —
   early prefers low drift, late high — so the IPC weighting will pick a **low** drift too, likely
   below the 600 V we run).
2. **Recompute the live-window/IPC overlap for the current config.** Every "we catch 1.7–2 % of
   in-gate IPC" number in the memory index is from the 1 GbE / n64 / IPD 90 era and is now badly
   wrong — the comb is gone. Rerun `ipc_spectrum_vs_runs.py` on run_67. Expect the answer to be
   ~60 %, not 2 %.
3. **Mine the recov_0722 threshold scan for trigger headroom in 4–10 ms.** Readout can carry
   ~65 events/band at ZS/IPD 2 against the ~12 the trigger offers — a 5x margin. That scan already
   holds coincidence-vs-time-since-flash across the wall x plastic grid; it can say how much more
   trigger rate is available in-band and at what purity, with no new beam time.

**Needs beam, in priority order:**

4. ZS-vs-RAW tracking validation (§5b) — blocks everything else.
5. Front-edge Hwm ladder at ZS/IPD 2/RdClk 4.0 (§5a), with the front edge as the metric, not the band.
6. Fine resist scan 520–535 V in 2–3 V steps at the new config, IPC-weighted — confirm the 525 V
   optimum in situ.

---

## 7. Two cautions

- **Do not move the 1 ms gate later.** It is tempting ("detectors are blind till 5 ms, why take
  triggers at 1 ms?") and it is wrong: the 1 ms gate's real function is to make the rested-buffer
  dump happen *during* the blind period. Starting the gate at 3.7 ms would spend those ~11 buffer
  slots on good events and then block the TCM for the following 1–3.5 ms of live detector — moving
  the dead time from where it costs nothing to where it costs everything. Keep the gate at 1 ms.
- **Det D is not in this game at all.** At 530 V (D runs 10 V below → 520 V) its recovery is
  ~18.6 ms, so it contributes nothing anywhere in the 1–13 ms window that holds 96 % of the IPC — at
  *any* HV in the usable range. Either D's slow recovery is real, in which case the tracking should
  be re-planned around A/B/C for the IPC window, or it is the known noise/common-mode contamination
  inflating the measurement (the run_57 doc flags D's numbers as an upper bound). **Resolving which
  is worth more than any DAQ knob in this document** and is answerable from existing run_61 data.

## 8. Caveats on the numbers above

- run_57 recovery is a **pedestal-noise-return proxy**; the doc itself warns that track-based
  recovery will be equal or *longer*. The detector-edge column in §4 is therefore optimistic.
- run_61 efficiencies are **Det A, drift-pooled**, taken at n64/lat33/IPD 90/RAW. P(track|HV,t) is a
  detector property and should transfer, but the absolute scale should not be quoted across configs.
- `N_buf = 11` is measured at n32 (07-20 rest-toggle test, max 12; 07-22 `maxFIFOocc` = 11). The
  RAW-mode cycle decomposition (176 µs non-SCA remainder) is inferred from two measured points, not
  measured directly — worth one bracketed confirmation.
- The IPC normalisation carries ~25 % ENDF systematic plus the α_ipc and BR_X17 assumptions. All
  numbers here are **ratios** within one spectrum, so that scale cancels.
