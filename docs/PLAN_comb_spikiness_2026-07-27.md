# Acceptance-comb spikiness — measurement, and the two tests queued against it

**2026-07-27.** Operator flagged that the trigger distribution vs time-since-gamma-flash is
still spiky on the current production point, which costs neutron-energy coverage. This
records what the data actually says, and the two runs prepared to fix it.

---

## 1. The live run really is at Hwm 2 — verified both ways

run_81 (`run_config_stats_optimized_81.json`, the same operating point as run_79) requests
`ovr_wrn_hwm 2 / ovr_wrn_lwm 1`, and both halves of the verification checklist pass:

- **archived cfg**, `run_81/stat090_0001/raw_daq_data/Tcm_Mx17_July.cfg`:
  `Feu * Main_Trig_OvrWrnHwm 2`, `Feu * Main_Trig_OvrWrnLwm 1`
- **hardware**, live read via `dream_scripts/feu_trig_counters.py`: **Hwm 2 / Lwm 1 on all
  eight FEUs**

> ⚠ That tool's footer said *"occupancy never reaches HWM=2: the watermark cannot be
> biting."* **Ignore it.** The accepted/closeDrop/fifoDrop/maxFIFOocc counters read 0 because
> the read-only default does not issue the `--latch` poke those counters require, and the
> footer is computed from those unlatched zeros. It is a bug in the message, not a finding.

## 2. The 0.5 ms CV metric was hiding a real comb

The metric we optimised run_78/run_79 against — CV of the 1–10 ms distribution in **0.5 ms**
bins — reads 0.420 on run_79 and looks smooth. **The bin is wider than the dead gap**, so it
averages each tooth together with its own gap. Re-measured on finer bins
(`analysis/flash_comb/tools/flash_time_spikiness.py`, run_79 sub-runs 0000–0002, 325 114
events, 3114 flash-anchored spills):

| bin width | CV (1–10 ms) | min/mean | empty bins |
|---|---|---|---|
| 0.5 ms | 0.420 | 0.392 | 0.0 % |
| **0.25 ms** | **0.584** | **0.019** | 0.0 % |
| **0.1 ms** | **0.874** | **0.000** | 1.1 % |

The structure: period **~1.15 ms** (autocorrelation r = 0.51), **12 starved gaps** of median
width **0.35 ms** (max 0.40 ms) covering **33 % of the 1–10 ms band**.

Figure: `analysis/flash_comb/spikiness/run_79_flashtime.png`.

**Why it matters:** on the EAR2 flight path `t[ms] = 1.41/√(E[eV])`, so `dE/E = 2·dt/t`. A
0.39 ms gap at 5 ms erases **16 %** of the energy scale there. These are not lost counts
spread thinly — they are whole neutron-energy bands with nothing in them.

## 3. The mechanism, and the two levers it implies

**⚠ CORRECTED — the "full-FIFO drain" model is wrong.** The first version of this section
said the gap was a full FIFO drained (`gap = Hwm × t_ev` = 392 µs at the production point),
which matched the 0.35–0.40 ms starved bands nicely. The inter-trigger-interval distribution
falsifies it directly. On run_79 (20 067 intervals, sub-runs 0000/0002/0007, both the 1–10 ms
and 10–40 ms bands):

```
dt mode = 195 µs,  p25 = 180 µs        stable to ±10 µs across every sub-run and band
t_ev    = n_samples × (4.83 + 0.998 × IPD) = 20 × 9.82 = 196 µs
```

The floor on trigger spacing is **one** per-event readout, not two — consistent with BUSY
asserting at occupancy ≥ Hwm and clearing at ≤ Lwm, i.e. the FEU drains just far enough to
clear the hysteresis. What survives, and it is the useful half:

- **`t_ev` is confirmed to 0.7 %**, and IPD is its only free knob. **IPD 5 → 2 predicts the
  floor drops 196 → 137 µs (−30 %)** — sharp, and settled within a single sub-run (thousands
  of intervals).
- **Hwm's mechanism is empirical, not modelled.** run_78 vs run_77 proved it does something
  large; how it aggregates BUSY into the ~1.15 ms comb period is not understood. The 0.35 ms
  starved bands are phase-coherent comb structure, closer to the DREAM delayed-cell-release
  cycle of the 2026-07-20 flash-off pulser study than to any `Hwm × t_ev` block.

Do not read the 2×2 below as if both axes were equally well modelled.

**The comb only exists above ~3 kHz offered rate.** Same run, same settings, split by band:

