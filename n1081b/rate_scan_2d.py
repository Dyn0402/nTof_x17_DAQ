#!/usr/bin/env python3
"""2D trigger-rate scan: M2 plastic threshold x M1 SiPM-wall threshold.

Design (2026-07-16, post-calibration run224460/224466):
  * Plastic PMT HVs are first set to the equalized values from
    calibrations/pss/hv_equalization_run224466.json and then LEFT ALONE.
  * 2D grid = plastic discriminator threshold (M2, negative, uniform across
    sections after HV equalization) x wall threshold (M1, positive, per-wall
    values keeping the calibration's relative offsets, scaled by a common
    multiplier).
  * Two passes, two sectors scanned at a time while the other two are HELD at
    the nominal baseline as a live beam reference (the two-set fixed-reference
    method that held ratios stable across 848<->410 e10 swings in the timing
    scans): pass AC scans A+C holding B+D, pass BD scans B+D holding A+C.
  * Per grid point, sequentially on M5 (.244, monitoring-only scalers):
      1. counter phase: M5 SEC_A/B/C scaler deltas over --dwell s
         (per-sector wall singles, scint singles, sector coincidences);
      2. time-tag phase: stream M5 SEC_D (lemo0=Singles, lemo1=Doubles,
         lemo2=PS/flash line, lemo3=spare) for a proton-gated dwell -- gives
         TIMESTAMPED Singles/Doubles edges plus the flash anchor, so in-window
         (30 ms post-flash) vs out-of-window rates fall out offline.
    Counter reads and TT streaming are never concurrent (M5 broadcasts desync
    replies), and each phase uses its own board_session (lock + clean close).
  * Beam normalization: per-pulse proton integration from the beam CSV
    (BeamAccumulator; never sum beam_state.json) + the held sectors' rates.

Board hygiene: all board contact goes through n1081b_session.board_session.
M1+M2 sessions are held for the whole scan (the flock keeps poll_modules and
other agents off them); M5 sessions are per-phase with BoardBusy retries so
poll_modules' per-subrun snapshot can interleave. M3, M4 and M6 are never
written; M4 is read once (read-only) at preflight to cross-check that M5.D
lemo1 really is the Doubles line. Safe alongside a live flash/flash_random
run: M1/M2 are not in that trigger path (validated 2026-07-14/15).

Hardware limit enforced here and documented in RUN_MODES_2026-07.md par.4:
discriminator thresholds have a |10| mV hardware floor.

Usage:
  .venv/bin/python n1081b/rate_scan_2d.py [--dwell 20] [--tt-pulses 12]
      [--tt-max 65] [--skip-hv] [--label 2d_thr_scan] [--dry-run]
"""
import argparse
import csv
import json
import os
import re
import socket
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
for p in (_HERE, os.path.join(_REPO, "scintillator_hv")):
    if p not in sys.path:
        sys.path.insert(0, p)

from n1081b_sdk import N1081B
from n1081b_session import (board_session, BoardBusyError, BoardWedgedError,
                            BoardQuarantinedError)

try:
    from websocket import WebSocketTimeoutException
except Exception:
    class WebSocketTimeoutException(Exception):
        pass

IDLE = (socket.timeout, WebSocketTimeoutException)

M1_IP = "192.168.10.240"   # walls (positive DISCR)
M2_IP = "192.168.10.241"   # plastics (negative DISCR)
M4_IP = "192.168.10.243"   # read-only preflight cross-check
M5_IP = "192.168.10.244"   # scalers / TT source (monitoring-only board)
SEC_D = N1081B.Section.SEC_D

THRESHOLD_FLOOR_MV = 10    # hardware minimum |threshold| (RUN_MODES par.4)
WRITE_GAP_S = 0.3

