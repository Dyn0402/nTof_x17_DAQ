# Live ZS beam run — source studies index (2026-07-19)

Goal: set up a **live-beam DAQ run** that (a) turns on **zero suppression (ZS)**,
(b) keeps **n_samples = 32** but **adjusts trigger latency** per the drift-window
study, and (c) sets **scintillator trigger thresholds** from the Y88 calibration +
Geant4 energy-deposition. This doc is the single place another model can pick up to
synthesize all five source studies fast. Every claim carries a `file:line` (or
`commit`) citation so it can be verified.

Where each knob lives in this repo (`run_config_beam.py` → `dream_daq_info`, applied by
`dream_daq_control.py:make_config_from_template` at `dream_daq_control.py:545-609`):

| Knob | run-config key | DREAM register | current value |
|---|---|---|---|
| ZS on/off | `zero_suppress` | `Sys DaqRun Mode`=ZS / `Feu_RunCtrl_ZS` | **False** |
| Common-mode sub | `common_noise_subtraction` | `Feu_RunCtrl_CM` | *unset* → **0** ⚠ |
| Pedestal sub | `pedestal_subtraction` | `Feu_RunCtrl_Pd` | *unset* |
| ZS type | `zs_type` | `Feu_RunCtrl_ZsTyp` | *unset* (want TPC=1) |
| Extra samples past crossing | `zs_check_sample` | `Feu_RunCtrl_ZsChkSmp` (0–4) | 4 |
| Trigger latency | `latency` | `Feu * Dream * 12` | 5 (flash_random) |
| Samples/waveform | `n_samples_per_waveform` | `Sys NbOfSamples` | 32 |
| Sample period | `sample_period` | DrmClk divs | 60 ns |

The **k·σ ZS threshold itself is NOT a run-config field** — it is baked into the
`_thr.prg` produced by a **pedestal-threshold run** (`do_pedestal_threshold_run`),
where `Sys PedRun Threshold = N` writes `thr_ch = ped_ch + N·σ_ch`. `σ_ch` is measured
*after* ped+CM subtraction. To get 3.5σ from a `Threshold 5` prg, rescale the threshold
memory ×(3.5/5). Scint trigger thresholds are a **separate hardware layer** — N1081B
discriminators in mV on the logic boards (M1 walls, M2 plastics), NOT DREAM.

---

## Source 1 — On-detector ZS + IPD/network safety harness (LIVE, includes beam-on)
`~/beam_july/test/zs_rate_scan/` (2026-07-15) and `~/beam_july/test/zs_ipd_safety/` (2026-07-18)

- **ZS pipeline** = pedestal-sub → common-mode (CMN)-sub → TPC-mode ZS; threshold
  compared to the *processed* sample `raw − ped − CMN + CmOffset`.
  `zs_rate_scan/ZS_SETTINGS.md:9-17,32-42`. Held-fixed config: `ZS=1, Pd=1, CM=1,
  ZsTyp=1(TPC), ZsChkSmp=4, CmOffset=256`.
- **k·σ ladder** (gated, ~620 Hz in-burst): event size cliff at ~4.5σ —
  k25=1.17, k12=1.40, k8=1.80, k6=2.17, **k5=4.56, k4=38.5 (=Raw)** kB/event/FEU.
  Below ~4.5σ the threshold drops under the noise floor → every channel floods →
  full-Raw event size. `zs_rate_scan/gated_ladder_summary.csv:2-9`,
  `FINDINGS_2026-07-15.md:42-58`. Event *count*/burst is identical k5↔k25.
- **07-15 conclusion: optimal ≈ 5σ** at the single reachable rate (`FINDINGS_2026-07-15.md:9,49-61`).
- **07-18 beam-on Phase 8 supersedes k5 for a live run**: the γ-flash makes the first
  events of every window full-size regardless of ZS, so at **k5 the 1 GbE wire wall
  moves inside the window** even at IPD=10 → 64 ev/window, **20.7 % in-window loss**.
  Raising to **k8** shrinks flash-era events ~3× and removes it.
  **DELIVERABLE: k8 + IPD=2 → 165 ev / 30 ms window (max 197), 0.9 % tagged in-window
  loss.** `FINDINGS_2026-07-18.md:186-205`. Baseline for reference: k5 + IPD=100 =
  18.7 ev/window, 0 % loss (`:188`).
