# ZS pulser test — runbook (2026-07-19)

## RESULT (2026-07-19 17:52 run `zs_pulser_test`, 3×2 min, ~675 evt/subrun) — PASSED
DAQ machinery fully validated end-to-end:
- k8 thresholds loaded (8 FEUs), ZS active: FDF **8.2 MB vs ~90 MB** full-Raw (~9%).
- Suppression works: pulser events collapse to the 3 tracer channels (10th-pctile = 3
  distinct channels/event); tracers 0/224/511 present in **100%** of events.
- Baseline sane: tracer amplitude ~260 ≈ CmOffset 256; signals to full scale.
- **IPD=2 sustained** at 5–6 Hz on the SSD path, no stalls; per-subrun poll safely skipped
  the ladder-held M1/M2 (BoardBusyError), no wedge.

**Option B (Pd=0 + offline pedestal subtraction) is CORRECT — and needs NO processor change.**
`analyze_waveforms` both ways on the Pd=0 data:

| FEU | WITH ped (Option B) | NO ped (256 path) |
|---|---|---|
| 01 det D | 6966 hits, med amp 2402, 0.2% baseline neg | 16874 hits, med 817, 0.9% neg |
| 05 det B | 8304 hits, med amp 2600, 0.1% neg | 11589 hits, med 1998, 0.1% neg |

With Pd=0 the firmware leaves per-channel pedestals in the data, so offline subtraction is
required; the WITH-ped path gives clean/fewer/higher-amplitude hits, the 256 path over-counts
~1.4–2.4× (uncorrected per-channel offsets faked as signal) and over-subtracts more channels
negative. The production `processor_watcher` (`pedestal_loc='find'`, `--cns 0`) already does
exactly the WITH-ped path → **Option B runs through the existing pipeline unchanged; the
Option-A processor pedestal-skip switch is NOT needed.**

Remaining before a physics run: operator sets scint thresholds + drift/resist HV; re-verify
flash/PS co-framing at the physics latency; then the same ZS config drives a doubles+PS run.

---


First production-path zero-suppression test, driven by the **random pulser** so it needs
NO scintillator-trigger boards (those are held by the live plastic-threshold ladder). See
`docs/live_zs_run_sources_2026-07-19.md` for the full source synthesis and the two
production gaps this closes.

## What's already prepared (offline-validated, no hardware touched)
- **IPD plumbed**: `dream_daq_info['inter_packet_delay']` → `Feu_InterPacket_Delay`
  (`run_config_beam.py`, `dream_daq_control.py`). Verified it rewrites the register.
- **k8 threshold set**: `~/beam_july/pedestals/zs_k8_tracer_from_07-18-26_14-06-43/` — latest
  pedestals (07-18) rescaled ×1.6 (matches the beam-validated `gen_zs_ladder`) + tracers on
  FEU-ch 0/224/511. Built by `dream_scripts/prep_zs_thresholds.py --k 8` (re-run any time; it's
  just text-editing the thr.prg — no pedestal re-take needed).
- **ZS cfg template**: `~/beam_july/dream_config/Tcm_Mx17_July_ZS.cfg` — base template + active
  per-FEU `Feu N Feu_RunCtrl_PdFile/ZsFile` lines for FEUs 1–8 (base template untouched).
- **ZS run config**: `run_config_zs_pulser_test.py` → `run_config_zs_pulser_test.json`.
  ZS=1, CM=1, **Pd=0 (Option B)**, ZsTyp=tpc, ZsChkSmp=4, IPD=2, latency 5, 32 smp.
  3 × 2 min sub-runs, HV left as-is (only scint PMT holds asserted).
- **Dry-run confirmed** the emitted `.cfg` has `DaqRun Mode ZS`, `Pd 0 / CM 1 / ZS 1 /
  ZsTyp 1 / ZsChkSmp 4`, `InterPacket_Delay 2`, latency `0x0005`, all 8 ZsFile/PdFile lines,
  and all 16 prg's stage under the canonical names.