# Wall nominals: calibrations/wal_trigger/thresholds_run224460.json recommended
# 12.5/13.5/12.5/13.5 digitizer-mV; hardware mapping unknown, so these are the
# x1.0 anchors (rounded to int mV) and the multiplier ladder spans the mapping
# uncertainty. Relative per-wall offsets from the calibration are kept.
WALL_NOMINAL = {"A": 15, "B": 16, "C": 15, "D": 16}   # post-FIFO 2026-07-17 calib
WALL_MULTS = [1.0, 1.5, 2.2, 3.3]
PLASTIC_LADDER = [-20, -30, -44, -66]   # post-FIFO (2x amplitude); D dead at -20
PASSES = [("AC", ("A", "C"), ("B", "D")),
          ("BD", ("B", "D"), ("A", "C"))]
PLASTIC_BASELINE = -30                  # post-FIFO standing baseline (A/B/C; D runs -38)

SAFE_MODES = {"flash", "flash_random"}
CALIB_HV = os.path.join(_REPO, "calibrations", "pss", "hv_equalization_run224466.json")
OUTPUT_ROOT = os.path.expanduser("~/beam_july/rate_scan_2d")

# PSS calibration name -> scint_hv_config channel name
PMT_TO_CHANNEL = {f"PSS{w}{i}": f"plastic_{w}_{'L' if i == 1 else 'R'}"
                  for w in "ABCD" for i in (1, 2)}


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


class _V:
    def __init__(self, v):
        self.value = v


def _sec(name):
    return getattr(N1081B.Section, f"SEC_{name}")


def check_safe_mode():
    """Exact-match trigger-mode allow-list (same as systematic v3)."""
    r = subprocess.run([".venv/bin/python", "n1081b/trigger_mode.py", "status"],
                       capture_output=True, text=True, cwd=_REPO)
    out = r.stdout + r.stderr
    m = re.search(r"looks like mode: (\S+)", out)
    mode = m.group(1) if m else None
    if mode not in SAFE_MODES:
        raise RuntimeError(f"trigger mode '{mode}' not in safe set {SAFE_MODES}:\n{out}")
    return mode


def daq_alive():
    return subprocess.run(["tmux", "has-session", "-t", "daq_control"],
                          capture_output=True).returncode == 0


def open_session(ip, purpose, tries=8, wait_s=6, **kw):
    """board_session with polite BoardBusy retries (poll_modules holds boards
    for a few seconds at sub-run boundaries). Wedge/quarantine still raise."""
    for i in range(tries):
        try:
            s = board_session(ip, purpose=purpose, min_gap_s=WRITE_GAP_S, **kw)
            s.__enter__()
            return s
        except BoardBusyError:
            if i == tries - 1:
                raise
            log(f"  {ip} busy (likely poll_modules) -- retrying in {wait_s}s")
            time.sleep(wait_s)


# --------------------------------------------------------------------------- #
# M1/M2 threshold rig (sessions held for the whole scan)
# --------------------------------------------------------------------------- #
class ThresholdRig:
    def __init__(self):
        self.m1 = self.m2 = None
        try:
            self.m1 = open_session(M1_IP, "rate_scan_2d wall thresholds")
            self.m2 = open_session(M2_IP, "rate_scan_2d plastic thresholds")
            self.original = {"wall": {}, "plastic": {}}
            for sec in "ABCD":
                self.original["wall"][sec] = self.get(self.m1, sec)
                self.original["plastic"][sec] = self.get(self.m2, sec)
        except Exception:
            self.close()
            raise

    def close(self):
        for s in (self.m1, self.m2):
            if s is not None:
                try:
                    s.__exit__(None, None, None)
                except Exception:
                    pass
        self.m1 = self.m2 = None

    @staticmethod
    def get(s, sector):
        return s.call("get_input_configuration", _sec(sector))["data"]["threshold"]

    def set(self, board, sector, mv):
        mv = int(mv)
        if abs(mv) < THRESHOLD_FLOOR_MV:
            raise ValueError(f"|{mv}| mV below the {THRESHOLD_FLOOR_MV} mV hardware floor")
        s = self.m1 if board == "wall" else self.m2
        sec = _sec(sector)
        orig = s.call("get_input_configuration", sec)["data"]
        if orig["threshold"] == mv:
            return
        s.call("set_input_configuration", sec,
               N1081B.SignalStandard.STANDARD_DISCRIMINATOR,
               _V(orig["standard_sub"]), mv, N1081B.SignalImpedance.IMPEDANCE_50)
        rb = s.call("get_input_configuration", sec)["data"]
        if rb["threshold"] != mv:
            raise RuntimeError(f"{board} SEC_{sector}: wanted {mv}, read back {rb['threshold']}")


