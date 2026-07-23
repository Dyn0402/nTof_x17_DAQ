# TX401 compatibility audit — DAQ PC, 2026-07-22

> ⚠ **SUPERSEDED IN PART — see [`05_as_built_2026-07-22.md`](05_as_built_2026-07-22.md).**
> The card was installed on 2026-07-22 and three things went differently:
> the **I210 was removed from the machine** rather than relocated to SLOT1; **CERN ended up
> on the onboard `eno1`** and DREAM on the TX401 (`enp4s0`), i.e. the reverse of §3/§4 here;
> and the card reports as an **AQC113 `[1d6a:04c0]`**, not the AQC107 `[1d6a:07b1]` this
> audit was written around (`atlantic` bound to it regardless, so the driver verdict holds).
> **The slot recommendation was correct** — the card negotiated ×4 @ 8.0 GT/s in SLOT4 and
> the Radeon was never touched.

All facts below were measured on the live machine, not inferred from spec sheets.

## Verdict

**The TP-Link TX401 will work.** Driver support is in-tree and already present; no
download, no DKMS, no Secure Boot exemption.

**The slot question is resolved (see §3): TX401 → SLOT4, I210 → SLOT1, the Radeon
stays put.** No GPU removal is required.

---

## The machine

| | |
|---|---|
| Model | Dell **OptiPlex 7060 MT** (`chassis_type = 3` reads "Desktop" on MT and SFF alike — the four expansion connectors incl. legacy PCI are what identify it as the MT; see §2) |
| Board | Dell 0C96W1 |
| CPU | Intel **i5-8400**, 6 cores / 6 threads, 2.8–4.0 GHz |
| RAM | 15 GiB (≈6 used, 7 cache) |
| Kernel | `7.0.0-28-generic` |
| Secure Boot | **disabled** |
| Storage | `/` 468 G on `sdb2` (SSD), `/mnt/data` **3.4 T on `sda4`** (58 % used, 1.4 T free) |

## Networking as it stands today

| Iface | Device | Driver | Speed | MTU | Address | Role |
|---|---|---|---|---|---|---|
| `eno1` | Intel **I219-LM** (onboard, `8086:15bb`) | `e1000e` | **1000 Mb/s** | **9000** | 192.168.10.8/24 | **DAQ / FEU / N1081B network** |
| `enp4s0` | Intel **I210** add-in (`8086:1533`, `04:00.0`) | `igb` | 1000 Mb/s | 1500 | 128.141.177.103/24 | CERN network, default route |

`eno1` lifetime counters: **499.7 GB rx**, 127.4 M packets, **zero** rx errors / drops /
overruns / CRC / missed. The 1 Gb link is not *erroring*, it is *saturating* — confirmed
2026-07-22 from the 2 Hz `system_stats` log: at the clean IPD-75 threshold the readout
burst demands **975 Mb/s, i.e. 104 % of the link's usable payload rate**. The corruption
at lower IPD is downstream packet loss under burst, not a bad link. See
[`04_bandwidth_model.md`](04_bandwidth_model.md).

