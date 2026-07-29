# HANDOFF — DREAM config / documentation deep-dive for further optimizations (2026-07-23)

**For:** a fresh model, starting cold, during a beam-off window (~couple hours).
**Mission:** read the DREAM documentation and config files *thoroughly* and find any *other*
DAQ/readout optimizations we can make — beyond the read-clock win already banked today. Produce a
ranked, evidence-backed list of candidate levers, each with a concrete test plan, and (where safe and
clearly worthwhile) test them on the bench with the beam-off saturating pulser.

This is an **analysis + careful-bench-test** task, not a blind-scan task. Measure, don't assume; every
"knob does nothing" claim in this system has a history of being an artifact (see Traps below).

---

## 0. Orientation — read these first (in order)

1. `docs/CLOCK_RATE_SCAN_2026-07-23.md` — today's full result. The read-clock win, the firmware limits,
   the SparseRd nulls, the window-fixed sparse test. **Read this completely** — it defines the physics
   model you'll build on and lists what's already ruled out.
2. `CLAUDE.md` (repo root) + `n1081b/CLAUDE.md` — hard safety rules for the N1081B trigger boards.
3. This session's memory notes (in the auto-memory index): `dream-clock-firmware-limits`,
   `dream-clock-rate-scan`, `dream-flash-comb-mechanism`, `runctrl-clamps-feu-watermarks`,
   `stale-dream-daq-server-drops-cfg-overrides`, `raw-ipd-ladder-10gbe`,
   `dream-rate-answer-trigger-limited`. These encode the traps and prior nulls.

## 1. Where things stand (what is already TRUE — don't re-derive)

- **Read clock is now maxed and default.** `SAMPLE_PERIOD_CLOCK_DIVS[60] = ('4.0','6.0')` in
  `dream_daq_control.py` → every beam run reads out at **25 MHz (RdClk_Div 4.0)** with the 1.92 µs /
  32-sample / 60 ns window unchanged. Measured 1.5× rate (7231→10847 Hz, 0 drops, tracers 100%).
  **RdClk_Div 4.0 is the hard firmware floor** (`~/Feu/Firmware5/.../FeuUdpControl/DrmClkConfig.c`,
  `#define Drm_RdClk_Div_Min 4.0`; below it `FeuCtrl_Open fails`). 25 MHz is already a mild overclock
  of the ASIC's rated 20 MHz RCk. **There is no read-clock headroom left.** A multi-hour soak of the
  25 MHz default is the one open item there (do with beam back).
- **The physics readout model:** per-event cycle ≈ `NbOfSamples × (4.83 + 0.998×IPD) µs` (fitted from
  `~/beam_july/test/deadtime_study/results/deadtime_db.csv`). Readout time ∝ **NbOfSamples** (frozen
  SCA columns) / RCk. The DREAM ASIC reads "triggered columns only," 512-cell SCA, WCk 1–50 MHz,
  RCk ≤20 MHz rated.
- **At beam, the DAQ is TRIGGER-limited, not readout-limited** (~95 ev/spill = what the PS+singles
  beam trigger offers; see `dream-rate-answer-trigger-limited`). So readout optimizations do **not**
  raise beam yield *today*. They matter for headroom: hotter beam, a faster/denser trigger, shorter
  flash-recovery windows, or RAW-at-lower-IPD. Frame any proposal against that reality — say plainly
  whether a lever raises *headroom* or actual *yield*.

## 2. What is already RULED OUT (do not spend time re-testing)

- **RdClk < 4.0** — firmware-rejected, run fails. Confirmed (rd3wr4 → `FeuCtrl_Open failed`).
- **SparseRd (Main_Conf 0x100004 bits 19:17)** — register reaches hardware (verified live at 2 and 3
  on all 8 FEUs via `dream_scripts/feu_main_conf.py`) but is **inert**: flat rate AND flat volume,
  07-22 and 07-23. The one thing not settled beam-off: whether it silently narrows the window vs does
  literally nothing — needs a *timed* injected pulse to tell apart, and it doesn't change the verdict.