| band | accepted rate | starved bins | CV (0.25 ms) |
|---|---|---|---|
| 1–2 ms | 5.56 kHz | 35.0 % | 0.391 |
| 2–4 ms | 3.27 kHz | 37.5 % | 0.642 |
| 4–6 ms | 3.12 kHz | 42.5 % | 0.609 |
| 6–10 ms | 3.44 kHz | 27.5 % | 0.538 |
| **10–20 ms** | **2.68 kHz** | **2.5 %** | 0.320 |
| **20–40 ms** | **1.27 kHz** | **0.0 %** | 0.256 |

Nothing about the DAQ changes across that boundary — only the singles rate, as the neutron
flux falls. **The system sits right at the knee**, between 3.1 and 2.7 kHz. That is a
measured, in-run demonstration that dropping the offered rate clears the comb.

So there are **three** levers, and they are not equivalent:

| lever | what it does | cost | status |
|---|---|---|---|
| **IPD 5 → 2** | makes every readout 30 % faster (`t_ev` 196 → 137 µs), shortening dead time wherever it occurs | **none — no triggers given up** | modelled + `t_ev` confirmed to 0.7 %; **IPD 2 unproven on beam** |
| **Hwm 2 → 1** | changes how BUSY aggregates into the comb | triggers, mostly from the 1–2 ms spike (it gives up the derandomiser's ~1.5× readout/transmit overlap) | empirical only — run_78 vs run_77 shows it works, mechanism unknown |
| **plastic threshold ↑** | drops the offered rate below the ~3 kHz knee so BUSY stops engaging — **removes** dead time rather than redistributing it | triggers everywhere | grounded in the band table above |

IPD is the only one of the three that costs nothing, which is why it belongs in the first
test rather than a later one. run_82 (§4) takes IPD and Hwm together as a 2×2; the threshold
ladder (§4c) stays the fallback.

## 4. run_82 — the 40-minute 2×2 that settles both levers

`run_configs/run_config_hwm_ipd_2x2.py` → `run_config_hwm_ipd_2x2.json`.
**Supersedes** `run_config_hwm_spikiness.py` (Hwm only), left in place unused.

| point | Hwm/Lwm | IPD | t_ev = predicted dt floor | 1-event ceiling | isolates |
|---|---|---|---|---|---|
| **A** | 2 / 1 | 5 | 196 µs | 5.09 kHz | **baseline = production** |
| **B** | 1 / 0 | 2 | 137 µs | 7.32 kHz | both levers |
| **C** | 2 / 1 | 2 | 137 µs | 7.32 kHz | IPD alone |
| **D** | 1 / 0 | 5 | 196 µs | 5.09 kHz | Hwm alone |

A/D and B/C share a predicted floor **on purpose**. If the measured floor tracks IPD and
ignores Hwm, the corrected readout model holds and IPD is the knob that actually shortens
readout; whatever Hwm then does to the comb it does by another route, which A-vs-D and
C-vs-B isolate at fixed readout speed.

**Order `A B C D | D C B A`** — a palindrome, so every setting has mean position 4.5 and a
linear beam-intensity ramp cancels exactly. This matters today: comb depth is driven by the
offered singles rate, which tracks beam intensity, and the beam is spotty. The generator
*asserts* the balance and refuses to write an unbalanced `ORDER`.

**8 × 4.4 min + 27 s/sub-run overhead = 38.8 min, ~11 GB.** The 27 s is measured across
run_79's 16 sub-run boundaries, not assumed. At the live rate (16.2 flashes/min, measured on
run_81 today) each point gets ~71 flashes, ~143 pooled over the two cycles.

**Truncation is graceful by construction:**

| stopped after | you have |
|---|---|
| 2 points (~10 min) | baseline vs both-levers → "does it flatten at all?", **plus the IPD-2 integrity check** |
| 4 points (~19 min) | the complete 2×2 — the answer, single exposure |
| 8 points (~39 min) | doubled statistics **and** drift-cancelled |

### ⚠ IPD 2 has never been run on beam — points B and C are unproven

The 2026-07-22 post-10 GbE ladder reached IPD 5 (0.026 % eventId gaps, clean) and never found
the corruption threshold going lower. "Never found" is not "measured". An IPD-2 point runs
**second**, ~6 min in, precisely so the integrity check happens early and the rest can be
abandoned if it fails:

```
/home/mx17/ana/.venv/bin/python \
    ~/beam_july/analysis/flash_comb/tools/flash_time_spikiness.py \
    --run run_82 --group-by-setting --tmax 40
```

