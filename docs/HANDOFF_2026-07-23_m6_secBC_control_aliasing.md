# M6 (.245): Section B/C control aliasing — one physical output, two sections' registers

**2026-07-23, morning.** Operator observation, confirmed **after a hard power reboot of the
board this morning** (so it is not leaked session state, not a stale web-GUI cache, and not
a consequence of anything we wrote on 07-22).

Predecessor: `docs/HANDOFF_2026-07-22_m6_enable_layers.md` (two enable layers, and §9's
"B drives both the mesh and the SiPMs" conclusion, which this observation now puts in doubt).

---

## 1. The observation, as reported

Bringing up a **new, previously unused channel on M6 Section C — GUI "Out 4"** (= SDK
`out3`; GUI is 1-based, SDK 0-based):

| property of the physical C Out-4 line | which section's controls actually move it |
|---|---|
| **output type / level — NIM ↔ TTL** | **Section C** (as expected) |
| **invert** — inverts the actual pulse | **Section C** (as expected); B's invert does *not* |
| **enable — on / off** | **Section B**, its "Output 4" |

So a single physical output leg has *all its signal-shaping* registers under the section it
belongs to, and **only its enable bit** under a different section. This is not a wholesale
"the page is mislabelled" swap — two of three properties land where they should.

**That narrows the fault considerably.** A GUI page pointed at the wrong section would move
type, invert and enable together. Instead the split is *per-property*, which points at the
**enable register specifically**, not a mixed-up page. It also means the still-open
GUI-vs-register question (§3) is really: *is the enable bit itself aliased in firmware, or
only in the GUI's write path for that one control?*

### GANGED, not displaced (operator, 2026-07-23, later)

**SEC_B's own output status switches do still turn SEC_B's outputs on and off.** So B did
not *lose* its enable to C. The working model is that **one enable bit gates two physical
outputs at the same index**:

```
B Out N  enable  ──┬──>  B Out N   (works normally)
                   └──>  C Out N   (aliased — the surprise)
```

Not a swap, a **fan-out of the enable bit**. This matters in three ways:

1. The workaround in §3b still holds — see that section.
2. It creates a **standing requirement**: for the SiPM enables on **C Out 1/2** to conduct,
   **B Out 1/2's enables must be left ON**, even though those legs now drive only a scope.
   Anyone "tidying up" by disabling apparently-unused B legs kills the SiPM walls.
3. It cleanly reproduces the 07-22 observation: disabling B out0–3 took C out0/out1 — the
   cabled SiPM enables — down with them.

### Direction: ONE-WAY, B → C (operator, 2026-07-23)

**Setting C Out 1 `status` OFF does not affect B Out 1.** The alias is therefore
**asymmetric** — B's enable reaches C, C's enable does not reach B:

```
B Out N enable ──┬──> B Out N        C Out N enable ──> C Out N (?)
                 └──> C Out N                       ─X─> B Out N  (confirmed NO)
```

This closes the §3 "is it directional?" question: it is. It also means **SEC_B's outputs are
safe from anything done on SEC_C** — B's legs (now mesh A and mesh C) cannot be disturbed by
SEC_C activity, which is a useful guarantee for the re-cabled layout.

### C's own status bit is INERT — ANSWERED (operator, 2026-07-23)

**Section C's output status switches do nothing for Section C's own outputs.** So the model
is *overridden*, not ANDed:

> **A SEC_C output leg is gated ONLY by the same-numbered SEC_B status bit. SEC_C's own
> per-channel output status is a dead register.**

Final picture for the B/C output pair:

| what you set | effect on B Out N | effect on C Out N |
|---|---|---|
| **B Out N status** | on/off (normal) | **on/off (the whole gate)** |
| C Out N status | nothing | **nothing** |
| C Out N type / invert | nothing | works normally |

**Consequences:**

1. **Operational rule simplifies:** only **`B Out 1/2 status = ON`** matters for the cabled
   SiPM enables. C's status is irrelevant in either position — do not spend time on it, and
   do not treat a healthy-looking C readback as evidence of anything.
2. **This de-confounds §4.** Turning SEC_C's output `status` off on 07-22 left the walls fully
   live because **the write did nothing at all** — not because the flash observable is blind
   to blanking. That entry can be closed on the simple explanation.
3. **⚠ Our SEC_C tooling is writing a dead register.**
   `n1081b/set_m6_secC_sipm_enable.py`'s `--outputs-off` is a **no-op**, as is any script
   setting SEC_C output `status`. Anything that appeared to work through that path was
   coincidence or the operator's parallel GUI action. Audit before reuse.
4. **Caveat — this is the `status` layer only.** The separate function `lemo_enables` layer
   (rule 3 in `n1081b/CLAUDE.md`) is untouched by this finding and is **GUI-only for fanout
   sections**. SEC_C's lemo bits may still gate its outputs; nothing here tests that.

**Persists across a hard power cycle.** Confirmed this morning, board fully powered down and
back up before the check.

---

## 2. Why this matters more than it looks

Last night's §9 conclusion was that **M6 Section B reaches the SiPM walls as well as the
Micromegas mesh** — "one fan-out feeding two subsystems, most likely a shared rail its pulses
keep pumped" — with the recommended next step being *hardware tracing* of M6.B out0–3.