- **"Sample fast + go sparse to keep the window"** — empirically cannot beat coarse sampling at a fixed
  window (`win_sparse` run). readout_time = (columns spanning window)/RCk; both maxed at coarse+RdClk4.
- **Lowering OvrWrnHwm / trigger-FIFO watermarks** — NULL at beam and bench (band is trigger-limited;
  `runctrl-clamps-feu-watermarks`, and RunCtrl one-directionally clamps the cfg value anyway).
- **A network faster than 10 GbE** — readout stopped being the constraint before the network did.

## 3. Candidate levers to investigate (START HERE — ranked by expected value)

Each needs: (a) confirm the mechanism in the manual, (b) confirm it's plumbed or plumb it, (c) verify
the value lands **on the hardware** (peek), (d) measure with brackets. None is proven — that's the job.

1. **DreamMask / channel count — the most promising untested lever.** `Main_Conf_DreamMask` (0x100004
   bits 7:0, 1 bit per Dream ASIC, template `0x00` = all 8 active) and the ASIC channel-skip (DREAM
   slow-control **registers 9 & 10**, see DREAM manual §7.6.J/K). Readout time ∝ enabled channels ×
   frozen columns. **If any Dreams/channels are unused by the physics, masking them shortens the
   per-column mux directly — a real rate/deadtime win at full time resolution and full window.** This
   is the lever the clock work kept pointing at. `dream_mask` is **NOT plumbed** in
   `make_config_from_template` — you'd add it like `sparse_rd`. FIRST question to answer from the
   detector map: are all 8 FEUs × 8 Dreams × 64 ch actually instrumented/used? Ask before masking.
2. **Rd2AdcDataDel.** ADC-data delay in read-clock cycles. Manual: "for the 20.8 MHz read clock this
   value is usually set to 8." Our template does **not** set it (firmware default 0), yet data is clean
   at both 16.7 and 25 MHz. Worth understanding: is 0 leaving ADC-sampling margin on the table at
   25 MHz? Could a tuned Rd2AdcDataDel (or AdcClk_Phase) improve S/N or allow a cleaner overclock? Check
   the DREAM/FEU manual timing, then a bracketed baseline-noise comparison. Low risk, possibly low reward.
3. **MultiPackThr / MultiPack (UDP packing).** `multipack_thr`/`multipack_enb` ARE plumbed. One prior
   null (mp_off, mp8188 on 07-22) was **never re-checked on hardware** and the volumes did move
   (mp8188 = 103 MB vs base 1.5 GB — that looks like it DID something; investigate what). On 10 GbE
   with jumbo this affects host-side efficiency, not analog readout — frame as a network-efficiency
   lever. Verify the register (peek) before trusting any result.
4. **Latency vs derandomiser depth.** `latency` (Dream reg 12) sets the trigger pipeline depth and,
   via RunCtrl, the effective trigger-FIFO watermark cap `drm_evt_buf=(512-lat)/NbOfSamples`
   (`runctrl-clamps-feu-watermarks`). We run latency 35 at n32 → cap Hwm 11. Is 35 optimal for the
   PS/flash co-framing AND the derandomiser? The derandomiser overlap saturates at depth ~3 (proven),
   so likely little to gain, but the interaction with a shorter window (fewer samples) is unexplored.
5. **NbOfSamples / window depth as an operating choice.** n32→n16→n8 gives real ~1.5×/~2× steps
   (9.6/6.2/2.9 ms comb scaling) but throws away window depth (n8 = 0.48 µs). Not a free lever, but
   worth a crisp statement of the rate-vs-window Pareto front now that the read clock is maxed — the
   operator may accept a shorter window for a given physics goal.
6. **Zero-suppression tuning.** `ZsChkSmp`, `ZsTyp` (tracker vs TPC), thresholds (k-factor). ZS cuts
   **network/disk** volume, not analog readout time — so it helps the IPD/host ceiling, not the
   per-event deadtime. Relevant only if a future mode is host-limited again. Document the distinction.
