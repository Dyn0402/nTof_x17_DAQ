# HANDOFF 2026-07-31 — Putting Detector E (det4) into the banco/P2 DREAM crate at the SPS

**Goal.** Add one of our MX17 chambers — **Detector E = det4 = `mx17_4`**, the chamber
left out of the n_TOF experiment — to the banco-run DREAM crate in the SPS H4 line, on
**cfg FEU 3, all 8 Dream connectors** (X3-X6 on Dreams 0-3, Y3-Y6 on Dreams 4-7), read
out **un-suppressed (raw waveforms)**, physically between the last P2 station and the
back uRWELL reference. Then teach banco's disk tooling to delete *our FEU's* files as
soon as they are on EOS, so our volume does not sink their run.

> **Cabling fixed 2026-07-31 (Dylan): FEU 3, Dreams 0-3 = X3/X4/X5/X6, Dreams 4-7 =
> Y3/Y4/Y5/Y6.** That is a **full FEU — 512 channels, no masked Dreams**. It widens the
> instrumented window to 99.8-298.7 mm (both good live bands, with margin — §2) and it
> puts the un-prescaled wire rate **at 102 % of the per-FEU link ceiling** (§6).
> `Feu_PreScale_EvtData` is therefore **mandatory**, not a tuning option.

**Everything below was read off banco read-only on 2026-07-31 (~18:30).** Nothing was
changed. Citations are `file:line` into `banco:~/DAQ_Control_Dream_Beam` (their fork of
this repo) or into this repo. Things I inferred rather than measured are marked
⚠ UNVERIFIED. Open decisions for Dylan are collected in §11.

Companion document: `docs/HANDOFF_2026-07-31_shared_crate_zs_thresholds.md` — the
per-FEU/per-channel ZS threshold mechanism. This document is the concrete application of
it to a crate that actually exists, with measured numbers instead of estimates.

---

## 1. What banco is running right now

Their repo is our repo, forked. `daq_control.py`, `dream_daq_control.py`,
`run_config_beam.py`, `backup_watcher.py`, `processor_watcher.py`,
`flask_app/space_manager.py` are all recognisably the same files, which is why almost
everything here is a small edit rather than new code.

- Repo: `~/DAQ_Control_Dream_Beam`, branch `qa-watcher-performance-fixes`, head `5c7a377`.
- Site: `DAQ_SITE=sps` → `BASE_DATA_DIR=/local/home/banco/P2_data/TB_July2026_H4/`
  (`run_config_beam.py:81-99`).
- DREAM cfg template: `<BASE_DATA_DIR>dream_config/P2B_Beam.cfg`, selected by
  `TRIGGER_MODE=external` (`run_config_beam.py:44,64,109`).
- CAEN mainframe `192.168.10.199`, 12-ch cards in **slots 8 and 12 only**
  (`run_config_beam.py:88-91`).
- EOS primary: `/eos/experiment/ntof/data/x17/p2_sps_july/` via `root://eospublic.cern.ch`
  (`config/backup_config.json`) — note this is already inside the n_TOF x17 EOS tree.

### The readout is one FEU

As of **2026-07-30** the three P2 stations were recabled onto the **VMM** DAQ, so their
DREAM FEUs sit on disconnected connectors and were dropped from the readout
(`4167db6`, `run_config_beam.py:919-940`). Left in, each wrote ~430 MB/min of empty
events.

```python
self.included_detectors = ['EIC_uRWELL_front', 'EIC_uRWELL_back']   # readout = cfg FEU 1 only
```

Both uRWELL references share **cfg FEU 1 (Id 68, 192.168.10.80)**: front on Dream
connectors 1-4, back on 5-8 (`run_config_beam.py:944,966-984`). All eight uRWELL Dream
connectors are cabled `inverted`.

Their HV is still ramped by banco even though VMM reads them out — `_operating_hvs()`
walks `DET_HV`, not `included_detectors`.

### `P2B_Beam.cfg` — the values that matter, next to ours

| | banco (`P2B_Beam.cfg`) | us (`Tcm_Mx17_July_ZS.cfg`) |
|---|---|---|
| `Sys NbOfSamples` | **16** | **400** |
| sample period (`DrmClk WrClk_Div`) | 6.0 → **60 ns** | 2.0 → **20 ns** |
| readout window | **0.96 µs** | **8 µs** |
| read clock (`RdClk_Div`) | 6.0 → 16.7 MHz | 4.0 → 25 MHz |
| `Sys DaqRun Mode` | **ZS** | Raw |
| `Feu_RunCtrl_ZS / ZsTyp / ZsChkSmp` | 1 / 1 / 1 | 0 / 1 / 1 |
| `Feu_RunCtrl_Pd / CM / CmOffset` | 1 / 1 / 256 | 1 / 1 / 256 |
| `Feu_InterPacket_Delay` | **1** | **100** |
| trigger latency (`Dream * 12`) | 0x0020 = **32** | 0x0018 = 24 |
| watermarks Lwm/Hwm/Thresh | 36 / 40 / 48 | 16 / 20 / 28 |
| `Sys PedRun Threshold` | 5.00 σ | 5 σ |
| `Sys DaqRun FileSize` | 1000 MB | 200 MB |
| `UdpChan_MultiPackThr` | 4888 | 4888 |

`Feu * Feu_RunCtrl_PdFile/ZsFile None` in the template; each run programs fresh
pedestals/thresholds (`get_pedestals`, `dream_daq_control.py:914`).