**`gapId% > 0.1` on an IPD-2 point = corruption → that point is void, keep IPD 5.** Also
watch `nFEU` (must stay 8) and `minFEU%` (must not fall on the IPD-2 points relative to the
IPD-5 ones). This check is not optional: **dropping events flattens a comb beautifully**, so
a corrupt point is exactly what a "win" looks like on the CV.

### Spotty beam — three independent defences

1. **Every metric is per-flash-anchored-spill.** A beam gap costs statistics but does not
   bias the shape: a short point is a *noisy* point, not a wrong one.
2. **`beam_gate.py`** drives `.pause_run` from `config/beam_state.json`, so the run will not
   *start* a point into a beam gap — daq_control checks that flag at every sub-run boundary
   (`daq_control.py:264`). It cannot rescue a gap that opens mid-sub-run; that point just
   gets fewer flashes, which is why defence 1 exists.
   - **Co-operative:** it pauses only if `.pause_run` is free, releases only a hold recorded
     in its own `.beam_gate_hold` sidecar, and releases on exit — so it can never stamp on an
     operator pause or on daq_control's own n1081b-apply hold, and a Ctrl-C cannot pin the run.
   - **UNKNOWN is not OFF:** `beam_on: null` or a stale `beam_state.json` (monitor down) makes
     it hold its current state rather than pause on a monitoring glitch.
   - Verified against a synthetic `beam_state.json` in a scratch dir: pause/release, refusing
     to claim someone else's hold, refusing to release it, UNKNOWN on null / stale / missing /
     corrupt, and adoption of a sidecar left by a crashed instance. All pass.
3. **`RESUME=1`** rewrites the config to skip every sub-run that already has a
   `.subrun_complete` marker, so a run killed by a long beam stop is picked up, not re-taken.

Backstop already in daq_control: it aborts after `MAX_EMPTY_SUBRUNS=2` consecutive zero-byte
sub-runs, so a beam stop outlasting the gate cannot burn the schedule.

⚠ **Judge every point by its flash count, not its wall-clock.** The analysis prints `flash`
per setting; a point far below its partner was under-exposed and should be re-taken via
RESUME rather than compared.

## 4b. Runbook

```bash
# 0. pre-flight (beam ON — daq_control has no beam-gating of its own)
cat config/beam_state.json                                  # beam_on: true, recent pulse
.venv/bin/python n1081b/trigger_mode.py status              # C or_veto [0], D [0,1]
.venv/bin/python n1081b/set_ps_trigger_delay.py --show      # delay 1440
.venv/bin/python beam_gate.py --status                      # what would the gate do now?

# 1. stop run_81, then generate + launch
.venv/bin/python run_configs/run_config_hwm_ipd_2x2.py
.venv/bin/python beam_gate.py &                             # leave running for the whole run
./start_run.sh run_config_hwm_ipd_2x2.json

# 2. ~6 min in, on sub-run 0001 (Hwm 1 / IPD 2) — the two things that void the run
grep -H -E "Main_Trig_OvrWrn|InterPacket" ~/july_dream/dream_run/run_82/*/Tcm_Mx17_July.cfg
.venv/bin/python dream_scripts/feu_trig_counters.py         # Hwm 1 / Lwm 0 on all 8
/home/mx17/ana/.venv/bin/python \
    ~/beam_july/analysis/flash_comb/tools/flash_time_spikiness.py \
    --run run_82 --group-by-setting --tmax 40                # gapId% must stay < 0.1

# 3. at the end
/home/mx17/ana/.venv/bin/python \
    ~/beam_july/analysis/flash_comb/tools/flash_time_spikiness.py \
    --run run_82 --group-by-setting --tmax 40 --out <dir> --label run_82
/home/mx17/ana/.venv/bin/python \
    ~/beam_july/analysis/flash_comb/tools/plot_flash_time_spikiness.py \
    <dir>/run_82_Hwm1_IPD2_flashtime.npz

# if the beam killed it part way:
RESUME=1 .venv/bin/python run_configs/run_config_hwm_ipd_2x2.py
./start_run.sh run_config_hwm_ipd_2x2_resume.json
```

**Back to production afterwards:** nothing on the boards was touched (all eight sub-runs
carry the same `stat090` tag), so it is one command —
`RUN_NUM=84 .venv/bin/python run_configs/run_config_stats_optimized.py`, optionally with
`OVR_WRN_HWM=`/`IPD=` set to whatever run_82 concludes.

## 4c. run_83 — plastic threshold, still the fallback