# --------------------------------------------------------------------------- #
# M5 phases (fresh session per phase)
# --------------------------------------------------------------------------- #
def m5_counter_rates(dwell, sections=("SEC_A", "SEC_B", "SEC_C")):
    s = open_session(M5_IP, "rate_scan_2d counter phase")
    try:
        def snap():
            out = {}
            for name in sections:
                res = s.call("get_function_results", getattr(N1081B.Section, name))
                ctrs = ((res or {}).get("data") or {}).get("counters") or []
                out[name] = [c.get("value") for c in ctrs]
            return out
        r0 = snap()
        t0 = time.time()
        time.sleep(dwell)
        r1 = snap()
        dt = time.time() - t0
        rates = {}
        for name in sections:
            a, b = r0[name], r1[name]
            n = min(len(a), len(b), 4)
            rates[name] = {i: round((b[i] - a[i]) / dt, 3) for i in range(n)}
        return rates, dt
    finally:
        s.__exit__(None, None, None)


def m5_arm_tt():
    """Put M5.SEC_D in time-tag mode, all 6 lemos (stream stays stopped)."""
    s = open_session(M5_IP, "rate_scan_2d arm TT")
    try:
        s.call("set_section_function", SEC_D, N1081B.FunctionType.FN_TIME_TAG)
        # Result:False from this call is cosmetic on this fw (mod5_timetag_logger)
        s.call("configure_time_tagging", SEC_D, True, True, True, True, True, True)
    finally:
        s.__exit__(None, None, None)


def m5_restore_counter():
    s = open_session(M5_IP, "rate_scan_2d restore counter")
    try:
        s.call("set_section_function", SEC_D, N1081B.FunctionType.FN_COUNTER)
        s.call("configure_counter", SEC_D, True, True, True, True, False)
        names = [x["function_name"] for x in s.call("get_sections_function")["data"]]
        if names[SEC_D.value] != "counter":
            raise RuntimeError(f"M5.SEC_D restore verify failed: {names}")
        log(f"M5 sections restored: {names}")
    finally:
        s.__exit__(None, None, None)


def m5_tt_phase(writer, point_id, target_pulses, min_s, max_s, anchor_panel):
    """Stream M5.SEC_D tags until >=target_pulses anchor (PS/flash) edges AND
    >=min_s elapsed, or max_s cap. Rows: point_id, host_unix, panel, t_board_ns.
    Raw ws sends for start/stop (the SDK helper desyncs); own session so the
    post-stream socket is simply closed, never reused for commands."""
    s = open_session(M5_IP, "rate_scan_2d TT phase")
    stats, ps = {}, 0
    t0 = time.time()
    try:
        dev = s.dev
        dev.ws.send('{"command":"reset_channel", "callback":"start", '
                    '"params":{"section":%d, "channel":1}}' % SEC_D.value)
        dev.ws.send('{"command":"start_tt_data", "callback":"start", '
                    '"params":{"section":%d}}' % SEC_D.value)
        dev.ws.settimeout(0.5)

        def drain_until(t_end, gated):
            nonlocal ps
            while time.time() < t_end:
                el = time.time() - t0
                if gated and el >= min_s and ps >= target_pulses:
                    return
                try:
                    raw = dev.ws.recv()
                except IDLE:
                    continue
                now = time.time()
                try:
                    pkt = json.loads(raw.replace(",]", "]"))
                except Exception:
                    continue
                if pkt.get("command") != "send_data":
                    continue
                for tag in pkt.get("timetag_data", []):
                    writer.writerow((point_id, f"{now:.3f}", tag[0], tag[1]))
                    stats[tag[0]] = stats.get(tag[0], 0) + 1
                    if tag[0] == anchor_panel:
                        ps += 1

        drain_until(t0 + max_s, gated=True)
        dev.ws.send('{"command":"stop_tt_data", "callback":"stop", '
                    '"params":{"section":%d}}' % SEC_D.value)
        time.sleep(0.2)
        drain_until(time.time() + 0.6, gated=False)
        return {"tags_per_panel": stats, "anchor_edges": ps,
                "t0": round(t0, 3), "live_s": round(time.time() - t0, 1)}
    finally:
        s.__exit__(None, None, None)