**Two of these are the whole story for us.** `Sys NbOfSamples` is a `Sys`-level key —
it is global, one value for the crate, and it is **16** where we are used to 400. And
their sample period is 60 ns where ours is 20 ns. So Detector E in this crate gets a
**0.96 µs, 16-sample window**, not our 8 µs one. That is a real physics constraint, not
a configuration detail — see §11 Q1.

*(Aside worth passing to them: our 07-23 optimisation found `RdClk_Div` 6.0 → 4.0 is a
free 1.5× on accepted readout rate with the window untouched — `docs/CLOCK_RATE_SCAN_2026-07-23.md`.
They are still at 6.0. Not our call, but it is free rate for their run too.)*

### Measured rate and event size — the baseline everything else scales from

From `runs/run_33/driftscan_gap150V/` (a real 12-min beam subrun, 2026-07-31 16:13):

| quantity | value |
|---|---|
| events | 3,366,699 in 724.4 s |
| sustained trigger rate | **4676 Hz** (`RunCtrl ... IntRate= 4675.93Hz`) |
| FDF bytes, one FEU | 1,286,347,970 B |
| **per event per FEU** | **382 B** |
| → data rate | 1.79 MB/s, ~6.4 GB/h |

382 B/event is what ZS at their operating point buys them. Hold onto that number; §6
is about what happens when we put an un-suppressed FEU next to it.

---

## 2. Detector E = det4 = `mx17_4` — and the thing that decides the cabling

The letter is already in use in the analysis repo:
`nTof_x17/common/mx17_active_area.py:70-77` maps `mx17_2`=B(det2), `mx17_3`=A(det3),
**`mx17_4`=E(det4)**, `mx17_6`=C(det6), `mx17_7`=D(det7). Our `run_config_beam.py`
aliases follow the same scheme (`mx17_A`→`mx17_3`, …), so **Detector E takes
`'alias': 'mx17_4'`** and nothing has to be renamed anywhere.

### ⚠ det4 is 62 % dead, in stripes, and that dictates which connectors to plug

`nTof_x17/mx_june_cosmic_qa/det4_sps_assessment/DET4_SPS_ASSESSMENT.md` (2026-07-31) is
the assessment of exactly this question. Headline:

> det4 works, and it is not "low gain". **62 % of its area does not amplify at all, in
> fixed stripes**; the remaining 38 % is a normal detector (77 % efficient, 0.59 mm, 2.1°).
> No voltage fixes the dead area, and it is not the readout — the pedestals are flat
> across the dead bands.

- 11-12 live bands, median width 12 mm, median spacing 35 mm, **irregular**.
- Structure is one-dimensional: efficiency swings 0 → 98 % with detector-local **X**,
  and is **flat in Y**.
- Stable in time (same stripes, same X, 24 h apart).
- The **best band is detector-local X 177–215 mm** (38 mm wide × the full 360 mm of Y):
  82 % within 5 mm, 90 % excluding discharges, det3-like cluster sizes, 0.62 mm, ~2°.
- For an 80 mm beam spot the assessment's recommended target is **X ≈ 146–215 mm**
  (69 mm, two live bands with a ~10 mm notch), best spot centre **X ≈ 183 mm**.

**This is why the connector choice is not arbitrary.** We instrument a square window on a
chamber that is mostly dead. Put it in the wrong place and we record noise.

### The connectors 3-6 window, computed

From `nTof_x17/mx17_m1_map.csv` (512 strips/plane, 8 connectors × 64 channels, 0.78 mm
pitch), connectors map to detector-local position **sequentially**:

| connector | channel_num | position | cabled? |
|---|---|---|---|
| 1 | 0-63 | 0.0 – 49.1 mm | — |
| 2 | 64-127 | 49.9 – 99.1 mm | — |
| **3** | **128-191** | **99.8 – 149.0 mm** | **✓ Dream 0 / 4** |
| **4** | **192-255** | **149.8 – 198.9 mm** | **✓ Dream 1 / 5** |
| **5** | **256-319** | **199.7 – 248.8 mm** | **✓ Dream 2 / 6** |
| **6** | **320-383** | **249.6 – 298.7 mm** | **✓ Dream 3 / 7** |
| 7 | 384-447 | 299.5 – 348.7 mm | — |
| 8 | 448-511 | 349.4 – 398.6 mm | — |

So **connectors 3-6 = 99.8 – 298.7 mm**, a 199 × 199 mm square centred on the chamber
(the metallised square is 398.6 mm, so this is the middle half in each view).

Against all three of the assessment's candidate bands (`DET4_SPS_ASSESSMENT.md:§3b`):

| band | width | within 5 mm | in our window? |
|---|---|---|---|
| X 146–165 | 19 mm | 80.0 % (highest charge in the chamber, best angles) | **fully** ✓ |
| **X 177–215** | 38 mm | **82.2 %** — "the band to use" | **fully** ✓ |
| X 355–398 | 43 mm | 79.7 % — **"the one to avoid"** (2× worse angular resolution, efficiency partly built on near-noise hits) | outside — correctly not cabled ✓ |

- Recommended 8 cm-spot target **X 146–215**: **fully covered**, with 46 mm of margin
  below and 84 mm above. (The earlier 4-connector plan lost the bottom 3.8 mm.)
- Best spot centre X ≈ 183 mm sits 83 mm from the lower edge, 116 mm from the upper.
- Going out to connectors 7-8 would only have added the band we were told to avoid.

