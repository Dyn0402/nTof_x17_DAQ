# DREAM post-flash "comb" — mechanism, data loss, and levers (2026-07-19)

> ## ⚠ CORRECTION 2026-07-22 — three rows of the lever table are INVALID
> See **`docs/FEU_WATERMARKS_2026-07-22.md`** for the full account. In short:
> - **`TrigVetoLen 0/250/500/1000`** and **`SparseRd 0/1/3`** — the knobs **never reached the
>   cfg**. `dream_daq_control.py` is a long-lived server and was running code predating that
>   plumbing, so every sub-run used the template default (archived cfgs show `TrigVetoLen 0`
>   and `SparseRd 0` at *every* point). Both nulls are artifacts; neither knob was tested.
> - **`water marks OvrWrnHwm 20→48`** — invalid twice over. Same server problem, *and* RunCtrl
>   clamps the cfg watermark down to a cap it derives itself (11 at lat35/n32), so 20 and 48
>   would both have been forced to the same value anyway.
> - The line *"FIFO never reaches the HWM (occ 11)"* is **backwards**: `maxFIFOocc = 11` **is**
>   the watermark. Measured 2026-07-22, `maxFIFOocc == Hwm` exactly at Hwm = 11, 3 and 1.
>   Lowering the HWM works and is free down to Hwm 3.
> - **`UdpChan_MultiPackThr` has not been re-checked** — treat that null as unverified.
>
> **Everything else in this document stands**, including the `NbOfSamples` scaling (it travels
> via `Sys NbOfSamples`, which really did vary), the TCM mechanism, and the 0-FEU-drops result.

## TL;DR
Anchoring every DREAM event to the preceding γ-flash, the events **comb**: a big cluster at
t≈0, then teeth at ~10 ms intervals with dead gaps between. It is real **data loss** — at the
production `n_samples = 32` we live-record only **~34 %** of the in-spill neutron-capture signal.

The deadtime is **not** in the network and **not** in the FEU's trigger FIFO. Direct FEU counter
reads show the **FEU drops nothing** (0 close-drops, 0 fifo-drops, max FIFO occupancy = 11). The
loss is enforced **upstream by the FEU-BUSY → TCM veto**: while a FEU reads out it raises BUSY on
the RJ45 TI link and **the TCM stops sending it triggers**. The FEU runs on a **~fixed dead cycle
(~9.6 ms at n32), input-independent** — proven by driving a 4 kHz pulser (100× the beam rate) and
getting the *identical* 18-events/window comb.

**This is now confirmed verbatim by the TCM manual** (see "TCM mechanism" below) — no longer a
deduction. The TCM discards *every* trigger while any FEU is BUSY, and has **no** dead-time /
hold-off / prescale / rate-limit register, so the veto length is exactly the FEU readout duration.

**Only `NbOfSamples` shortens the cycle** (fewer SCA columns → shorter readout → shorter BUSY).
ZS, InterPacketDelay, MultiPackThr/Enb, SparseRd, RdClk_Div, trigger latency, TrigVetoLen, and a
10 GbE upgrade **all do nothing** to the comb.

## History of the interpretation (so the record is honest)
1. First guess: FEU trigger-FIFO fills and *the FEU* refuses triggers ("drain-limited"). **Wrong** —
   the FEU-side latch-read shows 0 drops, occ 11.
2. Second guess: the tail gaps are *idle* (FEU waiting for a low, decaying physics rate). **Wrong** —
   a 4 kHz pulser did not fill them; the gaps are forced regardless of input rate.
3. Correct: a **fixed BUSY→TCM-veto dead cycle**, ~9.6 ms at n32, input-independent (below).

## The observation
Trigger = scint-DOUBLES OR PS-pickup (the flash marks each beam pulse). Per-flash tooth sizes
(clean doubles, n32): **tooth-1 ≈ 10.6 events/flash** (the flash burst), every later tooth only
**~3/flash**. Teeth spaced ~10 ms; spacing scales with n_samples (n32 ≈ 10 ms, n8 ≈ 3 ms) but is
**independent of input rate** (see the 4 kHz test). The true capture rate is smooth and
front-loaded — the teeth are the deadtime artifact, not physics.

