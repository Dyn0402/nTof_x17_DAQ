# HANDOFF 2026-07-22 (evening) — M6 enable layers, the mesh↔SiPM-wall coupling, and what it cost

**Read §1 before touching any N1081B section.** It is the operational lesson and it
invalidated several hours of reasoning, including three conclusions that had already been
written down as fact.

Companion documents: `docs/HANDOFF_2026-07-22_sipm_wall_dropouts.md` (the morning's
diagnosis), `n1081b/CLAUDE.md` rule 3 (the short form), `n1081b/n1081b_module_map.py`
(`_module6`).

---

## 1. TWO ENABLE LAYERS — a signal needs BOTH

Every N1081B section has **two independent enable registers**, and the signal path is
their **AND**:

| layer | read with | write with | who drives it here |
|---|---|---|---|
| per-channel `status` | `get_{input,output}_channel_configuration` | `set_{input,output}_channel_configuration` | `n1081b_scan_watcher` (`mesh_b`, `input_status`/`output_status`), our M6 scripts |
| function `lemo_enables` | `get_function_configuration` | `configure_or`, `configure_or_veto`, … | `trigger_mode.py`, **and the board's web GUI** |

A channel with `status=True` whose lemo is disabled **passes nothing**, and *each side's
readback looks perfectly healthy*. That is the trap.

**For a FANOUT section the SDK cannot write `lemo_enables` at all.** There is no
`FN_FANOUT` in `N1081B.FunctionType` and no `configure_fanout` — verified by inspecting the
SDK. M6.A/B/C are all fanouts, so on those sections the lemo layer is **GUI-only**. It can
be read from a script; it cannot be set from one.

**How this played out (2026-07-22, ~21:00–21:50).** The operator was disabling sections via
the GUI (`lemo_enables`); the assistant was reading and writing per-channel `status`. Both
reported success. The hardware stayed dark. Symptoms that should be recognised instantly
next time:

- SDK reads `status=True` while the GUI shows the same output OFF — **not** a stale read,
  two different registers.
- An SDK `status` write produces a **brief burst of signal downstream and then nothing** —
  the reconfiguration glitch, with the lemo gate still shut behind it.
- Neither side can reproduce the other's change.

**The GUI is 1-based on channels; the SDK is 0-based.** GUI "Output 1" = SDK lemo/ch `0`.
The operator's report that "M4.D **In 1** now sees nothing" referred to the SDK's `in0`,
the PS/flash line.

**Snapshot caveat.** `poll_modules` records *both* layers per sub-run
(`<run>/<subrun>/n1081b_config.json` → `sections[SEC_x].function_configuration.data.lemo_enables`
and `.input_channels` / `.output_channels`). Historic analyses that only looked at
`output_channels` were reading half the state.

---

## 2. M6 (.245) as built — channel counts

**SIX inputs (0–5), FOUR outputs (0–3) per section.** From `n1081b_module_map.py`:
`_in` runs to `range(1, 6)`, `_out` only `range(4)`.

**Reading an out-of-range OUTPUT does not error — the board returns uninitialised junk.**
`SEC_C.out4` reads `mono_value = 16843009` = `0x01010101`, and every section shows an
identical bogus `out4/out5: status=True, mono=False, mono_value=0`. `poll_modules` used a
single `SECTIONS_RANGE = 6` for both loops, so **every archived snapshot up to 2026-07-22
contains two junk output rows per section**. Fixed: `INPUT_RANGE = 6`, `OUTPUT_RANGE = 4`.

| section | function | role |
|---|---|---|
| SEC_A | fanout | PS/T0 fan-out, in0 G&D delay 9600 ns → downstream PS chain (incl. M4.D `in0`) |
| SEC_B | fanout | Micromegas mesh charge-injection, in0 G&D 1260 ns, outs mono 500 ns |
| SEC_C | fanout | SiPM enable / blank, inverted TTL, mono 1000 ns — **map says only out0 and out1 are cabled**, out2/out3 unused |
| SEC_D | pulse_generator | ~667 Hz Poisson test pulser → M4.C in4 |

---

## 3. The mesh ↔ SiPM-wall coupling — what is actually measured

Disabling **M6.B outputs** collapses all four SiPM walls to ~1/40 gain. This kills the wall
leg of the per-sector wall∧plastic Singles coincidence, leaving only the PS/flash trigger:
**~1 event per beam pulse.** Confirmed on two observables, with MM HV pinned throughout.

### DREAM rate (`mesh_toggle_test`, 21:00, 7 × 1-min alternating sub-runs)

Plastic threshold 1.41 MIP and drift 600 / resist 530 held on every sub-run; the only
variable is `mesh_b` (`output_status` on SEC_B out0–3).