## ZS scheme under test — Option B (operator-preferred)
- **Firmware**: ZS + CM on, **pedestal subtraction OFF** (`Feu_RunCtrl_Pd=0`). PdFile is still
  loaded so the firmware has pedestals for the ZS threshold reference.
- **Offline**: processor pipeline UNCHANGED — `analyze_waveforms` subtracts the pedestal ROOT
  as today (`pedestal_loc='find'`), `--cns 0` (already set, so firmware CM isn't doubled).
- **Open question this answers**: can the FEU zero-suppress with Pd=0? If yes → clean (no
  processor change). If the pulser events come out full-size (no suppression) or the hits look
  pedestal-shifted → Pd=0 isn't supported; switch to **Option A** (below).

## Launch sequence
Prereq: **run_57 must be stopped** (only one RunCtrl/DREAM at a time). The threshold ladder on
M1/M2/M5 keeps running — it does NOT conflict (pulser trigger is on M4/M6). Do not SIGKILL
anything on the N1081B boards.

1. Confirm the pulser trigger is still applied (it is, from run_57 — do NOT re-run trigger_mode,
   which would touch M4/M1/M2): `.venv/bin/python n1081b/trigger_mode.py status`
   (expect flash_random: C=or_veto[4], D=[0,1]) and `n1081b/set_pulser.py --show`.
2. Stop run_57 cleanly (operator action — it's live data).
3. Regenerate the ZS test JSON (picks up current run_config_beam inheritance):
   `.venv/bin/python run_config_zs_pulser_test.py`
4. Launch: `./start_run.sh run_config_zs_pulser_test.json`  (bare filename — start-run gotcha).
5. Watch the first sub-run.

## Checks (what "pass" looks like)
1. **Thresholds loaded** — RunCtrl log shows it reading `dream_thresholds_0N_thr.prg` for FEUs
   1–8 with no error; the sub-run `.cfg` in the run dir has the 8 ZsFile lines.
2. **Suppression works** — pulser (non-flash) events are tiny: near-empty except the 3 tracer
   channels/FEU. Event size ≈ k8 target (~1.8 kB/FEU or less), NOT the ~38 kB full-Raw size.
   If events are full-size → Pd=0 didn't suppress → go to Option A.
3. **Sparse decode OK** — `processor_watcher` decodes without error; hit maps populate; tracer
   channels 0/224/511 present every event (integrity watermark).
4. **No double-subtract** — decoded hit amplitudes look physical (flash events show real MM
   signal; pedestals not driven to ~−256). If garbage/pedestal-shifted → Option A.
5. **DAQ keeps up at IPD=2** — no host `Udp RcvbufErrors` growth on the SSD path; sustained
   rate matches the pulser gate.

## Fallback — Option A (if Pd=0 fails)
- Set `pedestal_subtraction=True` in `run_config_zs_pulser_test.py` (firmware pre-subtracts,
  baseline → ~256).
- Make the processor pass NO pedestal ROOT for ZS (so `analyze_waveforms` uses its 256 baseline
  instead of double-subtracting): needs a ZS-aware switch in `processor_config.py` /
  `processor_watcher.py` (currently `pedestal_loc='find'` always attaches one). Keep `--cns 0`.
- This is the code path that was NOT built yet (deliberately — Option B needs no processor
  change). Implement only if the test forces it.

## Notes / risks
- HV is left wherever run_57 left it — fine for a machinery test (pulser reads flat pedestal
  regardless). Operator sets drift/resist for the real physics run.
- The k8/tracer prg's are derived from the 07-18 pedestals; if pedestals are re-taken, re-run
  `prep_zs_thresholds.py`.
- Only offline validation done so far — the firmware ZS-with-Pd=0 behaviour (check #2/#4) is
  genuinely unverified and is the point of this test.
