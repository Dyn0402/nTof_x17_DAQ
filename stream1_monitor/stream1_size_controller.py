#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on July 22 2026
Created in PyCharm
Created as nTof_x17_DAQ/stream1_monitor/stream1_size_controller.py

@author: Dylan Neff, dylan

n_TOF stream1 watcher — online detection of SiPM-wall dropouts, in two layers.

Live version of ~/beam_july/analysis/sipm_wall_filesize/ (FINDINGS.md).

**Size layer** (every poll, ~20 ms). Each stream1 file is a ~70 s chunk of the n_TOF
stream; when a wall drops out its channels stop contributing zero-suppressed hits and
the file shrinks. The 2026-07-21/22 dropouts were cleanly bimodal — ~1.9 GiB full vs
~0.92 GiB reduced. Cheap and continuous, but it only says "volume dropped".

**Waveform layer** (sampled, ~2 s). Decodes the first event of a file and measures the
gamma-flash amplitude per channel. The flash is in every proton pulse, so it is an
absolute reference: during the dropout the walls fell from ~34 000 counts to ~600
(0.02x) while baseline and noise RMS were unchanged — a gain collapse, and one that
names the affected detectors. Graded against a frozen nominal, never a rolling
average, so a multi-hour dropout cannot become the new "normal".

The watcher appends both layers to per-day CSVs and publishes a summary to
STREAM1_STATE_PATH for the Flask app (/stream1/status) and the Telegram monitor.

Size classification
-------------------
Files are measured PER PROTON PULSE (a file holds one event per pulse, so this is what
takes the beam out; see BeamIndex) and graded against a FROZEN benchmark, the same way
the waveform layer works. A trailing average was the obvious first choice and is the
wrong one: it absorbs a long dropout and stops calling it a dropout — on 2026-07-22
11:50 the size layer read "good" straight through a real one while the walls were at
30 % of nominal.

The benchmark is auto-seeded once from a healthy stretch (only when the waveform layer
agrees the detectors are fine) and thereafter changes only on request, from the GUI.
The trailing level is still computed and published as `suggestion`, because absolute
size is NOT comparable across run configurations (the offline study saw full levels of
~1.9 GiB and ~2.4 GiB in adjacent runs, and 2026-07-22 saw the per-pulse level double
at a config change): when the suggestion drifts away from the frozen benchmark and the
waveform layer says the detectors are healthy, that is a run-config change and the
operator should re-freeze.

