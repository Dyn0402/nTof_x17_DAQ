#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on October 17 17:56 2025
Created in PyCharm
Created as Cosmic_Bench_DAQ_Control/weiner_ps_monitor

@author: Dylan Neff, dn277127
"""

import requests
from bs4 import BeautifulSoup


def parse_value(value_str):
    """
    Convert a string like '4500.0 mV', '35.000  A', or '0 V' to a float in SI units.

    - Voltages are returned in **volts [V]**.
    - Currents are returned in **amps [A]**.
    """
    if not value_str or value_str.strip() in ["", "-"]:
        return None

    parts = value_str.split()
    if len(parts) == 0:
        return None

    try:
        value = float(parts[0])
    except ValueError:
        return None

    # Detect units and convert to base SI
    if len(parts) > 1:
        unit = parts[1].lower()
        if unit == "mv":
            value /= 1000.0  # convert millivolts → volts
        elif unit == "v":
            value = value  # already volts
        elif unit == "ma":
            value /= 1000.0  # convert milliamps → amps
        elif unit == "a":
            value = value  # already amps
        # else: unknown unit, leave as-is
    return value


def get_pl512_status(url="http://192.168.10.222"):
    """
    Query the Wiener PL512 power supply web page and return a structured dictionary
    with the global power status and numeric per-channel measurements.

    Voltages are given in volts [V].
    Currents are given in amps [A].
    """
    try:
        r = requests.get(url, timeout=3)
        r.raise_for_status()
    except requests.RequestException as e:
        return {"status": "error", "message": str(e)}

    soup = BeautifulSoup(r.text, "html.parser")

    # --- Global status ---
    global_status = None
    global_table = soup.find("caption", string="Global Status")
    if global_table:
        parent = global_table.find_parent("table")
        if parent:
            cells = parent.find_all("td")
            if len(cells) >= 2:
                global_status = cells[1].get_text(strip=True)

    # --- Channel status ---
    channels = {}
    channels_table = soup.find("caption", string="Output Channels")
    if channels_table:
        parent = channels_table.find_parent("table")
        rows = parent.find_all("tr")[1:]  # skip header row
        for row in rows:
            cells = [c.get_text(strip=True) for c in row.find_all("td")]
            if len(cells) == 7:
                channel_info = {
                    "sense_voltage": parse_value(cells[1]),  # [V]
                    "current_limit": parse_value(cells[2]),  # [A]
                    "measured_sense_voltage": parse_value(cells[3]),  # [V]
                    "measured_current": parse_value(cells[4]),  # [A]
                    "measured_terminal_voltage": parse_value(cells[5]),  # [V]
                    "status": cells[6],
                }
                channels[cells[0]] = channel_info

    return {
        "status": "ok",
        "power_supply_status": global_status,
        "channels": channels,
    }


if __name__ == "__main__":
    status = get_pl512_status()
    if status["status"] == "ok":
        print("Power Supply Status:", status["power_supply_status"])
        for channel_name, channel in status["channels"].items():
            print(f"\nChannel {channel_name}:")
            print(f"  Sense Voltage [V]: {channel['sense_voltage']}")
            print(f"  Current Limit [A]: {channel['current_limit']}")
            print(f"  Measured Sense Voltage [V]: {channel['measured_sense_voltage']}")
            print(f"  Measured Current [A]: {channel['measured_current']}")
            print(f"  Measured Terminal Voltage [V]: {channel['measured_terminal_voltage']}")
            print(f"  Status: {channel['status']}")
    else:
        print("Error retrieving status:", status["message"])
