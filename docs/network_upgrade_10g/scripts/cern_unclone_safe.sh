#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Remove the I210 MAC clone from eno1 (CERN), AND restore mtu 1500.
#
# SAFE-BY-CONSTRUCTION for a REMOTE session:
#   * run it detached (see the invocation below) so an SSH drop cannot kill it
#     half-way and strand you with no CERN link;
#   * if the real MAC does not get a working CERN lease within the timeout, it
#     AUTOMATICALLY restores the clone and re-applies. Worst case you end up
#     exactly where you started, still reachable.
#
# Does NOT touch enp4s0 / the DREAM network, so a run in progress is unaffected.
#
# INVOKE LIKE THIS (detached, survives disconnect):
#   sudo setsid nohup bash docs/network_upgrade_10g/scripts/cern_unclone_safe.sh \
#        > /home/mx17/cern_unclone.log 2>&1 < /dev/null &
#   tail -f /home/mx17/cern_unclone.log
# ---------------------------------------------------------------------------
set -uo pipefail

F=/etc/netplan/01-eno1-cern.yaml
REAL_MAC=8c:ec:4b:b4:ac:64
CLONE_MAC=b4:96:91:4d:1a:95
TIMEOUT=120
STAMP=$(date +%F_%H-%M-%S)
BK="$F.pre-unclone-$STAMP"
RESULT=/home/mx17/cern_unclone_result.txt

log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
[[ $EUID -eq 0 ]] || { echo "run me with sudo"; exit 1; }

log "=== eno1 CERN unclone + MTU fix ==="
log "before: mac=$(cat /sys/class/net/eno1/address) mtu=$(cat /sys/class/net/eno1/mtu)"
log "        addr=$(ip -4 -br addr show eno1 | awk '{print $3}')"

# ---------------------------------------------------------------- backup
cp -a "$F" "$BK" || { log "backup FAILED, aborting"; exit 1; }
log "backup -> $BK"
log "--- current $F ---"; sed 's/^/    /' "$F"

# ---------------------------------------------------------------- edit
# Preserve whatever else is in the file (the other session rewrote it); only
# drop the clone and force mtu 1500.
sed -i '/^[[:space:]]*macaddress:/d' "$F"
if grep -qE '^[[:space:]]*mtu:' "$F"; then
  sed -i 's/^\([[:space:]]*\)mtu:.*/\1mtu: 1500/' "$F"
else
  sed -i '/^[[:space:]]*eno1:[[:space:]]*$/a\      mtu: 1500' "$F"
fi
log "--- new $F ---"; sed 's/^/    /' "$F"

if ! netplan generate; then
  log "netplan generate FAILED -> restoring and aborting"
  cp -a "$BK" "$F"; netplan generate && netplan apply
  exit 1
fi

# ---------------------------------------------------------------- apply
log "applying (eno1 will bounce; SSH over CERN will drop here)"
netplan apply
sleep 3
# force a fresh lease on the new MAC
nmcli dev disconnect eno1 >/dev/null 2>&1
sleep 2
nmcli dev connect eno1 >/dev/null 2>&1

# ---------------------------------------------------------------- verify
log "waiting up to ${TIMEOUT}s for a working CERN lease on $REAL_MAC ..."
OK=0; ADDR=""
for i in $(seq 1 $TIMEOUT); do
  sleep 1
  ADDR=$(ip -4 -br addr show eno1 2>/dev/null | awk '{print $3}')
  [[ "$ADDR" == 128.141.177.* ]] || continue
  GW=$(ip route | awk '/^default/ && $5=="eno1" {print $3; exit}')
  [[ -n "$GW" ]] || continue
  ping -c1 -W2 "$GW" >/dev/null 2>&1 || continue
  OK=1; break
done

# ---------------------------------------------------------------- verdict
if [[ "$OK" == 1 ]]; then
  log "SUCCESS"
  log "  mac  = $(cat /sys/class/net/eno1/address)   (real hardware MAC)"
  log "  mtu  = $(cat /sys/class/net/eno1/mtu)"
  log "  addr = $ADDR   gateway reachable"
  getent hosts cern.ch >/dev/null 2>&1 && log "  DNS OK" || log "  DNS not resolving yet"
  {
    echo "UNCLONE SUCCESS $STAMP"
    echo "eno1 mac  : $(cat /sys/class/net/eno1/address)"
    echo "eno1 addr : $ADDR"
    echo "eno1 mtu  : $(cat /sys/class/net/eno1/mtu)"
    echo "reconnect : ssh mx17@ntof-x17-daq.dyndns.cern.ch"
    echo "NEXT: delete the $CLONE_MAC row in LanDB (device NTOF-X17-DAQ)"
  } > "$RESULT"; chown mx17:mx17 "$RESULT"
  log "wrote $RESULT"
  log ""
  log "*** IF YOUR SSH DROPPED, RECONNECT TO: $ADDR ***"
  log "*** or by name: ntof-x17-daq.dyndns.cern.ch  (dyndns follows the MAC) ***"
else
  log "NO WORKING CERN LEASE after ${TIMEOUT}s (addr='$ADDR')"
  log "LanDB has probably not propagated. ROLLING BACK to the clone automatically."
  cp -a "$BK" "$F"
  netplan generate && netplan apply
  sleep 3
  nmcli dev disconnect eno1 >/dev/null 2>&1; sleep 2; nmcli dev connect eno1 >/dev/null 2>&1
  for i in $(seq 1 60); do
    sleep 1
    ADDR=$(ip -4 -br addr show eno1 2>/dev/null | awk '{print $3}')
    [[ "$ADDR" == 128.141.177.* ]] && break
  done
  log "rolled back: mac=$(cat /sys/class/net/eno1/address) addr=$ADDR"
  {
    echo "UNCLONE FAILED, ROLLED BACK $STAMP"
    echo "eno1 mac  : $(cat /sys/class/net/eno1/address)  (clone restored)"
    echo "eno1 addr : $ADDR"
    echo "Retry later; LanDB propagation of $REAL_MAC was not complete."
  } > "$RESULT"; chown mx17:mx17 "$RESULT"
  log "wrote $RESULT — you should still be reachable at $ADDR"
fi

log "note: enp4s0 / DREAM untouched -> $(ip -4 -br addr show enp4s0 | awk '{print $3}') mtu $(cat /sys/class/net/enp4s0/mtu)"
log "=== done ==="
