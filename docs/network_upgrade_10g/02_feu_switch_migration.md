# FEU-area switch migration

> **This is the highest-risk part of the upgrade, and it is not the part with the new
> card in it.** The 192.168.10.0/24 segment carries the **N1081B logic modules** as well
> as the FEUs, and those boards wedge from careless handling with no reliable remote
> reboot. Read `n1081b/CLAUDE.md` before touching cabling.

## What is actually on this network

From the live ARP table (2026-07-22):

| Host | IP | MAC prefix | Notes |
|---|---|---|---|
| FEU 1 | 192.168.10.44 | — | ID 32 |
| FEU 6 | 192.168.10.43 | `00:0a:35` (Xilinx) | ID 31 |
| FEU 7 | 192.168.10.81 | `00:0a:35` | ID 69 |
| FEU 8 | 192.168.10.82 | `00:0a:35` | ID 70 |
| FEU 2 | 192.168.10.83 | `00:0a:35` | ID 71 |
| FEU 3 | 192.168.10.110 | `00:0a:35` | ID 98 |
| FEU 4 | 192.168.10.111 | `00:0a:35` | ID 99 |
| FEU 5 | 192.168.10.118 | `00:0a:35` | ID 106 |
| **N1081B M1** | **192.168.10.240** | `00:12:5e` (CAEN) | **fragile** |
| **N1081B M2** | **192.168.10.241** | `00:12:5e` | **fragile** |
| **N1081B M3** | **192.168.10.242** | `00:12:5e` | **fragile** |
| **N1081B M4** | **192.168.10.243** | `00:12:53` | **fragile** — trigger module |
| **N1081B M5** | **192.168.10.244** | `00:12:5e` | **fragile** |
| **N1081B M6** | **192.168.10.245** | `00:12:5e` | **fragile** — pulser / γ-flash |
| DAQ PC | 192.168.10.8 | — | `eno1` today, TX401 after step 1 |

FEU→ID map source: `dream_scripts/feu_trig_counters.py:47-54`.

## Requirements for the new switch

| Requirement | Why |
|---|---|
| **≥ 1 × 10GBASE-T uplink** to the DAQ PC | the entire point — this is what removes the aggregation bottleneck |
| **≥ 8 × 1 GbE** access ports for the FEUs | each FEU link is 1 GbE and is *not* the constraint; do not pay for 10 G here |
| **+ ≥ 6 ports for the N1081B boards** (or keep them on the existing switch) | see the split-vs-combined decision below |
| **Jumbo frames, MTU ≥ 9000** | `eno1` runs 9000 today; FEU frame cap 8192, `MultiPackThr` 4888. A switch defaulting to 1500 silently breaks readout. |
| **Non-blocking / adequate buffers** | the failure mode being fixed *is* egress-queue overflow. A cheap switch with a shallow buffer on the 10 G uplink can reproduce the problem at higher rate. Prefer a switch that states its packet-buffer size. |
| **Flow control (802.3x) configurable** | may help or hurt; you want to be able to turn it off and compare. Pause frames toward the FEUs could interact badly with their fixed pacing. |

Deliberately **not** required: L3, VLANs, managed features beyond jumbo + counters.
Do want: **per-port drop/discard counters**, because that is where the loss lives and the
host NIC cannot see it.

## Decision: put the N1081B boards on the new switch, or leave them?

**Recommendation: leave the N1081B boards on the existing switch** if the topology allows
it, and run only the FEUs + DAQ uplink through the new one.

Reasons:
- The boards contribute negligible traffic; they gain nothing from 10 G.
- It removes them from the blast radius of the migration entirely.
- Future switch work on the FEU path then never touches them.

The cost is one inter-switch link and a slightly more complex diagram. Worth it.

If they must move, move them **last**, one at a time, verifying each with a ping before
the next — never in a bulk re-patch.

## Migration procedure

### Pre-flight

```bash
# 1. DAQ stopped
cat config/current_run_state.json

# 2. NO N1081B session held, and nothing quarantined mid-recovery
ls -la config/n1081b_access/

# 3. Baseline + a record of the current patching
docs/network_upgrade_10g/scripts/net_baseline.sh
arp -an | grep 192.168.10 | sort -t. -k4 -n     # MAC↔IP map, in case ports get shuffled
```

Stop the DAQ, the watchers, and anything that polls the boards (`poll_modules.py`, the
time-tag watcher, the scan watcher). **Do not SIGKILL** any of them — a dirty disconnect
is exactly how boards wedge (`n1081b-wedge-root-cause`, `rate-scan-ctrlc-m5-wedge`).

### Swap

1. **Label every cable before unplugging.** Photograph the existing patch panel.
2. Configure the new switch **on the bench first** — jumbo frames enabled, uplink port
   confirmed at 10 G. Do not configure it in-place with the FEUs hanging off it.
3. Move the DAQ uplink (Cat6a, TX401 → new switch 10 G port). Verify link at 10000 Mb/s
   before moving anything else.
4. Move the 8 FEU links.
5. Trunk to the old switch for the N1081B boards (or move them last, one at a time).

### Verify — in this order

```bash
# link rate first
docs/network_upgrade_10g/scripts/verify_10g_link.sh enp1s0

# jumbo end-to-end  (Test 3 — the most commonly-missed failure)
#   ⛔ NOT with ping. Measured 2026-07-22: FEUs echo at most a 2023-byte frame and the
#   N1081B boards at most 1500, so `ping -M do -s 8972` fails on ANY switch here.
#   Use the tcpdump capture in 03_test_plan.md Test 3 instead.

# everything reachable  (Test 4)
for ip in 43 44 81 82 83 110 111 118; do ping -c1 -W1 192.168.10.$ip >/dev/null \
  && echo "FEU .$ip OK" || echo "FEU .$ip *** DOWN ***"; done
for ip in 240 241 242 243 244 245; do ping -c1 -W1 192.168.10.$ip >/dev/null \
  && echo "N1081B .$ip OK" || echo "N1081B .$ip *** DOWN ***"; done
```

**If an N1081B board does not ping:** do **not** retry connections at it, and do not open
a session to "see if it's OK". Check the physical link LED and the switch port first. If
the port is up and the board is unreachable, treat it as wedged and follow
`n1081b/HANDOFF_2026-07-15_wedge_root_cause.md` — recovery is hours of *isolation*, and
further connection attempts extend it.

Then restart the DAQ and take a short pedestal or known-good run **before** starting any
IPD ladder. Confirm normal operation is normal again first.

## After the swap

- Run [Test 5 — bench IPD ladder](03_test_plan.md#test-5--bench-ipd-ladder-no-beam-★-the-decisive-test).
  It needs no beam and gives the answer in ~30 min.
- **Record the switch model, firmware, port assignments, jumbo setting, and flow-control
  setting** in this directory. If an N1081B board wedges in the following days, the swap
  is a suspect and you will want those details.
- Add the new switch's per-port discard counters to whatever you check when readout looks
  wrong. That counter is now the most direct view of the failure mode this upgrade exists
  to fix.
