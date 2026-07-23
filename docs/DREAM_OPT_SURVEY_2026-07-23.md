# DREAM optimization survey — 2026-07-23 beam-off pulser hour

Deliverable for `docs/PLAN_2026-07-23_beamoff_pulser_hour.md` (§6) and
`HANDOFF_2026-07-23_dream_config_optimization.md` (§7). Executed 12:20–12:45, beam off.
Companion to `docs/CLOCK_RATE_SCAN_2026-07-23.md` (the read-clock work this builds on).

**Headline: no new rate lever was found. Both tested knobs are clean nulls — but one incidental
finding (`PedSub`) is potentially serious and needs follow-up.**

---

## 1. Corrections to the handoff and the plan (record these — both were wrong in the same way)

| claim | source | reality |
|---|---|---|
| "`Rd2AdcDataDel` is at firmware default 0, margin left on the table" | handoff §3.2 | **Wrong.** It is **8**, and it is **explicit in our cfg**: `Tcm_Mx17_July_ZS.cfg:202` / Raw `:197` carry `Feu * Feu_RunCtrl_AdcDatRdyDel 8`. |
| "`DreamRdDel=1` is stale state inherited from the pedestal run; **not in our cfg**" | plan §0 / §1 | **Wrong provenance.** It IS in our cfg: `Tcm_Mx17_July_ZS.cfg:198` / Raw `:193` carry `Feu * Feu_RunCtrl_RdDel 1`. A deliberate template line, not inheritance. |

**Root cause of both errors: grepping the *register* name (`Rd2AdcDataDel`, `DreamRdDel`) instead of
the *cfg keyword* (`Feu_RunCtrl_AdcDatRdyDel`, `Feu_RunCtrl_RdDel`).** The two naming conventions
differ throughout this system. **Always grep the cfg keyword**, and confirm with an emitted cfg
(`make_config_from_template` with no override) rather than reasoning about firmware defaults.

Also correcting the handoff's own §3.1: the ASIC channel-skip registers are **10 and 11**, not 9/10
(8/9 are discriminator inhibit) — the plan caught this, and `FeuConfigParams.c:96-105` hard-initialises
10/11 to `0xFFFF` with the comment *"Never touch Dream registers 10 and 11."*

## 2. DreamMask — DEAD, for two independent reasons

The handoff ranked this #1. It is dead twice over:
1. **No free Dreams.** All 4 detectors use 8/8 connectors on 2 FEUs each (`run_config_beam.py`
   `dream_feus`) → every one of the 8 FEUs × 8 Dreams is instrumented. (Plan §0.)
2. **Even if there were, it could not shorten readout — the Dreams are read in PARALLEL**, not
   serially, so masking one does not shorten the per-event readout at all. Readout time is set by the
   per-Dream column/channel sequence, which masking a *chip* does not touch.

Do not re-open this. The channel-skip route (regs 10/11) remains the only way to test the
readout∝channels model, and the vendor forbids it — worth at most a one-off model-validation
experiment, never an adopted config.

## 3. TEST 1 — `DreamRdDel` 1 → 0 : clean NULL, and safe

Run `rddel`, 6 × 0.75 min, saturating 20 kHz pulser, ZS k8, n32, RdClk 4.0, IPD 2, bracketed ×3.
Config `run_config_rddel_test.py`. Knob newly plumbed as `rd_del`.

`Feu_RunCtrl_RdDel` (0x200008 bit 22) = 1 in our templates. FEU manual §3.2.3: default **0**,
"intended for tests"; when 1 the *first* Dream Read of the train is delayed by a hardcoded **1536 core
clocks** (12.3 µs @125 MHz / 15.4 µs @100 MHz). Against a ~92 µs/event cycle that is 13–17 %, so if it
applied per event it would be a large, obvious win.

| point | rd_del | hardware (peeked, all 8) | IntRate | hits/ev | tracers | chan0 med / rms |
|---|---|---|---|---|---|---|
| nom_a | (tmpl) | 1 | 10 848 Hz | 96.0 | 1500/1500 | 263 / 86.4 |
| rddel1 | 1 | 1 | 10 850 Hz | 96.0 | 1500/1500 | 263 / 86.1 |
| **rddel0** | **0** | **0** (raw 0x8068→0x8028) | **10 849 Hz** | 96.0 | 1500/1500 | 263 / 85.3 |
| nom_b | (tmpl) | 1 | — | — | — | — |
| rddel0_b | 0 | 0 | 10 847 Hz | — | — | — |
| nom_c | (tmpl) | 1 | — | — | — | — |

