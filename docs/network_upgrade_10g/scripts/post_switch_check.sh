#!/usr/bin/env bash
# post_switch_check.sh — run this the moment the new switch is in and cabled.
# Answers, in order, the only two questions that matter before you spend DAQ time:
#
#   1. Can I still talk to everything on the network?   (Test 4)
#   2. Am I getting the speed I expect?                 (Test 1/2)
#
#   ./post_switch_check.sh                 checks only, changes nothing
#   sudo ./post_switch_check.sh --tune     also raises the RX ring to max (needs root)
#
# Nothing here opens an N1081B session. Ping only. A board that does not ping needs
# n1081b/HANDOFF_2026-07-15_wedge_root_cause.md, NOT another connection attempt.
#
# Jumbo is deliberately NOT tested here — `ping -M do -s 8972` is a FALSE FAILURE on this
# segment on ANY switch (N1081B echo <=1500, FEUs <=2023). Use jumbo_capture.sh during a
# run instead. See 05_as_built_2026-07-22.md §9.
set -uo pipefail

IF=enp4s0
TUNE=0
BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/baseline_preswitch_2026-07-22"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --iface) IF="$2"; shift 2 ;;
    --tune)  TUNE=1; shift ;;
    -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

FAIL=0; WARN=0
ok()   { printf '  \033[32mPASS\033[0m  %s\n' "$*"; }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$*"; FAIL=$((FAIL+1)); }
warn() { printf '  \033[33mWARN\033[0m  %s\n' "$*"; WARN=$((WARN+1)); }
hdr()  { printf '\n\033[1m=== %s\033[0m\n' "$*"; }

FEUS=(43 44 81 82 83 110 111 118)
BOARDS=(240 241 242 243 244 245)

# ============================================================ Q1: reachability
hdr "Q1  Can I still talk to everything?"

echo "  FEUs (8):"
for ip in "${FEUS[@]}"; do
  if ping -c2 -W1 "192.168.10.$ip" >/dev/null 2>&1; then ok "FEU  .$ip"
  else bad "FEU  .$ip  *** DOWN *** — check its switch port link LED and the patch label"; fi
done

echo "  N1081B logic modules (6) — ping only, NEVER open a session to probe these:"
for ip in "${BOARDS[@]}"; do
  if ping -c2 -W1 "192.168.10.$ip" >/dev/null 2>&1; then ok "N1081B .$ip"
  else bad "N1081B .$ip  *** DOWN *** — do NOT retry connections; check link LED first,
             then treat as wedged (n1081b/HANDOFF_2026-07-15_wedge_root_cause.md)"; fi
done

# MAC check — catches "it pings, but that IP is now a different box" after a re-patch.
if [[ -f "$BASE/baseline.md" ]]; then
  echo "  MAC identity vs pre-swap baseline:"
  MIS=0
  while read -r bip bmac; do
    now=$(ip neigh show "$bip" dev "$IF" 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="lladdr") print $(i+1)}')
    if [[ -z "$now" ]]; then continue; fi
    if [[ "$now" != "$bmac" ]]; then
      bad "$bip MAC changed: was $bmac now $now — the IP moved to different hardware"; MIS=1
    fi
  done < <(awk '/^192\.168\.10\./ {print $1, $3}' "$BASE/baseline.md")
  [[ "$MIS" == 0 ]] && ok "all resolved MACs match the pre-swap map"
else
  warn "no baseline at $BASE — cannot verify MAC identity"
fi

# ============================================================ Q2: speed
hdr "Q2  Am I getting the speed I expect?"

DRV=$(basename "$(readlink -f "/sys/class/net/$IF/device/driver" 2>/dev/null)" 2>/dev/null)
[[ "$DRV" == atlantic ]] && ok "$IF is the 10G card (driver atlantic)" \
  || bad "$IF driver is '${DRV:-none}', expected atlantic — WRONG INTERFACE (see the name trap, 05_as_built §3)"

SPEED=$(cat "/sys/class/net/$IF/speed" 2>/dev/null || echo 0)
case "$SPEED" in
  10000) ok "link speed 10000 Mb/s  <-- this is the goal" ;;
  5000|2500)
    bad "link speed $SPEED Mb/s — NBASE-T fallback, the port did not negotiate 10 G."
    warn "     2.5G alone predicts an IPD threshold near 28. If the ladder lands there, this is why."
    warn "     Check: Cat6a cable, switch port is a real 10G port, port speed not pinned in switch config." ;;
  1000)
    bad "link speed 1000 Mb/s — NOTHING CHANGED. Either the DAQ uplink is still in a 1 G port,"
    warn "     or the cable is not Cat6a. Do not run any ladder until this reads 10000." ;;
  *) bad "link speed '${SPEED}' — no link. Check cable seated at both ends." ;;
esac

if command -v ethtool >/dev/null; then
  LP=$(ethtool "$IF" 2>/dev/null | awk '/Link partner advertised link modes/,/Link partner advertised pause/' | tr -s ' ')
  [[ -n "$LP" ]] && { echo "  link partner advertises:"; echo "$LP" | sed 's/^/       /'; }
fi