**Conclusion: connectors 3-6 in both views is the right window**, and it is right for a
specific measured reason, not by symmetry. Record that reason in the cfg comment so
nobody "tidies" it to connectors 1-4 later.

### ⚠ The axis convention must be checked before cabling — this is the one that bites

Two conventions in play and they disagree on their face:

- `run_config_beam.py:615,623` (this repo): `x_1` … *"Runs along x direction, indicates
  **y** hit location"*; `y_1` … *"indicates **x** hit location"*. i.e. a connector is
  named for the direction its strips **run**, so it measures the *other* coordinate.
- `mx17_m1_map.csv`: rows with `axis=x` carry a varying `x_position_mm` and
  `y_position_mm=0`. i.e. a connector is named for the coordinate it **measures**.

det4's stripes vary with detector-local **X** and are flat in **Y**. So *whichever plane
resolves X* is the one that must sit on 149.8–248.8 mm. Under the map's convention that
is the `axis=x` connectors; under `run_config_beam.py`'s comment it is the `y_` ones.

Because we are plugging connectors 3-6 in **both** planes, the window is the same square
either way and **the choice of connectors is safe under both conventions.** What is *not*
safe is any later claim about which reconstructed coordinate is which. Resolve it before
the first analysis pass — §7 checklist item 6.

The wider window also buys insurance here: at 199 mm per side, even a whole-connector
error in the registration (49 mm) still leaves the X 177–215 band inside the acceptance.

---

## 3. Which FEU

TCM inventory from `dream_config/RackTcm.cfg:64-103` (cfg `Feu N` = TCM input port N):

| cfg Feu | Id | IP | state |
|---|---|---|---|
| 1 | 68 | 192.168.10.80 | **in use** — both uRWELL references |
| 2 | 120 | 192.168.10.132 | commented out in RackTcm; ⚠ physically present? |
| 3 | 101 | 192.168.10.113 | **free** — was P2_IN, moved to VMM 07-30 |
| 4 | 102 | 192.168.10.114 | **free** — was P2_MID |
| 5 | 103 | 192.168.10.115 | **free** — was P2_OUT |

**DECIDED (2026-07-31): cfg FEU 3, Id 101, 192.168.10.113.** The reasons it was the right
pick:

- Its `Feu 3 Feu_RunCtrl_Id` / `NetChan_Ip` / `Sys Topo Feu 3` blocks **already exist and
  are already active** in `P2B_Beam.cfg` — the only reason it is currently dark is that
  `included_detectors` no longer names a detector on it, so `set_active_feus` comments it
  out per run (`dream_daq_control.py:760-830`). Adding Detector E to
  `included_detectors` turns it straight back on. **Zero template edits needed.**
- Its TCM input cable is presumably still plugged (it was reading P2_IN until 07-30).
- FEU 2 would need new RackTcm/template lines *and* a physically installed board.

`set_active_feus` rewrites each Dream's role on an active Topo line to `Dat` if its
connector is used and `Msk` otherwise (`dream_daq_control.py:794-799`), with
`CONNECTOR_DREAM_OFFSET = 1` so **connector = Dream index + 1**
(`dream_daq_control.py:749-750`).

**With all 8 connectors cabled there is nothing to mask** — every Dream is `Dat`, the FEU
reads its full **512 channels**, and none of the halving assumed by an earlier draft of
this document applies. That is the single fact driving §6.

| Dream | FEU connector | detector connector | window |
|---|---|---|---|
| 0 | 1 | X3 | 99.8 – 149.0 mm |
| 1 | 2 | X4 | 149.8 – 198.9 mm |
| 2 | 3 | X5 | 199.7 – 248.8 mm |
| 3 | 4 | X6 | 249.6 – 298.7 mm |
| 4 | 5 | Y3 | 99.8 – 149.0 mm |
| 5 | 6 | Y4 | 149.8 – 198.9 mm |
| 6 | 7 | Y5 | 199.7 – 248.8 mm |
| 7 | 8 | Y6 | 249.6 – 298.7 mm |

---

## 4. Geometry and HV

### Position

Beam order and z, `run_config_beam.py:476-482` (confirmed at the beam 2026-07-22):

```
EIC_uRWELL_front    0.0 mm
P2_IN             320.0
P2_MID            630.0
P2_OUT            940.0        <-- Detector E goes in this 430 mm gap
EIC_uRWELL_back  1370.0
```

"Between the back uRWELL and the last P2" is the **940 → 1370 mm** gap. Suggest
**`DET_Z_MM['mx17_E'] = 1155.0`** (mid-gap) as a placeholder — **it must be surveyed**,
the gap is 43 cm and a tracking residual is only as good as this number. Note the
existing entries also carry a `TODO-SPS: survey the transverse x/y offsets`, still open.

### HV

`DET_HV` (`run_config_beam.py:488-494`) — card 8 is **full** (channels 0-7). Card 12 has
only channels 0 and 1 used (the two uRWELL resistive layers), and it is a 12-channel
card, so **channels 2-11 are free**.

Detector E is an MX17 resistive-**strip** chamber and needs two channels, `drift` +
`resist`:

```python
'mx17_E': {'drift': (12, 2), 'resist': (12, 3)},
```

⚠ Verify card 12's per-channel voltage ceiling reaches our drift setpoint before
trusting this — it currently only ever supplies 420 V (uRWELL resist), while det4's
drift wants 600 V and the fleet runs drift up to 700 V.