- **Deadtime law** ≈ `1 µs × IPD × (NbOfSamples/32)` (`FINDINGS_2026-07-18.md:10`).
  IPD→sustained ceiling: 100→312 Hz, 10→3.07 kHz, 2→7.21 kHz (`:14-20`,
  `zs_rate_scan/ipd_scan.csv`). n_samples scan: 8→1000, 16→624, 32→312, 64→156 Hz
  (`zs_rate_scan/nsamples_scan.csv`).
- **Hard constraints for a run NOW**:
  1. **Write to the SSD path `~/july_dream`, not the HDD `/mnt/data`** — all prior
     host-side stall losses were the HDD writeback path; on SSD they vanish
     (`FINDINGS_2026-07-18.md:146-167`). *(Current config already writes DREAM to
     `/home/mx17/july_dream/dream_run/…` — good; see run_config_beam.py:221.)*
  2. **rmem tuning is live but not persistent** (`net.core.rmem_max` 4→128 MB reverts
     on reboot). Persist via `zs_ipd_safety/apply_daq_net_tuning.sh` (insurance-tier,
     not required on the SSD path). `FINDINGS_2026-07-18.md:102-106,229-230`.
  3. **Keep tracer channels (0,224,511/FEU) at threshold 0** as a per-event integrity
     watermark (`FINDINGS_2026-07-18.md:53-57`).
  4. **M4.C routing gotcha** — `daq_control` board-apply does NOT reset M4.C; after any
     pulser routing, restore it or the run triggers on the pulser (`:226-228`).
  5. **Do not touch M3/.242 (stress-wedged) or .244 (tt watcher)** (`FINDINGS_2026-07-15.md:132-136`, `PLAN.md:66`).
- **Monday 10 GbE upgrade is an enhancement, NOT a prerequisite** — k8+IPD=2 runs now on
  1 GbE; the upgrade only unlocks full-k5 quality (~220 ev/window). `HANDOFF_network_upgrade_monday.md:1-6`.

## Source 2 — ZS hit-loss / CM optimization (OFFLINE sim on run_55 no-ZS waveforms)
`~/PycharmProjects/nTof_x17/mx_july_beam_qa/` — `ZS_OPTIMIZATION_RUN55.md`,
`26_zs_sim_extract.py`, `26b_zs_analysis.py`, `calib/26_zs_summary.json`, `HANDOFF_RUN55_HV_ZS.md`.
Commit `927e8ca`.

- **Strip survival % vs N·σ (CM ON, per-Dream)** — `calib/26_zs_summary.json:33-122`:

  | N[σ] | A | B | C | D |
  |---|---|---|---|---|
  | 3.0 | 99.81 | 94.15 | 94.28 | 97.24 |
  | **3.5** | **99.71** | **92.38** | **93.61** | **96.78** |
  | 5.0 | 99.02 | 88.83 | 92.28 | 95.34 |

  Det A ~lossless to 5σ; B/C/D loss is **threshold-independent CM signal-bias in busy
  windows**, not the threshold — going below 3σ buys ~nothing and doubles volume
  (`ZS_OPTIMIZATION_RUN55.md:70-86`).
- **CM correction is MANDATORY** (`Feu_RunCtrl_CM=1`, currently **0** in the deployed
  config — the single change that must be made). In beam, per-channel baseline wander is
  **10–20× beam-off σ** and is **>99% coherent within each Dream chip (64 ch)**; a
  per-Dream per-sample median subtraction restores residual to ≈ beam-off σ (4.1 ADC).
  **Per-FEU (512 ch) CM is NOT enough** (36 ADC residual). `ZS_OPTIMIZATION_RUN55.md:26-42`,
  `HANDOFF_RUN55_HV_ZS.md:74-76`.
