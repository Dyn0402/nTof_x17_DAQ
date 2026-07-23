#!/usr/bin/env bash
# net_baseline.sh — snapshot the DAQ network + storage state BEFORE the 10 GbE upgrade,
# so the "after" has something to be compared against.
#
#   ./net_baseline.sh                          full snapshot -> baseline/<timestamp>/
#   ./net_baseline.sh --sample-only            just the live rate sampler (Test 0)
#   ./net_baseline.sh --iface eno1 --seconds 60
#   ./net_baseline.sh --no-disk                skip the disk write test
#
# The disk test writes a 4 GB file to /mnt/data and deletes it. Skip it during a run.
set -uo pipefail

IFACE=eno1
SECONDS_SAMPLE=60
DO_DISK=1
SAMPLE_ONLY=0
DISK_PATH=/mnt/data

while [[ $# -gt 0 ]]; do
  case "$1" in
    --iface)       IFACE="$2"; shift 2 ;;
    --seconds)     SECONDS_SAMPLE="$2"; shift 2 ;;
    --no-disk)     DO_DISK=0; shift ;;
    --sample-only) SAMPLE_ONLY=1; shift ;;
    -h|--help)     sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ ! -d /sys/class/net/$IFACE ]]; then
  echo "no such interface: $IFACE" >&2; exit 1
fi

# ---------------------------------------------------------------- rate sampler
# Samples at 100 ms so the spill BURST rate is visible, not just the duty-averaged rate.
# The DAQ is bursty (~46 ms of spill in a ~1.2 s cycle), so the mean badly understates
# how full the link gets. The peak is the number that matters for saturation.
sample_rate() {
  local iface=$1 secs=$2
  local f=/sys/class/net/$iface/statistics/rx_bytes
  local n=$(( secs * 10 ))
  local prev cur peak=0 total_start total_end
  prev=$(<"$f"); total_start=$prev
  for ((i=0; i<n; i++)); do
    sleep 0.1
    cur=$(<"$f")
    local mbps=$(( (cur - prev) * 8 * 10 / 1000000 ))
    (( mbps > peak )) && peak=$mbps
    prev=$cur
  done
  total_end=$prev
  local mean=$(( (total_end - total_start) * 8 / secs / 1000000 ))
  echo "iface        : $iface"
  echo "link speed   : $(cat /sys/class/net/$iface/speed 2>/dev/null) Mb/s"
  echo "window       : ${secs}s @ 100 ms sampling"
  echo "PEAK rx rate : ${peak} Mb/s      <-- compare against link speed"
  echo "mean rx rate : ${mean} Mb/s      (duty-averaged; expected to be far lower)"
  echo
  local speed; speed=$(cat /sys/class/net/$iface/speed 2>/dev/null || echo 0)
  if (( speed > 0 )); then
    local pct=$(( peak * 100 / speed ))
    echo "peak = ${pct}% of line rate"
    if (( pct >= 88 )); then
      echo "VERDICT: link is SATURATING -> the host link is the constraint. Premise confirmed."
    elif (( pct >= 60 )); then
      echo "VERDICT: heavily loaded but not obviously pinned. Sample during a real"
      echo "         RAW/low-IPD burst before concluding."
    else
      echo "VERDICT: link NOT saturated in this window. Either no readout was running,"
      echo "         or the constraint is elsewhere -- investigate before buying a switch."
    fi
  fi
}

if (( SAMPLE_ONLY )); then
  sample_rate "$IFACE" "$SECONDS_SAMPLE"
  exit 0
fi

# ---------------------------------------------------------------- full snapshot
HERE=$(cd "$(dirname "$0")" && pwd)
OUT="$HERE/../baseline/$(date +%Y-%m-%d_%H%M%S)"
mkdir -p "$OUT"
echo "writing baseline to $OUT"

{
  echo "=== date ==="; date -Is
  echo; echo "=== kernel / model ==="
  uname -a
  cat /sys/class/dmi/id/sys_vendor /sys/class/dmi/id/product_name 2>/dev/null
  echo; echo "=== cpu / mem ==="; lscpu | grep -E 'Model name|^CPU\(s\)'; free -g
} > "$OUT/system.txt" 2>&1