MTU=$(cat "/sys/class/net/$IF/mtu")
[[ "$MTU" == 9000 ]] && ok "MTU 9000" || bad "MTU $MTU — must be 9000 (FEU frame cap 8192)"

# PCIe width — a re-seat during the switch work can silently drop it
BDF=$(basename "$(readlink -f "/sys/class/net/$IF/device")")
CW=$(cat "/sys/bus/pci/devices/$BDF/current_link_width" 2>/dev/null || echo 0)
CS=$(cat "/sys/bus/pci/devices/$BDF/current_link_speed" 2>/dev/null || echo '?')
[[ "$CW" -ge 4 && "$CS" == 8.0* ]] && ok "PCIe x$CW @ $CS" \
  || warn "PCIe x$CW @ $CS — below the x4 gen3 as-built value; card may have been disturbed"

# ---- RX ring: raise before any low-IPD ladder ----
RING=$(ethtool -g "$IF" 2>/dev/null | awk '/^Current hardware settings/,0' | awk '/^RX:/{print $2; exit}')
RMAX=$(ethtool -g "$IF" 2>/dev/null | awk '/^Pre-set maximums/,/^Current/' | awk '/^RX:/{print $2; exit}')
if [[ -n "${RING:-}" && "$RING" -lt "${RMAX:-0}" ]]; then
  if [[ "$TUNE" == 1 && $EUID -eq 0 ]]; then
    ethtool -G "$IF" rx "$RMAX" && ok "RX ring raised $RING -> $RMAX (NOT persistent across reboot)"
  else
    warn "RX ring $RING of $RMAX — raise it before the low-IPD ladder:  sudo ethtool -G $IF rx $RMAX"
  fi
else
  ok "RX ring at max (${RING:-?})"
fi

# ---- drop counters: were flatly ZERO on 1 GbE; non-zero now = bottleneck moved into the host
# NOTE: `atlantic` does NOT use the intel names (rx_missed_errors / rx_over_errors /
# rx_no_dma_resources) that 03_test_plan.md Test 5 and Test 7 tell you to watch. Those
# greps return NOTHING on this card and read as "clean" when they are simply absent.
# The equivalents are: InErrors, InDroppedDma, Queue[N] InErrors, Queue[N] AllocFails.
hdr "Host-side drop counters (baseline: all zero)"
D=$(ethtool -S "$IF" 2>/dev/null | grep -E 'InErrors|InDroppedDma|AllocFails|InLroPackets|Dma.*[Dd]rop' || true)
if [[ -z "$D" ]]; then warn "driver exports no drop counters — cannot see host-side loss"; else
  echo "$D" | grep -vE ': 0$' | sed 's/^/       /' || true
  NZ=$(echo "$D" | awk -F: '{gsub(/ /,"",$2); if ($2+0 > 0) print}' | grep -v LroPackets || true)
  if [[ -z "$NZ" ]]; then ok "no host-side drops (InErrors / InDroppedDma / AllocFails all 0)"
  else warn "NON-ZERO host drops above — bottleneck may have moved INTO the host."
       warn "     Fix with RX ring size (ethtool -G) and RSS, not with more network."; fi
fi

# ---- free jumbo counter: atlantic counts jumbo frames per queue, no tcpdump needed
hdr "Jumbo frames received (atlantic counts these natively)"
JUMBO=$(ethtool -S "$IF" 2>/dev/null | awk -F: '/InJumboPackets/ {gsub(/ /,"",$2); s+=$2} END{print s+0}')
echo "       InJumboPackets (sum over queues) = $JUMBO"
if [[ "$JUMBO" -gt 0 ]]; then
  ok "jumbo frames ARE arriving — the switch passes >1500 on the readout path"
else
  echo "       0 is EXPECTED when idle — only readout produces jumbo frames."
  echo "       Re-read this counter DURING a run; if it stays 0 while data flows, the"
  echo "       switch is clamping to MTU 1500. Cross-check with jumbo_capture.sh."
fi

# ---- optional throughput, only if a 10G peer exists
hdr "Throughput (optional — needs a 10 G iperf3 peer)"
if ! command -v iperf3 >/dev/null; then
  echo "  iperf3 not installed. Install while CERN (eno1) is up:  sudo apt install iperf3"
  echo "  Without a 10 G peer there is no synthetic throughput test — the REAL throughput"
  echo "  proof is the bench IPD ladder (Test 5), which needs no beam and no peer."
else
  echo "  iperf3 present. With a peer:"
  echo "    iperf3 -c <peer> -t 30 -P 4        # TCP,  PASS >= 9.0 Gb/s"
  echo "    iperf3 -c <peer> -t 30 -u -b 9G    # UDP,  watch the LOSS column — readout is UDP"
fi

# ============================================================ verdict
hdr "VERDICT"
if [[ "$FAIL" -eq 0 ]]; then
  echo "  ${WARN} warning(s), no failures."
  echo "  NEXT:  1. jumbo_capture.sh  during a short run   (the silent killer)"
  echo "         2. run_config_raw_ipd_10g.py smoke point  (RAW, IPD 10)"
  exit 0
else
  echo "  $FAIL failure(s), $WARN warning(s).  Do NOT start a ladder."
  exit 1
fi
