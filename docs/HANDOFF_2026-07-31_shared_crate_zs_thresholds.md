# HANDOFF 2026-07-31 — Per-FEU / per-channel ZS thresholds in a shared DREAM crate

**Context.** We expect to run in a shared crate + shared RunControl with P2 at the SPS.
P2 wants zero suppression on their trackers. We want our Micromegas read out
un-suppressed (or as close to it as possible). `Sys DaqRun Mode` is a **single global
run mode**, so "they run ZS, we run Raw" is not expressible as two modes in one run.

**Answer: it does not have to be.** The ZS threshold is stored **per FEU and per
channel**, not globally. Setting our channels' thresholds to 0 makes them survive ZS
unconditionally, which reproduces Raw *content* inside a ZS run. This is not a
speculative trick — we already do it on three channels per FEU in every ZS run (the
tracer channels), and they are present in 100 % of events.

The DAQ for the joint run will be driven from a different repo. This document is meant
to be self-contained enough to implement there. Every claim below carries a
`file:line` citation into this repo so it can be checked, and the few things that are
*inferred rather than measured* are marked ⚠ UNVERIFIED.

---

## 1. Where the threshold actually lives

Not in any run config. Each FEU loads its own threshold file, referenced from the DREAM
`.cfg` (`~/beam_july/dream_config/Tcm_Mx17_July_ZS.cfg:214-237`):

```
Feu * Feu_RunCtrl_PdFile         None
Feu * Feu_RunCtrl_ZsFile         None
Feu 1 Feu_RunCtrl_PdFile         dream_pedestals_01_ped.prg
Feu 1 Feu_RunCtrl_ZsFile         dream_thresholds_01_thr.prg
Feu 2 Feu_RunCtrl_PdFile         dream_pedestals_02_ped.prg
Feu 2 Feu_RunCtrl_ZsFile         dream_thresholds_02_thr.prg
...  (through Feu 8)
```

Each `_thr.prg` carries **8 Dreams × 64 channels = 512 independent 12-bit thresholds**.
So the granularity available to you is:

| Scope | Independent? | How |
|---|---|---|
| Run mode (Raw/ZS) | ✗ global | `Sys DaqRun Mode` |
| ZS enable bit | per-FEU (see §6) | `Feu N Feu_RunCtrl_ZS` |
| **ZS threshold** | **✓ per FEU *and* per channel** | `_thr.prg`, 512 values/FEU |
| Pedestal baseline | ✓ per FEU and per channel | `_ped.prg` |
| Inter-packet delay | per-FEU | `Feu N Feu_InterPacket_Delay` |
| Samples/window | ✗ global in practice | `Sys NbOfSamples` |

### `_thr.prg` file format

Plain ASCII, 256 data lines, decoded/encoded by
`dream_scripts/dream_threshold_manager.py:58-146`. Two Dreams are packed per 32-bit word:

```
0x0112011 -- <idx> B<block> E0 S0 C<channel> D <even_dream>-0x<even> D <odd_dream>-0x<odd>
```

- line index `idx` 0..255; `block = idx // 64`, `channel = idx % 64`
- `even_dream = block*2`, `odd_dream = block*2 + 1`
- `even_val = word & 0xFFF`, `odd_val = (word >> 16) & 0xFFF`
- values are 12-bit, so 0..4095 (`set_threshold` raises outside that range)

`DreamThresholdManager` also does `to_csv()` / `from_csv()`, so a mixed set can be built
in a spreadsheet if that is easier for the joint run. **Copy that module into the other
repo rather than reimplementing the packing** — it round-trips headers/footers verbatim,
which the firmware loader appears to care about.

---

## 2. How a threshold set is produced today

1. **A pedestal-threshold run** (`Sys Action PedThrRun 1`, `dream_daq_control.py:733`)
   writes, per channel, `thr_ch = ped_ch + N·σ_ch` where `N = Sys PedRun Threshold`,
   fixed at **5** in our template (`Tcm_Mx17_July_ZS.cfg:52`). σ is measured *after*
   ped + CM subtraction (`docs/live_zs_run_sources_2026-07-19.md:25-28`).