**Operating point from the cosmic bench** (`DET4_SPS_ASSESSMENT.md:25`, run `g_det4` =
`mx17_det4_day_6-24-26/long_run`): **Ar/isobutane 95/5, resist 495 V, drift 600 V.**

Two caveats from the assessment:
- 495 V sits **~15 % below the gain peak at 505–510 V**, and inside the good band det4 is
  **discharge**-limited (8.6 %), not gain-limited — so pushing up costs sparks. The
  operating point is worth re-optimising *in the band* (`DET4_SPS_ASSESSMENT.md:§4`).
- det4 **has only ever run at drift 600 V**; 700 V is untested on this chamber.

**⚠ Gas is an open problem.** det4 ran on Ar/isobutane 95/5. The P2/uRWELL setup has its
own gas system and its own mixture. A separate line, a separate bottle, or running det4
on their mixture (with an unmeasured gain shift) are three different answers. §11 Q3.

---

## 5. The `run_config_beam.py` edit (on banco)

Four edits, all in `banco:~/DAQ_Control_Dream_Beam/run_config_beam.py`. Copy the field
set from **our** `mx17_D` block (`nTof_x17_DAQ/run_config_beam.py:590-680`) so the JSON
that reaches the analysis has the same shape as every other MX17 chamber.

**(a) `DET_Z_MM`** — add `'mx17_E': 1155.0,` after `P2_OUT` (survey it).

**(b) `DET_HV`** — add `'mx17_E': {'drift': (12, 2), 'resist': (12, 3)},`.

**(c) `OPERATING_HV`** (and `MAX_HV`) — add
`'mx17_E': {'drift': 600, 'resist': 495},` and a `MAX_HV` ceiling. Keep Detector E out
of `BEAM_DRIFT_SCAN_DETS` / `BEAM_SCAN_DETS` / `BEAM_2D_SCAN_DETS` unless we actually
want it scanned; the scan builders assert every scanned detector against `MAX_HV`
(`run_config_beam.py:705-770`).

**(d) `included_detectors` + the detector dict.** Add `'mx17_E'` to the list
(`run_config_beam.py:940`) — this is the single line that turns FEU 3 on — and add the
detector, in the same style as their `_urwell()` helper:

```python
# Detector E (det4, mx17_4) — our MX17 chamber, cfg Feu 3 (Id 101, .113), the
# FEU freed when P2_IN moved to the VMM DAQ on 2026-07-30.
#
# Cabled 2026-07-31: Dreams 0-3 = X3/X4/X5/X6, Dreams 4-7 = Y3/Y4/Y5/Y6.
# CONNECTOR_DREAM_OFFSET = 1, so FEU connector = Dream index + 1.
#
# The MIDDLE FOUR connectors of each view, not connectors 1-4. det4 has ~62%
# dead area in fixed stripes along detector-local X
# (nTof_x17/mx_june_cosmic_qa/det4_sps_assessment/DET4_SPS_ASSESSMENT.md).
# Connectors 3-6 cover 99.8-298.7 mm, which contains BOTH usable live bands
# (X 146-165 and X 177-215, the latter "the band to use" at 82% efficient) and
# the whole recommended 80 mm beam target X 146-215. The widest-looking band,
# X 355-398, is the one the assessment says to AVOID — correctly not cabled.
# Do not "tidy" this to connectors 1-4.
#
# All 8 Dreams are Dat: 512 channels, nothing masked. See the handoff §6 —
# un-prescaled this FEU alone is ~102% of the per-FEU link ceiling.
_detE_feus = {
    'x_3': (3, 1),   # Dream 0
    'x_4': (3, 2),   # Dream 1
    'x_5': (3, 3),   # Dream 2
    'x_6': (3, 4),   # Dream 3
    'y_3': (3, 5),   # Dream 4
    'y_4': (3, 6),   # Dream 5
    'y_5': (3, 7),   # Dream 6
    'y_6': (3, 8),   # Dream 7
}
_detE_orient = {k: 'inverted' for k in _detE_feus}   # ⚠ CONFIRM against the plugs
```

```python
{
    'name': 'mx17_E',
    'alias': 'mx17_4',
    'description': 'MX17 det4, the chamber left out of n_TOF. cfg Feu 3 '
                   '(Id 101). X3-X6 = Dream 0-3, Y3-Y6 = Dream 4-7; '
                   'instrumented window is detector-local 99.8-298.7 mm in '
                   'both views.',
    'det_type': 'mx17',
    'resist_type': 'strip',
    'drift_gap': '30 mm',
    'frame_type': 'aluminum',      # ⚠ confirm for det4
    'det_center_coords': {'x': 0, 'y': 0, 'z': DET_Z_MM['mx17_E']},
    'det_orientation': {'x': 0, 'y': 0, 'z': 0},
    'hv_channels': DET_HV['mx17_E'],
    'dream_feus': _detE_feus,
    'dream_feu_orientation': _detE_orient,
},
```

Two consequences of adding the name to `included_detectors`, both intended:

- `get_active_feu_connectors` → `included_feus` gains FEU 3, so the cfg readout gains it
  (`run_config_beam.py:1066-1067`).
- `run_config_pedestals.py` imports the same list: **included detectors get the 200 V
  pedestal bias**, excluded ones get 0 V. So Detector E is biased during pedestal runs
  automatically.

**Pedestals must be retaken** after this — the FEU set changes, and their own comment
says so (`run_config_beam.py:937-939`).

---

## 6. Raw waveforms on our FEU — the mechanism, and what it costs

