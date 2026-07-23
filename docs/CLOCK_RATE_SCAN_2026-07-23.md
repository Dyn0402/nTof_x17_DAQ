# DREAM clock rate scan — `clk_rate`, 2026-07-23 (beam off, saturating pulser)

> **Start at [`DAQ_OPTIMIZATION_SUMMARY_2026-07-23.md`](DAQ_OPTIMIZATION_SUMMARY_2026-07-23.md)** —
> it consolidates all three of today's sessions and flags which intermediate claims were corrected.
>
> **Independent confirmation of the 1.5× (session C, `IPC_YIELD_OPTIMIZATION_2026-07-23.md`):** the
> 8 Dreams read in **parallel**, so per-event readout = `N_columns × (64 ch / RCk)`. That predicts
> 64/16.7 MHz = 3.83 µs vs **4.31 µs measured**, and 64/25 MHz = 2.56 µs vs **2.88 µs measured** —
> ~12 % fixed overhead, and a ratio of **4.31/2.88 = 1.50**, exactly the rate gain measured here by a
> completely different route. Two independent derivations of the same 1.5×.
>
> **Yield vs headroom:** session C briefly argued this change was a +10 % *yield* win via a front-edge
> model, then **refuted its own model** with run_67 flash-anchored data (DAQ live from ~1.0 ms,
> coverage ~100 %, the plateau is the trigger). **The read clock is headroom, not yield** — the
> original framing below stands. See summary §3.

**Question.** On 10 GbE the network and IPD stopped being the readout ceiling — the beam
*trigger* is (~95 ev/spill, see `docs/network_upgrade_10g/results_2026-07-22_switch_swap.md`).
But the per-event cycle `n × (4.83 + 0.998·IPD) µs` still carries a 4.83 µs SCA-readout term
nobody had ever moved. That term is set by the DREAM read clock `DrmClk RdClk_Div`
(TrigClock 100 MHz / div). This scan asks whether raising it buys sustained rate — and it does.

**Method.** ZS k8, n32 / lat 35, **IPD 2** (chosen so the clock-driven 4.83 µs is 71 % of the
cycle instead of 33 % at IPD 10). Saturating 20 kHz fixed pulser (M6.D) with M4.C veto open →
the DAQ, not the input, is the limit, so any cycle shortening shows up directly as higher
accepted rate. 8 × 0.75 min, triple-bracketed with the nominal (RdClk 6.0) point.
Rate read live as TCM `IntRate`; volume is a clean cross-check (fixed ZS event size, equal
duration → volume ∝ events ∝ rate).

## Result — a faster read clock buys ~1.5×, and the data stays clean

| sub-run | RdClk / WrClk | readout clk | sampling | IntRate | raw vol | vs nominal |
|---|---|---|---|---|---|---|
| nom60_a | 6.0 / 6.0 | 16.7 MHz | 60 ns | 7231 Hz | 2.5 G | 1.00× |
| sp3_probe | 6.0 / 6.0 +SparseRd3 | 16.7 MHz | 60 ns | 7232 Hz | 2.5 G | 1.00× |
| **clk20** | **4.0 / 2.0** | **25 MHz** | **20 ns** | **10 847 Hz** | **3.8 G** | **1.50×** |
| rd4wr4 | 4.0 / 4.0 | 25 MHz | 40 ns | 10 847 Hz | 3.8 G | 1.50× |
| nom60_b | 6.0 / 6.0 | 16.7 MHz | 60 ns | 7231 Hz | 2.5 G | 1.00× |
| clk20_sp3 | 4.0 / 2.0 +SparseRd3 | 25 MHz | 20 ns | 10 847 Hz | 3.8 G | 1.50× |
| **rd3wr4** | **3.0 / 4.0** | **33 MHz** | 40 ns | — | **0.35 M** | **collapse (broken)** |
| nom60_c | 6.0 / 6.0 | 16.7 MHz | 60 ns | 7231 Hz | 2.5 G | 1.00× |

**Brackets nom60_a/b/c all 7231 Hz / 2.5 G — drift excluded, fully reproducible.**