`run_configs/run_config_plastic_thresh_spikiness.py`, unchanged and ready:
**0.90 → 1.13 → 1.41 → 2.00 → 0.90 MIP**, 12 min each, ~63 min. Opens *and* closes on the
production point as a beam-drift control. New scan tags `thr090/thr113/thr141/thr200` are in
`config/n1081b_scan_schedule.json` (additive; `stat090` byte-identical; backup at
`.bak_20260727_pre_thr_spikiness`), with mV identical to the run_67/run_70 `m###On` tags and
`mesh_ac` held OFF in every one. Set `HWM=`/`IPD=` to run_82's winner so the levers compose.
⚠ scan_control does not restore section thresholds on exit — re-apply 0.90 MIP by hand after.

It is **not** folded into run_82: it does not fit in 40 minutes, and it is only needed if the
two DAQ levers come up short. Its rationale is §3's rate knee, which is unaffected by the
model correction above.

## 4d. RESULTS — run_82, 2026-07-27 13:19–13:58 (COMPLETE, all 8 points)

All eight sub-runs ran and are marked complete. The beam died at ~13:55, during the last
sub-run (`h2i5_0007`, the baseline repeat), which therefore got ~1.6 of its 4.4 min — so the
baseline pools 71 flashes against ~105 for the other three. Its cycle-1-only numbers
(27.8 % starved) agree with the pooled value (26.7 %), so nothing rests on the short point.
`beam_gate.py` detected the drop within 10 s and held `.pause_run`; there was no ninth
sub-run to gate, and it released cleanly on exit.

**Integrity: all 8 sub-runs 0.0000 % eventId loss, 8/8 FEUs, zero spread.**
**IPD 2 is proven safe on beam** — the first time IPD < 5 has ever been measured there.

| point | Hwm | IPD | flashes | **starved** | CV@0.5 | CV@0.25 | CV@0.1 | corrected | trig/flash | ev/flash | Δt mode | Δt p25 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A | 2 | 5 | 71 | 26.7 % | 0.41 | 0.59 | 0.85 | 0.83 | 32.46 | 101.4 | 195 µs | 180 |
| C | 2 | **2** | 106 | **41.1 %** | 0.66 | 0.81 | 0.94 | 0.93 | 33.30 | 101.0 | 135 µs | 128 |
| B | **1** | **2** | 104 | 6.7 % | 0.34 | 0.45 | 0.55 | 0.52 | 31.39 | 90.7 | 155 µs | 185 |
| **D** | **1** | 5 | 105 | **3.3 %** | **0.16** | **0.22** | **0.41** | **0.37** | 30.36 | 89.2 | 215 µs | 241 |

Figure: `analysis/flash_comb/spikiness/run_82_hwm_ipd_2x2.png`.
Data: `run_82_bysetting.json` + per-setting `.npz` in the same directory.

### The answer: Hwm 1 / Lwm 0, IPD left at 5

**Starved bins 26.7 % → 3.3 %, an 8.1× improvement**, and CV falls at every bin width —
including the 0.5 ms width the original metric used (0.41 → 0.16). For reference, run_79's
comb-free 20–40 ms band read CV 0.26 at 0.25 ms; point D reads **0.22**, i.e. the 1–10 ms
IPC window is now *as flat as the late gate already was*. That was the stated win condition.

**Cost, and it lands in the right place:**

```
1-10 ms (the IPC window)   32.46 -> 30.36 trig/flash    -6.5%
whole gate                101.4  -> 89.2  ev/flash     -12.0%
```

Hwm 1 gives up nearly twice as much *outside* the IPC window as inside it — the triggers it
sheds are preferentially ones we did not want. (At these statistics the −6.5 % is ~2.3σ; the
evenness result is far outside any noise.)

### ⚠ IPD 2: readout model CONFIRMED, but it DEEPENS the comb. §3/§5 had this backwards.

Both sections above argue IPD is "strictly the better lever" because it shortens dead time
for free. **The readout half is exactly right and the evenness half is wrong.**

Readout model, now validated at two IPD values and two watermarks:

| point | predicted t_ev | measured Δt mode | |
|---|---|---|---|
| Hwm2 / IPD5 | 196 µs | 195 µs | −1 % |
| Hwm2 / IPD2 | 137 µs | 135 µs | −1 % |
| Hwm1 / IPD5 | 196 µs | 215 µs | +9 % |
| Hwm1 / IPD2 | 137 µs | 155 µs | +14 % |

