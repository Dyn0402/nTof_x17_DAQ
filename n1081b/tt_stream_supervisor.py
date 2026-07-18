#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on July 18 2026
Created as nTof_x17_DAQ/n1081b/tt_stream_supervisor.py

@author: Dylan Neff, dylan

Production supervisor for the continuous single-section TT stream on N1081B .244
(the faithful per-trigger record for DREAM event matching). Wraps
`tt_stream_qualify.py` segments with the recovery policy learned 2026-07-18:

- CHAINED SEGMENTS: run `tt_stream_qualify.py --section C --duration <seg>` on
  repeat; each boundary costs ~70 s and yields fresh counter baselines.
- SILENT-START DETECTION: if a segment's stream opens and delivers ZERO edges
  through the first SILENT_START_S while the pre-baseline said the input is live
  (>= MIN_EXPECT_HZ aggregate), that is the stalled-TT-engine state (seen 07-18
  02:47, healed by rest) — NOT rate silence. The segment is stopped cleanly.
- STRIKE POLICY (from the v3 watcher design + the 07-18 incident):
    strike 1  -> one immediate clean retry (fresh session);
    strike 2  -> REST (default 50 min, board untouched) -> ONE tt_probe_v2 on
                 the section -> streams? relaunch : rest longer and repeat;
    PROBE_MAX consecutive failed probes -> Telegram alarm + STOP.
- HARNESS ALARMS (socket error / BoardBusy / login fail — the harness itself
  never reconnects): rest ALARM_REST_S (10 min), then ONE fresh segment; a
  second consecutive alarm -> Telegram alarm + STOP. The rest + single paced
  attempt is a clean new session, not reconnect churn.
- Any successful segment start (edges flowing) resets all strikes.
- TELEGRAM: stall/recovery/stop events via the monitor bot
  (config/monitor_config.json); send failures are non-fatal.
- STATE FILE: publishes config/n1081b_timetag_state.json (mode "supervisor")
  so the GUI Module-5 card shows Logging/rates — run inside the
  `n1081b_timetag_watcher` tmux session (poll_modules then skips .244).

Stop cleanly: SIGTERM/SIGINT/SIGHUP to this process (forwards TERM to the
harness, which closes the stream and restores counters), or touch the
stop-file. NEVER SIGKILL (n1081b/CLAUDE.md rule 3).

    .venv/bin/python n1081b/tt_stream_supervisor.py                 # section C
    .venv/bin/python n1081b/tt_stream_supervisor.py --segment 21600 --section C