- **Recommended: `Sys PedRun Threshold = 3.5σ`, CM=1, Pd=1, ZsTyp=1(TPC), ZsChkSmp=4,
  CmOffset=256** (`ZS_OPTIMIZATION_RUN55.md:88-93`). Volume at 3.5σ ≈ 9.4 % samples,
  ~0.20 ms/event (~10× vs no-ZS).
- **OPEN QUESTION — does the FEU firmware CM run per-Dream-chip or per-FEU?** The 3.5σ
  recommendation *assumes per-Dream*. If firmware CM is only per-FEU, the live noise
  floor stays high (matching Source 1's live 4.5σ flood cliff) and 3.5σ is not
  achievable live. Flagged open in `ZS_OPTIMIZATION_RUN55.md:99-101`.

### Reconciling Source 1 (k8) vs Source 2 (3.5σ) — THE key ZS decision
They answer different questions and are **both right in their domain**:
- Source 2 (per-strip charge retention, offline, ideal per-Dream CM) → 3.5σ keeps the
  most real charge.
- Source 1 (live event size vs the 1 GbE wire wall under γ-flash) → 3.5σ makes events
  large; under the flash the wire floods → must run **k8** to stay DAQ-safe on 1 GbE.
- Recommended path: **first live ZS test at CM=1 + k8 + IPD=2 (DAQ-safe, beam-validated),
  measure live event sizes / in-window loss, confirm the firmware CM granularity, THEN
  step k down toward 3.5σ** if the CM-cleaned floor holds. Do not open at 3.5σ blind.

## Source 3 — Drift-window latency/n_samples study
`~/PycharmProjects/nTof_x17/mx_july_beam_qa/` — `DRIFT_WINDOW_ANALYSIS.md`,
`DRIFT_WINDOW_HANDOFF.md`. Commits `097c936` (superseded) → `d1eb0fd` (final).

- **For KEEPING n_samples = 32: set latency 35 → 34** ("conservative" row): 0.0 % of
  drifting primaries lost, ~0.02 % mean charge loss, full baseline-return on the deepest
  ~1 % of tracks. `DRIFT_WINDOW_ANALYSIS.md:121-123,140-145`.
- **Do NOT stay at latency 35 / n=32**: the deepest ~1 % of primaries clip their charge
  tail at the sample-31 ceiling. Latency 34 pulls the fall inside the window.
- The commit headline **"latency 32 / n=28" is superseded** (peak-sized, ignored the
  +7-sample fall). Final target ladder is latency 35→32→30 *paired with* n 32→29→24; the
  "latency 32" value is coupled to trimming n. **With n fixed at 32, the matched latency
  is 34, not 32** (`:30-31,18-19,142`).
- Sample period 60 ns → n=32 = 1.92 µs window. Prompt (near-mesh) peak ≈ latency − 26
  (`:60`; supersedes the handoff's −24). 90/10 Magboltz drift table
  `DRIFT_WINDOW_HANDOFF.md:81-96`; full-gap drift ~11–15 samples across dets.
- Secondary (not forced by n=32): stage **det A drift 600 → 700 V (via 650)** for
  wet-gas immunity (`:168-178`); B/C/D stay 800 V.
- ⚠ **Applies to a TRACKING/physics trigger** (where latency frames the drifting track),
  e.g. scint-doubles. It is NOT the flash_random latency=5 of the current run_57, whose
  random-pulser events read flat pedestal (latency irrelevant). Confirm the trigger mode
  of the live ZS run before applying latency 34.

## Source 4 — Y88 scintillator calibration
`~/PycharmProjects/nTof_x17/mx_july_beam_qa/` — `HANDOFF_Y88_SCAN.md`, `21`–`24_y88_*.py`,
`calib/y88_energy_calib.json`, `calib/adc_to_mv_run2244*.json`, `TRIGGER_THRESHOLDS.md`.
Commit `7f4f0fc`. Runs 224476–79.

- **Energy scale (mV per MeVee), through-origin slope** — `calib/y88_energy_calib.json`:
  - **Liquids (LIQ), operating HV → usable:** 31.7–37.2 mV/MeVee.
  - **SiPM walls (WAL), operating HV → usable:** ~28–56 mV/MeVee (WALD7 outlier 56, treat
    with caution). Source-facing channels only.
  - **Plastics (PSS): 24–39 mV/MeVee but at RAISED HV — NOT directly usable.** ⚠
- **Compton edges** used: 698.63 & 1612.06 keVee. Stored in mV. ADC→mV ≈ **0.0306 mV/ADC**
  (`calib/adc_to_mv_run224476.json`).
- **⚠ Plastic threshold blocker**: PSS calib is at a raised HV not recorded in DAQsettings.
  Converting to nominal-HV mV needs (a) the exact 224476–79 plastic HV and (b) the per-PMT
  power-law index in `calib/pss_mip_calib_run224489.json`. Until then **no valid nominal-HV
  plastic mV threshold** can be produced from Y88. Also: at nominal HV the plastic MIP MPV
  sits **below the ~4.9 mV trigger threshold → the plastics must be equalized / re-biased
  first** (`README.md:110,158-161`). LIQ/WAL are ready now.
- **Existing wall threshold rec** (separate beam-MIP analysis, run 224460, NOT Y88):
  **WALA 12, WALB 14, WALC 12, WALD 14 mV** on the top+bottom analog SUM
  (`TRIGGER_THRESHOLDS.md:39-42`). Compare to the **standing post-FIFO walls +15/+16/+15/+16 mV**.
- **MeV→mV recipe**: Geant4 E_dep(MeV) → MeVee (Birks quench; e±/MIP ≈ 1:1) →
  ×`mv_per_mevee` → mV; walls need the single-bar→top+bottom-sum mapping (~2× a single end)
  and the FIFO 2× fan-out factor before comparing to a board discriminator. Hardware floor
  **|10| mV**.

## Source 5 — Geant4 energy-deposition / trigger optimization
`~/CLionProjects/MX17_Full_Geant/` — `CAMPAIGN_STATUS.md`, `HANDOFF_THERMAL_TRIGGER.md`,
`analysis/trigger_thermal/trigger_scan.json`, `scripts/analyze_trigger_thermal.py`.
Commits `134e3e5`, `49ba787`, `28cfae6`, `cefe30f`.

- **⚠ CANNOT compile on this DAQ box** — no `/cvmfs`, no Geant4; the sim builds only on
  **lxplus** (`scripts/setup_lxplus.sh` sources G4 11.2 from CVMFS, then `scripts/build.sh`).
  The **energy-dep OUTPUTS are committed and directly usable** (`analysis/trigger_thermal/`);
  a fresh lxplus run is only needed for the not-yet-done µs-window (MeV-ROI) scan.
- **MIP energy scale (master calibration)** — `trigger_scan.json:2-6`:
  **SiPM bar 1 MIP = 458 keV; plastic paddle 1 MIP = 4.33 MeV; liquid 1 MIP = 6.12 MeV.**
  Trigger observables are **per-channel maxima** (max single SiPM bar / max plastic bar),
  not sums.
- **Signal vs background**: signal (X17/IPC e± pair legs) makes a real **MIP peak** in both
  SiPM and plastic; thermal-capture-γ background is **Compton-only** (no SiPM MIP peak) and
  dies below the ²⁸Al 7.72 MeV Compton edge in plastic. That gap is what the threshold uses
  (`CAMPAIGN_STATUS.md:32-34`).
- **Recommended thresholds** (`CAMPAIGN_STATUS.md:35-41`, `HANDOFF_THERMAL_TRIGGER.md:12-25`):
  **SiPM wall bars ≥ 0.5 MIP ≈ 229 keV; plastic paddles ≥ 1.0–1.2 MIP ≈ 4.3–5.2 MeV.**
  Do NOT set plastic ≥ 1.5 MIP (kills the crossing-leg signal). S/N-optimized plastic knob:
  ~2.6–3.0 MeV (0.6–0.7 MIP).
- **Topology**: 2 SiPM legs ≥ 0.5 MIP (one/arm) + ≥ 1 plastic bar ≥ 1.0–1.2 MIP confirm
  ("leg2_confirm1"). Efficiency ceiling of this topology ~10.8 %.
- **Measured DAQ ceiling** ~200 singles / 30 ms window (from the same 07-18 DREAM scan as
  Source 1) — the operator's swallow budget (`HANDOFF_THERMAL_TRIGGER.md:59-73`).
- ⚠ These thresholds are validated in the **>1 ms thermal gate** (a background/veto menu);
  for live *signal* they must be re-scanned in the µs MeV window (`CAMPAIGN_STATUS.md:42-45`).

### Cross-check: Geant4 ↔ Y88 ↔ standing config, for the walls
Geant4 0.5-MIP SiPM = 229 keVee. At Y88 ~37 mV/MeVee that is ~8.5 mV per single end, ~17 mV
on the top+bottom sum — consistent with the beam-MIP rec (12–14 mV) and the standing walls
(15/16 mV). So the **walls are already ≈ correctly set**; the actionable threshold work is
**plastics, which are blocked** (raised-HV Y88 + sub-floor nominal-HV MPV → need HV
equalization). Liquids are calibrated but not in the trigger.

---

## Compile / reproduce status (per the "make sure you can compile all of these" ask)
- **Sources 1–4 are Python analyses** — they *run* (given the `mx_july_beam_qa` venv + the
  run_55/224xxx data), they don't compile. Their key outputs (JSON/CSV/figures) are already
  committed and were read directly for this doc.
- **Source 5 (Geant4) is the only true build, and it CANNOT be built here** (no CVMFS/Geant4).
  It is an lxplus target; committed outputs are used instead. Open question for the operator:
  attempt a remote lxplus build, or just consume the committed energy-dep scan? (The latter
  is sufficient for setting thresholds.)

## Open decisions to confirm before building the run config
1. **Trigger mode of the live ZS run** — flash_random (latency 5, current run_57) or a
   tracking/physics trigger (scint-doubles) where latency 34 applies? ZS-on-tracks needs
   real MM hits → a physics trigger.
2. **ZS operating point** — open at DAQ-safe **CM=1 + k8 + IPD=2 (SSD path)** and step
   toward 3.5σ, vs open at 3.5σ. (Recommend the former.)
3. **Thresholds** — leave walls at the standing 15/16 mV (already ≈ Geant4 0.5 MIP), and
   defer plastics (blocked)? Or invest in the plastic HV-equalization + nominal-HV calib
   first?
4. **Live-state** — run_57 is taking data now and a `rate_scan_2d` process holds board
   access (.240/.241/.244). Stopping run_57 / reclaiming the boards needs operator go-ahead.

---

## Build wiring — how to turn the decisions into an actual run (2026-07-19 decisions)
Decisions locked: **trigger = scint-doubles OR PS-pickup (keep D1 PS leg)**; **ZS opens at
k8 + IPD=2, then step down**; **keep n=32, latency 35→34**; **thresholds = operator working
(re-ask before launch)**.

### Trigger (N1081B, static board setup — needs board access, currently held by rate_scan_2d)
This is the **run_56 mode** (scint-DOUBLES OR PS-pickup, flash/PS co-framed):
```
.venv/bin/python n1081b/trigger_mode.py scint --doubles --ps-pickup
.venv/bin/python n1081b/set_ps_trigger_delay.py --delay 1800        # re-enable G&D (run_57 disabled it)
.venv/bin/python n1081b/trigger_mode.py status                      # expect C=or_veto[1], D=[0,1] -> "scint(doubles)+ps"
.venv/bin/python n1081b/set_ps_trigger_delay.py --show              # expect enable_gd, delay 1800
```
- Cabling (`n1081b/trigger_mode.py:8-9,25-29`): M4.C or_veto lemo1 = Doubles; `--ps-pickup`
  ORs the PS/flash line (M4.D lemo0) so the run fires on **Doubles OR PS pickup**. The PS leg
  carries its own **1800 ns G&D delay** to co-frame the flash next to the doubles MM pulse.
- ⚠ **Latency-34 vs co-framing recheck**: the 1800 ns delay + "flash @ smp 13 / doubles MM @
  smp 11" were measured at **latency 35, 32 smp** (2026-07-19). Moving to latency 34 shifts the
  window 1 sample earlier (60 ns); everything stays in-window but re-verify against the recent
  `~/beam_july/test/ps_flash_framing/` test (`ps_lat35_32_delayed/`) before trusting the frame.

### ZS threshold (k8) — the production-path gap
- Template default `Sys PedRun Threshold = 5` (σ) → a pedestal-threshold run writes
  `*_thr.prg` = **5σ per channel** (`Tcm_Mx17_July.cfg:47`). A k-σ set = per-channel
  ×(k/5); k8 = ×1.6 (`~/beam_july/test/zs_rate_scan/gen_zs_ladder.py` header).
- **Two production options for k8**:
  1. Do a fresh **pedestal-threshold run with `Sys PedRun Threshold = 8`** (edit the pedestals
     config / template) at the actual run config, then point the ZS run's `pedestals` at it —
     **iff** `dream_daq_control.get_pedestals` stages `*_thr.prg` + activates the
     `Feu N Feu_RunCtrl_ZsFile` lines. **VERIFY this** — currently that staging lives only in
     the test harness `run_zs_test.py:stage_zs_files` (`:92-114`), NOT in `dream_daq_control.py`.
  2. If it doesn't, port `stage_zs_files` into `dream_daq_control` (stage rescaled `_thr.prg`
     + write `ZsFile` lines) — the real code change needed for production ZS.
- Fresh pedestals recommended over reusing the July `*_thr.prg` (noise σ is condition-dependent).

### DREAM run-config (`run_config_beam.py` → `dream_daq_info`) deltas from run_57
```
'zero_suppress':            True,      # Sys DaqRun Mode ZS / Feu_RunCtrl_ZS
'pedestal_subtraction':     True,      # Feu_RunCtrl_Pd
'common_noise_subtraction': True,      # Feu_RunCtrl_CM  <-- MANDATORY (was 0/unset)
'zs_type':                  'tpc',     # Feu_RunCtrl_ZsTyp = 1  (confirm _to_zs_typ mapping)
'zs_check_sample':          4,         # already 4
'n_samples_per_waveform':   32,        # keep
'latency':                  34,        # was 5 (flash_random); 34 = drift-window keep-n=32 rec
# 'inter_packet_delay':     2,         # NOT PLUMBED YET -> see below
```
- **IPD is not a run-config field.** It is `Feu * Feu_InterPacket_Delay 100` in the cfg template
  (`Tcm_Mx17_July.cfg:247`). To get IPD=2, either add an `inter_packet_delay` key +
  `updates["Feu * Feu_InterPacket_Delay"]` line in `make_config_from_template`
  (`dream_daq_control.py:563-604`, clean), or edit the template (affects all runs — IPD=2 is
  ZS-only-safe, Raw needs ≥75, so a run-config override is the right design).
- **Write path is already the SSD** (`run_config_beam.py:221` → `/home/mx17/july_dream/…`) ✔.
- Keep **tracer channels (0,224,511/FEU) at threshold 0** in the staged `_thr.prg`.

### Pre-launch checklist
1. Operator: finish scint thresholds (walls ≈ standing 15/16 mV; plastics blocked) — **re-ask**.
2. Stop run_57; free the boards (rate_scan_2d holds .240/.241/.244).
3. Apply the scint-doubles+PS trigger (commands above) + verify status.
4. Fresh pedestal-threshold run at 8σ (or generate/stage k8 `_thr.prg`); wire ZsFile lines.
5. Plumb IPD=2 (run-config field or template edit) + confirm rmem tuning is live (SSD path
   makes it insurance-tier).
6. Regenerate `run_config_beam.json` (only AFTER run_57 is stopped — it is the live config file).
7. Re-verify flash/PS co-framing at latency 34; launch a short sub-run; check live event sizes
   (~k8 target ~1.8 kB/FEU) + in-window loss; then step k down toward 3.5σ if the CM floor holds.

---

## ZS enablement — engineering gaps found 2026-07-19 (must close + TEST before a real run)

### DONE: IPD is now a run-config knob
`dream_daq_info['inter_packet_delay']` (`run_config_beam.py`) → `make_config_from_template`
writes `Feu * Feu_InterPacket_Delay` (`dream_daq_control.py`). Default 100 (Raw-safe);
ZS beam runs set **2**. Verified: register rewrites 100→2 on a template copy; both files
compile. (run_57's live JSON not regenerated.)

### STATUS 2026-07-19 — prepared + offline-validated (see docs/ZS_PULSER_TEST_PROCEDURE.md)
GAP 1 is **closed** via the operator's template-rename approach (no `stage_zs_files` port
needed): a ZS cfg template `~/beam_july/dream_config/Tcm_Mx17_July_ZS.cfg` carries active
per-FEU `Feu N Feu_RunCtrl_PdFile/ZsFile` lines pointing at the canonical names
`get_pedestals` already writes — so the existing copy step + these lines load the thresholds
with no `dream_daq_control` change. The k-rescale + tracers are done by editing the (text)
thr.prg: `dream_scripts/prep_zs_thresholds.py --k 8` → a self-contained pedestal set
`zs_k8_tracer_from_07-18-26_14-06-43` the run config references by name. Dry-run confirmed the
emitted `.cfg` + all 16 staged prg's. **GAP 2 RESOLVED by live test (run `zs_pulser_test`,
2026-07-19)**: Option B (firmware Pd=0 + offline pedestal subtraction) is CORRECT and runs
through the existing `processor_watcher` (`pedestal_loc='find'`, `--cns 0`) **unchanged** — no
processor pedestal-skip switch needed. Verified by running `analyze_waveforms` with vs without
the pedestal ROOT: WITH-ped gives clean hits (FEU01 6966 / FEU05 8304), the 256 path over-counts
1.4–2.4× and over-subtracts more channels negative (with Pd=0 the per-channel pedestals remain in
the data, so offline subtraction is required). Machinery all passed: thresholds load, ZS
suppresses to ~9% of Raw, tracers 100%, IPD=2 sustained on SSD. See
docs/ZS_PULSER_TEST_PROCEDURE.md "RESULT". Original gap analysis kept below for reference.