# --------------------------------------------------------------------------- #
# HV equalization + beam helpers (scintillator_hv machinery)
# --------------------------------------------------------------------------- #
def equalize_plastic_hv():
    import scint_hv_config as shv_cfg
    import scint_hv_lib as shv
    with open(CALIB_HV) as f:
        calib = json.load(f)
    targets = {PMT_TO_CHANNEL[k]: v["v_suggested"] for k, v in calib["pmts"].items()}
    shv.validate_channels()
    caen = shv.open_session()
    before, after = {}, {}
    with caen:
        chans = []
        for c in shv_cfg.SCINT_CHANNELS:
            slot, ch = int(c["slot"]), int(c["channel"])
            before[c["name"]] = round(caen.get_ch_vmon(slot, ch), 1)
            tv = targets[c["name"]]
            caen.set_ch_v0(slot, ch, tv)
            if caen.get_ch_power(slot, ch) != 1:
                caen.set_ch_pw(slot, ch, 1)
            chans.append((c, slot, ch, tv))
            log(f"  {c['name']} {slot}:{ch}: {before[c['name']]} -> {tv} V")
        deadline = time.time() + 180
        while True:
            pending = []
            for c, slot, ch, tv in chans:
                vmon = caen.get_ch_vmon(slot, ch)
                after[c["name"]] = round(vmon, 1)
                if abs(vmon - tv) > 1.5:
                    pending.append(f"{c['name']}@{vmon:.0f}")
            if not pending:
                log("  HV equalization ramped.")
                return before, targets, after
            if time.time() > deadline:
                raise RuntimeError(f"HV ramp timeout; still pending: {pending}")
            time.sleep(5)


def wait_for_beam():
    import scint_hv_lib as shv
    while True:
        state = shv.read_beam_state()
        if state and state.get("beam_on"):
            return state
        log("  beam OFF -- waiting 15 s (Ctrl-C to abort)...")
        time.sleep(15)


# --------------------------------------------------------------------------- #
def preflight_baseline(dwell):
    """Simultaneous M5 (all 4 sections) + M4.B (read-only) scaler window.
    Establishes: sector-D coincidence presence on M5.C ch3, the M5.D lemo map
    (0=Singles, 1=Doubles [vs M4.B TOTAL], 2=PS ~0.3 Hz), pre-scan rates."""
    s5 = open_session(M5_IP, "rate_scan_2d preflight")
    s4 = None
    try:
        s4 = open_session(M4_IP, "rate_scan_2d preflight M4.B read-only")

        def snap():
            out = {}
            for name in ("SEC_A", "SEC_B", "SEC_C", "SEC_D"):
                res = s5.call("get_function_results", getattr(N1081B.Section, name))
                ctrs = ((res or {}).get("data") or {}).get("counters") or []
                out[name] = [c.get("value") for c in ctrs]
            res = s4.call("get_function_results", N1081B.Section.SEC_B)
            ctrs = ((res or {}).get("data") or {}).get("counters") or []
            out["M4_B"] = [c.get("value") for c in ctrs]
            return out

        r0 = snap()
        t0 = time.time()
        time.sleep(dwell)
        r1 = snap()
        dt = time.time() - t0
        rates = {}
        for k in r0:
            n = min(len(r0[k]), len(r1[k]))
            rates[k] = [round((r1[k][i] - r0[k][i]) / dt, 3) for i in range(n)]
        return rates, dt
    finally:
        for s in (s4, s5):
            if s is not None:
                try:
                    s.__exit__(None, None, None)
                except Exception:
                    pass


