# 10 GbE DAQ network upgrade — planning index

**Goal:** remove the shared 1 GbE host link as the binding constraint on FEU readout, so
RAW readout can run at low `Feu_InterPacket_Delay` (IPD) without event corruption.

**Two independent hardware steps, in this order:**

1. **Host NIC** — TP-Link TX401 (Marvell/Aquantia AQC107) PCIe 10GBASE-T adapter in the
   DAQ PC, replacing `eno1` (I219-LM, 1 Gb) as the 192.168.10.0/24 interface.
2. **Area switch** — a 10 GbE-uplink switch in the FEU area, so the 8 FEUs' 1 Gb links
   aggregate into a 10 Gb uplink instead of contending for one 1 Gb port.

**Step 1 alone buys nothing** unless the existing area switch already has a 10 Gb uplink
port. Both are needed for the full win, but step 1 is testable on its own (host↔switch
link rate) and is the lower-risk half.

## Documents

| File | What it covers |
|---|---|
| [`05_as_built_2026-07-22.md`](05_as_built_2026-07-22.md) | ⭐ **START HERE.** What is actually installed and configured. **Overrides the plan docs where they disagree** — the I210 was removed (not relocated), CERN and DREAM swapped interfaces, and `enp4s0` now means the 10 G card. Also the CERN/LanDB MAC registration procedure, which blocked the cutover and is in none of the plan docs. |
| [`00_system_readiness_2026-07-22.md`](00_system_readiness_2026-07-22.md) | Pre-purchase compatibility audit of the DAQ PC. **Verdict + the one open item.** |
| [`01_tx401_install.md`](01_tx401_install.md) | Physical install, netplan cutover, rollback |
| [`02_feu_switch_migration.md`](02_feu_switch_migration.md) | Area-switch swap — incl. the N1081B wedge risk |
| [`03_test_plan.md`](03_test_plan.md) | **The tests.** Fastest-first, with pass/fail numbers predicted in advance |
| [`04_bandwidth_model.md`](04_bandwidth_model.md) | Why the 1 GbE link is the constraint, and what 10 GbE should buy |

## Scripts

| Script | Use |
|---|---|
| `scripts/analyze_link_load.py` | **Reruns the load analysis** against the `system_stats` 2 Hz CSV. Derives RAW event size from delivered bytes, then the burst rate demanded vs the wire. Works before and after. |
| `scripts/net_baseline.sh` | Run **before** any hardware change. Snapshots link state, counters, offloads, disk write rate to `baseline/`. |
| `scripts/verify_10g_link.sh` | Run **after** each hardware step. 60 s go/no-go: driver bound, PCIe width, link speed, MTU, FEU + N1081B reachability. |

## Status

> **As built ≠ as planned.** Read
> [`05_as_built_2026-07-22.md`](05_as_built_2026-07-22.md) **first** — the I210 was removed
> rather than relocated, CERN and DREAM swapped NICs while `enp4s0` kept its name, and the
> card is an AQC113 not an AQC107.

- [x] Compatibility audit (2026-07-22) — **PASS**
- [x] Slot question resolved (2026-07-22) — TX401 → SLOT4 ✅ confirmed at ×4 @ 8.0 GT/s.
      (The I210 was removed entirely, not moved to SLOT1; the Radeon did stay.)
- [x] Pre-upgrade IPD ladder at n32 RAW — clean ≥ 75, corrupt ≤ 50
- [x] **Premise confirmed from the existing `system_stats` log** — at the IPD-75 threshold
      the burst demands **104 % of usable 1 GbE**; the corruption threshold sits exactly
      where demand crosses the wire
- [x] Baseline captured (`scripts/net_baseline.sh`)
- [x] **TX401 installed** (2026-07-22) — `enp4s0`, ×4 @ 8.0 GT/s
- [x] **DREAM network cut over to the TX401** (2026-07-22 16:14) — `192.168.10.8/24`,
      MTU 9000, all 8 FEUs + all 6 N1081B boards reachable.
      `scripts/dream_net_step2.sh`, record in `05_as_built` §9
- [x] `system_stats` CSV rotated at the swap boundary → `system_stats_2026-07-22.csv.pre_10g`
- [ ] **LanDB: drop the MAC clone on `eno1`** once `8C-EC-4B-B4-AC-64` propagates
- [ ] Area switch installed — **link is still 1000 Mb/s, which is correct until this happens**
- [ ] Post-upgrade IPD ladder — the answer

## The one-sentence summary of the test plan

Pre- and post-upgrade, run the **same** n32 RAW IPD ladder with all 8 FEUs and find the
IPD at which corruption starts. Today it breaks between **75 (clean) and 50 (corrupt)**,
exactly where the demanded burst rate crosses 1 GbE; if the upgrade works it should break
near **IPD ≈ 4**, and running at **IPD 10** should give RAW ~23–24 events/spill in the
4–10 ms band — parity with today's ZS, but with full waveforms. Everything else in
[`03_test_plan.md`](03_test_plan.md) is a faster proxy for that one measurement.

> Note the post-upgrade margin over the `n × 4.83 µs` FEU floor is only **1.7×** (264 µs
> vs 155 µs). 10 GbE is correctly sized; anything faster would be wasted.