7. **EventPrescale / Feu_PreScale_EvtData (Prescale reg 0x200018).** Sends every Nth event only — a
   throughput-vs-completeness knob for very high trigger rates. Not an optimization for physics
   completeness, but note it exists as a safety valve (manual §3.2.7).

**Also worth a careful read** (may surface something not listed): TCM manual (`tcm_user_manual_V2_12.pdf`,
now present — the 07-02 handoff said it was missing; the TCM `trig_rate` internal generator and the
lemo/veto mapping were open questions), and `MvtSoftwareUtilities.pdf`.

## 4. Key resources

- **Manuals:** `~/Documents/dream/` — `221217_FeuUsersManual.pdf` (FEU firmware: register map, Main_Conf
  0x100004, DreamClock 0xD00000, TrigGen 0xE00000, config-file appendix), `DREAM_User Manual_prod_v3.pdf`
  (ASIC internals: SCA, read/write clocks, slow-control registers 0–12 incl. 9/10 channel-skip),
  `tcm_user_manual_V2_12.pdf` (TCM), `MvtSoftwareUtilities.pdf`. **Extract text with**
  `pdftotext -layout <pdf> out.txt` into your scratchpad — grep is far faster than paging the PDF.
- **Firmware source (authoritative over the manuals — the manuals are looser):**
  `~/Feu/Firmware5/Distribution/Sources/Software/` — `RunCtrl/` (RunCtrl.c, FeuCtrl.c: what actually
  gets written, and the watermark clamp at RunCtrl.c:998-1051) and `FeuUdpControl/DrmClkConfig.c` (the
  clock limits). **When a manual and the source disagree, the source wins** (proven today: manual says
  RdClk range [2;6], source enforces [4.0;15.5]).
- **Config templates:** `~/beam_july/dream_config/*.cfg` — `Tcm_Mx17_July_ZS.cfg` (ZS, the production
  template) and `Tcm_Mx17_July.cfg` (Raw). All already `RdClk_Div 4.0`. The `Feu * <Module>_<Field>`
  lines map to registers; unplumbed fields (e.g. `DreamMask`, `DreamPol`, `Rd2AdcDataDel`) can be set
  by adding a param to `make_config_from_template`.
- **The config plumbing:** `dream_daq_control.py::make_config_from_template` — currently plumbs
  samples_per_waveform, latency, sample_period, rdclk_div, wrclk_div, sparse_rd, inter_packet_delay,
  multipack_thr/enb, trig_veto_len, ovr_wrn_hwm/lwm, loc_throt, zs_type, zs_check_sample,
  pedestal/CM subtraction, daq_run_events. Add new knobs the same way (and gate them per-sub-run via
  the subrun dict, which `{**dream_info, **subrun}` merges).
- **Read-only hardware tools:** `dream_scripts/feu_trig_counters.py --latch` (per-FEU accepted/drops/
  watermark/occupancy) and `dream_scripts/feu_main_conf.py [--expect N]` (Main_Conf 0x100004: SparseRd,
  Samples). Both are pure UDP peeks (port 1300+FeuId), safe any time incl. mid-run. To peek any other
  register, reuse the `peek()` helper in `feu_trig_counters.py` (mind the address-echo parse gotcha
  documented there).

## 5. HARD RULES (safety — violating these wedges hardware or corrupts data)

- **N1081B logic boards (192.168.10.240–.245) are fragile.** Any board access MUST go through
  `n1081b/n1081b_session.py`, one process at a time, never SIGKILL mid-session. Read `n1081b/CLAUDE.md`
  before *any* board touch. Check `config/n1081b_access/` for holder/quarantine first. `.244` has a
  stale quarantine entry — ignore it, but don't assume; the pulser/veto work only touches .245 (M6) and
  .243 (M4).