### GAP 1 — production DAQ never *activates* the ZS threshold `.prg` (firmware side)
- The `_thr.prg` is emitted **by the pedestal-threshold run itself** at `Sys PedRun Threshold`
  σ (template = **5σ**, hardcoded, never overridden in code — `Tcm_Mx17_July.cfg:47`). So the
  operator's model ("use latest pedestals to produce a threshold file") is half right: the
  threshold file is a *product of the ped run*, not derived from a separate pedestals file.
  A different k = either re-run pedestals at a new `Sys PedRun Threshold`, or post-rescale the
  5σ prg ×(k/5) (`gen_zs_ladder.py:rescale_thr`, k8=×1.6).
- `dream_daq_control.get_pedestals` **copies** `_ped.prg`→`dream_pedestals_NN_ped.prg` and
  `_thr.prg`→`dream_thresholds_NN_thr.prg` (`:805-814`) but **never writes the per-FEU
  `Feu N Feu_RunCtrl_PdFile/ZsFile` lines**, and the template wildcard is `...File None`
  (`Tcm_Mx17_July.cfg:200-201`) with all per-FEU lines commented (`:314-321`). ⇒ a production
  ZS run today **enables ZS mode but loads NO threshold file**.
- Fix: port `run_zs_test.py:stage_zs_files` (`:92-114`) into the production path — after
  `get_pedestals`, append `Feu N Feu_RunCtrl_PdFile dream_pedestals_NN_ped.prg` +
  `Feu N Feu_RunCtrl_ZsFile dream_thresholds_NN_thr.prg` for each active FEU (bare filenames
  override the wildcard `None`). Add an optional k-rescale + **tracer injection** (thr=0 on
  FEU-ch 0/224/511 — only in test `gen_tracer_thresholds.py` today, absent in production).