| mesh | raw MB/min |
|---|---|
| ON | 275.9, 289.0, 286.1, 282.8 → **mean 283.4** |
| OFF | 4.7, 4.4, 4.8 → **mean 4.6** |

**Ratio 61×.** Earlier single points agree: run_67 `m141On` 174 MB/min (at half beam) vs
`m141Off` 6.2; restored 427 MB/min at 1.13 MIP on full beam.

### n_TOF wall flash vs time since transition — the mechanism

`stream1_monitor/wall_probe.py`, flash amplitude near each stream file's start
(live ≈ 34 000 ADC, collapsed 350–1200):

| since mesh OFF | flash | | since mesh ON | flash |
|---|---|---|---|---|
| 5 s | 34 120 **live** | | 1 s | 34 037 **live** |
| 21 s | 522 dead | | 30 s | 34 282 live |
| 45 s | 552 dead | | 54 s | 34 155 live |
| 69 s | 588 dead | | | |

**Collapse is slow (bracketed 5–21 s); recovery is immediate (<1 s).** That asymmetry is
the signature of a **bias/rail actively maintained by the pulses** — stop pumping and it
bleeds down over seconds; resume and a single pulse restores it. A gate or enable would
switch symmetrically. Consistent with the morning's note that M6.B's outputs feed *"the two
ramp"* generators (`RMPA`/`RMPC` in the n_TOF stream are the amplified ramp trigger).

**To pin the time constant properly:** read deeper into a single mesh-off stream file to
get flash per proton pulse (~3.3 s resolution) instead of one point per file.

### It is NOT the MM HV

Four transitions at identical drift 600 / resist 540, walls following M6.B every time,
including both reversals. Independently, the morning's correlation study found HV flat
across every drift/resist setpoint (within-run r ≈ +0.05). The operator also ramped the MM
HV by hand on 2026-07-22 evening and saw nothing.

---

## 4. What is NOT established

- **SEC_C's role.** Disabling SEC_C's per-channel output `status` (21:24, all four) left the
  walls fully live at 100 s (flash 34 159, full-size 2.96 GB file). **RESOLVED 2026-07-23:
  SEC_C's own output `status` is a DEAD REGISTER — the write did nothing, so this was never
  a test of SEC_C's signal path** (see `docs/HANDOFF_2026-07-23_m6_secBC_control_aliasing.md`
  §3c; C's outputs are gated solely by the same-numbered SEC_B status bit). The two original
  caveats stand independently: the `lemo_enables` layer was never checked, and `wall_probe`
  cannot detect a *blanking* function — the flash rails the walls to the ADC bottom whether
  or not they are blanked.
- **"M6.B fully dead behaves like half-dead."** The 19:55 "full off" cleared inputs 0–3 and
  outputs 0–3 only. Inputs 4/5 were untouched (SEC_B `in4` stayed enabled) and the lemo
  layer was never touched. It was never a full shutdown. **Do not cite this.**
- **Why B couples to the walls at all.** The ramp-generator route is the leading candidate,
  not a demonstrated path. Tracing what M6.B out0–3 physically feed remains open, and is
  still the single most valuable next measurement.

---

## 5. The SEC_C lemo-0 question — ANSWERED (null), operator hypothesis

Snapshots taken while the walls demonstrably worked (`mesh_toggle_test` 20:59:31 and
21:06:43, both with the 61× toggle behaving) record:

| section | lemo_enables ON |
|---|---|
| SEC_A | 0, 1, 2, 3 |
| SEC_B | 0, 1, 2, 3 |
| **SEC_C** | **1, 2, 3 — lemo 0 OFF** |
| SEC_D | 0, 1, 2, 3 |

The module map says SEC_C's **cabled** legs are **out0 and out1** ("SiPM enable ×2"), with
out2/out3 unused. So the long-standing configuration had **one of the two cabled SiPM-enable
lines disabled, while both enabled legs were uncabled spares.**

**The operator deliberately enabled SEC_C lemo 0 on 2026-07-22 ~21:17** (GUI "Output 1"),
on the hypothesis that this missing enable leg was itself a fault contributing to wall
problems. As of 21:49 SEC_C reads `lemo_enables ON = [0,1,2,3]`.

### RESULT — no effect on the mesh↔wall coupling

`mesh_toggle_test2` (21:51, lemo 0 **ON**) vs `mesh_toggle_test` (21:00, lemo 0 **OFF**),
everything else identical:

| run | SEC_C lemo 0 | mean ON | mean OFF | ratio |
|---|---|---|---|---|
| `mesh_toggle_test` | OFF | 283.7 MB/min (n=3) | 4.63 | **61.3×** |
| `mesh_toggle_test2` | ON | 283.0 MB/min (n=4) | 4.58 | **61.8×** |

