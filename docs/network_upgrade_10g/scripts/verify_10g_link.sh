#!/usr/bin/env bash
# verify_10g_link.sh — 60-second go/no-go after installing the TX401 and/or the new switch.
#
#   ./verify_10g_link.sh            auto-detect the atlantic interface
#   ./verify_10g_link.sh enp1s0     name it explicitly
#
# Exits non-zero if any REQUIRED check fails. Warnings do not fail the run but are the
# things that quietly cost you performance.
set -uo pipefail

FAIL=0; WARN=0
ok()   { printf '  \033[32mPASS\033[0m  %s\n' "$*"; }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$*"; FAIL=$((FAIL+1)); }
warn() { printf '  \033[33mWARN\033[0m  %s\n' "$*"; WARN=$((WARN+1)); }
hdr()  { printf '\n\033[1m%s\033[0m\n' "$*"; }

# ------------------------------------------------------------------ find the card
hdr "1. Card present and driver bound"

BDF=$(lspci -Dn 2>/dev/null | awk '$3 ~ /^1d6a:/ {print $1}' | head -1)
if [[ -z "$BDF" ]]; then
  bad "no Aquantia/Marvell device (vendor 1d6a) on the PCI bus -- card not seated or dead"
  echo; echo "Aborting: nothing else can be checked."; exit 1
fi
ok "PCI device found at $BDF : $(lspci -s "${BDF#0000:}" | cut -d' ' -f2-)"

DRV=$(basename "$(readlink -f "/sys/bus/pci/devices/$BDF/driver" 2>/dev/null)" 2>/dev/null)
if [[ "$DRV" == "atlantic" ]]; then
  ok "driver bound: atlantic"
else
  bad "driver is '${DRV:-none}', expected 'atlantic' -- check: dmesg | grep -i atlantic"
fi

# ------------------------------------------------------------------ pcie width
hdr "2. PCIe link width  (the check people skip and regret)"

CW=$(cat "/sys/bus/pci/devices/$BDF/current_link_width" 2>/dev/null || echo 0)
CS=$(cat "/sys/bus/pci/devices/$BDF/current_link_speed" 2>/dev/null || echo "?")
echo "       negotiated: x$CW @ $CS"
if [[ "$CW" -ge 4 && "$CS" == 8.0* ]]; then
  ok "x$CW @ gen3 -- full 10 Gb capable (~3.9 GB/s)"
elif [[ "$CW" -ge 2 ]]; then
  warn "x$CW @ $CS -- below the x4 gen3 target; usable but leaves headroom on the table"
else
  warn "x$CW @ $CS -- caps throughput (x1 gen3 ~= 7.9 Gb/s, x1 gen2 ~= 4 Gb/s)."
  warn "     Expected slot is SLOT4: the LONG (x16-length) connector on the PCH side,"
  warn "     root port 00:1d.0, which is gen3 x4. Do not confuse it with the short x1"
  warn "     SLOT1 that holds the I210. See 00_system_readiness_2026-07-22.md §3."
fi

# As built 2026-07-22 the I210 was REMOVED, not relocated to SLOT1, and CERN moved to the
# onboard I219-LM (eno1). Its absence is now the expected state. See 05_as_built.
IBDF=$(lspci -Dn 2>/dev/null | awk '$3 ~ /^8086:1533/ {print $1}' | head -1)
if [[ -n "$IBDF" ]]; then
  warn "an I210 (8086:1533) is present at $IBDF. As built it should be REMOVED, and eno1"
  warn "     may be presenting its cloned MAC for CERN -- a duplicate MAC on the wire."
  warn "     See 05_as_built_2026-07-22.md §5 before going further."
else
  ok "I210 absent -- expected; CERN is on the onboard I219-LM (eno1)"
fi

# ------------------------------------------------------------------ interface
hdr "3. Interface and link rate"

IF=${1:-}
if [[ -z "$IF" ]]; then
  IF=$(ls "/sys/bus/pci/devices/$BDF/net" 2>/dev/null | head -1)
fi
if [[ -z "$IF" || ! -d /sys/class/net/$IF ]]; then
  bad "no net interface bound to $BDF -- driver loaded but no netdev created"
  echo; echo "Aborting."; exit 1
fi
ok "interface: $IF"

STATE=$(cat /sys/class/net/$IF/operstate 2>/dev/null)
[[ "$STATE" == "up" ]] && ok "operstate: up" || bad "operstate: $STATE -- check cable / switch port"

SPEED=$(cat /sys/class/net/$IF/speed 2>/dev/null || echo 0)
case "$SPEED" in
  10000) ok "link speed: 10000 Mb/s" ;;
  5000|2500) warn "link speed: $SPEED Mb/s -- NBASE-T, switch port is not 10 G" ;;
  1000)  warn "link speed: 1000 Mb/s -- card is fine, the OTHER END is 1 G."
         warn "     Expected if the area switch has not been upgraded yet."
         warn "     If the switch IS 10 G: check for Cat6a cable, and the switch port config." ;;
  *)     bad  "link speed: ${SPEED} Mb/s -- no link" ;;
esac

MTU=$(cat /sys/class/net/$IF/mtu)
[[ "$MTU" == "9000" ]] && ok "MTU 9000" || warn "MTU $MTU -- FEU readout expects 9000 (frame cap 8192)"

# ------------------------------------------------------------------ tuning
hdr "4. Offloads / queues  (only matters once the link is 10 G)"

