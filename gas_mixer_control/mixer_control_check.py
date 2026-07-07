#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on July 07 10:25 AM 2026
Created in PyCharm
Created as nTof_x17_DAQ/mixer_control_check.py

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


def main():
    run_diagnostics()
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


def run_diagnostics():
    print("====================================================")
    print("   BRONKHORST FLOW CONTROLLER LINUX DIAGNOSTIC Tool ")
    print("====================================================\n")

    ports = find_serial_ports()
    if not ports:
        print("❌ No active serial ports found.")
        print("👉 Check your hardware connections or run: sudo usermod -aG dialout $USER")
        return

    print(f"🔍 System detected ports: {ports}")

    for port in ports:
        print(f"\nTesting connection on port: {port}...")
        try:
            # Open a baseline connection to probe the master bus gateway
            base_instrument = propar.instrument(port)
            master_bus = base_instrument.master

            print("🔄 Scanning FLOW-BUS network for daisy-chained nodes...")
            nodes = master_bus.get_nodes()

            if not nodes:
                print(f"⚠️ Port {port} opened, but no Bronkhorst controllers responded.")
                continue

            print(f"🎉 Success! Detected {len(nodes)} controller(s) on {port}.")

            for index, raw_node in enumerate(nodes, start=1):
                node_addr = extract_node_address(raw_node)
                print(f"\n--- [Device #{index}] Configuring Node Address: {node_addr} ---")

                try:
                    # Targeted connection directly to the specific node identifier
                    mfc = propar.instrument(port, node_addr)

                    # Read baseline status
                    initial_flow = mfc.measure
                    print(f"   📡 Ping: Responsive")
                    print(f"   📈 Initial Flow Rate: {initial_flow} (out of 32000)")

                    # --- FUNCTIONAL VALVE TEST ---
                    print("   🧪 Initializing brief operational test...")

                    print("   👉 Setting setpoint to 0% (0)...")
                    mfc.setpoint = 0
                    time.sleep(1.5)

                    # We will command 10% flow capacity (10% of 32000 = 3200)
                    test_target = 3200
                    print(f"   👉 Blipping setpoint up to 10% ({test_target})...")
                    mfc.setpoint = test_target

                    print("   ⏳ Waiting 3.5 seconds for valve physics to settle...")
                    time.sleep(3.5)

                    readback_flow = mfc.measure
                    print(f"   📉 Readback Flow Rate: {readback_flow} / 32000")

                    if readback_flow > 0:
                        print("   ✅ VERDICT: Device active and reading physical changes.")
                    else:
                        print("   ❓ VERDICT: Electronic communication works, but no flow change.")
                        print("      (Normal if the unit is dry on a test bench with no gas pressure!)")

                    # Always zero out the valve before exiting the script for safety
                    print("   🔒 Safety shutdown: Returning setpoint to 0%...")
                    mfc.setpoint = 0
                    print("   ✅ Device test cycle completed successfully.")

                except Exception as node_err:
                    print(f"   ❌ Failed to probe individual node {node_addr}: {node_err}")

        except Exception as port_err:
            print(f"❌ Could not initialize master communication on {port}: {port_err}")
            print("   Ensure no other scripts or serial monitors are holding the port open.")


if __name__ == '__main__':
    main()