## The decisive evidence — FEU counter latch-read
Read live during a run (counters reset at run-end). Latch = `poket 0x100000 0x10 0x10` (Main
command reg 0x100000, `Latch` = bit 4), then peek `TrigAcptCntr 0x100018` and `TrigDropCntr
0x10001C` (close-drop bits 0-7 / fifo-drop bits 8-15 / **max FIFO occupancy** bits 16-21).
Register defs: `~/Feu/Firmware5/.../CBus/CBus_Common.h`. Peek/poke = plain ASCII UDP on port
1300+FEU-ID (`peek 0x<addr>` / `poket <adr> <val> <mask>`), same as RunCtrl.

All 8 FEUs, every condition tried (doubles ~6 Hz, singles, **4 kHz pulser**):
```
closeDrop = 0      fifoDrop = 0      maxFIFOocc = 11      (accepted counter climbs normally)
```
- **0 drops** → the FEU accepts everything it is *sent*; nothing is refused at the FEU.
- **max occupancy = 11** → the FIFO only ever reaches the flash-burst size. Nothing (not even a
  4 kHz feed) pushes it higher, because the TCM stops sending while the FEU is BUSY.
- So the flash's 11 get in only because they arrive in **57 µs** — faster than the BUSY→veto loop
  can close the gate. Everything after is TCM-paced.

## The 4 kHz pulser test — gaps are FORCED, not idle
Drove the M6 pulser at **4 kHz** (period 250 µs, gated into the flash windows) — over 100× the
beam rate and far above the ~1150 Hz nominal readout ceiling:

| | events/window | teeth | max occ | drops |
|---|---|---|---|---|
| doubles (~6 Hz) | 18 | 0/9/18 ms | 11 | 0 |
| **4 kHz pulser** | **18** | **0/9/18 ms** | **11** | **0** |

Identical. If the tail gaps were idle, a continuous 4 kHz feed would fill them to full teeth. It
changed nothing → **the dead cycle is imposed by the FEU/TCM independent of input rate.** The FEU
even records *below* its own nominal readout ceiling (18/window, not ~34), so it is neither
FIFO-limited nor input-limited — it is limited by the fixed BUSY→veto cycle.

## Levers — every knob tried, with verdict
Metric = the post-flash busy-gap and/or events-per-spill (yield), FEU01, doubles+PS, k8, n32
unless noted. Yield is the honest metric; the raw "busy-gap" is confounded by tooth size.

| knob | result | verdict |
|---|---|---|
| ZS on↔off, IPD 2–100, MultiPackThr/Enb, **SparseRd 0/1/3**, RdClk_Div 6/5.5/5.0, **10 GbE** | no change | all downstream of / orthogonal to the readout cycle |
| **trigger latency 3 / 34 / 60** | yield flat 17.6–19.2 | latency shifts *which* SCA columns are read (signal peak sample 0→7→31, proof it applied) — no deadtime |
| **TrigVetoLen 0/250/500/1000** (0–10 µs) | flash burst flat ~10.7/flash | FEU per-trigger holdoff is **inert under external (TCM) triggering** (manual: self-trigger only) |
| water marks OvrWrnHwm 20→48 | null | FIFO never reaches the HWM (occ 11) |
| **NbOfSamples 32→16→8** | **9.6→6.2→2.9 ms cycle; yield 18→36→53/spill** | the **only** lever — shorter SCA readout ⇒ shorter BUSY ⇒ shorter veto ⇒ more captured |
| **fine NbOfSamples 32/31/30** | 9.61/9.30/8.95 ms | **linear ~0.32 ms/sample**, no divisor-of-512 quantization — trim by 1–2 for a proportional gain |
| **ZS vs Raw** | 18 vs 17/spill, 0 host RcvbufErrors | ZS gives **no throughput benefit** (readout/veto-limited); only 10× data volume. Raw runs fine on 1 GbE at this rate |
| **singles vs doubles** | identical yield (n32 18, n8 53) | confirms rate-independence — singles delivers far more triggers, records the same (veto ceiling) |

### Notes on individual results
- **RdClk_Div** didn't take: live peek shows RdClk (0xD00020) = WrClk (0xD00028) = 0x1618 in all
  cases; RunCtrl clamps the read clock to the write divisor. No ~20 % uplift accessible this way.
- **10 GbE**: cannot help (readout/veto-limited, not network). Not even needed to read Raw — Raw
  at IPD 10 (27–41 MB/FEU-file) caused **0** host UDP RcvbufErrors at the physics rate.
