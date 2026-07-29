# PLAN — beam-off saturating-pulser hour, 2026-07-23 (~12:15 → ~13:30)

**For:** the model that will actually run this. Written after reading
`HANDOFF_2026-07-23_dream_config_optimization.md`, `docs/CLOCK_RATE_SCAN_2026-07-23.md`, the FEU /
DREAM manuals, the RunCtrl + FeuUdpControl sources, the cfg template, and **live register peeks off
all 8 FEUs** (done while writing this — see §1).

State at writing: beam OFF since 09:53 (`beam_state.json`, 8200 s since last pulse), no run active,
`dream_daq` server up with the 25 MHz-default clock map.

---

## 0. TL;DR — what changed vs the handoff's ranking

The handoff ranked **DreamMask/channel-masking #1** and **Rd2AdcDataDel #2**. Desk work + live peeks
move both and surface a new #1:

| handoff rank | lever | verdict now |
|---|---|---|
| 1 | DreamMask (Main_Conf bits 7:0) | **DEAD — no free Dreams.** All 4 detectors use 8/8 connectors on 2 FEUs each (`run_config_beam.py` `dream_feus`) → 8 FEUs × 8 Dreams, every one instrumented. `set_active_feus()` already masks per-run when a detector is excluded. Nothing to win. |
| 1b | ASIC channel-skip | **Re-identified and vendor-forbidden.** Manual §7.6.K/L: the SCA-read-enable bits are registers **10 and 11** (not 9/10 — 8/9 are *discriminator* inhibit, which our cfg already sets to all-on). `FeuConfigParams.c:96-105` hard-initialises regs 10/11 to `0xFFFF/0xFFFF` with the comment *"Never touch Dream registers 10 and 11 / All 64 Dream channels must be enabled for readout."* Still cfg-overridable (`Feu * Dream * 10 ...`) → keep as a **stretch** experiment only, to *quantify* the readout∝channels model. |
| 2 | Rd2AdcDataDel | **ANSWERED by peek — it is already 8**, not 0, on all 8 FEUs (`0x200008` bits 20:16, see §1). The handoff's premise ("firmware default 0, margin left on the table") is wrong. The *remaining* question is different and still good: 8 is the manual's value **for the 20.8 MHz read clock**, and we now run **25 MHz** — is 8 still optimal? |
| — | **DreamRdDel (`Feu_RunCtrl_RdDel`)** | **NEW #1.** Peek shows **`DreamRdDel = 1` on all 8 FEUs.** The FEU manual §3.2.3 calls this bit *"intended for tests"*, default **0**, and says it delays the first Dream Read of the train by a hardcoded **1536 core-clock cycles**. It is **not in our cfg** — it is stale state inherited from the pedestal run that precedes every data run (`RunCtrl.c:1129/1157/1185/1193` set `Feu_RunCtrl_RdDel = 1` on the Constant/low-rate pedestal branches; the `Tg_Src_ExtSyn` branch we use for data never sets it, and an unset param is never written). **A one-line cfg change may remove ~12–15 µs of per-event readout latency that nobody put there on purpose.** |

Everything below is framed the way the handoff asks: **headroom** vs **yield**. At beam we are
trigger-limited (~95 ev/spill), so none of this raises today's yield. Test 1 is the only candidate
that could also shorten *per-event latency*, which is the flash-comb-relevant quantity.

---

## 1. Live register facts (measured 2026-07-23 12:12, read-only peeks, all 8 FEUs)

`RunControl` register **`0x200008`** (FEU manual §3.2.3) — identical on all 8:

```
0x8068XX4F  PedSub=1 CM=1 ZS=1 ZsTyp=1 ZsChkSmp=4 DrRawOvh=0
            Rd2AdcDataDel=8   EvTstExt=1   DreamRdDel=1   CmnPedOffset=256
```