2. **`dream_scripts/prep_zs_thresholds.py`** builds any other operating point *without
   re-taking pedestals*: it multiplies every stored threshold by `k/5`, clamps to 12 bit,
   and forces the tracer channels to 0 (`prep_zs_thresholds.py:79-104`).

   ⚠ The rescale is a **pure multiply of the stored value**, deliberately byte-identical
   to the beam-validated `~/beam_july/test/zs_rate_scan/gen_zs_ladder.py:rescale_thr`.
   That is what keeps "our k8" the same threshold set that was validated on beam. Do not
   "fix" the math (e.g. to rescale only the σ term) without re-validating on beam —
   noted at `prep_zs_thresholds.py:12-16`.

3. The output is laid out exactly like a real pedestal run
   (`<out_root>/<out_name>/pedestals/<original filenames>`), so the production DAQ
   consumes it with **no code change**:

   ```python
   dream_daq_info['pedestals_dir'] = '/home/mx17/beam_july/pedestals'   # out_root
   dream_daq_info['pedestals']     = 'zs_k8_tracer_from_07-18-26_14-06-43'
   ```

   `dream_daq_control.get_pedestals()` (`dream_daq_control.py:882-975`) then copies each
   `*_NN_thr.prg` / `*_NN_ped.prg` into the run dir under the canonical names
   `dream_thresholds_NN_thr.prg` / `dream_pedestals_NN_ped.prg` that the cfg references.

   The FEU number `NN` is scraped out of the **source filename** with `re.search(r'_(\d{2})_', file)`
   (`dream_daq_control.py:937`) — see the FEU-mapping warning in §4.

---

## 3. The recipe for the joint run

A pedestal "set" is just a directory of 8 `_thr.prg` + 8 `_ped.prg` files. Nothing
requires them to share a `k`. **Build one mixed set:**

- P2 tracker FEUs → their chosen `k` (ours was 8; see §5 for why)
- our Micromegas FEUs → **all 512 channels set to 0**

Concretely, on top of the existing tooling:

```python
from dream_threshold_manager import DreamThresholdManager

mgr = DreamThresholdManager()
mgr.read_prg(src)                       # from the latest pedestals_<stamp>/pedestals/
for d in range(8):
    for c in range(64):
        mgr.set_threshold(d, c, 0)      # thr 0 = channel never suppressed
mgr.write_prg(dst)                      # dst named ..._NN_thr.prg with OUR feu number NN
```

Run `prep_zs_thresholds.py --k <their_k>` first to generate the whole set at their
operating point, then overwrite the 4 (or however many) `_thr.prg` files belonging to our
FEUs with the all-zero version. Keep `_ped.prg` **verbatim** in both cases — the firmware
still wants the baselines as the ZS reference even under Option B (`Pd=0`,
`Tcm_Mx17_July_ZS.cfg:218-221`).

Do not rename the set to something matching `pedestals_<datetime>`; `pedestals='latest'`
would then be able to pick it up by accident (`prep_zs_thresholds.py:31-32`).

**Keep the tracers.** Even in the all-zero FEUs it costs nothing, and on P2's FEUs the
tracer at FEU-channel 511 doubles as the FEU size-truncation monitor.

---

## 4. ⚠ FEU-number mapping — the way this goes silently wrong

Our detector → FEU map (`run_config_beam.py:355,377 / 433,455 / 511,535 / 591,614`),
FEU numbers as they appear in the cfg:

| Detector | x-connectors | y-connectors |
|---|---|---|
| mx17_A | FEU 3 | FEU 4 |
| mx17_B | FEU 5 | FEU 6 |
| mx17_C | FEU 7 | FEU 8 |
| mx17_D | FEU 1 | FEU 2 |

cfg FEU number ↔ `Feu_RunCtrl_Id` ↔ IP (`Tcm_Mx17_July_ZS.cfg:100-125`):

```
Feu 1 Id 32  192.168.10.44     Feu 5 Id 106 192.168.10.118
Feu 2 Id 71  192.168.10.83     Feu 6 Id 31  192.168.10.43
Feu 3 Id 98  192.168.10.110    Feu 7 Id 69  192.168.10.81
Feu 4 Id 99  192.168.10.111    Feu 8 Id 70  192.168.10.82
```

