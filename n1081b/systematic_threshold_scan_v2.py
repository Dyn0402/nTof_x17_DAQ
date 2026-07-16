#!/usr/bin/env python3
"""Systematic wall/scint threshold scan, v2 -- beam-normalized.

v1 (systematic_threshold_scan.py) measured one sector at a time: single-shot
~10s reads, no cross-check against beam intensity drift. The resulting curves
were noisy enough that a real feature (sector C's response) was briefly
mistaken for a threshold peak that turned out to be pure beam noise.

v2 fixes this by reading ALL 4 sectors SIMULTANEOUSLY in every measurement
window (all 4 M3 sections can be flipped to counter mode at once; M4.SEC_A
already reads 3 sectors' coincidence in one flip) and normalizing the sector
being swept against the other sectors, whose thresholds are held fixed for
that whole sweep. This is the same two-set fixed-reference trick used in
timing_task3_scan.py's delay-curve scan: the reference sectors cancel
beam-intensity common-mode drift point by point, since they're read in the
exact same window as the sector under test.

Primary curve plotted/optimized: coinc_norm = coinc_hz[swept] / mean(coinc_hz[others]),
a beam-corrected ratio. Selection is smoothed (3-point moving average across
neighboring threshold points) before taking the best point, to favor broad
plateaus over single-point spikes (the exact failure mode v1 hit on sector C).

Usage:
  .venv/bin/python n1081b/systematic_threshold_scan_v2.py [--dwell S] [--points N] [--out path.json]
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
SIG_EPS_HZ = 0.01

SECTORS = ["A", "B", "C", "D"]
M4_LEMO_FOR_SECTOR = {"A": 0, "B": 1, "C": 3, "D": None}
COINC_SECTORS = [s for s in SECTORS if M4_LEMO_FOR_SECTOR[s] is not None]


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
    def __init__(self):
        self.m1 = connect(M1_IP)
        self.m2 = connect(M2_IP)
        self.m3 = connect(M3_IP)
        self.m4 = connect(M4_IP)
        self.m3_orig = {}
        for s in SECTORS:
            sec = _sec(s)
            fn = [x["function_name"] for x in self.m3.get_sections_function()["data"]][sec.value]
            assert fn == "and", f"M3 SEC_{s} is not 'and' (got {fn}) -- refusing to proceed"
            self.m3_orig[s] = self.m3.get_function_configuration(sec)["data"]
        fn = [x["function_name"] for x in self.m4.get_sections_function()["data"]][N1081B.Section.SEC_A.value]
        assert fn == "or", f"M4 SEC_A is not 'or' (got {fn}) -- refusing to proceed"
        self.m4a_orig = self.m4.get_function_configuration(N1081B.Section.SEC_A)["data"]

    def close(self):
        for d in (self.m1, self.m2, self.m3, self.m4):
            try:
                d.disconnect()
            except Exception:
                pass

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
            raise RuntimeError(f"threshold set failed for {board} SEC_{sector}: "
                                f"wanted {mv}, read back {rb['threshold']}")

    def _m3_to_counter(self, sector):
        sec = _sec(sector)
        le = {e["lemo"]: e["enable"] for e in self.m3_orig[sector]["lemo_enables"]}
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
        le = {e["lemo"]: e["enable"] for e in self.m4a_orig["lemo_enables"]}
        sec = N1081B.Section.SEC_A
        self.m4.set_section_function(sec, N1081B.FunctionType.FN_COUNTER)
        self.m4.configure_counter(sec, le.get(0, False), le.get(1, False), le.get(2, False), le.get(3, False), False)
        for ch in range(4):
            if le.get(ch, False):
                self.m4.reset_channel(sec, ch, N1081B.FunctionType.FN_COUNTER)

    def _m4a_restore(self):
        le = {e["lemo"]: e["enable"] for e in self.m4a_orig["lemo_enables"]}
        sec = N1081B.Section.SEC_A
        self.m4.set_section_function(sec, N1081B.FunctionType.FN_OR)
        self.m4.configure_or(sec, le.get(0, False), le.get(1, False), le.get(2, False), le.get(3, False),
                              le.get(4, False), le.get(5, False),
                              self.m4a_orig.get("bypass_enable", False), self.m4a_orig.get("bypass_section", 0))
        rb = self.m4.get_function_configuration(sec)["data"]
        if rb["lemo_enables"] != self.m4a_orig["lemo_enables"]:
            raise RuntimeError("M4 SEC_A restore-to-OR verify FAILED")

    def measure_all(self, dwell):
        """One simultaneous read of ALL 4 sectors' wall+scint singles (phase 1,
        all 4 M3 sections flipped to counter at once) and the 3 reachable
        sectors' coincidence (phase 2, M3 back in AND, M4.SEC_A flipped).
        Phases are sequential (M4's tap needs M3's AND live -- see v1 note)."""
        for s in SECTORS:
            self._m3_to_counter(s)
        try:
            r0 = {s: self.m3.get_function_results(_sec(s))["data"]["counters"] for s in SECTORS}
            t0 = time.time()
            time.sleep(dwell)
            dt1 = time.time() - t0
            r1 = {s: self.m3.get_function_results(_sec(s))["data"]["counters"] for s in SECTORS}
        finally:
            for s in SECTORS:
                self._m3_restore(s)

        wall_hz, scint_hz = {}, {}
        for s in SECTORS:
            c0 = {c["lemo"]: c["value"] for c in r0[s]}
            c1 = {c["lemo"]: c["value"] for c in r1[s]}
            wall_hz[s] = round((c1.get(0, 0) - c0.get(0, 0)) / dt1, 3)
            scint_hz[s] = round((c1.get(1, 0) - c0.get(1, 0)) / dt1, 3)

        self._m4a_to_counter()
        try:
            r0m4 = self.m4.get_function_results(N1081B.Section.SEC_A)["data"]["counters"]
            t0 = time.time()
            time.sleep(dwell)
            dt2 = time.time() - t0
            r1m4 = self.m4.get_function_results(N1081B.Section.SEC_A)["data"]["counters"]
        finally:
            self._m4a_restore()
        m4c0 = {c["lemo"]: c["value"] for c in r0m4}
        m4c1 = {c["lemo"]: c["value"] for c in r1m4}
        coinc_hz = {s: round((m4c1.get(M4_LEMO_FOR_SECTOR[s], 0) - m4c0.get(M4_LEMO_FOR_SECTOR[s], 0)) / dt2, 3)
                    for s in COINC_SECTORS}
        coinc_hz["D"] = None

        return {"dwell_singles_s": round(dt1, 2), "dwell_coinc_s": round(dt2, 2),
                "wall_hz": wall_hz, "scint_hz": scint_hz, "coinc_hz": coinc_hz}


def mean(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def annotate_point(sector, m):
    others = [s for s in SECTORS if s != sector]
    wall_ref = mean(m["wall_hz"][s] for s in others)
    coinc_ref = mean(m["coinc_hz"][s] for s in others if s in COINC_SECTORS)
    wall_x = m["wall_hz"][sector]
    scint_x = m["scint_hz"][sector]
    coinc_x = m["coinc_hz"].get(sector)
    accidental_hz = round(wall_x * scint_x * GATE_S, 6)
    true_hz = round(coinc_x - accidental_hz, 3) if coinc_x is not None else None
    significance = round(true_hz / (max(accidental_hz, SIG_EPS_HZ)) ** 0.5, 2) if true_hz is not None else None
    coinc_norm = round(coinc_x / coinc_ref, 4) if (coinc_x is not None and coinc_ref) else None
    return {**m, "wall_hz_x": wall_x, "scint_hz_x": scint_x, "coinc_hz_x": coinc_x,
            "wall_ref": round(wall_ref, 2) if wall_ref else None,
            "coinc_ref": round(coinc_ref, 3) if coinc_ref else None,
            "accidental_hz": accidental_hz, "true_hz": true_hz, "significance": significance,
            "coinc_norm": coinc_norm}


def thresholds_around(baseline, n_points=7):
    mults = [0.5, 0.7, 0.85, 1.0, 1.2, 1.5, 1.9][:n_points]
    sign = 1 if baseline >= 0 else -1
    mag = abs(baseline)
    return sorted({int(round(sign * mag * m)) for m in mults})


def smoothed_best(thresholds, points, key):
    """3-point moving average over `key` (in threshold order), then argmax --
    favors broad plateaus over single-point spikes (see module docstring)."""
    vals = [p[key] for p in points]
    if all(v is None for v in vals):
        return None
    smoothed = []
    for i in range(len(vals)):
        window = [vals[j] for j in (i - 1, i, i + 1) if 0 <= j < len(vals) and vals[j] is not None]
        smoothed.append(sum(window) / len(window) if window else None)
    best_i = max(range(len(smoothed)), key=lambda i: smoothed[i] if smoothed[i] is not None else -1e9)
    return thresholds[best_i]


def scan_board(rig, sector, board, dwell, n_points, log):
    baseline = rig.get_threshold(board, sector)
    thresholds = thresholds_around(baseline, n_points)
    points = []
    for t in thresholds:
        rig.set_threshold(board, sector, t)
        m = rig.measure_all(dwell)
        p = annotate_point(sector, m)
        p["threshold_mv"] = t
        points.append(p)
        log(f"    {board:5s} SEC_{sector} T={t:+5d}mV  wall={p['wall_hz_x']:7.1f}Hz(ref {p['wall_ref']})  "
            f"coinc={p['coinc_hz_x']}(ref {p['coinc_ref']})  true={p['true_hz']}  "
            f"coinc_norm={p['coinc_norm']}  sig={p['significance']}")
    key = "coinc_norm" if sector in COINC_SECTORS else "wall_hz_x"
    best_t = smoothed_best(thresholds, points, key)
    if best_t is None:
        best_t = baseline
    rig.set_threshold(board, sector, best_t)
    log(f"    -> applied {board} SEC_{sector} = {best_t:+d} mV (smoothed-plateau pick on {key})")
    return {"baseline_mv": baseline, "thresholds_mv": thresholds, "points": points, "applied_mv": best_t}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dwell", type=float, default=15.0)
    ap.add_argument("--points", type=int, default=7)
    ap.add_argument("--out", default="n1081b/snapshots/systematic_scan_v2_2026-07-15.json")
    args = ap.parse_args()

    def log(msg):
        print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)

    log("safety check: trigger mode must be clean flash, DAQ must be alive")
    log(check_flash_mode().strip())
    if not daq_alive():
        raise RuntimeError("daq_control tmux session not found")

    rig = Rig()
    results = {"gate_ns": GATE_NS, "dwell_s": args.dwell, "sectors": {}}
    try:
        for i, sector in enumerate(SECTORS):
            log(check_flash_mode().strip())
            if not daq_alive():
                raise RuntimeError("daq_control tmux session vanished mid-scan")
            log(f"  --- sector {sector} ---")
            wall_res = scan_board(rig, sector, "wall", args.dwell, args.points, log)
            scint_res = scan_board(rig, sector, "scint", args.dwell, args.points, log)
            results["sectors"][sector] = {"wall": wall_res, "scint": scint_res}
            with open(args.out, "w") as f:
                json.dump(results, f, indent=2)
        log(f"wrote {args.out}")
    finally:
        rig.close()
        log(check_flash_mode().strip())
        log("DAQ alive: " + str(daq_alive()))


if __name__ == "__main__":
    sys.exit(main())
