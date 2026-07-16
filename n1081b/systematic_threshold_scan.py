#!/usr/bin/env python3
"""Systematic wall/scint threshold scan for M1 (.240) / M2 (.241), driven off
M3 (.242) + M4 (.243) counter-mode reads -- M5 (.244) is hard-wedged as of
2026-07-15 (see HANDOFF_2026-07-15_timetag_watcher_board_wedge.md), so this
bypasses it entirely.

Measurement trick (validated live before this script was written): a section's
own analog discriminator only feeds its *logic* function (or/and/coincidence_gate),
not counter mode -- so M1/M2 can't be read by flipping themselves to counter.
But M3's sections receive M1+M2's *already-digital* outputs as genuine inputs,
and M4.SEC_A ("Singles" = OR of the 4 M3 sector outputs, on lemos 0,1,3,4 per
M3 sector 1,2,3,4 respectively -- see n1081b_module_map.py _module4()) receives
M3's digital outputs the same way. Flipping either to FN_COUNTER, reading twice
across a dwell, then restoring the original function+config exactly, gives a
real rate reading without ever touching M5.

Gap: FN_COUNTER only exposes 4 channels (lemo0-3); M4.SEC_A's sector-D tap is on
lemo4, outside that range (confirmed on hardware, even via raw protocol access).
Sector D's coincidence rate is therefore never directly measured here -- see
estimate_sector_d() for how its threshold is chosen from the other sectors'
pattern instead.

Coordinate descent per sector: sweep wall (M1) threshold holding scint (M2)
fixed -> apply the best point -> sweep scint threshold holding new wall fixed
-> apply the best point. Repeated for ROUNDS rounds. Every point is logged
(not just the winner) for later tradeoff-curve plotting.

Objective ("significance"): true_hz / sqrt(max(accidental_hz, epsilon)), where
accidental_hz = r_wall * r_scint * GATE_NS*1e-9 (textbook two-fold accidental
formula) and true_hz = measured_coincidence_hz - accidental_hz. Under the
thresholds/rates actually seen in this campaign, accidental_hz is predicted
negligible almost everywhere (sub-0.1 Hz vs measured coincidences of order
10-100 Hz) -- so this effectively reduces to maximizing true coincidence rate,
while the curves still show up if accidentals ever become non-negligible at
very low threshold.

Usage:
  .venv/bin/python n1081b/systematic_threshold_scan.py [--rounds N] [--dwell S] [--out path.json]
"""
import argparse
import json
import subprocess
import sys
import time

from n1081b_sdk import N1081B

M1_IP = "192.168.10.240"
M2_IP = "192.168.10.241"
M3_IP = "192.168.10.242"
M4_IP = "192.168.10.243"
PASSWORD = "password"
GATE_NS = 20
GATE_S = GATE_NS * 1e-9
SIG_EPS_HZ = 0.01  # floor on accidental_hz to avoid divide-by-zero in significance

SECTORS = ["A", "B", "C", "D"]
# M4.SEC_A lemo -> M3 sector (see n1081b_module_map.py _module4(): lemos 0,1,3,4
# = M3 sectors 1,2,3,4 = A,B,C,D). D is on lemo4, unreachable via FN_COUNTER (4
# channels max, lemo0-3 only) -- confirmed on hardware.
M4_LEMO_FOR_SECTOR = {"A": 0, "B": 1, "C": 3, "D": None}


def connect(ip):
    d = N1081B(ip)
    if not d.connect():
        raise RuntimeError(f"connect to {ip} failed")
    d.ws.settimeout(8)
    if not d.login(PASSWORD):
        raise RuntimeError(f"login to {ip} failed")
    return d


def _sec(name):
    return getattr(N1081B.Section, f"SEC_{name}")


class _V:
    def __init__(self, v):
        self.value = v


def check_flash_mode():
    r = subprocess.run(
        [".venv/bin/python", "n1081b/trigger_mode.py", "status"],
        capture_output=True, text=True, cwd="/home/mx17/PycharmProjects/nTof_x17_DAQ")
    out = r.stdout + r.stderr
    if "mode: flash" not in out:
        raise RuntimeError(f"trigger mode is NOT clean flash -- refusing to touch boards:\n{out}")
    return out


def daq_alive():
    r = subprocess.run(["tmux", "has-session", "-t", "daq_control"], capture_output=True)
    return r.returncode == 0