def main():
    global WALL_NOMINAL, WALL_MULTS, PLASTIC_LADDER, PLASTIC_BASELINE
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dwell", type=float, default=20.0, help="counter-phase seconds")
    # Grid overrides (2026-07-17: FIFO fan-out doubled the plastic amplitudes and
    # the SiPM sums were re-zeroed -- the baked-in ladders are stale; pick new
    # ones from a threshold_ladder.py run and pass them here).
    ap.add_argument("--wall-nominal", default=None,
                    help='per-wall x1.0 anchors, e.g. "A:15,B:16,C:15,D:16"')
    ap.add_argument("--wall-mults", default=None, help='e.g. "1.0,1.5,2.2,3.3"')
    ap.add_argument("--plastic-ladder", default=None, help='e.g. "-20,-30,-44,-66"')
    ap.add_argument("--plastic-baseline", type=int, default=None,
                    help="held-sector plastic mV (avoid at-floor values: a held "
                         "sector saturated at -10 biased the 07-16 scan)")
    ap.add_argument("--tt-pulses", type=int, default=12,
                    help="anchor (PS) edges to collect per TT phase")
    ap.add_argument("--tt-min", type=float, default=40.0)
    ap.add_argument("--tt-max", type=float, default=65.0)
    ap.add_argument("--anchor-panel", type=int, default=3,
                    help="TT panel number of the PS/flash line (lemo2 -> panel 3)")
    ap.add_argument("--skip-hv", action="store_true",
                    help="skip the plastic HV equalization step")
    ap.add_argument("--preflight-dwell", type=float, default=30.0)
    ap.add_argument("--label", default="2d_thr_scan")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the grid and time estimate; no hardware contact")
    args = ap.parse_args()

    if args.wall_nominal:
        WALL_NOMINAL = {k.strip().upper(): int(v) for k, v in
                        (kv.split(":") for kv in args.wall_nominal.split(","))}
        if sorted(WALL_NOMINAL) != list("ABCD"):
            raise SystemExit(f'--wall-nominal needs all of A:..,B:..,C:..,D:.. '
                             f'(got {args.wall_nominal})')
    if args.wall_mults:
        WALL_MULTS = [float(x) for x in args.wall_mults.replace(" ", "").split(",") if x]
    if args.plastic_ladder:
        PLASTIC_LADDER = [int(x) for x in args.plastic_ladder.replace(" ", "").split(",") if x]
    if args.plastic_baseline is not None:
        PLASTIC_BASELINE = args.plastic_baseline
    for v in list(PLASTIC_LADDER) + [PLASTIC_BASELINE]:
        if v >= 0 or abs(v) < THRESHOLD_FLOOR_MV:
            raise SystemExit(f"plastic value {v} must be negative and |>= {THRESHOLD_FLOOR_MV}| mV")
    for s in "ABCD":
        if WALL_NOMINAL[s] < THRESHOLD_FLOOR_MV:
            raise SystemExit(f"wall nominal {s}:{WALL_NOMINAL[s]} below the "
                             f"{THRESHOLD_FLOOR_MV} mV floor")

    grid = [(m, p) for m in WALL_MULTS for p in PLASTIC_LADDER]
    n_pts = len(PASSES) * len(grid)
    per_pt = args.dwell + (args.tt_min + args.tt_max) / 2 + 12
    log(f"grid: {len(WALL_MULTS)} wall mults x {len(PLASTIC_LADDER)} plastic thr "
        f"x {len(PASSES)} passes = {n_pts} points; ~{per_pt:.0f} s/pt -> "
        f"~{n_pts * per_pt / 60:.0f} min + ~8 min overhead")
    for m in WALL_MULTS:
        walls = {s: max(THRESHOLD_FLOOR_MV, int(round(WALL_NOMINAL[s] * m))) for s in "ABCD"}
        log(f"  wall x{m}: {walls}   plastic ladder: {PLASTIC_LADDER}")
    if args.dry_run:
        return

    outdir = os.path.join(OUTPUT_ROOT, f"{time.strftime('%Y-%m-%d_%H-%M-%S')}_{args.label}")
    os.makedirs(outdir, exist_ok=True)
    log(f"output: {outdir}")

    mode = check_safe_mode()
    log(f"trigger mode: {mode} (safe); daq_control tmux alive: {daq_alive()}")

    import scint_hv_lib as shv
    beam_acc = shv.BeamAccumulator()
    wait_for_beam()

    cfg_rec = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "mode": mode,
               "wall_nominal": WALL_NOMINAL, "wall_mults": WALL_MULTS,
               "plastic_ladder": PLASTIC_LADDER, "plastic_baseline": PLASTIC_BASELINE,
               "dwell_s": args.dwell, "tt_pulses": args.tt_pulses,
               "tt_min_s": args.tt_min, "tt_max_s": args.tt_max,
               "anchor_panel": args.anchor_panel, "argv": sys.argv}

    if not args.skip_hv:
        log("Equalizing plastic HV (calibrations/pss, then left alone)...")
        hv_before, hv_targets, hv_after = equalize_plastic_hv()
        cfg_rec["hv_equalization"] = {"before": hv_before, "targets": hv_targets,
                                      "after": hv_after}
    else:
        log("--skip-hv: leaving plastic HV as-is")

    log(f"Preflight scaler baseline ({args.preflight_dwell:.0f} s, M5 all sections + M4.B)...")
    pre_rates, pre_dt = preflight_baseline(args.preflight_dwell)
    cfg_rec["preflight_rates_hz"] = pre_rates
    cfg_rec["preflight_dwell_s"] = round(pre_dt, 1)
    log(f"  M5.A walls   : {pre_rates['SEC_A']}")
    log(f"  M5.B scints  : {pre_rates['SEC_B']}")
    log(f"  M5.C sectors : {pre_rates['SEC_C']}  <- ch3 = sector D presence check")
    log(f"  M5.D taps    : {pre_rates['SEC_D']}  <- [Singles, Doubles?, PS?, spare]")
    log(f"  M4.B doubles : {pre_rates['M4_B']}  (TOTAL first; cross-check vs M5.D ch1)")

    rig = ThresholdRig()
    cfg_rec["original_thresholds"] = rig.original
    log(f"original thresholds: {rig.original}")
    with open(os.path.join(outdir, "config.json"), "w") as f:
        json.dump(cfg_rec, f, indent=1)

    points_path = os.path.join(outdir, "points.jsonl")
    tt_disabled = False
    interrupted = False
    try:
        # everyone to the nominal baseline first
        log("Setting ALL sections to baseline (walls nominal, plastics "
            f"{PLASTIC_BASELINE} mV)...")
        for sec in "ABCD":
            rig.set("wall", sec, WALL_NOMINAL[sec])
            rig.set("plastic", sec, PLASTIC_BASELINE)
        m5_arm_tt()
        log("M5.SEC_D armed for time-tag phases.")

        for pass_name, scan_secs, hold_secs in PASSES:
            check_safe_mode()
            if not daq_alive():
                log("WARNING: daq_control tmux gone (scan is independent; continuing)")
            tt_csv = open(os.path.join(outdir, f"tt_pass{pass_name}.csv"), "a", buffering=1)
            tt_writer = csv.writer(tt_csv)
            if tt_csv.tell() == 0:
                tt_writer.writerow(("point_id", "host_unix", "panel", "t_board_ns"))
            log(f"=== pass {pass_name}: scanning {scan_secs}, holding {hold_secs} at baseline ===")
            try:
                for wi, mult in enumerate(WALL_MULTS):
                    for sec in scan_secs:
                        rig.set("wall", sec, max(THRESHOLD_FLOOR_MV,
                                                 int(round(WALL_NOMINAL[sec] * mult))))
                    ladder = PLASTIC_LADDER if wi % 2 == 0 else PLASTIC_LADDER[::-1]
                    for pthr in ladder:
                        for sec in scan_secs:
                            rig.set("plastic", sec, pthr)
                        point_id = f"{pass_name}_w{mult}_p{pthr}"
                        state = wait_for_beam()
                        beam_acc.update(state)
                        beam_acc.reset()
                        t_pt = time.time()
                        rates, cdt = m5_counter_rates(args.dwell)
                        tt = None
                        if not tt_disabled:
                            tt = m5_tt_phase(tt_writer, point_id, args.tt_pulses,
                                             args.tt_min, args.tt_max, args.anchor_panel)
                            if sum(tt["tags_per_panel"].values()) == 0:
                                log("WARNING: TT phase returned 0 tags while beam on -- "
                                    "disabling further TT phases (counters continue)")
                                tt_disabled = True
                        state = shv.read_beam_state()
                        beam_acc.update(state)
                        rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                               "pass": pass_name, "point_id": point_id,
                               "wall_mult": mult, "plastic_mv": pthr,
                               "wall_mv": {s: rig.get(rig.m1, s) for s in scan_secs},
                               "held": {"sectors": list(hold_secs),
                                        "wall_mv": {s: WALL_NOMINAL[s] for s in hold_secs},
                                        "plastic_mv": PLASTIC_BASELINE},
                               "counters_hz": rates, "counter_dwell_s": round(cdt, 1),
                               "tt": tt, "point_wall_s": round(time.time() - t_pt, 1),
                               "beam_e10": round(beam_acc.cum_e10, 1),
                               "beam_state": {k: state.get(k) for k in
                                              ("beam_on", "last_pulse_e10", "pulses_10min")
                                              } if state else None}
                        with open(points_path, "a") as f:
                            f.write(json.dumps(rec) + "\n")
                        coinc = rates.get("SEC_C", {})
                        log(f"  {point_id}: sectors {coinc} Hz | "
                            f"tt {tt['tags_per_panel'] if tt else 'off'} | "
                            f"{rec['beam_e10']} e10 | {rec['point_wall_s']}s")
            finally:
                tt_csv.close()
            # scanned pair back to baseline before the passes swap roles
            for sec in scan_secs:
                rig.set("wall", sec, WALL_NOMINAL[sec])
                rig.set("plastic", sec, PLASTIC_BASELINE)
            log(f"pass {pass_name} done; {scan_secs} restored to baseline")
    except KeyboardInterrupt:
        interrupted = True
        log("Interrupted -- restoring...")
    finally:
        try:
            for sec in "ABCD":
                rig.set("wall", sec, WALL_NOMINAL[sec])
                rig.set("plastic", sec, PLASTIC_BASELINE)
            log("thresholds left at nominal baseline (originals in config.json)")
        except Exception as e:
            log(f"WARNING: baseline restore incomplete ({e!r}) -- check M1/M2 manually")
        rig.close()
        try:
            m5_restore_counter()
        except Exception as e:
            log(f"WARNING: M5.SEC_D restore failed ({e!r}) -- rerun "
                f"mod5_timetag_logger-style restore or restore_244_counters.py")
    check_safe_mode()
    log(("INTERRUPTED -- partial " if interrupted else "DONE -- ")
        + f"data in {outdir}")


if __name__ == "__main__":
    main()