Identical well inside the sub-run scatter (ON points span 268–300 within one run).
**Enabling SEC_C lemo 0 neither causes nor modulates the wall collapse.** A clean A/B: the
per-sub-run snapshots confirm SEC_C sat at `[1,2,3]` for every sub-run of run 1 and
`[0,1,2,3]` for every sub-run of run 2, with `out0-3 status` True throughout both.

This does **not** clear the leg as a standing defect — see §9, and note that the flash
observable is blind to blanking by construction (§4). It only rules it out as the cause of
the B↔SiPM coupling.

Worth also checking whether it was off *deliberately* at some earlier date; the map's
"2 used" and the observed `[1,2,3]` disagree about *which* two.

---

## 6. Corrections issued on 2026-07-22 — claims previously stated as fact

1. **"`poll_modules` leaves `.245` per-channel state null."** False — a parsing error
   (`.get('data')` called on a dict of per-channel responses). All six boards' channels are
   captured every sub-run.
2. **"Sections have six channels, so tools using four are wrong."** Half wrong: six
   **inputs**, four **outputs**. The original four was correct for outputs. Out-of-range
   output reads return junk (`0x01010101`) rather than erroring.
3. **"The GUI is overwriting SDK writes."** False. Nothing was overwritten; the two sides
   were writing different registers (§1).
4. **"I can see my own SEC_C write, so my reads are live."** Void — the operator had also
   set the same channels to the same value, so the observation was over-determined.
5. **"Taking M6.B fully dead behaves like outputs-only, so it is not a partially-driven
   rail."** Withdrawn (§4).
6. **"M6.C cannot be involved."** Withdrawn (§4).

---

## 7. Tooling added or changed

| file | change |
|---|---|
| `n1081b/CLAUDE.md` | new rule 3 (two enable layers); GUI-as-second-controller + 1-based note under rule 2 |
| `n1081b/poll_modules.py` | `SECTIONS_RANGE = 6` → `INPUT_RANGE = 6` / `OUTPUT_RANGE = 4`; snapshots stop recording junk output rows |
| `n1081b/inspect_m6_sections.py` | **new**, read-only per-channel dump of `.245`; `--in-channels 6` / `--out-channels 4` |
| `n1081b/set_m6_secB_full_off.py` | **new**, SEC_B inputs 0–5 + outputs 0–3 off, snapshot + `--restore` |
| `n1081b/set_m6_secC_sipm_enable.py` | **new**, SEC_C to one input (in0), outputs on; `--outputs-off` diagnostic; snapshot + `--restore` |
| `run_config_mesh_toggle_test.py` | **new**, forced mesh ON/OFF square wave, `RUN_NAME` / `SUBRUN_MIN` env overrides |
| `analyze_mesh_toggle.py` | **new**, correlates DREAM rate + n_TOF flash against mesh state; EOS UTC→local; flash assigned by state at the sample instant, size flagged when it straddles |

**Snapshots** (all restorable with the matching `--restore`):
`n1081b/snapshots/m6_secB_asfound_2026-07-22_19-55-37.json` (mesh ON, pre-full-off),
`…_20-01-28.json` (fully off), `m6_secC_asfound_2026-07-22_21-15-12.json`,
`…_21-24-49.json` (pre-outputs-off).

---

## 8. Next steps, in order of value

1. **Read `mesh_toggle_test2` (21:51) against `mesh_toggle_test` (21:00)** — the direct A/B
   on SEC_C lemo 0. `analyze_mesh_toggle.py --run mesh_toggle_test2`.
2. **Trace what M6.B out0–3 physically feed** besides the mesh — scope the ramp generators.
   This is the actual unknown; everything else is characterisation around it.
3. **Per-pulse flash** from one mesh-off stream file to fit the decay constant (§3).
4. **`wall_probe.READ_BYTES = 2 << 20` is too small** in the current run config — returns
   `verdict: unknown, 0 wall channels`, which is why `stream1_watcher`'s waveform check has
   been silently useless. 8 MB reaches WALA, 24 MB reaches WALA/B/C. Raise it.
5. `stream1_watcher` still throws `EOS listing failed: tuple.index(x): x not in tuple`, and
   n_TOF has rolled to run **224540**.
6. **run_67 is built and ready** (36 sub-runs, mesh ON throughout, ~6.6 h) and has taken
   only one half-beam sub-run. It is the physics run waiting on all of this.

---

## 9. OPERATOR'S CONCLUSION (2026-07-22, ~22:10–22:15) — B drives BOTH the mesh and the SiPMs

> **SUPERSEDED IN PART — 2026-07-23.** See
> `docs/HANDOFF_2026-07-23_m6_secBC_control_aliasing.md`. On M6, **Section C's Out 4 takes
> its NIM/TTL type from Section C but its enable from Section B** (confirmed after a hard
> power cycle). If that aliasing also holds for C's *cabled* Out 1/Out 2, then "disabling
> Sec B's outputs" was cutting the SiPM enable lines directly and **no B→SiPM hardware path
> is implied**. Treat this section as **open**, not established. The operational constraint
> it produced (mesh cannot be toggled without killing the walls) is measured and still holds.