class Rig:
    """Holds persistent connections to M1-M4 and the cached original function
    configs for M3/M4.SEC_A, so every measurement flips to counter and restores
    to the EXACT original state (verified) without re-fetching config each time."""

    def __init__(self):
        self.m1 = connect(M1_IP)
        self.m2 = connect(M2_IP)
        self.m3 = connect(M3_IP)
        self.m4 = connect(M4_IP)
        self.m3_orig = {}   # sector -> (fn_enum, cfg)
        self.m4a_orig = None
        for s in SECTORS:
            sec = _sec(s)
            fn = [x["function_name"] for x in self.m3.get_sections_function()["data"]][sec.value]
            assert fn == "and", f"M3 SEC_{s} is not 'and' (got {fn}) -- refusing to proceed"
            cfg = self.m3.get_function_configuration(sec)["data"]
            self.m3_orig[s] = cfg
        fn = [x["function_name"] for x in self.m4.get_sections_function()["data"]][N1081B.Section.SEC_A.value]
        assert fn == "or", f"M4 SEC_A is not 'or' (got {fn}) -- refusing to proceed"
        self.m4a_orig = self.m4.get_function_configuration(N1081B.Section.SEC_A)["data"]

    def close(self):
        for d in (self.m1, self.m2, self.m3, self.m4):
            try:
                d.disconnect()
            except Exception:
                pass

    # ---- threshold get/set (M1 wall, M2 scint) ----

    def get_threshold(self, board, sector):
        d = self.m1 if board == "wall" else self.m2
        return d.get_input_configuration(_sec(sector))["data"]["threshold"]

    def set_threshold(self, board, sector, mv):
        d = self.m1 if board == "wall" else self.m2
        sec = _sec(sector)
        orig = d.get_input_configuration(sec)["data"]
        d.set_input_configuration(sec, N1081B.SignalStandard.STANDARD_DISCRIMINATOR,
                                   _V(orig["standard_sub"]), int(mv), N1081B.SignalImpedance.IMPEDANCE_50)
        rb = d.get_input_configuration(sec)["data"]
        if rb["threshold"] != int(mv):
            raise RuntimeError(f"M{'1' if board=='wall' else '2'} SEC_{sector} threshold set failed: "
                                f"wanted {mv}, read back {rb['threshold']}")

    # ---- M3 flip: wall+scint singles for one sector ----

    def _m3_to_counter(self, sector):
        sec = _sec(sector)
        cfg = self.m3_orig[sector]
        le = {e["lemo"]: e["enable"] for e in cfg["lemo_enables"]}
        self.m3.set_section_function(sec, N1081B.FunctionType.FN_COUNTER)
        self.m3.configure_counter(sec, le.get(0, False), le.get(1, False), le.get(2, False), le.get(3, False), False)
        for ch in range(4):
            if le.get(ch, False):
                self.m3.reset_channel(sec, ch, N1081B.FunctionType.FN_COUNTER)

    def _m3_restore(self, sector):
        sec = _sec(sector)
        cfg = self.m3_orig[sector]
        le = {e["lemo"]: e["enable"] for e in cfg["lemo_enables"]}
        self.m3.set_section_function(sec, N1081B.FunctionType.FN_AND)
        self.m3.configure_and(sec, le.get(0, False), le.get(1, False), le.get(2, False), le.get(3, False),
                               le.get(4, False), le.get(5, False),
                               cfg.get("bypass_enable", False), cfg.get("bypass_section", 0))
        rb = self.m3.get_function_configuration(sec)["data"]
        if rb["lemo_enables"] != cfg["lemo_enables"]:
            raise RuntimeError(f"M3 SEC_{sector} restore-to-AND verify FAILED: {rb}")

    def _m4a_to_counter(self):
        cfg = self.m4a_orig
        le = {e["lemo"]: e["enable"] for e in cfg["lemo_enables"]}
        sec = N1081B.Section.SEC_A
        self.m4.set_section_function(sec, N1081B.FunctionType.FN_COUNTER)
        self.m4.configure_counter(sec, le.get(0, False), le.get(1, False), le.get(2, False), le.get(3, False), False)
        for ch in range(4):
            if le.get(ch, False):
                self.m4.reset_channel(sec, ch, N1081B.FunctionType.FN_COUNTER)

    def _m4a_restore(self):
        cfg = self.m4a_orig
        le = {e["lemo"]: e["enable"] for e in cfg["lemo_enables"]}
        sec = N1081B.Section.SEC_A
        self.m4.set_section_function(sec, N1081B.FunctionType.FN_OR)
        self.m4.configure_or(sec, le.get(0, False), le.get(1, False), le.get(2, False), le.get(3, False),
                              le.get(4, False), le.get(5, False),
                              cfg.get("bypass_enable", False), cfg.get("bypass_section", 0))
        rb = self.m4.get_function_configuration(sec)["data"]
        if rb["lemo_enables"] != cfg["lemo_enables"]:
            raise RuntimeError(f"M4 SEC_A restore-to-OR verify FAILED: {rb}")

    def measure(self, sector, dwell):
        """One measurement point for `sector`, done in two SEQUENTIAL phases (not
        concurrent -- M4.SEC_A's coincidence tap for this sector only sees a pulse
        while M3.SEC_<sector> is actively running its AND function, so singles and
        coincidence can't be read in the same window: flipping M3 to counter to
        read singles necessarily silences the coincidence output M4 is tapping).

        Phase 1: flip M3.SEC_<sector> to counter, dwell, read wall+scint singles,
                 restore to AND (so the AND function is live again).
        Phase 2 (only if this sector's coincidence tap is reachable): flip M4.SEC_A
                 to counter, dwell, read this sector's coincidence channel, restore
                 to OR.
        """
        need_coinc = M4_LEMO_FOR_SECTOR[sector] is not None

        self._m3_to_counter(sector)
        try:
            r0_m3 = self.m3.get_function_results(_sec(sector))["data"]["counters"]
            t0 = time.time()
            time.sleep(dwell)
            dt_singles = time.time() - t0
            r1_m3 = self.m3.get_function_results(_sec(sector))["data"]["counters"]
        finally:
            self._m3_restore(sector)

        c0 = {c["lemo"]: c["value"] for c in r0_m3}
        c1 = {c["lemo"]: c["value"] for c in r1_m3}
        wall_hz = round((c1.get(0, 0) - c0.get(0, 0)) / dt_singles, 3)
        scint_hz = round((c1.get(1, 0) - c0.get(1, 0)) / dt_singles, 3)

        coinc_hz = None
        dt_coinc = None
        if need_coinc:
            self._m4a_to_counter()
            try:
                r0_m4 = self.m4.get_function_results(N1081B.Section.SEC_A)["data"]["counters"]
                t0 = time.time()
                time.sleep(dwell)
                dt_coinc = time.time() - t0
                r1_m4 = self.m4.get_function_results(N1081B.Section.SEC_A)["data"]["counters"]
            finally:
                self._m4a_restore()
            lemo = M4_LEMO_FOR_SECTOR[sector]
            m4c0 = {c["lemo"]: c["value"] for c in r0_m4}
            m4c1 = {c["lemo"]: c["value"] for c in r1_m4}
            coinc_hz = round((m4c1.get(lemo, 0) - m4c0.get(lemo, 0)) / dt_coinc, 3)

        accidental_hz = round(wall_hz * scint_hz * GATE_S, 6)
        true_hz = round(coinc_hz - accidental_hz, 3) if coinc_hz is not None else None
        significance = round(true_hz / (max(accidental_hz, SIG_EPS_HZ)) ** 0.5, 2) if true_hz is not None else None

        return {"dwell_singles_s": round(dt_singles, 2),
                "dwell_coinc_s": round(dt_coinc, 2) if dt_coinc is not None else None,
                "wall_hz": wall_hz, "scint_hz": scint_hz,
                "coinc_hz": coinc_hz, "accidental_hz": accidental_hz, "true_hz": true_hz,
                "significance": significance}