The IPs are **non-contiguous** and `.101-.108` are *not* FEUs. The template already has a
commented `Feu 9 / 192.168.10.133` slot (`:77,124-125`) — presumably where P2's boards
would be added.

**The failure mode:** assign the all-zero file to the wrong FEU number and you flood
P2's link with un-suppressed tracker data *and* suppress our own detector, and neither
side notices until offline decoding. The `_NN_` in the filename is what binds threshold
set to hardware, and it is matched by a bare 2-digit regex.

Before trusting a mixed set, **verify on the hardware, not on the filenames**: read back
`0x200008` per FEU with `dream_scripts/feu_runctrl_reg.py` (decodes `ZS / ZsTyp /
ZsChkSmp / CmnPedOffset`, `feu_runctrl_reg.py:22-23,60-61,100-107`), and confirm in a
short test run that hit multiplicity per FEU is ~512 ch on ours and sparse on theirs.

---

## 5. What "threshold 0" costs — this is the real decision

ZS compares the **processed** sample `raw − ped − CMN + CmOffset` (CmOffset = 256) against
the per-channel threshold, in TPC mode (`ZsTyp=1`), where a crossing keeps the channel's
whole waveform (`docs/live_zs_run_sources_2026-07-19.md:38-42`). So thr = 0 on a channel
gives you the full Raw waveform for that channel, wrapped in ZS payload format.

Measured k·σ ladder, gated beam, kB/event/FEU
(`docs/live_zs_run_sources_2026-07-19.md:44-48`, from `zs_rate_scan/gated_ladder_summary.csv`):

| k | 25 | 12 | 8 | 6 | 5 | 4 |
|---|---|---|---|---|---|---|
| kB/ev/FEU | 1.17 | 1.40 | 1.80 | 2.17 | 4.56 | **38.5 (= Raw)** |

Below ~4.5σ the threshold drops under the live noise floor, every channel floods, and the
event size *is already* full-Raw. So k→0 is not a new regime — it is the k4 corner, just
made deterministic instead of noise-dependent.

**The cost is bandwidth and deadtime, and in a shared crate it is P2's problem too:**

- Deadtime ≈ `1 µs × IPD × (NbOfSamples/32)`. IPD 100 → ~312 Hz sustained, IPD 10 →
  ~3.07 kHz, IPD 2 → ~7.21 kHz (`live_zs_run_sources_2026-07-19.md:55-58`).
- Raw-sized events need **IPD ≥ ~75** or the wire and host buffers overflow
  (`dream_daq_control.py:~640` comment). ZS at k8 runs at IPD 2.
- The per-FEU throughput ceiling is **83 MB/s**, invariant over event size and FEU count
  (memory: `feu-per-link-83MBs-ceiling`).
- `Feu_InterPacket_Delay` **is** per-FEU, so we can run slow while P2 runs fast — but
  **FEU BUSY is what throttles the TCM**. Our slow, Raw-sized FEUs assert
  OverflowWarning → FEU BUSY → the TCM stops sending triggers → *P2's trackers lose the
  same triggers we do.* Expect to impose roughly a factor ~20 rate reduction on the joint
  run (IPD 2 → ~75+) if we go fully un-suppressed.

**Middle ground if that is unacceptable:** put our FEUs at a low-but-finite k (k5 was the
07-15 optimum at ~4.6 kB/ev/FEU, ~2.5× their k8 volume rather than ~21×), and reserve
thr = 0 for a chosen subset of channels we want unconditionally. Mixed k *per channel*
within one FEU is the same mechanism — the file is per channel.

Also relevant if we go low-k: **CM subtraction is mandatory** (`Feu_RunCtrl_CM=1`). In
beam, per-channel baseline wander is 10–20× the beam-off σ
(`live_zs_run_sources_2026-07-19.md:88-94`). With thr = 0 this stops mattering for
*survival*, but it still shapes the recorded sample values, and it remains open whether
firmware CM is per-Dream or per-FEU (`:99-101`).

