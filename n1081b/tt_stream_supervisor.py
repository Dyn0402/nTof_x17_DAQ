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

Housekeeping for unattended running (added 2026-07-29 for the long-term rollout):
- DISK GUARD: a segment is not started if the output filesystem is below
  MIN_FREE_GB; the supervisor rests and retries instead of filling the disk.
- GZIP + PRUNE: each finished segment's edges.csv is gzipped (~4x) between
  segments, and segment dirs older than KEEP_DAYS are removed. Section C runs
  about 400 MB/day raw, ~100 MB/day compressed. The gzip is nice'd and happens
  while the board is idle, so it costs no stream time.
- DURABLE LOG: everything also goes to logs/n1081b_tt_stream.log (size-rotated),
  because a tmux pane's scrollback does not survive the thing we most want to
  diagnose.
- The child's stats.json — not just its stdout — is the authority on what a
  segment did (pre-rates, alarm, whether the restore verified).

Stop cleanly: SIGTERM/SIGINT/SIGHUP to this process (forwards TERM to the
harness, which closes the stream and restores counters), or touch the
stop-file. NEVER SIGKILL (n1081b/CLAUDE.md rule 3).

    .venv/bin/python n1081b/tt_stream_supervisor.py                 # section C
    .venv/bin/python n1081b/tt_stream_supervisor.py --segment 21600 --section C
