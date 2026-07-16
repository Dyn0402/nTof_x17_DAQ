# HANDOFF — Random-trigger / veto-gate pathology: confirm, bisect, qualify
**For the session running 2026-07-13 (after the gas has changed to Ar/Iso 90/10).**
Written 2026-07-12 ~02:30. Owner instruction: *"we need to test this randomizer
before we run — very important."* Do NOT start the next random-trigger physics
run until §5 passes.

---

## RESULTS 2026-07-12 ~11:45–12:00 (midday, beam ON ~0.32 Hz, post gas change)

§3 AND test and §4b or_veto test both run (4 min DREAM each, one RunCtrl at a
time, harness `~/beam_july/test/latency_singles/run_test.py`; analysis
`analyze_intervals.py` there; PNGs saved next to each variant):

| variant | config | result |
|---|---|---|
| `vetoAND_gated` | C=AND(lemo4 pulser, lemo5 INVERTED), D=OR(C) | **CLEAN**: 1329 evt, 77 bursts @ 0.321 Hz (= beam 0.315 Hz), span med/p90 25.8/29.2 ms, in-burst mean 1.57 ms, quartile/mean = [0.283, 0.691, 1.344] (exp: 0.288/0.693/1.386), sub-0.2 ms bins match exponential bin-by-bin — NO trains, NO 10 ms comb |
| `vetoORb_gated` (§4b, run_30 replication) | C=or_veto(lemo4), D=OR(C) | **CLEAN too**: 1380 evt, 77 bursts @ 0.321 Hz, span 26.5/29.5 ms, mean 1.54 ms, quartiles exponential — pathology did NOT reproduce |

Conclusions:
1. **§5 randomizer qualification: PASS at this hour** — the gated trigger is a
   textbook Poisson (1.5 ms mean) inside ~30 ms windows at the beam-pulse rate.
   NOTE: criterion §5.1 as written (frac<0.1 ms < 5 %) is miscalibrated for a
   1.5 ms-mean generator — a perfect exponential puts ~6.3 % below 0.1 ms; both
   runs measured 5.5–6.6 % with no excess over the exponential prediction. The
   correct form is "no EXCESS below 0.1 ms vs the fitted exponential."
2. **or_veto is NOT (currently) reproducibly broken** — both AND and or_veto
   gate cleanly back-to-back. Consistent with the intermittent/line-chatter
   hypothesis (§0): run_30's trains appeared in the EVENING. §4e still open:
   repeat one 4-min run (`run_test.py` + `analyze_intervals.py`) in the evening
   before trusting an evening random-trigger run; §4c (line-only OR) is the
   smoking-gun test if trains ever reappear.
3. Board state: .243 RESTORED to the run_32 config (§2: C=OR(lemo0), D=OR(0,1),
   D.in0 G&D 1980 ns) and verified. Snapshots:
   `dump_2026-07-12_{pre,post}_veto_test.json`. .245 pulser verified Poisson
   period 1 500 000 width 100. Config helper for re-running the AND test:
   `n1081b/setup_vetoAND_test.py` (has `--anti` for the anti-window variant).

## 0. TL;DR for the executing session

run_30's "random" probe triggers were not random: **~30 µs trigger trains
repeating at exactly 10.0 ms** (100 Hz = mains half-period). The Poisson pulse
generator itself is verifiably fine; the pathology appeared exactly when the
REAL `or_veto` went live on M4.C. Working hypothesis: **the N93B veto line
chatters across the NIM threshold at 100 Hz** (mains ripple), chopping the
or_veto output into comparator-chatter bursts. The owner is (rightly)
skeptical — confirm with the transparent AND test (§3), bisect (§4), then
qualify the randomizer (§5) before any new random-trigger run.

**Preconditions**: DAQ idle (`pgrep -x RunCtrl` empty; `tmux capture-pane -t
daq_control -p | tail -3` shows done/donzo), run_32 (gas-change watch) finished
or stopped. Beam NOT required for the pulser tests (the veto line's 30 ms
windows need PS pulses though — beam-on makes §4c meaningful).

---

## 1. Evidence so far (all reproducible from disk)

Interval distributions of consecutive DREAM triggers (decoded `timestamp`×10 =
ns; first entry per eventId; dt<500 ms):

| dataset | config | result |
|---|---|---|
| `~/beam_july/test/latency_singles/m2_confirm32_lat5/` (2026-07-11 15:30) | pulser 15 ms Poisson, **veto INERT** (C was still plain OR — pre-`set_section_function` fix) | clean exponential, median 11.5 ms, quartiles 4.7–23.3 — **generator is truly Poisson** |
| `runs/run_30/randOn_A700_00` (18:37) | pulser 1.5 ms Poisson, **REAL or_veto** | **66 % of dt < 0.1 ms (median 31 µs!), 12–13 % at 8–12 ms; gaps at exactly ~10.0 ms** |
| `runs/run_30/randOff_A650_05` (19:38) and re-takes (~21:50) | same | identical pathology — stable for 3+ h |
| `runs/run_19/dr800_A485_00_p1` | old external generator (pre-N1081B-veto) | clean, median 1.07 ms |
| LA gate test 2026-07-11 ~15:55 (`veto_gate_test.py`) | REAL or_veto + pulser | **clean**: 0.38 Hz captures ≈ beam rate — so the chatter is intermittent / condition-dependent (evening?) |

