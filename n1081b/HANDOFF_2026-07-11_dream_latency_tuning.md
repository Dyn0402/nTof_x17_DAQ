# HANDOFF — DREAM readout latency tuning for the Singles trigger (2026-07-11)

**Goal:** find the DREAM DAQ **latency** that places the Singles-triggered Micromegas pulse inside
the readout window for `run_29` (**32 samples × 60 ns = 1920 ns**, `run_config_beam.py`). The
latency currently in run_29 is **3**, inherited from run_19 — but run_19 triggered on the *old
gamma-flash* signal, which has a **different time relationship to the detector pulse** than the new
**Singles** trigger (Singles arrives later, via the M4.A→C→D monostable chain). So the latency
almost certainly needs re-tuning; the wrong value = clipped/empty waveforms all night.

This was set up but **not completed** — the FEUs were network-down at the time (now recovered).
Companion doc: `HANDOFF_2026-07-11_trigger_and_rate_tests.md`.

---

## Prerequisites (check first)
1. **All 8 FEUs reachable** (were down 2026-07-11 early AM; recovered):
   `for ip in 44 83 110 111 118 43 81 82; do ping -c1 -W1 192.168.10.$ip; done` — all must UP.
2. **No other RunCtrl running** (`pgrep -x RunCtrl` empty). The main DAQ (`dream_daq` tmux) must be
   idle — only one RunCtrl can talk to the TCM at a time.
3. **Detector HV on** (mx17 A–D: resist card5 ch1–4, drift card9 ch0–3). Was resist ~475 / drift
   800 — good gain; no need to change for the scan.
4. **Singles trigger live**: M4.D out0 must fire (see trigger handoff §A.5). Verify on the LA or
   just that RunCtrl records events. **The DREAM trigger cable is on D1 out.**
5. **Beam on** helps (higher trigger rate → faster stats) but is not required.

---

## Config mapping (how run_config params become DREAM registers)
From `dream_daq_control.py` (`make_config_from_template`):
- `Sys NbOfSamples`  = 32
- **latency** → `Feu * Dream * 12` = `0x{latency:04X}` (e.g. latency 3 → `0x0003`)
- **sample_period 60 ns** → `Feu * DrmClk RdClk_Div = 6.0`, `WrClk_Div = 6.0`
  (20 ns would be 4.0 / 2.0 — the template default)
- Full readout (`Sys DaqRun Mode Raw`), external trigger (`Sys DaqRun Trig Ext`), all 4 detectors.

**Latency ↔ sample position is 1:1** (Dream reg 12 is in write-clock = sample units). From the
2026-07-02 gamma-flash scan (`~/beam_july/test/gamma_flash/SCAN_RESULTS.md`), at 20 ns:
`peak_sample ≈ latency + 27`. The `+27` offset is trigger-specific, so **for the Singles trigger
the offset is unknown and must be measured** — but the 1:1 shift still holds, which is what makes
the one-shot method below work.

---

## Harness (already staged)
`~/beam_july/test/latency_singles/` contains a copy of the gamma-flash harness pointed at the
production template:
- `run_test.py` — copies the template into a variant subdir, applies `--set KEY=VALUE` overrides,
  runs RunCtrl (batch `-b`), decodes FDFs, runs `check_flash.py`. `GO_TIMEOUT` lowered to 40 s.
- `Tcm_Mx17_July.cfg` — copy of the production template (`/mnt/data/x17/beam_july/dream_config/`).
- `check_flash.py` / `check_completeness.py`, Grace `.par` files.

One RunCtrl run at latency N, 60 ns, 32 samples:
```
cd ~/beam_july/test/latency_singles
python3 run_test.py lat_N --minutes 3 \
  --set "Sys NbOfSamples=32" \
  --set "Feu * DrmClk RdClk_Div=6.0" \
  --set "Feu * DrmClk WrClk_Div=6.0" \
  --set "Feu * Dream * 12=0x00XX 0x0000 0x0000 0x0000"    # XX = hex(latency)
```
Decoded output lands in `<variant>/decoded_root/*.root` (tree `nt`, branches `sample`, `amplitude`).

---

## Method — one wide-window locator, then compute
1. **Locate**: run once with a **wide window** (128 samples) so the pulse is caught regardless of
   offset:
   ```
   python3 run_test.py locate128 --minutes 3 \
     --set "Sys NbOfSamples=128" --set "Feu * DrmClk RdClk_Div=6.0" \
     --set "Feu * DrmClk WrClk_Div=6.0" --set "Feu * Dream * 12=0x0003 0x0000 0x0000 0x0000"
   ```
   In the decoded data, find the **peak sample S** of the event-averaged, channel-averaged profile
   (the beam-induced Micromegas bump above pedestal). `check_flash.py` prints `med_peak_smp` and
   plots the profile; for a sparse track a max-channel-based finder is more sensitive if needed.
2. **Compute**: because latency shifts 1:1, to move the peak to a target sample T_tgt (~10–14 of
   32, leaving room for baseline before + tail after):
   `latency_final = 3 + (T_tgt − S)`  (used latency 3 in the locator). If that is < 0 the pulse is
   already too late even at latency 0 — widen sampling or check cabling/trigger delay.
3. **Confirm**: run 32 samples at `latency_final`; verify the pulse sits at ~T_tgt, has good
   amplitude, and is not clipped at either window edge.
4. **Set run_29**: put `latency_final` in `run_config_beam.py` `dream_daq_info['latency']`,
   re-generate the JSON (`python run_config_beam.py`), then launch run_29.

---

## Known gotchas (cost hours if ignored)
- **60 ns DrmClk config UNVERIFIED.** Every RunCtrl attempt during setup failed at
  `DrmClkConfig: PwrBits Set WrRd_Missmatch → FeuCtrl_Reset failed` — but that was the **FEU
  network outage** (a 20 ns run failed identically; all 8 UDP connections were dead). Now that the
  FEUs are back, **the very first thing to confirm is that a run actually configures** — do a quick
  20 ns sanity run first (`--set "Sys NbOfSamples=32"` only, no DrmClk override). If it takes data,
  FEUs are healthy; then test the 60 ns clock. If the 60 ns `DrmClk 6.0/6.0` PLL relock genuinely
  fails even with FEUs up, that is a **separate real blocker** (run_19 supposedly used 60 ns via
  the normal DAQ path — check whether it needs a pedestal run first to relock, or whether run_29
  should fall back to 20 ns / more samples). Flag to the user before committing the overnight run.
- **Batch-mode "G" nudge**: RunCtrl in `-b` sometimes waits for a `G` to start; `run_test.py`
  sends it after `GO_TIMEOUT` (now 40 s) if no FDFs appear. Keep runs ≥ 2–3 min so there is data
  time after the nudge.
- **"No datrun FDFs / 0 events"** with FEUs healthy = no triggers arriving → the Singles trigger
  (M4.D out0) is not reaching the TCM. Re-check the trigger fix (trigger handoff §A.5).
- **Only one RunCtrl at a time** — stop the main `dream_daq` run first.
- Decoder: `/home/mx17/CLionProjects/mm_strip_reconstruction/build/decoder/decode`.

---

## After latency is set
Launch `run_29` (built, in `run_config_beam.py`): Singles-triggered, 32×60 ns, gas Ar/Iso 80/20,
Marex, uniform −10 V drift-800 scans (×2) + brief drift-600 + 8 h final at max−15 V. Schedule
printed by `python run_config_beam.py`. **Verify the first sub-run's waveforms sit in-window
before leaving it overnight.**
