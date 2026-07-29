# DAQ optimization — consolidated summary, 2026-07-23

**Read this first.** Three parallel Claude sessions worked DREAM/DAQ optimization on 2026-07-23 and
produced five documents. This is the single entry point: what was established, what was refuted, what
is now in production, and what is still open. Several intermediate claims were **corrected within the
same day** — the table in §2 is the authoritative end state; do not quote an intermediate claim from
one of the source documents without checking it here.

## 1. The documents

| doc | session | what it is |
|---|---|---|
| `CLOCK_RATE_SCAN_2026-07-23.md` | A | The read-clock result (1.5×), the firmware clock limits, SparseRd nulls, the window-fixed sparse test. **The production change came from here.** |
| `DREAM_OPT_SURVEY_2026-07-23.md` | A | The beam-off pulser hour: `DreamRdDel` (null) and `Rd2AdcDataDel` (confirmed 8, razor-sharp) + the `PedSub` anomaly. |
| `PLAN_2026-07-23_beamoff_pulser_hour.md` | B | The plan session A executed. **Its §0/§1 provenance claims are wrong** — see §4. |
| `IPC_YIELD_OPTIMIZATION_2026-07-23.md` | C | IPC-weighted yield + HV analysis. **Its front-edge model was refuted the same day** — see §3. |
| `HANDOFF_2026-07-23_dream_config_optimization.md` | A | The cold-start handoff that seeded the survey. Its lever ranking is superseded by §2. |

Supporting (earlier): `network_upgrade_10g/results_2026-07-22_switch_swap.md` (10 GbE, trigger-limited),
`FEU_WATERMARKS_2026-07-22.md` (RunCtrl clamps + the stale-server trap), `DREAM_flash_comb_study_2026-07-19.md`
(now carrying a 2026-07-23 correction retiring its `n16` advice).

## 2. Authoritative end state — every lever tried

| lever | verdict | evidence |
|---|---|---|
| **Read clock `RdClk_Div` 6.0→4.0 (16.7→25 MHz)** | ✅ **ADOPTED — 1.5× readout rate, free** | 7231→10847 Hz, 0 drops, tracers 100 %, baseline unchanged, triple-bracketed. Window untouched. |
| `RdClk_Div` < 4.0 | ❌ impossible | 4.0 is the hard firmware floor (`DrmClkConfig.c`); 3.0 → `FeuCtrl_Open failed`. Also already above the ASIC's rated 20 MHz RCk. |
| **`Rd2AdcDataDel`** | ✅ **confirmed 8 — leave alone** | ±1 cycle destroys data (96 → ~500 hits/ev). Pipeline-depth count, clock-invariant ⇒ no re-tune when the clock changes. |
| `DreamRdDel` 1→0 | ⚪ clean null, safe | Rate flat to 0.03 % under saturation (per-*train* delay, amortised). Leave at 1. |
| `SparseRd` | ⚪ live but inert | Register verified held at 2 and 3 on all 8 FEUs; rate *and* volume flat. |
| "sample fast + sparse, same window" | ❌ cannot win | At fixed 1920 ns window: coarse/32 reads = 10681 Hz vs fine/96 reads = 3617 Hz (ratio 2.95 ≈ 96/32). Sparse ties coarse at best. |
| `NbOfSamples` (n16/n8) | ❌ **retired** | Front edge is INVARIANT in n (dump term cancels); and on 10 GbE we are trigger-limited. **Keep n32 / 1.92 µs.** |
| `DreamMask` / channel masking | ❌ dead twice over | No free Dreams (all 8 FEUs × 8 Dreams instrumented), **and the 8 Dreams read in PARALLEL** so masking a chip cannot shorten readout — it is a volume knob only. |
| ASIC channel-skip (regs **10/11**, not 9/10) | ❌ vendor-forbidden | `FeuConfigParams.c:96-105` hard-sets `0xFFFF` with *"Never touch Dream registers 10 and 11."* |
| Trigger-FIFO watermarks | ❌ null | Band is trigger-limited; RunCtrl one-directionally clamps the cfg value anyway. |
| ZS | ⚪ **no rate uplift** — storage only | ZS runs in FEU firmware *after* digitisation; the ASIC still clocks every frozen column × channel. On 10 GbE nothing left to relieve. Still worth running: ~9 % of RAW size. |
| Network > 10 GbE | ❌ no case | Readout stopped being the constraint before the network did. |

**Net result of the day: one adopted change (the read clock, 1.5×), one confirmation
(`Rd2AdcDataDel`=8), and a large set of well-evidenced dead ends.** The readout is now bounded by
`samples_read / RCk` with both maxed.

## 3. The headroom-vs-yield question — settled as HEADROOM

This flip-flopped during the day; the end state is:

1. Session A originally stated the read clock buys **headroom, not yield**, because at beam we are
   trigger-limited (~95 ev/spill).
2. Session C's front-edge model briefly argued it was a **+10 % yield win** (front edge 4.45→3.95 ms
   against an IPC spectrum with 39.8 % of in-gate rate arriving before 4.46 ms).