### The headline
- **RdClk 6.0 → 4.0 (16.7 → 25 MHz) = 1.50× sustained rate** (7231 → 10 847 Hz), 0 FEU drops,
  maxFIFOocc pinned at the watermark 11 throughout. The gain is real readout headroom.
- **It's available on the fully vendor-supported 20 ns preset (`clk20`, RdClk 4.0 / WrClk 2.0)** —
  no un-phased clock risk needed to get it. `WrClk_Phase 2 / AdcClk_Phase 5` are valid for this pair.

### Data sanity — the 1.5× is real events, not corruption
Decoded `clk20` vs `nom60_a` (first 3000 events each):
- tracer channels 0/224/511 present in **100 %** of events (both) — the ZS integrity watermark holds
- **96.0 hits/event** both — identical ZS content, so more volume = more events, not fatter events
- chan-0 baseline median **263** both (≈ CmOffset 256), rms 89 vs 92 — no clock-induced pedestal shift
→ the faster clock reads *more good events*, not garbage faster.

### The clock ceiling is between 25 and 33 MHz
`rd3wr4` (RdClk 3.0, 33 MHz) collapsed to 352 kB — essentially no valid readout. 33 MHz is past
what the SCA/phasing tolerates; the FEU stops producing usable data. So **25 MHz is at or near
the usable maximum** on this hardware. A finer step (RdClk 3.5 ≈ 28.6 MHz) could locate the exact
edge, but 25 MHz is the safe operating point and already a vendor-blessed pair.

### SparseRd — the two prior nulls are now RESOLVED as genuine
`sp3_probe` peeked `Main_Conf 0x100004` on the live hardware: **all 8 FEUs held SparseRd=3**
(`dream_scripts/feu_main_conf.py --expect 3` → PASS). So RunCtrl does *not* clamp it (unlike the
watermarks) — the knob is live. And with it live, rate/volume are **flat** (7232 vs 7231 Hz,
2.5 G vs 2.5 G; `clk20_sp3` = `clk20`). **SparseRd genuinely does nothing to readout rate** — the
07-19 and 07-22 nulls stand, now for the right reason (applied and inert, not silently dropped).

## Operating recommendation

**Move the standing DREAM read clock to the 25 MHz / 20 ns preset (RdClk 4.0 / WrClk 2.0).** It is
a 1.5× readout-rate gain with clean, tracer-valid data on a vendor-supported clock pair, at zero
drop cost. Today this does not raise *beam* yield — that is trigger-limited at ~95 ev/spill — but
it widens the margin between the readout ceiling and the trigger, which is exactly the headroom
that a hotter beam, a faster trigger, or a shorter flash-recovery window would consume. The 20 ns
sampling shrinks the waveform window to 0.64 µs (vs 1.92 µs at 60 ns); if the full window is
needed, `rd4wr4` (RdClk 4.0 / WrClk 4.0, 40 ns / 1.28 µs) delivered the same 1.5× — pending its
decoded-tracer confirmation, since that pair is un-phased.

**Do not go to RdClk 3.0 (33 MHz) — it breaks the readout.**

## MADE DEFAULT — 2026-07-23

The 25 MHz read clock is now the production default. **One operative change** — the read clock is
decoupled from the sample clock and pinned at the 4.0 minimum divisor:

```python
# dream_daq_control.py  SAMPLE_PERIOD_CLOCK_DIVS   (RdClk_Div, WrClk_Div)
20: ('4.0', '2.0'),   # unchanged (was already 25 MHz read)
60: ('4.0', '6.0'),   # was ('6.0','6.0') -> now 25 MHz read, 60 ns sample unchanged
```

Every beam run inherits `sample_period: 60` from `run_config_beam.py`, so all of them now read out at
25 MHz with the **1.92 µs / 32-sample / 60 ns window completely unchanged** — same physics, ~1.5×
the readout-rate headroom. The sample clock, latency, PS co-framing, ZS, and the decoded 60 ns sample
spacing are all untouched, so **analysis and calibrations are unaffected** (the read clock changes only
how fast bytes leave the chip, not what the samples mean).

