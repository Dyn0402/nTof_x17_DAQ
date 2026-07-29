# Results — 10 GbE switch swap, 2026-07-22 (~16:40–16:50)

Switch swapped; DAQ PC uplink moved to a 10 G port. Kit used:
`scripts/post_switch_check.sh`, `dream_scripts/feu_trig_counters.py`,
`harness/verify_run.py`. Runbook: [`06_switch_swap_runbook.md`](06_switch_swap_runbook.md).

## Verdict: the upgrade is good, and the beam ladder confirms it.

| Test | Result |
|---|---|
| 1 — card / link | **PASS** `enp4s0` 10000 Mb/s, MTU 9000, PCIe x4 @ 8.0 GT/s |
| 2 — throughput | **not run** — no 10 G iperf3 peer existed (see below) |
| 3 — jumbo | **PASS** — 36 920 jumbo frames in one short run, 0 errors |
| 4 — reachability | **PASS** — 8 FEUs + 6 N1081B + TCM, all MACs match baseline |
| 5 — bench IPD ladder | **NOT RUN** — no saturating trigger while beam was off (recipe now proven, see below) |
| 6 — beam IPD ladder | **PASS on the network question** — threshold moved IPD 75 → below 5. Physics number NOT established. **[Full results at the end of this file.](#beam-ipd-ladder--raw_ipd_10g-2026-07-22-170017211)** |

> Sections 5 and 6 below were written *before* the beam ladder ran and describe why it was
> deferred. The ladder subsequently ran at 17:00–17:21 — see the final section, which
> supersedes them.

## Link

`ethtool` link partner now advertises `100/1000/2500/5000/10000baseT` (was `10/100/1000`),
so it is a genuine 10 G port, not an NBASE-T fallback. Flow control changed too: the new
switch advertises **symmetric pause**; the old one advertised none. Worth remembering if
pacing behaviour ever looks different — pause frames toward the FEUs could interact with
their fixed pacing.

RX ring raised 2048 → 8184 (**not persistent across reboot**; netplan has no ring key).

## Jumbo — PASSED, and by a better method than planned

The plan called for a `tcpdump` capture. Unnecessary: **the `atlantic` driver counts jumbo
frames natively.** After one short RAW run through the new switch:

```
Queue[0] InJumboPackets: 4615     Queue[2] InJumboPackets: 13845
Queue[1] InJumboPackets: 13845    Queue[3] InJumboPackets:  4615
TOTAL: 36920        InErrors / InDroppedDma / AllocFails: all 0
```

`ethtool -S enp4s0 | grep InJumboPackets` during a run is now the standard jumbo check — no
root, no capture, unambiguous. The counter also confirms **RSS spreading across 4 queues**.

A pre-run frame-size ladder reproduced the pre-swap endpoint limits exactly (FEUs echo 2000
not 2028, `.240` caps at 1500), confirming the new switch is not 1500-only. Note again that
`ping -M do -s 8972` remains a **false failure** on this segment on any switch.

## ⚠ The incident: UDP dead while ICMP worked

After the swap, `RunCtrl` failed with `TcmCtrl_Open failed for port=16000`. Diagnosis
sequence and the lesson:

1. TCM `.32` **pinged at 0.17 ms** but answered **no** read-only UDP query.
2. Decisive step: **all 8 FEUs were also UDP-unreachable** (`feu_trig_counters.py` 8/8
   timeout) while all 8 pinged. So it was never a TCM fault — it was the whole DREAM UDP
   control plane.
3. Resolved by a **power cycle of the DREAM crates**. Afterwards: all 8 FEUs return
   registers, TCM reports `V2.21`, state `STANDBY` (no latched error), and
   `Fem.Detected = 0x1fe` (all 8 FEUs seen).

**Root cause not established.** The crates had been through a link flap during the swap,
but note that **DREAM UDP was never exercised between the NIC cutover (16:14) and the switch
swap (~16:40)** — `05_as_built` §9 verified reachability by *ping only*. So the NIC cutover
is as plausible an origin as the switch. If this recurs, that ambiguity is worth resolving.

### Lesson — ping is not reachability

Both `post_switch_check.sh` and `05_as_built` §9 declared "PASS, everything reachable" on
ICMP alone, while the control plane every one of these devices actually uses was dead.
**Add a UDP check to the post-swap routine:**

```bash
.venv/bin/python dream_scripts/feu_trig_counters.py    # read-only, no beam, no run
```

8/8 FEUs returning registers is the real reachability test. It takes seconds and it is the
check that would have caught this immediately.

## Not run, and why

**Test 2 (iperf3).** iperf3 was installed and a server left running on the DAQ host, but no
10 G peer was ever connected — `.32` is the TCM, not a peer. No synthetic throughput number
exists. This is a gap, though a minor one: the bench ladder is the better proof.

**Test 5 (bench IPD ladder).** Blocked on trigger source, not on the network. The harness
needs a **saturating** trigger; the trigger is currently `scint(singles+ps)` and with beam
off it delivers **5.8 Hz** (mean interval 171 ms) against a 0.25–3.3 ms readout cycle. Every
IPD point from 3 to 100 would read "clean" and the ladder would prove nothing.

Saturating it requires either N1081B work (M6.D pulser via `set_pulser.py` at a much shorter
period, plus opening the M4.C veto via `set_veto_open.py`) or the TCM's internal trigger
generator (`trig_rate`, Reg #22 — a TCM *write*, untested here). The N1081B route also has to
be restored afterwards (`trigger_mode.py scint --singles --ps-pickup`, delay 1800,
C.in5 invert False) and would put the beam trigger chain at risk if beam returned. Deferred
deliberately.

`harness/run_matrix_10g.py` is **written and dry-run clean**, ready to go once a saturating
trigger exists. It appends to `results/deadtime_db.csv` as `test='interpacket_10g'` with
non-colliding labels, and refuses to start unless the link reads 10000 Mb/s. NIC counters go
to a sidecar (`results/nic_counters_10g.csv`) because `DB_FIELDS` is fixed and
`DictWriter(extrasaction='ignore')` would silently drop them.

**Test 6 (beam ladder).** Beam off — last real pulse 400 e10 at 16:25.
`run_config_raw_ipd_10g.py` (`--smoke` = 1×3 min at IPD 10; full = 100/30/15/10/5/3/100) is
ready and generates cleanly.

## Still to record

Switch **model, firmware, port assignments, jumbo setting, flow-control setting** — none of
which I can read from the host. If an N1081B wedges in the next few days, the swap is a
suspect and these will be wanted. Also add the switch's **per-port discard counters** to the
"readout looks wrong" checklist: `atlantic` exports little, so that counter is now the most
direct view of the failure mode this upgrade exists to fix.

---

# Beam IPD ladder — `raw_ipd_10g`, 2026-07-22 17:00–17:21

Run `raw_ipd_10g`, 7×3 min, held identical to the morning's 1 GbE `raw_ipd_ladder`
(n32 RAW, lat 35, sample_period 60, PS+singles co-framed, drift 600 / resist 530).
Both analysed with the same `raw_ipd_analysis.py`.

## Network verdict: PASS, decisively

| IPD | 1 GbE gaps | verdict | **10 GbE gaps** | **verdict** |
|---|---|---|---|---|
| 100 | 0.000% | clean | 0.000% | clean |
| 75 | 0.000% | clean | — | — |
| 50 | 56.3% | CORRUPT | — | — |
| 30 | **99.0%** | CORRUPT | **0.000%** | **clean** |
| 15 | **86.8%** | CORRUPT | **0.000%** | **clean** |
| 10 | — | — | 0.041% | clean |
| 5 | — | — | 0.026% | clean |

**The corruption threshold moved from IPD 75 to below 5 — we never found it.** The two
points that were catastrophic on 1 GbE (99.0% and 86.8% eventId gaps) are now at 0.000%.
Total yield at IPD 10 = **92.2 ev/spill** vs 30.5 at IPD 100 — **3.0× more events**.

Host side over the whole ladder: **5.6 GB, 1 867 528 jumbo frames, InErrors /
InDroppedDma / AllocFails all 0.** The bottleneck did not move into the host.

## ⚠ The physics number is NOT established — do not quote band/spill yet

At the **same IPD 100, same config, same day**, `band/spill` (events in 4–10 ms) is
**11.00 on 1 GbE (14:50) vs 0.31 on 10 GbE (17:01)** — while `ev/spill` reproduces almost
exactly (30.4 vs 30.5). **The event count reproduces; their time distribution does not.**

Ruled out so far:
- **Board config identical** — diff of `n1081b_config.json` between the two ladders shows
  only free-running counter values (consistent with the crate power cycle resetting them;
  implied rates match) and `polled_at`. No configuration difference.
- **PS co-framing intact** — `set_ps_trigger_delay.py --show`: `enable_gd=True delay=1800
  invert=False`, exactly as required.
- **Beam intensity identical** — 1 GbE ladder mean 608 e10 / median 417 / max 864;
  10 GbE ladder mean 588 / median 415 / max 862.

**Unexplained.** Until it is, the best in-band figure from this run (11.96 ev/spill at
IPD 10, vs the predicted 23–24) cannot be trusted, and neither can the claim of ZS parity.
Note also that all three clean 1 GbE points read *exactly* 11.00 — suspiciously equal to the
~11-trigger flash burst of the comb mechanism; worth checking whether `band/spill` is
saturating on that burst rather than measuring what we think.

## Invalid points — beam dropout 17:17–17:20

Beam went to **zero real pulses for 17:17, 17:18, 17:19, 17:20**, returning weakly (5) at
17:21. Consequences:

- **`ipd100_b` (closing bracket) is VOID** — 8 spills, 3.0 ev/spill, logged
  "stopped manually". **There is therefore no drift control on this ladder.**
- **`ipd003` is contaminated** — its tail lost beam; 3.0 ev/spill, 0.16 band,
  cycle 10241 µs. Its "clean" verdict is meaningless at that event count and must not be
  read as "IPD 3 works".

**Re-run `ipd100_b` (3 min) to restore the bracket** — beam is back as of 17:22.

## Tool caveats

- **`raw_ipd_analysis.py`'s closing extrapolation block is hardcoded for the pre-upgrade
  context.** On this run it printed `Lowest CLEAN IPD on 1 GbE : 3` and then scaled by a
  further 1/10 to "usable IPD ~0.3, floor-limited". **Ignore that block for post-upgrade
  runs** — it is describing a 10 GbE ladder as if it were 1 GbE.
- **Do not read link utilisation off my 5 s sampler.** Its peak 5 s-average was 149 Mb/s
  (1.5% of line rate), but 0.5 s bins already understate the ~100 ms burst by ~5×, so 5 s
  bins understate it far more. The real figure needs
  `analyze_link_load.py --iface enp4s0 --line-mbps 10000` over the subrun windows, and the
  event size must still come back at 311 kB.

## ✅ RESOLVED: the `band/spill` anomaly is a metric artefact, not a physics change

`band/spill` is **not** "events in the 4–10 ms window after the gamma flash", which is what
`03_test_plan.md` and the prediction table claim it is. It is *events arriving 4–10 ms after
the **first captured event** of each spill*:

```python
# ipc_spectrum_vs_runs.py:79   (used by raw_ipd_analysis.py via ref.comb)
rel = (ts[s:e] - ts[s]) * TICK_MS      # anchor = ts[s] = FIRST EVENT of the group
```

`run62_spill_comb.py` states the same choice outright — *"the FIRST event of each spill is
the anchor (== 'flash' analogue)"* — and it was written for run_62, which had **no flash
trigger to anchor on**. It is a proxy, valid only while the DAQ reliably captures the flash
itself.

**Why that breaks the A/B:** the anchor is data-dependent. Change the readout rate and you
change *which* event is first, so the whole T axis slides and `band/spill` moves even though
the physics is unchanged. Comparing 1 GbE to 10 GbE on this number is measuring against a
ruler that moved. That is fully consistent with everything else reproducing exactly —
`ev/spill` 30.4 vs 30.5, identical board config, identical beam intensity, PS delay 1800
intact — and it needed no hardware explanation at all.

### What this does and does not invalidate

- **The network verdict is UNAFFECTED.** eventId gap fraction and cross-FEU spread do not
  depend on any time anchor. IPD 30/15 going 99%/86.8% → 0.000% stands.
- **`ev/spill` is safe** — it is a pure count, anchor-independent. **30.5 → 92.2 at IPD 10,
  a 3.0× yield gain, is quotable.**
- **`band/spill` and therefore "RAW reaches ZS parity" are NOT quotable** from either ladder,
  including the 1 GbE 11.00 baseline. Both used the same moving anchor.

**To get the real physics number**, re-analyse with T anchored on the **PS/gamma-flash
pickup** (which these runs *do* carry — co-framed at 1800 ns, unlike run_62) instead of on
the first captured event. Until then, quote the yield gain, not the in-band figure.

---

# ANSWER: the DREAM rate — `raw_ipd_10g_low`, 2026-07-22 17:45–18:09

Second 10 GbE ladder, walking the low end the first one never reached: **100 (bracket),
5, 3, 2, 1, 100 (bracket)**. Beam monitored live throughout — no dropout, so unlike the
first ladder **both brackets are valid**.

Plots: `~/beam_july/analysis/flash_comb/net_1g_vs_10g/`
Script: `~/beam_july/analysis/flash_comb/tools/net_1g_vs_10g.py`

## The network is no longer the limit. The TRIGGER is.

| IPD | model cycle | ev/spill | gap% | verdict |
|---|---|---|---|---|
| 100 | 3348 µs | 30.2 / 30.4 | 0.000 | clean (brackets) |
| 10 | 474 µs | 92.2 | 0.041 | clean |
| 5 | 314 µs | 95.7 | 0.000 | clean |
| 3 | 250 µs | 94.4 | 0.020 | clean |
| 2 | 218 µs | 95.6 | 0.000 | clean |
| **1** | **186 µs** | **94.8** | **0.020** | **clean** |

**Clean at every IPD down to 1 — the corruption threshold does not exist on 10 GbE within
the settable range.** On 1 GbE it was 75.

**And the yield has saturated.** From IPD 5 → 1 the modelled cycle falls **1.68×** while the
recorded yield changes by **1.014×** — flat at **95.1 ev/spill mean**. That plateau sits
exactly on what the PS+singles beam trigger delivers: **~93 events per spill**
(`run_config_raw_ipd_ladder.py:24`, "bursty: ~93 events per spill inside ~46 ms, then idle").

So the DAQ is now recording **essentially every trigger the beam offers**. This is not the
FEU floor (155 µs/event) and not the link — we never reached either. It is the trigger.

## Bracket quality

Five independent IPD-100 measurements across three ladders and both networks:
**30.4, 30.7 (1 GbE) · 30.5 (10 GbE #1) · 30.2, 30.4 (10 GbE #2)** — a 1.6 % spread. Drift
is excluded; the ladder is sound.

## Operating recommendation

**Run RAW at IPD 5–10.** Below 5 buys nothing measurable (the trigger is the ceiling), and
IPD 5 showed a 70.1 vs 95.7 yield swing between the two ladders — both clean on gaps, so
**yield goes unstable before corruption appears**. IPD 10 gave 92.2 with no such swing and
sits 2.5× above the FEU floor. There is no case for going lower, and **no case for a network
faster than 10 GbE**: the readout stopped being the constraint before the network did.

Against the 1 GbE baseline (best clean point, IPD 75, 36.0 ev/spill) this is a **2.6×**
yield gain with full waveforms — and against the old RAW operating point (IPD 100,
30.4 ev/spill) it is **3.1×**.

## Caveats that stand

- `band/spill` remains unquotable (moving anchor, see above). The gain above is stated in
  `ev/spill`, which is anchor-independent.
- Host counters stayed at **0 errors / 0 drops / 0 alloc-fails** for the entire session
  across ~10 GB and >4.6 M jumbo frames. The bottleneck never moved into the host.

---

# OvrWrnHwm scan in the 1 ms-window regime — `hwm_10g`, 2026-07-22 18:25–18:47

10 GbE, RAW n32, lat 35, **IPD 5**, **1 ms window start**, PS+singles beam trigger,
drift 600 / resist 530. 7×3 min, bracketed by the Hwm-11 default at both ends.

Figures — `~/beam_july/analysis/flash_comb/hwm_10g/`
  * `hwm_10g_overlay.png`   live windows over the IPC spectrum (`hwm_scan_ipc.py`, same code
                            and same look as the original `hwm_scan`)
  * `hwm_10g_summary.png`   capture efficiency / live-window model / recorded-in-band
  * `ipc_spectrum_vs_hwm*.png`  the standard per-point reference plot
  * `hwm_10g_scan.png`      target-vs-control two-panel (`plot_hwm_10g.py`)

## Result: a HARD NULL, with the knob verified

| Hwm | 11 (a) | 8 | 6 | 4 | 3 | 2 | 11 (b) |
|---|---|---|---|---|---|---|---|
| **events in 4–10 ms / spill** | 11.76 | 11.83 | 11.75 | 12.00 | 12.00 | 12.00 | 11.78 |
| total ev/spill | 100.8 | 97.9 | 94.8 | 92.5 | 91.4 | 88.9 | 99.7 |

**Band flat to ~2 % across the whole ladder while total yield falls 12 %.** Brackets agree
(11.76 vs 11.78) so drift is excluded.

**Why the null is believable.** Two earlier sweeps in this repo were artifacts because the
value never reached the hardware. Here the FEU watermark was read back per sub-run with
`dream_scripts/feu_trig_counters.py` and tracked **11/8/6/4/3/2/11 exactly** on all 8 FEUs,
and the control variable (total yield) responded monotonically. Knob verified, response
real, target unmoved.

## Why no DAQ knob can help: the band is TRIGGER-limited

- **Live coverage is 100 % for every configuration** — the overlay shows every bar spanning
  the full in-gate window, and `frac`/`frac_band` are 100.00 % everywhere. The comb is
  entirely gone; there is no dead time for a watermark to recover.
- **Readout capacity** at IPD 5 is 6 ms / 314 µs = **19.1** events per band; we record
  **12.0**. Not capacity-limited.
- **~95 ev/spill over the ~46 ms live window = ~12.4 triggers per 6 ms** — which is what we
  record. We are already capturing essentially every trigger that arrives in 4–10 ms.

This is the same wall as the total rate (see the previous section), now confirmed *inside*
the band.

## The morning scan does not contradict this — different regime

`hwm_beam` (5 ms window start) found lowering Hwm HURT, 23.4 → 13.4. There the ~11-event
flash burst drained **inside** the band, so throttling it deleted counted events. At a 1 ms
start the burst drains 1.00 → 4.46 ms, almost entirely in the flash-blind region — so
truncating it costs nothing, and gains nothing. **Always state the window start alongside
any band number.**

## Where the remaining headroom is

Not in the DAQ. Readout can already carry ~2.5× more events in the band than there are
triggers to fill it. The two live directions are:

1. **The trigger** — how many usable triggers arrive in 4–10 ms. The only lever that moves
   the target. Note the ~11-event flash burst consumes *trigger* slots at 1–4 ms while the
   detector is flash-blind.
2. **ZS instead of RAW** — cheaper events, but since the band is trigger- not
   capacity-limited, expect little. Worth one 3-min point to confirm rather than assume.

Hardware left at the production default **Hwm 11 / Lwm 8** (closing bracket restored it),
verified 18:51.