### The mechanism (from the companion ZS handoff, unchanged)

`Sys DaqRun Mode` is global and stays **ZS**. The ZS threshold is stored **per FEU and
per channel** (`_thr.prg`, 8 Dreams × 64 ch, 12-bit). **Set our FEU's 512 thresholds to
0** and every channel survives suppression unconditionally: TPC-mode ZS (`ZsTyp=1`) keeps
a crossing channel's whole waveform, so thr = 0 reproduces **Raw content** inside a ZS
run. Same mechanism as the tracer channels, which sit at 0 and appear in 100 % of events.

Banco already has both tools: `dream_scripts/dream_threshold_manager.py` (identical to
ours) and `dream_scripts/generate_zs_test_thresholds.py`, which already flat-sets every
`.prg` in a pedestal directory. The latter needs one change — **restrict it to our FEU's
files** rather than all of them:

```python
# Their prg naming: EicP2Bt_pedestals_pedthr_<date>_<time>_000_<NN>_thr.prg
# where NN is the cfg FEU number (verified: files _01/_03/_04/_05 exist in
# pedestals_07-29-26_10-01-47/pedestals/, matching cfg Feu 1/3/4/5).
OUR_FEU = 3
for fname in os.listdir(prg_dir):
    if not re.search(rf'_{OUR_FEU:02d}_thr\.prg$', fname):
        continue                      # P2/uRWELL thresholds untouched
    mgr = DreamThresholdManager()
    mgr.read_prg(os.path.join(prg_dir, fname))
    for d in range(8):
        for c in range(64):
            mgr.set_threshold(d, c, 0)
    mgr.write_prg(os.path.join(prg_dir, fname))
```

Leave every `_ped.prg` **verbatim** — the firmware still wants the baselines as the ZS
reference. Leave the uRWELL FEU's `_thr.prg` alone entirely.

`get_pedestals` scrapes the FEU number out of the source filename with
`re.search(r'_(\d{2})_(?:ped|thr)\.prg$', file)` (`dream_daq_control.py:977`), so as long
as the `_NN_` is right the file lands on the right hardware. **That bare 2-digit regex is
the only thing binding a threshold set to a board** — get it wrong and we flood the
uRWELL link while suppressing ourselves, and neither side notices until offline decoding.
Verify on hardware, not on filenames (§7).

### What it costs — measured baseline, computed load

Our FEU: **8 connectors = 8 Dreams = 512 channels**, at the crate's global
`NbOfSamples 16`.

```
payload            512 ch x 16 samples x 2 B     = 16,384 B/event
+ ~10% ZS-format per-channel framing             ≈ 18,000 B/event  (17.6 kB)
```

| | banco's FEU 1 (ZS) | Detector E (thr = 0) | ratio |
|---|---|---|---|
| bytes/event/FEU | **382 B** (measured) | ~17.6 kB (computed) | **~47×** |
| at 4676 Hz | 1.79 MB/s | **~84.3 MB/s** | |
| per hour | 6.4 GB/h | **~303 GB/h** | |

Context for those numbers — and this is the part that changed when the cabling went to
8 connectors:

- **Per-FEU link ceiling is 83 MB/s** (memory `feu-per-link-83MBs-ceiling`), invariant
  over event size and FEU count. **84.3 MB/s is 102 % of it.** Un-prescaled, this FEU
  does not fit on its own link. It will assert OverflowWarning → FEU BUSY → **the TCM
  stops sending triggers → banco loses exactly the triggers we do.**
- **banco's free disk is 234 GB** (938 GB nvme, 74 % used). At 303 GB/h that is **46
  minutes** to full with no pruning.
- Deadtime is *not* the binding constraint. `1 µs × IPD × (NbOfSamples/32)` at
  NbOfSamples 16 is `0.5 µs × IPD`; even IPD 20 gives a 100 kHz ceiling against a 4.7 kHz
  trigger rate. Our n_TOF "raw needs IPD ≥ 75" rule came from 400-sample events and does
  **not** transfer.

So the arithmetic no longer says "tight but survivable" — it says **this configuration
cannot run un-prescaled.** That is not an argument against the cabling: 8 connectors is
the right window (§2), and the fix is a per-FEU register, not a cable.

### The levers — one of them is now mandatory

`Feu_InterPacket_Delay`, `Feu_PreScale_EvtData` and the threshold are all **per-FEU**, so
each can be set on ours without touching banco's.

1. **`Feu 3 Feu_PreScale_EvtData N`** — records only every Nth event **on our FEU alone**
   (Prescale reg `0x200018`, FEU manual §3.2.7;
   `docs/HANDOFF_2026-07-23_dream_config_optimization.md:92`). **This is now load-bearing,
   not a safety valve.** banco's trigger rate is unaffected either way.

   | N | our wire rate | per hour | recorded | events/hour |
   |---|---|---|---|---|
   | 1 | 84.3 MB/s ❌ over ceiling | 303 GB/h | 4676 Hz | 16.8 M |
   | 5 | 16.9 MB/s | 61 GB/h | 935 Hz | 3.37 M |
   | **10** | **8.4 MB/s** | **30 GB/h** | **468 Hz** | **1.68 M** |
   | 20 | 4.2 MB/s | 15 GB/h | 234 Hz | 0.84 M |
   | 25 | 3.4 MB/s | 12 GB/h | 187 Hz | 0.67 M |

