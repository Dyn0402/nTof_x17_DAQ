#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on July 07 2026
Created in PyCharm
Created as nTof_x17_DAQ/gas_mixer_control/max_flow_test.py

@author: Dylan Neff, dylan
"""

import sys
import time
import glob

try:
    import propar
except ImportError:
    print("❌ Error: 'bronkhorst-propar' library not found.")
    print("Please run: pip install bronkhorst-propar")
    sys.exit(1)

MAX_SETPOINT = 32000  # Full-scale setpoint (100% flow)
HOLD_SECONDS = 30.0    # How long to hold both devices at max flow


def main():
    run_max_flow_test()
    print('donzo')


def find_serial_ports():
    """Scans Ubuntu filesystem for active USB serial or ACM diagnostic devices."""
    return glob.glob('/dev/ttyUSB*') + glob.glob('/dev/ttyACM*')


def extract_node_address(node):
    """Safely extracts integer node address across varying library versions."""
    if isinstance(node, int):
        return node
    if isinstance(node, dict):
        return node.get('address', node.get('node', 0))
    if hasattr(node, 'address'):
        return node.address
    return int(str(node))


def discover_devices():
    """Scans all serial ports and returns a list of (port, node_addr) for every
    responsive Bronkhorst controller found."""
    devices = []
    ports = find_serial_ports()
    if not ports:
        print("❌ No active serial ports found.")
        print("👉 Check your hardware connections or run: sudo usermod -aG dialout $USER")
        return devices

    print(f"🔍 System detected ports: {ports}")

    for port in ports:
        print(f"\nScanning port: {port}...")
        try:
            base_instrument = propar.instrument(port)
            nodes = base_instrument.master.get_nodes()

            if not nodes:
                print(f"⚠️ Port {port} opened, but no Bronkhorst controllers responded.")
                continue

            print(f"🎉 Detected {len(nodes)} controller(s) on {port}.")
            for raw_node in nodes:
                node_addr = extract_node_address(raw_node)
                devices.append((port, node_addr))

        except Exception as port_err:
            print(f"❌ Could not initialize master communication on {port}: {port_err}")
            print("   Ensure no other scripts or serial monitors are holding the port open.")

    return devices


def run_max_flow_test():
    print("====================================================")
    print("   BRONKHORST MAX-FLOW TEST (both devices @ 100%)   ")
    print("====================================================\n")

    devices = discover_devices()

    if len(devices) < 2:
        print(f"\n❌ Found {len(devices)} device(s); need 2 connected to run the max-flow test.")
        print("   Aborting without changing any setpoints.")
        return

    print(f"\n✅ Found {len(devices)} devices. Connecting to each...")

    mfcs = []
    for port, node_addr in devices:
        try:
            mfc = propar.instrument(port, node_addr)
            _ = mfc.measure  # Ping to confirm responsiveness before commanding flow
            mfcs.append((port, node_addr, mfc))
            print(f"   📡 {port} node {node_addr}: responsive")
        except Exception as err:
            print(f"   ❌ Failed to connect to {port} node {node_addr}: {err}")

    if len(mfcs) < 2:
        print(f"\n❌ Only {len(mfcs)} device(s) responded on connect; need 2. Aborting.")
        return

    try:
        # Command every device to full flow as close together as possible
        print(f"\n👉 Setting all devices to MAX flow ({MAX_SETPOINT})...")
        for port, node_addr, mfc in mfcs:
            mfc.setpoint = MAX_SETPOINT
            print(f"   ⏫ {port} node {node_addr}: setpoint -> {MAX_SETPOINT}")

        print(f"\n⏳ Holding max flow for {HOLD_SECONDS} seconds...")
        time.sleep(HOLD_SECONDS)

        print("\n📉 Readback while at max flow:")
        for port, node_addr, mfc in mfcs:
            try:
                print(f"   {port} node {node_addr}: {mfc.measure} / {MAX_SETPOINT}")
            except Exception as err:
                print(f"   {port} node {node_addr}: readback failed: {err}")

    finally:
        # Always zero out every valve before exiting, even on error/interrupt
        print("\n🔒 Safety shutdown: returning all setpoints to 0%...")
        for port, node_addr, mfc in mfcs:
            try:
                mfc.setpoint = 0
                print(f"   ✅ {port} node {node_addr}: setpoint -> 0")
            except Exception as err:
                print(f"   ⚠️ {port} node {node_addr}: FAILED to zero setpoint: {err}")

    print("\n✅ Max-flow test cycle completed.")


if __name__ == '__main__':
    main()