{
  echo "=== ip -br addr ==="; ip -br addr
  echo; echo "=== ip -br link ==="; ip -br link
  echo; echo "=== routes ==="; ip route
  echo; echo "=== arp ==="; arp -an | grep 192.168.10 | sort -t. -k4 -n
} > "$OUT/network.txt" 2>&1

for i in $(ls /sys/class/net | grep -v '^lo$'); do
  {
    echo "=== $i: ethtool ==="       ; ethtool "$i"
    echo; echo "=== $i: driver ==="  ; ethtool -i "$i"
    echo; echo "=== $i: offloads ===" ; ethtool -k "$i"
    echo; echo "=== $i: rings ==="   ; ethtool -g "$i"
    echo; echo "=== $i: channels ===" ; ethtool -l "$i"
    echo; echo "=== $i: coalesce ===" ; ethtool -c "$i"
    echo; echo "=== $i: stats ==="   ; ethtool -S "$i"
    echo; echo "=== $i: mtu ==="     ; cat "/sys/class/net/$i/mtu"
  } > "$OUT/iface_$i.txt" 2>&1
done

{
  echo "=== lspci -nn ==="; lspci -nn
  echo; echo "=== pcie link state ==="
  for d in /sys/bus/pci/devices/*/; do
    [[ -e "$d/max_link_width" ]] || continue
    printf '%s  max=%s/%s  cur=%s/%s  %s\n' "$(basename "$d")" \
      "$(cat "$d/max_link_speed" 2>/dev/null)" "$(cat "$d/max_link_width" 2>/dev/null)" \
      "$(cat "$d/current_link_speed" 2>/dev/null)" "$(cat "$d/current_link_width" 2>/dev/null)" \
      "$(lspci -s "$(basename "$d" | cut -d: -f2-)" 2>/dev/null | cut -d' ' -f2-)"
  done
} > "$OUT/pci.txt" 2>&1

{
  echo "=== FEUs ==="
  for ip in 43 44 81 82 83 110 111 118; do
    if ping -c1 -W1 "192.168.10.$ip" >/dev/null 2>&1; then echo "  .$ip OK"; else echo "  .$ip DOWN"; fi
  done
  echo "=== N1081B (ping only -- never open a session to probe) ==="
  for ip in 240 241 242 243 244 245; do
    if ping -c1 -W1 "192.168.10.$ip" >/dev/null 2>&1; then echo "  .$ip OK"; else echo "  .$ip DOWN"; fi
  done
  echo "=== jumbo path (9000 B, DF) ==="
  for ip in 83 240; do
    if ping -c1 -W1 -M do -s 8972 "192.168.10.$ip" >/dev/null 2>&1; then
      echo "  .$ip jumbo OK"; else echo "  .$ip jumbo FAIL"; fi
  done
} > "$OUT/reachability.txt" 2>&1

{
  echo "=== df ==="; df -h | grep -vE 'tmpfs|loop'
} > "$OUT/storage.txt" 2>&1

if (( DO_DISK )); then
  if [[ -w "$DISK_PATH" ]]; then
    echo "disk write test (4 GB to $DISK_PATH) ..."
    {
      echo "=== sustained write, 4 GB, O_DIRECT-ish (dd oflag=direct) ==="
      dd if=/dev/zero of="$DISK_PATH/.net_baseline_test" bs=1M count=4096 \
         oflag=direct conv=fsync 2>&1 || \
      dd if=/dev/zero of="$DISK_PATH/.net_baseline_test" bs=1M count=4096 \
         conv=fsync 2>&1
      echo
      echo "NOTE: 10 GbE line rate is ~1250 MB/s. If this number is far below that,"
      echo "      storage becomes the binding constraint once the network stops being one."
    } >> "$OUT/storage.txt" 2>&1
    rm -f "$DISK_PATH/.net_baseline_test"
  else
    echo "SKIPPED: $DISK_PATH not writable" >> "$OUT/storage.txt"
  fi
fi

echo "sampling $IFACE for ${SECONDS_SAMPLE}s ..."
sample_rate "$IFACE" "$SECONDS_SAMPLE" | tee "$OUT/rate_sample.txt"

echo
echo "baseline written to $OUT"
ls -1 "$OUT"
