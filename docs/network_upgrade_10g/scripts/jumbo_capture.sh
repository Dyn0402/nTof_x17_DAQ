#!/usr/bin/env bash
# jumbo_capture.sh — the ONLY valid jumbo test on the 192.168.10.0/24 segment.
#
# Run this WHILE A RUN IS TAKING DATA. It histograms the actual FEU->host readout frame
# sizes on the wire.
#
#   sudo ./jumbo_capture.sh                  200 frames on enp4s0
#   sudo ./jumbo_capture.sh --iface enp4s0 --count 500
#
# WHY NOT ping: `ping -M do -s 8972` is a FALSE FAILURE here on ANY switch. ICMP echo needs
# the ENDPOINT to send a big frame back and none can — N1081B boards top out at a 1500-byte
# frame, FEUs at 2023 (~2 kB echo buffer, binary-searched 2026-07-22). The jumbo path that
# matters is one-directional, FEU -> host, up to the FEU frame cap 8192 (MultiPackThr 4888),
# and it is only exercised during a run. See 05_as_built_2026-07-22.md §9.
#
# THE TRAP THIS CATCHES: a new switch silently defaulting to MTU 1500 clamps every readout
# frame near 1500. That looks like DATA CORRUPTION, not like a config error, and it will make
# a perfectly good 10 G link produce a garbage IPD ladder.
#
# CHEAPER FIRST CHECK, no root and no tcpdump: the atlantic driver counts jumbo frames
# itself. During a run:
#     ethtool -S enp4s0 | grep InJumboPackets      # must be climbing
# If that is climbing, jumbo works and you do not need this script. Use this one to see the
# actual size DISTRIBUTION, or when the counter is ambiguous.
set -uo pipefail

IF=enp4s0
COUNT=200
while [[ $# -gt 0 ]]; do
  case "$1" in
    --iface) IF="$2"; shift 2 ;;
    --count) COUNT="$2"; shift 2 ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
[[ $EUID -eq 0 ]] || { echo "must run as root (tcpdump)"; exit 1; }

echo "Capturing $COUNT readout frames on $IF ... (a run must be ACTIVE, or this hangs)"
TMP=$(mktemp)
timeout 120 tcpdump -i "$IF" -n -c "$COUNT" 'src net 192.168.10.0/24 and udp' -e 2>/dev/null \
  | grep -oE 'length [0-9]+' | awk '{print $2}' > "$TMP"

N=$(wc -l < "$TMP")
if [[ "$N" -eq 0 ]]; then
  echo "NO UDP FRAMES CAPTURED. Is a run actually taking data right now?"
  rm -f "$TMP"; exit 1
fi

echo
echo "  frames captured : $N"
echo "  size histogram  :"
sort -n "$TMP" | uniq -c | sort -rn | head -10 | awk '{printf "     %6d frames  @ %s bytes\n", $1, $2}'
MAX=$(sort -n "$TMP" | tail -1)
P50=$(sort -n "$TMP" | awk '{a[NR]=$1} END{print a[int(NR/2)]}')
echo "  median          : $P50"
echo "  max             : $MAX"
echo
ABOVE=$(awk '$1 > 1500' "$TMP" | wc -l)
rm -f "$TMP"

if [[ "$MAX" -gt 4000 ]]; then
  printf '  \033[32mPASS\033[0m  jumbo is passing — max %s bytes, %s/%s frames above 1500\n' "$MAX" "$ABOVE" "$N"
  echo "        Proceed to the IPD ladder."
elif [[ "$MAX" -gt 1500 ]]; then
  printf '  \033[33mWARN\033[0m  frames exceed 1500 (max %s) but fall short of the 8192 FEU cap.\n' "$MAX"
  echo "        Switch may be at an intermediate MTU. Check the switch jumbo setting."
else
  printf '  \033[31mFAIL\033[0m  every frame clamped at/below 1500 — THE SWITCH IS NOT PASSING JUMBO.\n'
  echo "        Enable jumbo on the switch (often global, often needs a reboot) and recapture."
  echo "        DO NOT run the IPD ladder: you would measure an MTU artefact and conclude"
  echo "        the 10 G upgrade failed."
  exit 1
fi