3. Session C then **refuted its own model** with run_67 flash-anchored data: the first event lands at
   0.996–1.007 ms, i.e. the DAQ is live the instant the 1 ms N93B gate opens; coverage is already
   ~100 % from 1 ms and the 2.0 ev/ms plateau is the **trigger**, not a DAQ ceiling.

⇒ **No DAQ knob buys IPC yield. The read clock is headroom.** The original framing stands. Headroom
still matters — for hotter beam, a denser trigger, or a shorter flash-recovery window — but do not
quote the read clock as a yield gain.

## 4. Corrections to record (all cost real time today)

- **cfg keyword ≠ register name.** `Feu_RunCtrl_RdDel` ↔ *DreamRdDel*; `Feu_RunCtrl_AdcDatRdyDel` ↔
  *Rd2AdcDataDel*. Grepping the register name finds nothing and leads to the false conclusion
  "not in our cfg / at firmware default". **Both are explicit template lines** (ZS template 198/202).
  Two independent sessions made this exact error. Always grep the cfg keyword and confirm against an
  emitted override-free cfg.
- **The manuals are looser than the firmware.** FEU manual says `RdClk_Div` range `[2;6]`, the cfg
  comment says `{3,4,5,6}`; the compiled source enforces `[4.0;15.5]`. **Source wins.**
- **The cfg template's own RunControl bit-map comment is wrong** (`bit3=DrOvh`, `ADC DataRdy 21-16`).
  The FEU manual §3.2.3 map (`bit3=ZsType`, `bit7=DrRawOvh`, `Rd2AdcDataDel 20:16`, `EvTstExt 21`) is
  the one that fits the observed register word. Use the manual's.
- **ZS tracer channels do NOT detect data flooding**, only data loss — they read 1500/1500 in every
  corrupted `Rd2AdcDataDel` point because at ~1000 hits/event every channel is present. **Pair the
  tracer check with a hits/event bound** (~96 at n32 ZS k8). This check was relied on throughout the
  clock scan.
- **Raw data volume is not a rate proxy** when samples/event varies (volume ∝ rate × samples/event).
  Use IntRate.
- **`n16 ≈ doubles the yield`** in the flash-comb study is retired (correction added in place).

## 5. Production state after today

- **Read clock 25 MHz is the default.** One line: `SAMPLE_PERIOD_CLOCK_DIVS[60] = ('4.0','6.0')` in
  `dream_daq_control.py` (was `('6.0','6.0')`). All beam runs inherit `sample_period: 60`, so all get
  it; the 1.92 µs / 32-sample / 60 ns window is unchanged, so **analysis and calibrations are
  unaffected** — the read clock only changes how fast bytes leave the chip.
- **Templates already carried `RdClk_Div 4.0`** — the `sample_period` override had been silently
  downgrading them to 6.0. No template change was needed for the clock.
- `Rd2AdcDataDel 8` and `DreamRdDel 1` remain the template values (both confirmed correct / leave-alone),
  now with protective comments in both templates.
- **New per-run/per-sub-run knobs** in `dream_daq_control.py`: `wrclk_div`, `rd_del`, `adc_dat_rdy_del`
  (alongside the existing `rdclk_div`, `sparse_rd`, …). **The server must be restarted after adding any
  knob** or it is silently dropped.
- **New read-only verifier tools:** `dream_scripts/feu_main_conf.py` (Main_Conf 0x100004 — SparseRd,
  Samples) and `dream_scripts/feu_runctrl_reg.py` (RunControl 0x200008 — RdDel, AdcDel, PedSub, CM, ZS,
  ZsTyp, CmnPedOffset), both with `--expect-*` PASS/FAIL. Use these to verify any register change.

## 6. Open items, in priority order

1. **⚠ `PedSub = 1` on hardware while every ZS cfg sets `Pd = 0`** — peeked mid-*data*-run, so not idle
   residue. If the firmware subtracts pedestals while `analyze_waveforms` also does, ZS data is
   **double-subtracted** (breaks the Option B assumption). Supporting: decoded baseline pinned at ~263
   ≈ `CmnPedOffset` 256. Against: the 07-19 ZS test behaved as if firmware were not subtracting. One
   plausible resolution: TPC-mode ZS may *require* pedestal subtraction internally and the firmware
   forces it. **Needs no beam.** Highest value — this is data correctness, not performance.
2. **25 MHz soak** — a multi-hour run to confirm the mild ASIC overclock is stable over a shift.
   Operator elected to do this with beam back.
3. **ZS-vs-RAW tracking validation has never been done** (run_67 was RAW) and the IPC/HV conclusions
   route through ZS.
4. **HV: IPC-weighted optimum 525 V vs the 530 V we run** (+18 %, sharply peaked) — session C's HV
   analysis survives its own model's refutation. Needs an operator decision.
5. **Det D contributes nothing in 1–13 ms at any usable HV** — resolve whether real or the known D
   noise/CM contamination.
