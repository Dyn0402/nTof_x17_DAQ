#!/usr/bin/env python3
"""Live plot of a Keithley 2000 reading over GPIB (NI GPIB-USB-HS + linux-gpib).

Configures the DMM's measurement function once, then polls `:READ?` repeatedly
and shows a scrolling time-series. Works for any Keithley 2000 function.

Examples:
    python keithley2000_live.py                       # DC volts, live window
    python keithley2000_live.py --func RES             # 2-wire resistance
    python keithley2000_live.py --nplc 0.1 --interval 100   # faster sampling
    python keithley2000_live.py --csv volts.csv        # also log to CSV
    python keithley2000_live.py --duration 10 --save plot.png   # headless capture

Keys in the live window:  q = quit,  c = clear history.
"""
import argparse
import csv
import sys
import time
from collections import deque

import Gpib
import gpib

# Units shown on the y-axis for each supported function.
FUNC_UNITS = {
    "VOLT:DC": ("V", "DC Voltage"),
    "VOLT:AC": ("V", "AC Voltage"),
    "CURR:DC": ("A", "DC Current"),
    "CURR:AC": ("A", "AC Current"),
    "RES": ("Ω", "2-wire Resistance"),
    "FRES": ("Ω", "4-wire Resistance"),
    "FREQ": ("Hz", "Frequency"),
    "TEMP": ("°C", "Temperature"),
}


def query(inst, cmd, read_len=256):
    inst.write(cmd.encode())
    return inst.read(read_len).decode(errors="replace").strip()


def main():
    ap = argparse.ArgumentParser(description="Live plot a Keithley 2000 over GPIB")
    ap.add_argument("--board", type=int, default=0, help="GPIB board index (default 0)")
    ap.add_argument("--pad", type=int, default=15, help="Keithley primary address (default 15)")
    ap.add_argument("--func", default="VOLT:DC",
                    help="Function: VOLT:DC, VOLT:AC, CURR:DC, RES, FRES, FREQ, TEMP")
    ap.add_argument("--nplc", type=float, default=None,
                    help="Integration time in power-line cycles (lower=faster/noisier, e.g. 0.1)")
    ap.add_argument("--interval", type=int, default=250, help="Poll interval in ms (default 250)")
    ap.add_argument("--window", type=float, default=60.0,
                    help="Seconds of history shown on screen (default 60)")
    ap.add_argument("--csv", default=None, help="Append (timestamp,elapsed,value) to this CSV file")
    ap.add_argument("--duration", type=float, default=None,
                    help="Auto-stop after N seconds (for headless capture)")
    ap.add_argument("--save", default=None, help="Save final figure to this PNG and exit")
    args = ap.parse_args()

    func = args.func.upper()
    unit, label = FUNC_UNITS.get(func, ("", func))

    # ---- headless capture uses the non-interactive Agg backend ----
    import matplotlib
    if args.save and args.duration is not None:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.animation import FuncAnimation

    # ---- open + configure the instrument ----
    try:
        inst = Gpib.Gpib(args.board, args.pad, 0, gpib.T3s)
    except gpib.GpibError as e:
        sys.exit(f"Could not open GPIB board {args.board} @ pad {args.pad}: {e}")

    inst.clear()
    idn = query(inst, "*IDN?")
    print(f"Connected: {idn}")

    query_ok = True
    inst.write(f":CONFigure:{func}".encode())          # set function once
    if args.nplc is not None and func in ("VOLT:DC", "VOLT:AC", "CURR:DC", "CURR:AC", "RES", "FRES"):
        inst.write(f":{func}:NPLCycles {args.nplc}".encode())
    inst.write(b":FORMat:ELEMents READing")            # return the reading only

    csv_writer = None
    csv_file = None
    if args.csv:
        csv_file = open(args.csv, "a", newline="")
        csv_writer = csv.writer(csv_file)
        if csv_file.tell() == 0:
            csv_writer.writerow(["unix_time", "elapsed_s", f"value_{unit or 'reading'}"])

    # ---- rolling buffers ----
    t0 = time.time()
    times = deque()
    vals = deque()
    stats = {"reads": 0, "errors": 0}

    fig, ax = plt.subplots(figsize=(9, 5))
    (line,) = ax.plot([], [], lw=1.5, color="#2a7de1")
    ax.set_xlabel("Elapsed time (s)")
    ax.set_ylabel(f"{label} ({unit})" if unit else label)
    ax.grid(True, alpha=0.3)
    title = ax.set_title("Starting…")

    def read_one():
        """Poll one reading; return float or None on error."""
        try:
            raw = query(inst, ":READ?")
            v = float(raw.split(",")[0])
            stats["reads"] += 1
            return v
        except (gpib.GpibError, ValueError) as e:
            stats["errors"] += 1
            print(f"read error: {e}", file=sys.stderr)
            return None

    def update(_frame):
        now = time.time()
        elapsed = now - t0
        v = read_one()
        if v is not None:
            times.append(elapsed)
            vals.append(v)
            if csv_writer:
                csv_writer.writerow([f"{now:.3f}", f"{elapsed:.3f}", f"{v:.9g}"])
            # drop points older than the display window
            while times and (elapsed - times[0]) > args.window:
                times.popleft()
                vals.popleft()

        line.set_data(times, vals)
        if times:
            ax.set_xlim(max(0, elapsed - args.window), max(args.window, elapsed))
            lo, hi = min(vals), max(vals)
            pad = (hi - lo) * 0.1 or (abs(hi) * 0.01 or 1.0)
            ax.set_ylim(lo - pad, hi + pad)
            latest = vals[-1]
            title.set_text(f"{label}: {latest:.6g} {unit}    "
                           f"[{stats['reads']} reads, {stats['errors']} err]")

        if args.duration is not None and elapsed >= args.duration:
            ani.event_source.stop()
            plt.close(fig)
        return line, title

    def on_key(event):
        if event.key == "q":
            plt.close(fig)
        elif event.key == "c":
            times.clear()
            vals.clear()

    fig.canvas.mpl_connect("key_press_event", on_key)

    ani = FuncAnimation(fig, update, interval=args.interval,
                        blit=False, cache_frame_data=False)

    try:
        if args.save and args.duration is not None:
            # headless: drive the animation manually, then save
            steps = int(args.duration * 1000 / args.interval) + 1
            for _ in range(steps):
                update(None)
                time.sleep(args.interval / 1000.0)
            fig.savefig(args.save, dpi=110)
            print(f"Saved {args.save} — {stats['reads']} reads, {stats['errors']} errors")
        else:
            plt.show()
    finally:
        if csv_file:
            csv_file.close()
        inst.write(b":SYSTem:LOCal")  # return front panel to local control


if __name__ == "__main__":
    main()