**Result: NULL on rate (all points within 0.03 %), and data is perfectly clean at rd_del=0.**

- **The knob demonstrably reached the hardware** (raw word changed on all 8 FEUs, `--expect-rddel`
  PASS at both 1 and 0) — this is not another silent-no-op null.
- **`rddel1` reproduces `nom_a`**, so the explicit path and the template path agree.
- **Interpretation:** the delay applies to the *first Read of a train*, exactly as the manual says.
  Under a saturating pulser the trigger queue never drains, so there is effectively one train and the
  12–15 µs is amortised to nothing. **This does not refute a latency effect** — it means the lever, if
  any, lives in the *isolated-trigger* regime (per-event latency), not the sustained-rate ceiling.
- **Safety established:** clearing the bit did NOT desynchronise the SCA readout — tracers 100 %,
  hits/ev 96.0, baseline unchanged. So the "protection" it provides is not load-bearing at our
  32-sample (1.92 µs) trigger pulse, as predicted.

**Recommendation: do NOT change the template.** It buys nothing measurable and the current value is
the safe side. Revisit only if a per-event *latency* measurement becomes interesting — the test would
be a **paced** pulser knee scan (periods giving ~6/8/9/10/11 kHz offered, find where accepted departs
from offered, bracketed rd_del 1 vs 0), or a beam flash-comb measurement. Not worth beam time on
present evidence.

## 4. TEST 2 — `Rd2AdcDataDel` at 25 MHz : **CLOSED — 8 is correct, and it is razor-sharp**

### RESULT (decoded 13:35)

| point | delay | settling | hits/ev | chan0 med | chan0 rms | all-amp rms | verdict |
|---|---|---|---|---|---|---|---|
| adc07 | 7 | 280 ns | 483.5 | 485.0 | 50.62 | 468 | **CORRUPT** |
| **adc08_a** | **8** | 320 ns | **96.0** | 262.0 | 84.50 | **67.33** | **CLEAN — production** |
| **adc08_b** | **8** | 320 ns | **96.0** | 263.0 | 84.50 | **67.30** | **CLEAN — bracket** |
| adc09 | 9 | 360 ns | 503.2 | 229.0 | 57.93 | 987 | **CORRUPT** |
| adc10 | 10 | 400 ns | 883.1 | 246.0 | 62.05 | 1032 | CORRUPT |
| adc12 | 12 | 480 ns | 1053.2 | 188.0 | 23.99 | 1049 | CORRUPT |

(adc06 still decoding at writeup; non-essential — 7 already fails, so the boundary is bracketed on
both sides.) Brackets reproduce exactly (96.0 / 67.33 vs 96.0 / 67.30) ⇒ drift excluded. IntRate was
flat throughout (10 848-10 849 Hz), the correct control for a non-rate knob.

**`Rd2AdcDataDel = 8` is a single-valued optimum: ±1 cycle (±40 ns) destroys the data.**
Today's 25 MHz production default is therefore CONFIRMED CORRECT — the main open worry about the
shipped read-clock change is retired.

### The prediction was WRONG, and the mechanism is now understood

The prediction in `run_config_adcdel_scan.py` — that the delay encodes a fixed *physical* settling
time, so the optimum should scale with clock frequency to ~9-10 at 25 MHz — is **refuted**. 8 is
optimal at 25 MHz, and 8 was also fine at our old 16.7 MHz default (clean production data for months).

**Correct mechanism: it is a digital PIPELINE-DEPTH parameter, not a settling time.** It compensates a
fixed number of clock cycles between issuing the Dream Read strobe and the corresponding ADC word
arriving. The strobe and the ADC clock both derive from RdClk (AdcClk is auto-slaved to RdClk_Div in
`DrmClkConfig.c`), so the pipeline depth in *cycles* is invariant under clock changes. That explains:
- why 8 is right at 16.7, 20.8 and 25 MHz alike (the manual's "for the 20.8 MHz read clock" is just
  the frequency they characterised at — the value is actually clock-independent);
