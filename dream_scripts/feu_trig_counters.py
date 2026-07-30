#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
feu_trig_counters.py — read the FEU trigger-FIFO statistics and the trigger-logic
configuration (watermarks) over plain ASCII UDP.

This is the decisive instrument for the OvrWrnHwm / OvrWrnLwm / LocThrot study: it reports,
per FEU, how many triggers were accepted, how many were dropped and (crucially) the MAX
trigger-FIFO OCCUPANCY reached -- the number that made the 2026-07-19 "raise the HWM" test a
null result (occupancy never exceeded ~11-12, so a HWM of 20 was never reached).

Registers (FEU User's Manual 3.1.x; defs in ~/Feu/Firmware5/.../CBus/CBus_Common.h):
  0x100000  Main command      -- LatchStat = bit 4 (write 1 then 0 to freeze statistics)
  0x100008  TrigConfig        -- TimeStamp 11:0 | OvrWrnLwm 17:12 | OvrWrnHwm 23:18
                                 | OvrThersh 29:24 | LocThrot 30
  0x100018  TrigAcptCntr      -- accepted triggers
  0x10001C  TrigDropCntr      -- closeDrop 7:0 | fifoDrop 15:8 | maxFIFOocc 21:16

Reading 0x100008 back matters independently: it proves a cfg watermark change actually
reached the hardware. The sibling knob RdClk_Div silently did NOT take (RunCtrl clamped it),
which is exactly the failure mode that can fake a "null result".

Protocol: slow-control UDP on port 1300 + FEU-Id, `peek 0x<addr>` / `poket <addr> <val> <mask>`,
NUL-terminated; the reply payload starts at byte 8. Same channel RunCtrl uses.

SAFETY
  - Default is READ-ONLY (pure peeks). Peeks are cheap and safe at any time.
  - --latch issues the LatchStat poke (0x100000 bit 4) to freeze coherent statistics first.
    This is a statistics latch, not a configuration change, and the comb study used it live
    during a run. Still, it is a WRITE: it is opt-in.
  - Touches FEUs only. Nothing here goes near the N1081B logic modules, so the
    n1081b_session / one-process-per-board rules do not apply.

Usage:
  feu_trig_counters.py                 # read-only snapshot, all 8 FEUs
  feu_trig_counters.py --latch         # latch statistics first (a write), then read
  feu_trig_counters.py --feus 1 2      # subset, by cfg slot number
  feu_trig_counters.py --watch 5       # re-read every 5 s until Ctrl-C
  feu_trig_counters.py --latch --watch 2 --csv /path/log.csv   # log a whole ladder run

⚠ COUNTER WIDTHS — only `accepted` is usable under saturation
  accepted   is the full 32-bit register.  closeDrop and fifoDrop are 8 bit and
  maxFIFOocc is 6 bit, so under a saturating pulser the drop counters WRAP within
  milliseconds. Read them as "nonzero = dropping", never as a rate. The honest
  throughput number is d(accepted)/dt from --csv sampling.
  Counters reset when RunCtrl starts a sub-run, so a --csv log sampled straight
  through a multi-sub-run ladder captures each sub-run's final value as the last
  sample before the reset — no coordination with daq_control needed.
"""
import argparse
import os
import socket
import sys
import time

# cfg slot -> (Feu_RunCtrl_Id, NetChan_Ip); from dream_config/Tcm_Mx17_July_ZS.cfg
FEUS = {
    1: (32, "192.168.10.44"),
    2: (71, "192.168.10.83"),
    3: (98, "192.168.10.110"),
    4: (99, "192.168.10.111"),
    5: (106, "192.168.10.118"),
    6: (31, "192.168.10.43"),
    7: (69, "192.168.10.81"),
    8: (70, "192.168.10.82"),
}

REG_CMD = 0x100000
REG_TRIGCONF = 0x100008
REG_ACPT = 0x100018
REG_DROP = 0x10001C
LATCH_BIT = 0x10

TIMEOUT_S = 1.0


def _rpc(ip, port, cmd):
    """Send one ASCII command, return the reply payload (bytes 8+) as text."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(TIMEOUT_S)
    try:
        s.sendto(cmd.encode() + b"\x00", (ip, port))
        data = s.recvfrom(8192)[0]
    finally:
        s.close()
    return data[8:].decode(errors="replace").rstrip("\x00").strip()


def peek(ip, port, addr):
    """Read a 32-bit register. Returns int, or None if unparseable.

    The FEU replies `<addr-echo> = 0x<value>`, e.g. peek 0x100008 -> '00008 = 0x5c309000'.
    Take the value AFTER the '='; a naive scan picks up the address echo instead and
    silently returns nonsense (it reads 0x100018 as the number 18).
    """
    reply = _rpc(ip, port, f"peek 0x{addr:X}")
    if "=" not in reply:
        return None
    val = reply.split("=", 1)[1].strip().split()[0]
    try:
        return int(val, 16) if val.lower().startswith("0x") else int(val, 16)
    except (ValueError, IndexError):
        return None


def latch(ip, port):
    """Freeze coherent statistics: LatchStat = 1 then 0. This is a WRITE."""
    _rpc(ip, port, f"poket 0x{REG_CMD:X} 0x{LATCH_BIT:X} 0x{LATCH_BIT:X}")
    _rpc(ip, port, f"poket 0x{REG_CMD:X} 0x0 0x{LATCH_BIT:X}")


def decode_trigconf(v):
    return {
        "TimeStamp": v & 0xFFF,
        "OvrWrnLwm": (v >> 12) & 0x3F,
        "OvrWrnHwm": (v >> 18) & 0x3F,
        "OvrThersh": (v >> 24) & 0x3F,
        "LocThrot": (v >> 30) & 0x1,
    }


def decode_drop(v):
    return {
        "closeDrop": v & 0xFF,
        "fifoDrop": (v >> 8) & 0xFF,
        "maxFIFOocc": (v >> 16) & 0x3F,
    }


def read_feu(slot, do_latch):
    fid, ip = FEUS[slot]
    port = 1300 + fid
    row = {"slot": slot, "id": fid, "ip": ip}
    try:
        if do_latch:
            latch(ip, port)
        tc = peek(ip, port, REG_TRIGCONF)
        row["accepted"] = peek(ip, port, REG_ACPT)
        drop = peek(ip, port, REG_DROP)
        row.update(decode_trigconf(tc) if tc is not None else {})
        row.update(decode_drop(drop) if drop is not None else {})
        row["ok"] = tc is not None and drop is not None
    except socket.timeout:
        row["ok"], row["err"] = False, "timeout"
    except OSError as e:
        row["ok"], row["err"] = False, str(e)
    return row


def print_table(rows):
    hdr = (f"{'slot':>4} {'id':>4} {'Hwm':>4} {'Lwm':>4} {'Thr':>4} {'LTh':>4} "
           f"{'accepted':>10} {'closeDr':>8} {'fifoDr':>7} {'maxOcc':>7}")
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        if not r.get("ok"):
            print(f"{r['slot']:>4} {r['id']:>4}   -- unreachable ({r.get('err', 'bad reply')})")
            continue
        print(f"{r['slot']:>4} {r['id']:>4} {r['OvrWrnHwm']:>4} {r['OvrWrnLwm']:>4} "
              f"{r['OvrThersh']:>4} {r['LocThrot']:>4} {r['accepted']:>10} "
              f"{r['closeDrop']:>8} {r['fifoDrop']:>7} {r['maxFIFOocc']:>7}")
    good = [r for r in rows if r.get("ok")]
    if good:
        occ = [r["maxFIFOocc"] for r in good]
        drops = sum(r["closeDrop"] + r["fifoDrop"] for r in good)
        print(f"\n  maxFIFOocc across FEUs: min={min(occ)} max={max(occ)}   total drops={drops}")
        hwms = {r["OvrWrnHwm"] for r in good}
        if len(hwms) > 1:
            print(f"  !! FEUs disagree on OvrWrnHwm: {sorted(hwms)}")
        elif max(occ) < min(hwms):
            print(f"  -> occupancy never reaches HWM={hwms.pop()}: the watermark cannot be biting.")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--feus", type=int, nargs="+", choices=sorted(FEUS),
                    help="cfg slot numbers (default: all 8)")
    ap.add_argument("--latch", action="store_true",
                    help="issue LatchStat (a WRITE) to freeze coherent statistics first")
    ap.add_argument("--watch", type=float, metavar="SEC",
                    help="repeat every SEC seconds until Ctrl-C")
    ap.add_argument("--csv", metavar="PATH",
                    help="append every sample to PATH as CSV (one row per FEU per sample); "
                         "flushed each sample so the file is complete if this is killed")
    ap.add_argument("--quiet", action="store_true",
                    help="with --csv: suppress the table, print one progress line per sample")
    args = ap.parse_args()

    slots = args.feus or sorted(FEUS)
    csv_f = None
    if args.csv:
        new = not os.path.exists(args.csv) or os.path.getsize(args.csv) == 0
        csv_f = open(args.csv, "a", buffering=1)
        if new:
            csv_f.write("t_unix,iso,slot,id,hwm,lwm,ovrthresh,locthrot,"
                        "accepted,close_drop,fifo_drop,max_fifo_occ,ok\n")
    try:
        while True:
            rows = [read_feu(s, args.latch) for s in slots]
            now = time.time()
            if csv_f:
                iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now))
                for r in rows:
                    csv_f.write(
                        f"{now:.3f},{iso},{r['slot']},{r['id']},"
                        f"{r.get('OvrWrnHwm', '')},{r.get('OvrWrnLwm', '')},"
                        f"{r.get('OvrThersh', '')},{r.get('LocThrot', '')},"
                        f"{r.get('accepted', '')},{r.get('closeDrop', '')},"
                        f"{r.get('fifoDrop', '')},{r.get('maxFIFOocc', '')},"
                        f"{int(bool(r.get('ok')))}\n")
            if csv_f and args.quiet:
                acc = [r.get("accepted", 0) for r in rows if r.get("ok")]
                print(f"{time.strftime('%H:%M:%S')}  {len(acc)}/{len(rows)} FEUs  "
                      f"accepted min={min(acc) if acc else '-'} max={max(acc) if acc else '-'}")
            else:
                print(f"\n=== FEU trigger counters  {time.strftime('%H:%M:%S')}"
                      f"{'  (latched)' if args.latch else '  (read-only)'} ===")
                print_table(rows)
            if not args.watch:
                return 0 if any(r.get("ok") for r in rows) else 1
            time.sleep(args.watch)
    except KeyboardInterrupt:
        return 0
    finally:
        if csv_f:
            csv_f.close()


if __name__ == "__main__":
    sys.exit(main())