### GAP 2 — offline processor DOUBLE-SUBTRACTS pedestals on ZS data (analysis side)
- The `decode` binary auto-detects ZS vs Raw from the FEU header bit and parses the sparse ZS
  format fine (`DreamDecoder.cpp:91,129,189-222`); it does **no** pedestal math.
- **All pedestal subtraction is in `analyze_waveforms`/`WaveformAnalyzer`, and it ALWAYS
  subtracts a per-channel mean** (`WaveformAnalyzer.cpp:201-205,365`): with a pedestal ROOT →
  the measured raw baseline (correct for Raw); with **no** pedestal ROOT → a hard **256**
  (this IS the intended ZS / firmware-pre-subtracted path, `:28-35`, `.h:82`). It never
  inspects the ZS bit.
- **Production `processor_watcher` (`pedestal_loc='find'`) ALWAYS attaches a pedestal ROOT**
  (`processor_watcher.py:521-544`). So **firmware `Feu_RunCtrl_Pd=1` + current pipeline ⇒
  double subtraction ⇒ garbage hits.** Offline CNS is already OFF (`processor_config.py:41`),
  which is correct (firmware CM=1 must not be doubled).
- Two self-consistent options:
  - **Option A (designed ZS path):** firmware `ZS=1,Pd=1,CM=1` (sends ped+CM-subtracted, baseline
    ≈256) + offline runs analyze with **NO** pedestal ROOT (256 path) + `--cns 0`. **Requires a
    processor_watcher/processor_config change** to skip attaching the pedestal ROOT for a ZS run
    (no such switch today — the gap). This matches the ZS study's pipeline `ADC→Pd→CM→ZS thr`
    (Source 1 `ZS_SETTINGS.md`), so Pd=1 is very likely *required* for the ZS threshold to be
    referenced correctly.
  - **Option B (least offline change):** firmware `ZS=1,CM=1,Pd=0` + offline unchanged
    (subtracts the pedestal ROOT as today). Only valid **iff the FEU can zero-suppress on
    un-pedestal-subtracted samples** — needs FEU-firmware-doc confirmation; likely NOT available
    given the ZS pipeline above.
