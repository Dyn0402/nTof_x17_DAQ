# TCM veto-lemo test — results (2026-07-02)

Remote test of the open question in `~/Documents/dream/DREAM_HANDOFF.md`: do any of
DREAM's TCM "other 3" lemos emit a veto/busy output? Run entirely from `mx17-daq`.

**Bottom line: conclusive NO signal.** With DREAM confirmed heavily busy and the
detector fully positive-controlled, the three veto-suspect TCM lemos show nothing —
no edges, no level — across every input standard, polarity, impedance, and threshold
down to ±30 mV, on three independent readout methods.

## Rig

- **Trigger source:** `.245` (6th N1081B, serial 23011, CERN net `128.141.177.245`,
  fw 2022.3.0.0, **no login/auth**) — **section C = pulse_generator**, deterministic,
  `period=100000` ns, `width=20` ns → **10 kHz** (measured, see positive control).
  Output → module 5 (`.244`) section A → `.244` section C out lemo 2 → **TCM trigger
  lemo** → DREAM.
- **Veto monitor:** `.244` (`192.168.10.244`) **section D**, panel LEMO **1,4,5 =
  SDK lemo 0,3,4** (panel labels are 1-based; SDK is 0-based).
- **Dummy DREAM acquisition:** `RunCtrl -c Tcm_Mx17_July.cfg -f None -b` in
  `~/beam_july/test/veto_test/` (copy of `test/test2`, only `.cfg` + Grace `.par`
  kept). **cfg edited: `Sys Action PedThrRun 0`** so it skips the pedestal run (which
  fails under `-f None`) and goes straight to external-trigger data-taking. `-f None`
  = full FEU readout, nothing to disk.

## Confirmed facts

1. **The .245 pulse is the SOLE trigger source.** Disabling it froze `nb_of_events`
   (0 Hz); re-enabling restored triggers. Whole `.245 → .244 → TCM → DREAM` chain works.
2. **Real pulse rate = 10 kHz; DREAM is deadtime-limited at ~298 Hz** → **97 %
   deadtime**, i.e. FEUs busy essentially continuously. (`IntRate` pinned at 298.22 Hz
   across a 100× pulse-period sweep.) This is exactly the condition the handoff said
   was needed to make any busy/veto assert.

## Detector is positive-controlled (this is the key upgrade from the first pass)

Borrowed `.244` **section A** (where the pulse physically lands, panel 1) and pointed
each readout method at the known 10 kHz pulse:

- **Counter:** 50,233 counts in 5 s = **10,045 Hz** on lemo 0 (panel 1). ✅
- **Time Tag:** 40,011 tags in 4 s ≈ 10 kHz. ✅ → **no T0 needed**; time-tag results
  are trustworthy. Tag format = `[channel(1-based), timestamp_ns]`, 100,000 ns spacing.
- **Logic Analyzer:** sec A panel 1 shows the pulse (10 high samples / 2048). ✅

So all three methods provably detect a real signal and the panel→SDK mapping is right.
(Section A was restored to its original `majority` config afterwards; chain re-verified
at 298 Hz.)

## Result on the 3 veto lemos (`.244` section D panel 1,4,5)

Every method, every setting → **zero**:

- **Counter** (panel 1,4 = SDK 0,3), continuous during busy: 0 over 60 s.
- **Counter, toggle-busy test** (toggling the pulse on/off to force busy assert/
  de-assert transitions — catches a *sustained-level* veto that continuous edge
  counting would miss): 0 across NIM / TTL / DISCR ±150 mV / 50 Ω / high-Z.
- **Time Tag** (panel 1,4,5 = SDK 0,3,4), continuous during busy: 0 over 60 s.
- **Logic Analyzer** (reads the actual logic *level* of all inputs, 2048 samples,
  triggered on the pulse so the window sits inside a busy period): panel 1,4,5 =
  **0/2048** at DISCR ±150 both impedances, NIM/50, and low thresholds **±30/50/80 mV
  at 50 Ω** (rules out a small-amplitude veto — these inputs are driven by the TCM, not
  floating).