def thresholds_around(baseline, board):
    """6 candidate thresholds spanning ~0.5x-2.2x the baseline magnitude,
    preserving sign (M1 wall = positive, M2 scint = negative)."""
    mults = [0.5, 0.75, 1.0, 1.3, 1.7, 2.2]
    sign = 1 if baseline >= 0 else -1
    mag = abs(baseline)
    vals = sorted({int(round(sign * mag * m)) for m in mults})
    return vals


def best_point(points, thresholds):
    """argmax significance (A/B/C); if all significance is None (sector D),
    argmax coinc-agnostic proxy: caller handles D separately."""
    scored = [(t, p) for t, p in zip(thresholds, points) if p["significance"] is not None]
    if not scored:
        return None
    t, p = max(scored, key=lambda tp: tp[1]["significance"])
    return t


def scan_board(rig, sector, board, dwell, log):
    baseline = rig.get_threshold(board, sector)
    thresholds = thresholds_around(baseline, board)
    points = []
    for t in thresholds:
        rig.set_threshold(board, sector, t)
        p = rig.measure(sector, dwell)
        p["threshold_mv"] = t
        points.append(p)
        log(f"    {board:5s} SEC_{sector} T={t:+5d}mV  wall={p['wall_hz']:7.1f}Hz "
            f"scint={p['scint_hz']:7.1f}Hz  coinc={p['coinc_hz']}  true={p['true_hz']}  sig={p['significance']}")
    best_t = best_point(points, thresholds)
    if best_t is None:
        # sector D (no coincidence tap): fall back to the baseline itself for now,
        # real choice made by estimate_sector_d() after A/B/C are done.
        best_t = baseline
    rig.set_threshold(board, sector, best_t)
    log(f"    -> applied {board} SEC_{sector} = {best_t:+d} mV")
    return {"baseline_mv": baseline, "thresholds_mv": thresholds, "points": points, "applied_mv": best_t}


