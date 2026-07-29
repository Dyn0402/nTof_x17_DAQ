#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# STEP 1b — fix up the CERN profile on eno1.
#
#   1. mtu: 1500          (the 9000 from the old DREAM config is still stuck on
#                          the interface; netplan does not reset an MTU it was
#                          never told about. My omission in the first script.)
#   2. macaddress clone   (present the I210's LANDB-registered MAC so CERN DHCP
#                          serves the real subnet instead of the 172.20.179.x
#                          unregistered pen)
#   3. route-metric 90    (make CERN the default route again, beating the USB
#                          tether's 100 — this restores how the box was before
#                          the swap. Drop this line to keep the tether primary.)
#   4. delete the junk auto-profile NM made for enp4s0
#
# Reversible: the previous file is backed up, and step 2 will own enp4s0.
# ---------------------------------------------------------------------------
set -uo pipefail

TS=$(date +%F_%H-%M-%S)
BK="/root/net-backup-$TS"
OLD_I210_MAC="b4:96:91:4d:1a:95"

hdr() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
die() { printf '\033[31mABORT: %s\033[0m\n' "$*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || die "run me with sudo"

hdr "1. Backup"
mkdir -p "$BK" && cp -a /etc/netplan/. "$BK/" || die "backup failed"
echo "  -> $BK"

hdr "2. Sanity: the I210 must NOT be in the machine (else we'd duplicate a MAC)"
if ip -br link | grep -qi "$OLD_I210_MAC"; then
  die "MAC $OLD_I210_MAC is live on an interface — do not clone it. Register the real MAC instead."
fi
if lspci -nn 2>/dev/null | grep -q "8086:1533"; then
  die "an I210 (8086:1533) is still on the PCI bus — do not clone its MAC."
fi
echo "  I210 absent from both the PCI bus and the interface list. Cloning is safe."

hdr "3. Rewrite /etc/netplan/01-eno1-cern.yaml"
cat > /etc/netplan/01-eno1-cern.yaml <<'YAML'
# CERN network on the motherboard port (eno1, Intel I219-LM).
# Written 2026-07-22 during the TX401 10 G swap: CERN moved from the I210 add-in
# card (now removed) to this onboard port; the DREAM/FEU net moved to the TX401
# in SLOT4 (enp4s0), which owns 192.168.10.8/24 + MTU 9000.
#
# mtu 1500: MUST be stated explicitly. This port previously carried the DREAM
# config at MTU 9000 and netplan will NOT reset an MTU it is not told about.
# Jumbo belongs to the DREAM side only.
#
# macaddress: this presents the removed I210's MAC, which is the one registered
# in CERN LANDB for this outlet. Without it DHCP parks us in the unregistered
# range 172.20.179.0/24 (observed 2026-07-22 15:44) instead of 128.141.177.0/24.
# Safe ONLY because the I210 is physically out of the machine.
#   PROPER FIX: register 8c:ec:4b:b4:ac:64 at https://network.cern.ch for this
#   outlet, then delete the macaddress line and re-apply.
#
# route-metric 90: beats the USB tether (100) so CERN is the default route
# again, as it was before the swap. Remove to leave the tether primary.
network:
  version: 2
  renderer: NetworkManager
  ethernets:
    eno1:
      dhcp4: true
      dhcp6: true
      wakeonlan: true
      mtu: 1500
      macaddress: "b4:96:91:4d:1a:95"
      dhcp4-overrides:
        route-metric: 90
YAML
chmod 600 /etc/netplan/01-eno1-cern.yaml
chown root:root /etc/netplan/01-eno1-cern.yaml
echo "  written"

hdr "4. Remove NM's junk auto-profile on enp4s0"
# netplan apply resets the 'managed no' flag, so NM invented one anyway.
for U in $(nmcli -t -f NAME,UUID,DEVICE con show 2>/dev/null \
           | awk -F: '$3=="enp4s0" && $1 ~ /^Wired connection/ {print $2}'); do
  echo "  deleting $U"
  nmcli con delete uuid "$U" >/dev/null 2>&1
done
echo "  done (step 2 will give enp4s0 a real profile)"

hdr "5. Apply"
netplan generate || die "netplan generate failed — nothing applied"
netplan apply    || die "netplan apply failed"

echo "  waiting up to 60 s for a DHCP lease on eno1..."
GOT=""
for i in $(seq 1 60); do
  sleep 1
  A=$(ip -4 -br addr show eno1 2>/dev/null | awk '{print $3}')
  [[ -n "$A" ]] && { GOT="$A"; break; }
done

hdr "6. Result"
echo "  eno1 mac now : $(cat /sys/class/net/eno1/address)   (expect $OLD_I210_MAC)"
echo "  eno1 mtu now : $(cat /sys/class/net/eno1/mtu)        (expect 1500)"
echo
ip -br addr show eno1
echo
ip route
echo

case "$GOT" in
  128.141.177.*)
    printf '\033[32m  SUCCESS: %s — the real CERN subnet.\033[0m\n' "$GOT"
    ;;
  172.20.*)
    printf '\033[33m  Got %s — still the UNREGISTERED pen.\033[0m\n' "$GOT"
    echo "  The MAC clone did not satisfy LANDB. Most likely the registration is"
    echo "  keyed to the outlet AND the switch port has changed, or the old entry"
    echo "  was released. Register the port at https://network.cern.ch."
    ;;
  "")
    printf '\033[31m  Still no lease.\033[0m\n'
    echo "  Check the cable is really in the motherboard port and the outlet is live:"
    echo "    ip -s link show eno1        # rx packets should be climbing"
    echo "    sudo journalctl -u NetworkManager --since '2 min ago' | grep -i dhcp"
    ;;
  *)
    printf '\033[33m  Got %s — unexpected subnet.\033[0m\n' "$GOT"
    ;;
esac

hdr "7. Connectivity"
for t in 128.141.177.1 cern.ch; do
  ping -c2 -W2 "$t" >/dev/null 2>&1 && echo "  $t reachable" || echo "  $t no reply"
done
echo "  NOTE: these can succeed via the USB tether even if eno1 is dead."
echo "  The honest test is 'ip route' above showing a default via eno1."