There is already a durable instrument for this: **`system_stats_watcher.py`** logs
per-interface throughput, CPU, and disk I/O at 2 Hz to
`~/beam_july/slow_control/system_stats/`, running since 2026-07-15. It is what makes the
before/after comparison possible without adding any instrumentation — but see the caveat
in §4 about its column names, and the 0.5 s binning caveat in
[`03_test_plan.md`](03_test_plan.md#test-0).

## 1. Driver — PASS (nothing to install)

The TX401 is a **Marvell/Aquantia AQC107**, PCI ID `1d6a:07b1`. The in-tree `atlantic`
driver on this kernel advertises exactly that ID:

```
$ modinfo atlantic | grep 07B1
alias:  pci:v00001D6Ad000007B1sv*sd*bc*sc*i*
```

- Module: `/lib/modules/7.0.0-28-generic/kernel/drivers/net/ethernet/aquantia/atlantic/atlantic.ko.zst`
- Also listed in `modules.builtin`, i.e. it is part of the distro kernel package.

**Consequence:** the card should enumerate and come up on plug-in with no user action.

**Consequence for kernel upgrades:** because `atlantic` is in-tree and distro-signed, it
does **not** have the failure mode the GPIB drivers have on this machine (out-of-tree
`.ko` disappearing after a kernel bump, needing DKMS). No DKMS setup is required for the
NIC. Do not copy the GPIB pattern here.

Secure Boot is off anyway, and the module is signed regardless — no MOK enrolment needed.

## 2. PCIe electrical budget — PASS

`sudo dmidecode -t slot` (run 2026-07-22) tied the root ports to physical connectors:

| Connector | dmidecode `Type` | Length | Root port | Negotiated | Occupant |
|---|---|---|---|---|---|
| **SLOT1** | `x1 PCI Express` | Short | — | — | **empty** |
| **SLOT2** | `x16 PCI Express 3` | Long | `00:01.0` (CPU) | gen3 ×8 | `01:00.0` AMD **Radeon Oland** + HDMI audio |
| **SLOT3** | `32-bit PCI` | Short | via `00:1c.0` → `02:00.0` TI XIO2001 bridge | — | **empty** |
| **SLOT4** | `x4 PCI Express 2 x16` | Long | `00:1d.0` (PCH) | gen1 ×1 | `04:00.0` Intel **I210** (`enp4s0`, CERN net) |
| SLOT5/6 | M.2 sockets | — | — | — | empty |

**Two of Dell's `Type` strings are misleading, and sysfs overrules both:**

```
/sys/bus/pci/devices/0000:00:1d.0   max = 4x @ 8.0 GT/s     ← SLOT4 is gen3, not "PCI Express 2"
/sys/bus/pci/devices/0000:04:00.0   max = 1x @ 2.5 GT/s     ← the I210 CARD is gen1 x1
```

So SLOT4 is a **gen3 ×4 slot in an ×16-length connector**, currently occupied by a card
that can only ever use gen1 ×1 of it. That is the whole basis of the §3 recommendation.

Bandwidth needed for 10GBASE-T line rate: **~1.25 GB/s per direction** (PCIe is full
duplex, so the two directions do not add).

| Slot the card ends up in | Usable payload per direction | 10 Gb line rate? |
|---|---|---|
| **gen3 ×4 (SLOT4)** | ~3.9 GB/s | **Yes, full — 3× margin** |
| gen3 ×8/×16 (SLOT2) | ~7.9 GB/s | **Yes, full** |
| gen3 ×1 | ~0.98 GB/s ≈ 7.9 Gb/s | ~80 % — *still a 7.9× win over today* |
| gen1/gen2 ×1 | 0.25 / 0.5 GB/s | 2–4 Gb/s — a 2–4× win, but leaves headroom on the table |

Note that the TX401 is a ×4 card in a ×4 **connector**, so SLOT1 (×1, Short) and SLOT3
(legacy 32-bit PCI) cannot physically take it. Only SLOT2 and SLOT4 are candidates — and
both are ample.

The TX401 is a **PCIe 3.0 ×4** card. It ships with a full-height bracket fitted and a
low-profile one in the box — on this MT chassis, **use the fitted full-height bracket**.
It draws its power entirely from the slot, no auxiliary connector, so the OptiPlex PSU is
not a concern.

## 3. Recommended slot: TX401 → SLOT4, I210 → SLOT1, Radeon stays

> **This supersedes the original "pull the Radeon" recommendation.** That advice was
> written before `dmidecode` was available and assumed there was nowhere good to put the
> I210. There is: it is a gen1 ×1 card and SLOT1 is a ×1 slot, so the move is free.

| Slot | Type | Today | After |
|---|---|---|---|
| SLOT1 | PCIe ×1 | empty | **I210** → `enp4s0`, CERN network |
| SLOT2 | PCIe 3.0 ×16 (CPU) | Radeon Oland | **Radeon, untouched** |
| SLOT3 | 32-bit PCI | empty | empty |
| SLOT4 | PCIe 3.0 ×4 (×16 connector) | I210 | **TX401** → `192.168.10.8` |

Why this is the right plan:

- **TX401 gets its full gen3 ×4** (~3.9 GB/s/direction, 3× what 10GBASE-T needs).
- **Relocating the I210 costs literally zero bandwidth.** It is capped at gen1 ×1 by the
  card itself (`max_link_width = 1`, `max_link_speed = 2.5 GT/s`) and is negotiated at
  gen1 ×1 today. In SLOT1 it runs at exactly the same gen1 ×1.
- **The GPU is not touched**, so no video cutover, no risk of coming back up headless, and
  the `radeon.si_support=0 amdgpu.si_support=1` kernel cmdline stays meaningful.
- Both cards move in **one** power-down window.

### The one real trade-off: SLOT4 is behind the PCH

SLOT2 hangs directly off the CPU; SLOT4 hangs off the PCH (`00:1d.0`), and everything
behind the PCH shares one **DMI 3.0 ×4 link (~3.9 GB/s)** with SATA, USB and the onboard
I219. Both halves of the DAQ path cross it: FEU traffic inbound, `/mnt/data` writes
outbound.

That is fine here, because the real workload is nowhere near line rate:

- The 8 FEUs have 1 Gb links, so inbound peaks at **~1.0 GB/s** regardless of how fast the
  host port is — the FEU links cap it, not the NIC.
- Measured disk write during the IPD ladder was **6.7–9.4 MB/s mean**, ~2 % duty — see
  §5. It is not a meaningful DMI consumer.
- The load is bursty (~100 ms of burst inside a ~3.6 s spill period), not sustained.

So ~1 GB/s of a ~3.9 GB/s shared link, in bursts. Comfortable. **SLOT2 remains the
fallback experiment** if post-upgrade testing ever shows an unexplained ceiling near
~2 GB/s aggregate — at that point, and only then, pulling the Radeon buys a CPU-direct
lane. Do not pay that cost pre-emptively.

**Caution on the I210 move:** it carries the CERN network and your SSH access
(`128.141.177.103`, default route). Have a monitor and keyboard on the machine before you
start — do not do this remotely.

## 4. Software cutover surface — small, but real

The FEU network is identified by **IP, not interface name**, almost everywhere. Keeping
`192.168.10.8` on the new NIC means nearly nothing changes:

- `config/json_run_configs/*.json` and `run_config_beam.py:218,255,268` set the DAQ host
  as `"ip": "192.168.10.8"` — **unchanged** if the address moves to the new card.
- `flask_app/access_config.py:11` allowlists `192.168.10.8` — **unchanged**.

Three places do reference the interface name and must be edited:

- `/etc/netplan/01-eno1-dream-daq.yaml` — carries the `192.168.10.8/24` + **MTU 9000**
  config for `eno1`. Root-owned; not readable by the DAQ user.
- `flask_app/app.py:1312` — `_NET_IFACES = ["enp4s0", "eno1"]` drives the GUI network
  panel. Add the new name (`enp1s0` or similar).
- `system_monitor/system_stats_controller.py:59` — `NET_IFACES = ["enp4s0", "eno1"]`,
  the durable 2 Hz logger. Its own comment says *"KEEP IN SYNC"* with `app.py`.

> **CSV schema hazard.** `NET_IFACES` determines the CSV column names, and `_log_row()`
> writes a header **only when the file does not already exist**. Changing the interface
> list part-way through a day appends rows under the *old* header — same column count,
> silently mislabelled. **Rename the interface at a day boundary, or move the day's CSV
> aside** before restarting `system_stats_watcher`. This log is the instrument you will
> use to verify the upgrade; do not corrupt it while performing the upgrade.

**Both interface names change under the §3 plan, not just the new one.** `enp4s0` encodes
*bus 4*; moving the I210 from SLOT4 to SLOT1 puts it on a different bus, so it will almost
certainly come back with a new name. Run `ip -br link` after the swap and update **both**
`NET_IFACES` lists in the same edit, along with anything that referenced `enp4s0`. This
is the one extra cost of keeping the Radeon — it is a text edit, not a risk.

The netplan directory also holds NetworkManager-generated profiles; `eno1` is managed by
the `netplan-eno1` connection. Keep the same management path for the new NIC — do not mix
a netplan-static NIC with an NM-managed one on the same subnet.

## 5. Risks / things that are *not* automatically fine

**Thermals — slightly worse under the §3 plan, and the reason Test 7 matters.** The AQC107
is a hot chip (~5–7 W) with a passive heatsink, and an OptiPlex 7060 has minimal slot
airflow. Keeping the Radeon means the TX401 sits in a more crowded chassis than the
"pull the GPU" plan would have given it. This is the price of not touching the video card,
and it is the right trade — but it makes the soak test non-optional. After install, check
`sensors` under load. This is the most likely *surprise* failure mode, and it is
slow-onset: it will not show up in a 60 s test. **If the card does throttle, moving it to
SLOT2 (Radeon out) is the fix** — same fallback as the DMI concern in §3, so one action
covers both.

**CPU.** 6 cores at 2.8 GHz is enough for 10 Gb *with* offloads and RSS, but not for
10 Gb of small packets with everything off. Confirm after install that
`rx-checksumming`, `generic-receive-offload`, `tcp-segmentation-offload` and multi-queue
RSS are enabled on the new NIC (they are on `eno1` today).

**Disk — checked, and it is *not* the next wall.** The instinct is that 10 Gb ≈ 1.25 GB/s
would swamp `/mnt/data` (`sda4`, 3.4 T). The logged `disk_hdd_write_bps` during the
2026-07-22 ladder says otherwise: **6.7–9.4 MB/s mean**, because the duty cycle is ~2 %
(a ~100 ms readout burst per ~3.6 s spill). Even a 10× event rate leaves the disk an order
of magnitude clear, and the per-spill burst needs only ~22 MB of RAM buffering. **Capacity**
is the real storage concern — 1.4 T free disappears fast at RAW/low-IPD rates.
`scripts/net_baseline.sh` still measures sustained write, for the record.

**Jumbo frames.** `eno1` runs MTU 9000 today. The new NIC must be set to 9000 **and the
new area switch must pass 9000-byte frames**. A switch defaulting to 1500 will silently
break FEU readout in a way that looks like corruption. Check this explicitly — it is on
the test checklist.

**The 192.168.10.0/24 subnet is not just FEUs.** ARP shows the **N1081B logic modules at
.240–.245** live on the same segment as the FEUs (.43, .81, .82, .83, .110, .111, .118,
plus .44). Per `n1081b/CLAUDE.md`, those boards wedge from careless handling and have no
reliable remote reboot. Any switch swap drops their links. See
[`02_feu_switch_migration.md`](02_feu_switch_migration.md) — this is the highest-risk
part of the whole upgrade, and it is not the part involving the new card.

---

## RESOLVED — the slot question (2026-07-22)

`sudo dmidecode -t slot` was run on the machine. Result, and what it settled:

**1. There are four expansion connectors, and only two can take the TX401.** SLOT1 is
×1 (Short) and SLOT3 is legacy 32-bit PCI — neither accepts a ×4 card. That leaves SLOT2
(×16, Radeon) and SLOT4 (×4 electrical in an ×16-length connector, I210).

**2. SLOT4 is gen3 ×4, and its current occupant wastes it.** Dell's `Type` string
`x4 PCI Express 2 x16` reads like gen2, but sysfs reports the root port at
`max_link_speed = 8.0 GT/s`. The I210 sitting in it is a **gen1 ×1 card** — the ×1 gen1
negotiation seen today is the card's ceiling, not the slot's.

**3. Therefore the Radeon does not need to come out.** TX401 → SLOT4 at full gen3 ×4;
I210 → SLOT1, where its gen1 ×1 is served exactly as well as it is today. See §3 for the
full slot map and the DMI trade-off.

**4. Chassis is the MT, not SFF** — four expansion connectors, one of them a 32-bit PCI
slot fed by the TI bridge; an SFF 7060 has two slots and no PCI connector. (`chassis_type = 3`
reads "Desktop" on both, so the slot list is the reliable discriminator.) **Use the
TX401's stock full-height bracket**; keep the low-profile one in the box. Confirm by eye
when the case is open — this is the only claim here not directly measured.

### Remaining open item

None blocking. The two things to watch are both post-install and both have the same
fallback (move to SLOT2, Radeon out): **card temperature under soak** (§5) and, much less
likely, a **DMI ceiling** near ~2 GB/s aggregate (§3).