2. **`Feu 3 Feu_InterPacket_Delay`** — raise from the wildcard's 1. Paces our UDP burst so
   we do not overrun the host buffer; at 17.6 kB/event we now send ~4 packets per event
   (`MultiPackThr 4888`) instead of ~2. Start ~10-20 and measure; there is huge deadtime
   headroom (above), so this is nearly free. Prescale lowers the *duty cycle* of the burst
   but not its instantaneous shape, so IPD still matters.
3. **Non-zero k on some channels** — thr = 0 on connectors 4-5 in both views (which carry
   both live bands) and a finite k on connectors 3 and 6. Same file, per channel. Keeps
   our FEU in **every** event — the fallback if prescale turns out to break the combiner.
   Halves the volume, which is not enough on its own: it still leaves ~42 MB/s, so it has
   to be combined with a modest prescale (N ≈ 5) rather than replace it.

**Recommend: thr = 0 on all 512 channels + `Feu_PreScale_EvtData 10` + IPD ~15.** 8.4 MB/s
is 10 % of the link ceiling and 30 GB/h against 234 GB of disk, with the pruner keeping up
comfortably. 468 Hz of raw det-E events is **1.68 M/hour** — for a chamber whose entire
June cosmic characterisation ran on 12.9 k clean rays, that is not a statistics
compromise in any sense that matters.

**N is the number to fix first**, because §8 shows it also has to satisfy
`N ≥ 84.3 / measured_EOS_MB_per_s`.

### ⚠ Three risks with this, all UNVERIFIED

- **FEU BUSY throttles the shared TCM.** If our FEU asserts OverflowWarning it goes BUSY,
  the TCM stops sending triggers, and **banco loses the same triggers we do.** Their
  watermarks are 36/40/48. With 8 connectors this is no longer a tail risk — un-prescaled
  we are *over* the link ceiling and it is the expected outcome. It is the single failure
  mode that turns our detector into their problem: watch their `IntRate` before and after
  we join (§7 item 5), and step the prescale **down** towards the target rather than
  starting at N = 1 and hoping.
- **Does the combiner tolerate a prescaled FEU?** `combine_feus_hits` merges per-FEU hits
  by event; a FEU present in only 1 event in N is a case nobody here has run. If it
  chokes, fall back to lever 3 (per-channel k) which keeps our FEU in every event.
- **Wildcard-override ordering.** `Feu 3 …` lines must come *after* the `Feu * …` line to
  win. Inferred from the templates' own layout, not from reading the RunControl parser.
  Also, our `update_config_value()` matches the literal key string, so a hand-added
  `Feu 3 Feu_InterPacket_Delay` is **not** rewritten when the code writes
  `Feu * Feu_InterPacket_Delay` — convenient, but it means the per-FEU lines must live in
  the **template**, because the run-directory cfg is regenerated from the template every
  subrun (`make_config_from_template`).

---

## 7. Pre-run verification checklist

1. **Register readback per FEU.** `dream_scripts/dream_register_reader.py` (their copy) or
   our `feu_runctrl_reg.py` — peek `0x200008` and confirm `ZS/ZsTyp/ZsChkSmp/CmnPedOffset`
   on **every** FEU, ours and theirs. Peek `0x200018` for prescale + IPD on ours.
2. **Short test run, decoded: our FEU = 512 distinct channels/event** (all 8 Dreams
   surviving), uRWELL FEU sparse. That one number proves the mixed threshold set landed on
   the right hardware.
3. **All eight Dreams `Dat`.** Confirm the generated cfg's `Sys Topo Feu 3` line reads
   `Dat` on Dream 0-7 with no `Msk`. Any `Msk` means a connector was left out of
   `dream_feus` and a quarter-plane is silently missing.
3b. **Prescale actually took.** Peek `0x200018` on FEU 3 and confirm the prescale field
   is N, **and** that FEU 1's is still 1. A prescale that silently landed on the wildcard
   would quietly decimate banco's uRWELL data — check both boards, not just ours.
4. **Baseline median ≈ 256–263 ADC, no `DreamRdErr`** — standard decoded sanity.
5. **banco's `IntRate` unchanged.** Record their sustained rate before we join (4676 Hz on
   run_33) and after. Any drop means BUSY — find out whose before blaming the network.
6. **Resolve the axis convention (§2)** on real data: fire a localised source or use the
   beam spot, and confirm which reconstructed coordinate moves. Write the answer into the
   detector `description` field.
7. **Survey z**, and the transverse offsets while someone is in the hall.
8. **Retake pedestals** — the FEU set changed.

---

## 8. Per-FEU pruning: delete our data as soon as it is on EOS

### What already exists on banco (better than expected)

- `flask_app/space_manager.py` (2128 lines) — components, EOS verification, guarded delete.
- `scripts/prune_active_run.py` — prunes **completed sub-runs of the running run**. It
  lifts exactly one guard (`_run_guard`, which normally refuses the active and newest
  run) via a sentinel, and keeps every other check.
- `scripts/prune_loop.sh` — calls the above on an interval for a whole night.

Both were written for the overnight 2D mesh×drift grid (`0567652`) and are proven. The
safety model is the right one and we should not weaken it:

> the data is only ever dropped locally once EOS provably holds the same bytes

### The enabler: FDFs are per-FEU files

Verified in `runs/run_33/driftscan_gap150V/`:

```
raw_daq_data/       EicP2Bt_driftscan_gap150V_datrun_260731_16H13_000_01.fdf
                                                              ^^^ ^^
                                                    file_num  ---' '--- cfg FEU number
decoded_root/       ..._000_01.root
hits_root/          ..._000_01_hits.root
combined_hits_root/ ..._000_feu-combined_hits.root     <-- NOT per-FEU
```

So `dream_run`, `raw_fdf`, `decoded_root` and `hits_root` are all cleanly separable by
FEU; **`combined_hits_root` is not** and must never be touched by a per-FEU pruner. That
is fine — it is the physics product, it is small, and it already contains our hits.

`dream_run` is excluded from the backup entirely (`backup_config.json: exclude_dirs`), and
`space_manager._verify_component` handles it specially by looking each staged `.fdf` up
under the sibling `raw_daq_data` path on EOS. That logic carries over unchanged.

### The one new guard we need, and why

`prune_active_run.py` only considers sub-runs carrying `.subrun_complete`. A 12-minute
sub-run at our rate is **61 GB un-prescaled, 6 GB at prescale 10** — against 234 GB free
that is 4 sub-runs' headroom in the best case and less than one in the worst. Waiting for
the marker is too late. We need to prune **inside** the in-progress sub-run.

That is safe if and only if we can prove a given FDF is **closed**. We can:

> **A file is a candidate only if a strictly higher `file_num` exists for the same FEU in
> the same sub-run.** `Sys DaqRun FileSize 1000` rolls the FDF at 1000 MB, so the DAQ
> having opened `_001_` is proof that `_000_` will never be written again.

This matters because a size match on EOS is **not** by itself proof of completeness for a
file still being written — `backup_watcher._xrd_sync_tree` pushes any file whose size
differs from EOS, mid-sub-run and mid-file, and re-copies it later when it grows. (Good
news, though: that same behaviour means closed files reach EOS within a poll or two —
`poll_interval` 30 s — without waiting for the sub-run to end.)

### Proposed implementation

Keep it as a **separate script** — do not modify `prune_active_run.py`, which banco relies
on for their own runs.

**(a) `flask_app/space_manager.py`** — one optional argument, default `None` so every
existing caller is unchanged:

```python
def _component_contents(run, subrun, comp, name_filter=None):
    ...
    for f in path.rglob('*'):
        ...
        if name_filter and not name_filter(f.name):
            continue
```
and thread the same argument through `_component_local_files` and `_delete_component`.

**(b) `scripts/prune_feu.py`** — modelled line-for-line on `prune_active_run.py`:

- `--feu N` (required, no default — this script must never guess whose data it deletes)
- components restricted to `dream_run,raw_fdf,decoded_root,hits_root`; **reject
  `combined_hits_root` with an error**, it is not per-FEU
- `name_filter` = `re.compile(rf'_(\d{{3}})_{N:02d}(_hits)?\.(fdf|root)$').search`
- the closed-file guard above, applied per (sub-run, FEU)
- reuse `sm._remote_runs_map(force=True)` + `sm._verify_component` **unchanged** — fresh
  EOS listing, byte-size match, no listing → no delete
- reuse the `_NO_GUARD` sentinel to lift the active/newest-run guard, exactly as
  `prune_active_run.py` does, and keep every other check
- dry-run by default, `--apply` to delete, deletions logged through `sm._log_delete` so
  the audit trail matches a GUI prune

**(c) `scripts/prune_feu_loop.sh`** — clone of `prune_loop.sh` with `--feu` threaded
through. Interval: the EOS listing costs ~32 s, so **120–300 s** is the sane range.

### Will it keep up? — the open question

Pruning cannot outrun the backup. Un-prescaled we would need **~84 MB/s sustained to
`root://eospublic.cern.ch`** just for our FEU — which is not going to happen, and is
another reason prescale is mandatory. `_xrdcp_file` pays a 5-10 s connect+Kerberos
handshake per invocation (batched by `_xrdcp_batch`), fine for 1 GB FDFs, but the
aggregate throughput is unmeasured.

**Measure it before the run**: push a few GB of dummy FDFs and time it. Then set

```
N  >=  84.3 MB/s  /  measured_EOS_MB_per_s
```

and take the larger of that and whatever §6 wants. If EOS sustains, say, 20 MB/s, then
N ≥ 5 on backup grounds and N = 10 from §6 — so 10 stands. If EOS only manages 5 MB/s,
N ≥ 17 and the recommendation moves to 20. **This one measurement fixes the prescale, and
the prescale fixes the run plan** — do it first (§9 step 1).

Note the backup carries banco's 1.79 MB/s as well, and pedestal pushes are slow
(637 small files ≈ 60 min, per their own comment), so leave real headroom rather than
sizing N to exactly saturate the link.

---

## 9. What to touch, in order

1. Measure EOS push throughput from banco (§8) → **decide the prescale factor**. Nothing
   downstream is safe to size until this number exists.
2. Physical: mount det4 in the 940-1370 mm gap, gas, HV to card 12 ch 2/3, 8 signal
   cables X3-X6 → Dreams 0-3 and Y3-Y6 → Dreams 4-7 on FEU 3 (Id 101, .113). Survey z.
3. `run_config_beam.py` edits (§5). Add `Feu 3` IPD/prescale lines to the **template**
   `P2B_Beam.cfg`, after the `Feu *` lines (§6 risk 3).
4. Retake pedestals with the new FEU set.
5. Build the mixed threshold set: run their generator, then zero **only** `_03_thr.prg`
   (§6). Do not name the output `pedestals_<datetime>` — `pedestals='latest'` could pick
   it up by accident.