- The cfg **templates were already `RdClk_Div 4.0`** — the `sample_period=60` override had been
  silently downgrading them to 6.0. No template edit was needed; the fix is the map entry above.
- **Server restarted 2026-07-23** to load the new map (a stale server would keep emitting 6.0).
- Verified: a fresh `sample_period=60` cfg now emits `RdClk_Div 4.0 / WrClk_Div 6.0 / NbOfSamples 32`.
- **To revert** to the conservative 16.7 MHz read clock: set `60: ('6.0', '6.0')` and restart the server.
- **Soak still pending** — 25 MHz is a mild overclock of the ASIC's 20 MHz rated RCk; clean in all
  short tests, but a multi-hour run with beam back should confirm long-term stability.

## Documentation cross-check (2026-07-23) — why 25 MHz is a hard floor, and why the sparse scheme can't win

Read `~/Documents/dream` (FEU User's Manual, DREAM ASIC manual) + the RunCtrl/FeuUdpControl source.

**RdClk_Div 4.0 is the enforced minimum — confirmed three ways:**
- `FeuUdpControl/DrmClkConfig.c`: `#define Drm_RdClk_Div_Min 4.0` (also `_Max 15.5`, `WrClk_Min 2.0`).
  The config call rejects `RdClk_Div < 4.0` with `D_RetCode_Err_Wrong_Param`.
- `rd3wr4` (RdClk 3.0) didn't run degraded — its RunCtrl log shows **`FeuCtrl_Open failed`** and the
  per-FEU cfg copy reads `RdClk_Div 0.0` (unset). The 352 kB was a failed configure, not a slow run.
- The docs are *looser* than the firmware: FEU manual says RdClk_Div range `[2;6]`, the cfg comment
  says `{3,4,5,6}` — both wrong. The compiled control software is the authority: `[4.0; 15.5]`.
- AdcClk_Freq is auto-derived from `RdClk_Div` in the same routine (reg==1 uses RdClk_Div), so ADC
  always tracks the read clock — not a failure mode we can trip.

**25 MHz is also already above the ASIC's rated read clock.** DREAM manual: SCA read clock **RCk max
20 MHz** (write clock WCk 1–50 MHz, 512 time bins, "triggered columns only" readout). The firmware's
Div-4.0 floor was sized for a 125 MHz TCM clock (→ 31 MHz); at our 100 MHz TCM it lands at 25 MHz.
So RdClk 4.0 is simultaneously the software floor and a mild overclock of the chip — clean in our
data, but there is genuinely no headroom for a faster read clock without a hardware clock change.

**The "sample fast + sparse to keep the window" scheme cannot beat the 1.5×.** From the ASIC readout
model: a trigger freezes `Nc = NbOfSamples` columns (Trig pulse lasts Nc WCk periods); readout
multiplexes the enabled channels of each frozen column at RCk. So **readout time ∝ NbOfSamples**
(independently confirmed by the real 32/16/8 → 9.6/6.2/2.9 ms comb scaling). For a fixed physics
window T: `NbOfSamples = T · WCk`, so **sampling faster only adds columns and lengthens readout**.
The best a perfect sparse readout could do is claw those extra columns back — returning to the *same*
column count (and same readout time, same rate) as today's 60 ns / 32-sample config. There is no net
win; at fixed window and fixed resolution, `readout_time = (columns spanning the window) / RCk`, and
RCk is maxed. And empirically SparseRd is inert anyway (verified live at SparseRd=3, flat rate *and*
flat volume, here and on 07-22) — it neither shortens readout nor shrinks data in our firmware/ZS mode
(the FEU manual documents it only as a one-line register bit, added late, with no timing description).

**The remaining real levers** (all trade something, none is a faster clock): fewer samples over the
window = coarser resolution (the NbOfSamples lever, e.g. n16); fewer channels read (DreamMask /
ASIC registers 9-10 channel-skip) if any channels are unused — shortens the per-column mux; ZS
(already on) which cuts *network* volume, not the analog readout time.

## Empirical test of the sparse scheme — `win_sparse`, 2026-07-23 (window held FIXED)

Direct test of "sample fast + go sparse to keep the window, win on rate." Every point holds the
**full time window = 1920 ns** and pins **RdClk 4.0 (25 MHz)**; only how the window is built varies.
Saturating 20 kHz pulser, ZS k8, IPD 2.

| point | sample clk | N req | sparse | **IntRate** | samples/event (decoded) | window |
|---|---|---|---|---|---|---|
| coarse_a | 60 ns | 32 | 0 | **10 681 Hz** | 32 | 1920 ns (60×32) |
| fine_nosp | 20 ns | 96 | 0 | **3 617 Hz** | 96 | 1920 ns (20×96) |
| fine_sp2 | 20 ns | 32 | 2 | **10 840 Hz** | 32 | 640 ns (if sparse inert) / 1920 ns (if it works) |
| coarse_b | 60 ns | 32 | 0 | ~10.7 k | 32 | 1920 ns (bracket) |

SparseRd=2 verified live on all 8 FEUs during fine_sp2 (`feu_main_conf.py --expect 2` → PASS);
all points tracer-clean (fine_sp2 500/500). Note raw volume is flat (~3.8 G) at every point because
volume ∝ rate × samples/event is constant (3617×96 ≈ 10681×32) — **rate, not volume, is the metric**.

**Result — the scheme cannot win, shown three ways:**
1. **Readout rate is set by samples READ.** 32 → 10 681 Hz, 96 → 3 617 Hz; the ratio is 2.95 ≈ 96/32.
   Building the 1920 ns window by fine sampling *without* sparse (fine_nosp) costs exactly the 3× the
   `readout_time ∝ NbOfSamples` model predicts.
2. **The fast rate at the full window needs no sparse — just coarse sampling.** coarse_a delivers
   1920 ns at 10.7 kHz reading 32 columns. That is today's operating point (now with RdClk 4.0).
3. **Sparse ties coarse at best, never beats it.** fine_sp2 reads 32 columns → 10.8 kHz = coarse_a.
   And it read **32** samples/event, same as plain n32 — i.e. SparseRd did *not* reduce reads (matching
   the 07-22 flat-volume null), so its window most likely collapsed to 640 ns rather than staying 1920.
   Either way (inert 640 ns, or working-but-1920 ns) it does not beat coarse_a.

**Not resolvable beam-off:** whether fine_sp2's window is 640 ns (sparse inert) or 1920 ns (sparse
working-but-useless) — the two are indistinguishable without a *timed* signal in the waveform, and
beam-off pulser events carry none. It doesn't change the conclusion (no win in either case). To settle
whether SparseRd does anything at all, inject the FEU Dream test pulser (reg 0x200014) at a known
sample and compare its apparent time position between coarse_a and fine_sp2 — a follow-up, out of
scope here. Given two independent register-verified nulls, inert is the leading read.

