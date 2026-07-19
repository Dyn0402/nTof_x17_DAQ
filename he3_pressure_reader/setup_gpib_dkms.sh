#!/usr/bin/env bash
#
# setup_gpib_dkms.sh — make the linux-gpib kernel module survive kernel upgrades.
#
# Registers the trimmed linux-gpib kernel source (ni_usb_gpib -> gpib_common)
# with DKMS so it auto-rebuilds on every new kernel. This is the durable fix for
# the 3He pressure reader dropping /dev/gpib0 after a kernel bump.
#
# Run with sudo:   sudo bash setup_gpib_dkms.sh
#
set -euo pipefail

PKG=linux-gpib-kernel
VER=4.3.7
# repo root = parent of this script's dir (he3_pressure_reader/), even under sudo
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_DIR=$(dirname "$SCRIPT_DIR")
STAGE="$REPO_DIR/gpib_build/dkms_src/${PKG}-${VER}"
KVER=$(uname -r)

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: run me with sudo (needs to write /usr/src and load modules)." >&2
    exit 1
fi

if [[ ! -f "$STAGE/dkms.conf" ]]; then
    echo "ERROR: staged source not found at $STAGE" >&2
    exit 1
fi

echo "== [1/6] ensure dkms is installed =="
if ! command -v dkms >/dev/null 2>&1; then
    apt-get install -y dkms
else
    echo "dkms already present: $(dkms --version 2>/dev/null || echo yes)"
fi

echo "== [2/6] install source into /usr/src =="
rm -rf "/usr/src/${PKG}-${VER}"
cp -a "$STAGE" "/usr/src/"

echo "== [3/6] dkms add (idempotent) =="
dkms add -m "$PKG" -v "$VER" 2>&1 | grep -v "already loaded" || true

echo "== [4/6] dkms build for $KVER =="
dkms build -m "$PKG" -v "$VER"

echo "== [5/6] dkms install for $KVER (force: supersede the hand-installed module) =="
dkms install --force -m "$PKG" -v "$VER"

echo "== [6/6] load + configure the board, then verify =="
depmod -a
modprobe -r ni_usb_gpib 2>/dev/null || true
modprobe ni_usb_gpib
sleep 1
# udev normally configures the board on module load; do it explicitly to be sure.
gpib_config --minor 0 2>/dev/null || true
sleep 1

echo
echo "==================== RESULT ===================="
echo "-- dkms status --"
dkms status -m "$PKG" -v "$VER"
echo "-- module loaded --"
lsmod | grep -E 'ni_usb_gpib|gpib_common' || echo "MODULE NOT LOADED (problem!)"
echo "-- auto-load alias present --"
grep -i '3923p709b' "/lib/modules/${KVER}/modules.alias" && echo "ALIAS OK" || echo "ALIAS MISSING (problem!)"
echo "-- device node --"
ls -l /dev/gpib0 2>&1 || echo "/dev/gpib0 MISSING (problem!)"
echo "==============================================="
echo
if [[ -e /dev/gpib0 ]]; then
    echo "SUCCESS: /dev/gpib0 is up and DKMS will rebuild on future kernels."
    echo "The he3_pressure_watcher will reconnect within ~5s."
else
    echo "WARNING: /dev/gpib0 not present — tell Claude the output above."
fi