6. Short test run → §7 checklist, all 8 items.
7. Deploy `prune_feu.py` + loop, run it **dry** for one sub-run and read the output before
   ever passing `--apply`.
8. Only then go to production rate.

---

## 10. ⚠ UNVERIFIED — the honest list

| # | claim | why it is not verified |
|---|---|---|
| 1 | ~17.6 kB/event for our FEU | computed from 512 ch × 16 samples × 2 B + assumed 10 % ZS framing; **not measured**. Everything in §6 scales off it, including the "over the link ceiling" conclusion — the payload term (16,384 B) is solid, only the framing fraction is assumed. Even at 0 % overhead it is 76.6 MB/s, i.e. 92 % of the ceiling, so the conclusion survives the uncertainty. |
| 2 | Per-FEU `Feu 3 …` overrides beat the `Feu *` wildcard | inferred from template layout, not from the RunControl parser. Applies to IPD, prescale, and any per-FEU line. |
| 3 | The combiner tolerates a prescaled FEU | never run here — and prescale is now **mandatory**, so this moved from "nice to know" to a blocker. Fallback (per-channel k) only halves the volume and cannot replace prescale on its own. **Test this early**, §7 item 2 on a short run. |
| 5 | Card 12 channels 2/3 reach 600 V | card 12 currently only ever supplies 420 V. |
| 6 | `dream_feu_orientation: 'inverted'` for det4 | copied from our `mx17_D`; must be read off the actual plugs. |
| 7 | The axis convention (§2) | two conventions in the repo disagree; the connector choice is safe either way, downstream coordinate claims are not. |
| 8 | EOS sustains our push rate | unmeasured, and it gates the whole plan (§8). |
| 9 | det4's stripe map registers to the DAQ strip coordinate the way §2 assumes | the assessment used a run-dependent sliding alignment (`local X = reference Y + 186 mm`). The *relative* geometry is solid; the absolute registration should be re-confirmed on first beam. |

---

## 11. Questions for Dylan

**Q1 — the 16-sample window.** `Sys NbOfSamples` is global to the crate. In banco's crate
Detector E gets **16 samples at 60 ns = 0.96 µs**, against the 400 samples at 20 ns = 8 µs
we are used to. Is that enough for what you want det4 for? If the answer is
"we need a longer window", there are only bad options: ask banco to raise `NbOfSamples`
for everyone (costs them ~proportional rate), or explore the per-FEU `Feu * Main_Conf_Samples`
register, which *is* per-FEU-expressible in the cfg grammar but which I have never seen set
inconsistently and which the event builder may well reject (⚠ untested, and it is the kind
of thing that silently produces malformed events).

**Q2 — what prescale, given it is now forced?** With 8 connectors the un-prescaled wire
rate is 102 % of the per-FEU link ceiling, so `Feu_PreScale_EvtData` is not a choice any
more — only its value is. §6 recommends **N = 10** (8.4 MB/s, 1.68 M raw events/hour),
subject to the EOS measurement possibly pushing it higher. Two things to confirm:
is 468 Hz of det-E events enough for what you want, and are you comfortable that our FEU
is then absent from 9 events in 10? The latter breaks the "our FEU is in 100 % of events"
integrity check and is untested through their `combine_feus_hits` — it is now risk #3 in
§10 rather than a footnote.

**Q3 — gas.** det4 ran Ar/isobutane 95/5 on the bench. What is it running on at H4 —
a separate line of ours, or the P2/uRWELL mixture? If the latter, the 495 V / 600 V
operating point moves and needs re-establishing in situ.

**Q4 — do we tell banco first?** With 8 connectors we are over the per-FEU link ceiling
un-prescaled, so FEU BUSY costing them trigger rate is the *expected* behaviour, not a
tail risk (§6). §7 item 5 is the measurement that settles it, but this is now clearly a
conversation to have before the cables go in.

**Q5 — resolved.** FEU 3 confirmed (was Q4). Its cfg block is already live in
`P2B_Beam.cfg`, so no template edits are needed to bring it back.

**Q6 — scope of the pruner.** §8 proposes a *separate* `prune_feu.py`, leaving their
`prune_active_run.py` untouched. Alternative is one script with a `--feu` flag. Separate is
safer (their overnight runs keep a script whose behaviour did not change); one script is
less duplication. Preference?

---

### Sources

**On banco (read-only, 2026-07-31):** `run_config_beam.py`, `dream_daq_control.py`,
`flask_app/space_manager.py`, `backup_watcher.py`, `scripts/prune_active_run.py`,
`scripts/prune_loop.sh`, `dream_scripts/`, `config/backup_config.json`,
`config/processor_config.json`, `P2_data/TB_July2026_H4/dream_config/{P2B_Beam.cfg,RackTcm.cfg}`,
`runs/run_33/driftscan_gap150V/`.

**This repo:** `docs/HANDOFF_2026-07-31_shared_crate_zs_thresholds.md`,
`docs/live_zs_run_sources_2026-07-19.md`, `docs/CLOCK_RATE_SCAN_2026-07-23.md`,
`docs/HANDOFF_2026-07-23_dream_config_optimization.md`, `run_config_beam.py`,
`dream_daq_control.py`.

**Analysis repo (`nTof_x17`):**
`mx_june_cosmic_qa/det4_sps_assessment/DET4_SPS_ASSESSMENT.md`,
`common/mx17_active_area.py`, `mx17_m1_map.csv`.