**Bottom line:** the rate ceiling is `samples_read / RCk`, both maxed at the coarse_a / RdClk-4.0 point.
The 1.5× from the read clock is the whole prize; the sparse path returns to the same point by a longer
road.

## Artifacts
- Config: `run_config_clock_rate_scan.py` → `config/json_run_configs/run_config_clock_rate_scan.json`
- New plumbing: `wrclk_div` per-run/sub-run knob in `dream_daq_control.py` (decouples WrClk from
  the `sample_period` preset). **Server must be restarted after adding it** — was, 2026-07-23 ~10:40.
- New tool: `dream_scripts/feu_main_conf.py` — peeks `Main_Conf 0x100004` (SparseRd, Samples) off
  the hardware; `--expect N` gives a PASS/FAIL clamp verdict.
- Data: `~/july_dream/dream_run/clk_rate/`, decoded `/mnt/data/x17/beam_july/runs/clk_rate/`.
- Trigger restored to `scint(singles)+ps`, PS delay 1800, pulser Poisson 1.5 ms — verified.

## Still to do
- Confirm `rd4wr4` / `clk20_sp3` decoded tracers once the processor reaches them (un-phased 40 ns
  window sanity). The vendor `clk20` point already validates the rate answer.
- Optional: RdClk 3.5 (28.6 MHz) single point to pin the exact ceiling between 25 and 33 MHz.