The measured facts behind that were:

- disabling **Sec B** outputs collapses all four SiPM walls (61×, flash 34 000 → ~500);
- disabling **Sec B** outputs also genuinely stops the MM mesh switch (22:15 check);
- delaying **Sec C's input** delays the SiPMs, so C is in the SiPM path;
- the SiPM enables are cabled to **Sec C** outs (GUI Out 1 + Out 2 = SDK out0 + out1).

Today's observation supplies a **second, purely control-plane explanation** for the first
bullet: if the enable bits for Section C's outputs are addressed under Section B, then
"disabling Sec B's outputs" was never a mesh-only action — it was **also disabling the SiPM
enable lines**, exactly as the cabling map says those lines belong to C. No shared rail
required.

It also retro-explains a loose end in §4 of the 07-22 doc: turning **Sec C output `status`
off did not collapse the walls**. If the effective enable for those legs lives under B, C's
own `status` register is not the gate, and that null result is exactly what the aliasing
predicts.

**Consequence for the slow-collapse / fast-recovery asymmetry (5–21 s down, <1 s up):** that
observation still stands on its own and is *not* explained by aliasing. It remains evidence
for a pumped bias somewhere in the SiPM chain — but it no longer needs to implicate Section
B's fan-out specifically.

### Status of the 07-22 §9 conclusion

**Not refuted, but no longer supported by its main evidence.** Treat "B physically feeds the
SiPMs" as **open**, and do not cite it. The hard operational constraint it produced —
*mesh cannot be toggled without killing the walls* — is still **empirically true** (measured
61×, twice, both directions) regardless of mechanism, so run planning is unchanged: keep using
dets B/D as the in-run no-mesh control.

---

## 3. What is established vs. what is inferred

**Established (operator, post-reboot):**
- C Out 4's NIM/TTL type follows Section C's control.
- C Out 4's invert follows Section C's control — it inverts the real pulse; B's invert does not.
- C Out 4's enable follows Section B's "Output 4" control.
- ⇒ the aliasing is **confined to the enable bit**, not the whole channel.

**Not established:**
- Whether the same B-holds-the-enable aliasing applies to **C Out 1 and Out 2** — the two
  *cabled* SiPM-enable legs. This is the load-bearing question; today's check was on Out 4,
  an uncabled spare.
- ~~Whether the aliasing is directional or one-way.~~ **ANSWERED: one-way, B → C.** C Out 1
  status OFF leaves B Out 1 unaffected.
- ~~Whether C's own status bit still gates C's own output.~~ **ANSWERED: it does nothing.**
  C Out N is gated solely by B Out N's status; C's own status is a dead register.
- Whether it also affects A and D, or is specific to the B/C pair.
- Whether it is a **GUI-only** presentation bug (wrong section header on the page) or reaches
  the **SDK/register** layer too. Our scripts write via the SDK, so this decides whether every
  M6 script we have is addressing the channel it thinks it is.
- Whether the split follows the *sections* or the *fan-out function* — recall A, B and C are
  all fanouts and, per 07-22 rule 3, the fan-out lemo layer is **GUI-only** (no `FN_FANOUT`,
  no `configure_fanout`), so the SDK can only *read* it.

**Do not assume** the 07-22 `lemo_enables` readbacks map to the sections they are filed
under until the point above is settled. In particular the long-standing
`SEC_C lemo_enables = [1,2,3]` (out0 OFF) may not mean what §5 assumed it meant.

---

## 3b. Operator's workaround, applied same day

The mesh was **physically re-cabled onto SEC_B Out 3/4** (det A / det C) with a scope on
Out 1/2, so that mesh toggling no longer shares an output index with SEC_C's cabled SiPM
enables on C Out 1/2. See `docs/HANDOFF_2026-07-23_m6_mesh_recable_out34.md` — including a
live hazard: `set_mesh_injection.py` and the `mesh_b` scan target still default to all four
SEC_B legs.

---

## 4. Next steps, in order

1. **Repeat today's test on C Out 1 and Out 2** (the cabled SiPM enables). Same protocol:
   toggle type from C, toggle enable from B, confirm on a scope. This is the one that decides
   whether §9 stands or falls.
2. **Determine GUI-only vs. register-level.** With the board idle, read the per-channel
   `status` and function `lemo_enables` for B and C via `n1081b/inspect_m6_sections.py`
   (read-only) immediately after a GUI enable toggle on C Out 4, and see **which section's
   readback moves**. That distinguishes a mislabelled GUI page from genuine register
   aliasing, without writing anything.
3. **Scope-confirm the direction**: does anything on Section C's page move a *Section B*
   channel's property?
4. Only then revisit the ramp-generator tracing from 07-22 §8 step 2 — it may be unnecessary.
5. **Report to CAEN.** If step 2 shows register-level aliasing on a stock board after a cold
   boot, this is a firmware defect worth adding to the existing thread
   (`n1081b/CAEN_email_draft_2026-07-15.md`).

**Board-handling reminder:** M6 is `192.168.10.245`, the board with the older firmware
(`n1081b-sdk-gotchas`). Everything above goes through `n1081b/n1081b_session.py`, one process
at a time, and no SIGKILL mid-session — see `n1081b/CLAUDE.md`.