### Believed cabling (operator, high confidence)

- **Micromegas mesh switches → M6 Section B outputs.**
- **The two SiPM enables → M6 Section C, GUI outputs 1 and 2** (= SDK `out0` and `out1`).
  This matches `n1081b_module_map.py::_module6`, which marks only `c_out[0]` and `c_out[1]`
  as cabled ("SiPM enable ×2") with out2/out3 unused.

### What is actually observed

1. **Disabling Section B outputs turns the SiPMs OFF.** Reproduced many times, both
   layers, both directions; quantified as 61× in the DREAM rate (§3) and as a wall-flash
   collapse from ~34 000 to ~500 ADC. Section B is not supposed to be in the SiPM path
   at all.
2. **Delaying Section C's INPUT delays the SiPMs.** Operator measurement. This is a clean
   positive identification that Section C *is* in the SiPM path, and that its timing
   propagates to them — consistent with the believed cabling.

**Both sections affect the SiPMs.** Section C sets their timing; Section B gates whether
they have gain at all. Only the second of those is explained by the documented cabling.
**Mechanism unknown.**

### Reconciling this with §4's "SEC_C outputs off did nothing"

At 21:24 the assistant disabled SEC_C's per-channel output `status` (out0–3) and the walls
stayed fully live (flash 34 159 at 100 s). That is **not** in conflict with the operator's
delay measurement, for two reasons, and the two results together are informative:

- Removing the blank/enable **pulse** does not remove SiPM **gain** — it leaves them
  unblanked, which is a different failure mode from being switched off.
- `wall_probe` measures gamma-flash amplitude, and the flash **rails the walls to the ADC
  bottom whether or not they are blanked**, so that observable is blind to blanking by
  construction (§4). A timing shift, by contrast, is exactly what the operator's scope can
  see and the probe cannot.

So: Section C = timing/blanking (visible on a scope, invisible to the flash probe);
Section B = gain/enable (obvious in both the flash amplitude and the DREAM rate).

### Candidate explanations for the B → SiPM coupling

Not yet discriminated; listed so they can be tested rather than argued about.

1. **Shared rail.** M6.B's pulses maintain a bias/charge-pump rail that the SiPM enable
   chain also depends on. Fits the §3 asymmetry exactly — slow bleed-down (5–21 s), instant
   recovery (<1 s) — which is a pumped supply, not a logic gate.
2. **Fan-out feeding more than the mesh.** The morning's handoff already asks what M6.B
   out0–3 feed "besides the two ramp" generators; `RMPA`/`RMPC` in the n_TOF stream are our
   amplified ramp trigger, so at least part of B's fan-out goes somewhere other than the
   mesh switches.
3. **Miscabling / shared connector.** A B output physically landing on, or coupling into,
   the SiPM enable chain.

### TEST RESULT (operator, 2026-07-22 ~22:15) — B drives BOTH

**Disabling Section B's lemo outputs DOES turn off the MM mesh switch.**

So the believed cabling is **correct** — M6.B really is the mesh-switch source — and B
*additionally* reaches the SiPM chain. **One fan-out feeding two subsystems.** Candidate 3
(miscabling of the mesh leg) is excluded; candidates 1 and 2 survive and are now the same
question: *what else is on M6.B's fan-out, and how does it hold up the SiPM bias?*

Combined with §3's timing asymmetry (5–21 s bleed-down, <1 s recovery), the working picture
is a **shared rail that M6.B's pulses keep pumped**, feeding both the mesh switches and
something the SiPM bias depends on. That is a hardware-tracing job, not a configuration one:
follow M6.B out0–3 physically, including the `RMPA`/`RMPC` ramp generators that the morning's
handoff already flagged as being on that fan-out.

**Operational consequence, and it is a hard constraint:** the mesh cannot be switched off
without switching off the SiPM walls. **There is no mesh ON/OFF axis available to any
wall-dependent (Singles-triggered) run** until this is traced and fixed. The no-mesh control
must come from detectors **B and D**, which are uncabled for mesh injection and therefore a
same-beam, same-HV, full-gain control inside every mesh-ON sub-run.

**Standing decision (2026-07-22 22:15):** run with M6 exactly as it is, mesh ON, and touch
nothing on M6 until 2026-07-23.

### Note on what is already excluded

MM HV (four transitions at fixed drift 600 / resist 540, plus a manual HV ramp by the
operator), and SEC_C lemo 0 (clean A/B: 61.3× with it off, 61.8× with it on — §5).

