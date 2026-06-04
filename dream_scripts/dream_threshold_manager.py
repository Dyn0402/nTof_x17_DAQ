#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on June 04 5:05 PM 2026
Created in PyCharm
Created as nTof_x17_DAQ/dream_threshold_manager.py

@author: Dylan Neff, dylan
"""

import os
import csv
from datetime import datetime


# =====================================================================
# Example Usage
# =====================================================================
def main():
    manager = DreamThresholdManager()

    # 1. Provide a dummy file name for testing (Replace with your actual file path)
    input_filename = "dream_thresholds_04_thr.prg"
    output_filename = "dream_thresholds_updated.prg"

    # Quick test file creation so the script can run right away out-of-the-box
    if not os.path.exists(input_filename):
        with open(input_filename, "w") as test_f:
            test_f.write("# Sample Header\n0x01120111 --    0 B0 E0 S0 C 0 D 0-0x0111 D 1-0x0112\n# Last address: 63\n")

    # 2. Read and decode the raw file
    manager.read_prg(input_filename)

    # 3. Read a specific value natively in Python
    print(f"Current Value of Dream 0, Channel 0: {manager.get_threshold(dream_id=0, channel=0)}")

    # 4. Modify some specific thresholds manually (using basic decimal notation)
    manager.set_threshold(dream_id=0, channel=0, value=300)  # Noise tweak
    manager.set_threshold(dream_id=4, channel=50, value=427)  # Overriding the outlier from earlier

    # 5. OPTIONAL: Export to a CSV to open/edit easily inside Excel
    manager.to_csv("human_readable_thresholds.csv")

    # ... You could edit 'human_readable_thresholds.csv' in Excel here, then load it back:
    # manager.from_csv("human_readable_thresholds.csv")

    # 6. Repack and generate the updated final hardware production file
    manager.write_prg(output_filename)


class DreamThresholdManager:
    def __init__(self):
        # Internal structure: 8 Dreams (0-7), each with 64 channels (0-63)
        # Stored as standard decimal integers for easy human readability
        self.thresholds = {dream_id: {ch: 0 for ch in range(64)} for dream_id in range(8)}
        self.headers = []
        self.footers = []

    def read_prg(self, filepath):
        """Reads and decodes a .prg threshold configuration file."""
        self.headers = []
        self.footers = []
        data_lines_count = 0

        with open(filepath, 'r') as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue

                # Capture metadata/comments
                if stripped.startswith('#'):
                    if data_lines_count == 0:
                        self.headers.append(line)
                    else:
                        self.footers.append(line)
                    continue

                # Extract the 32-bit hex token
                parts = stripped.split('--')
                hex_word_str = parts[0].strip()

                try:
                    word = int(hex_word_str, 16)
                except ValueError:
                    print(f"Skipping invalid data line: {line}")
                    continue

                # Calculate indices sequentially based on the file layout
                # 256 total lines: 4 blocks * 64 channels
                block = data_lines_count // 64
                channel = data_lines_count % 64
                even_dream = block * 2
                odd_dream = block * 2 + 1

                # Decode the 12-bit thresholds
                even_val = word & 0xFFF
                odd_val = (word >> 16) & 0xFFF

                self.thresholds[even_dream][channel] = even_val
                self.thresholds[odd_dream][channel] = odd_val

                data_lines_count += 1

        print(f"Successfully loaded {data_lines_count * 2} thresholds from {filepath}")

    def write_prg(self, filepath):
        """Encodes internal thresholds and writes them back out to a pristine .prg file."""
        with open(filepath, 'w') as f:
            # Write original headers if present, otherwise generate a generic one
            if self.headers:
                for header in self.headers:
                    f.write(header)
            else:
                f.write("# Threshold memory initialization file\n")
                f.write(f"# Produced: {datetime.now().strftime('%H%M %d%m%y')}\n\n")

            # Construct and write the 256 data lines
            idx = 0
            for block in range(4):
                even_dream = block * 2
                odd_dream = block * 2 + 1
                for channel in range(64):
                    even_val = self.thresholds[even_dream][channel] & 0xFFF
                    odd_val = self.thresholds[odd_dream][channel] & 0xFFF

                    # Repack into a 32-bit word
                    word = (odd_val << 16) | even_val

                    # Exact format replication matching the ASIC documentation's layout
                    line = (
                        f"0x{word:08x} -- {idx:>4} "
                        f"B{block} E0 S0 C{channel:>2} "
                        f"D {even_dream}-0x{even_val:04x} "
                        f"D {odd_dream}-0x{odd_val:04x}\n"
                    )
                    f.write(line)
                    idx += 1

            # Write footers
            if self.footers:
                for footer in self.footers:
                    f.write(footer)
            else:
                f.write("# Last address: 63\n")

        print(f"Successfully wrote updated threshold file to {filepath}")

    def get_threshold(self, dream_id, channel):
        """Returns the decimal threshold value for a specific Dream chip and Channel."""
        return self.thresholds[dream_id][channel]

    def set_threshold(self, dream_id, channel, value):
        """Sets the threshold value (expects normal decimal integer, max 4095)."""
        if not (0 <= value <= 4095):
            raise ValueError("Threshold values must fit inside a 12-bit integer (0 to 4095).")
        self.thresholds[dream_id][channel] = value

    def to_csv(self, csv_filepath):
        """Dumps all data to a clean, human-readable spreadsheet format (CSV)."""
        with open(csv_filepath, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['Dream_ID', 'Channel', 'Threshold_Decimal', 'Threshold_Hex'])
            for d in range(8):
                for c in range(64):
                    val = self.thresholds[d][c]
                    writer.writerow([d, c, val, f"0x{val:03x}"])
        print(f"Exported human-readable CSV to {csv_filepath}")

    def from_csv(self, csv_filepath):
        """Loads values back from a modified CSV spreadsheet."""
        with open(csv_filepath, 'r') as f:
            reader = csv.reader(f)
            header = next(reader)  # Skip table header
            for row in reader:
                if not row: continue
                dream_id = int(row[0])
                channel = int(row[1])
                val_decimal = int(row[2])
                self.set_threshold(dream_id, channel, val_decimal)
        print(f"Imported updated thresholds from {csv_filepath}")


if __name__ == '__main__':
    main()