- **n28 "anomaly"**: the raw busy-gap = (events per tooth)×(readout/event), so it rises when a knob
  *recovers* events. Bracketed re-run (n32 stable 18.1/18.1/17.9, n28 21.1/21.2 = **+18 %**)
  confirmed via the clean **yield** metric that fewer samples is strictly better.

## Data loss — CONFIRMED
Events captured per spill vs the readout cycle (robust spill count via >0.4 s gaps):

| n_samples | dead cycle | events/spill | live fraction |
|---|---|---|---|
| **32 (production)** | 9.6 ms | **18** | ~34 % |
| 16 | 6.2 ms | 36 | ~68 % |
| 8 | 2.9 ms | **53** | ~100 % (of the front-loaded burst) |

The dropped signal is the **front-loaded neutron captures vetoed at the TCM while the FEU reads
the 11-event flash burst** (~9.6 ms). Shorter samples ⇒ shorter flash readout ⇒ shorter veto ⇒
fewer lost.

## Options

> ### ⚠ CORRECTION 2026-07-23 — option 1 (`n16 ≈ doubles the yield`) is RETIRED. Keep n32.
> Two independent results kill it, and both post-date this document:
> - **On 10 GbE the sustained ceiling is no longer binding at all.** This advice was written when
>   the 1 GbE readout ceiling was the constraint; the switch upgrade moved the corruption threshold
>   from IPD 75 to below 1 and the DAQ now records essentially every trigger the beam offers
>   (`docs/network_upgrade_10g/results_2026-07-22_switch_swap.md`). At beam we are **trigger-limited**,
>   so buying readout rate by throwing away drift depth buys nothing.
> - **NbOfSamples is INVARIANT for the quantity that actually matters** (when the live window opens):
>   `N_buf = (512-lat)/n` while `cycle_SCA ∝ n`, so the buffer-dump term cancels — n32/n16/n8 all give
>   the same ~1.02 ms front edge (`docs/IPC_YIELD_OPTIMIZATION_2026-07-23.md`). And run_67
>   flash-anchored data shows coverage is already ~100 % from 1 ms, so **no DAQ knob buys IPC yield.**
>
> **Keep n32 and the full 1.92 µs drift window.** The readout-rate lever that IS free is the read
> clock (RdClk 6.0→4.0 = 1.5×, now the default) — it costs no window at all. See
> `docs/CLOCK_RATE_SCAN_2026-07-23.md`.

1. ~~**Fewer samples**~~ — *superseded, see the correction above.* (Original text: the only DAQ-side
   lever, trades drift-window depth (n8 recovers 3× but 0.48 µs window). n16 ≈ doubles the yield for
   half the drift. Linear, so tune it finely.)
2. **Tame the flash burst upstream** — the flash's 11 triggers cause the long veto. The FEU's own
   TrigVetoLen can't reject them (inert under external trig). A **scint-side discriminator holdoff
   (N1081B) or a TCM-side veto/deadtime** that collapses the flash to ~1 trigger would cut the
   post-flash dead from ~9.6 ms toward ~0.9 ms and recover most of the front-loaded signal at full
   n_samples. This is the real headroom and it lives in the trigger electronics, not the FEU.
3. **Accept + characterize** the ~34 % live fraction and correct offline.