if command -v ethtool >/dev/null; then
  OFF=$(ethtool -k "$IF" 2>/dev/null)
  for f in rx-checksumming generic-receive-offload scatter-gather; do
    if grep -q "^$f: on" <<<"$OFF"; then ok "$f on"; else warn "$f OFF -- costs CPU at 10 Gb"; fi
  done
  # `ethtool -l` returns "Operation not supported" on the atlantic driver -- it does not
  # export channel config. Count the card's MSI-X vectors instead, which is what actually
  # determines whether IRQ load spreads across cores.  (2026-07-22: atlantic reports 5.)
  RXQ=$(ethtool -l "$IF" 2>/dev/null | awk '/^Current hardware settings/,0' | awk '/^(RX|Combined):/{print $2}' | sort -rn | head -1)
  [[ -z "${RXQ:-}" ]] && RXQ=$(grep -c "[[:space:]]$IF\$" /proc/interrupts || echo 0)
  if [[ -n "${RXQ:-}" && "$RXQ" -gt 1 ]]; then
    ok "RSS: $RXQ rx queues / MSI-X vectors (spreads IRQ load across cores)"
  else
    warn "single rx queue -- one core will pin at 100% softirq near line rate"
  fi
  RING=$(ethtool -g "$IF" 2>/dev/null | awk '/^Current hardware settings/,0' | awk '/^RX:/{print $2; exit}')
  RINGMAX=$(ethtool -g "$IF" 2>/dev/null | awk '/^Pre-set maximums/,/^Current/' | awk '/^RX:/{print $2; exit}')
  if [[ -n "${RING:-}" && -n "${RINGMAX:-}" ]]; then
    if [[ "$RING" -lt "$RINGMAX" ]]; then
      warn "rx ring $RING of max $RINGMAX -- consider: ethtool -G $IF rx $RINGMAX"
    else
      ok "rx ring at max ($RING)"
    fi
  fi
fi

# ------------------------------------------------------------------ drops
hdr "5. Host-side drop counters  (were flatly ZERO on 1 GbE)"

DROPS=$(ethtool -S "$IF" 2>/dev/null | grep -iE 'rx_missed|rx_over|no_dma|rx_dropped|rx_fifo' || true)
if [[ -z "$DROPS" ]]; then
  echo "       (driver exports no such counters)"
else
  echo "$DROPS" | sed 's/^/       /'
  if echo "$DROPS" | awk -F: '{gsub(/ /,"",$2); if ($2+0 > 0) exit 1}'; then
    ok "no host-side drops"
  else
    warn "NON-ZERO host drops -- the bottleneck has moved INTO the host."
    warn "     Fix with ring size (ethtool -G) and RSS, not with more network."
  fi
fi

# ------------------------------------------------------------------ reachability
hdr "6. Reachability on 192.168.10.0/24"

echo "     FEUs:"
for ip in 43 44 81 82 83 110 111 118; do
  if ping -c1 -W1 "192.168.10.$ip" >/dev/null 2>&1; then
    printf '       \033[32mOK\033[0m    FEU .%s\n' "$ip"
  else
    printf '       \033[31mDOWN\033[0m  FEU .%s\n' "$ip"; FAIL=$((FAIL+1))
  fi
done

echo "     N1081B logic modules (ping only -- NEVER open a session to probe these):"
for ip in 240 241 242 243 244 245; do
  if ping -c1 -W1 "192.168.10.$ip" >/dev/null 2>&1; then
    printf '       \033[32mOK\033[0m    N1081B .%s\n' "$ip"
  else
    printf '       \033[31mDOWN\033[0m  N1081B .%s  <-- see n1081b/CLAUDE.md, do NOT retry connections\n' "$ip"
    FAIL=$((FAIL+1))
  fi
done

# ------------------------------------------------------------------ frame size
hdr "7. Frame-size ladder  (NOT a jumbo pass/fail -- see note)"

# Measured 2026-07-22: a 9000-byte DF ping is a FALSE FAILURE on this segment, on ANY
# switch. ICMP echo needs the *endpoint* to send a large frame back, and none can:
# the N1081B boards top out at a 1500-byte frame, the FEUs at 2023 (~2 kB echo buffer).
# This ladder therefore only answers "does the switch pass anything above 1500?".
# Real jumbo verification is by capture during a run -- 03_test_plan.md Test 3:
#   sudo tcpdump -i <if> -n -c 200 'src net 192.168.10.0/24 and udp' -e
# See 05_as_built_2026-07-22.md §9.

ABOVE_1500=0
for ip in 83 240; do
  line="     .$ip:"
  for sz in 1472 1972 4000; do
    if ping -c1 -W2 -M do -s "$sz" "192.168.10.$ip" >/dev/null 2>&1; then
      line+="  $((sz+28))=OK"
      [[ "$sz" -gt 1472 ]] && ABOVE_1500=1
    else
      line+="  $((sz+28))=--"
    fi
  done
  echo "$line"
done
echo "     expected: .83 (FEU) passes 2000 not 4028;  .240 (N1081B) passes 1500 only"

if [[ "$ABOVE_1500" == 1 ]]; then
  ok "switch forwards frames larger than 1500 -- it is not a 1500-only switch"
else
  warn "nothing above a 1500-byte frame got through. On the OLD switch this would be"
  warn "     new and suspicious; on a NEW switch it means jumbo is not enabled yet."
  warn "     Confirm with tcpdump during a run before trusting any IPD ladder."
fi

# ------------------------------------------------------------------ verdict
hdr "VERDICT"
if [[ "$FAIL" -eq 0 && "$WARN" -eq 0 ]]; then
  echo "  All checks passed. Proceed to Test 5 (bench IPD ladder) in 03_test_plan.md."
  exit 0
elif [[ "$FAIL" -eq 0 ]]; then
  echo "  $WARN warning(s), no failures. Read them -- they are the things that quietly"
  echo "  cost throughput. Safe to proceed if you understand each one."
  exit 0
else
  echo "  $FAIL failure(s), $WARN warning(s). Do NOT run the IPD ladder yet."
  exit 1
fi