At Hwm 2 `t_ev = n_samples × (4.83 + 0.998 × IPD)` is exact. At Hwm 1 the measured floor sits
~10 % high — the FEU serialises strictly (BUSY on every single event), adding a per-event
handshake the model does not carry. Δt p25 rising 180 → 241 µs is the signature of Hwm 1
working as intended.

And yet:

```
starved bins:   A (Hwm2/IPD5) 26.7 %  ->  C (Hwm2/IPD2) 41.1 %      WORSE
                D (Hwm1/IPD5)  3.3 %  ->  B (Hwm1/IPD2)  6.7 %      WORSE
```

**IPD 2 makes the comb worse at both watermarks**, consistently, despite being loss-free and
even gaining triggers (+2.6 % at Hwm 2). Faster readout sharpens the burst/gap structure
rather than smoothing it — consistent with §3's picture of the comb as phase-coherent
aggregation that IPD rescales rather than removes.

⇒ **Do not adopt IPD 2 for evenness.** It stays available as a real, free throughput gain if
total yield ever becomes the objective, but it is not the comb lever. Retract the §5
recommendation.

### Follow-up

`config/json_run_configs/run_config_stats_optimized_83.json` is generated and ready —
production at Hwm 1 / Lwm 0, everything else unchanged (latency 27, n20, IPD 5, RAW, same HV
and thresholds). No board work needed: run_82 held the `stat090` tag throughout and never
touched the N1081B. Launch with `./bash_scripts/start_run.sh run_config_stats_optimized_83.json`
once beam returns.

The plastic-threshold ladder (§4c) is **no longer needed** — it was the fallback for exactly
the case where the watermark failed to flatten the comb, and it did not fail. Its generator
default moved to `RUN_NUM=84` so it cannot collide with run_83.

## 5. ⚠ SUPERSEDED — a third lever, inter-packet delay

> **Retracted by §4d.** IPD 2 does shorten readout exactly as modelled and loses no events,
> but it makes the comb WORSE at both watermarks (26.7→41.1 % and 3.3→6.7 % starved). The
> "strictly better lever" claim below is wrong. Kept for the reasoning trail only.

The readout ceiling is `1000 / (n_samples × (4.83 + 0.998 × IPD))` kHz. **IPD 5 → 2 moves it
from 5.09 to 7.32 kHz (+44 %) and shortens every dead gap in the same proportion, without
giving up a single trigger** — Hwm only redistributes triggers, and threshold discards them.
The 2026-07-22 post-10 GbE ladder measured IPD 5 at 0.026 % corrupt gaps and never found the
corruption threshold going down, so there is headroom; but **IPD 2 has never been measured on
beam.** Both generators take `IPD=` if you want it as an extra axis. Worth doing.

## 6. Verify before trusting any result

1. **Per-sub-run watermark reached the cfg** — these overrides have been silently dropped
   before by a stale `dream_daq` server:
   `grep -H "Main_Trig_OvrWrn" ~/july_dream/dream_run/run_82/*/Tcm_Mx17_July.cfg`
2. **Hardware read-back**, live, during a Hwm 1 sub-run: `feu_trig_counters.py` → Hwm 1 / Lwm 0.
   (RunCtrl's clamp is downward-only and its cap here is `(512−27)//20 = 24 → Hwm 20`, so both
   rungs pass through untouched.)
3. **Analyse both runs with the same tool and the same bins.** Read CV at **0.25 ms and
   0.1 ms**, the starved-bin fraction, and trig/flash in 1–10 ms. The 0.5 ms number is the one
   that hid this in the first place — do not report it alone.

```
/home/mx17/ana/.venv/bin/python \
    ~/beam_july/analysis/flash_comb/tools/flash_time_spikiness.py \
    --run run_82 --tmax 40 --fine 0.05 --out <dir> --label run_82
/home/mx17/ana/.venv/bin/python \
    ~/beam_july/analysis/flash_comb/tools/plot_flash_time_spikiness.py <dir>/run_82_flashtime.npz
```

**Win condition:** the 1–10 ms band starts to look like 20–40 ms already does — starved
fraction toward 0 — at a trigger cost worth paying. Note `flash_time_spikiness.py` reads
`combined_hits_root` (1.2 GB/sub-run), not `decoded_root` (12 GB), and needs
`/home/mx17/ana/.venv/bin/python` for uproot.

**Related:** `docs/METHOD_readout_window_optimization.md` (§5 is the watermark section this
supersedes on the evenness metric), `docs/FEU_WATERMARKS_2026-07-22.md`,
`docs/DREAM_flash_comb_study_2026-07-19.md`.
