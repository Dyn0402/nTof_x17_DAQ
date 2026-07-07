#!/usr/bin/env python3
"""Test read a Keithley 2000 multimeter over GPIB (NI GPIB-USB-HS + linux-gpib).

Prereqs (already set up on this laptop):
  * linux-gpib 4.3.7 kernel module `ni_usb_gpib` loaded, board configured
    (`sudo gpib_config --minor 0`), /etc/gpib.conf defines board `gpib0` and
    device `keithley2000` at primary address 16.
  * The `gpib`/`Gpib` python module installed in this venv.

Usage:
    python keithley2000_read.py                 # *IDN? then a DC voltage read
    python keithley2000_read.py --pad 16         # override GPIB address
    python keithley2000_read.py --func VOLT:DC   # VOLT:DC, VOLT:AC, RES, FRES, CURR:DC...
"""
import argparse
import sys

import Gpib
import gpib  # low-level, for GpibError / constants


def query(inst, cmd, read_len=4096):
    """Write an SCPI command and return the instrument's reply as a str."""
    inst.write(cmd.encode() if isinstance(cmd, str) else cmd)
    return inst.read(read_len).decode(errors="replace").strip()


def main():
    ap = argparse.ArgumentParser(description="Test-read a Keithley 2000 over GPIB")
    ap.add_argument("--board", type=int, default=0, help="GPIB board index (default 0)")
    ap.add_argument("--pad", type=int, default=15, help="Keithley primary address (this unit is at 15)")
    ap.add_argument("--func", default="VOLT:DC",
                    help="Measurement function, e.g. VOLT:DC, VOLT:AC, RES, FRES, CURR:DC")
    args = ap.parse_args()

    # Open by board index + primary address (does not rely on /etc/gpib.conf names).
    # timeout=T1s (11) -> 1 second; sad=0 (no secondary address).
    try:
        inst = Gpib.Gpib(args.board, args.pad, 0, gpib.T1s)
    except gpib.GpibError as e:
        sys.exit(f"Could not open GPIB board {args.board} @ pad {args.pad}: {e}")

    inst.clear()  # device clear

    try:
        idn = query(inst, "*IDN?")
    except gpib.GpibError as e:
        sys.exit(f"*IDN? failed (is the Keithley at address {args.pad} and powered on?): {e}")
    print(f"*IDN? -> {idn}")

    # Single measurement of the requested function.
    try:
        reading = query(inst, f"MEASure:{args.func}?")
        print(f"MEAS:{args.func}? -> {reading}")
    except gpib.GpibError as e:
        print(f"Measurement read failed: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
