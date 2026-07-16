#!/usr/bin/env python3
"""Systematic wall/scint threshold scan, v3 -- full 2D grid + measured on/off-plateau.

v2 (systematic_threshold_scan_v2.py) fixed beam-normalization (all 4 sectors read
simultaneously, swept sector ratioed against the other 3) but was still coordinate
descent (1D: sweep wall holding scint, then scint holding new wall) and "accidental"
was a textbook formula (r_wall*r_scint*gate), never actually measured.

v3 does both requested upgrades:
  1. FULL 2D GRID per sector (A/B/C, which have a coincidence tap): every
     (wall_threshold, scint_threshold) combination, not just two 1D slices.
     Sector D has no coincidence tap at all (hardware limit, unchanged from v1/v2)
     so it stays a simple independent 1D singles sweep on each board.
  2. MEASURED on/off-plateau coincidence: "on-plateau" is the existing delay=0
     M4.SEC_A read (true+accidental). "off-plateau" temporarily shifts the SWEPT
     sector's M3 wall-in channel delay to +150ns (same gate=20ns width, well
     outside the ~38ns-FWHM coincidence peak found in the original delay-curve
     scan -- see trigger-timing-scan-beam memory) so the same M4 tap now reads
     accidentals only, then restores delay=0 before the next point.

SAFETY FIX vs v1/v2: those scripts used a loose substring check ("mode: flash"
in status text) which would ALSO match "flash_random" even if that mode were
someday unsafe. This version checks the exact reported mode against an explicit
allow-list.

This is a long-running scan (~2.5h estimated for a 7x7 grid x 3 sectors + D's 1D
sweeps at 15s dwell/phase) -- checkpoints to `--out` after EVERY grid point (not
just per-sector) so an interruption loses at most one point's data, and re-checks
flash-mode/DAQ health every `--safety-check-every` points, aborting immediately
(with full restore) if the mode leaves the safe set.

Usage:
  .venv/bin/python n1081b/systematic_threshold_scan_v3.py [--dwell S] [--grid N] [--out path.json]
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

if "__file__" in globals():
    _HERE = os.path.dirname(os.path.abspath(__file__))
    if _HERE not in sys.path:
        sys.path.insert(0, _HERE)

from n1081b_sdk import N1081B
from n1081b_session import (board_session, BoardBusyError, BoardWedgedError,
                            BoardQuarantinedError)

M1_IP = "192.168.10.240"
M2_IP = "192.168.10.241"
M3_IP = "192.168.10.242"
M4_IP = "192.168.10.243"
PASSWORD = "password"
GATE_NS = 20
GATE_S = GATE_NS * 1e-9
OFF_PLATEAU_DELAY_NS = 150  # well outside the ~38ns-FWHM coincidence peak (see trigger-timing-scan-beam memory)
WRITE_GAP_S = 0.3           # pace config writes per the board-hygiene guardrail

SECTORS = ["A", "B", "C", "D"]
M4_LEMO_FOR_SECTOR = {"A": 0, "B": 1, "C": 3, "D": None}
COINC_SECTORS = [s for s in SECTORS if M4_LEMO_FOR_SECTOR[s] is not None]
SAFE_MODES = {"flash", "flash_random"}


class _Board:
    """Adapter so the existing ``dev.method(args)`` call sites route through a
    mandatory board_session (pacing + breaker + interprocess lock + guaranteed clean
    close). Attribute access returns a callable that forwards to ``session.call``.

    The four boards' sessions are held open for the whole scan, so the interprocess
    lock guarantees NOTHING else (a data run's poll_modules / scan_control, another
    agent) can touch M1-M4 mid-scan — exactly the collision this scan must avoid.
    A breaker trip surfaces as BoardWedgedError from any forwarded call, aborting
    the sweep (see main)."""

    def __init__(self, session):
        self._s = session

    def __getattr__(self, name):
        return lambda *a, **k: self._s.call(name, *a, **k)

    def close(self):
        self._s.__exit__(None, None, None)


def connect(ip):
    """Open a locked, clean-closing board_session and return a call-forwarding
    adapter. Raises BoardBusyError/BoardWedgedError/BoardQuarantinedError if the
    board is held/unreachable/resting — the caller must abort, not force."""
    s = board_session(ip, purpose="systematic threshold scan v3", min_gap_s=WRITE_GAP_S)
    s.__enter__()   # acquires the lock + connects; raises on busy/wedged/quarantined
    return _Board(s)


def _sec(name):
    return getattr(N1081B.Section, f"SEC_{name}")


class _V:
    def __init__(self, v):
        self.value = v


def check_safe_mode():
    """Exact-match safety check (v1/v2's substring check would also match
    'flash_random' as 'flash' even if that combination were ever unsafe --
    this parses the reported mode and checks it against an explicit allow-list)."""
    r = subprocess.run(
        [".venv/bin/python", "n1081b/trigger_mode.py", "status"],
        capture_output=True, text=True, cwd="/home/mx17/PycharmProjects/nTof_x17_DAQ")
    out = r.stdout + r.stderr
    m = re.search(r"looks like mode: (\S+)", out)
    mode = m.group(1) if m else None
    if mode not in SAFE_MODES:
        raise RuntimeError(f"trigger mode is '{mode}' (not in safe set {SAFE_MODES}) "
                            f"-- refusing to touch boards:\n{out}")
    return mode


def daq_alive():
    r = subprocess.run(["tmux", "has-session", "-t", "daq_control"], capture_output=True)
    return r.returncode == 0


class Rig:
    def __init__(self):
        # Hold all four board locks for the whole scan; on any partial-open failure
        # release the ones already acquired so we never leak a lock.
        self.m1 = self.m2 = self.m3 = self.m4 = None
        try:
            self.m1 = connect(M1_IP)
            self.m2 = connect(M2_IP)
            self.m3 = connect(M3_IP)
            self.m4 = connect(M4_IP)
            self.m3_orig = {}
            self.m3_wallin_gd_orig = {}
            for s in SECTORS:
                sec = _sec(s)
                fn = [x["function_name"] for x in self.m3.get_sections_function()["data"]][sec.value]
                assert fn == "and", f"M3 SEC_{s} is not 'and' (got {fn}) -- refusing to proceed"
                self.m3_orig[s] = self.m3.get_function_configuration(sec)["data"]
                self.m3_wallin_gd_orig[s] = self.m3.get_input_channel_configuration(sec, 0)["data"]
            fn = [x["function_name"] for x in self.m4.get_sections_function()["data"]][N1081B.Section.SEC_A.value]
            assert fn == "or", f"M4 SEC_A is not 'or' (got {fn}) -- refusing to proceed"
            self.m4a_orig = self.m4.get_function_configuration(N1081B.Section.SEC_A)["data"]
        except Exception:
            self.close()
            raise

    def close(self):
        for d in (self.m1, self.m2, self.m3, self.m4):
            if d is None:
                continue
            try:
                d.close()          # clean websocket Close + release the interprocess lock
            except Exception:
                pass

    def assert_flash_trigger_intact(self):
        """Mid-scan safety check that uses the ALREADY-HELD M4 session (a subprocess
        trigger_mode.py would deadlock on M4's lock, which we hold). Both safe modes
        (flash, flash_random) keep M4.SEC_D = OR with the gamma-flash line (lemo0)
        enabled; if that is no longer true the trigger changed under us -> abort."""
        fns = {x["section"]: x["function_name"]
               for x in (self.m4.get_sections_function() or {}).get("data", [])}
        d_fn = fns.get(N1081B.Section.SEC_D.value)
        d_fc = (self.m4.get_function_configuration(N1081B.Section.SEC_D) or {}).get("data") or {}
        lemo0 = any(l.get("lemo") == 0 and l.get("enable") for l in d_fc.get("lemo_enables", []))
        if d_fn != "or" or not lemo0:
            raise RuntimeError(f"M4.SEC_D trigger no longer flash-safe (fn={d_fn}, "
                               f"flash-line-enabled={lemo0}) -- aborting scan")

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

    def _set_wallin_delay(self, sector, delay_ns):
        sec = _sec(sector)
        c = self.m3.get_input_channel_configuration(sec, 0)["data"]
        self.m3.set_input_channel_configuration(sec, 0, c["status"], c["enable_gd"], c["gate"], delay_ns, c["invert"])
        rb = self.m3.get_input_channel_configuration(sec, 0)["data"]
        if rb["delay"] != delay_ns:
            raise RuntimeError(f"M3 SEC_{sector} wall-in delay set to {delay_ns} FAILED (read back {rb['delay']})")

    def _restore_wallin_delay(self, sector):
        c = self.m3_wallin_gd_orig[sector]
        sec = _sec(sector)
        self.m3.set_input_channel_configuration(sec, 0, c["status"], c["enable_gd"], c["gate"], c["delay"], c["invert"])
        rb = self.m3.get_input_channel_configuration(sec, 0)["data"]
        if rb["delay"] != c["delay"] or rb["gate"] != c["gate"]:
            raise RuntimeError(f"M3 SEC_{sector} wall-in G&D restore FAILED: {rb} vs {c}")

    def measure_singles(self, dwell):
        """All 4 sectors' wall+scint singles, simultaneously (phase 1)."""
        for s in SECTORS:
            self._m3_to_counter(s)
        try:
            r0 = {s: self.m3.get_function_results(_sec(s))["data"]["counters"] for s in SECTORS}
            t0 = time.time()
            time.sleep(dwell)
            dt = time.time() - t0
            r1 = {s: self.m3.get_function_results(_sec(s))["data"]["counters"] for s in SECTORS}
        finally:
            for s in SECTORS:
                self._m3_restore(s)
        wall_hz, scint_hz = {}, {}
        for s in SECTORS:
            c0 = {c["lemo"]: c["value"] for c in r0[s]}
            c1 = {c["lemo"]: c["value"] for c in r1[s]}
            wall_hz[s] = round((c1.get(0, 0) - c0.get(0, 0)) / dt, 3)
            scint_hz[s] = round((c1.get(1, 0) - c0.get(1, 0)) / dt, 3)
        return wall_hz, scint_hz

    def measure_coinc(self, dwell):
        """The 3 reachable sectors' on-plateau (delay=0) coincidence, simultaneously
        (phase 2). M3 sections must already be in AND (true after measure_singles)."""
        self._m4a_to_counter()
        try:
            r0 = self.m4.get_function_results(N1081B.Section.SEC_A)["data"]["counters"]
            t0 = time.time()
            time.sleep(dwell)
            dt = time.time() - t0
            r1 = self.m4.get_function_results(N1081B.Section.SEC_A)["data"]["counters"]
        finally:
            self._m4a_restore()
        c0 = {c["lemo"]: c["value"] for c in r0}
        c1 = {c["lemo"]: c["value"] for c in r1}
        return {s: round((c1.get(M4_LEMO_FOR_SECTOR[s], 0) - c0.get(M4_LEMO_FOR_SECTOR[s], 0)) / dt, 3)
                for s in COINC_SECTORS}

    def measure_coinc_offplateau(self, sector, dwell):
        """Sector `sector`'s off-plateau (accidental-only) coincidence (phase 3):
        shift its M3 wall-in delay, read M4.SEC_A's tap for it, restore delay.

        Returns ALL reachable sectors' values from this read, not just `sector`'s
        -- the other sectors' M3 delay was untouched, so they're still on-plateau
        during this window. Their readings serve as a beam-drift proxy between
        phase 2 (on-plateau) and this phase, since the two are sequential, not
        simultaneous, and beam intensity can shift between them (caught live on
        hardware: a naive on-minus-off subtraction went negative on one point --
        see module docstring / HANDOFF for the incident)."""
        self._set_wallin_delay(sector, OFF_PLATEAU_DELAY_NS)
        try:
            self._m4a_to_counter()
            try:
                r0 = self.m4.get_function_results(N1081B.Section.SEC_A)["data"]["counters"]
                t0 = time.time()
                time.sleep(dwell)
                dt = time.time() - t0
                r1 = self.m4.get_function_results(N1081B.Section.SEC_A)["data"]["counters"]
            finally:
                self._m4a_restore()
        finally:
            self._restore_wallin_delay(sector)
        c0 = {c["lemo"]: c["value"] for c in r0}
        c1 = {c["lemo"]: c["value"] for c in r1}
        return {s: round((c1.get(M4_LEMO_FOR_SECTOR[s], 0) - c0.get(M4_LEMO_FOR_SECTOR[s], 0)) / dt, 3)
                for s in COINC_SECTORS}


def thresholds_grid(baseline, n):
    """n candidate thresholds spanning ~0.4x-2.2x the baseline magnitude."""
    mults = [0.4, 0.55, 0.7, 0.85, 1.0, 1.3, 1.6, 2.0, 2.4][:n]
    if n < len(mults):
        step = len(mults) / n
        mults = [mults[int(round(i * step))] for i in range(n)]
    sign = 1 if baseline >= 0 else -1
    mag = abs(baseline)
    return sorted({int(round(sign * mag * m)) for m in mults})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dwell", type=float, default=15.0)
    ap.add_argument("--grid", type=int, default=7, help="grid points per dimension for A/B/C's 2D scan")
    ap.add_argument("--points-d", type=int, default=7, help="1D sweep points for sector D")
    ap.add_argument("--safety-check-every", type=int, default=8)
    ap.add_argument("--out", default="n1081b/snapshots/systematic_scan_v3_2026-07-15.json")
    args = ap.parse_args()

    def log(msg):
        print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)

    log(f"safety check: mode must be in {SAFE_MODES}, DAQ must be alive")
    log("current mode: " + check_safe_mode())
    if not daq_alive():
        raise RuntimeError("daq_control tmux session not found")

    try:
        rig = Rig()
    except (BoardBusyError, BoardWedgedError, BoardQuarantinedError) as e:
        log(f"!! cannot start: board unavailable ({e!r}). Another process/agent may "
            f"hold a board, or a board is resting/wedged. Aborting — do NOT force.")
        return 2
    results = {"gate_ns": GATE_NS, "off_plateau_delay_ns": OFF_PLATEAU_DELAY_NS,
               "dwell_s": args.dwell, "grid_n": args.grid, "sectors": {}}
    n_since_check = 0

    def maybe_check_safety():
        nonlocal n_since_check
        n_since_check += 1
        if n_since_check >= args.safety_check_every:
            n_since_check = 0
            # Held-session check (a trigger_mode.py subprocess would deadlock on the
            # M4 lock we hold for the whole scan).
            rig.assert_flash_trigger_intact()
            if not daq_alive():
                raise RuntimeError("daq_control tmux session vanished mid-scan")

    try:
        for sector in COINC_SECTORS:
            log(f"  --- sector {sector}: 2D grid ({args.grid}x{args.grid}) ---")
            wall_base = rig.get_threshold("wall", sector)
            scint_base = rig.get_threshold("scint", sector)
            wall_grid = thresholds_grid(wall_base, args.grid)
            scint_grid = thresholds_grid(scint_base, args.grid)
            grid_points = []
            for wt in wall_grid:
                rig.set_threshold("wall", sector, wt)
                for st in scint_grid:
                    rig.set_threshold("scint", sector, st)
                    maybe_check_safety()

                    wall_hz, scint_hz = rig.measure_singles(args.dwell)
                    coinc_on = rig.measure_coinc(args.dwell)
                    coinc_off_all = rig.measure_coinc_offplateau(sector, args.dwell)
                    coinc_off = coinc_off_all[sector]

                    others = [s for s in SECTORS if s != sector]
                    ref_coinc_sectors = [s for s in others if s in COINC_SECTORS]
                    wall_ref = sum(wall_hz[s] for s in others) / len(others)
                    coinc_ref_on = sum(coinc_on[s] for s in ref_coinc_sectors) / max(1, len(ref_coinc_sectors))
                    coinc_ref_off = sum(coinc_off_all[s] for s in ref_coinc_sectors) / max(1, len(ref_coinc_sectors))
                    # beam may have drifted between the (sequential) on- and off-plateau
                    # reads -- the untouched reference sectors are still on-plateau in
                    # BOTH windows, so their ratio is a live drift correction factor.
                    drift = (coinc_ref_off / coinc_ref_on) if coinc_ref_on else 1.0
                    coinc_off_corrected = round(coinc_off / drift, 3) if drift else coinc_off

                    accidental_formula_hz = round(wall_hz[sector] * scint_hz[sector] * GATE_S, 6)
                    true_hz_measured = round(coinc_on[sector] - coinc_off_corrected, 3)
                    coinc_norm = round(coinc_on[sector] / coinc_ref_on, 4) if coinc_ref_on else None

                    pt = {"wall_mv": wt, "scint_mv": st,
                          "wall_hz": wall_hz[sector], "scint_hz": scint_hz[sector],
                          "wall_ref_hz": round(wall_ref, 2),
                          "coinc_on_hz": coinc_on[sector], "coinc_off_hz_raw": coinc_off,
                          "coinc_off_hz": coinc_off_corrected, "drift_factor": round(drift, 4),
                          "coinc_ref_hz": round(coinc_ref_on, 3) if coinc_ref_on else None,
                          "true_hz_measured": true_hz_measured,
                          "accidental_formula_hz": accidental_formula_hz,
                          "coinc_norm": coinc_norm}
                    grid_points.append(pt)
                    log(f"    W={wt:+5d} S={st:+5d}mV  wall={wall_hz[sector]:7.1f} scint={scint_hz[sector]:7.1f}  "
                        f"on={coinc_on[sector]:6.2f} off={coinc_off_corrected:6.3f}(raw {coinc_off:6.3f} "
                        f"drift {drift:.2f}) true={true_hz_measured:6.2f}  norm={coinc_norm}")
                    results["sectors"].setdefault(sector, {"wall_grid": wall_grid, "scint_grid": scint_grid,
                                                            "points": []})
                    results["sectors"][sector]["points"] = grid_points
                    with open(args.out, "w") as f:
                        json.dump(results, f, indent=2)

            # pick best: smoothed true_hz_measured over the grid (simple: max, grid is
            # dense enough that a single argmax is reasonably robust; log neighborhood)
            best = max(grid_points, key=lambda p: p["true_hz_measured"])
            rig.set_threshold("wall", sector, best["wall_mv"])
            rig.set_threshold("scint", sector, best["scint_mv"])
            results["sectors"][sector]["applied_wall_mv"] = best["wall_mv"]
            results["sectors"][sector]["applied_scint_mv"] = best["scint_mv"]
            log(f"    -> applied SEC_{sector}: wall={best['wall_mv']:+d}mV scint={best['scint_mv']:+d}mV "
                f"(max measured true_hz={best['true_hz_measured']})")
            with open(args.out, "w") as f:
                json.dump(results, f, indent=2)

        # Sector D: no coincidence tap -- independent 1D singles sweeps for reporting only
        log("  --- sector D: 1D singles sweeps (no coincidence tap) ---")
        d_points = {"wall": [], "scint": []}
        for board in ("wall", "scint"):
            base = rig.get_threshold(board, "D")
            grid = thresholds_grid(base, args.points_d)
            for t in grid:
                rig.set_threshold(board, "D", t)
                maybe_check_safety()
                wall_hz, scint_hz = rig.measure_singles(args.dwell)
                d_points[board].append({"threshold_mv": t, "wall_hz": wall_hz["D"], "scint_hz": scint_hz["D"]})
                log(f"    D {board} T={t:+5d}mV  wall={wall_hz['D']:7.1f}Hz scint={scint_hz['D']:7.1f}Hz")
            rig.set_threshold(board, "D", base)  # restore baseline; D's estimate handled separately
        results["sector_d_singles"] = d_points
        with open(args.out, "w") as f:
            json.dump(results, f, indent=2)

        log(f"wrote {args.out}")
    except (BoardBusyError, BoardWedgedError, BoardQuarantinedError) as e:
        # Circuit-breaker / lock abort: STOP the whole sweep. A wedged board cannot
        # be talked to, so an in-flight COUNTER/off-plateau state may not have been
        # restorable — flag it loudly. Do NOT retry; let the board rest.
        log(f"!! ABORTED (board unavailable): {e!r}")
        log("   The sweep is stopped. If a board wedged mid-measurement it may be "
            "left in COUNTER or off-plateau delay; the restore could not run. "
            "Do NOT retry — leave the board alone to rest, then verify its config.")
    finally:
        rig.close()
        # Final mode check via subprocess is safe here: rig.close() released M4's lock.
        try:
            log("final mode: " + check_safe_mode())
        except Exception as e:  # noqa: BLE001 - never mask the primary outcome
            log(f"final mode check skipped: {e!r}")
        log("DAQ alive: " + str(daq_alive()))


if __name__ == "__main__":
    sys.exit(main())