- Recommendation: plan for **Option A** — set `pedestal_subtraction=True` on the firmware AND add
  a ZS-aware switch to `processor_config`/`processor_watcher` that passes no pedestal ROOT (256
  baseline) + keeps `--cns 0`. Verify Pd-required against the FEU docs before committing to B.

## Trigger-rate testing status (2026-07-19) — context + the live re-tune
- **COMPLETED (ratified):** the 07-16 / 07-17-night **2D rate scans** (M2 plastic mV × M1 wall
  mult) concluded **Doubles-gated is the trigger** — Singles ≈1700/30ms window (70–90× the
  ~20-event DREAM budget, flat vs threshold); Doubles ≈79/window but 92% in the first 1 ms →
  clean tail 5.6/window, ~7–8 recorded events/window inside budget. In-window fraction 0.87
  (after the +20 ns M3 wall-leg alignment). Standing config **walls +15/+16/+15/+16 mV,
  plastics −30/−30/−30/−38 mV** (D deeper: M2 D1 broken, dead ≤−24 mV). Refs
  `~/beam_july/analysis/rate_scan_2d/night_0717/FINDINGS_2026-07-18.md` + `key_numbers.json`,
  `n1081b/HANDOFF_2026-07-17_night_trigger_scans.md`.
- **RUNNING NOW (the operator's threshold work):** NOT the 2D tool — it is
  `n1081b/threshold_ladder.py --board plastic --sections AC` (pid **213741**, holder since
  16:43; the "rate_scan_2d…" purpose strings are a reused session label). A 5-step
  "followup_newwalls" campaign proposing a **deeper wall set A:25/B:35/C:34/D:36 mV** and
  re-sweeping plastics (−200→−10 mV); at ~16:56 it was at point 52/96 of the A/C pass.
  Restores originals on **clean** exit → boards return to 15/16 + −30/−38 unless a final step
  applies the new walls. **Confirm the post-campaign board state before launching the run.**
- **Blocker/coordination:** this ladder holds exactly the boards the ZS-run trigger setup needs
  (M1/.240, M2/.241, M5/.244). Let it exit **cleanly (never SIGKILL** — wedges the boards for
  hours); the `scint --doubles --ps-pickup` setup then contends for the same `board_session`
  flock.
