# TX401 install + cutover procedure

> ⚠ **DONE 2026-07-22, and it did not go the way this document describes.**
> Read [`05_as_built_2026-07-22.md`](05_as_built_2026-07-22.md) instead for what is actually
> installed. In particular: the I210 was **removed**, not moved to SLOT1; **CERN is now on
> `eno1`** (onboard) and DREAM on `enp4s0` (the TX401), so every "the CERN NIC is untouched"
> claim below is false; and CERN **LanDB MAC registration** — not covered anywhere in this
> document — was the only thing that actually blocked the cutover.
> This file is kept for its rollback procedure and its netplan/verification mechanics.

Read [`00_system_readiness_2026-07-22.md`](00_system_readiness_2026-07-22.md) first. This
document assumes its verdict: driver in-tree, Secure Boot off, and the §3 slot plan —
**TX401 into SLOT4, I210 moves to SLOT1, the Radeon stays.**

**Estimated downtime:** ~20 min if it goes well. **Do it with the DAQ stopped and no
N1081B session open** — see the pre-flight.

## Before you start — gather

- TX401 card with its **stock full-height bracket** (this is a 7060 MT — the low-profile
  bracket in the box is not needed; confirm by eye when the case is open)
- **Cat6a** patch cable to the switch. Cat6 works only short and quiet; Cat5e will
  negotiate 1 G and you will chase it for an hour.
- **Monitor + keyboard on the machine.** The I210 (CERN network, `128.141.177.103`,
  default route) is being *moved*, so SSH goes away mid-procedure and its interface name
  will change. Do not attempt this remotely. The Radeon stays, so its video outputs still
  work — no need for a motherboard-port cable.

## Pre-flight (do not skip)

```bash
# 1. Capture the baseline — this is also Test 0 data
docs/network_upgrade_10g/scripts/net_baseline.sh

# 2. No run in progress
cat config/current_run_state.json

# 3. No N1081B session held — a board mid-session when the link drops is the
#    single worst outcome of this whole procedure. See n1081b/CLAUDE.md.
ls -la config/n1081b_access/

# 4. Save the netplan config you are about to change
sudo cp /etc/netplan/01-eno1-dream-daq.yaml ~/netplan-backup-$(date +%F).yaml
sudo cat /etc/netplan/01-eno1-dream-daq.yaml     # you will need its contents below
```

Stop the DAQ and the watchers cleanly. **Never SIGKILL anything holding an N1081B
session.**

## Physical install

1. Power off, unplug mains, ground yourself.
2. Note which cable is in the **I210** (`enp4s0`) before you unplug it — that is the CERN
   network. Label it if there is any chance of confusion with the `eno1` cable.
3. Remove the **I210** from SLOT4 (the ×16-length connector on the PCH, `00:1d.0`) and
   reseat it in **SLOT1** (the short ×1 connector). It is a gen1 ×1 card, so ×1 is not a
   downgrade — it is what it was already negotiating.
4. Seat the **TX401 in the now-free SLOT4**. It is a ×4 card in an ×16-length connector;
   that is fine, and the slot is electrically gen3 ×4 — full bandwidth.
5. **Leave the Radeon in SLOT2 alone.** Video is unchanged.
6. Cat6a from the TX401 to the switch. Reconnect the CERN cable to the I210 in its new
   slot. **Leave `eno1` cabled for now** — it is your rollback.
7. Power on.

## Bring-up

```bash
lspci -nn | grep -i 1d6a          # expect [1d6a:07b1]
lspci -k -s $(lspci | grep -i aquantia | cut -d' ' -f1)   # Kernel driver in use: atlantic
ip -br link                       # BOTH names change -- see below
dmesg | grep -i atlantic
```

**Two interfaces get new names, not one.** The TX401 in SLOT4 takes over bus 4, so it will
likely come up as `enp4s0` — *the name the I210 used to have* — while the I210 in SLOT1
appears as something else entirely. Do not assume `enp4s0` still means "CERN network".
Identify them by MAC or by driver, not by name:

```bash
for i in /sys/class/net/*/; do n=$(basename $i); \
  printf '%-10s %-10s %s\n' "$n" "$(basename $(readlink -f $i/device/driver 2>/dev/null))" \
  "$(cat $i/address)"; done
# atlantic = the new 10 G card;  igb = I210 / CERN;  e1000e = eno1 (onboard)
```

The I210's MAC is `b4:96:91:4d:1a:95` (recorded pre-move). Write down the resulting
name→role mapping now — §4 of the readiness doc lists the two `NET_IFACES` lists that both
need it.

Then run the full check:

```bash
docs/network_upgrade_10g/scripts/verify_10g_link.sh enp1s0
```

**The check that people skip and regret:** PCIe link width. Must be **×4 @ 8.0 GT/s** —
that is what SLOT4 is capable of, confirmed from its root port `00:1d.0`
(`max_link_width = 4`, `max_link_speed = 8.0 GT/s`).

```bash
BDF=$(lspci -Dn | awk '$3 ~ /^1d6a:/ {print $1}')
cat /sys/bus/pci/devices/$BDF/current_link_width    # expect 4     (verify_10g_link.sh does this)
cat /sys/bus/pci/devices/$BDF/current_link_speed    # expect 8.0 GT/s PCIe
```