## TCM mechanism — RESOLVED (TCM User Manual V2.12, D. Calvet, `~/Documents/dream/tcm_user_manual_V2_12.pdf`)
The TCM runs a **single-event stop-readout-restart handshake**: one accepted trigger → `SCA_STOP`
("the SCA_STOP bit is the trigger", p.37) → all FEUs read out & assert BUSY → **the TCM waits for
*every* active FEU to clear BUSY** (Reg #19 FEM_BUSY, p.13: "When all Feminos are no longer busy, a
SCA_START is sent automatically") → auto `SCA_START` → next trigger accepted. The veto is the
**logical OR of all FEU BUSY flags**. The decisive sentence (Reg #27 EVENT_TX_CNT, p.18):
**"When one or several Feminos are still busy… all the valid triggers received by the TCM will be
lost."** Corollary (§6.5 p.26): "If no trigger source fires when the TCM has its BUSY output
asserted, no event should be dropped."

- Confirms **0 FEU drops** — the *TCM* drops the vetoed triggers; they never reach the FEU.
- Confirms **fixed, input-independent cycle** — there is **NO** programmable dead-time, hold-off,
  minimum-spacing, or prescale register anywhere in the TCM (checked full Config Reg #22 bit list,
  p.15–16). Veto length = FEU readout duration, period.
- **`trig_rate <range> <rate>`** (Reg #22, p.16) sets the **internal trigger generator** frequency
  only — **not** a throttle, does **not** gate external NIM/LVDS/TTL triggers. Dead end as a lever.
- **Still not in any manual:** the exact per-cycle *count* (11 at flash, ~3 in tail) — that is
  FEU/DREAM multi-event SCA buffering; the TCM manual models one accepted trigger per handshake and
  is silent on FEU-side multi-buffering. It affects the tooth *size*, not the loss mechanism.

### MEASURED on the live TCM (read-only probe, 2026-07-19, DAQ idle after the pulser run)
Probed 192.168.10.32:16000 (ASCII UDP, reply payload @byte 8), read-only queries only:
- **`event rx` = 12,774 vs `event tx` = 1,996 → 10,778 (84.4%) dropped at the TCM.** Only 15.6% of
  triggers were forwarded to the FEUs. This is the *TCM's own counter* proof of the drop, and its
  `event tx` = what the FEU accepted (FEU latch-read: 0 FEU drops) — the loss happens entirely at
  the TCM, upstream of the FEU. **Capstone confirmation, both ends agree.**
- **`state` = `WAITING_TRIG` (0x2a000), no error flags** → the comb is genuine per-event BUSY veto,
  NOT a latched START_ACK_MISS/TRIG_ACK_MISS/NO_BUSY_MISS stuck-TCM artifact.
- **`feminos detected/enabled/sampling` = 8 FEUs (0x1fe, slots 1-8), `busy` = 0** (idle).
- **`hbusy`**: resolution = 1 µs/bin (`busy_resol` 0); occupied bins in the multi-ms region
  (consistent with the flash-burst readout as the dominant BUSY duration). Exact peak not decoded —
  the mclient histogram encoding isn't documented/in-source — but not needed given rx/tx.
- Probe script: `scratchpad/tcm_probe.py` (curated read-only command list).

### Decisive TCM-side diagnostics (read-only, proves the drop from the TCM's own counters)
- **`event rx` vs `event tx`** (Reg #26 vs #27) — difference = triggers the TCM dropped while a FEU
  was BUSY; during the flash expect rx ≫ tx. The direct on-TCM analogue of the FEU latch-read.
- **`hbusy get`** (dead-time histogram; `busy_resol <0-3>` sets range up to 1.022 s) — reads the
  ~9.6 ms BUSY duration directly.
- **`state`** (Reg #20) — rules out a *latched* error (START_ACK_MISS / TRIG_ACK_MISS /
  NO_BUSY_MISS → "TCM stops accepting triggers until a RESTART", p.14–15) masquerading as the comb.
- Coordination: the TCM (192.168.10.32, ASCII UDP) is owned by RunCtrl during a run — probe between
  runs or with a careful read-only query so RunCtrl's control channel does not desync.

## Tooling / plumbing (all opt-in, null by default)
`run_config` + `dream_daq_control` expose per-run/per-sub-run: `inter_packet_delay`,
`multipack_thr`, `multipack_enb`, `sparse_rd`, `rdclk_div`, `trig_veto_len` (each → the matching
`Feu * …` cfg register). Study configs: `run_config_zs_{comb_study,sparserd,readout,control,
latency,vetolen,singles,latch}.py`. Analysis = decode FEU FDF → `nt` tree → timestamp×10 ns →
per-spill busy-gap / yield / tooth structure. FEU counter latch-read script pattern in this doc.
Mechanism memory: `dream-flash-comb-mechanism`. Diagram artifact:
`claude.ai/code/artifact/d6555d79` (needs a revision to show the fixed BUSY→TCM-veto cycle).

---

# TODAY'S PLAN (2026-07-20) — crack the 11/3 tooth structure, exhaust the TCM/queue/network levers, then optimize

## The objective, stated as a DAQ requirement
Maximize the number of events that **can contain a real track**. A trackable event must
(a) arrive **late enough that the DREAM front-end is no longer saturated** by the flash
(≳3 ms post-flash), and (b) ideally be **~uniformly distributed across the ~30 ms tail**
after that. The current comb delivers the opposite: ~11 events piled at t≈0 (saturated,
**useless for tracks**) followed by sparse ~3-event teeth with multi-ms dead gaps. So the
real target is: **stop spending the readout budget on the flash burst, and spread the
surviving budget evenly over the tail.** Every lever below is judged by that yardstick,
*not* by raw event count (18 combed events ≠ 18 trackable events; most of tooth-1 is dead
flash).

This plan is deliberately **investigate-then-optimize**: we first spend the day proving
(or killing) the three things that could still be levers — a TCM trigger queue, a raw-mode
limit, and a 10 GbE benefit — and thoroughly explaining the odd 11/3 structure. If those
come back negative (as the manuals predict), we accept the BUSY-veto floor and optimize the
trigger timing and `NbOfSamples` under it.

## Safety / coordination (unchanged, read first)
- **N1081B hygiene** (`n1081b/CLAUDE.md`): every board touch via `board_session()`, one
  process per board, **never SIGKILL** a board session, respect quarantine. Check
  `config/n1081b_access/` before launching any board-touching step.
- **TCM (192.168.10.32, ASCII UDP)** is owned by RunCtrl *during a run*. All TCM probes are
  **read-only and between runs** (or a single careful read-only query) so RunCtrl's control
  channel does not desync. Curated read-only command list: `scratchpad/tcm_probe.py`.
- **FEU peek/poke** = plain ASCII UDP on port 1300+FEU-ID; peeks are cheap, pokes only
  between runs. Register defs `~/Feu/Firmware5/.../CBus/CBus_Common.h` and the FEU manual.

## Investigation A — the 11/3 tooth structure = DREAM multi-event SCA buffer (the real unknown)
**Working hypothesis.** The teeth are **DREAM analog-memory (SCA) multi-event buffering**,
which the TCM manual does *not* model (it assumes one accepted trigger per SCA_STOP/START
handshake). The DREAM ASIC has a **512-cell circular SCA**; each event consumes
`NbOfSamples` cells, so it can buffer `N_buf ≈ (512 − latency) / NbOfSamples` events
(= **~15 at n32/lat35** per the deadtime-study derandomiser formula, `DEADTIME_REPORT.md:33`)
*before* it must stop and read the whole block out through the ADC (~0.87 ms/event). The
flash dumps **11** triggers in 57 µs — fast enough to (nearly) fill the buffer before BUSY
closes the TCM gate — then the FEU reads out all 11 (~9.6 ms), vetoing everything.

**The genuine anomaly to explain (do not hand-wave it):** under a *continuous* 4 kHz feed
the tail teeth are only **~3 events** and spaced **~9–10 ms**, not the ~11-events-every-~12-ms
we'd expect if the buffer simply refilled and re-read at the same 11-deep block. Either the
buffer does **not** refill in the tail, or tail readout is effectively single-event-paced,
or the "tooth" is a histogram-binning artifact of the readout. **This is the crux the
operator flagged as odd, and it decides whether even-distribution is achievable.**

**Read first (the one doc not yet read):** `~/Documents/dream/DREAM_User Manual_prod_v3.pdf`
— DREAM ASIC SCA depth, circular-buffer / multi-event / derandomiser behaviour, and the
read sequence. This is where the 11/3 mechanism actually lives (the FEU + TCM manuals are
silent on multi-event SCA buffering). Confirm SCA = 512 cells and the buffer-depth formula.

**FINDING (2026-07-20, DREAM ASIC manual read).** Confirmed and *sharpened*:
- **512-cell circular SCA per channel**, explicitly a **multi-event / derandomising L1
  buffer** (p.5, p.6 Table 1, p.13 Fig.8). Each trigger freezes `Nc = NbOfSamples` cells
  (Event-Number-tagged), the write pointer **skips frozen columns**, readout is FIFO
  oldest-first (p.14). Buffer depth ≈ **`(512 − TRIGLAT)/Nc`** ⇒ ~15 events at n32/lat35;
  the ≤**512-WCk delayed cell release** after each read (p.15) tightens the *usable* depth
  during a fast burst toward the observed **~11**. This is the origin of the tooth size.
- **Crucial subtlety:** the ASIC is **designed dead-time-free** — "the sampling and
  triggering operations are **not interrupted during the readout**" (p.14); "'deadtime free'
  operation for trigger rates up to more than 20 kHz" (p.5). Readout drives an external
  12-bit ADC at ≤20 MHz (p.16, p.43) ⇒ ~48–50 ns/sample, ~3.4 µs/event for all 64 ch.
  **So the ~9.6 ms comb dead time is NOT an ASIC limitation** — it is the **FEU firmware
  choosing a single-block stop-readout-BUSY handshake** (TCM `SCA_STOP`→BUSY→`SCA_START`)
  layered on top of a chip that could in principle keep sampling. That reframes the real
  lever: the headroom is in **how the FEU/TCM schedules readout & BUSY around the flash
  burst**, not in the chip. Full digest: this repo's analysis notes; the two governing
  numbers are **TRIGLAT (reg 12)** and **Nc = NbOfSamples**.

**Offline companion study (started 2026-07-20):** a systematic *event-time-since-flash*
analysis across recorded runs (singles/doubles/pulser, beam on/off, cfg scans) is running
in `~/beam_july/analysis/flash_comb/` (`flash_comb_analysis.py` + `PLAN.md`). It anchors
every event to its preceding flash (flash tag = distinct-channel count ≥ 300) and measures
the dead-cycle (autocorrelation), events-per-tooth, and flash-to-flash consistency. First
result reproduces the reference **9.5 ms** comb on beam k5/IPD100; the controlled
`zs_comb_study` (NbOfSamples) + `zs_latency` scans decoded today will confirm the scaling.

**Measurements (read-only diagnostics + pulser; no wedging):**
1. **TCM `hbusy get`** (dead-time histogram, `busy_resol 0` = 1 µs/bin, range [0,1.022 ms];
   step to `busy_resol 1` = 10 µs for the full ~10 ms block). Answers: is a tail "tooth"
   **one ~9.6 ms block** or **N × single-event ~0.87 ms** reads? Decodes the per-event
   readout time directly (manual §6.7 / §8.3). Pair with **`hevper get`** (inter-event-time
   histogram) to see the true trigger-arrival spacing vs the readout spacing.
2. **`event rx` / `event tx`** across the flash cycle → drops localise to the readout block
   (already 84.4 % aggregate; now resolve it in time).
3. **FEU `TrigFifoMaxOcc` + `AcqFSM=ReadDream`** timing (0x10001C / 0x10000C) — is the FEU
   trigger FIFO (64-deep, separate from the SCA) ever the limiter? (Expected no: occ 11.)
4. **Latency scan 3 / 34 / 60** at fixed n32: predicted `N_buf` = 15.9 / 14.9 / 14.1 —
   nearly flat. **If tooth-1 tracks latency strongly, it is NOT buffer-depth-limited**
   (points to the BUSY-close race instead). Study cfg `run_config_zs_latency.py`.
5. **NbOfSamples scan 8 / 16 / 32** → tooth size (`N_buf ∝ 1/N`) and spacing (readout ∝ N).
   The one lever known to move the comb; quantify both axes cleanly. `run_config_zs_comb_study.py`.
6. **Pulser burst-shape test (decisive for the buffer-fill race).** Drive M6.D
   (`n1081b/set_pulser.py`) as a **single tight burst of K pulses in ≲57 µs then silence**,
   K = 1, 2, 5, 11, 20, 30. Map *captured-per-cycle* vs K → directly reads out the buffer
   depth and the BUSY-close race (does it saturate at ~11? ~15?). Then a **paced** pulser at
   ~1 ms spacing (≥ single-event readout): if that yields **single-event readout, evenly
   spread, no teeth**, we have proven the optimization path (see §E). New burst/pace modes
   may need a small `set_pulser.py` addition — build it, don't hack the board.
7. **Flash-off continuous pulser** at 100 Hz–2 kHz: do teeth appear at all with **no flash
   seed**? If the tail teeth vanish without a flash burst, the comb is flash-seeded, not an
   intrinsic steady-state cycle — a strong result for the veto-the-flash strategy.

## Investigation B — is there a TCM trigger queue? + every remaining TCM knob
**Prior from the manual (to confirm, not assume):** the TCM has **no trigger queue/FIFO and
no prescale/hold-off/rate-limit** for *external* triggers — it drops every trigger while any
FEU asserts BUSY (Reg #19 FEM_BUSY p13, Reg #27 EVENT_TX_CNT p18, §8.3 p33). The only
"queue" in the system is the **DREAM SCA multi-event buffer on the FEU** (Investigation A).
`TRIG_RATE`/`EVENT_CNT_LIMIT` act **only on the TCM internal generator**, not external NIM/
LVDS/TTL. Confirm empirically: `event rx − tx` = the drops **and** `state = WAITING_TRIG`
with **no** latched `START_ACK_MISS`/`TRIG_ACK_MISS`/`NO_BUSY_MISS` (Reg #20) → genuine
per-event veto, not a stuck-TCM artifact (already seen once 07-19; re-confirm during flash).

**Knobs actually worth trying (read-only survey → careful between-run pokes):**
- **`MULT_TRIG_ENA` / `MULT_TRIG_DST`** (Config Reg #22) — *multi-trigger* mode, **not yet
  explored**. Read the manual detail; it may change how bursts are accepted/routed. Only
  real unknown left in Reg #22. Test only if the manual says it affects external-trigger
  handling.
- **`DO_END_OF_BUSY` + `TTL_OUT<0>`** (Reg #22 / `do_end_of_busy` cmd, p16/p23/p33) — **the
  actual TCM BUSY/veto output**, on the `J_TTLIO` TTL header (pin `TTL_OUT<0>`), **not a
  LEMO** (the four LEMOs JX1–4 are NIM *inputs*). This resolves the old "no veto seen on the
  scope" puzzle (we were watching inputs). This output is the hook for the flash-burst
  collapse in §E — verify it toggles (level when 0; ~170 ns end-of-busy pulse when 1).
- **`max_readout_time`** (EVENT_MAX_TIME watchdog, p16/p23) — confirm it is **not** clipping
  the ~9.6 ms block (it's a fault watchdog, not a throttle; just rule it out).
- **`trig_delay <type>`** (Reg #24/25 latency, p17/p25) — per-type *delay*, not a throttle;
  confirm no interaction. **Verdict expected: no TCM lever recovers the tail** — but we prove
  it rather than assert it.

## Investigation C — prove Raw mode is not a limit for us
Our **tail** physics rate is tiny, but the **flash burst is 11 full-size events/FEU in 57 µs**
→ in Raw *every* event is full-size (~38.5 kB/FEU), so ~11 × 38.5 kB × 8 FEU ≈ **3.4 MB in
the ~9.6 ms readout ≈ 350 MB/s**, well above the 1 GbE 125 MB/s wire. The question is whether
that causes **loss** (host `Udp RcvbufErrors` / FEU `TrigDropCntr`/`FifoDropCntr` / tracer-511
truncation) *beyond* the BUSY comb, or whether FEU output buffering + IPD simply spread the
transfer over the dead time with zero loss.
- **Test:** a Raw run (`run_config_zs_readout.py` / a Raw variant), IPD swept **75 → 100 →
  150** (deadtime study: **IPD ≥ 75 required** for clean 8-FEU Raw; ≤50 corrupts), on the
  **SSD path**. Measure per-window in-window loss, host RcvbufErrors, and FEU drop counters
  during the flash era specifically.
- **Caveat to surface honestly:** IPD 75 caps sustained rate at **~392 Hz** (deadtime law).
  If the optimized tail wants ~1 kHz single-event pacing (§E), **Raw at IPD ≥ 75 could clip
  it** — this is the one place Raw might actually limit us, and the test must check the *tail*
  rate, not just "does it corrupt." If it clips, that is the sole argument for ZS (smaller
  events → lower IPD → higher tail ceiling), independent of the comb.

## Investigation D — prove 10 GbE won't help (layer separation)
The dead cycle is the **SCA readout BUSY** (internal ADC, network-independent) → a faster
wire cannot shorten it. But Raw flash-era volume (≈350 MB/s, §C) *could* cause a **host/wire**
loss layer that 10 GbE **would** fix — a different loss from the comb. Decisive method
(already the `zs_ipd_safety` layer-separation technique): during a Raw flash-era run, read
**FEU `TrigDropCntr`/`FifoDropCntr` (= 0 ⇒ FEU fine)**, **host `RcvbufErrors`**, and
**tracer-511 truncation**:
- If drops/RcvbufErrors are **~0** and the only loss is the BUSY comb → **10 GbE cannot help**
  (confirms the doc's claim; the comb is untouched by wire speed).
- If RcvbufErrors **grow** in the Raw flash era → that layer is wire/host-limited and **10 GbE
  (or higher IPD / SSD / ZS) fixes *that layer only*, never the comb.** Either way the comb
  floor stands; the report states exactly which layers 10 GbE touches.

## Optimization under the constraints (the payoff — if A–D confirm the floor)
Two levers survive the manuals; combine them:
1. **Collapse / veto the flash burst (biggest win).** The 11 useless flash triggers *cause*
   the ~9.6 ms readout that vetoes the trackable tail. If we **blank triggers during the
   flash + first ~1 ms** (N1081B side: a discriminator/G&D **hold-off** on the PS/flash and
   scint trigger lines — same M-board machinery as the +20 ns M3 delay and the `m4c-veto-gate`;
   **or** drive it from the TCM `TTL_OUT<0>` END_OF_BUSY), the SCA never fills with dead flash
   events, so there is **no 9.6 ms post-flash veto** → the trackable tail becomes immediately
   live. This directly realises requirement (a) *without* shrinking `NbOfSamples`.
2. **Pace the trigger to force single-event, evenly-spread readout (requirement (b)).** If
   Investigation A #6 confirms that triggers spaced ≥ single-event readout (~0.87 ms) read out
   one-at-a-time (no block, no teeth), then a **per-trigger hold-off ≈ readout time** on the
   scint trigger spreads captured events **uniformly at ~1 kHz** across the tail — replacing
   the comb with a flat ~30-events-in-30-ms distribution. Set/verify the exact hold-off from
   the measured `hbusy` single-event time, not a guess.
3. **`NbOfSamples` trade (fallback / multiplier).** Independently, fewer samples shorten every
   readout (n16 ≈ 2× tail yield for ½ drift depth; linear ~0.32 ms/sample). Cross-check the
   drift-window study (`DRIFT_WINDOW_ANALYSIS.md`: full-gap drift ≈ 11–15 samples) — **n16
   (0.96 µs) may be too shallow to contain the track**; keep n32 unless the drift study says a
   shorter window still contains the drift. This lever is compatible with (1)+(2) and just
   scales the ceiling.

**Expected end state:** veto the flash → tail live from ~1 ms; pace triggers → ~uniform tail;
keep n32 for drift depth (or trim to n29/n30 for a small linear gain). That is the maximum
achievable given a BUSY-veto DAQ with no TCM queue and a network that isn't the bottleneck.

## Execution order for today
1. Read the DREAM ASIC manual (SCA/multi-event) — grounds Investigation A. *(no hardware)*
2. **Read-only TCM survey between runs**: `hbusy`, `hevper`, `event rx/tx`, `state`, and the
   Reg #22 bit list incl. `MULT_TRIG_*` / `DO_END_OF_BUSY` (`scratchpad/tcm_probe.py`).
3. **Pulser burst-shape + paced tests** (Investigation A #6/#7) — the decisive buffer/pacing
   experiment; needs only M6.D + FEU peeks, no scint boards.
4. **Latency + NbOfSamples scans** (A #4/#5) to pin the buffer-depth vs readout-time axes.
5. **Raw + IPD sweep on SSD** (Investigation C) with full layer counters (Investigation D
   piggybacks on the same run).
6. Synthesise: does any TCM knob or 10 GbE recover the tail? (Expected: no.) Then prototype
   the **flash veto + trigger pacing** (Optimization 1+2) on the N1081B and measure the tail
   distribution.

## Success criteria
- The 11/3 structure is **explained** (buffer depth + fill/BUSY-close race + tail refill
  behaviour), backed by `hbusy`/`hevper` + the burst-shape scan, and cross-checked to the
  DREAM ASIC manual.
- **TCM queue: proven absent** (or, if a `MULT_TRIG` behaviour exists, characterised); every
  TCM knob has a documented verdict.
- **Raw + 10 GbE: each has a data-backed verdict** ("does not limit us" / "would only touch
  loss-layer X, never the comb"), with the FEU/host counters that prove which layer is which.
- A concrete, measured **flash-veto + trigger-pacing** recipe (N1081B timing values from the
  measured single-event readout) that turns the comb into a ~uniform ≳3–33 ms tail, plus the
  `NbOfSamples` setting reconciled with the drift-window requirement.