Diagnostic plots: `analysis/flash_recovery_run{18,19,30}/figures/trigger_time_distribution.png`
(run_30 = clusters at 0/10/20/30 ms; run_18/19 uniform). Interval-check snippet in §6.

Key deduction: 30 µs spacing cannot come from ANY setting of a 1.5 ms-period
generator → something downstream multiplies edges. 10.0 ms periodicity → 100 Hz
→ mains. The only config difference between clean and pathological datasets is
the or_veto being genuinely selected.

## 2. Current board state & what must be restored

- run_32 trigger is live on .243 (from `setup_run31_trigger.py`):
  C = **plain FN_OR(lemo0=Singles)**, D = OR(lemo0 flash, lemo1 C-out),
  **D.in0 G&D delay = 1980 ns**. After ALL tests, either restore this (if
  run_32-style running continues) or apply whatever the next run needs —
  and if reverting to standard scint: `trigger_mode.py scint --singles` +
  zero D.in0's G&D (`set_input_channel_configuration(SEC_D, 0, True, False,
  100, 0, False)`).
- Pulser M6.D (.245): Poisson (`STAT_POISSON`), period 1 500 000, width 100
  (the as-found 2026-07-09 values). **Do NOT set period ≥ ~100 ms — silently
  kills the output** (15 ms verified OK for throttled tests).
- **THE SDK GOTCHA** (cost days): `configure_or/or_veto/and/...` do NOT set
  the function type — call `d.set_section_function(Section.SEC_C,
  FunctionType.FN_X)` FIRST, then `configure_x(...)`, then verify with
  `get_sections_function()` (its `function_name` is truthful).
- Snapshot before touching anything:
  `.venv/bin/python n1081b/dump_module_info.py > n1081b/snapshots/dump_2026-07-13_pre_veto_test.json`
- Cabling (LA-verified 2026-07-11): M4.C lemo0=Singles, lemo1=Doubles,
  **lemo4=pulser**, **lemo5=N93B veto line** (inverted NIM: HIGH=veto,
  LOW 30 ms after each PS pulse = enable). M4.D lemo0=PS/flash line,
  lemo1=C out; D.out0 = DREAM trigger cable. Docs: `RUN_MODES_2026-07.md`.

## 3. THE OWNER'S CONFIRMATION TEST — transparent AND (do this first)

Replace or_veto's opaque semantics with an explicit AND so the gating logic is
beyond doubt, then look at the DREAM event-time distribution.

Config (on .243, DAQ idle):
```python
import trigger_mode as tm
from n1081b_sdk import N1081B
d = tm.connect()
d.set_section_function(N1081B.Section.SEC_C, N1081B.FunctionType.FN_AND)
# AND of lemo4 (pulser) + lemo5 (veto line). Singles/doubles lemos DISABLED.
# NOTE: check configure_and's signature (5 or 6 enables) with inspect.signature
# before calling; enable ONLY lemo4 and lemo5.
d.configure_and(N1081B.Section.SEC_C, False, False, False, False, True, True, False, 0)
# INVERT the veto line at the input so AND fires IN-window (line LOW = enable):
c = d.get_input_channel_configuration(N1081B.Section.SEC_C, 5)['data']
d.set_input_channel_configuration(N1081B.Section.SEC_C, 5, True, False, c['gate'], 0, True)  # invert=True
tm.set_d_or(d, [1])   # D = C-out only (no flash line, clean sample)
```
Also make sure D.in0's G&D from run_32 is irrelevant (D lemo0 disabled above)
and D.in1 input channel is enabled, gd off.

Then a short DREAM run (harness, ~4 min — one RunCtrl only!):
```bash
cd ~/beam_july/test/latency_singles
python3 run_test.py vetoAND_gated --minutes 4 \
  --set "Sys NbOfSamples=32" \
  --set "Feu * DrmClk RdClk_Div=6.0" --set "Feu * DrmClk WrClk_Div=6.0" \
  --set "Feu * Dream * 12=0x0005 0x0000 0x0000 0x0000"
