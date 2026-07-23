#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analyze_link_load.py — measure how full the DAQ host link is during an IPD ladder, using
the system_stats watcher's durable 2 Hz CSV log.

This is the retroactive version of "Test 0" in 03_test_plan.md: no extra instrumentation
was needed, because system_stats_watcher.py has been logging per-interface throughput at
2 Hz since 2026-07-15 to ~/beam_july/slow_control/system_stats/system_stats_<day>.csv.

WHAT IT DOES
------------
1. For each ladder point, integrates the logged rx bytes over the point's time window and
   divides by the number of events actually recorded -> the RAW event size on the wire.
   The CLEAN points must all agree; that agreement is the check that the method works.
2. Uses that measured size plus the deadtime cycle model to compute the *instantaneous*
   rate the FEUs demand during the spill burst, and compares it to the link's line rate.
   The corruption threshold should sit where demand crosses line rate.

WHY THE INTEGRATION STEP IS NECESSARY
-------------------------------------
The log samples at 2 Hz (0.5 s bins; MIN_SAMPLE_PERIOD_S in system_stats_controller.py
caps it there). The readout burst is ~100 ms inside a ~3.6 s spill period, so a 0.5 s bin
averages the burst down by ~5x and the whole-point mean by ~35x. A peak bin of ~200 Mb/s
on a 1000 Mb/s link therefore does NOT mean the link is 80% idle -- it means the burst is
short. Never read saturation off the raw plot; integrate.

USAGE
-----
    ./analyze_link_load.py                      # the 2026-07-22 1 GbE ladder (baseline)
    ./analyze_link_load.py --csv <path> --iface enp1s0 --line-mbps 10000 \
        --points 'ipd010=15:02:11,15:05:20,10,72.0' ...

    Each --points entry is  name=start,end,ipd,events_per_spill  (times are HH:MM:SS
    local, matching the subrun file mtimes under ~/july_dream/dream_run/<run>/<subrun>/).
