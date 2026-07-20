# DREAM post-flash "comb" — mechanism, data loss, and levers (2026-07-19)

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
1. **Fewer samples** — the only DAQ-side lever, trades drift-window depth (n8 recovers 3× but
   0.48 µs window). n16 ≈ doubles the yield for half the drift. Linear, so tune it finely.
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