"""
import argparse
import glob
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_DIR = os.path.dirname(_MODULE_DIR)
sys.path.insert(0, _REPO_DIR)

from common_functions import log_event

PYTHON = os.path.join(_REPO_DIR, ".venv", "bin", "python")
HARNESS = os.path.join(_MODULE_DIR, "tt_stream_qualify.py")
PROBE = os.path.join(_MODULE_DIR, "tt_probe_v2.py")
MONITOR_CONFIG = os.path.join(_REPO_DIR, "config", "monitor_config.json")
STATE_PATH = os.path.join(_REPO_DIR, "config", "n1081b_timetag_state.json")
STOP_FILE = os.path.join(_REPO_DIR, "config", "tt_stream_supervisor.stop")
LOG_PATH = os.path.join(_REPO_DIR, "logs", "n1081b_tt_stream.log")
# SECOND, much smaller log — events only, in the shared convention (see
# common_functions.log_event and docs/PLAN_2026-07-29_watcher_logging.md). This is not
# redundant with LOG_PATH: that one is the full narrative including a t+ progress line
# every 10 s, which at ~1.1 MB/day means its 20 MB x 2 rotation retains about five
# weeks. Segment boundaries, stalls, strikes and alarms are ~10 lines/day, so keeping
# them apart makes them greppable next to every other watcher AND keeps them for years
# instead of rotating out behind the heartbeat.
#
# Named for the tmux session rather than this file, so whichever implementation owns
# .244 writes one continuous M5 history; the source column says which one it was.
EVENT_LOG_PATH = os.path.join(_REPO_DIR, "logs", "n1081b_timetag_watcher.log")
OUT_BASE = os.path.expanduser("~/beam_july/slow_control/n1081b_timetag/stream")

SILENT_START_S = 90.0      # zero edges this long after stream-open => stall
MIN_EXPECT_HZ = 5.0        # only classify stall if pre-baseline said input live
REST_S = 50 * 60.0         # rest before the first recovery probe (07-18: healed)
REST_GROW = 1.5            # each further probe cycle rests this much longer
PROBE_MAX = 3              # consecutive failed probe cycles -> alarm + stop
ALARM_REST_S = 10 * 60.0   # rest after a harness alarm before the one retry
SEG_GAP_S = 20.0           # pause between healthy segments
STATE_EVERY_S = 10.0

MIN_FREE_GB = 25.0         # refuse to start a segment below this much free space
DISK_REST_S = 30 * 60.0    # rest this long when the disk guard trips

# --- reachability gate (added 2026-07-30, see HANDOFF_2026-07-30_tt_reboot_race.md) ---
BOARD_IP = "192.168.10.244"
REACH_WAIT_S = 180.0       # patient wait for the DREAM link at boot, before segment 1
REACH_POLL_S = 3.0         # ICMP only -- never touches the board's websocket server
UNREACH_REST_S = 10 * 60.0 # rest this long when the board stays unreachable, then re-check
KEEP_DAYS = 21             # prune segment dirs older than this
LOG_MAX_BYTES = 20 * 1024 * 1024

_RE_PRE = re.compile(r"pre rates .*: \[([0-9., ]+)\]")
_RE_STATUS = re.compile(r"t\+\s*(\d+)s edges=\s*(\d+)")
_RE_OPEN = re.compile(r"stream OPEN")
# tt_probe_v2 prints e.g. "    IMMEDIATE:    209 tags | first-1s ..." (the label is
# %-9s padded, so DELAYED/DOUBLE/CYCLE carry spaces before the colon). This used to be
# written `LABEL=(\d+)`, which matches nothing the probe actually emits: every recovery
# probe read as SILENT, so a board that HAD healed still burned through PROBE_MAX and
# stopped the chain for good. Fixed 2026-07-29.
_RE_TAP = re.compile(r"(?:IMMEDIATE|DELAYED|DOUBLE|CYCLE)\s*:\s*(\d+)\s+tags")


def log(msg):
    line = f"{datetime.now().strftime('%m-%d %H:%M:%S')} [tt_supervisor] {msg}"
    try:
        print(line, flush=True)
    except Exception:
        pass
    _log_to_file(line)


def event(name, **details):
    """Record one line in the shared event log (EVENT_LOG_PATH).

    Call this ALONGSIDE log(), never instead of it — log() keeps the human narrative
    in the pane and the rotating chain log; this keeps the same fact greppable and
    permanent. log_event never raises, so this is safe anywhere in the loop.
    """
    log_event(EVENT_LOG_PATH, name, 'tt_super', **details)


def _log_to_file(line):
    """Append to the durable log, rotating once past LOG_MAX_BYTES. Never raises:
    losing the log must never take down the stream."""
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > LOG_MAX_BYTES:
            os.replace(LOG_PATH, LOG_PATH + ".1")
        with open(LOG_PATH, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def free_gb(path):
    try:
        st = os.statvfs(path)
        return st.f_bavail * st.f_frsize / 1e9
    except Exception:
        return float("inf")     # can't tell -> don't block on it


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
        event('TELEGRAM_FAILED', error=repr(e))


def gzip_segment(out_dir):
    """Compress a finished segment's edges.csv in place (~4x on TT edges). Runs after
    the stream is closed and the board released, so it competes with nothing on the
    board side — but it is ~30 s of CPU on the host the DAQ runs on, so it goes
    through `nice` in a CHILD process. (Doing it with os.nice() in-process would
    renice the supervisor itself, permanently and cumulatively — nice is per-process
    and never resets.)"""
    src = os.path.join(out_dir, "edges.csv")
    if not os.path.exists(src) or os.path.getsize(src) == 0:
        return
    dst = src + ".gz"
    raw_mb = os.path.getsize(src) / 1e6
    t0 = time.time()
    try:
        # gzip(1) removes the source only on success, so a failure cannot lose data
        rc = subprocess.run(["nice", "-n", "15", "gzip", "-6", src],
                            capture_output=True, text=True, timeout=1800)
        if rc.returncode != 0:
            raise RuntimeError(f"gzip exit {rc.returncode}: {rc.stderr.strip()[:200]}")
        log(f"gzipped {os.path.basename(out_dir)}/edges.csv {raw_mb:.0f} -> "
            f"{os.path.getsize(dst) / 1e6:.0f} MB in {time.time() - t0:.0f}s")
    except Exception as e:
        log(f"gzip failed (non-fatal, raw CSV kept): {e!r}")
        try:
            if os.path.exists(dst) and os.path.exists(src):
                os.remove(dst)      # never leave a truncated .gz beside the raw
        except Exception:
            pass


def prune_segments(out_base, keep_days=KEEP_DAYS):
    """Drop segment dirs older than keep_days. The edges are for offline DREAM
    matching and get copied out; this host only needs a rolling window."""
    cutoff = time.time() - keep_days * 86400
    for d in sorted(glob.glob(os.path.join(out_base, "*"))):
        if not os.path.isdir(d):
            continue
        try:
            if os.path.getmtime(d) < cutoff:
                shutil.rmtree(d)
                log(f"pruned segment older than {keep_days} d: {os.path.basename(d)}")
        except Exception as e:
            log(f"prune failed for {d}: {e!r}")


class Supervisor:
    def __init__(self, section, segment_s, out_base=None):
        self.section = section
        self.segment_s = segment_s
        self.out_base = os.path.expanduser(out_base or OUT_BASE)
        self.stop = False
        self.child = None
        self.seg_index = 0
        self.silent_strikes = 0
        self.probe_fails = 0
        self.alarm_strikes = 0
        self.segments_ok = 0
        self.edges_total = 0
        # Wall-clock start, used to tell a quarantine marker written by our OWN chain
        # from one that predates us (see _own_quarantine).
        self.t_start = time.time()
        self._own_q_waits = 0
        # per-section counter-measured input rates from the latest segment baseline —
        # the only wall (A) / liq (B) rate record left while we own .244
        self.counter_hz = {}
        self.counter_hz_at = None
        self._seg_dir = None
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
            "sections": "".join(sorted(self.counter_hz)) or self.section,
            "tt_sections": self.section,
            # convention from the v3 watcher: rate_hz = counter-measured INPUT rate per
            # section (refreshed each segment baseline, so up to ~6 h old);
            # tt_rate_hz / rate_hz_total = the live TT edge rate on the streamed section
            "rate_hz": {c: round(v, 1) for c, v in self.counter_hz.items()},
            "counter_rates_at": self.counter_hz_at,
            "tt_rate_hz": {self.section: round(self._edge_rate, 1)},
            "rate_hz_total": round(self._edge_rate, 1),
            "status_detail": self._status,
            "segment": self.seg_index,
            "cycles": self.segments_ok,
            "segments_ok": self.segments_ok,
            "edges_total": self.edges_total,
            "total_today_all": self.edges_total,
            "csv_path": self._seg_dir or self.out_base,
            "silent_strikes": self.silent_strikes,
            "probe_fails": self.probe_fails,
            "out_base": self.out_base,
            "free_gb": round(free_gb(self.out_base), 1),
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

    def _read_counter_rates(self, out_dir, key):
        """Pull per-section aggregate counter rates out of the segment's stats.json.
        Best-effort: the card going stale is never worth failing a stream over."""
        try:
            with open(os.path.join(out_dir, "stats.json")) as f:
                rates = json.load(f).get(key) or {}
            if rates:
                self.counter_hz = {c: sum(v) for c, v in rates.items()}
                self.counter_hz_at = datetime.now().isoformat(timespec="seconds")
                self._publish(force=True)
        except Exception:
            pass

    def run_segment(self):
        """Run one harness segment, watching stdout live.
        Returns 'ok' | 'stall' | 'alarm' | 'stop'."""
        self.seg_index += 1
        label = f"sup_sec{self.section}_{datetime.now().strftime('%m%d_%H%M')}_seg{self.seg_index}"
        out_dir = self._seg_dir = os.path.join(self.out_base, label)
        cmd = [PYTHON, HARNESS, "--section", self.section,
               "--duration", str(int(self.segment_s)), "--label", label,
               "--out-base", self.out_base,
               # walls (A) + liq (B) keep a 6-hourly rate record even though
               # poll_modules is skipping .244 for as long as we hold it
               "--baseline-sections", "ABCD",
               "--rates-csv"]
        log(f"segment {self.seg_index}: {' '.join(cmd[2:])}")
        event('SEGMENT_START', seg=self.seg_index, section=self.section,
              label=label, duration_s=int(self.segment_s))
        self.child = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                      stderr=subprocess.STDOUT, text=True, bufsize=1)
        pre_hz = None
        opened_at = None
        edges = 0
        stalled = False
        for line in self.child.stdout:
            line = line.rstrip("\n")
            print(line, flush=True)
            _log_to_file(line)
            m = _RE_PRE.search(line)
            if m:
                pre_hz = sum(float(x) for x in m.group(1).split(","))
                # baselines are complete by now; lift every section's counter rate
                # out of stats.json so the Module-5 card shows the walls again
                self._read_counter_rates(out_dir, "pre_rates_all_hz")
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
                    event('SILENT_START', seg=self.seg_index, silent_through_s=t_s,
                          input_hz=f'{pre_hz:.0f}', action='stopping segment (stall)')
                    self.child.send_signal(signal.SIGTERM)
            self._publish()
            if self._stop_requested() and self.child.poll() is None:
                self.child.send_signal(signal.SIGTERM)
        rc = self.child.wait()
        self.child = None

        # stats.json, not stdout, is the authority on what the segment actually did
        st = {}
        try:
            with open(os.path.join(out_dir, "stats.json")) as f:
                st = json.load(f)
        except Exception as e:
            log(f"could not read segment stats.json ({e!r}); judging on exit code only")
        if st:
            self.edges_total += st.get("edges_total", 0)
            log(f"segment {self.seg_index} done: {st.get('edges_total', 0)} edges in "
                f"{st.get('stream_seconds', 0):.0f}s, finished={st.get('finished')}, "
                f"restored={st.get('restored')}, max gap {st.get('max_packet_gap_s')}s, "
                f"gaps>thresh {len(st.get('gaps') or [])}")
            event('SEGMENT_DONE', seg=self.seg_index,
                  edges=st.get('edges_total', 0),
                  stream_s=f"{st.get('stream_seconds', 0):.0f}",
                  finished=st.get('finished'), restored=st.get('restored'),
                  max_gap_s=st.get('max_packet_gap_s'),
                  gaps_over_thresh=len(st.get('gaps') or []))
            for c, r in (st.get("post_rates_all_hz")
                         or st.get("pre_rates_all_hz") or {}).items():
                log(f"    counters SEC_{c}: {r} Hz (agg {sum(r):.0f})")
            self._read_counter_rates(out_dir, "post_rates_all_hz")
            if st.get("restored") is False:
                # the board is left in wire/time_tag; say so loudly, but keep going —
                # the next segment's ensure_counter is the actual repair
                log("WARNING: segment did not verify the restore to counters")
                # The one board-state fact worth keeping permanently: .244 was left in
                # wire/time_tag rather than counters at this boundary.
                event('RESTORE_UNVERIFIED', seg=self.seg_index,
                      note='.244 left in wire/time_tag; next segment ensure_counter repairs')
                telegram("segment ended without a verified restore to counters; "
                         "next segment's ensure_counter should repair it")

        gzip_segment(out_dir)
        prune_segments(self.out_base)

        if self._stop_requested():
            return "stop"
        if stalled:
            return "stall"
        if rc != 0 or st.get("alarm"):
            return "alarm"
        self.segments_ok += 1
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
            _log_to_file(out)
            tags = [int(n) for n in _RE_TAP.findall(out)]
            log(f"probe taps: {tags or 'no tap lines parsed'}")
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

    def _board_pingable(self):
        """True if .244 answers ICMP (which also proves ARP resolves).

        Deliberately ping and NOT a websocket connect: this runs before every segment,
        so it must be something that cannot itself harm the board. ICMP never touches
        libwebsock, so this is not the "reconnect churn" the board hygiene rules forbid
        (n1081b/CLAUDE.md rule 7) -- no session is opened until the gate passes.
        """
        try:
            return subprocess.run(["ping", "-c", "1", "-W", "1", BOARD_IP],
                                  capture_output=True).returncode == 0
        except Exception:
            return False

    def _reachable_ok(self):
        """Gate every segment on the board actually being reachable.

        This is the fix for the 2026-07-30 cold-boot false wedge: the supervisor
        autostarted ~14 s after a host reboot, before the DREAM link was up, and its
        first connect got OSError(113) -> "wedge" -> 6 h quarantine -> permanent stop,
        on a healthy board.

        Note the two things measured on 07-30 that rule out the simpler gates: the
        `network-online.target` drop-in was ALREADY in place and did not help (nm-online
        was satisfied by the CERN NIC while enp4s0 still had no carrier), and enp4s0's
        IP was up 2.8 s BEFORE the failing connect -- the connect failed on unresolved
        ARP because the switch port was not forwarding yet. So neither "network-online"
        nor "link is up" is sufficient; only end-to-end reachability is.

        Shaped like _disk_ok(): rest and re-check rather than alarm, so a link problem
        (cf. `.242`, link-dead since 07-28) never burns alarm strikes and never stops
        the chain for good. Returns False if no segment should start yet."""
        if self._board_pingable():
            return True
        # Patient short wait first -- at boot the link comes up within seconds.
        log(f"REACH GATE: {BOARD_IP} not answering; waiting up to "
            f"{REACH_WAIT_S:.0f}s for the link (no board session opened)")
        self._status = "waiting for board link"
        self._publish(force=True)
        deadline = time.time() + REACH_WAIT_S
        while time.time() < deadline and not self._stop_requested():
            time.sleep(REACH_POLL_S)
            if self._board_pingable():
                waited = REACH_WAIT_S - (deadline - time.time())
                log(f"REACH GATE: {BOARD_IP} reachable after {waited:.0f}s — proceeding")
                event('REACH_OK', ip=BOARD_IP, waited_s=f'{waited:.0f}')
                return True
        if self._stop_requested():
            return False
        log(f"REACH GATE: {BOARD_IP} still unreachable after {REACH_WAIT_S:.0f}s — "
            f"resting {UNREACH_REST_S / 60:.0f} min (NOT a wedge; check cable/switch "
            f"port and `ip neigh show {BOARD_IP}`)")
        event('REACH_FAIL', ip=BOARD_IP, waited_s=f'{REACH_WAIT_S:.0f}',
              action=f'resting {UNREACH_REST_S / 60:.0f} min, no segment started',
              note='link/routing failure, not a wedge — resting will not fix a dead link')
        telegram(f"M5 ({BOARD_IP}) is not reachable — TT streaming paused. This is a "
                 f"LINK problem, not a board wedge: check the cable/switch port. "
                 f"Retrying every {UNREACH_REST_S / 60:.0f} min.")
        self._rest(UNREACH_REST_S, "board unreachable")
        return False

    def _own_quarantine(self):
        """The quarantine record on .244 IF this supervisor's own chain wrote it.

        Backstop for handoff fix (b). A quarantine written by our own previous segment
        is NOT independent evidence of a second fault: the one allowed retry cannot
        possibly succeed inside a 6 h window, so counting it as harness alarm #2
        guaranteed a permanent stop 10 min after any single transient error. `since`
        being after our start time is what makes it ours."""
        try:
            from n1081b.n1081b_session import quarantine_status
            rec = quarantine_status(BOARD_IP)
        except Exception:
            return None
        if rec and rec.get("since", 0) >= self.t_start:
            return rec
        return None

    def _disk_ok(self):
        """Refuse to open a stream we have nowhere to put. Rest and re-check rather
        than stopping: the disk is usually freed by the backup/space watchers."""
        gb = free_gb(self.out_base)
        if gb >= MIN_FREE_GB:
            return True
        log(f"DISK GUARD: {gb:.1f} GB free at {self.out_base} "
            f"(floor {MIN_FREE_GB:.0f} GB) — not starting a segment")
        event('DISK_GUARD', free_gb=f'{gb:.1f}', floor_gb=f'{MIN_FREE_GB:.0f}',
              action=f'resting {DISK_REST_S / 60:.0f} min, no segment started')
        telegram(f"disk guard: only {gb:.1f} GB free; TT streaming paused "
                 f"(floor {MIN_FREE_GB:.0f} GB). Free space or lower MIN_FREE_GB.")
        prune_segments(self.out_base)
        self._rest(DISK_REST_S, "disk guard")
        return False

    def run(self):
        if os.path.exists(STOP_FILE):
            os.remove(STOP_FILE)
        os.makedirs(self.out_base, exist_ok=True)
        log(f"supervisor starting: section {self.section}, segment "
            f"{self.segment_s / 3600:.1f} h, out {self.out_base} "
            f"({free_gb(self.out_base):.0f} GB free), "
            f"policy strikes/rest/probe per docstring")
        event('START', section=self.section,
              segment_h=f'{self.segment_s / 3600:.1f}', out_base=self.out_base,
              free_gb=f'{free_gb(self.out_base):.0f}')
        telegram(f"supervisor started (section {self.section}, "
                 f"{self.segment_s / 3600:.0f}h segments)")
        prune_segments(self.out_base)
        while not self._stop_requested():
            if not self._disk_ok():
                continue
            # Reachability BEFORE the disk-free check would be equally valid; it goes
            # after so the cheap local check runs first. Either way: no board session is
            # opened until the board answers ICMP.
            if not self._reachable_ok():
                continue
            verdict = self.run_segment()
            if verdict == "stop":
                break
            if verdict == "ok":
                self._status = "between segments"
                self._publish(force=True)
                time.sleep(SEG_GAP_S)
                continue
            if verdict == "alarm":
                # Handoff fix (b): being blocked by a quarantine THIS chain wrote is not
                # independent evidence of a second fault. Wait the window out instead of
                # spending it on a retry that cannot succeed and then stopping for good.
                # Capped so a genuinely wedged board still ends in a human-facing alarm.
                own_q = self._own_quarantine()
                if own_q and self._own_q_waits < 2:
                    self._own_q_waits += 1
                    left = max(0.0, own_q["until"] - time.time())
                    log(f"alarm was OUR OWN quarantine marker ({left / 60:.0f} min left) "
                        f"— not counting it as an independent alarm; waiting it out")
                    event('OWN_QUARANTINE_WAIT', wait_num=self._own_q_waits,
                          minutes_left=f'{left / 60:.0f}',
                          note='not counted toward the two-consecutive-alarms stop')
                    telegram(f"TT chain is inside a quarantine it wrote itself "
                             f"({left / 60:.0f} min left); waiting it out rather than "
                             f"stopping. If .244 pings, this is the reboot race — see "
                             f"HANDOFF_2026-07-30_tt_reboot_race.md.")
                    self._rest(left + 30.0, "own quarantine")
                    continue
                self.alarm_strikes += 1
                if self.alarm_strikes >= 2:
                    self._status = "stopped-alarm"
                    self._publish(force=True)
                    msg = ("STOPPED: two consecutive harness alarms -- needs a "
                           "human (check board state gently, see chain log)")
                    log(f"ALARM: {msg}")
                    event('ALARM_STOP', reason='two consecutive harness alarms',
                          segments_ok=self.segments_ok, detail=msg)
                    telegram(msg)
                    return 1
                log("harness alarm; resting then ONE fresh attempt")
                event('HARNESS_ALARM', strike=self.alarm_strikes,
                      action=f'resting {ALARM_REST_S / 60:.0f} min then ONE fresh attempt')
                telegram("harness alarm; resting 10 min then one fresh attempt")
                self._rest(ALARM_REST_S, "post-alarm")
                continue
            # verdict == 'stall'
            self.silent_strikes += 1
            if self.silent_strikes == 1:
                log("stall strike 1: one immediate clean retry")
                event('STALL_STRIKE', strike=1, action='one immediate clean retry')
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
                    event('PROBE_RECOVERED', after_rest_min=f'{rest / 60:.0f}',
                          action='resuming segments')
                    telegram("TT engine recovered after rest; resuming")
                    self.probe_fails = 0
                    self.silent_strikes = 0
                    break
                self.probe_fails += 1
                log(f"probe silent ({self.probe_fails}/{PROBE_MAX})")
                event('PROBE_SILENT', fails=f'{self.probe_fails}/{PROBE_MAX}',
                      next_rest_min=f'{rest * REST_GROW / 60:.0f}')
                if self.probe_fails >= PROBE_MAX:
                    self._status = "stopped-alarm"
                    self._publish(force=True)
                    msg = (f"STOPPED: {PROBE_MAX} rest+probe cycles failed -- "
                           "TT engine not recovering; leave .244 alone and "
                           "investigate at next access")
                    log(f"ALARM: {msg}")
                    event('ALARM_STOP', reason=f'{PROBE_MAX} rest+probe cycles failed',
                          segments_ok=self.segments_ok, detail=msg)
                    telegram(msg)
                    return 1
                rest *= REST_GROW
        self._status = "stopped"
        self._publish(force=True)
        log(f"supervisor stopped cleanly after {self.segments_ok} good segment(s), "
            f"{self.edges_total} edges")
        event('STOP', segments_ok=self.segments_ok, edges_total=self.edges_total)
        telegram("supervisor stopped (clean)")
        return 0


def main():
    ap = argparse.ArgumentParser(description="Production TT stream supervisor (.244)")
    ap.add_argument("--section", default="C", choices=list("ABCD"),
                    help="C = the four sector coincidences (default: richest record "
                         "and the lowest rate, so the most margin under the per-channel "
                         "TT ceiling); D = Singles/Doubles/master trigger")
    ap.add_argument("--segment", type=float, default=21600.0,
                    help="seconds per stream segment (default 21600 = 6 h, the "
                         "harness cap and the length proven clean on 2026-07-18)")
    ap.add_argument("--out-base", default=OUT_BASE,
                    help=f"parent dir for segment output (default {OUT_BASE})")
    args = ap.parse_args()
    sys.exit(Supervisor(args.section, args.segment, out_base=args.out_base).run())


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        # Durable second copy only — re-raised so the pane still shows the live
        # traceback and the process still exits non-zero. Worth recording: if the
        # supervisor dies, the M5 record stops with it.
        import traceback as _tb
        event('CRASH', error=repr(e), traceback=_tb.format_exc())
        raise
