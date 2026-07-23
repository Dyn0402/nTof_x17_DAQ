#!/usr/bin/env bash
# Step 2 of the 10G upgrade: put the DREAM/FEU network (192.168.10.8/24, MTU 9000)
# onto enp4s0 (the AQC113 TX401 in SLOT4).
#
# Spec: docs/network_upgrade_10g/05_as_built_2026-07-22.md §8
# Run with: sudo bash dream_net_step2.sh
set -euo pipefail

IFACE=enp4s0
TARGET=/etc/netplan/02-enp4s0-dream-daq.yaml
BK=/root/net-backup-$(date +%F_%H-%M-%S)

[[ $EUID -eq 0 ]] || { echo "must run as root"; exit 1; }

echo "=== 0. sanity: enp4s0 is the atlantic card ==="
drv=$(basename "$(readlink -f /sys/class/net/$IFACE/device/driver)")
[[ "$drv" == atlantic ]] || { echo "REFUSING: $IFACE driver is '$drv', expected 'atlantic'"; exit 1; }
echo "  $IFACE driver=atlantic mac=$(cat /sys/class/net/$IFACE/address)  OK"

echo "=== 1. backup /etc/netplan -> $BK ==="
mkdir -p "$BK"; cp -a /etc/netplan/. "$BK"/
cp -a "$BK" /home/mx17/ && chown -R mx17:mx17 "/home/mx17/$(basename "$BK")"
echo "  also readable at /home/mx17/$(basename "$BK")"

echo "=== 2. delete orphaned NM auto-profiles (the §6 gotcha) ==="
# Anything named 'Wired connection N' that is bound to enp4s0 or bound to nothing.
# 'Wired connection 1' is the USB tether (enx52ca...) and is explicitly preserved.
while IFS=: read -r name uuid dev; do
  [[ "$name" == Wired\ connection* ]] || continue
  [[ "$dev" == enp4s0 || -z "$dev" ]] || continue
  echo "  deleting '$name' ($uuid) dev='${dev:-<none>}'"
  nmcli con delete uuid "$uuid" || true
done < <(nmcli -t -f NAME,UUID,DEVICE con show)

echo "=== 3. write $TARGET ==="
cat > "$TARGET" <<'YAML'
network:
  version: 2
  renderer: NetworkManager
  ethernets:
    enp4s0:
      addresses:
      - "192.168.10.8/24"
      dhcp4: false
      dhcp6: false
      mtu: 9000
      routes:
      - metric: 9999
        to: "192.168.10.0/24"
        via: "192.168.10.8"
YAML
chmod 600 "$TARGET"; chown root:root "$TARGET"
cat "$TARGET"

echo "=== 4. netplan apply ==="
echo "  (systemd-networkd / network1.service errors below are COSMETIC -- NM renderer)"
netplan apply || true
sleep 8

echo "=== 5. verify ==="
ip -br addr show "$IFACE"
echo "--- MTU (must be 9000) ---"
ip link show "$IFACE" | head -1
echo "--- CERN untouched? ---"
ip -br addr show eno1
ip route | grep '^default' || true
echo "--- stray profiles ---"
nmcli -t -f NAME,UUID,DEVICE con show

echo
echo "=== 6. subnet reachability (ping only -- NEVER open an N1081B session here) ==="
for ip in 43 44 81 82 83 110 111 118; do
  ping -c1 -W1 192.168.10.$ip >/dev/null 2>&1 && echo "  FEU .$ip OK" || echo "  FEU .$ip *** DOWN ***"
done
for ip in 240 241 242 243 244 245; do
  ping -c1 -W1 192.168.10.$ip >/dev/null 2>&1 && echo "  N1081B .$ip OK" || echo "  N1081B .$ip *** DOWN ***"
done

echo
echo "=== 7. frame-size ladder (NOT a jumbo pass/fail -- see below) ==="
# ⛔ `ping -M do -s 8972` is an INVALID jumbo test on this segment. Measured 2026-07-22:
# the N1081B boards echo at most a 1500-byte frame and the FEUs at most 2023 (their ICMP
# buffer), so a 9000-byte DF ping fails against ANY switch here. What this ladder DOES
# show is whether the switch passes anything above 1500 at all.
# Real jumbo verification is by capture during a run -- 03_test_plan.md Test 3.
for tgt in 192.168.10.83 192.168.10.240; do
  echo "  --- $tgt ---"
  for sz in 1472 1972 4000 8972; do
    ping -M do -s $sz -c1 -W2 "$tgt" >/dev/null 2>&1 && r=OK || r=FAIL
    printf "    payload %5d (frame %5d) : %s\n" "$sz" "$((sz+28))" "$r"
  done
done
echo "  EXPECT: FEU OK to 1972, FAIL at 4000;  N1081B OK at 1472 only. That is NORMAL."

echo
echo "DONE. Rollback: rm $TARGET && netplan apply   (backups in $BK)"