Needs a valid Kerberos ticket for EOS (the same keytab-seeded one the backup watcher
uses). Read-only: this watcher owns no hardware and commands nothing; the only thing
it can be told to do is re-freeze its nominal (STREAM1_COMMAND_PATH).
"""

import os
import csv
import glob
import json
import re
import signal
import struct
import subprocess
import time
from datetime import datetime, timedelta, timezone

# Shared paths for the watcher/Flask split (resolved relative to the repo so both
# agree). The stream1_watcher process writes; Flask only reads.
_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_DIR = os.path.dirname(_MODULE_DIR)
# Per-day CSVs live with the other slow-control logs on the data disk, not in the repo.
STREAM1_LOG_DIR = os.path.expanduser("~/beam_july/slow_control/stream1_filesize")
STREAM1_STATE_PATH = os.path.join(_REPO_DIR, "config", "stream1_filesize_state.json")
STREAM1_CONFIG_PATH = os.path.join(_REPO_DIR, "config", "stream1_filesize_config.json")
# Per-detector reference the waveform layer grades against. Learned once from a
# healthy file and then frozen (see adopt_nominal) — NOT a rolling average, so a
# multi-hour dropout can never become the new "normal".
STREAM1_NOMINAL_PATH = os.path.join(_REPO_DIR, "config", "stream1_waveform_nominal.json")
# Frozen size reference (bytes per proton pulse), same idea as the waveform nominal:
# a trailing average silently absorbs a long dropout and stops flagging it, so the
# benchmark is fixed once from a healthy stretch and only changes when asked.
STREAM1_SIZE_NOMINAL_PATH = os.path.join(_REPO_DIR, "config", "stream1_size_nominal.json")
# Flask drops commands here (currently only {"cmd": "set_nominal"}) and the watcher
# applies them on its next poll — the watcher stays the sole decoder, as with the gas
# watcher's command file.
STREAM1_COMMAND_PATH = os.path.join(_REPO_DIR, "config", "stream1_command.json")

# EOS source. Sizes/mtimes come from `xrdfs <URL> ls -l <EOS_BASE>/<run>/stream1`.
EOS_URL = "root://eospublic.cern.ch"
EOS_BASE = "/eos/experiment/ntof/DAQ/2026/EAR2/X17_measurement"

GB = 1024 ** 3

POLL_S = 120.0            # EOS listing cadence (files arrive every ~70 s)
N_RUNS_WATCH = 2          # newest N run dirs listed each poll (covers a run boundary)
BASELINE_WINDOW = 400     # trailing files defining the "full" level (~8 h at 70 s)
BASELINE_TOP_FRAC = 0.25  # baseline = median of the largest this-fraction of them
MIN_BASELINE_FILES = 20   # below this the baseline is not trusted -> state "unknown"
# Three-level grading of each file, against two cuts derived from the trailing window:
#
#   bad          size < REDUCED_RATIO * baseline           (wall-dropout level)
#   questionable size < questionable cut, but not bad      (low, no clean drop)
#   good         everything else
#
# The 07-21/22 episodes sat at ~0.48 of full, so the bad cut is generous; the
# questionable band catches partial/marginal drops that a two-level split would call
# healthy. Both cuts are fractions of the baseline, which is the TYPICAL full-file
# size (see _baseline) — not the top of the distribution, or ordinary ±10 % scatter
# would land a large share of healthy files in the questionable band.
# Cuts are looser than the raw-size era (0.90/0.75) because the graded quantity is now
# size per proton pulse: counting pulses in an approximate writing window adds ~±1
# pulse (±8 %) on top of the natural file-to-file scatter, and at 0.90 that tail alone
# painted a third of perfectly healthy files questionable. Measured on 2026-07-22:
# healthy files span 0.83-1.08 of baseline, the real 11:50 wall dropout sat at
# 0.63-0.71, so 0.85/0.70 separates them with nothing spurious in "bad".
REDUCED_RATIO = 0.70       # size/pulse < ratio * baseline  ->  file graded BAD
QUESTIONABLE_RATIO = 0.85  # size/pulse < ratio * baseline  ->  at best QUESTIONABLE
RECENT_FILES = 20         # "recent" window for the reduced-fraction summary
ALERT_CONSEC = 5          # consecutive bad files before the state reads "reduced"
STALE_MIN = 20.0          # no new file for this long -> state "no new files"

GRADES = ("good", "questionable", "bad")
# Not a grade on the good..bad scale: "there was no beam, so there is nothing to
# judge". These files are excluded from the baseline, from dropout episodes and from
# alerting — see BeamIndex.
NO_BEAM = "no_beam"

# Beam context (written by beam_watcher.py; PULSE_THRESHOLD_E10 matches its own cut).
BEAM_LOG_DIR = os.path.expanduser("~/beam_july/slow_control/beam_intensity")
BEAM_STATE_PATH = os.path.join(_REPO_DIR, "config", "beam_state.json")
PULSE_THRESHOLD_E10 = 50.0
# A file needs at least this many proton pulses in its own writing window for its size
# to mean anything. Files arrive every ~70-85 s and beam runs at ~1 pulse / 2.4 s, so a
# healthy file sees ~20-35 pulses; 3 is comfortably "the beam was there".
MIN_PULSES_TO_GRADE = 3
FILE_SPAN_DEFAULT_S = 75.0     # assumed writing window when the previous close is unknown
FILE_SPAN_CLAMP_S = (20.0, 300.0)

# ---------------------------------------------------------------------------
# Waveform layer
# ---------------------------------------------------------------------------
# File size only says "something stopped contributing hits". Decoding one event tells
# you WHICH detector and by how much: the gamma flash is in every proton pulse, so its
# amplitude is an absolute per-channel reference (2026-07-22 dropout: walls fell from
# ~34 000 counts to ~600, i.e. 0.02x, while baseline and noise RMS were unchanged —
# a gain collapse, not a dead digitiser). See docs/NTOF_RAW_FORMAT.md.
#
# Cost: `xrdfs cat` of the file head at ~90 MB/s, stopping at the second event header.
# One event across all 51 channels is ~150-200 MB, decoded in ~0.2 s.
WAVEFORM_ENABLED = True
# Read cap for one event. The decode BREAKS at the second EVEH, so in the healthy case
# exactly one event crosses the network however high this sits — a generous cap costs
# nothing. A tight one is not free: it drops the tail of the event and with it the last
# channels, which then look absent. The cap exists only to bound a malformed file where
# the second EVEH never arrives at all.
#
# 2026-07-27: the original 260 MB was chosen when an event was "~150-200 MB". ZS
# occupancy grew (zs_blocks 1.24x the 07-22 nominal) until a typical event sat just
# under the cap and the upper tail crossed it. Run 224583_34 was 268.8 MB, so the LAST
# bank in the event — PSSD ch 1 — never arrived, and _zeroed_channels reported it
# "missing": an EMERGENCY "PSSD 1/2 missing" alert for a channel that re-decoding at a
# larger cap showed to be perfectly healthy (3139 ZS blocks). Twice in six days, both
# times PSSD, because PSSD ch 1 is the last bank in the event and so is always the
# first casualty. Three separate defences below exist because of that, and each covers
# a different way this can bite:
#   * the cap starts well clear of a real event and GROWS itself as events grow, so
#     rising occupancy cannot creep up on it again (_update_waveform_cap);
#   * `missing` is only ever reported from a COMPLETE read, because a read that
#     stopped early cannot tell "absent" from "not reached yet" (_zeroed_channels);
#   * a nominal is never adopted from an incomplete read, or a short sample would
#     freeze the truncated channel count as the reference and permanently blind the
#     check it is supposed to arm (adopt_nominal).
WAVEFORM_MAX_BYTES = 600_000_000
# Ceiling for the self-growing cap: past this, something is wrong with the file rather
# than merely large, and reading further is throwing bandwidth at a corrupt stream.
WAVEFORM_MAX_BYTES_CEILING = 2_000_000_000
WAVEFORM_CAP_HEADROOM = 2.0       # keep the cap >= this x the largest complete event
WAVEFORM_MIN_INTERVAL_S = 300.0   # at most one sampled file per this interval
# Consecutive incomplete reads before the missing-channel check counts as disarmed and
# says so out loud. One is a large event or an EOS hiccup and self-corrects on the next
# sample (the cap grows); a run of them means the layer is half blind and nobody would
# otherwise know, because the symptom of this failure is SILENCE.
WAVEFORM_INCOMPLETE_CONSEC = 3
WAVEFORM_BASELINE_SAMPLES = 2000  # leading samples of the first block used for base/RMS

# Per-detector grading of the flash amplitude against the stored nominal.
FLASH_BAD_RATIO = 0.50
FLASH_QUESTIONABLE_RATIO = 0.85
# A channel whose baseline moved by more than this (ADC counts), or whose noise RMS
# changed by more than this factor either way, is flagged separately: that is a
# digitiser/DC-path problem rather than the gain collapse the flash ratio catches.
BASELINE_SHIFT_COUNTS = 500.0
RMS_FACTOR = 3.0
# Refuse to adopt a nominal whose walls are already dead (they sit at ~34 000 alive,
# ~600 dead), so a re-baseline during a dropout cannot silently bless the fault.
NOMINAL_MIN_WALL_FLASH = 10_000.0
NOMINAL_WALL_PREFIX = "WAL"

# Beam witnesses INSIDE the event. The gamma flash only exists if protons hit the
# target, so with no beam every detector reads ~0 and the layer would report a
# facility-wide "failure" (2026-07-22 14:19-14:29: PKUP 0.3 %, PSS 0.2 %, LIQ 0.2 %,
# walls 15 %). PKUP is the proton pickup — no PKUP flash means no proton pulse, full
# stop — and PSS/LIQ corroborate. A real wall dropout looks nothing like this: there
# the witnesses stay at ~100 % while only the walls fall (224528_10: PKUP 99.5 %,
# PSSA 99.9 %, walls 1.7-2.1 %). This check is self-contained, so it still works when
# the beam watcher is down.
BEAM_WITNESSES = ("PKUP", "PSSA", "PSSB", "PSSC", "PSSD", "LIQA", "LIQB", "LIQC", "LIQD")
WITNESS_BEAM_RATIO = 0.50   # witnesses below this fraction of nominal = no proton pulse
# Detectors that are measured and displayed but never GRADED, so they can never raise
# an alert or move the overall verdict. Keyed by detector-name prefix -> the reason,
# which is shown both in the per-detector notes and on the GUI so it is obvious that a
# blank grade is deliberate rather than a missing nominal.
#
#   PKUP  flash amplitude is PROPORTIONAL to beam intensity rather than railing at the
#         digitiser's range, so a fixed nominal says nothing about its health: it read
#         38 637 in run 224533 and 14 411 in run 224534 — 0.37x — while WAL/PSS/LIQ sat
#         at ~34 000 in both. Still used as a beam witness; grading it alerted on a
#         perfectly healthy pickup within minutes of going live.
#   RMP   the RAMP channels. Excluded on request (2026-07-22) — they are not part of
#         the detector health question this layer answers, and are reported for
#         reference only. Remove the prefix here to bring them back under grading.
#   SILI  the silicon monitor. Excluded on request (2026-07-25) for the same reason as
#         RMP: not part of the detector health question this layer answers. Its numbers
#         are still measured and shown; they just cannot move the verdict or alert.
UNGRADED_PREFIX_REASONS = {
    "PKUP": "beam-intensity proportional — witness only, not graded",
    "RMP": "RAMP channel — reported only, ignored for alerts",
    "SILI": "silicon monitor — reported only, ignored for alerts",
}
UNGRADED_PREFIXES = tuple(UNGRADED_PREFIX_REASONS)


def ungraded_reason(det):
    """Why `det` is not graded, or None if it is. Prefix match so RMPA/RMPC/... are all
    covered by the single "RMP" entry above."""
    for prefix, reason in UNGRADED_PREFIX_REASONS.items():
        if det.startswith(prefix):
            return reason
    return None

# ---------------------------------------------------------------------------
# Zeroed (flatlined) channels — the most severe waveform fault
# ---------------------------------------------------------------------------
# Distinct from BOTH other failure modes this file already grades, and the reason it
# gets its own detection path and its own (highest) alert severity:
#
#   gain collapse  flash falls to ~2 % of nominal, baseline and RMS UNCHANGED. The
#                  detector is alive and still sees noise; it just lost gain.
#                  (2026-07-22 walls: flash 34 000 -> ~600, RMS 19-20 throughout.)
#   no beam        flash falls to ~80 counts on EVERY detector at once, RMS again
#                  UNCHANGED. Nothing is wrong; there were no protons.
#                  (2026-07-22 gaps: LIQ/PSS flash ~74-93, RMS 17.9-21.3.)
#   ZEROED         the samples themselves are flat: no noise, no baseline wander,
#                  nothing. That is not a detector that sees too little, it is a
#                  channel that is not producing data at all — dead digitiser, pulled
#                  cable, unpowered front-end. Nothing downstream of it is trustworthy.
#
# So the discriminator is the NOISE, not the flash: RMS is what stays put in the two
# benign modes and is the one thing that cannot survive a dead channel. It is also
# beam-independent, which is what lets this fire during a beam gap — a channel that
# flatlines while the beam is off is still broken, and waiting for beam to find out
# would hide it for the length of the gap.
#
# Threshold from measured data (2026-07-22, 51 samples x 16 detectors): the quietest
# live channel anywhere in the day sat at RMS 17.55 (LIQA), walls at 18.93 and up, and
# NOTHING was ever observed below that — beam on or off, healthy or mid-dropout. A
# floor of 1.0 counts is ~17x below the quietest real channel and comfortably above
# digitiser dither, so it separates "flat" from "quiet" with no room for argument.
ZERO_RMS_COUNTS = 1.0       # per-channel RMS at/below this = flatlined
ZERO_FLASH_COUNTS = 5.0     # ...and essentially no excursion either
# Walls / liquids / plastics. These are the physics detectors: the walls carry the
# tracking, and PSS/LIQ are also the beam witnesses the whole no-beam determination
# rests on, so a zeroed witness poisons the grading of everything else too. PKUP,
# SILI and RMP are deliberately excluded — they are not what was asked for, and PKUP
# is beam-proportional (see UNGRADED_DETECTORS).
ZEROED_ALERT_PREFIXES = ("WAL", "LIQ", "PSS")


def describe_zeroed(zeroed):
    """Compact 'WALA ch 3,5 flat; LIQB 1/1 missing' summary of _zeroed_channels().

    Module level and shared by the watcher log line and the Telegram rule, so the
    thing an operator reads on the phone matches the thing in the pane exactly.
    """
    by_det = {}
    for z in zeroed:
        by_det.setdefault(z["det"], []).append(z)
    out = []
    for det in sorted(by_det):
        flat = sorted(z["chan"] for z in by_det[det] if z["kind"] == "flatlined")
        gone = [z for z in by_det[det] if z["kind"] == "missing"]
        bits = []
        if flat:
            bits.append("ch " + ",".join(str(c) for c in flat) + " flat")
        for g in gone:
            bits.append(f"{g['n_missing']}/{g['n_expected']} missing")
        out.append(f"{det} {' + '.join(bits)}")
    return "; ".join(out)


# `xrdfs ls -l` line:
#   -rw------- ntofdaq za 1969044020 2026-07-21 10:28:58 /eos/.../run224524_0_s1.raw.finished
_LINE_RE = re.compile(
    r'^\S+\s+\S+\s+\S+\s+(\d+)\s+(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})\s+(\S+)$')
_NAME_RE = re.compile(r'run(\d+)_(\d+)_s1\.raw')


class BeamIndex:
    """Proton pulses on target, read from the beam watcher's per-day CSVs.

    Both layers need this. Without beam there is no gamma flash and almost no
    triggers, so a beam gap looks exactly like a detector dying: on 2026-07-22 the
    09:53-11:30 and 14:19-14:29 beam-off gaps produced 0.1-0.4 GiB files and events
    whose flash was gone on EVERY detector. Files are graded per proton pulse, and
    files with no beam at all are not graded (or alerted on) at all.

    Reads incrementally (byte offset per day file) so a poll costs nothing once the
    day's history is loaded. Falls back cleanly to "unavailable" if the beam watcher
    is not running — the size layer then grades raw size, as it did before.
    """

    def __init__(self, log_dir=BEAM_LOG_DIR, threshold_e10=PULSE_THRESHOLD_E10):
        self.log_dir = log_dir
        self.threshold_e10 = threshold_e10
        self._offsets = {}          # path -> bytes consumed
        self._pulses = []           # sorted (unix_ts, intensity_e10) of REAL pulses
        self.last_error = None

    def refresh(self):
        """Pick up whatever the beam watcher has appended since the last call."""
        try:
            files = sorted(glob.glob(os.path.join(self.log_dir, "beam_intensity_*.csv")))[-2:]
            for path in files:
                off = self._offsets.get(path, 0)
                size = os.path.getsize(path)
                if size <= off:
                    continue
                with open(path) as f:
                    if off:
                        f.seek(off)
                    else:
                        f.readline()          # header
                    for line in f:
                        parts = line.rstrip("\n").split(",")
                        if len(parts) < 3:
                            continue
                        try:
                            ts, val = float(parts[1]), float(parts[2])
                        except ValueError:
                            continue
                        if val >= self.threshold_e10:
                            self._pulses.append((ts, val))
                    self._offsets[path] = f.tell()
            self._pulses.sort()
            # A day and a half is far more than any grading window needs.
            cutoff = time.time() - 36 * 3600
            self._pulses = [p for p in self._pulses if p[0] >= cutoff]
            self.last_error = None
        except Exception as e:
            self.last_error = str(e)
        return self

    @property
    def available(self):
        return bool(self._pulses)

    def window(self, end_dt, span_s):
        """(n_pulses, protons_e10) delivered in the span_s ending at end_dt."""
        end = end_dt.timestamp()
        start = end - span_s
        sel = [v for ts, v in self._pulses if start < ts <= end]
        return len(sel), round(sum(sel), 1)

    def seconds_since_pulse(self):
        return round(time.time() - self._pulses[-1][0], 1) if self._pulses else None


def _eos_time(day, tod):
    """EOS mtime -> naive LOCAL datetime.

    `xrdfs ls -l` prints mtimes in UTC. Reading them as local time made every file
    look two hours old under CEST, which is enough to park the monitor permanently in
    its "no new files" state during normal running. (The same offset explains the
    apparent write gap at the end of the offline study's window.)"""
    utc = datetime.strptime(f"{day} {tod}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return utc.astimezone().replace(tzinfo=None)


def load_config(path=STREAM1_CONFIG_PATH):
    """Optional tuning overrides (poll_s, reduced_ratio, ...). Missing file = defaults."""
    defaults = {
        "poll_s": POLL_S,
        "n_runs_watch": N_RUNS_WATCH,
        "baseline_window": BASELINE_WINDOW,
        "reduced_ratio": REDUCED_RATIO,
        "questionable_ratio": QUESTIONABLE_RATIO,
        "alert_consec": ALERT_CONSEC,
        "stale_min": STALE_MIN,
        "eos_base": EOS_BASE,
        "waveform_enabled": WAVEFORM_ENABLED,
        "waveform_max_bytes": WAVEFORM_MAX_BYTES,
        "waveform_min_interval_s": WAVEFORM_MIN_INTERVAL_S,
    }
    try:
        with open(path) as f:
            defaults.update(json.load(f) or {})
    except Exception:
        pass
    return defaults


class Stream1SizeMonitor:
    """Polls EOS stream1 listings, logs file sizes, flags reduced-size episodes."""

    # `reduced` (0/1) is kept alongside `grade` so rows stay readable by anything
    # written against the two-level version; reduced == (grade == "bad").
    CSV_FIELDS = ["timestamp", "unix_ts", "run", "seq", "size_bytes", "size_gib",
                  "pulses", "protons_e10", "baseline_gib", "ratio",
                  "quest_cut_gib", "bad_cut_gib", "grade", "reduced"]

    def __init__(self, config_path=STREAM1_CONFIG_PATH, state_path=STREAM1_STATE_PATH,
                 log_dir=STREAM1_LOG_DIR):
        cfg = load_config(config_path)
        self.poll_s = float(cfg["poll_s"])
        self.n_runs_watch = int(cfg["n_runs_watch"])
        self.baseline_window = int(cfg["baseline_window"])
        self.reduced_ratio = float(cfg["reduced_ratio"])
        self.questionable_ratio = float(cfg["questionable_ratio"])
        self.alert_consec = int(cfg["alert_consec"])
        self.stale_min = float(cfg["stale_min"])
        self.eos_base = cfg["eos_base"]
        self.waveform_enabled = bool(cfg["waveform_enabled"])
        self.waveform_max_bytes = int(cfg["waveform_max_bytes"])
        self.waveform_min_interval_s = float(cfg["waveform_min_interval_s"])
        self.state_path = state_path
        self.log_dir = log_dir
        self.connected = False
        self.last_error = None
        self._stop = False
        # Rolling history of (mtime, size_bytes, run, seq, grade) for the baseline and
        # the summary, seeded from the CSVs so a restart keeps its reference level.
        # grade is one of GRADES, or None for a file seen before a baseline existed.
        # Beam context first: _load_history needs to know whether per-pulse
        # normalisation is available before it can interpret older rows.
        self._beam = BeamIndex().refresh()
        self._hist = self._load_history()
        self._seen = {(r["run"], r["seq"]) for r in self._hist}
        self._pending = {}   # (run, seq) -> size at the previous listing (settle check)
        # Waveform layer: frozen per-detector reference + the newest graded sample.
        self._nominal = self.load_nominal()
        self._size_nominal = self.load_size_nominal()
        self._last_waveform = None
        self._last_waveform_at = 0.0
        self._waveform_error = None
        self._nominal_message = None
        # Effective read cap. Starts at the configured value and only ever grows, so
        # events getting bigger cannot silently start truncating the channel list —
        # see WAVEFORM_MAX_BYTES for what that cost us on 2026-07-27.
        self._waveform_cap = self.waveform_max_bytes
        self._waveform_event_bytes = None      # largest COMPLETE event seen this session
        self._waveform_incomplete_consec = 0   # run of reads that never reached event 2
        self._size_nominal_message = None

    # ---------------- EOS listing ----------------

    def _xrdfs(self, args, timeout=60):
        """Run `xrdfs <URL> <args...>`, returning stdout lines. Raises on failure."""
        p = subprocess.run(["xrdfs", EOS_URL] + args, capture_output=True,
                           text=True, timeout=timeout)
        if p.returncode != 0:
            raise RuntimeError((p.stderr or p.stdout or "xrdfs failed").strip().splitlines()[-1])
        return [ln for ln in p.stdout.splitlines() if ln.strip()]

    def _newest_runs(self):
        """The N_RUNS_WATCH highest-numbered run dirs under EOS_BASE."""
        runs = []
        for ln in self._xrdfs(["ls", self.eos_base]):
            name = ln.rstrip("/").rsplit("/", 1)[-1]
            if name.isdigit():
                runs.append(int(name))
        return sorted(runs)[-self.n_runs_watch:]

    def _list_run(self, run):
        """(mtime, size_bytes, run, seq) for every stream1 file of one run."""
        out = []
        try:
            lines = self._xrdfs(["ls", "-l", f"{self.eos_base}/{run}/stream1"])
        except Exception as e:
            # A run dir with no stream1 yet is normal right after a run starts.
            self.log(f"listing run {run} failed: {e}")
            return out
        for ln in lines:
            m = _LINE_RE.match(ln.strip())
            if not m:
                continue
            size, day, tod, path = m.groups()
            nm = _NAME_RE.search(os.path.basename(path))
            if not nm:
                continue
            out.append((_eos_time(day, tod),
                        int(size), int(nm.group(1)), int(nm.group(2))))
        return out

    def _run_finished(self, run):
        """True if the run has been closed out. n_TOF writes stream0/ (the run index
        plus a mark.dto end-of-run marker) only when a run ends, so its absence means
        the run is still taking data — which is what distinguishes 'the last run
        ended' from 'files stopped arriving'."""
        try:
            return any(ln.rstrip("/").endswith("stream0")
                       for ln in self._xrdfs(["ls", f"{self.eos_base}/{run}"]))
        except Exception:
            return None

    # ---------------- waveform layer ----------------

    def _decode_first_event(self, run, seq):
        """Per-channel metrics from the first event of one stream1 file.

        Streams `xrdfs cat` straight into the bank reader and stops at the second
        event header — the transfer is killed there, so only as much of the file as
        one event needs is ever moved. Returns (rows, meta) or (None, error)."""
        import numpy as np
        try:
            from .ntof_raw import iter_banks, parse_acqc
        except ImportError:                      # run as a plain script, not a package
            from ntof_raw import iter_banks, parse_acqc

        path = f"{self.eos_base}/{run}/stream1/run{run}_{seq}_s1.raw.finished"
        proc = subprocess.Popen(["xrdfs", EOS_URL, "cat", path],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        cap = self._waveform_cap
        rows, n_eveh, event, complete = [], 0, None, False
        # How far the read actually got. iter_banks returns silently both when it hits
        # max_bytes and when the stream just ends, so the caller cannot otherwise tell
        # "the cap stopped us" (raise it) from "the file is short or corrupt" (do not).
        end = 0
        try:
            for off, tag, _ver, payload in iter_banks(proc.stdout, max_bytes=cap):
                end = off + 16 + len(payload)     # 16 = ntof_raw.HDR_SIZE
                if tag == "EVEH":
                    n_eveh += 1
                    if n_eveh > 1:
                        complete = True     # first event fully read
                        break
                    event = struct.unpack_from("<10I", payload, 0)[3]
                elif tag == "ACQC" and n_eveh == 1:
                    det, chan, blocks = parse_acqc(payload, with_samples=True)
                    if not blocks:
                        continue
                    # Metrics come from the always-kept first block (the 30 us around
                    # the gamma flash), which every channel has whether or not it is
                    # still producing zero-suppressed pulses.
                    s = blocks[0][1].astype(float)
                    lead = s[:WAVEFORM_BASELINE_SAMPLES]
                    base = float(np.median(lead))
                    rows.append({
                        "det": det, "chan": int(chan),
                        "baseline": round(base, 1),
                        "rms": round(float(np.std(lead)), 2),
                        "flash": round(float(np.max(np.abs(s - base))), 1),
                        "zs_blocks": len(blocks),
                    })
        except Exception as e:
            return None, f"decode failed: {e}"
        finally:
            # SIGKILL, not wait(): xrdfs is mid-transfer and we deliberately want the
            # rest of the file to never come across.
            try:
                proc.kill()
                proc.communicate(timeout=10)
            except Exception:
                pass
        if not rows:
            # Usually transient: a file can appear in the listing a moment before it
            # is readable. The next poll retries (_last_waveform_at only advances on
            # success), so surface xrdfs's own words and move on.
            try:
                err = (proc.stderr.read() or b"").decode(errors="replace").strip()
            except Exception:
                err = ""
            return None, f"no channels decoded ({err.splitlines()[-1] if err else 'empty head'})"
        return rows, {"run": run, "seq": seq, "event": event, "complete": complete,
                      "bytes_read": end, "cap_bytes": cap,
                      # The next bank header would not have fitted under the cap, so
                      # the cap is what ended the read — the one case worth growing it
                      # for. A short/corrupt stream ends well below it and must not.
                      "cap_limited": (not complete) and end + 16 > cap}

    def _update_waveform_cap(self, meta):
        """Keep the read cap comfortably clear of what an event actually costs.

        Growing it is FREE — the decode breaks at the second EVEH, so a healthy file
        moves one event whatever the cap says — while being under it is what fakes a
        missing channel. So the cap only ever grows, on both available signals: a
        complete read measures a real event, an incomplete one proves the cap is
        already too small. It never shrinks, because the quantity that matters is the
        LARGEST event, and shrinking back to the median would re-arm the exact failure
        this exists to prevent."""
        cap, want = self._waveform_cap, None
        if meta.get("complete") and meta.get("bytes_read"):
            self._waveform_event_bytes = max(self._waveform_event_bytes or 0,
                                             meta["bytes_read"])
            want = int(self._waveform_event_bytes * WAVEFORM_CAP_HEADROOM)
        elif meta.get("cap_limited"):
            # Size unknown — the read stopped before the end of the event — so step up
            # rather than fit. Doubling reaches the ceiling in a couple of samples.
            want = cap * 2
        if want is None or want <= cap:
            return
        self._waveform_cap = min(want, WAVEFORM_MAX_BYTES_CEILING)
        if self._waveform_cap > cap:
            self.log(f"waveform read cap raised {cap / 1e6:.0f} -> "
                     f"{self._waveform_cap / 1e6:.0f} MB "
                     + (f"(event is {meta['bytes_read'] / 1e6:.0f} MB)"
                        if meta.get("complete")
                        else f"(read hit the cap at {meta.get('bytes_read', 0) / 1e6:.0f} MB)"))
        elif want > WAVEFORM_MAX_BYTES_CEILING:
            self.log(f"waveform read cap pinned at its {WAVEFORM_MAX_BYTES_CEILING / 1e6:.0f} MB "
                     f"ceiling — an event should never need this much; suspect a "
                     f"corrupt file rather than a large one")

    @staticmethod
    def _by_detector(rows, key):
        """Mean of one metric per detector name."""
        out = {}
        for r in rows:
            out.setdefault(r["det"], []).append(r[key])
        return {d: sum(v) / len(v) for d, v in out.items()}

    def load_nominal(self):
        try:
            with open(STREAM1_NOMINAL_PATH) as f:
                return json.load(f)
        except Exception:
            return None

    def adopt_nominal(self, rows, meta, source="auto"):
        """Freeze the per-detector reference from a healthy sample.

        Refuses while the walls are down, so neither the auto-seed nor an operator
        pressing "set nominal" mid-dropout can bless the fault as normal. Refuses an
        incomplete read for the same reason: the nominal is where `n_chan` comes from,
        so freezing a truncated sample would record the short channel count as correct
        and permanently disarm the missing-channel check — the failure would be a
        check that never fires again, which is not a failure anyone notices."""
        if not meta.get("complete", True):
            return None, (f"refused: only {len(rows)} channels were read before the "
                          f"decoder stopped at {meta.get('bytes_read', 0) / 1e6:.0f} MB "
                          f"— an incomplete event would freeze a short channel count")
        flash = self._by_detector(rows, "flash")
        walls = [v for d, v in flash.items() if d.startswith(NOMINAL_WALL_PREFIX)]
        if walls and min(walls) < NOMINAL_MIN_WALL_FLASH:
            return None, (f"refused: wall flash down to {min(walls):.0f} counts "
                          f"(< {NOMINAL_MIN_WALL_FLASH:.0f}) — this file is not healthy")
        nominal = {
            "adopted": datetime.now().isoformat(timespec="seconds"),
            "source": source,
            "run": meta["run"], "seq": meta["seq"], "event": meta["event"],
            "detectors": {
                d: {"flash": round(flash[d], 1),
                    "baseline": round(self._by_detector(rows, "baseline")[d], 1),
                    "rms": round(self._by_detector(rows, "rms")[d], 2),
                    "zs_blocks": round(self._by_detector(rows, "zs_blocks")[d], 1),
                    "n_chan": sum(1 for r in rows if r["det"] == d)}
                for d in flash},
        }
        try:
            tmp = STREAM1_NOMINAL_PATH + ".tmp"
            with open(tmp, "w") as f:
                json.dump(nominal, f, indent=2)
            os.replace(tmp, STREAM1_NOMINAL_PATH)
        except Exception as e:
            return None, f"could not write nominal: {e}"
        self._nominal = nominal
        return nominal, None

    @staticmethod
    def _zeroed_channels(rows, nominal, complete=True):
        """Walls/liquids/plastics channels that produced no waveform at all.

        Two ways a channel can have nothing in it, both reported here:
          flatlined — present in the event but its samples do not move (RMS ~ 0).
          missing   — no ACQC bank / no blocks at all, so the decoder never even made
                      a row for it. Invisible to every per-detector mean in this file,
                      because a detector's mean is taken over the channels that DID
                      report.

        Per CHANNEL, never per detector: a wall is 8 channels, so one dead SiPM moves
        the detector-mean RMS by about an eighth (28 -> 24.5) and hides inside the
        healthy spread. The mean is the wrong instrument for this fault.

        `missing` needs `complete` — i.e. the decoder actually reached the end of the
        event — because it is inferred from ABSENCE, and a read that stopped early is
        absence for a wholly innocent reason. The channels at the tail of the event
        are simply the ones that had not arrived yet, and on 2026-07-27 that turned a
        268.8 MB event under a 260 MB cap into an emergency "PSSD 1/2 missing" for a
        healthy channel. `flatlined` needs no such gate: those channels were read, and
        what they contained was nothing.
        """
        flat, missing = [], []
        for r in rows:
            if not r["det"].startswith(ZEROED_ALERT_PREFIXES):
                continue
            if r["rms"] <= ZERO_RMS_COUNTS and r["flash"] <= ZERO_FLASH_COUNTS:
                flat.append({"det": r["det"], "chan": r["chan"], "kind": "flatlined",
                             "rms": r["rms"], "flash": r["flash"],
                             "baseline": r["baseline"], "zs_blocks": r["zs_blocks"]})
        if not complete:
            return flat
        # Missing channels need the nominal to know how many there should be. Without
        # one, silence is indistinguishable from a detector that never existed.
        seen = {}
        for r in rows:
            seen[r["det"]] = seen.get(r["det"], 0) + 1
        for det, ref in ((nominal or {}).get("detectors", {}) or {}).items():
            if not det.startswith(ZEROED_ALERT_PREFIXES):
                continue
            gone = int(ref.get("n_chan") or 0) - seen.get(det, 0)
            if gone > 0:
                missing.append({"det": det, "chan": None, "kind": "missing",
                                "n_missing": gone,
                                "n_expected": int(ref.get("n_chan") or 0)})
        return flat + missing

    def _grade_waveform(self, rows, meta):
        """Compare one sample against the nominal -> per-detector + overall verdict."""
        nominal = self._nominal or self.load_nominal()
        flash = self._by_detector(rows, "flash")
        base = self._by_detector(rows, "baseline")
        rms = self._by_detector(rows, "rms")
        zs = self._by_detector(rows, "zs_blocks")

        # Was there a proton pulse in this event at all? Decided from the event itself
        # (see BEAM_WITNESSES) and cross-checked against the beam log, which covers
        # the case where a witness detector is itself faulty.
        witness_ratios = {}
        for d in BEAM_WITNESSES:
            ref = ((nominal or {}).get("detectors", {}) or {}).get(d)
            if ref and ref.get("flash") and d in flash:
                witness_ratios[d] = flash[d] / ref["flash"]
        witnesses_dead = bool(witness_ratios) and all(
            r < WITNESS_BEAM_RATIO for r in witness_ratios.values())
        beam_pulses = None
        if self._beam.available:
            rec = next((h for h in reversed(self._hist)
                        if h["run"] == meta["run"] and h["seq"] == meta["seq"]), None)
            beam_pulses = rec["pulses"] if rec else None
        no_beam = witnesses_dead or (beam_pulses is not None
                                     and beam_pulses < MIN_PULSES_TO_GRADE)

        dets, worst = {}, "good"
        for d in sorted(flash):
            ref = ((nominal or {}).get("detectors", {}) or {}).get(d)
            ratio = (flash[d] / ref["flash"]) if ref and ref["flash"] else None
            ungraded = ungraded_reason(d)
            if ratio is None or ungraded:
                grade = None
            elif no_beam:
                # Nothing to compare: with no flash to reference, a low amplitude is
                # the absence of beam, not a detector fault.
                grade = NO_BEAM
            elif ratio < FLASH_BAD_RATIO:
                grade = "bad"
            elif ratio < FLASH_QUESTIONABLE_RATIO:
                grade = "questionable"
            else:
                grade = "good"
            # Baseline/RMS move independently of gain: call them out separately so a
            # gain collapse is never confused with a digitiser or DC-path fault.
            notes = []
            if ungraded:
                notes.append(ungraded)
            if ref and not ungraded:
                if abs(base[d] - ref["baseline"]) > BASELINE_SHIFT_COUNTS:
                    notes.append(f"baseline {base[d]:.0f} vs {ref['baseline']:.0f}")
                if ref["rms"] and not (1 / RMS_FACTOR <= rms[d] / ref["rms"] <= RMS_FACTOR):
                    notes.append(f"RMS {rms[d]:.1f} vs {ref['rms']:.1f}")
            dets[d] = {
                "flash": round(flash[d], 1),
                "flash_nominal": ref["flash"] if ref else None,
                "flash_ratio": round(ratio, 4) if ratio is not None else None,
                "zs_blocks": round(zs[d], 1),
                "zs_nominal": ref["zs_blocks"] if ref else None,
                "zs_ratio": (round(zs[d] / ref["zs_blocks"], 4)
                             if ref and ref["zs_blocks"] else None),
                "baseline": round(base[d], 1), "rms": round(rms[d], 2),
                "n_chan": sum(1 for r in rows if r["det"] == d),
                "grade": grade, "notes": notes,
                # Distinguishes "deliberately not graded" from "no nominal for it yet":
                # both leave grade None, but only one of them is a gap in coverage.
                "ungraded": ungraded,
            }
            if grade in GRADES and GRADES.index(grade) > GRADES.index(worst):
                worst = grade

        return {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "run": meta["run"], "seq": meta["seq"], "event": meta["event"],
            "complete": meta["complete"],
            "grade": (NO_BEAM if no_beam else worst) if nominal else None,
            "no_beam": no_beam,
            "beam_pulses": beam_pulses,
            "witness_ratios": {d: round(r, 4) for d, r in witness_ratios.items()},
            "witnesses_dead": witnesses_dead,
            "have_nominal": bool(nominal),
            # Named in the sample itself so the GUI can say which detectors were sat
            # out, without having to know the prefix rules.
            "ungraded_detectors": {d: v["ungraded"] for d, v in
                                   sorted(dets.items()) if v["ungraded"]},
            "nominal_adopted": (nominal or {}).get("adopted"),
            "nominal_run": (nominal or {}).get("run"),
            "bad_detectors": [] if no_beam else [d for d, v in dets.items()
                                                 if v["grade"] == "bad"],
            "questionable_detectors": [] if no_beam else [
                d for d, v in dets.items() if v["grade"] == "questionable"],
            # Deliberately NOT gated on no_beam, unlike every other verdict above. The
            # no-beam suppression exists because an absent gamma flash is not evidence
            # of a fault — but a flat channel is a fault whatever the beam is doing,
            # and a beam gap is exactly when a quietly dead front-end would otherwise
            # go unnoticed. Also not gated on `nominal`: flatlined channels are judged
            # against zero, not against a reference. It IS gated on a complete read,
            # but only for its `missing` half — see _zeroed_channels.
            "zeroed_channels": self._zeroed_channels(rows, nominal, meta["complete"]),
            # Whether the absence-based half of that check actually ran. Published
            # rather than left implicit: a disarmed check and a clean one both look
            # like an empty list, and only this distinguishes them.
            "missing_check": "ok" if meta["complete"] else "disarmed (incomplete read)",
            "bytes_read": meta.get("bytes_read"),
            "cap_bytes": meta.get("cap_bytes"),
            "cap_limited": meta.get("cap_limited"),
            "n_chan_total": len(rows),
            "n_chan_expected": sum(
                int((ref or {}).get("n_chan") or 0)
                for ref in ((nominal or {}).get("detectors", {}) or {}).values()) or None,
            "detectors": dets,
            "channels": rows,
        }

    def _sample_waveform(self, run, seq, size_grade):
        """Decode one file and grade it; seeds the nominal on the first healthy sample."""
        t0 = time.time()
        rows, meta = self._decode_first_event(run, seq)
        if rows is None:
            self.log(f"waveform sample of run {run}_{seq} failed: {meta}")
            self._waveform_error = str(meta)
            return None
        self._waveform_error = None
        # Cap first: a truncated read is the one thing that must change the NEXT
        # sample's behaviour, and it has to happen whether or not grading succeeds.
        self._update_waveform_cap(meta)
        if meta["complete"]:
            self._waveform_incomplete_consec = 0
        else:
            self._waveform_incomplete_consec += 1
            self.log(
                f"waveform run {run}_{seq}: INCOMPLETE read — {len(rows)} channels in "
                f"{meta.get('bytes_read', 0) / 1e6:.0f} MB, "
                + ("stopped by the read cap" if meta.get("cap_limited")
                   else "stream ended before the next event")
                + f"; missing-channel detection is disarmed for this sample "
                  f"({self._waveform_incomplete_consec} in a row)")
        if self._nominal is None and size_grade == "good":
            # Auto-seed from the first file the (independent) size layer calls healthy;
            # adopt_nominal still vetoes if the walls are actually down.
            nominal, err = self.adopt_nominal(rows, meta, source="auto-seed")
            self.log(f"nominal {'adopted from' if nominal else 'NOT adopted from'} "
                     f"run {run}_{seq}" + (f": {err}" if err else ""))
        wf = self._grade_waveform(rows, meta)
        wf["decode_s"] = round(time.time() - t0, 1)
        self._log_waveform(wf)
        self._last_waveform_at = time.time()
        bad = wf["bad_detectors"]
        zeroed = wf.get("zeroed_channels") or []
        self.log(f"waveform run {run}_{seq}: {wf['grade'] or 'no nominal'}"
                 + (f" — down: {', '.join(bad)}" if bad else "")
                 # Printed even when the grade is no_beam/None, because that is
                 # precisely the case the grade itself will not tell you about.
                 + (f" — ZEROED: {describe_zeroed(zeroed)}" if zeroed else "")
                 + f" ({wf['decode_s']}s)")
        return wf

    def _reload_nominals_if_edited(self):
        """Pick up hand-edited nominal files (both are plain JSON an operator may
        reasonably tweak) without needing a watcher restart."""
        # NB: compare by name, not by bound method — `self.load_nominal is
        # self.load_nominal` is False, which silently sent the waveform nominal down
        # the size branch and threw KeyError on every poll.
        for kind, path, attr in (("waveform", STREAM1_NOMINAL_PATH, "_nominal_mtime"),
                                 ("size", STREAM1_SIZE_NOMINAL_PATH, "_size_nominal_mtime")):
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            if getattr(self, attr, None) == mtime:
                continue
            setattr(self, attr, mtime)
            if kind == "waveform":
                self._nominal = self.load_nominal()
            else:
                self._size_nominal = self.load_size_nominal()
                if self._size_nominal:
                    self.log(f"size benchmark reloaded: "
                             f"{self._size_nominal['per_pulse_gib'] * 1024:.0f} MiB/pulse "
                             f"({self._size_nominal.get('source')})")

    def _handle_command(self):
        """Apply and clear a pending GUI command. Only 'set_nominal' exists: decode
        the newest known file and freeze it as the new reference (vetoed if the walls
        are down). Result goes back through the state file."""
        try:
            with open(STREAM1_COMMAND_PATH) as f:
                cmd = json.load(f)
        except Exception:
            return
        try:
            os.remove(STREAM1_COMMAND_PATH)
        except OSError:
            pass
        if cmd.get("cmd") == "set_size_nominal":
            nominal, err = self.adopt_size_nominal(cmd.get("per_pulse_bytes"),
                                                   source="operator")
            self._size_nominal_message = err or (
                f"size benchmark frozen at {nominal['per_pulse_gib'] * 1024:.0f} MiB"
                + ("/pulse" if nominal["unit"] == "bytes_per_pulse" else "/file"))
            self.log(f"set_size_nominal: {self._size_nominal_message}")
            return
        if cmd.get("cmd") != "set_nominal" or not self._hist:
            return
        run, seq = self._hist[-1]["run"], self._hist[-1]["seq"]
        self.log(f"set_nominal requested — decoding run {run}_{seq}")
        rows, meta = self._decode_first_event(run, seq)
        if rows is None:
            self._nominal_message = f"could not decode run {run}_{seq}: {meta}"
        else:
            # Same cap feedback as the automatic path: an operator whose "set nominal"
            # was refused for a short read should find the cap already raised, so
            # pressing it again is a fix rather than a repeat of the same failure.
            self._update_waveform_cap(meta)
            nominal, err = self.adopt_nominal(rows, meta, source="operator")
            self._nominal_message = (err if err else
                                     f"nominal set from run {run}_{seq} "
                                     f"({len(nominal['detectors'])} detectors)")
            self._last_waveform = self._grade_waveform(rows, meta)
            self._last_waveform_at = time.time()
        self.log(f"set_nominal: {self._nominal_message}")

    # ---------------- classification ----------------

    def _level(self, sizes):
        """Typical size of a *full* file in a set of sizes.

        Two passes, because the set may contain a mix of full and dropped-out files:
        the median of the largest BASELINE_TOP_FRAC anchors the full level, then the
        baseline is the median of every file within REDUCED_RATIO of that anchor, i.e.
        the median of the full population alone. Taking the anchor itself as the
        baseline would sit at the TOP of normal scatter and push a large share of
        healthy files below the questionable cut."""
        sizes = sorted(sizes)
        top = sizes[-max(1, int(len(sizes) * BASELINE_TOP_FRAC)):]
        anchor = top[len(top) // 2]
        full = [s for s in sizes if s >= self.reduced_ratio * anchor]
        return full[len(full) // 2] if full else anchor

    def _moving_baseline(self, fallback=None):
        """'Full' level from the trailing window — the SUGGESTION for a frozen
        baseline, and the fallback while none has been adopted.

        In units of bytes PER PROTON PULSE when beam data is available, plain bytes
        when it is not (see _file_norm). Beam-less files carry no norm and are left out
        entirely: a beam gap must not drag the reference down and make the recovery
        afterwards look like an excess."""
        window = [r["norm"] for r in self._hist[-self.baseline_window:]
                  if r["norm"] is not None]
        if len(window) < MIN_BASELINE_FILES:
            return fallback
        return self._level(window)

    def _baseline(self, fallback=None):
        """The level files are actually graded against.

        The frozen benchmark when one has been adopted, otherwise the trailing window.
        Freezing is the point: a moving reference absorbs a long dropout and stops
        calling it a dropout (2026-07-22 11:50 — the size layer read "good" through a
        real one while the walls were at 30 % of nominal). The stored unit must match
        how files are being measured now, or a beam watcher coming up / going down
        would silently compare bytes against bytes-per-pulse."""
        frozen = self._size_nominal
        if frozen and frozen.get("unit") == self._norm_unit():
            return frozen["per_pulse_bytes"]
        return self._moving_baseline(fallback)

    def _norm_unit(self):
        return "bytes_per_pulse" if self._beam.available else "bytes"

    def _cuts(self, base):
        """(questionable_cut, bad_cut) in bytes, or (None, None) without a baseline."""
        if not base:
            return None, None
        return base * self.questionable_ratio, base * self.reduced_ratio

    def _grade(self, size, base):
        """good / questionable / bad for one file (None while un-baselined)."""
        quest, bad = self._cuts(base)
        if bad is None:
            return None
        if size < bad:
            return "bad"
        if size < quest:
            return "questionable"
        return "good"

    def _typical_span(self, n=20):
        """Median recent gap between file closes = how long one file takes to write."""
        times = [r["t"] for r in self._hist[-(n + 1):]]
        gaps = sorted((b - a).total_seconds() for a, b in zip(times, times[1:])
                      if FILE_SPAN_CLAMP_S[0] <= (b - a).total_seconds() <= FILE_SPAN_CLAMP_S[1])
        return gaps[len(gaps) // 2] if gaps else FILE_SPAN_DEFAULT_S

    def _file_norm(self, mtime, size, prev_mtime):
        """(pulses, protons, norm) for one file.

        norm is the size per proton pulse — the beam-independent quantity, since the
        number of events in a file is set by how many pulses arrived while it was
        open. With no beam data at all, norm falls back to raw size so the layer keeps
        working exactly as it did before. With beam data but no pulses, norm is None
        and the file is graded NO_BEAM."""
        if not self._beam.available:
            return None, None, float(size)
        # The window is the file's own writing period, approximated by the gap to the
        # previous close — but capped at ~1.5x the typical gap. After a pause in
        # writing that gap is not the file's duration, and counting all the beam since
        # the last file inflates the pulse count and fakes a dropout: the file after a
        # 364 s pause on 2026-07-22 was credited 60 pulses instead of ~19 and graded
        # bad at 0.33x while being perfectly healthy.
        span = FILE_SPAN_DEFAULT_S
        if prev_mtime is not None:
            span = (mtime - prev_mtime).total_seconds()
        # Cap at the typical span, not a multiple of it: when a file genuinely took
        # longer than usual we undercount pulses and so over-estimate its per-pulse
        # content, which errs toward "good" — the right direction for a monitor that
        # must not cry wolf.
        typical = self._typical_span()
        span = min(max(span, FILE_SPAN_CLAMP_S[0]), min(typical, FILE_SPAN_CLAMP_S[1]))
        pulses, protons = self._beam.window(mtime, span)
        if pulses < MIN_PULSES_TO_GRADE:
            return pulses, protons, None
        return pulses, protons, size / pulses

    def load_size_nominal(self):
        try:
            with open(STREAM1_SIZE_NOMINAL_PATH) as f:
                n = json.load(f)
            return n if n.get("per_pulse_bytes") else None
        except Exception:
            return None

    def adopt_size_nominal(self, value_per_pulse_bytes=None, source="auto"):
        """Freeze the size benchmark. `value` defaults to the current trailing level.

        Vetoed unless the detectors are known healthy, which is what the waveform
        layer is for: freezing a benchmark measured during a dropout would bless the
        fault permanently — the exact failure mode a moving average has, made
        permanent. Returns (nominal, error)."""
        value = value_per_pulse_bytes or self._moving_baseline()
        if not value:
            return None, (f"not enough gradeable files yet "
                          f"(need {MIN_BASELINE_FILES} with beam)")
        wf = self._last_waveform
        if wf and wf.get("have_nominal") and wf.get("grade") == "bad":
            return None, ("refused: the waveform layer currently reports "
                          + ", ".join(wf.get("bad_detectors") or ["a detector"])
                          + " below nominal gain — fix that before freezing a size "
                            "benchmark measured through it")
        recent = [r["grade"] for r in self._hist[-RECENT_FILES:]
                  if r["grade"] in GRADES]
        if recent and sum(g == "bad" for g in recent) > len(recent) / 2:
            return None, "refused: most recent files are graded bad — this is not a healthy stretch"

        nominal = {
            "adopted": datetime.now().isoformat(timespec="seconds"),
            "source": source,
            "unit": self._norm_unit(),
            "per_pulse_bytes": float(value),
            "per_pulse_gib": round(value / GB, 5),
            "run": self._hist[-1]["run"] if self._hist else None,
            "n_files": len([r for r in self._hist[-self.baseline_window:]
                            if r["norm"] is not None]),
        }
        try:
            tmp = STREAM1_SIZE_NOMINAL_PATH + ".tmp"
            with open(tmp, "w") as f:
                json.dump(nominal, f, indent=2)
            os.replace(tmp, STREAM1_SIZE_NOMINAL_PATH)
        except Exception as e:
            return None, f"could not write size nominal: {e}"
        self._size_nominal = nominal
        return nominal, None

    def _current_episode(self):
        """(start_time, n_files) of the bad-size episode in progress, or None.

        The episode is the trailing run of not-good files, and it counts as one only
        if at least one of them is bad: a questionable file alone is not enough to
        declare a dropout, but it does not break one either (a marginal file inside a
        dropout should not reset the counter). Beam-less files are transparent — they
        neither start, extend nor break an episode, since they say nothing either way."""
        tail = []
        for rec in reversed(self._hist):
            if rec["grade"] == NO_BEAM:
                continue
            if rec["grade"] not in ("bad", "questionable"):
                break
            tail.append(rec)
        if not any(r["grade"] == "bad" for r in tail):
            return None
        return tail[-1]["t"], len(tail)

    # ---------------- poll ----------------

    def _poll_once(self):
        # Command confirmations are transient: cleared here so each one is shown for
        # the poll that follows it and then goes away, rather than sticking to the
        # card for the rest of the run.
        self._nominal_message = self._size_nominal_message = None
        self._reload_nominals_if_edited()
        self._handle_command()
        runs = self._newest_runs()
        found = []
        for run in runs:
            found.extend(self._list_run(run))
        self.connected = True
        self.last_error = None

        # Newly-seen files, oldest first, so each is classified against the baseline as
        # it stood when the file was written (matching what the CSV records).
        #
        # A file can appear in the listing before its content has landed — 224534_8 was
        # listed at 0 bytes at 14:41 and was 2.0 GiB minutes later — and grading that
        # snapshot invents a dropout. So only take a file once its size is SETTLED:
        # either a newer file exists for the same run (this one must be closed), or the
        # size is unchanged since the previous listing. Anything else waits a poll.
        newest_seq = {}
        for _mt, _sz, run, seq in found:
            newest_seq[run] = max(newest_seq.get(run, -1), seq)
        pending = {}
        fresh = []
        for rec in sorted(found):
            mtime, size, run, seq = rec
            if (run, seq) in self._seen:
                continue
            pending[(run, seq)] = size
            settled = size > 0 and (seq < newest_seq[run]
                                    or self._pending.get((run, seq)) == size)
            if settled:
                fresh.append(rec)
            else:
                self.log(f"holding run {run}_{seq}: size {size / GB:.2f} GiB not settled yet")
        self._pending = pending
        self._beam.refresh()
        rows = []
        for mtime, size, run, seq in fresh:
            prev = self._hist[-1]["t"] if self._hist else None
            pulses, protons, norm = self._file_norm(mtime, size, prev)
            # Cold start (no CSV history): without a reference the first
            # MIN_BASELINE_FILES files would go unclassified, which is exactly the
            # backlog most likely to hold a dropout. Reference the batch against
            # itself. (Only sensible once norms exist, hence the recompute per file.)
            cold = None
            if not self._size_nominal and len(self._hist) < MIN_BASELINE_FILES:
                seeds = [r["norm"] for r in self._hist if r["norm"] is not None]
                if len(seeds) >= MIN_BASELINE_FILES // 2:
                    cold = self._level(seeds)
            base = self._baseline(fallback=cold)
            if norm is None:
                grade, ratio, quest_cut, bad_cut = NO_BEAM, None, None, None
            else:
                grade = self._grade(norm, base)
                ratio = (norm / base) if base else None
                quest_cut, bad_cut = self._cuts(base)
            # Cuts are stored back in raw GiB (cut x this file's pulses) so the plot
            # can show them against the plotted file sizes.
            unit = pulses if pulses else 1
            self._hist.append({"t": mtime, "size": size, "run": run, "seq": seq,
                               "grade": grade, "pulses": pulses, "norm": norm})
            self._seen.add((run, seq))
            rows.append({"timestamp": mtime.isoformat(sep=" "),
                         "unix_ts": round(mtime.timestamp(), 3),
                         "run": run, "seq": seq, "size_bytes": size,
                         "size_gib": round(size / GB, 4),
                         "pulses": pulses if pulses is not None else "",
                         "protons_e10": protons if protons is not None else "",
                         "baseline_gib": round(base * unit / GB, 4) if base else "",
                         "ratio": round(ratio, 4) if ratio is not None else "",
                         "quest_cut_gib": round(quest_cut * unit / GB, 4) if quest_cut else "",
                         "bad_cut_gib": round(bad_cut * unit / GB, 4) if bad_cut else "",
                         "grade": grade or "",
                         "reduced": int(grade == "bad")})
        if rows:
            self._log_rows(rows)
            self._hist = self._hist[-max(self.baseline_window * 3, 1200):]
            # Decode the newest file when the waveform layer is due. Sampling the
            # NEWEST (not the oldest unseen) keeps the verdict about now, and the
            # interval keeps a backlog of 100 files from triggering 100 decodes.
            # A file the size layer is unhappy about earns a faster look, because the
            # waveform layer is the one that can still see a dropout the moving size
            # baseline has absorbed (2026-07-22 11:50: size graded good, walls at 30 %
            # of nominal with 1-7 ZS blocks instead of ~1000).
            due = self.waveform_min_interval_s
            if self._hist and self._hist[-1]["grade"] in ("bad", "questionable"):
                due /= 5
            if self.waveform_enabled and time.time() - self._last_waveform_at >= due:
                newest = self._hist[-1]
                if newest["grade"] == NO_BEAM:
                    # Don't spend a 200 MB transfer to rediscover that the beam is off;
                    # the size layer already knows, and the event would have no flash.
                    self.log(f"waveform sample skipped: no beam for run "
                             f"{newest['run']}_{newest['seq']}")
                else:
                    wf = self._sample_waveform(newest["run"], newest["seq"], newest["grade"])
                    if wf:
                        self._last_waveform = wf

            # Auto-seed the frozen size benchmark once there is a healthy stretch to
            # measure — only if the waveform layer agrees the detectors are fine, so
            # the first thing the monitor ever sees cannot become its idea of normal.
            if self._size_nominal is None and self._moving_baseline():
                wf_ok = (not self.waveform_enabled
                         or ((self._last_waveform or {}).get("grade") == "good"))
                if wf_ok:
                    nominal, err = self.adopt_size_nominal(source="auto-seed")
                    self.log(f"size benchmark {'frozen at' if nominal else 'NOT frozen'} "
                             + (f"{nominal['per_pulse_gib'] * 1024:.0f} MiB/pulse"
                                if nominal else err))
        # Only worth asking (one extra listing) when the stream has gone quiet.
        finished = None
        if self._hist and not rows:
            age_min = (datetime.now() - self._hist[-1]["t"]).total_seconds() / 60.0
            if age_min > self.stale_min:
                finished = self._run_finished(self._hist[-1]["run"])
        return self._build_state(runs, len(rows), finished)

    def _build_state(self, runs, n_new, finished=None):
        now = datetime.now()
        base = self._baseline()
        suggestion = self._moving_baseline()   # what a re-freeze would adopt right now
        quest_cut, bad_cut = self._cuts(base)
        recent = self._hist[-RECENT_FILES:]
        episode = self._current_episode()

        if not self._hist:
            state, latest = "no_data", None
        else:
            latest = self._hist[-1]
            age_min = (now - latest["t"]).total_seconds() / 60.0
            if base is None:
                state = "unknown"          # not enough history for a reference level
            elif latest["grade"] == NO_BEAM:
                # No protons while that file was open: its size means nothing, and
                # neither would a "reduced" verdict drawn from it.
                state = "no_beam"
            elif episode and episode[1] >= self.alert_consec:
                state = "reduced"
            elif age_min > self.stale_min:
                # Nothing written recently — not a size anomaly. `finished` says which
                # kind: the run was closed out (normal) vs it is still open and the
                # files stopped coming (DAQ trouble or a lagging EOS transfer).
                state = "run_ended" if finished else "stale"
            elif latest["grade"] == "questionable" or any(r["grade"] == "bad" for r in recent):
                state = "questionable"     # low but not (yet) a declared dropout
            else:
                state = "full"

        # Headline verdict: the worse of the two layers. Size is continuous but only
        # says "volume dropped"; waveform is sampled but says which detector and by
        # how much — a detector can be down while the file size still looks fine.
        wf_grade = (self._last_waveform or {}).get("grade")
        if state in ("full", "questionable", "reduced"):
            size_grade = {"full": "good", "questionable": "questionable",
                          "reduced": "bad"}[state]
            # Only a real good/questionable/bad waveform verdict can worsen the
            # headline. NO_BEAM (and anything else off the ladder) is not a fault
            # grade — folding it into the max is a ValueError, not a verdict.
            overall = max([size_grade] + ([wf_grade] if wf_grade in GRADES else []),
                          key=GRADES.index)
        else:
            overall = state              # stale / run_ended / unknown / no_data

        # Pulses to express the (per-pulse) baseline as an expected file size. The
        # newest gradeable file's beam is the honest choice; 1 when not normalising.
        scale = 1
        if self._beam.available:
            recent_pulses = [r["pulses"] for r in self._hist[-RECENT_FILES:]
                             if r["pulses"]]
            scale = recent_pulses[-1] if recent_pulses else 1

        def counts(recs):
            return {g: sum(1 for r in recs if r["grade"] == g)
                    for g in GRADES + (NO_BEAM,)}

        # Per-run summary for the newest runs (how each run's files graded out).
        run_summary = []
        for run in runs:
            recs = [r for r in self._hist if r["run"] == run]
            if not recs:
                continue
            sizes = sorted(r["size"] for r in recs)
            c = counts(recs)
            # Fractions are over GRADEABLE files: a run half of which had no beam is
            # not '50 % bad'.
            n_graded = max(1, len(recs) - c[NO_BEAM])
            run_summary.append({
                "run": run,
                "n_files": len(recs),
                "median_gib": round(sizes[len(sizes) // 2] / GB, 3),
                "counts": c,
                "frac_reduced": round(c["bad"] / n_graded, 3),
                "frac_questionable": round(c["questionable"] / n_graded, 3),
                "first": recs[0]["t"].isoformat(timespec="seconds"),
                "last": recs[-1]["t"].isoformat(timespec="seconds"),
            })

        return {
            "connected": True,
            "timestamp": now.isoformat(timespec="seconds"),
            "last_error": None,
            "state": state,
            "overall": overall,
            "eos_base": self.eos_base,
            "csv_path": self._csv_path(),
            "poll_s": self.poll_s,
            "reduced_ratio": self.reduced_ratio,
            "questionable_ratio": self.questionable_ratio,
            # The baseline and cuts live in bytes PER PULSE once normalisation is on,
            # which is not comparable to a file size. Publish both: the *_gib fields
            # stay what a file of the latest beam intensity should weigh (so the GUI
            # can put them next to the newest file), and *_per_pulse_gib is the
            # underlying beam-independent quantity.
            "baseline_gib": round(base * scale / GB, 3) if base else None,
            "threshold_gib": round(bad_cut * scale / GB, 3) if bad_cut else None,
            "questionable_gib": round(quest_cut * scale / GB, 3) if quest_cut else None,
            "baseline_per_pulse_gib": (round(base / GB, 5)
                                       if base and self._beam.available else None),
            "baseline_scaled_to_pulses": scale if self._beam.available else None,
            # The benchmark itself: frozen value, what the recent data would suggest
            # instead, and how far the two have drifted apart. A run-configuration
            # change legitimately moves the suggestion, and that is when an operator
            # should re-freeze — so both numbers are always on screen.
            "size_nominal": {
                "frozen": bool(self._size_nominal
                               and self._size_nominal.get("unit") == self._norm_unit()),
                "per_pulse_gib": (round(self._size_nominal["per_pulse_bytes"] / GB, 5)
                                  if self._size_nominal else None),
                "adopted": (self._size_nominal or {}).get("adopted"),
                "source": (self._size_nominal or {}).get("source"),
                "run": (self._size_nominal or {}).get("run"),
                "unit": (self._size_nominal or {}).get("unit"),
                "unit_mismatch": bool(self._size_nominal
                                      and self._size_nominal.get("unit") != self._norm_unit()),
                "suggestion_per_pulse_gib": (round(suggestion / GB, 5)
                                             if suggestion else None),
                "suggestion_gib": (round(suggestion * scale / GB, 3)
                                   if suggestion else None),
                "drift_pct": (round(100 * (suggestion / base - 1), 1)
                              if suggestion and base else None),
                "message": self._size_nominal_message,
            },
            "latest_grade": latest["grade"] if latest else None,
            "latest_run": latest["run"] if latest else None,
            "latest_seq": latest["seq"] if latest else None,
            "latest_size_gib": round(latest["size"] / GB, 3) if latest else None,
            "latest_pulses": latest["pulses"] if latest else None,
            "latest_ratio": (round(latest["norm"] / base, 3)
                             if latest and base and latest["norm"] else None),
            "latest_file_time": latest["t"].isoformat(timespec="seconds") if latest else None,
            "minutes_since_file": (round((now - latest["t"]).total_seconds() / 60.0, 1)
                                   if latest else None),
            "new_files_this_poll": n_new,
            "latest_run_finished": finished,
            "n_files_known": len(self._hist),
            "n_recent": len(recent),
            "recent_counts": counts(recent),
            "n_reduced_recent": counts(recent)["bad"],
            "frac_reduced_recent": (round(counts(recent)["bad"] / len(recent), 3)
                                    if recent else None),
            "episode_start": episode[0].isoformat(timespec="seconds") if episode else None,
            "episode_files": episode[1] if episode else 0,
            "episode_min": (round((self._hist[-1]["t"] - episode[0]).total_seconds() / 60.0, 1)
                            if episode else None),
            "runs": run_summary,
            # Waveform layer: the newest decoded sample, graded per detector against
            # the frozen nominal. None until the first file has been decoded.
            "waveform_enabled": self.waveform_enabled,
            "waveform": self._last_waveform,
            "waveform_error": self._waveform_error,
            # Health of the DECODER itself, as opposed to of the detectors. Spans
            # samples, so it cannot live inside the per-sample `waveform` block: the
            # question "has the missing-channel check been blind for a while?" is not
            # answerable from the newest sample alone.
            "waveform_read": {
                "incomplete_consec": self._waveform_incomplete_consec,
                "incomplete_consec_alert": WAVEFORM_INCOMPLETE_CONSEC,
                "cap_bytes": self._waveform_cap,
                "cap_ceiling_bytes": WAVEFORM_MAX_BYTES_CEILING,
                "cap_at_ceiling": self._waveform_cap >= WAVEFORM_MAX_BYTES_CEILING,
                "largest_event_bytes": self._waveform_event_bytes,
            },
            # Prefix -> why, for the GUI banner. Published unconditionally so the
            # exclusions are visible even before the first sample is decoded.
            "ungraded_prefixes": dict(UNGRADED_PREFIX_REASONS),
            # Beam context: both layers are meaningless without protons on target, so
            # say plainly whether beam data was available and what it showed.
            "beam": {
                "available": self._beam.available,
                "source": "beam_watcher CSV" if self._beam.available else "unavailable",
                "error": self._beam.last_error,
                "seconds_since_pulse": self._beam.seconds_since_pulse(),
                "latest_file_pulses": latest["pulses"] if latest else None,
                "no_beam_recent": sum(1 for r in recent if r["grade"] == NO_BEAM),
                "min_pulses_to_grade": MIN_PULSES_TO_GRADE,
                "normalised": self._beam.available,
            },
            "nominal_message": self._nominal_message,
            "waveform_age_min": (round((time.time() - self._last_waveform_at) / 60.0, 1)
                                 if self._last_waveform_at else None),
            "nominal": self._nominal,
        }

    # ---------------- CSV logging ----------------

    def _csv_path(self, day=None):
        day = day or datetime.now().strftime("%Y-%m-%d")
        return os.path.join(self.log_dir, f"stream1_filesize_{day}.csv")

    def _load_history(self):
        """Seed the trailing window from the newest CSVs so a restart keeps its
        baseline (and does not re-log files already recorded)."""
        hist = []
        try:
            files = sorted(glob.glob(os.path.join(self.log_dir, "stream1_filesize_*.csv")))
            for path in files[-3:]:
                with open(path) as f:
                    for row in csv.DictReader(f):
                        try:
                            # `grade` is absent in rows written by the two-level
                            # version; fall back to the reduced flag there.
                            grade = row.get("grade") or None
                            if grade not in GRADES + (NO_BEAM,):
                                grade = "bad" if row["reduced"] == "1" else "good"
                            size = int(row["size_bytes"])
                            try:
                                pulses = int(row["pulses"])
                            except (TypeError, ValueError, KeyError):
                                pulses = None      # pre-beam-aware row
                            if grade == NO_BEAM:
                                norm = None
                            elif pulses:
                                norm = size / pulses
                            elif self._beam.available:
                                # Row from before beam-awareness: its raw size is in
                                # different units from today's per-pulse norms, so it
                                # must not enter the baseline.
                                norm = None
                            else:
                                norm = float(size)   # no beam data: raw-size grading
                            hist.append({"t": datetime.fromisoformat(row["timestamp"]),
                                         "size": size, "run": int(row["run"]),
                                         "seq": int(row["seq"]), "grade": grade,
                                         "pulses": pulses, "norm": norm})
                        except (ValueError, KeyError):
                            continue
        except Exception as e:
            self.log(f"history seed failed: {e}")
        hist.sort(key=lambda r: (r["t"], r["run"], r["seq"]))
        return hist[-max(BASELINE_WINDOW * 3, 1200):]

    def _log_rows(self, rows):
        """Append rows to the CSV of the day each file was WRITTEN (its EOS mtime),
        so a day's file holds that day's data even when a poll straddles midnight."""
        try:
            os.makedirs(self.log_dir, exist_ok=True)
            by_day = {}
            for r in rows:
                by_day.setdefault(r["timestamp"][:10], []).append(r)
            for day, day_rows in by_day.items():
                path = self._csv_path(day)
                new = not os.path.exists(path)
                if not new:
                    self._migrate_csv(path)
                with open(path, "a", newline="") as f:
                    w = csv.DictWriter(f, fieldnames=self.CSV_FIELDS)
                    if new:
                        w.writeheader()
                    w.writerows(day_rows)
        except Exception as e:
            self.log(f"CSV log failed: {e}")

    # `complete` is per SAMPLE, not per detector, so it repeats down every row of a
    # sample. Worth the duplication: without it the only trace of a truncated read in
    # the history is having to sum n_chan across all 16 detectors and know that the
    # answer should be 51 — which is how the 2026-07-27 false alarm had to be
    # diagnosed after the fact.
    WAVEFORM_CSV_FIELDS = ["timestamp", "run", "seq", "event", "det", "flash",
                           "flash_nominal", "flash_ratio", "zs_blocks", "baseline",
                           "rms", "n_chan", "grade", "complete"]

    def _waveform_csv_path(self, day=None):
        day = day or datetime.now().strftime("%Y-%m-%d")
        return os.path.join(self.log_dir, f"stream1_waveform_{day}.csv")

    def _migrate_waveform_csv(self, path):
        """Add any newly-introduced columns to a day file written by an older column
        set, before appending to it.

        Without this, a DictWriter using the new field list appends rows one column
        wider than the header already in the file, and every reader silently
        mis-attributes the values from the added column onwards. Only ever ADDS
        columns — old rows get an empty cell, which is honest: the watcher that wrote
        them genuinely did not record the value."""
        try:
            with open(path, newline="") as f:
                reader = csv.DictReader(f)
                if reader.fieldnames == self.WAVEFORM_CSV_FIELDS:
                    return
                if not reader.fieldnames or (set(reader.fieldnames)
                                             - set(self.WAVEFORM_CSV_FIELDS)):
                    return          # unknown//reordered layout — leave it alone
                old_rows = list(reader)
            tmp = path + ".tmp"
            with open(tmp, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=self.WAVEFORM_CSV_FIELDS,
                                   extrasaction="ignore")
                w.writeheader()
                w.writerows(old_rows)
            os.replace(tmp, path)
            self.log(f"waveform CSV {os.path.basename(path)} migrated to "
                     f"{len(self.WAVEFORM_CSV_FIELDS)} columns")
        except Exception as e:
            self.log(f"waveform CSV migrate failed: {e}")

    def _log_waveform(self, wf):
        """One row per detector per sampled file — the series the GUI trends."""
        try:
            os.makedirs(self.log_dir, exist_ok=True)
            path = self._waveform_csv_path()
            new = not os.path.exists(path)
            if not new:
                self._migrate_waveform_csv(path)
            with open(path, "a", newline="") as f:
                w = csv.DictWriter(f, fieldnames=self.WAVEFORM_CSV_FIELDS,
                                   extrasaction="ignore")
                if new:
                    w.writeheader()
                for det, v in sorted(wf["detectors"].items()):
                    w.writerow({"timestamp": wf["timestamp"], "run": wf["run"],
                                "seq": wf["seq"], "event": wf["event"], "det": det,
                                "complete": int(bool(wf.get("complete"))),
                                **{k: v.get(k) for k in
                                   ("flash", "flash_nominal", "flash_ratio", "zs_blocks",
                                    "baseline", "rms", "n_chan", "grade")}})
        except Exception as e:
            self.log(f"waveform CSV log failed: {e}")

    def _migrate_csv(self, path):
        """Bring a day file written by an older column set up to CSV_FIELDS before
        appending to it — otherwise the appended rows would not line up with the
        header. Only ever adds columns: `grade` is filled in from `reduced`."""
        try:
            with open(path, newline="") as f:
                reader = csv.DictReader(f)
                if reader.fieldnames == self.CSV_FIELDS:
                    return
                old_rows = list(reader)
            for row in old_rows:
                # Older rows carry a baseline but no cuts; re-derive both cuts and the
                # grade from it so the whole file speaks the current three-level
                # language (this also recovers the questionable band that the
                # two-level version could not express).
                try:
                    base = float(row["baseline_gib"]) * GB
                    size = float(row["size_bytes"])
                except (TypeError, ValueError, KeyError):
                    row.setdefault("grade", "bad" if row.get("reduced") == "1" else "")
                    continue
                quest, bad = self._cuts(base)
                if not row.get("quest_cut_gib"):
                    row["quest_cut_gib"] = round(quest / GB, 4)
                if not row.get("bad_cut_gib"):
                    row["bad_cut_gib"] = round(bad / GB, 4)
                if not row.get("grade"):
                    row["grade"] = self._grade(size, base)
                    row["reduced"] = int(row["grade"] == "bad")
            tmp = path + ".tmp"
            with open(tmp, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=self.CSV_FIELDS, extrasaction="ignore")
                w.writeheader()
                w.writerows(old_rows)
            os.replace(tmp, path)
            self.log(f"migrated {os.path.basename(path)} to the {len(self.CSV_FIELDS)}-column format")
        except Exception as e:
            self.log(f"CSV migration failed for {path}: {e}")

    # ---------------- watcher IPC (state file) ----------------

    def log(self, msg):
        print(f"{datetime.now().strftime('%H:%M:%S')} [stream1_watcher] {msg}", flush=True)

    def _write_state(self, state):
        """Atomically publish the current state for the Flask app to read."""
        try:
            os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
            tmp = self.state_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(state, f)
            os.replace(tmp, self.state_path)   # atomic: readers never see a partial file
        except Exception as e:
            self.log(f"state write failed: {e}")

    def _write_error_state(self):
        self._write_state({
            "connected": False,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "state": "error",
            "eos_base": self.eos_base,
            "csv_path": self._csv_path(),
            "last_error": self.last_error,
        })

    # ---------------- poll loop ----------------

    def run_blocking(self):
        """Poll/log loop in the current thread until SIGINT/SIGTERM. Used by
        stream1_watcher.py."""
        signal.signal(signal.SIGINT, lambda *a: setattr(self, "_stop", True))
        signal.signal(signal.SIGTERM, lambda *a: setattr(self, "_stop", True))
        self.log(f"stream1 watcher starting (poll {self.poll_s:.0f}s, "
                 f"bad < {self.reduced_ratio:.2f} x baseline, "
                 f"{len(self._hist)} files seeded from CSV; waveform layer "
                 + (f"every {self.waveform_min_interval_s:.0f}s, nominal "
                    + ("loaded" if self._nominal else "not yet adopted")
                    if self.waveform_enabled else "disabled") + ")")
        while not self._stop:
            try:
                state = self._poll_once()
                self._write_state(state)
                if state["new_files_this_poll"]:
                    self.log(f"+{state['new_files_this_poll']} files  "
                             f"run {state['latest_run']}  "
                             f"{state['latest_size_gib']} GiB  "
                             f"({state['latest_ratio']}x baseline)  -> {state['state']}")
            except Exception as e:
                # Listing died (expired Kerberos ticket, EOS blip, network). Nothing
                # to reconnect — the next poll retries from scratch.
                self.connected = False
                self.last_error = f"EOS listing failed: {e}"
                self.log(self.last_error)
                self._write_error_state()
            self._sleep(self.poll_s)
        self.log("stream1 file-size watcher stopped")

    def _sleep(self, seconds):
        end = time.time() + seconds
        while not self._stop and time.time() < end:
            time.sleep(0.5)


def main():
    Stream1SizeMonitor().run_blocking()


if __name__ == "__main__":
    main()
