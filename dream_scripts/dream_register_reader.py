#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on December 04 15:44 2025
Created in PyCharm
Created as nTof_x17/dream_register_reader

@author: Dylan Neff, dn277127
"""

from pprint import pprint


def decode_register1(reg_hex):
    """Decode the 32-bit Register 1 based on DREAM User Manual."""
    # Accept hex string or int
    if isinstance(reg_hex, str):
        reg = int(reg_hex, 16)
    else:
        reg = reg_hex & 0xFFFFFFFF

    # Helper
    def bit(n):       return (reg >> n) & 1
    def bits(l, h):   return (reg >> l) & ((1 << (h - l + 1)) - 1)

    out = {}

    # ----------------------------
    #  ICS A CURRENT (bits 0–1)
    # ----------------------------
    ic = bits(0, 1)
    ic_map = {
        0b00: "400 µA",
        0b01: "500 µA",
        0b10: "800 µA",
        0b11: "1.2 mA"
    }
    out["CSA_input_current"] = ic_map[ic]

    # ----------------------------
    #  TEST CAP GAIN (bits 2–3)
    # ----------------------------
    tg = bits(2, 3)
    tg_map = {
        0b00: "100 fF",
        0b01: "200 fF",
        0b10: "400 fF",
        0b11: "2 pF"
    }
    out["Test_capacitor"] = tg_map[tg]

    # ----------------------------
    #  SHAPER PEAKING TIME (bits 4–7)
    # ----------------------------
    time = bits(4, 7)
    time_table = [
        76, 123, 180, 228,
        283, 328, 388, 433,
        578, 618, 675, 717,
        781, 818, 880, 919
    ]
    out["Peaking_time_ns"] = time_table[time]

    # ----------------------------
    #  TEST MODE (bits 8–9)
    # ----------------------------
    tm = bits(8, 9)
    tm_map = {
        0b00: "nothing",
        0b01: "calibration",
        0b10: "test",
        0b11: "functionality"
    }
    out["Test_mode"] = tm_map[tm]

    # ----------------------------
    # INTEGRATOR MODE (bit 10)
    # ----------------------------
    out["Integrator_mode_500ns"] = bool(bit(10))

    # ----------------------------
    # POLARITY (bit 11)
    # ----------------------------
    out["Polarity"] = "positive (CSA current in)" if bit(11) else "negative (CSA current out)"

    # ----------------------------
    # VICM (bits 12–13)
    # ----------------------------
    vicm = bits(12, 13)
    vicm_map = {
        0b00: (1.25, "negative"),
        0b01: (1.35, "negative"),
        0b10: (1.55, "positive"),
        0b11: (1.65, "positive"),
    }
    out["VICM_voltage_V"], out["VICM_polarity"] = vicm_map[vicm]

    # ----------------------------
    # GLOBAL THRESHOLD DAC (bits 14–20) + SIGN (bit 21)
    # ----------------------------
    dac_value = bits(14, 20)
    dac_sign = bit(21)  # 0 = negative, 1 = positive
    out["Threshold_DAC"] = f"{'-' if dac_sign==0 else '+'}{dac_value}"

    # ----------------------------
    # HIT VETO (bit 22)
    # ----------------------------
    out["HIT_veto"] = "none" if bit(22) else "4 µs veto"

    # ----------------------------
    # TOT MODE (bit 23)
    # ----------------------------
    out["TOT_mode"] = "Time-over-threshold width" if bit(23) else "fixed width"

    # ----------------------------
    # HIT WIDTH RANGE (bit 24)
    # ----------------------------
    hit_range = bit(24)
    out["HIT_width_range"] = "200 ns" if hit_range else "100 ns"

    # ----------------------------
    # HIT WIDTH ADJUSTMENT (bits 25–26)
    # ----------------------------
    hw = bits(25, 26)

    width_tables = {
        0: [ 60.86,  49.8,   71.95,  82.86 ],  # wp, tm, ws for Range=100ns
        1: [142, 110, 153, 194],               # Range=200ns (wp)
    }

    # Both 100ns and 200ns tables exist but have 3 categories each (wp, tm, ws)
    # but since the chip doesn't specify which category is active, we return all.
    hit_width_table_100 = {
        0b00: { "wp":60.86,  "tm":93.88,  "ws":127.91 },
        0b01: { "wp":49.8,   "tm":77.4,   "ws":106.25 },
        0b10: { "wp":71.95,  "tm":110.36, "ws":149.8 },
        0b11: { "wp":82.86,  "tm":126.9,  "ws":171.6 }
    }
    hit_width_table_200 = {
        0b00: { "wp":142, "tm":218, "ws":297 },
        0b01: { "wp":110, "tm":170, "ws":232 },
        0b10: { "wp":153, "tm":235, "ws":319 },
        0b11: { "wp":194, "tm":298, "ws":404 }
    }
    out["HIT_width_ns"] = (
        hit_width_table_200[hw] if hit_range else hit_width_table_100[hw]
    )

    # ----------------------------
    # HIT ENABLE (bit 27)
    # ----------------------------
    out["HIT_enable"] = bool(bit(27))

    # ----------------------------
    # EXTERNAL INPUT (bits 28–29)
    # ----------------------------
    ext = bits(28, 29)
    ext_map = {
        0b00: "none",
        0b01: "SK filter input",
        0b10: "Gain-2 input",
        0b11: "CSA standby"
    }
    out["External_input"] = ext_map[ext]

    # ----------------------------
    # DISCRIMINATOR RANGE (bit 30)
    # ----------------------------
    out["Discriminator_range"] = "5%" if bit(30) else "17.5%"

    # ----------------------------
    # INTEGRATION TIME CONSTANT (bit 31)
    # ----------------------------
    out["Integration_time_constant"] = "50 µs" if bit(31) else "5 µs"

    return out


def encode_register1(params):
    """Encode a dictionary of parameters back into a 32-bit hex string."""
    reg = 0

    # 1. CSA_input_current (bits 0-1)
    ic_inv = {"400 µA": 0b00, "500 µA": 0b01, "800 µA": 0b10, "1.2 mA": 0b11}
    reg |= ic_inv[params["CSA_input_current"]] << 0

    # 2. Test_capacitor (bits 2-3)
    tg_inv = {"100 fF": 0b00, "200 fF": 0b01, "400 fF": 0b10, "2 pF": 0b11}
    reg |= tg_inv[params["Test_capacitor"]] << 2

    # 3. Peaking_time_ns (bits 4-7)
    time_table = [
        76, 123, 180, 228, 283, 328, 388, 433,
        578, 618, 675, 717, 781, 818, 880, 919
    ]
    reg |= time_table.index(params["Peaking_time_ns"]) << 4

    # 4. Test_mode (bits 8-9)
    tm_inv = {"nothing": 0b00, "calibration": 0b01, "test": 0b10, "functionality": 0b11}
    reg |= tm_inv[params["Test_mode"]] << 8

    # 5. Integrator_mode (bit 10)
    if params["Integrator_mode_500ns"]:
        reg |= 1 << 10

    # 6. Polarity (bit 11)
    if "positive" in params["Polarity"]:
        reg |= 1 << 11

    # 7. VICM (bits 12-13)
    # Mapping based on (voltage, polarity)
    vicm_map = {
        (1.25, "negative"): 0b00,
        (1.35, "negative"): 0b01,
        (1.55, "positive"): 0b10,
        (1.65, "positive"): 0b11,
    }
    reg |= vicm_map[(params["VICM_voltage_V"], params["VICM_polarity"])] << 12

    # 8. Threshold DAC (bits 14-20) + Sign (bit 21)
    dac_str = params["Threshold_DAC"]
    dac_sign = 1 if dac_str[0] == '+' else 0
    dac_val = int(dac_str[1:])
    reg |= (dac_val & 0x7F) << 14
    reg |= dac_sign << 21

    # 9. HIT Veto (bit 22)
    if params["HIT_veto"] == "none":
        reg |= 1 << 22

    # 10. TOT Mode (bit 23)
    if params["TOT_mode"] == "Time-over-threshold width":
        reg |= 1 << 23

    # 11. HIT Width Range (bit 24)
    hit_range = 1 if params["HIT_width_range"] == "200 ns" else 0
    reg |= hit_range << 24

    # 12. HIT Width Adjustment (bits 25-26)
    # We look for the dictionary that matches the provided sub-dict
    hw_table_100 = {
        0b00: {"wp": 60.86, "tm": 93.88, "ws": 127.91},
        0b01: {"wp": 49.8, "tm": 77.4, "ws": 106.25},
        0b10: {"wp": 71.95, "tm": 110.36, "ws": 149.8},
        0b11: {"wp": 82.86, "tm": 126.9, "ws": 171.6}
    }
    hw_table_200 = {
        0b00: {"wp": 142, "tm": 218, "ws": 297},
        0b01: {"wp": 110, "tm": 170, "ws": 232},
        0b10: {"wp": 153, "tm": 235, "ws": 319},
        0b11: {"wp": 194, "tm": 298, "ws": 404}
    }

    active_table = hw_table_200 if hit_range else hw_table_100
    # Find which bits correspond to the current sub-dict
    for bits, values in active_table.items():
        if values == params["HIT_width_ns"]:
            reg |= bits << 25
            break

    # 13. HIT Enable (bit 27)
    if params["HIT_enable"]:
        reg |= 1 << 27

    # 14. External Input (bits 28-29)
    ext_inv = {"none": 0b00, "SK filter input": 0b01, "Gain-2 input": 0b10, "CSA standby": 0b11}
    reg |= ext_inv[params["External_input"]] << 28

    # 15. Discriminator Range (bit 30)
    if params["Discriminator_range"] == "5%":
        reg |= 1 << 30

    # 16. Integration Time Constant (bit 31)
    if params["Integration_time_constant"] == "50 µs":
        reg |= 1 << 31

    return f"0x{reg:08X}"


# Example
if __name__ == "__main__":
    test = "0x081FD023"  # Nominal MMs self trigger
    # test = "0x281FE823"  # Nominal + polarity to gain 2 - hit polarity
    # test = "0x081FE823"  # Nominal + polarity to CSA - hit polarity
    # test = "0x83FE823"  # Nominal + polarity to CSA + hit polarity
    # test = "0x881FD003"
    # test = "0x091FD023"
    # test = "0x891FD023"  # Nominal TCM MM
    # test = "0x891FD020"  # Nominal TCM MM with 400nA CSA current instead of 1.2uA
    # test = "0x91FD023"  # Nominal TCM MM with 5us integration time constant instead of 50us
    test_decoded = decode_register1(test)
    pprint(test_decoded)

    # test_decoded['External_input'] = 'SK filter input'
    test_decoded['External_input'] = 'Gain-2 input'
    test_encoded = encode_register1(test_decoded)
    print(test_encoded)

    pprint(decode_register1(test_encoded))

    input('enter to exit')