"""
import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_DIR = os.path.dirname(_MODULE_DIR)
sys.path.insert(0, _REPO_DIR)

PYTHON = os.path.join(_REPO_DIR, ".venv", "bin", "python")
HARNESS = os.path.join(_MODULE_DIR, "tt_stream_qualify.py")
PROBE = os.path.join(_MODULE_DIR, "tt_probe_v2.py")
MONITOR_CONFIG = os.path.join(_REPO_DIR, "config", "monitor_config.json")
STATE_PATH = os.path.join(_REPO_DIR, "config", "n1081b_timetag_state.json")
STOP_FILE = os.path.join(_REPO_DIR, "config", "tt_stream_supervisor.stop")

SILENT_START_S = 90.0      # zero edges this long after stream-open => stall
MIN_EXPECT_HZ = 5.0        # only classify stall if pre-baseline said input live
REST_S = 50 * 60.0         # rest before the first recovery probe (07-18: healed)
REST_GROW = 1.5            # each further probe cycle rests this much longer
PROBE_MAX = 3              # consecutive failed probe cycles -> alarm + stop
ALARM_REST_S = 10 * 60.0   # rest after a harness alarm before the one retry
SEG_GAP_S = 20.0           # pause between healthy segments
STATE_EVERY_S = 10.0

_RE_PRE = re.compile(r"pre rates .*: \[([0-9., ]+)\]")
_RE_STATUS = re.compile(r"t\+\s*(\d+)s edges=\s*(\d+)")
_RE_OPEN = re.compile(r"stream OPEN")


def log(msg):
    try:
        print(f"{datetime.now().strftime('%H:%M:%S')} [tt_supervisor] {msg}", flush=True)
    except Exception:
        pass


def telegram(text):
    """Best-effort alert through the monitor bot. Never raises."""
    try:
        import requests
        with open(MONITOR_CONFIG) as f:
            cfg = json.load(f)
        token, chat = cfg.get("telegram_token"), cfg.get("telegram_chat_id")
        if not token or not chat:
            return
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat,
                            "text": f"[M5 trigger-timestamp stream]\n{text}"},
                      timeout=10)
    except Exception as e:
        log(f"telegram send failed (non-fatal): {e!r}")


class Supervisor:
    def __init__(self, section, segment_s, out_base=None):
        self.section = section
        self.segment_s = segment_s
        self.stop = False
        self.child = None
        self.seg_index = 0
        self.silent_strikes = 0
        self.probe_fails = 0
        self.alarm_strikes = 0
        self._last_state = 0.0
        self._status = "starting"
        self._edge_rate = 0.0
        for sg in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            try:
                signal.signal(sg, self._on_signal)
            except (ValueError, OSError):
                pass

    def _on_signal(self, *_a):
        self.stop = True
        if self.child and self.child.poll() is None:
            try:
                self.child.send_signal(signal.SIGTERM)  # harness restores counters
            except Exception:
                pass

    def _stop_requested(self):
        return self.stop or os.path.exists(STOP_FILE)

    # ---------------- state file (GUI card) ----------------

    def _publish(self, force=False):
        now = time.time()
        if not force and now - self._last_state < STATE_EVERY_S:
            return
        self._last_state = now
        state = {
            "connected": self._status.startswith("streaming"),
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "ip": "192.168.10.244",
            "mode": "supervisor (continuous single-section)",
            "sections": self.section,
            "tt_sections": self.section,
            "rate_hz": {self.section: round(self._edge_rate, 1)},
            "rate_hz_total": round(self._edge_rate, 1),
            "status_detail": self._status,
            "segment": self.seg_index,
            "silent_strikes": self.silent_strikes,
            "probe_fails": self.probe_fails,
            "alarm": None if self._status != "stopped-alarm" else self._status,
            "last_error": None,
        }
        try:
            tmp = STATE_PATH + ".tmp"
            with open(tmp, "w") as f:
                json.dump(state, f)
            os.replace(tmp, STATE_PATH)
        except Exception as e:
            log(f"state write failed: {e}")

    # ---------------- one segment ----------------

    def run_segment(self):
        """Run one harness segment, watching stdout live.
        Returns 'ok' | 'stall' | 'alarm' | 'stop'."""
        self.seg_index += 1
        label = f"sup_sec{self.section}_{datetime.now().strftime('%m%d_%H%M')}_seg{self.seg_index}"
        cmd = [PYTHON, HARNESS, "--section", self.section,
               "--duration", str(int(self.segment_s)), "--label", label]
        log(f"segment {self.seg_index}: {' '.join(cmd[2:])}")
        self.child = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                      stderr=subprocess.STDOUT, text=True, bufsize=1)
        pre_hz = None
        opened_at = None
        edges = 0
        stalled = False
        for line in self.child.stdout:
            line = line.rstrip("\n")
            print(line, flush=True)
            m = _RE_PRE.search(line)
            if m:
                pre_hz = sum(float(x) for x in m.group(1).split(","))
            if _RE_OPEN.search(line):
                opened_at = time.time()
                self._status = f"streaming seg{self.seg_index} (warming)"
            m = _RE_STATUS.search(line)
            if m:
                t_s, edges = int(m.group(1)), int(m.group(2))
                self._edge_rate = edges / max(t_s, 1)
                if edges > 0:
                    self._status = f"streaming seg{self.seg_index}"
                    self.silent_strikes = 0
                    self.probe_fails = 0
                    self.alarm_strikes = 0
                elif (t_s >= SILENT_START_S and pre_hz is not None
                      and pre_hz >= MIN_EXPECT_HZ and not stalled):
                    stalled = True
                    log(f"SILENT START: 0 edges through t+{t_s}s with input at "
                        f"{pre_hz:.0f} Hz -- stopping segment (stall)")
                    self.child.send_signal(signal.SIGTERM)
            self._publish()
            if self._stop_requested() and self.child.poll() is None:
                self.child.send_signal(signal.SIGTERM)
        rc = self.child.wait()
        self.child = None
        if self._stop_requested():
            return "stop"
        if stalled:
            return "stall"
        if rc != 0:
            return "alarm"
        return "ok"

    # ---------------- recovery probe ----------------

    def probe_streams(self):
        """One gentle tt_probe_v2 on the section. True if any tap streamed."""
        log(f"probe: tt_probe_v2 --sections {self.section}")
        try:
            out = subprocess.run(
                [PYTHON, PROBE, "--sections", self.section, "--gap", "5",
                 "--drain", "6"],
                capture_output=True, text=True, timeout=300).stdout
            print(out, flush=True)
            tags = [int(x) for x in re.findall(r"(?:IMMEDIATE|DELAYED|DOUBLE|CYCLE)=(\d+)", out)]
            return any(t > 0 for t in tags)
        except Exception as e:
            log(f"probe failed to run: {e!r}")
            return False

    def _rest(self, seconds, why):
        log(f"resting {seconds / 60:.0f} min ({why}); board untouched")
        self._status = f"resting ({why})"
        t_end = time.time() + seconds
        while time.time() < t_end and not self._stop_requested():
            self._publish()
            time.sleep(5)

    # ---------------- main loop ----------------

    def run(self):
        if os.path.exists(STOP_FILE):
            os.remove(STOP_FILE)
        log(f"supervisor starting: section {self.section}, segment "
            f"{self.segment_s / 3600:.1f} h, policy strikes/rest/probe per docstring")
        telegram(f"supervisor started (section {self.section}, "
                 f"{self.segment_s / 3600:.0f}h segments)")
        while not self._stop_requested():
            verdict = self.run_segment()
            if verdict == "stop":
                break
            if verdict == "ok":
                self._status = "between segments"
                self._publish(force=True)
                time.sleep(SEG_GAP_S)
                continue
            if verdict == "alarm":
                self.alarm_strikes += 1
                if self.alarm_strikes >= 2:
                    self._status = "stopped-alarm"
                    self._publish(force=True)
                    msg = ("STOPPED: two consecutive harness alarms -- needs a "
                           "human (check board state gently, see chain log)")
                    log(f"ALARM: {msg}")
                    telegram(msg)
                    return 1
                log("harness alarm; resting then ONE fresh attempt")
                telegram("harness alarm; resting 10 min then one fresh attempt")
                self._rest(ALARM_REST_S, "post-alarm")
                continue
            # verdict == 'stall'
            self.silent_strikes += 1
            if self.silent_strikes == 1:
                log("stall strike 1: one immediate clean retry")
                time.sleep(SEG_GAP_S)
                continue
            # strike >= 2: rest + probe cycles
            telegram(f"TT stream stalled (silent start x{self.silent_strikes}); "
                     f"resting {REST_S / 60:.0f} min then probing")
            rest = REST_S
            while not self._stop_requested():
                self._rest(rest, "stall recovery")
                if self._stop_requested():
                    break
                if self.probe_streams():
                    log("probe streams -- resuming segments")
                    telegram("TT engine recovered after rest; resuming")
                    self.probe_fails = 0
                    self.silent_strikes = 0
                    break
                self.probe_fails += 1
                log(f"probe silent ({self.probe_fails}/{PROBE_MAX})")
                if self.probe_fails >= PROBE_MAX:
                    self._status = "stopped-alarm"
                    self._publish(force=True)
                    msg = (f"STOPPED: {PROBE_MAX} rest+probe cycles failed -- "
                           "TT engine not recovering; leave .244 alone and "
                           "investigate at next access")
                    log(f"ALARM: {msg}")
                    telegram(msg)
                    return 1
                rest *= REST_GROW
        self._status = "stopped"
        self._publish(force=True)
        log("supervisor stopped cleanly")
        telegram("supervisor stopped (clean)")
        return 0


def main():
    ap = argparse.ArgumentParser(description="Production TT stream supervisor (.244)")
    ap.add_argument("--section", default="C", choices=list("ABCD"))
    ap.add_argument("--segment", type=float, default=21600.0,
                    help="seconds per stream segment (default 21600 = 6 h)")
    args = ap.parse_args()
    sys.exit(Supervisor(args.section, args.segment).run())


if __name__ == "__main__":
    main()