- `Rd2AdcDataDel = 8` — matches the manual's recommendation **for 20.8 MHz**. Not from our cfg.
- `DreamRdDel = 1` — non-default test bit, ON. Not from our cfg. **This is the finding.**
- `CmnPedOffset = 256` — explains the decoded baseline median 263 in the clock scan. Consistent.

`0x200018` (Prescale/IPD): `prescale=1`, IPD field `250` core cycles = 2000 ns for the cfg's
`inter_packet_delay: 2` → **the cfg IPD unit is µs**, which independently confirms the `0.998·IPD µs`
term in the fitted deadtime model. Useful sanity check; no action.

Reproduce with:
```bash
.venv/bin/python -c "
import sys; sys.path.insert(0,'dream_scripts')
from feu_trig_counters import FEUS, peek
for slot,(fid,ip) in FEUS.items():
    v=peek(ip,1300+fid,0x200008)
    print(f'slot{slot} 0x200008=0x{v:08X} Rd2AdcDataDel={(v>>16)&0x1F} DreamRdDel={(v>>22)&1}')"
```
Save this as `dream_scripts/feu_runctrl_reg.py` (mirror `feu_main_conf.py`, add `--expect-rddel N` /
`--expect-adcdel N`) — you need it as the per-sub-run hardware verifier anyway. **10 minutes, do it
first**; every result below is worthless without the peek (the cfg is not proof — see §5 of the
handoff).

---

## 2. Plumbing required before ANY of this (≈10 min, do once)

Two new knobs in `dream_daq_control.py::make_config_from_template`, exactly like `sparse_rd`:

```python
if rd_del is not None:
    # Feu_RunCtrl_RdDel -> RunControl 0x200008 bit 22 (DreamRdDel). FEU manual 3.2.3: "intended
    # for tests", default 0; when 1 the FIRST Dream Read of the train is delayed by a hardcoded
    # 1536 core-clock cycles. NOT in our template -> the FEU inherits 1 from the pedestal run
    # (RunCtrl.c sets it on the Constant/low-rate pedestal branches). Setting it explicitly to 0
    # is the vendor default for data runs. VERIFY by peek: 0x200008 bit 22.
    updates["Feu * Feu_RunCtrl_RdDel"] = int(rd_del)
if adc_dat_rdy_del is not None:
    # Feu_RunCtrl_AdcDatRdyDel -> 0x200008 bits 20:16 (Rd2AdcDataDel): read-clock cycles the logic
    # waits between the Dream Read strobe and valid ADC data. Manual: "for the 20.8 MHz read clock
    # this value is usually set to 8". Hardware currently holds 8; we now read at 25 MHz.
    updates["Feu * Feu_RunCtrl_AdcDatRdyDel"] = int(adc_dat_rdy_del)
```

Both keys are genuine vendor cfg keywords — verified present in
`FeuUdpControl/FeuConfigParams.c` (`Feu_RunCtrl_RdDel` line 990, `Feu_RunCtrl_AdcDatRdyDel` line
1007). They are only written when >= 0, so absence = today's behaviour.

**THEN RESTART THE SERVER** — this is the trap that invalidated two earlier studies:
```bash
tmux send-keys -t dream_daq C-c ; tmux send-keys -t dream_daq C-c
tmux send-keys -t dream_daq 'PATH=/home/mx17/PycharmProjects/nTof_x17_DAQ/bash_scripts/daq_shims:$PATH python dream_daq_control.py' Enter
```
Sanity: build one cfg and grep it for both new lines before launching.

---

## 3. Trigger setup (≈5 min)

```bash
.venv/bin/python n1081b/set_pulser.py --fixed --period 50000 --width 100   # M6.D, 20 kHz
.venv/bin/python n1081b/set_veto_open.py --lemos 4                          # M4.C veto open — REQUIRED
```
Only .245 (M6) and .243 (M4) are touched. Check `config/n1081b_access/` for a holder first; ignore
the known-stale `.244` quarantine entry but do not assume.

---

## 4. The tests, in priority order