def estimate_sector_d(rig, results, dwell, log):
    """Sector D has no coincidence tap. Re-derive the *relative* threshold
    multiplier (vs each sector's own original baseline) that maximized
    significance for A/B/C, average it, and apply that same relative move to
    D's wall+scint thresholds -- an estimate, not an independent measurement."""
    mults_wall, mults_scint = [], []
    for s in ["A", "B", "C"]:
        wb = results[s]["wall"]["baseline_mv"]
        wa = results[s]["wall"]["applied_mv"]
        sb = results[s]["scint"]["baseline_mv"]
        sa = results[s]["scint"]["applied_mv"]
        if wb:
            mults_wall.append(wa / wb)
        if sb:
            mults_scint.append(sa / sb)
    mw = sum(mults_wall) / len(mults_wall) if mults_wall else 1.0
    ms = sum(mults_scint) / len(mults_scint) if mults_scint else 1.0
    log(f"  sector D estimate: applying A/B/C's average relative move "
        f"(wall x{mw:.2f}, scint x{ms:.2f}) to D's own baseline")

    wb = rig.get_threshold("wall", "D")
    sb = rig.get_threshold("scint", "D")
    w_est = int(round(wb * mw))
    s_est = int(round(sb * ms))
    rig.set_threshold("wall", "D", w_est)
    rig.set_threshold("scint", "D", s_est)
    p = rig.measure("D", dwell)
    log(f"    D wall {wb}->{w_est}mV, scint {sb}->{s_est}mV  "
        f"wall_hz={p['wall_hz']} scint_hz={p['scint_hz']} accidental_hz={p['accidental_hz']} (coinc unmeasured)")
    return {"wall_baseline_mv": wb, "wall_applied_mv": w_est,
            "scint_baseline_mv": sb, "scint_applied_mv": s_est,
            "post_apply_measurement": p, "method": "relative-move-from-ABC-average, coincidence NOT independently verified"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--dwell", type=float, default=15.0)
    ap.add_argument("--out", default="n1081b/snapshots/systematic_scan_2026-07-15.json")
    args = ap.parse_args()

    def log(msg):
        print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)

    log("safety check: trigger mode must be clean flash, DAQ must be alive")
    log(check_flash_mode().strip())
    if not daq_alive():
        raise RuntimeError("daq_control tmux session not found")

    rig = Rig()
    all_results = {"gate_ns": GATE_NS, "rounds": [], "sector_d_estimate": None}
    try:
        for rnd in range(1, args.rounds + 1):
            log(f"=== ROUND {rnd}/{args.rounds} ===")
            round_results = {}
            for sector in ["A", "B", "C"]:
                if rnd % 4 == 0:  # cheap periodic health re-check
                    log(check_flash_mode().strip())
                    if not daq_alive():
                        raise RuntimeError("daq_control tmux session vanished mid-scan")
                log(f"  --- sector {sector} ---")
                wall_res = scan_board(rig, sector, "wall", args.dwell, log)
                scint_res = scan_board(rig, sector, "scint", args.dwell, log)
                round_results[sector] = {"wall": wall_res, "scint": scint_res}
            all_results["rounds"].append(round_results)
            with open(args.out, "w") as f:
                json.dump(all_results, f, indent=2)

        log("  --- sector D (estimate, no coincidence tap) ---")
        all_results["sector_d_estimate"] = estimate_sector_d(rig, all_results["rounds"][-1], args.dwell, log)
        with open(args.out, "w") as f:
            json.dump(all_results, f, indent=2)
        log(f"wrote {args.out}")
    finally:
        rig.close()
        log(check_flash_mode().strip())
        log("DAQ alive: " + str(daq_alive()))


if __name__ == "__main__":
    sys.exit(main())