- why ±1 cycle is catastrophic rather than merely sub-optimal: being off by one pipeline stage
  mis-attributes every ADC sample to the wrong channel, so the whole event is misaligned.

**Practical consequence: `Rd2AdcDataDel` does NOT need re-tuning if the read clock changes again.**
Leave it at 8. It is not a free-S/N knob — there is no S/N to reclaim, only a cliff on either side.

### Failure signature (useful diagnostically)

Misalignment presents as **hits/event exploding 96 → 480-1050** (zero-suppression stops suppressing,
because misattributed samples look like signal everywhere) and **all-amplitude RMS 67 → ~1000**.
Baseline median wanders (485/229/246/188) instead of sitting at ~263.

**⚠ The ZS tracer watermark does NOT detect this.** Tracer channels 0/224/511 read **1500/1500 in
every corrupted point** — with ~1000 hits/event essentially every channel is present, tracers
included. **Tracers detect data LOSS, not data FLOODING.** Any future integrity check must pair the
tracer test with a hits/event bound. This corrects a check relied on throughout the clock scan.

**It also stresses the processing chain:** the flooded sub-runs produced 40 root files vs 24 for the
clean ones and repeatedly tripped the decoder's truncation-repair path
(`TRUNCATED ... re-decoding ... still short after retry`). So mass truncation warnings *plus*
hits/event in the hundreds ⇒ suspect ADC misalignment, not a disk/network fault.

### Original design rationale (kept for the record)

Run `adcdel`, 7 × 0.75 min, ladder **8(a) / 6 / 7 / 9 / 10 / 8(b) / 12**, all at RdClk 4.0.
Config `run_config_adcdel_scan.py`. Knob newly plumbed as `adc_dat_rdy_del`.

**Purpose: de-risk a change already in production.** The manual specifies 8 *for the 20.8 MHz read
clock*; we now read at 25 MHz. The delay is counted in read-clock **cycles** while the thing being
waited out (analogue settling into the ADC) is a fixed **physical** time, so the required count should
scale with frequency:

    8 cycles @ 20.8 MHz = 8 × 48.0 ns = 384 ns of settling
    same 384 ns @ 25 MHz (40 ns period) = 9.6  ->  optimum should sit near 9-10, not 8

If that holds we are currently latching the ADC ~64 ns early, part-way up the settling edge — costing
S/N, not rate.

**Confirmed so far:**
- Knob reaches hardware — `--expect-adcdel` PASS at 8, 6 and 10 (raw 0x8068 / 0x8066 / 0x806A).
- **IntRate is FLAT** (10 849 Hz at adc06 vs 10 848 at adc08_a) — correct for a non-rate knob, and a
  useful control: if rate HAD moved, something else changed and the run would be suspect.
- adc08_a (production value): hits/ev 96.0, tracers 1500/1500, chan0 med 262.0, **rms 84.50**.

**PENDING:** the processor had decoded only adc08_a at time of writing. **The measurement is the
baseline-RMS-vs-delay curve** — fill this table when decode completes:

| point | delay | settling | chan0 rms | tracers | verdict |
|---|---|---|---|---|---|
| adc08_a | 8 | 320 ns | **84.50** | 1500/1500 | production reference |
| adc06 | 6 | 240 ns | *pending* | | |
| adc07 | 7 | 280 ns | *pending* | | |
| adc09 | 9 | 360 ns | *pending* | | |
| adc10 | 10 | 400 ns | *pending* | | ~matches the manual's 384 ns |
| adc08_b | 8 | 320 ns | *pending* | | must reproduce adc08_a |
| adc12 | 12 | 480 ns | *pending* | | |

Read it as: **U-shaped RMS with the minimum away from 8 ⇒ free S/N at the new clock** (adopt the
minimum); **flat ⇒ 8 is fine and the 25 MHz default is confirmed sound** (also valuable — it retires
the main open worry about a shipped change). If *nothing* moves across 6→12, suspect the write and go
back to the peek — but the peek already PASSED at three values, so a flat curve here is a real null.

## 5. ⚠ INCIDENTAL FINDING — `PedSub = 1` on hardware while every ZS cfg sets `Pd = 0`

Found while verifying the register during a **live data run** (not idle residue — peeked mid-run):

