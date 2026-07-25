# M6 mesh re-cabling 2026-07-23 — mesh moved to SEC_B Out 3/4 to dodge the enable aliasing

**2026-07-23, operator.** Physical re-cabling of the M6 (`192.168.10.245`) Section B
fan-out, done to work around the Section B/C **enable-bit aliasing** recorded in
`docs/HANDOFF_2026-07-23_m6_secBC_control_aliasing.md`.

---

## 1. As-built after the change

Operator numbering is the **GUI's, 1-based**; SDK indices are 0-based (GUI "Out 3" = SDK `out2`).

| M6 SEC_B leg (GUI) | SDK index | now connected to |
|---|---|---|
| Out 1 | `out0` | **oscilloscope** (monitor only) |
| Out 2 | `out1` | **oscilloscope** (monitor only) |
| **Out 3** | **`out2`** | **Micromegas mesh circuit — detector A** |
| **Out 4** | **`out3`** | **Micromegas mesh circuit — detector C** |

**Detectors B and D mesh circuits are UNPLUGGED** (unchanged in spirit from the 07-21
"A+C cabled only" state, but now on different legs).

SEC_C is unchanged: the two cabled SiPM enable / blank legs remain **C Out 1 + Out 2**
(SDK `out0` + `out1`).

**Previous state (superseded):** mesh injection was spread across SEC_B `out0-3`, with A+C
cabled — see memory `mesh-off-run66` / `run64-mesh-injection-result`. Any analysis or
snapshot from before 2026-07-23 refers to that layout.

---

## 2. The rationale — why this should defeat the cross-talk

The measured aliasing is that a Section C output's **enable bit** is driven from the
**same-numbered** Section B output control (C Out 4's enable follows B Out 4). **B's own
outputs still respond to their status switches too** — so the enable is *ganged*, one bit
gating both `B Out N` and `C Out N`, not moved from one to the other. Taking that rule as
index-preserving:

| control touched | aliased enable it also moves | cabled to |
|---|---|---|
| B Out 1 / Out 2 | **C Out 1 / Out 2** | **the two SiPM enables** ← danger |
| B Out 3 / Out 4 | C Out 3 / Out 4 | nothing (uncabled spares) |

By moving the mesh onto **B Out 3/4**, per-sub-run mesh toggling drives B Out 3/4 normally
(which is what the mesh needs) and only aliases onto **uncabled** C legs — harmless. The SiPM
enables on C Out 1/2 are ganged to B Out 1/2, which after this change carry only a scope and
are never toggled during a run. **The two subsystems no longer share an output index, so
neither can gate the other.**

⚠ **Standing requirement from the ganged model: B Out 1 and Out 2 must be left ENABLED.**
Their enable bits are what let the SiPM enables on C Out 1/2 conduct. They look like unused
scope-monitor legs, so the failure mode is someone disabling them as housekeeping — and the
symptom would be the full 61× wall collapse, indistinguishable from the 07-22 fault.

If the aliasing hypothesis is right, this **restores a real mesh ON/OFF axis** — the hard
constraint from 07-22 ("mesh cannot be switched off without killing the SiPM walls", which
forced dets B/D to serve as the in-run no-mesh control) would be lifted. **That is a
prediction, not a result.** It must be verified before any run plan depends on it.

---

## 3. ⚠ IMMEDIATE HAZARD — existing tooling still drives all four legs

Two code paths default to **all of SEC_B out0–3**, which now includes the two legs whose
aliases gate the SiPM enables:

| where | what |
|---|---|
| `n1081b/set_mesh_injection.py:44` | `OUT_CHS = (0, 1, 2, 3)` — the default for both `on` and `off` |
| `n1081b/n1081b_module_map.py:392` | `"scan_targets": {"mesh_b": ("B", None)}` — `None` = all four legs, used by `scan_control` / `scan_watcher` per sub-run |

**Under the aliasing rule, `set_mesh_injection.py off` would disable C Out 1/Out 2's enables
= the SiPM enables.** That is the exact failure being routed around, and it would look
identical to the 07-22 wall collapse.

**Until these defaults are changed, all mesh work must pass `--outputs 2 3` explicitly**
(SDK indices for GUI Out 3/4).

Proposed fixes (**not yet applied — awaiting operator go-ahead**, since both touch live
run configuration):
1. `set_mesh_injection.py`: `OUT_CHS = (2, 3)`, with the docstring updated to name A and C.
2. `n1081b_module_map.py`: `"mesh_b": ("B", [2, 3])` — and confirm `scan_control` honours an
   explicit channel list for a scan target rather than only `None`.
3. Module-map SEC_B/SEC_C `_out(...)` descriptors: legs 0/1 → scope monitor, 2/3 → mesh A / mesh C.

Also note `run_config_mesh_toggle_test.py` and any run config carrying `mesh_b` schedule tags
inherit whatever the map says.

---

## 4. Verification to run before trusting this

1. **Scope check, board idle:** toggle **B Out 3** enable; confirm the mesh-A pulse appears
   and disappears on the scope, and that **C Out 1/Out 2** are untouched.
2. **The decisive one:** toggle **B Out 1** enable and confirm it moves **C Out 1's** enable
   (this both confirms the aliasing rule is index-preserving *and* confirms the two cabled
   SiPM legs are affected — the load-bearing item still open from the aliasing handoff §3).
   Direction is already settled: the alias is **one-way B → C** (C Out 1 status OFF does not
   affect B Out 1), and **SEC_C's own output status is a dead register** — C Out N is gated
   *solely* by B Out N. So **SEC_B's mesh legs cannot be disturbed by anything done on
   SEC_C**, and the only bit that matters for the SiPM enables is `B Out 1/2 status`.
3. **Then a live mesh toggle**: run the `mesh_toggle_test` A/B again with `--outputs 2 3`.
   Prediction under the aliasing model: the **61× wall collapse disappears**, DREAM rate stays
   ~280 MB/min in both mesh states, and `wall_probe` flash stays ~34 000 throughout. If the
   walls still collapse, the aliasing explanation is wrong and 07-22 §9's shared-rail
   hypothesis comes back into play.

Board-handling rules unchanged: `n1081b/n1081b_session.py` only, one process at a time, close
the web GUI tab first, no SIGKILL — `n1081b/CLAUDE.md`.