Common: ZS k8, n32, latency 35, IPD 2, `sample_period 60` (RdClk 4.0 / WrClk 6.0 — today's default),
**0.75 min sub-runs, nominal bracketed at both ends.** Metric = **IntRate** (`dream_daq` pane, and/or
`feu_trig_counters.py --latch`) — *never volume*, per the clock-scan lesson. Copy
`run_config_clock_rate_scan.py` as the skeleton.

### TEST 1 — DreamRdDel 1 → 0 *(the one to do; ~15 min)*
`run_config_rddel_test.py`, 6 sub-runs:

| # | label | `rd_del` | purpose |
|---|---|---|---|
| 1 | nom_a | (unset) | bracket — inherits 1 |
| 2 | rddel1 | 1 | explicit 1 — proves the knob reaches hw and reproduces nom |
| 3 | **rddel0** | **0** | the measurement |
| 4 | nom_b | (unset) | bracket |
| 5 | rddel0_b | 0 | repeat |
| 6 | nom_c | (unset) | bracket |

Per sub-run: peek `0x200008` bit 22 on all 8 FEUs and record it next to the rate. Sub-runs 1/2 must
agree (if `rddel1` ≠ `nom_a`, the knob is doing something other than what you think — stop and think).

**Predictions.** Per-event cycle at this point is 92 µs (10 847 Hz measured). 1536 cycles is
12.3 µs @125 MHz core / 15.4 µs @100 MHz. If the delay applies once per event, `rddel0` gives
**+13 to +20 % rate**. If it applies only to the first event of a queued train, saturating pulser
shows **NULL** — that is not a refutation, it means the lever lives in the *isolated-trigger* regime.

**If NULL, the follow-up (only if time):** the observable becomes latency, not ceiling. Take a
**paced** fixed pulser knee scan — periods giving ~6, 8, 9, 10, 11 kHz offered — and find where
accepted departs from offered, bracketed `rd_del` 1 vs 0. The knee is 1/cycle; a 12–15 µs term moves
it visibly. Cheaper alternative for the report: state the mechanism, the register evidence, and
"needs a paced knee scan / beam comb measurement".

**Risk & recovery.** `rd_del=0` is the vendor default for data runs and the value RunCtrl itself
forces on its high-rate pedestal branches. The bit exists to guarantee Dream Read never overlaps
Dream Trigger; our Trigger pulse is only 32 WCk = 1.92 µs, so the protection is not load-bearing
here. **Still: this is the one change that could desynchronise the SCA readout.** Gate acceptance on
decoded sanity, not rate — tracer channels 0/224/511 in ~100 % of events and chan-0 baseline median
≈ 263, exactly as the clock scan did. Any tracer loss or baseline shift ⇒ reject, report, revert.
Reverting is just dropping the cfg line.

### TEST 2 — Rd2AdcDataDel at 25 MHz *(de-risks today's default; ~20 min)*
`run_config_adcdel_scan.py`, 7 sub-runs at fixed RdClk 4.0: `adc_dat_rdy_del` = **8 (nom), 6, 7, 9,
10, 8 (bracket), 12**. Peek `0x200008` bits 20:16 each time.

**This is not a rate test** — the delay does not change readout duration, it changes *when the ADC
latches the multiplexed analogue data*. The metric is **data quality**:
- tracer presence (0/224/511) — the integrity watermark
- chan-0 baseline **median and RMS** (clock scan: 263 / rms 89–92)
- hits/event (was 96.0 at both clock points)

A U-shaped RMS vs delay with the minimum away from 8 = we are off-centre in the ADC eye at 25 MHz and
there is free S/N. A flat curve = 8 is fine and the 25 MHz default is confirmed sound — **also a
valuable result**, since it retires the main open worry about a change that is already in production.
Values far off the optimum should visibly degrade (garbage/tracer loss); if *nothing* changes at all
across 6→12, suspect the write did not land and go back to the peek.

Note this test needs the processor to decode before you can read it — queue the decode and do not
block the hour on it.

### TEST 3 — SCA read-enable mask, Dream regs 10/11 *(stretch, ~15 min, only if 1 & 2 finish clean)*
Purpose is **model validation, not adoption**: does readout time really scale with enabled channels?
Add `Feu * Dream * 10 0xFFFF 0x0000 0x0000 0x0000` (+ reg 11 likewise) to read 32 of 64 channels.
Prediction from the ASIC model: **~2× rate**. A 2× would put a hard number on a real Pareto option
(halve spatial coverage → double rate) for any future fast-rate special mode.

**Flags before doing this:** (a) the vendor source says never touch these registers; (b) the decoder
may not tolerate a half-populated Dream — expect to have to interpret the FDF by hand; (c) it is
cfg-only, so the next normal run restores 0xFFFF. Do **not** leave it set. If short on time, skip —
Tests 1 and 2 are worth more and this one is a "nice quantification", not a lever we would adopt.

### NOT worth this hour
- **MultiPack re-check** — network-efficiency only, and we are not network-limited on 10 GbE at IPD 2.
  The 07-22 "103 MB vs 1.5 GB" anomaly is almost certainly the rate-vs-volume confusion the clock scan
  already diagnosed (volume ∝ rate × samples/event). Note it in the report, do not spend beam-off time.
- **RdClk 3.5** — pins a ceiling we will never operate at.
- **SparseRd timed-pulse test** — the FEU internal pulser (`Feu_Pulser_*`, 0x200014) *is* in the
  template and would settle it, but the verdict does not change either way. Below the cut.

---

## 5. Timeline (60–75 min)

| min | action |
|---|---|
| 0–10 | write `feu_runctrl_reg.py`, peek baseline on all 8 FEUs, record |
| 10–20 | plumb `rd_del` + `adc_dat_rdy_del`, **restart `dream_daq`**, verify a built cfg |
| 20–25 | pulser + veto open; confirm beam still off |
| 25–42 | **TEST 1** (6 × 0.75 min + peeks) |
| 42–65 | **TEST 2** (7 × 0.75 min + peeks) |
| 65+ | restore, then write up; TEST 3 only if everything above is clean and beam is still off |

**Watch `config/beam_state.json` throughout** — the saturating pulser drops beam triggers.
`beam_on: null` means UNKNOWN, not off; confirm via CSV rows/minute.

**RESTORE (mandatory, also the moment beam returns):**
```bash
.venv/bin/python n1081b/trigger_mode.py scint --singles --ps-pickup
.venv/bin/python n1081b/set_pulser.py           # back to Poisson
.venv/bin/python n1081b/set_veto_open.py --show          # expect C = or_veto[0]
.venv/bin/python n1081b/set_ps_trigger_delay.py --show   # expect delay 1800
```
Also confirm the *next* production cfg carries no leftover `rd_del` / `adc_dat_rdy_del` /
`Dream * 10` line unless it was explicitly approved.

---

## 6. Deliverable

`docs/DREAM_OPT_SURVEY_2026-07-23.md` per §7 of the handoff. It should at minimum record, whatever
the bench says:

1. **DreamMask is dead** — all 64 Dreams instrumented; with the `dream_feus` evidence, so nobody
   re-opens it.
2. **The channel-skip registers are 10/11, not 9/10**, and the vendor forbids them — correct the
   handoff's citation.
3. **`Rd2AdcDataDel` is already 8** (peeked), so the "left at 0" premise was wrong; report whatever
   the 25 MHz scan says about whether 8 is still right.
4. **`DreamRdDel = 1` is stale pedestal-run state on production hardware** — with the rate result.
   Even a null here is worth a memory note: it is a non-default test bit that has been silently on
   for every run we have ever taken.

Memory notes worth writing after: one for the DreamRdDel finding, one amending
`dream-clock-firmware-limits` with the peeked `0x200008` state. Do **not** change production defaults
without flagging to the operator first.
