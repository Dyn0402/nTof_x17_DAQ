#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# STEP 1 of the NIC swap: put the MOTHERBOARD port (eno1, I219-LM) on the CERN
# network via DHCP.
#
# What this does NOT do: it does not configure enp4s0 (the TX401 / DREAM net).
# That is step 2, and this script deliberately parks enp4s0 as unmanaged so
# NetworkManager cannot invent a junk auto-profile for it in the meantime.
#
# Safe to run: the DREAM network is ALREADY down (eno1 is cabled to CERN now and
# cannot reach any FEU), so removing its static config from eno1 costs nothing.
# ---------------------------------------------------------------------------
set -uo pipefail

TS=$(date +%F_%H-%M-%S)
BK="/root/net-backup-$TS"
UBK="/home/mx17/net-backup-$TS"
OLD_I210_MAC="b4:96:91:4d:1a:95"     # recorded from this machine before the swap
STALE_UUID="36dcb314-6ac3-4b53-bff5-5379dd351c3b"   # "Profile 1", old CERN prof.

hdr() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
die() { printf '\033[31mABORT: %s\033[0m\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run me with sudo"

# ---------------------------------------------------------------- 0. diagnostics
hdr "0. DHCP lease history (root-only dir — this is why you're seeing it here)"
for f in /var/lib/NetworkManager/internal-*enp4s0.lease \
         /var/lib/NetworkManager/internal-*eno1.lease; do
  [[ -e "$f" ]] || continue
  echo "----- $f  (mtime $(stat -c %y "$f" | cut -d. -f1))"
  cat "$f"
done
echo "(A lease in 128.141.177.0/24 dated today means CERN DHCP already served"
echo " this box on whatever MAC was plugged in then — useful to know.)"

# ---------------------------------------------------------------- 1. backup
hdr "1. Backup"
mkdir -p "$BK" && cp -a /etc/netplan/. "$BK/" || die "backup failed"
mkdir -p "$UBK" && cp -a /etc/netplan/. "$UBK/" && chown -R mx17:mx17 "$UBK"
echo "netplan backed up to:"
echo "   $BK"
echo "   $UBK   (readable as mx17)"

# ---------------------------------------------------------------- 2. stale profile
hdr "2. Remove the orphaned CERN profile that is squatting on enp4s0"
if nmcli -t -f UUID con show 2>/dev/null | grep -q "$STALE_UUID"; then
  echo "Deleting 'Profile 1' ($STALE_UUID) — it was the I210's CERN DHCP profile,"
  echo "bound by INTERFACE NAME to enp4s0, which the TX401 has now inherited."
  nmcli con delete uuid "$STALE_UUID" || die "could not delete stale profile"
else
  echo "Not present (already gone) — nothing to do."
fi

# ---------------------------------------------------------------- 3. park enp4s0
hdr "3. Park enp4s0 (TX401) as unmanaged until step 2"
nmcli dev set enp4s0 managed no 2>/dev/null \
  && echo "enp4s0 -> unmanaged. Step 2 will re-enable with: nmcli dev set enp4s0 managed yes" \
  || echo "WARN: could not set enp4s0 unmanaged (harmless; watch for a junk auto-profile)"

# ---------------------------------------------------------------- 4. retire dream cfg
hdr "4. Retire the DREAM static config from eno1"
if [[ -f /etc/netplan/01-eno1-dream-daq.yaml ]]; then
  mv /etc/netplan/01-eno1-dream-daq.yaml "$BK/01-eno1-dream-daq.yaml.RETIRED"
  echo "Moved 01-eno1-dream-daq.yaml -> $BK/01-eno1-dream-daq.yaml.RETIRED"
  echo "Its contents (192.168.10.8/24, mtu 9000, the metric-9999 route) are what"
  echo "step 2 will re-apply to enp4s0. Do not delete this backup."
else
  echo "Already absent."
fi

# ---------------------------------------------------------------- 5. write cern cfg
hdr "5. Write the CERN profile for eno1"
cat > /etc/netplan/01-eno1-cern.yaml <<'YAML'
# CERN network on the motherboard port (eno1, Intel I219-LM).
# Written 2026-07-22 during the TX401 10 G swap: CERN moved from the I210 add-in
# card (removed) to this onboard port; the DREAM/FEU net moved to the TX401.
#
# MTU is left at the hardware default (1500) on purpose — jumbo belongs to the
# DREAM side only.
#
# If CERN DHCP refuses to hand out a lease, it is almost certainly LANDB MAC
# registration: the outlet is registered to the I210's MAC, not this port's.
# Uncomment the macaddress line below to present the old, registered MAC.
# Safe *because the I210 is physically out of the machine* — no duplicate.
network:
  version: 2
  renderer: NetworkManager
  ethernets:
    eno1:
      dhcp4: true
      dhcp6: true
      wakeonlan: true
      #macaddress: "b4:96:91:4d:1a:95"   # old I210 MAC, registered in LANDB
YAML
chmod 600 /etc/netplan/01-eno1-cern.yaml
chown root:root /etc/netplan/01-eno1-cern.yaml
echo "Wrote /etc/netplan/01-eno1-cern.yaml"

# ---------------------------------------------------------------- 6. apply
hdr "6. netplan generate + apply"
netplan generate || die "netplan generate failed — config is invalid, nothing applied"
netplan apply    || die "netplan apply failed"
echo "Applied. Waiting up to 45 s for a DHCP lease on eno1..."

GOT=""
for i in $(seq 1 45); do
  sleep 1
  A=$(ip -4 -br addr show eno1 2>/dev/null | awk '{print $3}')
  if [[ -n "$A" && "$A" != "192.168.10.8/24" ]]; then GOT="$A"; break; fi
done

# ---------------------------------------------------------------- 7. verify
hdr "7. Result"
ip -br addr show eno1
echo
ip route
echo

if [[ -z "$GOT" ]]; then
  printf '\033[31mNO LEASE on eno1 after 45 s.\033[0m\n'
  echo "This is the expected failure if LANDB has not been told about this port's"
  echo "MAC ($(cat /sys/class/net/eno1/address)). Fix, in order of speed:"
  echo
  echo "  A) Present the registered I210 MAC (instant, reversible):"
  echo "       sudo sed -i 's/^      #macaddress:/      macaddress:/' /etc/netplan/01-eno1-cern.yaml"
  echo "       sudo netplan apply && sleep 20 && ip -br addr show eno1"
  echo
  echo "  B) Register $(cat /sys/class/net/eno1/address) for this outlet at"
  echo "     https://network.cern.ch  (correct long-term fix; needs propagation)"
  echo
  echo "Nothing is broken — rerun is safe."
elif [[ "$GOT" == 128.141.177.* ]]; then
  printf '\033[32mLEASE OK: %s  — that is the CERN subnet.\033[0m\n' "$GOT"
else
  printf '\033[33mGot %s, which is NOT the expected 128.141.177.0/24.\033[0m\n' "$GOT"
  echo "Check which switch/outlet the motherboard port is actually cabled to."
fi

hdr "8. Connectivity check"
GW=$(ip route | awk '/^default/ && $5=="eno1" {print $3; exit}')
if [[ -n "$GW" ]]; then
  ping -c2 -W2 "$GW" >/dev/null 2>&1 && echo "  gateway $GW reachable" \
                                     || echo "  gateway $GW NOT reachable"
else
  echo "  no default route via eno1 (the USB tether still owns the default route —"
  echo "   that is fine, and it is why you have not lost anything by doing this)"
fi
getent hosts cern.ch >/dev/null 2>&1 && echo "  DNS resolves cern.ch" \
                                     || echo "  DNS does not resolve cern.ch yet"

hdr "Done — step 1 only"
echo "enp4s0 (TX401 / DREAM) is intentionally unconfigured and unmanaged."
echo "Say the word and I'll write step 2: static 192.168.10.8/24 + MTU 9000 on enp4s0."