```
0x200008 = 0x8068204F  ->  PedSub=1  CM=1  ZS=1  ZsTyp=1  ZsChkSmp=4  CmnPedOffset=256
```

Every ZS run config sets `pedestal_subtraction: False`, and the emitted cfg really does contain
`Feu * Feu_RunCtrl_Pd 0`. **Yet the hardware reads PedSub=1.** Bit 0 = PedSub is confirmed from the
FEU manual §3.2.3 (line 759, default 0), and every *other* bit in that word (CM/ZS/ZsTyp/ZsChkSmp)
matches the cfg exactly — so the bit map is right and this is a genuine disagreement, not a decode error.

**Why it may matter:** this is the "Option B" assumption (`docs/ZS_PULSER_TEST_PROCEDURE.md`) — firmware
pedestal subtraction OFF, offline `analyze_waveforms` subtracts instead. If the firmware is *also*
subtracting, ZS data is being **double-subtracted**.

**Supporting evidence it is real, not a mis-read:** the decoded chan-0 baseline sits at **~263**, i.e.
essentially `CmnPedOffset = 256`. That is what you expect when the firmware subtracts and re-adds the
offset. With Pd genuinely 0 the raw per-channel pedestals would remain and the baseline would sit at
the raw ADC level, varying channel to channel — not pinned near 256.

**Counter-evidence / why NOT to panic yet:** the 07-19 ZS pulser test compared offline-with-pedestal
vs offline-without and concluded the with-pedestal path gives clean, physical, higher-amplitude hits —
which is the behaviour expected if the firmware were NOT subtracting. The two observations are in
tension and it is not resolved.

**This is the highest-value follow-up out of this hour** — it concerns data correctness, not rate.
Suggested resolution, none of which needs beam:
1. Check whether `Feu_RunCtrl_Pd` is actually the cfg keyword bound to bit 0 (`FeuConfigParams.c`), and
   whether RunCtrl overrides it on the data branch the way it does for the pedestal branch
   (`RunCtrl.c:1114` sets `Feu_RunCtrl_Pd = 0` for pedestal runs — check the data path).
2. Peek 0x200008 immediately before and after the data-run configure to see when bit 0 gets set.
3. Take one short ZS pulser run with `pedestal_subtraction: True` and compare decoded baselines — if
   identical to today's, the firmware was already subtracting regardless of the cfg.

## 6. Not done / not worth it

- **TEST 3 (Dream regs 10/11 channel mask)** — not run. Vendor-forbidden, decoder likely intolerant of
  a half-populated Dream, and it is model-validation rather than an adoptable lever. Superseded in
  value by §5.
- **MultiPack re-check** — network-efficiency only; we are not network-limited on 10 GbE at IPD 2. The
  07-22 "103 MB vs 1.5 GB" anomaly is almost certainly the rate-vs-volume confusion already diagnosed
  (volume ∝ rate × samples/event).
- **RdClk 3.5** — pins a ceiling we will never operate at (and 4.0 is the enforced firmware floor).

## 7. Housekeeping / state

- **Trigger fully restored** and verified: `scint(singles)+ps`, M4.C `or_veto[0]`, PS delay 1800,
  pulser back to Poisson 1.5 ms.
- **⚠ The FEUs were left holding `Rd2AdcDataDel = 12`** (the last sub-run's value) — the FEUs retain
  the last write. This **self-heals at the next run**, because both templates carry explicit
  `Feu_RunCtrl_AdcDatRdyDel 8` and `Feu_RunCtrl_RdDel 1` lines that are written at every configure.
  Verified that an override-free cfg emits 8/1. No action needed, but do not be confused by a peek
  taken before the next run.
- **New plumbing** in `dream_daq_control.py`: `rd_del`, `adc_dat_rdy_del` (per-run and per-sub-run).
  **Server restarted 12:28** to load them.
- **New tool:** `dream_scripts/feu_runctrl_reg.py` — decodes RunControl 0x200008 on all 8 FEUs with
  `--expect-rddel` / `--expect-adcdel` PASS/FAIL. Use it as the standard verifier; it also surfaces
  PedSub/CM/ZS/ZsTyp/CmnPedOffset, which is how §5 was found.
- **No production defaults were changed** by this hour's work.