- **The `dream_daq_control.py` server is long-lived (tmux `dream_daq`, port 1101).** It builds each cfg
  with the code it was STARTED with. **Any edit to `make_config_from_template` requires restarting the
  server** or the new knob is silently dropped (this exact trap invalidated two earlier studies). Restart:
  `tmux send-keys -t dream_daq C-c` (x2), then
  `tmux send-keys -t dream_daq 'PATH=/home/mx17/PycharmProjects/nTof_x17_DAQ/bash_scripts/daq_shims:$PATH python dream_daq_control.py' Enter`.
  (SIGINT on THIS server is fine — the no-SIGKILL rule is for the N1081B boards, not this.)
- **The cfg is NOT proof a value reached the hardware.** RunCtrl rewrites some registers at configure
  time (watermarks, Main_Conf_Samples, RdClk_Div). ALWAYS peek the register off the FEU to confirm.
- **Beam can return mid-test.** The saturating pulser DROPS beam triggers while active. Watch
  `config/beam_state.json` and restore promptly (`beam_on: null` = UNKNOWN, not off — check CSV rows).

## 6. Method — how to test a lever safely (beam off)

1. **Set up the saturating trigger** (needed so the DAQ, not the input, is the limit):
   `.venv/bin/python n1081b/set_pulser.py --fixed --period 50000 --width 100`  (M6.D, 20 kHz)
   `.venv/bin/python n1081b/set_veto_open.py --lemos 4`  (opens M4.C veto; REQUIRED or 0 events)
2. **Build a bracketed run config** (see `run_config_clock_rate_scan.py` /
   `run_config_window_sparse_test.py` as templates): ZS k8, IPD 2, short (0.5–0.75 min) sub-runs, and
   **bracket every measurement with the baseline at both ends** so drift can't masquerade as an effect.
3. **Launch:** `.venv/bin/python daq_control.py <config>.json` (bare filename; it prepends the path).
4. **Per sub-run, measure AND verify:** rate from the `dream_daq` tmux pane (`IntRate`) and/or
   `feu_trig_counters.py --latch`; peek the swept register on all 8 FEUs; after decode, check ZS tracer
   channels 0/224/511 present ~100% and baseline ~256 (system python3 has uproot; `nt` tree, branches
   eventId/sample/channel/amplitude). **Note: raw volume ∝ rate × samples/event — use RATE, not volume,
   as the metric when samples/event varies.**
5. **RESTORE when done (or if beam returns):**
   `.venv/bin/python n1081b/trigger_mode.py scint --singles --ps-pickup`  then  `set_pulser.py`
   Verify: `set_veto_open.py --show` (C=or_veto[0]), `set_ps_trigger_delay.py --show` (delay 1800).

## 7. Deliverable

A short markdown report (`docs/DREAM_OPT_SURVEY_2026-07-23.md` or similar): for each lever —
mechanism (with manual/source citation), whether it's a *yield* or *headroom* lever, plumbed y/n, any
bench result (with the bracketed numbers and the hardware-verified register value), and a
recommendation (adopt / reject-with-evidence / needs-beam). Update the auto-memory index with anything
durable. **Do not** change production defaults without flagging it for the operator first — today's
read-clock default change was explicitly requested; treat further defaults as proposals until approved.

## 8. State at handoff (verified 2026-07-23, ~12:00)

- Beam OFF (~2 h expected). Trigger RESTORED to `scint(singles)+ps`, PS delay 1800, pulser Poisson.
- `dream_daq` server RUNNING with the new 25 MHz-default clock map (restarted today).
- No run active. `config/beam_state.json` is the live beam truth.
- Uncommitted working-tree changes from today: `dream_daq_control.py` (wrclk_div knob + clock-map
  default), `run_config_beam.py` (comment), new files `dream_scripts/feu_main_conf.py`,
  `run_config_clock_rate_scan.py`, `run_config_window_sparse_test.py`,
  `docs/CLOCK_RATE_SCAN_2026-07-23.md`. Nothing committed — leave commits to the operator.