**Aside:** section D **panel 3** (SDK lemo 2 — the board's reserved veto *input*) reads
all-high *only* at DISCR +150 mV / high-Z, and 0 at 50 Ω → a **floating high-Z pickup
artifact**, not a real signal. Not one of the veto lemos anyway.

## Conclusion

Under confirmed heavy DREAM busy (10 kHz in, 298 Hz accepted), with counter, time-tag
and logic-analyzer all positive-controlled, **none of the three TCM veto-suspect lemos
(panel 1,4,5 into `.244` section D) carries any signal — no edge, no level, at any
polarity/standard/impedance/threshold.** This confirms and strengthens the handoff's
hypothesis: the TCM does not drive a veto/busy output on these lemos (or they are
inputs). What remains is outside the N1081B's reach:

- The **TCM lemo mapping** — needs the missing *TCM User's Manual* (D. Calvet) to know
  which of the 4 lemos are outputs vs inputs and whether any is a veto output at all.
- Whether the veto/busy is exposed on a **different** connector, or only internally
  over the RJ45 TI link to the FEUs (the handoff's dead-time chain), never on a lemo.

## Follow-up: `Feu * Trig_Conf_TrigVetoLen` (per-trigger veto) — also no effect

`Trig_Conf_TrigVetoLen` is a 10-bit field (0–1023, ×10 ns trigger-clock ticks → ≤10.2 µs)
in the FEU TrigGen config register (`EmbededTbMgr/TrigGen.h`, `_Ofs 17 _Len 10`). It
applies a veto window **after every trigger** — a *different* mechanism from the
FIFO-overflow busy (`OvrWrnHwm`/`LocThrot`) tested above. Swept it **0 / 500 / 1023**
(re-editing the cfg + restarting RunCtrl each time, 10 kHz pulse running), reading
section D with the LA triggered on the trigger pulse (its window sits exactly where a
per-trigger veto would appear) + a counter:

- **section D panel 1–6 = 0** at every value, both ±150 mV polarities. **No difference.**
- IntRate stayed ~298 Hz (expected: a ≤10 µs veto is negligible vs the ~3.3 ms readout
  deadtime, so it can't change acceptance — its only observable effect would be an output).
- **The flag really latched**: RunCtrl writes FEU registers with `poket` (write+readback
  verify) and all runs reached data-taking with no verify error, so TrigVetoLen=1023 was
  confirmed programmed into all 8 FEUs — it simply isn't routed to these lemos.

So **neither** FEU veto mechanism (FIFO-overflow busy, or per-trigger TrigVetoLen) drives
the three TCM lemos on `.244` section D.

## N1081B SDK/firmware gotchas learned (for future work)

- `load_configuration_file` → `"missing parameters"` on this fw — **broken**; restore
  configs manually via the setters.
- `delete_configuration_file` returns `Result:true` but does **not** remove the file.
- `set_logic_analyzer_trigger(LA_TRIGGER_OFF, …)` captures nothing (`no_data`). Use
  `LA_TRIGGER_OR` + a real edge (I triggered on `sec1_in1` = the pulse). LA data =
  `{time, inputs[28], outputs}`, 2048 samples/ch; input index = `section*6+(panel-1)`
  for sections A–D (idx 0–23).
- After a Time-Tag stream, the websocket has queued `send_data` packets — **drain or
  reconnect** before issuing other commands or the next reply desyncs (KeyError 'data').

## State reset to baseline (end of session)

- RunCtrl **stopped**. `.244` restored to `majority/counter/or_veto/counter` (section
  D = counter, NIM/50 Ω/th0; section A = majority, verified). `.245` section C pulse gen
  **left ON** at original (`period=100000`, `width=20`), as found (this is the user's
  own pre-existing setup, not a test artifact).
- `~/beam_july/test/veto_test/Tcm_Mx17_July.cfg` reset to match the `test/test2`
  template (`Sys Action PedThrRun 1`, `Trig_Conf_TrigVetoLen 0`). NB: to re-use this
  dir as a quick "make-DREAM-busy" rig, set `PedThrRun 0` and run with `-f None`
  (pedestal analysis fails under `-f None`, so it must be skipped).
- Harmless leftover on `.244`: on-board config file `veto_bak_lunch.json` (delete is a
  no-op on this fw; it's just a valid pre-test snapshot of `.244`).
- Scripts now in `n1081b/`: `veto_monitor.py` (section-D time-tag monitor),
  `level_test.py` (toggle-busy counter sweep), `measure_secD.py` (LA + counter section-D
  probe used for the TrigVetoLen sweep).