If it reports ×1, the card is in the wrong connector — SLOT4 is the **long** (×16-length)
connector on the PCH side, not the short ×1 one you just put the I210 into.

While you are here, confirm the I210 is unharmed by its move — it should read exactly as
it did before, ×1 @ 2.5 GT/s, because that is the card's own ceiling:

```bash
cat /sys/bus/pci/devices/0000:0*:00.0/current_link_width   # the igb device: expect 1
```

## Network cutover

The goal is that **`192.168.10.8` moves to the new NIC and nothing else changes.** Every
run config and the Flask allowlist reference the DAQ host by that IP, so if the address
moves cleanly there is no config churn:

- `config/json_run_configs/*.json`, `run_config_beam.py:218,255,268` → `"ip": "192.168.10.8"`
- `flask_app/access_config.py:11` → `"192.168.10.8"`

### 1. Netplan

Edit `/etc/netplan/01-eno1-dream-daq.yaml` (root). Change the interface key from `eno1` to
the new name, keeping **the same address and MTU 9000**:

```yaml
network:
  version: 2
  ethernets:
    enp1s0:                 # was: eno1
      addresses: [192.168.10.8/24]
      mtu: 9000
      dhcp4: no
      # keep any routes/nameservers exactly as the original file had them
```

Note the original is managed via the `netplan-eno1` NetworkManager connection. Keep the
same management path — do not end up with one NIC static-in-netplan and another
NM-managed on the same subnet.

```bash
sudo netplan try        # auto-reverts in 120 s if you lose access — use this, not `apply`
```

### 2. Interface lists — TWO files, kept in sync

`flask_app/app.py:1312` (live GUI panel):

```python
_NET_IFACES = ["enp4s0", "eno1"]      # →  ["enp4s0", "enp1s0"]
```

`system_monitor/system_stats_controller.py:59` (the 2 Hz durable logger):

```python
NET_IFACES = ["enp4s0", "eno1"]       # →  ["enp4s0", "enp1s0"]
```

Simplest safe option: **add** the new names rather than replacing, so everything is logged
through the transition and the before/after comparison is unambiguous.

> ⚠ **`enp4s0` in these lists no longer means what it did.** Under the SLOT4 plan the
> TX401 inherits bus 4 and the I210 lands elsewhere, so the literal string `enp4s0` may now
> refer to the *10 G card* rather than the CERN NIC. Fill both lists from the
> name→driver→MAC table you built during bring-up, not from the old values.

> ⚠ **Rotate the day's CSV when you change `NET_IFACES`.** The column names come from that
> list, but `_log_row()` writes a header only when the file does not exist — so appending
> after a change puts rows under the old header, silently mislabelled. Do it at a day
> boundary, or:
> ```bash
> mv ~/beam_july/slow_control/system_stats/system_stats_$(date +%F).csv{,.pre_10g}
> ```
> then restart the `system_stats_watcher` tmux session. This log is the instrument you
> will verify the upgrade with — do not corrupt it during the upgrade.

### 3. Verify

```bash
ip -br addr                                   # 192.168.10.8 on the new iface
ping -M do -s 8972 192.168.10.240             # ⛔ INVALID TEST -- see 05_as_built §9;
                                              #    .240 is MTU 1500, this always fails
docs/network_upgrade_10g/scripts/verify_10g_link.sh enp1s0
```

Then [Test 4](03_test_plan.md#test-4--nothing-else-on-the-subnet-broke) — ping every FEU
and every N1081B board.

## Expected result at this stage

If the area switch has **not** been upgraded yet, the link will most likely come up at
**1000 Mb/s** (negotiated down to the switch port). **That is not a failure** — it means
step 2 of the upgrade is still pending. Nothing in the IPD behaviour will change until the
switch side is 10 G too. Do not run the IPD ladder yet; you will just reproduce the
baseline and confuse the record.

If the switch already has a 10 G port, link at 10000 Mb/s and you can go straight to
[Test 2](03_test_plan.md#test-2--hostswitch-throughput).

## Rollback

Fast, and worth rehearsing before you need it:

1. `sudo cp ~/netplan-backup-<date>.yaml /etc/netplan/01-eno1-dream-daq.yaml`
2. `sudo netplan apply`
3. Move the Cat6a back to `eno1` (it never left).
4. Revert both `NET_IFACES` lists (`flask_app/app.py:1312`,
   `system_monitor/system_stats_controller.py:59`) — remembering that the I210's name has
   changed even in the rolled-back state, since it is physically in SLOT1 now.

**Software rollback does not require undoing the card moves**, and it is not worth
re-opening the case for. The card can stay physically installed while rolled back — an
unconfigured `atlantic`
interface is inert. The Radeon never moved, so video is unaffected either way.

## Post-install to-dos

- Confirm offloads and RSS on the new NIC (`ethtool -k`, `-l`, `-g`) roughly match what
  `eno1` had — the baseline script recorded them.
- **Watch card temperature over the first few hours** ([Test 7](03_test_plan.md#test-7--soak)).
  This matters more under the keep-the-Radeon plan than it would have otherwise — the
  chassis is fuller. If the AQC107 throttles, the fix is to pull the Radeon and move the
  TX401 to SLOT2 (see readiness §3/§5); that is a deliberate second step, not the default.
- **No DKMS needed.** `atlantic` is in-tree, so unlike the GPIB modules on this machine it
  survives kernel upgrades on its own. Do not add it to DKMS.