"""

import argparse
import csv
import os
import statistics as st

# deadtime cycle model:  cycle_us = n * (4.83 + 0.998 * IPD)
CYCLE_A, CYCLE_B = 4.83, 0.998

# Usable payload fraction of nominal line rate (framing + IPG + headers).
WIRE_EFFICIENCY = 0.941

DEFAULT_CSV = os.path.expanduser(
    "~/beam_july/slow_control/system_stats/system_stats_2026-07-22.csv")

# The 2026-07-22 1 GbE baseline ladder. name -> (start, end, IPD, ev/spill, clean?)
# Windows from subrun mtimes; ev/spill and verdicts from raw_ipd_analysis.py.
BASELINE_POINTS = [
    ("ipd100_a", "14:50:15", "14:53:27", 100, 30.4, True),
    ("ipd075",   "14:53:38", "14:56:48",  75, 36.0, True),
    ("ipd050",   "14:56:58", "15:00:07",  50,  3.8, False),
    ("ipd030",   "15:00:17", "15:03:31",  30,  7.5, False),
    ("ipd015",   "15:03:41", "15:06:54",  15,  9.4, False),
    ("ipd100_b", "15:07:05", "15:10:15", 100, 30.7, True),
]
BASELINE_SPILLS = 53      # spills per 3-min point
BASELINE_N = 32           # NbOfSamples


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def load_window(rows, iface, start, end):
    """Logged rx/tx rates (bytes/s) plus cpu and disk-write, for one HH:MM:SS window."""
    sel = [r for r in rows if start <= r["timestamp"][11:19] <= end]
    col = f"net_{iface}_rx_bps"
    if sel and col not in sel[0]:
        raise SystemExit(f"column {col!r} not in CSV -- check --iface "
                         f"(available: {[c for c in sel[0] if c.startswith('net_')]})")
    rx = [v for v in (_f(r[col]) for r in sel) if v is not None]
    cpu = [v for v in (_f(r.get("cpu_avg", "")) for r in sel) if v is not None]
    wr = [v for v in (_f(r.get("disk_hdd_write_bps", "")) for r in sel) if v is not None]
    return rx, cpu, wr


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", default=DEFAULT_CSV)
    ap.add_argument("--iface", default="eno1", help="eno1 pre-upgrade, e.g. enp1s0 after")
    ap.add_argument("--line-mbps", type=float, default=1000.0)
    ap.add_argument("--n-samples", type=int, default=BASELINE_N)
    ap.add_argument("--spills", type=float, default=BASELINE_SPILLS,
                    help="spills per ladder point")
    ap.add_argument("--sample-period", type=float, default=0.5,
                    help="CSV logging period, s (system_stats default 0.5)")
    ap.add_argument("--points", action="append", default=None,
                    help="name=start,end,ipd,ev_per_spill  (repeatable). "
                         "Append ',corrupt' to mark a point corrupt.")
    args = ap.parse_args()

    if args.points:
        pts = []
        for p in args.points:
            name, rest = p.split("=", 1)
            f = rest.split(",")
            pts.append((name, f[0], f[1], int(f[2]), float(f[3]),
                        not (len(f) > 4 and f[4].strip().lower() == "corrupt")))
    else:
        pts = BASELINE_POINTS

    rows = list(csv.DictReader(open(args.csv)))
    if not rows:
        raise SystemExit(f"no rows in {args.csv}")
    print(f"CSV   : {args.csv}")
    print(f"        {len(rows)} rows, {rows[0]['timestamp'][11:19]} -> "
          f"{rows[-1]['timestamp'][11:19]}")
    print(f"link  : {args.iface} @ {args.line_mbps:.0f} Mb/s nominal, "
          f"{args.line_mbps * WIRE_EFFICIENCY:.0f} Mb/s usable payload")
    print(f"model : cycle_us = {args.n_samples} x ({CYCLE_A} + {CYCLE_B} x IPD)\n")

    # ---- 1. event size from integrated bytes -------------------------------------
    print("1. RAW event size on the wire  (integrated bytes / recorded events)")
    print(f"   {'point':10s} {'dur s':>6s} {'MB total':>9s} {'events':>7s} "
          f"{'kB/event':>9s} {'kB/FEU':>7s}")
    sizes = {}
    stash = {}
    for name, a, b, ipd, evs, clean in pts:
        rx, cpu, wr = load_window(rows, args.iface, a, b)
        if not rx:
            print(f"   {name:10s} NO DATA in window {a}-{b}")
            continue
        dur = len(rx) * args.sample_period
        total = st.mean(rx) * dur
        nev = evs * args.spills
        stash[name] = (rx, cpu, wr, dur, total)
        note = "" if clean else "   <- CORRUPT, size meaningless"
        if clean:
            sizes[name] = total / nev
        print(f"   {name:10s} {dur:6.0f} {total / 1e6:9.1f} {nev:7.0f} "
              f"{total / nev / 1e3:9.1f} {total / nev / 8e3:7.1f}{note}")

    if not sizes:
        raise SystemExit("\nno clean points -- cannot calibrate event size")
    ev_bytes = st.mean(sizes.values())
    spread = (max(sizes.values()) - min(sizes.values())) / ev_bytes * 100
    print(f"\n   --> {ev_bytes / 1e3:.0f} kB/event across 8 FEUs "
          f"= {ev_bytes / 8e3:.1f} kB/FEU at n={args.n_samples}")
    print(f"       clean-point spread {spread:.1f}%  "
          f"({'consistent -- method valid' if spread < 5 else 'INCONSISTENT -- suspect'})")

    # Reference demand uses the largest clean yield: what the FEUs *try* to push.
    ev_ref = max(e for _, _, _, _, e, c in pts if c)

    # ---- 2. demanded burst rate vs the wire ---------------------------------------
    usable = args.line_mbps * WIRE_EFFICIENCY
    print(f"\n2. Demanded burst rate vs the wire   (assuming {ev_ref:.1f} ev/spill offered)")
    print(f"   {'point':10s} {'IPD':>4s} {'cycle us':>9s} {'burst ms':>9s} "
          f"{'burst MB':>9s} {'DEMAND Mb/s':>12s} {'% usable':>9s}  verdict")
    for name, a, b, ipd, evs, clean in pts:
        cyc = args.n_samples * (CYCLE_A + CYCLE_B * ipd)
        evs_try = evs if clean else ev_ref
        burst_s = cyc * evs_try / 1e6
        mb = evs_try * ev_bytes
        dem = mb / burst_s * 8 / 1e6
        print(f"   {name:10s} {ipd:4d} {cyc:9.0f} {burst_s * 1e3:9.1f} {mb / 1e6:9.2f} "
              f"{dem:12.0f} {dem / usable * 100:8.0f}%  {'CLEAN' if clean else 'CORRUPT'}")

    # ---- 3. predicted threshold ----------------------------------------------------
    # demand == usable  =>  solve for IPD
    burst_need = ev_ref * ev_bytes * 8 / (usable * 1e6)        # s
    cyc_need = burst_need / ev_ref * 1e6                        # us
    ipd_pred = (cyc_need / args.n_samples - CYCLE_A) / CYCLE_B
    floor_us = args.n_samples * CYCLE_A
    print(f"\n3. Predicted corruption threshold (demand == wire): "
          f"IPD ~ {ipd_pred:.0f}  (cycle {cyc_need:.0f} us)")
    print(f"   FEU-internal floor (IPD-independent): n x {CYCLE_A} = {floor_us:.0f} us/event")
    if cyc_need > floor_us:
        print(f"   -> LINK-limited: threshold sits {cyc_need / floor_us:.1f}x above the floor,"
              f" so more bandwidth still buys speed.")
    else:
        print(f"   -> FLOOR-limited: the link is no longer the constraint at this rate.")

    # ---- 4. delivered bytes + headroom ---------------------------------------------
    print("\n4. Delivered throughput (what the log actually saw) and duty")
    print(f"   {'point':10s} {'mean Mb/s':>10s} {'peak bin':>9s} {'burst duty':>11s} "
          f"{'cpu %':>6s} {'hdd wr MB/s':>12s}")
    for name, a, b, ipd, evs, clean in pts:
        if name not in stash:
            continue
        rx, cpu, wr, dur, total = stash[name]
        cyc = args.n_samples * (CYCLE_A + CYCLE_B * ipd)
        duty = (cyc * (evs if clean else ev_ref) / 1e6) / args.sample_period
        print(f"   {name:10s} {st.mean(rx) * 8 / 1e6:10.1f} {max(rx) * 8 / 1e6:9.1f} "
              f"{duty * 100:10.0f}% {st.mean(cpu) if cpu else 0:6.1f} "
              f"{(st.mean(wr) if wr else 0) / 1e6:12.1f}")
    print("\n   'burst duty' = fraction of a 0.5 s log bin the readout burst occupies.")
    print("   The peak bin understates the true burst rate by roughly 1/duty --")
    print("   that is why the Overview network plot never looks saturated.")


if __name__ == "__main__":
    main()