```
Analyze intervals with the §6 snippet. Readings:
- **clean gated Poisson** (bursts of exponential ~1.5 ms-mean intervals inside
  ~30 ms windows at the beam-pulse rate) → or_veto itself was the problem
  (function bug) — the AND is a valid replacement for physics runs.
- **same 30 µs / 10 ms trains** → the pathology is on the LINE (or input
  stage), not the or_veto function → go to §4.
- ALSO run the anti-window variant (invert=False on C.in5): should be the
  complement (pulser passing OUTSIDE windows). If BOTH show 10 ms structure,
  it is unambiguously the line.

## 4. Bisection matrix (each = one 3–4 min DREAM run + §6 snippet, or the
LA capture-rate quick test: clean-gated ≈ 0.3–0.6 Hz, chatter ≈ re-arm-limited ~7 Hz)

| # | C function | inputs | expects if line-chatter | expects if or_veto bug |
|---|---|---|---|---|
| a | plain OR | lemo4 only (pulser, ungated) | clean Poisson 667 Hz | clean |
| b | or_veto | lemo4; veto=lemo5 | 30 µs/10 ms trains (replicate run_30) | trains |
| c | **OR** | **lemo5 only (the LINE alone)** | **DREAM triggers in 30 µs/10 ms trains — the smoking gun** | clean ~0.3 Hz windows |
| d | AND | lemo4+lemo5 inverted (§3) | trains | clean |
| e | repeat (b) at a different time of day | — | comes & goes with mains conditions | stable |

(c) is the decisive one: it samples the veto line directly with ns precision —
no pulser, no veto logic. If (c) shows the 10 ms comb, the fix is hardware:
**scope the N93B output / check termination & grounding at the rack** (100 Hz
ripple on an inverted-NIM line sitting near threshold). A possible board-side
mitigation to TEST (not assume): give C.in5 a G&D (`enable_gd=True,
gate=50…100 ns`) — the G&D re-times on the leading edge and may swallow
chatter; verify it doesn't break the 30 ms window semantics (G&D replaces the
LEVEL with a fixed-width pulse — likely WRONG for a level veto! Check on the
LA before trusting). Rack-side fix is the real answer.

## 5. RANDOMIZER QUALIFICATION — MANDATORY before the next random-trigger run

Owner: *"we need to test this randomizer as well before we run — very
important."* Acceptance, from a ≥4 min DREAM run in the final intended trigger
config (gated pulser, whatever gate implementation §3/§4 lands on), using §6:

1. **No trains**: fraction of intervals < 0.1 ms is < 5 %.
2. **No mains comb**: no excess at 8–12 ms above the exponential tail
   (frac(8–12 ms) consistent with the fitted exponential, not a spike).
3. **In-window exponential**: intervals within bursts consistent with the
   generator mean (1.5 ms design, or whatever period is set), i.e. quartiles
   ~[0.4, 1.0, 2.1]×mean like run_19/m2_confirm.
4. **Window structure**: bursts ≈ 30 ms span at the beam-pulse rate; first
   event of each burst = flash only if the flash line is enabled.
5. Save the interval histogram PNG next to the run and note pass/fail in the
   run log / handoff.

Also re-verify the pulser config right before the run:
`get_function_configuration(SEC_D)` on .245 must read
`frequency_type 1 (Poisson), period 1500000, width 100`.

## 6. Interval-analysis snippet (venv: `~/PycharmProjects/nTof_x17/.venv`)

```python
import uproot, numpy as np, glob
fs = sorted(glob.glob('<variant_or_subrun>/decoded_root/*_03.root'))[:1]
a = uproot.open(fs[0])[uproot.open(fs[0]).keys()[0].split(';')[0]].arrays(
    ['eventId', 'timestamp'], library='np')
eid, t = a['eventId'], a['timestamp'].astype(np.float64) * 10.0  # ns
_, first = np.unique(eid, return_index=True)
t = np.sort(t[first]); dt = np.diff(t) / 1e6  # ms
dt = dt[dt < 500]
print('pct[5,25,50,75,95]:', np.percentile(dt, [5,25,50,75,95]).round(3),
      ' frac<0.1ms:', (dt<0.1).mean().round(2),
      ' frac 8-12ms:', ((dt>8)&(dt<12)).mean().round(2))
# healthy m2_confirm reference: [0.93, 4.7, 11.5, 23.3, 50.3], <0.1ms: 0.01
# run_30 pathology:             [0.005, 0.011, 0.031, 0.33, 10.0], <0.1ms: 0.66
```

## 7. Bookkeeping

- Gas after the change: **Ar/Iso 90/10** — HV maxes from the 80/20 campaign do
  NOT transfer; detector behavior at given HV will differ. Trigger timing
  (latencies 60/5/35) is gas-independent.
- Detector D is back in service (520 V during the gas change); its trip
  history (640–690 V on 80/20) means re-establish its max carefully.
- If tests conclude before the next run: leave boards in the run's intended
  trigger config and note it here; snapshot after
  (`dump_2026-07-13_post_veto_test.json`).
- Related docs: `RUN_MODES_2026-07.md` (IO map + modes),
  `HANDOFF_2026-07-11_latency_tuning.md` (latencies, veto discovery),
  memory notes `m4c-veto-gate`, `flash-recovery-trigger-timing`,
  `three-mode-trigger-latency`.