---

## 6. The alternative: turning ZS off per FEU

The cfg grammar supports overriding a `Feu *` wildcard with a `Feu N` line — the template
already relies on this for `PdFile`/`ZsFile` (`:214-237`) and has commented
`Feu 4 Main_Conf_DreamPol` examples (`:160-161`). So in principle:

```
Feu * Feu_RunCtrl_ZS   1
Feu 3 Feu_RunCtrl_ZS   0        # our FEUs read out Raw
Feu 4 Feu_RunCtrl_ZS   0
```

Two things worth knowing if the other repo wants to try this:

- ⚠ **UNVERIFIED.** Mixed ZS/non-ZS FEUs in one run has never been tried here.
  `Sys DaqRun Mode` stays global at `ZS`; whether the decoder keys off the per-event
  header ZS bit (it should — the bit is in the RunCtrl word at `:191`) or off the run
  mode is not something we have tested. Threshold 0 sidesteps the question entirely,
  which is why it is the recommendation.
- ⚠ Per-FEU lines must come **after** the wildcard line to win. Inferred from the
  template's own layout, not from reading the RunControl parser.
- Our `update_config_value()` (`dream_daq_control.py:843-879`) matches on the **literal**
  key string and stops at the first match per line, so a hand-added `Feu 3 Feu_RunCtrl_ZS`
  line is *not* touched when the code rewrites `Feu * Feu_RunCtrl_ZS`. Convenient — but it
  means the per-FEU line must live in the **template**, because the run-directory cfg is
  regenerated from the template on every subrun
  (`make_config_from_template`, `dream_daq_control.py:595-608`).

---

## 7. Pre-run verification checklist

1. `dream_scripts/feu_runctrl_reg.py` — per-FEU readback of `0x200008`; confirm
   `ZS/ZsTyp/ZsChkSmp/CmnPedOffset` are what the cfg claims on **every** FEU, ours and P2's.
2. Short test run, decoded: **our FEUs ≈ 512 distinct channels/event**, P2's FEUs sparse.
   That single number proves the mixed set landed on the right hardware.
3. Tracers 0 / 224 / 511 present in 100 % of events on P2's FEUs (integrity watermark,
   `ZS_PULSER_TEST_PROCEDURE.md:7`).
4. Baseline median ≈ 256–263 ADC and no `DreamRdErr` — the standard decoded-sanity check.
5. Event size per FEU vs the §5 table; sustained trigger rate vs the deadtime law. If the
   joint rate is far below `1 µs × IPD × (NbOfSamples/32)` predicts, someone is asserting
   BUSY — find out who before blaming the network.

---

## 8. Summary for the P2 conversation

- ZS thresholds are **per FEU and per channel**; only the run *mode* is global.
- We can emulate non-ZS on our detectors by shipping a threshold set with our FEUs' 512
  channels at 0. Validated mechanism — it is what our tracer channels already do.
- Content is then identical to Raw (TPC-mode ZS keeps the whole waveform on a crossing).
- **The negotiation is about rate, not about configuration.** Un-suppressed FEUs need
  IPD ≥ ~75 vs ZS's IPD 2, and FEU BUSY throttles the shared TCM, so the cost lands on
  P2's trigger rate as much as ours. If they cannot give that up, a low finite k on our
  side (k5 ≈ 2.5× their volume, vs ~21× for Raw) is the compromise, with thr = 0 on
  selected channels only.

### Source documents in this repo

- `docs/live_zs_run_sources_2026-07-19.md` — ZS pipeline, k-ladder, deadtime law, CM
- `docs/ZS_PULSER_TEST_PROCEDURE.md` — the validated ZS bring-up procedure
- `docs/DREAM_OPT_SURVEY_2026-07-23.md` — register-level readback of the RunCtrl word
- `dream_scripts/prep_zs_thresholds.py`, `dream_scripts/dream_threshold_manager.py`
- `dream_scripts/feu_runctrl_reg.py` — register decode/peek
- `~/beam_july/dream_config/Tcm_Mx17_July_ZS.cfg` — the ZS template
