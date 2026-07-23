#!/usr/bin/env bash
# Run AFTER LanDB shows 8c:ec:4b:b4:ac:64 on device NTOF-X17-DAQ and has had
# ~30 min to propagate. Removes the I210 MAC clone so eno1 uses its real MAC.
#
# If the lease does not come back, it just means propagation is not done yet:
# re-enable the clone with the one-liner printed at the end and try again later.
set -uo pipefail
[[ $EUID -eq 0 ]] || { echo "run me with sudo" >&2; exit 1; }

F=/etc/netplan/01-eno1-cern.yaml
REAL_MAC=8c:ec:4b:b4:ac:64

cp -a "$F" "$F.pre-unclone" || exit 1
echo "backup: $F.pre-unclone"

sed -i '/^      macaddress:/d' "$F"
echo "removed the macaddress line; eno1 will use $REAL_MAC"

netplan generate || { echo "generate failed"; exit 1; }
netplan apply

echo "waiting up to 90 s for a lease on the real MAC..."
GOT=""
for i in $(seq 1 90); do
  sleep 1
  A=$(ip -4 -br addr show eno1 2>/dev/null | awk '{print $3}')
  [[ -n "$A" ]] && { GOT="$A"; break; }
done

echo
echo "mac: $(cat /sys/class/net/eno1/address)   (expect $REAL_MAC)"
ip -br addr show eno1
ip route | grep -E "eno1|default"
echo

case "$GOT" in
  128.141.177.*) printf '\033[32mSUCCESS: %s on the real MAC. Clone no longer needed.\033[0m\n' "$GOT" ;;
  172.20.*)      printf '\033[33mGot %s — the unregistered pen. LanDB has not propagated yet.\033[0m\n' "$GOT" ;;
  "")            printf '\033[31mNo lease — LanDB has not propagated yet.\033[0m\n' ;;
  *)             printf '\033[33mGot %s — unexpected.\033[0m\n' "$GOT" ;;
esac

echo
echo "To roll back to the clone if needed:"
echo "  sudo cp $F.pre-unclone $F && sudo netplan apply"
