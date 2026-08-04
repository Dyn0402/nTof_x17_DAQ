#!/usr/bin/env python3
"""
DAQ monitor: periodically checks session statuses and sends Telegram alerts.

Rules are defined as methods named rule_<name>(self) -> (alert, str).
  alert : falsy (False/None/"")  = no alert
          True                   = alert at the default "alert" severity
          a severity name        = alert at that severity ("warning", "alert",
                                   "critical", "emergency")
  str   : human-readable description of the current state

Returning a plain bool keeps the old behaviour (True -> "alert"), so existing
rules need no changes. A rule that wants a graded response returns the severity
name instead of True; escalating from one severity to a higher one forces an
immediate re-send even if the resend interval has not elapsed.

To add a rule, add a rule_* method to DaqMonitor.
To disable a rule without deleting it, add  "rule_<name>": false  to the
"rules" dict in monitor_config.json.
"""

import os
import json
import re
import shutil
import threading
import time
from datetime import datetime, timedelta

import requests

from daq_status import (get_dream_daq_status, get_hv_control_status,
                        get_daq_control_status, get_processor_watcher_status,
                        get_qa_watcher_status, get_gas_watcher_status,
                        get_backup_watcher_status,
                        get_beam_watcher_status, get_stream1_watcher_status,
                        get_space_watcher_status, get_n1081b_timetag_watcher_status,
                        get_run_progress, status_field,
                        GAS_STATE_FILE, BEAM_STATE_FILE, STREAM1_STATE_FILE)

TELEGRAM_URL = "https://api.telegram.org/bot{token}/{method}"

_REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The auto-remover's own config. rule_ssd_disk_space anchors its alert levels to
# the same emergency_gb the space_watcher acts on, so retuning the floor from the
# Disk Space tab moves the alert with it — read live each check, never cached.
SPACE_CONFIG_FILE = os.path.join(_REPO_DIR, "config", "space_config.json")


def describe_zeroed(zeroed):
    """Format zeroed channels for an alert, borrowing the watcher's own formatter so
    the Telegram message and the watcher pane read identically.

    Resolved lazily (as _run_dir does in daq_status) because this module is imported
    before app.py puts the repo root on sys.path. Falls back to a plain rendering
    rather than raising: an unavailable import must never be the reason the most
    severe rule in this file fails to describe its fault.
    """
    try:
        import sys
        if _REPO_DIR not in sys.path:
            sys.path.append(_REPO_DIR)
        from stream1_monitor.stream1_size_controller import (
            describe_zeroed as _fmt)
        return _fmt(zeroed)
    except Exception:
        return "; ".join(
            f"{z.get('det')} ch {z.get('chan')} {z.get('kind')}" for z in zeroed)

# Sentinel distinguishing "no per-rule resend override in config" from an
# override explicitly set to null (which means "never repeat").
_UNSET = object()

# Alert severities, lowest to highest. rank drives escalation (a higher rank
# than the last sent forces an immediate re-send); emoji/label drive the message.
# "info" is for event rules that report a normal, expected thing happening (a
# beam<->cosmics changeover) rather than a fault — it must not look like a problem
# in the phone notification, which is the whole reason it is not just "warning".
SEVERITY_META = {
    "info":      {"rank": 0, "emoji": "ℹ️", "label": "INFO"},
    "warning":   {"rank": 1, "emoji": "🟡", "label": "WARNING"},
    "alert":     {"rank": 2, "emoji": "⚠️", "label": "ALERT"},
    "critical":  {"rank": 3, "emoji": "🔴", "label": "CRITICAL"},
    "emergency": {"rank": 4, "emoji": "🚨", "label": "EMERGENCY"},
}


def _severity_rank(severity):
    return SEVERITY_META.get(severity, SEVERITY_META["alert"])["rank"]


def normalize_alert(ret):
    """Turn a rule's (alert, detail) return into (severity|None, detail).

    falsy alert -> None (no alert); True -> "alert"; a known severity name ->
    itself; any other truthy value -> "alert" (fail safe, still notifies)."""
    alert, detail = ret
    if not alert:
        return None, detail
    if alert is True:
        return "alert", detail
    sev = str(alert).lower()
    return (sev if sev in SEVERITY_META else "alert"), detail


# ---------------------------------------------------------------------------
# Telegram helpers
# ---------------------------------------------------------------------------

def send_telegram(token, chat_id, text):
    """Send a message. Returns (success: bool, error: str|None)."""
    try:
        r = requests.post(
            TELEGRAM_URL.format(token=token, method="sendMessage"),
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        r.raise_for_status()
        return True, None
    except Exception as e:
        return False, str(e)


def fetch_chat_id(token):
    """Return the chat_id from the most recent message sent to the bot.

    The user must send any message (e.g. /start) to the bot before this works.
    Returns (chat_id: int|None, error: str|None).
    """
    try:
        r = requests.get(
            TELEGRAM_URL.format(token=token, method="getUpdates"),
            timeout=10,
        )
        r.raise_for_status()
        updates = r.json().get("result", [])
        if not updates:
            return None, "No messages received yet — send any message to the bot first."
        chat_id = updates[-1]["message"]["chat"]["id"]
        return chat_id, None
    except Exception as e:
        return None, str(e)


def get_bot_username(token):
    """Return the bot's @username. Returns (username: str|None, error: str|None)."""
    try:
        r = requests.get(
            TELEGRAM_URL.format(token=token, method="getMe"),
            timeout=10,
        )
        r.raise_for_status()
        return r.json()["result"]["username"], None
    except Exception as e:
        return None, str(e)


# ---------------------------------------------------------------------------
# Monitor
# ---------------------------------------------------------------------------

class DaqMonitor:
    # Edge/event rules fire once on a transition and clear on the very next check.
    # They must NOT emit a "RECOVERED" message (there is nothing to recover from) and
    # their resend gap is irrelevant because they self-clear. See rule_run_ended /
    # rule_long_run_warning.
    _EVENT_RULES = {"rule_run_ended", "rule_long_run_warning", "rule_mode_changeover"}

    # Per-rule recovery text, shown after the ✅ when an alert condition clears.
    # (The alert body itself is the rule's own returned `detail`, already tailored;
    # recovery has no detail, so it needs a tailored line of its own here.)
    # A rule with no entry falls back to "<rule label> is back to normal."
    _RECOVERY_MESSAGES = {
        "rule_dream_daq_session_dead": "dream_daq tmux session is back up.",
        "rule_daq_control_session_dead": "daq_control tmux session is back up.",
        "rule_no_active_run": "a run is active again — the DAQ is taking data.",
        "rule_hv_control_monitoring": "hv_control is monitoring HV again.",
        "rule_dream_daq_unknown_state": "dream_daq state is back to normal.",
        "rule_daq_control_unknown_state": "daq_control state is back to normal.",
        "rule_gas_watcher_dead": "gas_watcher is back up.",
        "rule_beam_watcher_dead": "beam_watcher is back up.",
        "rule_beam_off": "n_TOF beam is back.",
        "rule_stream1_watcher_dead": "stream1_watcher is back up.",
        "rule_backup_watcher_dead": "backup_watcher is back up — runs are reaching EOS again.",
        "rule_n1081b_tt_stream_dead": "N1081B trigger-timestamp stream is logging again.",
        "rule_processor_watcher_dead": "processor_watcher is back up.",
        "rule_qa_watcher_dead": "qa_watcher is back up.",
        "rule_space_watcher_dead": "space_watcher is back up — the SSD is being pruned again.",
        "rule_space_watcher_stuck": "SSD space recovered — runs are reaching EOS and can be pruned again.",
        "rule_stream1_files_reduced": "n_TOF stream1 file sizes are back to the full level.",
        "rule_stream1_detector_gain": "n_TOF detector gains are back at nominal.",
        "rule_stream1_zeroed_channels": (
            "n_TOF wall/liquid/plastic channels are producing waveforms again."),
        "rule_stream1_waveform_incomplete": (
            "n_TOF waveform decoder is reading complete events again — "
            "missing-channel detection is re-armed."),
        "rule_gas_flow_starved": "gas flow is back to normal.",
        "rule_ssd_disk_space": "SSD (/) free space is back above the auto-remover's emergency level.",
        "rule_hdd_disk_space": "HDD (/mnt/data) disk space is back to normal.",
        "rule_feu_unreachable": "All FEUs and the TCM are answering again. Verify the "
                                "next sub-run is full-size before trusting the data.",
        "rule_subrun_no_data": "Sub-runs are recording data again.",
    }

    # Presentational grouping for the Setup panel's rule list, ordered. Purely a
    # display concern (list_rules tags each rule with its section); it does not
    # affect which rules run. Any rule not named here falls into a trailing "Other"
    # section, so adding a rule method never silently hides it from the UI.
    _RULE_SECTIONS = [
        ("DAQ processes", [
            "rule_dream_daq_session_dead",
            "rule_daq_control_session_dead",
            "rule_hv_control_monitoring",
            "rule_dream_daq_unknown_state",
            "rule_daq_control_unknown_state",
        ]),
        ("DREAM crate & data integrity", [
            "rule_feu_unreachable",
            "rule_subrun_no_data",
        ]),
        ("Run lifecycle", [
            "rule_no_active_run",
            "rule_run_ended",
            "rule_long_run_warning",
            "rule_mode_changeover",
        ]),
        ("Background watchers", [
            "rule_gas_watcher_dead",
            "rule_beam_watcher_dead",
            "rule_stream1_watcher_dead",
            "rule_backup_watcher_dead",
            "rule_processor_watcher_dead",
            "rule_qa_watcher_dead",
            "rule_space_watcher_dead",
            "rule_space_watcher_stuck",
            "rule_n1081b_tt_stream_dead",
        ]),
        ("Disk space", [
            "rule_ssd_disk_space",
            "rule_hdd_disk_space",
        ]),
        ("Beam & data quality", [
            "rule_beam_off",
            "rule_gas_flow_starved",
            "rule_stream1_files_reduced",
            "rule_stream1_detector_gain",
            "rule_stream1_zeroed_channels",
            "rule_stream1_waveform_incomplete",
        ]),
    ]

    def __init__(self, config_path):
        self.config_path = config_path
        self.config = self._load_config()

        # State for the edge/event rules (see _EVENT_RULES).
        self._prev_daq_status = None    # last daq_control status seen by rule_run_ended
        self._long_run_warned = set()   # run names already given the 10-min warning
        self._last_beam_off_severity = None  # held verdict, see rule_beam_off / _beam_off_hold
        # rule_mode_changeover: last trigger mode seen live, and the last auto-changeover
        # timestamp already accounted for. _mode_primed guards the first check, so a DAQ
        # found mid-cosmics at monitor start is not announced as a fresh changeover.
        self._prev_run_mode = None
        self._last_changeover_ts = None
        self._attributed_changeover_ts = None  # spent explaining a transition already
        self._mode_primed = False

        self._thread = None
        self._stop_event = threading.Event()

        # Per-rule state
        self._alert_active = {}    # rule_name → bool (condition currently met past min_dur)
        self._alert_notified = {}  # rule_name → bool (a 🔴 alert was actually DELIVERED this
                                   # episode; gates the ✅ recovery so we never announce a
                                   # recovery for an alert the user was never sent)
        self._alert_severity = {}  # rule_name → severity name of the last alert sent
        self._alert_sent_at = {}   # rule_name → datetime
        self._pending_since = {}   # rule_name → datetime | None (condition first went True)

        self.last_check_time = None
        self.last_alert_time = None

        # Restore enabled state from config
        self.enabled = self.config.get("enabled", False)
        if self.enabled:
            self.start(save=False)

    # ---------------------------------------------------------------
    # Config
    # ---------------------------------------------------------------

    def _load_config(self):
        if os.path.exists(self.config_path):
            with open(self.config_path) as f:
                return json.load(f)
        return {}

    def save_config(self):
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        self.config["enabled"] = self.enabled
        with open(self.config_path, "w") as f:
            json.dump(self.config, f, indent=2)

    @property
    def token(self):
        return self.config.get("telegram_token")

    @property
    def chat_id(self):
        return self.config.get("telegram_chat_id")

    def set_chat_id(self, chat_id):
        self.config["telegram_chat_id"] = chat_id
        self.save_config()

    @property
    def check_interval(self):
        return self.config.get("check_interval_seconds", 60)

    @property
    def fast_check_interval(self):
        """Cadence of the FAST lane (see fast_rules). None/0 disables it.

        Config carries **5 s** (`fast_check_interval_seconds`, monitor_config.json —
        which is gitignored, so this is where the number is recorded). It is the
        cheapest term in the beam-off alert's latency budget, measured 2026-07-31:

            55 s  the pulse gap itself, set by the machine's supercycle structure
                  (rule_beam_off.early_gap_seconds — not ours to shrink)
          + 18 s  NXCALS ingestion, i.e. how old the newest logged point is when we
                  fetch it. MEASURED over 14 polls: 13.0 / 17.9 / 22.8 s
                  (min/median/max). NXCALS is a logging database — this is a FLOOR,
                  and polling harder does not move it.
          +  6 s  poll phase, mean of beam_watcher's POLL_S=12 s
          +  2 s  this lane, mean of 5 s   <-- the only term that is free to us
          ------
           ~81 s  typical, against ~103 s for the wall-clock-only rule it replaced

        Dropping POLL_S 12 -> 4 s would buy ~4 s more for 3x the query rate against a
        shared CERN service; judged not worth it. Anything below ~77 s needs a
        different SOURCE, not a faster poll: a CMW/JAPC live subscription (ms latency,
        but Technical Network + RBAC, and this host is on the GPN), or our own
        detectors (the M5/.244 timetag stream is already live 24/7). Neither is built.
        """
        return self.config.get("fast_check_interval_seconds") or None

    @property
    def fast_rules(self):
        """Rules cheap enough to run between full passes — file reads only, no
        pinging and no tmux sweeps. A full pass costs ~10 s (rule_feu_unreachable
        alone pings 9 boards serially), which is why the whole set cannot simply be
        run more often. Config carries rule_beam_off + rule_mode_changeover, together
        3.8 ms a pass (the beam rules read one JSON file; the changeover rule reads
        /proc, 4.3 ms for the scan) — which is what makes a 5 s lane free."""
        if not self.fast_check_interval:
            return []
        names = self.config.get("fast_rules", ["rule_beam_off"])
        known = set(self._rule_names())
        return [n for n in names if n in known]

    @property
    def default_resend_minutes(self):
        """Raw resend_interval_minutes config value (None means repeats are
        disabled by default)."""
        return self.config.get("resend_interval_minutes", 30)

    @property
    def resend_interval(self):
        """Seconds between repeated alerts, used by rules with no per-rule
        override. None means repeats are disabled by default (a re-send only
        happens if the alert's severity changes, e.g. warning -> critical or
        critical -> warning; see `escalated` in _check_all_rules)."""
        minutes = self.config.get("resend_interval_minutes", 30)
        return minutes * 60 if minutes is not None else None

    def _is_rule_enabled(self, name):
        return self.config.get("rules", {}).get(name, True)

    def _rule_names(self):
        return sorted(
            name for name in dir(self)
            if name.startswith("rule_") and callable(getattr(self, name))
        )

    def list_rules(self):
        """Return [{name, label, description, enabled, section}] for every rule method,
        ordered and tagged by section per _RULE_SECTIONS (the Setup panel groups on it).

        `description` is the first paragraph of the rule method's docstring,
        collapsed to a single line.
        """
        def entry(name, section):
            doc = (getattr(self, name).__doc__ or "").strip()
            # First blank-line-delimited paragraph, whitespace-collapsed.
            first_para = doc.split("\n\n", 1)[0]
            description = " ".join(first_para.split())
            label = name[len("rule_"):].replace("_", " ")

            raw = self._rule_resend_minutes_raw(name)
            if raw is _UNSET:
                resend_mode = "default"
                resend_minutes = None
            elif raw is None:
                resend_mode = "never"
                resend_minutes = None
            else:
                resend_mode = "minutes"
                resend_minutes = raw
            effective_secs = self._rule_resend_interval_secs(name)

            return {
                "name": name,
                "label": label,
                "description": description,
                "section": section,
                "enabled": self._is_rule_enabled(name),
                "resend_mode": resend_mode,
                "resend_minutes": resend_minutes,
                "resend_effective_minutes": (
                    None if effective_secs is None else effective_secs / 60
                ),
            }

        # Emit rules in section order; anything not placed in a section trails under
        # "Other" so a newly added rule method is never dropped from the panel.
        all_names = set(self._rule_names())
        rules, placed = [], set()
        for section, names in self._RULE_SECTIONS:
            for name in names:
                if name in all_names and name not in placed:
                    rules.append(entry(name, section))
                    placed.add(name)
        for name in sorted(all_names - placed):
            rules.append(entry(name, "Other"))
        return rules

    def set_rule_enabled(self, name, enabled):
        """Enable/disable a single rule and persist. Returns (ok, error)."""
        if name not in self._rule_names():
            return False, f"Unknown rule: {name}"
        self.config.setdefault("rules", {})[name] = bool(enabled)
        # Clear any live alert state so a just-disabled rule stops nagging (and does
        # not fire a recovery on disable) and a re-enabled one starts fresh rather than
        # firing on stale state.
        self._alert_active.pop(name, None)
        self._alert_notified.pop(name, None)
        self._alert_severity.pop(name, None)
        self._alert_sent_at.pop(name, None)
        self._pending_since.pop(name, None)
        self.save_config()
        return True, None

    def _rule_min_duration(self, name):
        """Seconds the condition must be True before an alert is sent (default 0)."""
        return self.config.get("rule_options", {}).get(name, {}).get("min_duration_seconds", 0)

    def _rule_resend_minutes_raw(self, name):
        """This rule's raw resend_minutes override: a number, None (explicitly
        set to "never repeat"), or _UNSET (no override -> use the global default)."""
        return self.config.get("rule_options", {}).get(name, {}).get("resend_minutes", _UNSET)

    def _rule_resend_interval_secs(self, name, severity=None):
        """Seconds between repeated alerts for this rule, or None if repeats are
        disabled (a re-send only happens on recovery+re-trigger, or on a severity
        change for graded rules — see `escalated` in _check_all_rules). Falls back
        to the global resend_interval when the rule has no override.

        A graded rule can nag at its high tiers without nagging at its low ones via
        rule_options.<rule>.resend_minutes_by_severity, e.g.
        {"critical": 30, "emergency": 15} alongside a base "resend_minutes": null —
        "stay quiet at warning/alert, but keep reminding me once it is critical".
        Keys are severity names; a key set to null disables repeats for that tier.
        Only consulted when `severity` is given (the Setup panel asks without one and
        gets the rule's base interval, which is what it displays)."""
        if severity is not None:
            by_sev = self.config.get("rule_options", {}).get(name, {}).get(
                "resend_minutes_by_severity") or {}
            if severity in by_sev:
                minutes = by_sev[severity]
                return None if minutes is None else minutes * 60
        minutes = self._rule_resend_minutes_raw(name)
        if minutes is _UNSET:
            return self.resend_interval
        if minutes is None:
            return None
        return minutes * 60

    def set_rule_resend(self, name, mode, minutes=None):
        """Set a rule's repeat behavior. mode is one of:
          "default" - use the global resend_interval (clears any override)
          "never"   - no periodic re-sends; still re-notifies immediately on a
                      severity change (graded rules) or on recovery+re-trigger
          "minutes" - repeat every `minutes` minutes (must be > 0)
        Returns (ok, error)."""
        if name not in self._rule_names():
            return False, f"Unknown rule: {name}"
        opts = self.config.setdefault("rule_options", {}).setdefault(name, {})
        if mode == "default":
            opts.pop("resend_minutes", None)
        elif mode == "never":
            opts["resend_minutes"] = None
        elif mode == "minutes":
            try:
                minutes = float(minutes)
            except (TypeError, ValueError):
                return False, "minutes must be a number"
            if minutes <= 0:
                return False, "minutes must be positive"
            opts["resend_minutes"] = minutes
        else:
            return False, f"Unknown mode: {mode}"
        self.save_config()
        return True, None

    # ---------------------------------------------------------------
    # Thread control
    # ---------------------------------------------------------------

    def start(self, save=True):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self.enabled = True
        if save:
            self.save_config()
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True, name="daq-monitor")
        self._thread.start()
        print("[monitor] Started")

    def stop(self, save=True):
        self.enabled = False
        if save:
            self.save_config()
        self._stop_event.set()
        print("[monitor] Stopped")

    def toggle(self):
        if self.is_running:
            self.stop()
        else:
            self.start()

    @property
    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    # ---------------------------------------------------------------
    # Monitor loop
    # ---------------------------------------------------------------

    def _monitor_loop(self):
        # Two cadences: every rule on check_interval, plus the cheap fast_rules on
        # fast_check_interval in between (a full pass is too expensive to run at the
        # fast rate — see the fast_rules docstring). The first pass is always full.
        next_full = 0.0
        while not self._stop_event.is_set():
            fast = self.fast_rules
            due_full = time.monotonic() >= next_full
            try:
                self._check_all_rules(only=None if due_full else fast)
            except Exception as e:
                print(f"[monitor] Unhandled error in check loop: {e}")
            if due_full:
                next_full = time.monotonic() + self.check_interval
            wait = self.fast_check_interval if fast else self.check_interval
            self._stop_event.wait(max(1.0, wait))

    def _check_all_rules(self, only=None):
        """Run the rule set. `only` restricts it to those rule names (the fast lane);
        None runs everything."""
        self.last_check_time = datetime.now()

        rules = {
            name: getattr(self, name)
            for name in sorted(dir(self))
            if name.startswith("rule_") and callable(getattr(self, name))
        }

        for name, fn in rules.items():
            if only is not None and name not in only:
                continue
            if not self._is_rule_enabled(name):
                continue
            try:
                severity, detail = normalize_alert(fn())
            except Exception as e:
                print(f"[monitor] Rule {name} raised: {e}")
                continue

            is_alert = severity is not None
            was_alert = self._alert_active.get(name, False)
            last_sent = self._alert_sent_at.get(name)
            now = datetime.now()

            if is_alert:
                # Record when the condition first became True
                if self._pending_since.get(name) is None:
                    self._pending_since[name] = now

                elapsed = (now - self._pending_since[name]).total_seconds()
                min_dur = self._rule_min_duration(name)

                if elapsed >= min_dur:
                    prev_severity = self._alert_severity.get(name)
                    # Keyed on the CURRENT severity, so a rule that is silent at
                    # warning starts nagging the moment it escalates to critical.
                    resend_secs = self._rule_resend_interval_secs(name, severity)
                    # resend_secs is None -> repeats disabled: only the first send
                    # in an episode is due; escalation (below) can still resend.
                    resend_due = last_sent is None or (
                        resend_secs is not None and (now - last_sent).total_seconds() > resend_secs
                    )
                    # Escalating to a higher severity notifies immediately, so a
                    # slow slide (warning → critical) is not masked by the resend gap.
                    escalated = severity != prev_severity
                    if not was_alert or resend_due or escalated:
                        self._send_alert(name, detail, severity)
                    self._alert_active[name] = True
                    self._alert_severity[name] = severity
                else:
                    pending_remaining = int(min_dur - elapsed)
                    print(f"[monitor] {name} in alert state — waiting {pending_remaining}s more before alerting.")
            else:
                # Only announce recovery if a 🔴 alert was actually DELIVERED for this
                # rule this episode. A "✅ back to normal" with no preceding alert is just
                # noise — it happens when the send failed, when a graded rule stood down
                # to another rule without ever alerting (e.g. rule_ssd_disk_space handing
                # off to space_watcher), or on fresh state after a monitor restart. Event
                # rules self-clear each check, so a recovery for them is always spurious.
                if self._alert_notified.get(name) and name not in self._EVENT_RULES:
                    self._send_recovery(name)
                self._alert_notified.pop(name, None)
                self._alert_active[name] = False
                self._alert_severity.pop(name, None)
                self._pending_since[name] = None  # reset pending timer

    # ---------------------------------------------------------------
    # Sending
    # ---------------------------------------------------------------

    def _send_alert(self, rule_name, detail, severity="alert"):
        if not self.token or not self.chat_id:
            print(f"[monitor] Alert triggered ({rule_name}) but Telegram not configured.")
            return
        meta = SEVERITY_META.get(severity, SEVERITY_META["alert"])
        msg = f"{meta['emoji']} {detail}"
        ok, err = send_telegram(self.token, self.chat_id, msg)
        if ok:
            self._alert_sent_at[rule_name] = datetime.now()
            self._alert_notified[rule_name] = True   # arms the ✅ recovery for this episode
            self.last_alert_time = datetime.now()
            print(f"[monitor] Alert sent: {rule_name} — {detail}")
        else:
            print(f"[monitor] Failed to send alert for {rule_name}: {err}")

    def _send_recovery(self, rule_name):
        if not self.token or not self.chat_id:
            return
        label = rule_name[len("rule_"):].replace("_", " ")
        detail = self._RECOVERY_MESSAGES.get(rule_name, f"{label} is back to normal.")
        msg = f"✅ {detail}"
        ok, err = send_telegram(self.token, self.chat_id, msg)
        if ok:
            print(f"[monitor] Recovery sent: {rule_name}")
        else:
            print(f"[monitor] Failed to send recovery for {rule_name}: {err}")

    def send_test_alert(self):
        if not self.token or not self.chat_id:
            return False, "Telegram token or chat_id not configured."
        msg = "🔔 DAQ monitor test — monitoring is active."
        ok, err = send_telegram(self.token, self.chat_id, msg)
        return ok, err

    # ---------------------------------------------------------------
    # Status summary (for the UI)
    # ---------------------------------------------------------------

    def status_dict(self):
        # Annotate each active alert with its severity label, highest first, so the
        # UI strip reads e.g. "ssd disk space [CRITICAL]". Kept as plain strings so
        # the existing join()-based rendering still works.
        active_pairs = sorted(
            ((name, self._alert_severity.get(name, "alert"))
             for name, v in self._alert_active.items() if v),
            key=lambda p: _severity_rank(p[1]), reverse=True,
        )
        active = [
            f"{name[len('rule_'):].replace('_', ' ')} [{SEVERITY_META.get(sev, SEVERITY_META['alert'])['label']}]"
            for name, sev in active_pairs
        ]
        return {
            "running": self.is_running,
            "enabled": self.enabled,
            "chat_id_set": self.chat_id is not None,
            "chat_id": self.chat_id,
            "check_interval": self.check_interval,
            "last_check": self.last_check_time.strftime("%H:%M:%S") if self.last_check_time else None,
            "last_alert": self.last_alert_time.strftime("%Y-%m-%d %H:%M:%S") if self.last_alert_time else None,
            "active_alerts": active,
        }

    # ---------------------------------------------------------------
    # Rules
    # ---------------------------------------------------------------

    def rule_dream_daq_session_dead(self):
        """Alert if the dream_daq tmux session is not running at all."""
        info = get_dream_daq_status()
        fields_str = str(info.get("fields", ""))
        if info["color"] == "danger" and "tmux not running" in fields_str:
            return True, "dream_daq tmux session is not running."
        return False, f"dream_daq: {info['status']}"

    def rule_daq_control_session_dead(self):
        """Alert if the daq_control tmux session is not running at all."""
        info = get_daq_control_status()
        fields_str = str(info.get("fields", ""))
        if info["color"] == "danger" and "tmux not running" in fields_str:
            return True, "daq_control tmux session is not running."
        return False, f"daq_control: {info['status']}"

    def rule_hv_control_monitoring(self):
        """Alert if hv_control is not actively monitoring HV (dead, off, or unknown)."""
        info = get_hv_control_status()
        ok_statuses = {"Monitoring HV", "HV Ramped", "Ramping HV"}
        if info["status"] not in ok_statuses:
            return True, f"hv_control is not monitoring HV — status: {info['status']}"
        return False, f"hv_control: {info['status']}"

    def rule_dream_daq_unknown_state(self):
        """Alert if dream_daq is running but in an unrecognised state."""
        info = get_dream_daq_status()
        if info["status"] == "UNKNOWN STATE":
            return True, "dream_daq is in UNKNOWN STATE — check the terminal."
        return False, f"dream_daq: {info['status']}"

    def rule_daq_control_unknown_state(self):
        """Alert if daq_control is running but in an unrecognised state."""
        info = get_daq_control_status()
        if info["status"] == "UNKNOWN STATE":
            return True, "daq_control is in UNKNOWN STATE — check the terminal."
        return False, f"daq_control: {info['status']}"

    def rule_gas_watcher_dead(self):
        """Alert if the gas-mixer watcher is not running or not producing fresh,
        connected readings — in that state gas logging AND flow control are down,
        and rule_gas_flow_starved below is blind (no data to judge)."""
        info = get_gas_watcher_status()
        status = info["status"]
        if status == "STOPPED":
            return True, "gas_watcher is not running — gas logging and flow control are down."
        if status == "No Gas":
            return True, "gas_watcher is up but the flow controllers are not connected."
        if status == "Stale":
            return True, "gas_watcher is not publishing fresh readings (state file is stale)."
        return False, f"gas_watcher: {status}"

    def rule_beam_watcher_dead(self):
        """Alert if the beam-intensity watcher is not running or not producing fresh
        NXCALS data (dead session, failing queries, or an expired Kerberos ticket) —
        in that state beam logging is down and rule_beam_off below is blind."""
        info = get_beam_watcher_status()
        status = info["status"]
        if status == "STOPPED":
            return True, "beam_watcher is not running — beam intensity logging is down."
        if status == "No NXCALS":
            return True, ("beam_watcher is up but NXCALS queries are failing — "
                          "likely an expired Kerberos ticket (kinit on the DAQ PC).")
        if status == "Stale":
            return True, "beam_watcher is not publishing fresh data (state file is stale)."
        return False, f"beam_watcher: {status}"

    def rule_beam_off(self):
        """Warn when the n_TOF beam has been off for a while (no proton pulse on
        target). Purely informational — beam availability is the facility's doing,
        not ours — but shifters want to know without watching the vistar. Graded:
        escalates to a higher severity the longer the beam stays off.

        Two independent detectors, because they fail in opposite ways:

        1. EARLY, on `observed_gap_s` — the gap as the DATA sees it (last logged cycle
           minus last real pulse), which carries NO wall-clock term and therefore none
           of the NXCALS ingestion delay. This is what fires first in practice.
        2. BACKSTOP, on `seconds_since_pulse` (wall clock) via `thresholds`. Needed
           because in a genuine stop the point stream itself can dry up (TOF out of the
           supercycle), which FREEZES observed_gap_s — detector 1 would then never
           cross. Also the only detector that can ever say "beam is back".

        Tunable via rule_options.rule_beam_off in monitor_config.json:
          early_gap_seconds — data-axis gap that counts as "beam down", at the lowest
                       severity in `thresholds`. Default 55; 0/null disables the early
                       path. See the margin discussion below before changing it.
          thresholds — {severity: off_minutes} gradient on the WALL-CLOCK gap, e.g.
                       {"warning": 1.5, "alert": 10}. The highest severity whose
                       minute threshold has been reached wins. Default: {"warning":
                       10} (must be comfortably above normal supercycle gaps and
                       NXCALS latency).
          off_minutes — legacy single-level form (used only if "thresholds" is
                        absent): pulse gap that counts as "beam down" at "warning".

        Do NOT set the wall-clock threshold below ~1.25 min. seconds_since_pulse
        carries the delay before a pulse becomes visible to us (NXCALS ingestion + poll
        phase, 11-23 s measured) on top of the real gap; with the longest healthy
        in-beam gap at 38.4 s (134 h sample, 2026-07-27) a running beam can legitimately
        read ~62 s.

        early_gap_seconds has no such latency term, so it can sit much closer to the
        real structure — that is the whole point of it. Re-measured 2026-07-31 over the
        full month of CSVs (731 h, 587 k pulses): the longest gap with the beam still
        running is 44.4 s, four gaps fall in 41-46 s, and the next one up is 48.4 s.
        55 s therefore clears the observed structural tail by ~11 s. Cost of the early
        path, same sample: gaps that cross 55 s but recover within 3 min run 1.94/day,
        against 1.54/day already crossing the current 80 s-equivalent boundary — i.e.
        it buys ~40 s of warning for ~0.4 extra self-clearing alerts per day.
        ⚠ Re-measure (beam_monitor/analyze_gap_band.py) before lowering it further: the
        band below 48 s belongs to the machine's supercycle structure, not to us.

        ⚠ A missing/stale reading is held at its last known severity via
        _beam_off_hold, NEVER treated as "recovered". 2026-07-29: beam_watcher.py
        restarted mid-episode (its own NXCALS session churn) and briefly wrote
        seconds_since_pulse: null before its first fresh pulse — with a bare `return
        False` there, that read as "beam on" and fired a false "beam is back" the
        moment it landed, clearing a real, still-active beam-off alert. A data gap is
        not evidence beam came back — same principle as mode_watcher.decide()'s
        beam == 'UNKNOWN' handling ("a glitch must not trigger a changeover").
        """
        opts = self.config.get("rule_options", {}).get("rule_beam_off", {})
        thresholds = opts.get("thresholds") or {"warning": opts.get("off_minutes", 10)}
        early_gap_s = opts.get("early_gap_seconds", 55)
        try:
            with open(BEAM_STATE_FILE) as f:
                st = json.load(f)
        except Exception:
            return self._beam_off_hold("beam state not available (watcher not running)")
        if not st.get("connected"):
            return self._beam_off_hold(
                "beam watcher disconnected (covered by rule_beam_watcher_dead)")
        gap = st.get("seconds_since_pulse")
        if gap is None:
            return self._beam_off_hold("no beam pulse seen yet since the watcher started")
        gap_min = gap / 60

        # Highest severity whose minute threshold has been reached (wall-clock backstop).
        ladder = sorted(thresholds.items(), key=lambda kv: float(kv[1]))
        severity = None
        for sev, thr in ladder:
            if gap_min >= float(thr):
                severity = sev

        # Early, latency-free detector. Only ever ADDS the lowest severity: a frozen
        # observed_gap_s must not be able to escalate, and must never clear anything.
        observed = st.get("observed_gap_s")
        early = (severity is None and early_gap_s and ladder
                 and isinstance(observed, (int, float)) and observed >= float(early_gap_s))
        if early:
            severity = ladder[0][0]

        self._last_beam_off_severity = severity
        if severity:
            if early:
                head = (f"n_TOF beam has STOPPED — no pulse for {observed:.0f} s "
                        f"(data gap; wall clock {gap:.0f} s).")
            else:
                head = f"n_TOF beam has been OFF for {gap_min:.0f} min."
            # No changeover/cosmics commentary here, at any severity (2026-07-31, on
            # the first alert seen in the field). This rule says exactly one thing —
            # the beam is gone — and rule_mode_changeover says exactly one thing back
            # when the DAQ swaps and again when it swaps home. Mixing "what the watcher
            # might do" into a repeating condition alert was noise on both messages.
            return severity, head
        return False, (f"beam on (last pulse {gap:.0f}s ago"
                       + (f", data gap {observed:.0f}s" if isinstance(observed, (int, float)) else "")
                       + ")")

    def _beam_off_hold(self, reason):
        """Replay rule_beam_off's last known severity instead of clearing it when the
        current reading is missing/stale/disconnected — see the warning in its
        docstring. Returns (False, reason) if there was nothing active to hold."""
        sev = self._last_beam_off_severity
        if sev:
            return sev, f"n_TOF beam still OFF (unconfirmed: {reason})"
        return False, reason

    def rule_stream1_watcher_dead(self):
        """Alert if the stream1 file-size watcher is not running or not producing fresh
        EOS listings — in that state rule_stream1_files_reduced below is blind. 'No EOS'
        is usually an expired Kerberos ticket, the same one the backup watcher needs."""
        info = get_stream1_watcher_status()
        status = info["status"]
        if status == "STOPPED":
            return True, "stream1_watcher is not running — SiPM-wall dropout monitoring is down."
        if status == "No EOS":
            return True, ("stream1_watcher is up but EOS listings are failing — "
                          "likely an expired Kerberos ticket (kinit on the DAQ PC).")
        if status == "Stale":
            return True, "stream1_watcher is not publishing fresh state (state file is stale)."
        return False, f"stream1_watcher: {status}"

    def rule_n1081b_tt_stream_dead(self):
        """Alert if the Module-5 trigger-timestamp stream is down or has given up.

        The supervisor Telegrams its own stalls and stops, but it cannot report the
        cases that matter most — the process itself dying (host reboot, OOM, someone
        killing the tmux session) — and there is no supervisor-of-the-supervisor. Note
        `Resting` is deliberately NOT an alert: a 50 min rest is the recovery policy
        working, and the supervisor already sent a message when it began.

        Data exposure is real but bounded: .244 is monitoring-only and never in the
        trigger, so a dead stream costs trigger timestamps for DREAM matching, not
        physics data."""
        info = get_n1081b_timetag_watcher_status()
        status = info["status"]
        if status == "STOPPED":
            return True, ("N1081B trigger-timestamp stream is not running — no per-trigger "
                          "timestamps are being logged (.244 is monitoring-only, so the run "
                          "itself is unaffected). Restart from the Module-5 tab.")
        if status == "ALARM":
            return True, ("N1081B trigger-timestamp stream STOPPED itself after repeated "
                          "failures — leave .244 alone and check logs/n1081b_tt_stream.log.")
        if status == "Stale":
            return True, ("N1081B trigger-timestamp stream is up but has stopped publishing "
                          "state — the supervisor may be hung.")
        if status == "No Board":
            return True, "N1081B trigger-timestamp stream cannot reach .244."
        return False, f"n1081b tt stream: {status}"

    def rule_backup_watcher_dead(self):
        """Alert if the EOS backup watcher is not running, or is up but failing to
        authenticate or transfer — in that state completed runs stop reaching EOS and
        only the local copies exist.

        Added 2026-07-22: this watcher is not part of the boot script's original set, so
        every reboot left it down with nothing noticing. Of the GUI-started watchers it
        is the one with real data-loss exposure, so it alerts by default."""
        info = get_backup_watcher_status()
        status = info["status"]
        if status == "STOPPED":
            return True, "backup_watcher is not running — completed runs are NOT reaching EOS."
        if status == "Auth Error":
            return True, ("backup_watcher cannot authenticate to EOS — expired/broken Kerberos "
                          "ticket (kinit -kt ~/.keytab/mx17_cern.keytab dneff@CERN.CH).")
        if status == "rsync Error":
            return True, "backup_watcher's last transfer FAILED — runs may not be reaching EOS."
        if status == "Waiting for Dir":
            return True, "backup_watcher is up but its source directory does not exist."
        return False, f"backup_watcher: {status}"

    def rule_processor_watcher_dead(self):
        """Alert if the decoding/processing watcher is not running — decoded output and
        everything downstream of it (online QA) silently stops advancing.

        Disabled by default: this watcher is legitimately stopped by hand during studies,
        and its failure only delays work rather than losing data. Enable it in the GUI if
        you want a reboot/crash backstop."""
        info = get_processor_watcher_status()
        status = info["status"]
        if status == "STOPPED":
            return True, "processor_watcher is not running — runs are not being decoded."
        if status == "Waiting for Dir":
            return True, "processor_watcher is up but its runs directory does not exist."
        return False, f"processor_watcher: {status}"

    def rule_qa_watcher_dead(self):
        """Alert if the online-QA watcher is not running — QA plots stop being produced
        for new subruns.

        Disabled by default, for the same reason as rule_processor_watcher_dead: it is
        routinely stopped on purpose and nothing is lost, only delayed."""
        info = get_qa_watcher_status()
        status = info["status"]
        if status == "STOPPED":
            return True, "qa_watcher is not running — online QA plots are not being produced."
        if status == "Waiting for Dir":
            return True, "qa_watcher is up but its QA directory does not exist."
        return False, f"qa_watcher: {status}"

    def rule_space_watcher_dead(self):
        """Alert if the SSD space watcher is not running. Without it nothing prunes the
        raw staging disk, which fills at the acquisition rate (~22 GB/hr measured) and
        stops the DAQ outright when it hits zero. Alerts by default: unlike the
        processor/QA watchers there is no routine reason to stop it, and its absence is
        silent until acquisition dies."""
        info = get_space_watcher_status()
        status = info["status"]
        if status == "STOPPED":
            return True, ("space_watcher is not running — the SSD raw staging disk is not "
                          "being pruned and will fill at the acquisition rate.")
        if status == "Stale":
            return True, "space_watcher is up but not publishing fresh state (wedged?)."
        return False, f"space_watcher: {status}"

    def rule_space_watcher_stuck(self):
        """Alert when the SSD space watcher cannot keep the staging disk above its floor.

        Three distinct conditions, deliberately graded apart because they need different
        responses:

          STUCK      nothing is safe to delete, i.e. runs are not reaching EOS — usually
                     a dead backup_watcher or an expired Kerberos ticket. This is the one
                     that actually needs a human, and the disk-space rules alone would
                     not say why.
          EMERGENCY  free space fell below the emergency level, so the newest-runs
                     reserve and minimum-age hold have been dropped and recent runs are
                     being deleted. Nothing is lost (they are on EOS), but you no longer
                     have them locally, and it says the disk is genuinely close.
          Held       below the floor with only guard-protected runs left. Backups are
                     FINE — this releases itself at the emergency level. A heads-up.
        """
        info = get_space_watcher_status()
        status = info["status"]
        if status == "STUCK":
            return "critical", ("SSD is below its free-space floor and NO run is safe to delete — "
                                "runs are not reaching EOS. Check backup_watcher and the Kerberos "
                                "ticket; acquisition stops when the disk fills.")
        if status == "EMERGENCY":
            return "critical", ("SSD is below its EMERGENCY level — the newest-runs reserve and "
                                "minimum-age hold are off and any EOS-verified run, however "
                                "recent, is being deleted from the staging disk. Data is safe on "
                                "EOS, but local copies of recent runs are going.")
        if status == "Held":
            return True, ("SSD is below its free-space floor and every deletable run is held by "
                          "the newest-runs/min-age guards. Backups are fine — the guards release "
                          "automatically at the emergency level, or lower the reserve by hand.")
        if status == "Low":
            return True, ("space_watcher freed what it could but the SSD is still below its "
                          "free-space floor.")
        if status == "STOPPED":
            return False, "space_watcher not running (covered by rule_space_watcher_dead)"
        return False, f"space_watcher: {status}"

    def rule_stream1_files_reduced(self):
        """Alert when n_TOF stream1 raw files have been arriving well below their
        recent size — the signature of a SiPM wall dropping out (the 07-21/22 episodes
        roughly halved the stream volume for hours). Size is a proxy for total hit
        multiplicity, so this says 'something stopped contributing hits', not which
        wall; confirm per-channel before acting.

        Tunable via rule_options.rule_stream1_files_reduced in monitor_config.json:
          thresholds  — {severity: episode_length_in_files} gradient, e.g.
                        {"warning": 5, "alert": 20}. Highest reached severity wins.
                        Files arrive every ~70 s, so 5 files ≈ 6 min.

        The episode length counted here is the watcher's: a trailing run of not-good
        files containing at least one BAD one, so a marginal (questionable) file in
        the middle of a dropout neither starts nor resets it.
        """
        opts = self.config.get("rule_options", {}).get("rule_stream1_files_reduced", {})
        thresholds = opts.get("thresholds") or {"warning": 5, "alert": 20}
        try:
            with open(STREAM1_STATE_FILE) as f:
                st = json.load(f)
        except Exception:
            return False, "stream1 state not available (watcher not running)"
        if not st.get("connected"):
            return False, "stream1 watcher disconnected (covered by rule_stream1_watcher_dead)"
        # A beam gap empties the files exactly like a dead detector would (07-22:
        # 0.1-0.4 GiB files through the 14:19-14:29 gap). The watcher grades those
        # NO_BEAM and keeps them out of episodes, so this only needs to say so.
        if st.get("state") == "no_beam":
            return False, "no beam — file sizes not gradeable"
        n = st.get("episode_files") or 0
        if not n:
            return False, (f"stream1 files nominal "
                           f"({st.get('latest_size_gib')} GiB, {st.get('state')})")

        severity = None
        for sev, thr in sorted(thresholds.items(), key=lambda kv: kv[1]):
            if n >= float(thr):
                severity = sev
        if severity:
            return severity, (
                f"n_TOF stream1 files reduced for {n} files "
                f"({st.get('episode_min')} min): {st.get('latest_size_gib')} GiB vs "
                f"{st.get('baseline_gib')} GiB baseline "
                f"({100 * (st.get('latest_ratio') or 0):.0f}%). Possible SiPM-wall dropout.")
        return False, f"{n} reduced file(s) — below the alert threshold"

    def rule_stream1_detector_gain(self):
        """Alert when a detector's gamma-flash amplitude collapses against its frozen
        nominal — the direct, per-detector version of rule_stream1_files_reduced.

        The flash is in every proton pulse, so this is an absolute gain reference: on
        2026-07-22 the four SiPM walls fell to ~2 % of nominal while their baselines
        and noise were untouched. Unlike the file-size rule this names the detectors,
        and it fires even when the total stream volume still looks ordinary.

        Tunable via rule_options.rule_stream1_detector_gain in monitor_config.json:
          severity_bad          — severity when any detector is BAD (< 50 % of
                                  nominal). Default "alert".
          severity_questionable — severity when one is only questionable (< 85 %).
                                  Default "warning".
        """
        opts = self.config.get("rule_options", {}).get("rule_stream1_detector_gain", {})
        try:
            with open(STREAM1_STATE_FILE) as f:
                st = json.load(f)
        except Exception:
            return False, "stream1 state not available (watcher not running)"
        if not st.get("connected"):
            return False, "stream1 watcher disconnected (covered by rule_stream1_watcher_dead)"
        wf = st.get("waveform")
        if not wf:
            return False, "no waveform sample yet"
        if not wf.get("have_nominal"):
            # Distinguish "never had one" from "had one and threw it away" (the stored
            # nominal was measured with a superseded sample decode — see NOMINAL_DECODE
            # in stream1_size_controller). Both disarm this rule, but the second is a
            # state someone caused and should be able to see the reason for. It clears
            # itself once the auto-seed re-freezes from a size-good file, which needs
            # beam: through a long beam stop, this rule stays disarmed.
            stale = st.get("nominal_stale")
            return False, (f"waveform nominal DROPPED, gain grading disarmed until it "
                           f"re-freezes: {stale}" if stale else
                           "no waveform nominal adopted yet — nothing to compare against")
        # Without protons there is no gamma flash, so EVERY detector reads ~0 and this
        # rule would cry facility-wide failure. The watcher detects that from the event
        # itself (PKUP/PSS/LIQ, the beam witnesses, all dead together) and from the
        # beam log; a real dropout leaves those witnesses at ~100 %.
        if wf.get("no_beam"):
            return False, ("no beam in the sampled event (witnesses "
                           + ", ".join(f"{d} {100 * r:.1f}%" for d, r in
                                       list((wf.get("witness_ratios") or {}).items())[:3])
                           + ") — gains not gradeable")

        def ratios(names):
            d = wf.get("detectors", {})
            return ", ".join(f"{n} {100 * (d[n].get('flash_ratio') or 0):.1f}%"
                             for n in names if n in d)

        bad, quest = wf.get("bad_detectors") or [], wf.get("questionable_detectors") or []
        where = f"(run {wf.get('run')}_{wf.get('seq')})"
        if bad:
            return opts.get("severity_bad", "alert"), (
                f"n_TOF detector gain COLLAPSED {where}: {ratios(bad)} of nominal. "
                f"Baseline/noise unchanged means a gain loss, not a dead digitiser.")
        if quest:
            return opts.get("severity_questionable", "warning"), (
                f"n_TOF detector gain low {where}: {ratios(quest)} of nominal.")
        return False, f"all detectors at nominal gain {where}"

    def rule_stream1_zeroed_channels(self):
        """EMERGENCY: a wall / liquid / plastic channel is producing no waveform at
        all — flat samples (RMS ~ 0), or no data bank in the event whatsoever.

        The most severe of the three waveform faults, and the reason it outranks
        rule_stream1_detector_gain rather than duplicating it:

          gain collapse (that rule)  the channel still works, it lost gain. Data is
                                     degraded but real, and the run is often still
                                     worth taking while someone looks at the HV.
          ZEROED (this rule)         the channel is not there. No noise, no baseline
                                     wander, nothing — a dead digitiser, a pulled
                                     cable, an unpowered front-end. Nothing recorded
                                     on that channel from now until it is fixed is
                                     recoverable in analysis, so every minute of
                                     beam spent in this state is lost outright.

        Deliberately NOT suppressed when the beam is off, unlike the gain rule. An
        absent gamma flash is not evidence of a fault, but a FLAT channel is one
        whatever the beam is doing — and a beam gap is exactly when a quietly dead
        front-end would otherwise sit undiscovered until beam returned. The watcher
        makes the same distinction on noise RMS, which is beam-independent: through
        the 2026-07-22 gaps every detector's flash fell to ~80 counts while RMS held
        at 17.9-21.3, and the quietest live channel measured all day was 17.55.

        Tunable via rule_options.rule_stream1_zeroed_channels in monitor_config.json:
          severity — default "emergency", the top of the SEVERITY_META ladder.
        """
        opts = self.config.get("rule_options", {}).get("rule_stream1_zeroed_channels", {})
        try:
            with open(STREAM1_STATE_FILE) as f:
                st = json.load(f)
        except Exception:
            return False, "stream1 state not available (watcher not running)"
        # NB: no `connected` gate here, unlike the sibling stream1 rules. Those read
        # live EOS listings; this reads the last decoded event, which stays valid
        # across an EOS blip. A listing failure must not silently disarm the most
        # severe rule in the file.
        wf = st.get("waveform")
        if not wf:
            return False, "no waveform sample yet"
        zeroed = wf.get("zeroed_channels")
        if zeroed is None:
            return False, "watcher predates zeroed-channel detection — restart it"
        if not zeroed:
            return False, "all wall/liquid/plastic channels producing waveforms"

        n_flat = sum(1 for z in zeroed if z.get("kind") == "flatlined")
        n_gone = sum(z.get("n_missing", 0) for z in zeroed if z.get("kind") == "missing")
        dets = sorted({z["det"] for z in zeroed})
        age = st.get("waveform_age_min")
        return opts.get("severity", "emergency"), (
            f"n_TOF ZEROED CHANNELS on {', '.join(dets)} "
            f"(run {wf.get('run')}_{wf.get('seq')}"
            + (f", sampled {age} min ago" if age is not None else "") + "): "
            + describe_zeroed(zeroed) + ". "
            + f"{n_flat} channel(s) flatlined"
            + (f", {n_gone} absent from the event" if n_gone else "")
            + ". This is not a gain loss — these channels are recording NOTHING "
              "and the data is unrecoverable. Check front-end power and cabling.")

    def rule_stream1_waveform_incomplete(self):
        """WARNING: the waveform decoder keeps stopping before the end of the event,
        which disarms the missing-channel half of rule_stream1_zeroed_channels.

        This is a monitoring-integrity rule, not a detector rule, and it exists
        because the failure it covers is SILENT. `missing` is inferred from absence,
        so it is only trustworthy from a read that reached the end of the event; a
        short read therefore suppresses it (see _zeroed_channels). That suppression is
        correct — on 2026-07-27 a 268.8 MB event under a 260 MB cap produced an
        emergency "PSSD 1/2 missing" for a healthy channel, because PSSD ch 1 is the
        last bank in the event — but a permanently short read would leave the check
        quietly switched off, and an empty zeroed-channel list looks identical whether
        the check passed or never ran.

        The watcher grows its own read cap on every truncated sample, so a single
        incomplete read is self-correcting and deliberately does NOT alert. A run of
        them means the growth is not keeping up, or the files are not what the decoder
        expects, and someone should look.

        Tunable via rule_options.rule_stream1_waveform_incomplete in
        monitor_config.json:
          consec   — consecutive incomplete reads before alerting (default: the
                     watcher's own WAVEFORM_INCOMPLETE_CONSEC, published in the state)
          severity — default "warning"
        """
        opts = self.config.get("rule_options", {}).get(
            "rule_stream1_waveform_incomplete", {})
        try:
            with open(STREAM1_STATE_FILE) as f:
                st = json.load(f)
        except Exception:
            return False, "stream1 state not available (watcher not running)"
        # Same reasoning as the sibling zeroed rule: this reads decoded samples, not
        # live EOS listings, so an EOS blip must not disarm it on top of everything.
        read = st.get("waveform_read")
        if not read:
            return False, "watcher predates incomplete-read tracking — restart it"
        consec = int(read.get("incomplete_consec") or 0)
        limit = int(opts.get("consec") or read.get("incomplete_consec_alert") or 3)
        if consec < limit:
            return False, ("waveform reads are complete — missing-channel detection armed"
                           if not consec else
                           f"{consec} incomplete read(s), below the {limit} needed to alert "
                           f"(the read cap grows itself)")
        cap = read.get("cap_bytes")
        return opts.get("severity", "warning"), (
            f"n_TOF waveform decoder has not read a complete event for {consec} "
            f"consecutive samples"
            + (f" (read cap now {cap / 1e6:.0f} MB" if cap else "")
            + (", AT ITS CEILING" if read.get("cap_at_ceiling") else "")
            + (")" if cap else "")
            + ". Missing-channel detection is DISARMED — a channel absent from the "
              "event would not be reported. Flatlined-channel and gain detection are "
              "unaffected. Check that stream1 files are well-formed and that the "
              "decoder is reaching the second event header.")

    def rule_gas_flow_starved(self):
        """Alert if a gas channel's measured flow stays far below its setpoint while
        the valve is wound open — the signature of an empty bottle / lost supply
        pressure. (Isobutane ran dry unnoticed on 2026-07-08: setpoint held at
        0.379 ln/h while flow decayed to 0 and the valve saturated at 73%.)

        Tunable via rule_options.rule_gas_flow_starved in monitor_config.json:
          min_setpoint_lnh  — ignore channels commanded at/below this (default 0.05,
                              so an intentional /gas/zero never trips it)
          deficit_fraction  — flow below this fraction of setpoint counts as starved
                              (default 0.5)
          valve_open_pct    — valve at/above this counts as wound open (default 50);
                              this is what distinguishes true starvation from a normal
                              ramp-down (where the valve closes instead).
        """
        try:
            with open(GAS_STATE_FILE) as f:
                st = json.load(f)
        except Exception:
            # No readable state -> handled by rule_gas_watcher_dead, not here.
            return False, "gas state unavailable"

        if not st.get("connected"):
            return False, "gas controllers not connected"

        # Skip stale data so we never judge starvation on frozen readings
        # (rule_gas_watcher_dead owns the stale/dead case).
        try:
            age = (datetime.now() - datetime.fromisoformat(st["timestamp"])).total_seconds()
        except Exception:
            age = None
        if age is not None and age > 30:
            return False, "gas state stale"

        opts = self.config.get("rule_options", {}).get("rule_gas_flow_starved", {})
        min_set = opts.get("min_setpoint_lnh", 0.05)
        deficit = opts.get("deficit_fraction", 0.5)
        valve_open = opts.get("valve_open_pct", 50.0)

        starved = []
        for key in ("argon", "iso"):
            ch = st.get(key) or {}
            sp = ch.get("set_lnh")
            fl = ch.get("flow_lnh")
            vp = ch.get("valve_pct")
            if sp is None or fl is None:
                continue
            if sp >= min_set and fl < deficit * sp and (vp is None or vp >= valve_open):
                vtxt = f"{vp:.0f}%" if vp is not None else "?"
                starved.append(
                    f"{ch.get('fluid', key)}: flow {fl:.3f} ln/h vs set {sp:.3f} "
                    f"ln/h (valve {vtxt})")
        if starved:
            return True, "Gas flow starved (check supply bottle/pressure) — " + "; ".join(starved)
        return False, "gas flow OK"

    # ---- Disk space ----------------------------------------------------

    # Default gradient: percent-full at/above which each severity kicks in.
    # Override per rule via rule_options.<rule>.thresholds in monitor_config.json,
    # e.g. {"warning": 75, "critical": 92}. Missing keys fall back to these.
    _DISK_THRESHOLDS = {"warning": 70, "alert": 80, "critical": 90, "emergency": 95}

    def _disk_space_alert(self, rule_name, path, label):
        """Graded disk-space check: returns (severity|False, detail) for `path`."""
        opts = self.config.get("rule_options", {}).get(rule_name, {})
        thresholds = dict(self._DISK_THRESHOLDS, **(opts.get("thresholds") or {}))

        try:
            usage = shutil.disk_usage(path)
        except Exception as e:
            # Not an alert (avoid nagging if a mount is briefly absent); just log.
            return False, f"{label}: disk usage unavailable ({e})"

        pct = usage.used / usage.total * 100 if usage.total else 0
        gb = 1024 ** 3
        detail = (f"{label} is {pct:.0f}% full — "
                  f"{usage.used / gb:.0f}/{usage.total / gb:.0f} GB used, "
                  f"{usage.free / gb:.0f} GB free.")

        # Highest severity whose threshold the usage has reached.
        severity = None
        for sev, thr in sorted(thresholds.items(), key=lambda kv: kv[1]):
            if pct >= thr:
                severity = sev
        if severity:
            return severity, detail
        return False, f"{label} OK — {pct:.0f}% full"

    def _ssd_emergency_gb(self):
        """The auto-remover's emergency free-space level (GB), read live from
        space_config.json so a floor retune from the Disk Space tab moves the alert
        with it. Falls back to space_watcher.py's own default (half the floor, or a
        flat 50 GB) if the file is missing or malformed — an unreadable config must
        never silence the last-resort disk alert."""
        try:
            with open(SPACE_CONFIG_FILE) as f:
                cfg = json.load(f)
            if "emergency_gb" in cfg:
                return float(cfg["emergency_gb"])
            if "low_water_gb" in cfg:            # mirror space_watcher's own default
                return round(float(cfg["low_water_gb"]) / 2, 1)
        except Exception:
            pass
        return 50.0

    def rule_ssd_disk_space(self):
        """Last-resort free-space safety net on the SSD staging filesystem (/), the
        same disk the auto-remover (space_watcher) prunes.

        This is NOT the percent-full gradient it used to be. The SSD dream_run
        staging area and / are one filesystem, and space_watcher deliberately parks
        free space just above its low_water floor — so "70% full" is the NORMAL,
        healthy operating point now, and a fixed percent gradient would nag forever.

        Division of labour with the space_watcher rules:
          * While free space stays in the band the auto-remover owns (>= emergency),
            this rule is SILENT. If the pruner is struggling in that band (held by
            its soft guards, freed only partially, or nothing safe to delete),
            rule_space_watcher_stuck reports that from the watcher's own state.
          * This rule only speaks up once free space has fallen to the auto-remover's
            EMERGENCY level or below — and it does so straight off a live statvfs, so
            it still fires when the watcher is dead or its state file is stale, which
            is exactly when rule_space_watcher_stuck cannot.
          * No double alert: when the watcher is ALIVE and already announcing the low
            disk itself (a fresh EMERGENCY or STUCK state, both of which
            rule_space_watcher_stuck raises at "critical"), this rule stands down and
            lets that one own it. It re-takes ownership the moment the watcher goes
            quiet — STOPPED or Stale — which is precisely when nothing else would warn.

        Graded on FREE GB, anchored to the auto-remover's emergency_gb:
          critical   free has fallen to the emergency level (space_watcher has, or
                     would have, dropped its newest-runs reserve and is deleting even
                     recent runs) — high-degree heads-up.
          emergency  free is at/below half the emergency level: the emergency deletes
                     are not keeping up (or nothing runs) and the disk is close to
                     stopping acquisition — highest degree.

        Tunable via rule_options.rule_ssd_disk_space.free_gb_thresholds in
        monitor_config.json, e.g. {"critical": 50, "emergency": 25}. Missing keys are
        derived from emergency_gb (critical = emergency_gb, emergency = half of it)."""
        opts = self.config.get("rule_options", {}).get("rule_ssd_disk_space", {})
        emerg_gb = self._ssd_emergency_gb()
        defaults = {"critical": emerg_gb, "emergency": round(emerg_gb / 2, 1)}
        thresholds = dict(defaults, **(opts.get("free_gb_thresholds") or {}))

        # Stand down while the watcher is alive and already raising the low disk itself,
        # so the operator gets one message, not two. Only a FRESH state counts: STOPPED
        # and Stale (and every other status) fall through to the live-statvfs net below.
        wstatus = get_space_watcher_status().get("status")
        if wstatus in ("EMERGENCY", "STUCK"):
            return False, f"space_watcher reporting {wstatus} (covered by rule_space_watcher_stuck)"

        try:
            usage = shutil.disk_usage("/")
        except Exception as e:
            return False, f"SSD (/): disk usage unavailable ({e})"

        gb = 1024 ** 3
        free_gb = usage.free / gb
        pct = usage.used / usage.total * 100 if usage.total else 0
        detail = (f"SSD (/) has {free_gb:.0f} GB free ({pct:.0f}% full) — at or below the "
                  f"auto-remover's {emerg_gb:.0f} GB emergency level; the pruner is deleting "
                  f"even the newest EOS-verified runs to keep the DAQ alive. Data is safe on "
                  f"EOS. Check backup_watcher/Kerberos if free space keeps falling.")

        # Most severe level whose free-GB floor we have dropped to/below (lowest floor wins).
        severity = None
        for sev, floor_gb in sorted(thresholds.items(), key=lambda kv: kv[1], reverse=True):
            if free_gb <= floor_gb:
                severity = sev
        if severity:
            return severity, detail
        return False, f"SSD (/) OK — {free_gb:.0f} GB free ({pct:.0f}% full), above the {emerg_gb:.0f} GB emergency level"

    @staticmethod
    def _hour_in_cyclic_window(hour, start, end):
        """True if `hour` falls in the [start, end) window, which may wrap midnight
        (23:00-08:00 is a wrap; 08:00-20:00 is not)."""
        if start <= end:
            return start <= hour < end
        return hour >= start or hour < end

    def _hdd_time_of_day_gate(self, now, opts):
        """The HDD's time-of-day gate: how many hours-till-empty it needs before this
        gate trips, AND at what severity. Two regimes:

          day    (day_start_hour <= now < night_start_hour, default 08:00-20:00):
                 a flat floor (default 8 h) at `warning` — someone is around to see
                 the Disk Space tab, so this is a heads-up, not an emergency.
          night  (otherwise): the disk must last until the next day_start_hour
                 (assumed wake time) PLUS a margin (default 2 h). This shrinks
                 through the night as wake time approaches — at 20:00 it demands
                 ~14 h (12 h to 08:00 + 2 h margin), by 07:00 only ~3 h — so the
                 rule tracks "will we make it to morning with a buffer", not a
                 fixed number.

        The night regime is deliberately LOUDER than the day one, because nobody is
        watching: it opens at `alert` when the evening starts, and hardens to
        `critical` from night_critical_hour (default 23:00) to wake — "you were told
        at 20:00 and it is still not fixed, and the disk will not survive the night."
        Set night_critical_hour to null to keep the whole night at `alert`.

        Returns (threshold_hours, severity, basis_str) for the alert detail text."""
        day_start = float(opts.get("day_start_hour", 8))
        night_start = float(opts.get("night_start_hour", 20))
        day_threshold = float(opts.get("day_threshold_hours", 8))
        night_margin = float(opts.get("night_margin_hours", 2))
        night_critical_hour = opts.get("night_critical_hour", 23)

        hour = now.hour + now.minute / 60 + now.second / 3600
        if day_start <= hour < night_start:
            return day_threshold, "warning", f"daytime floor: {day_threshold:.1f} h"

        hours_to_wake = (day_start - hour) if hour < day_start else (24 - hour + day_start)
        threshold = hours_to_wake + night_margin
        severity, when = "alert", "overnight"
        if night_critical_hour is not None and self._hour_in_cyclic_window(
                hour, float(night_critical_hour), day_start):
            severity, when = "critical", f"after {float(night_critical_hour):02.0f}:00"
        return threshold, severity, (f"{when}: {hours_to_wake:.1f} h to "
                                     f"{day_start:02.0f}:00 wake + {night_margin:.1f} h margin")

    def rule_hdd_disk_space(self):
        """Alert on the data HDD (/mnt/data) using projected TIME-TILL-EMPTY rather
        than percent-full, since "how full" says nothing about how urgent that is —
        5 GB/hr of margin means something very different at 10 GB free than at 500.

        Time-to-empty comes from disk_forecast's gross-inflow fit (see disk_forecast.py
        — a plain slope is useless here because the HDD is periodically pruned by
        hand/backup cleanup, which looks like a huge negative rate to a naive fit).

        Two independent gates; the alert takes whichever severity is HIGHER:
          time-of-day  time-to-empty has fallen below a floor that VARIES by time of
                    day (see _hdd_time_of_day_gate) — a flat 8 h at `warning` during
                    08:00-20:00, tightening overnight to "won't last to 08:00 wake
                    + 2 h margin" at `alert`, and hardening to `critical` from 23:00.
                    This is the "deal with it before you sleep" signal.
          absolute  time-to-empty below an absolute floor — `critical` under 3 h,
                    `emergency` under 30 min — day or night. Once it is this close to
                    actually filling, the time of day stops mattering.

        Repeats are severity-keyed (rule_options.….resend_minutes_by_severity): silent
        at warning/alert so a slow daytime slide does not nag, then every 30 min once
        critical and every 15 once emergency.

        Tunable via rule_options.rule_hdd_disk_space: day_start_hour, night_start_hour,
        day_threshold_hours, night_margin_hours, night_critical_hour, critical_hours,
        emergency_hours.

        Falls back to the OLD percent-full gradient (rule_options.rule_hdd_disk_space.
        thresholds, still warning/alert/critical/emergency at 70/80/90/95%) whenever
        the forecast can't produce a time-to-empty — no recent fill history, the HDD
        isn't currently filling (trend "idle"), or disk_forecast/pandas is unavailable
        — so a disk that is already nearly full but not filling right now (or a
        forecaster that's broken) still gets a floor to alert against."""
        opts = self.config.get("rule_options", {}).get("rule_hdd_disk_space", {})
        gb = 1024 ** 3

        def fallback(note):
            sev, detail = self._disk_space_alert("rule_hdd_disk_space", "/mnt/data", "HDD (/mnt/data)")
            return sev, f"{detail} [{note}, using percent-full fallback]"

        try:
            disk_forecast = self._repo_import("disk_forecast")
            fc = disk_forecast.forecast()
        except Exception as e:
            return fallback(f"time-to-empty forecast unavailable ({e})")

        if not fc.get("success"):
            return fallback(f"time-to-empty forecast unavailable ({fc.get('message')})")
        hdd = fc.get("disks", {}).get("hdd") or {}
        if hdd.get("trend") != "filling" or hdd.get("time_to_full_h") is None:
            return fallback(f"HDD not currently filling (trend={hdd.get('trend', 'unknown')})")

        ttf_h = hdd["time_to_full_h"]
        now = datetime.now()
        threshold_h, tod_severity, basis = self._hdd_time_of_day_gate(now, opts)
        critical_h = float(opts.get("critical_hours", 3))
        emergency_h = float(opts.get("emergency_hours", 0.5))
        eta_clock = (now + timedelta(hours=ttf_h)).strftime("%a %H:%M")

        detail = (f"HDD (/mnt/data) projected empty in {disk_forecast.human_duration(ttf_h)} "
                  f"(~{eta_clock}) at {hdd.get('inflow_gb_per_hr', 0):.1f} GB/hr — "
                  f"{hdd.get('free', 0) / gb:.0f} GB free. Floor {threshold_h:.1f} h ({basis}); "
                  f"nothing prunes this disk automatically.")

        # Both gates are evaluated; the louder one wins, so the overnight tightening
        # can never DOWNgrade an absolute-floor critical (or vice versa).
        candidates = []
        if ttf_h < emergency_h:
            candidates.append("emergency")
        elif ttf_h < critical_h:
            candidates.append("critical")
        if ttf_h < threshold_h:
            candidates.append(tod_severity)
        if candidates:
            return max(candidates, key=_severity_rank), detail
        return False, (f"HDD (/mnt/data) OK — {disk_forecast.human_duration(ttf_h)} until full "
                       f"(~{eta_clock}), above the {threshold_h:.1f} h floor ({basis})")

    # ---- DREAM crate health --------------------------------------------
    #
    # Both rules exist because of the 2026-07-24 FEU 3 dropout
    # (docs/HANDOFF_2026-07-24_feu3_dropout.md): a board fell off the network,
    # RunCtrl aborted configuration on every following sub-run, daq_control marked
    # them complete anyway, and 51 of run_75's 60 grid points recorded nothing with
    # no alarm of any kind. rule_feu_unreachable catches the cause, rule_subrun_no_data
    # catches the consequence — deliberately redundant, because either detector alone
    # can miss a variant of the same failure.

    # Sub-set of get_daq_control_status() statuses that mean a run is in progress, so
    # an outage can be reported as "losing data right now" rather than merely noted.
    _DAQ_ACTIVE_STATUSES = {"RUNNING", "Prepping DAQs", "Ramping HV", "STARTING",
                            "Finished Sub Run", "Paused"}

    def _repo_import(self, module_name):
        """Import a repo-root module (feu_health / subrun_health) lazily.

        Lazy because monitor.py lives in flask_app/ and is imported before the repo
        root is necessarily on sys.path — the same reason daq_status._run_dir defers
        its import."""
        import sys
        if _REPO_DIR not in sys.path:
            sys.path.append(_REPO_DIR)
        return __import__(module_name)

    def _mode_watcher(self):
        """The mode_watcher module, imported once and kept.

        Importing it also execs switch_mode (it loads it by path to keep one definition
        of a changeover) — both are import-safe, which is why app.py's /mode/status does
        exactly the same thing. Unlike app.py this does NOT reload on every call: the
        monitor only needs the module's constants and its two pure read functions, and a
        reload per check in the fast lane would re-exec switch_mode every 15 s.
        """
        mod = getattr(self, "_mode_watcher_mod", None)
        if mod is None:
            mod = self._repo_import("mode_watcher")
            self._mode_watcher_mod = mod
        return mod

    def _daq_is_running(self):
        try:
            return get_daq_control_status()["status"] in self._DAQ_ACTIVE_STATUSES
        except Exception:
            return False

    def rule_no_active_run(self):
        """Alert if daq_control is not actively taking data, for any reason at all —
        tmux session dead, an unrecognised state, or simply sitting idle ("Run
        Complete" / "WAITING") with nothing next queued.

        This is the general backstop the other daq_control rules don't cover between
        them, and it does not defer to them (it fires on ERROR/UNKNOWN STATE too) so
        it still works if those more specific, cause-attributing rules are disabled.
        It is what should have caught 2026-07-29: mode_watcher's beam->cosmics
        changeover failed underneath a board-lock guard and daq_control sat idle,
        correctly reporting a coherent "Run Complete" status the whole time, for
        16 minutes before anyone noticed.

        Tunable via rule_options.rule_no_active_run:
          min_duration_seconds — default 300 (5 min); rides out a normal beam<->
                                 cosmics changeover (run_103's took 19s; the stop
                                 and cfg-verify steps each carry their own multi-
                                 minute timeout in switch_mode.py, so this is not
                                 generous, just enough to not nag on the common case)
        """
        info = get_daq_control_status()
        status = info["status"]
        if status in self._DAQ_ACTIVE_STATUSES:
            return False, f"daq_control: {status}"
        return True, (f'⏸️ <b>No run is active</b> — daq_control reports "{status}", '
                     f"not taking data.")

    def rule_feu_unreachable(self):
        """Alert when any FEU (or the TCM) stops answering on the DREAM subnet.

        This is the direct cause-side check: if a board is unreachable, RunCtrl
        cannot configure it and every sub-run from that moment records nothing.

        Graded, because the blast radius differs:
          critical — the TCM is down, or 2+ FEUs are (crate/switch-level problem),
                     or a run is actively in progress (data is being lost NOW)
          alert    — a single FEU is down while the DAQ is idle

        An ARP entry that is missing entirely is called out in the message: it means
        the board is not answering ARP at all (dead at the link/board level, i.e.
        power-cycle territory) rather than merely hung at the IP layer.

        Tunable via rule_options.rule_feu_unreachable:
          min_duration_seconds — ride out brief blips (default 90; a couple of
                                 missed ICMP replies on a loaded 10 GbE link should
                                 not page anyone)
        """
        try:
            feu_health = self._repo_import("feu_health")
        except Exception as e:                                  # noqa: BLE001
            return False, f"feu_health module unavailable ({e!r})"

        try:
            res = feu_health.sweep()
        except Exception as e:                                  # noqa: BLE001
            return False, f"FEU sweep failed ({e!r})"

        if res["ok"]:
            return False, f"DREAM crate OK — {res['summary']}"

        running = self._daq_is_running()
        tcm_down = bool(res["tcm"]) and not res["tcm"]["ping"]
        n_feu_down = sum(1 for v in res["feus"].values() if not v["ping"])
        severity = "critical" if (tcm_down or n_feu_down >= 2 or running) else "alert"

        detail = "🔌 <b>DREAM crate unreachable</b>: " + ", ".join(res["missing"])
        if running:
            info = get_daq_control_status()
            detail += (f"\n⚠️ A run is IN PROGRESS ({status_field(info, 'Run') or '?'} / "
                       f"{status_field(info, 'Subrun') or '?'}) — sub-runs are recording "
                       f"NOTHING while this persists.")
        dead_link = [f"FEU {n}" for n, d in sorted(res["feus"].items())
                     if not d["ping"] and d["arp_incomplete"]]
        if dead_link:
            detail += (f"\n{', '.join(dead_link)} not answering ARP → dead at the "
                       f"link/board level (power cycle, not an IP hang).")
        detail += "\nDo NOT start a run until <code>python3 feu_health.py</code> is clean."
        return severity, detail

    def rule_subrun_no_data(self):
        """Alert when the current run's sub-runs are completing without recording data.

        The consequence-side check, and the one that would have capped 07-24 at a
        single lost grid point. It is cause-agnostic: a RunCtrl config abort, a dead
        TCM, or a silently misconfigured run all land here, because the test is simply
        "did datrun bytes appear on disk".

        Graded: one empty sub-run is an alert; three or more means the grid is racing
        through its points writing nothing, which is the 07-24 pathology and critical.

        Only the run daq_control currently reports is examined, so stale empty dirs from
        an earlier run stop alarming once the next run starts. Sub-runs already pruned
        from SSD staging by space_watcher are simply absent and never counted as failed.

        Tunable via rule_options.rule_subrun_no_data:
          grace_seconds        — how long a quiet, empty sub-run may be excused as
                                 "still writing" (default 180)
          critical_count       — empty sub-runs before escalating (default 3)
          min_duration_seconds — default 60
        """
        opts = self.config.get("rule_options", {}).get("rule_subrun_no_data", {})
        grace = int(opts.get("grace_seconds", 180))
        crit_n = int(opts.get("critical_count", 3))

        try:
            subrun_health = self._repo_import("subrun_health")
        except Exception as e:                                  # noqa: BLE001
            return False, f"subrun_health module unavailable ({e!r})"

        run = status_field(get_daq_control_status(), "Run")
        if not run or run in ("?", "None"):
            return False, "no current run"

        try:
            res = subrun_health.scan_run(run, grace_s=grace)
        except Exception as e:                                  # noqa: BLE001
            return False, f"sub-run scan failed ({e!r})"

        if not res["n_empty"]:
            return False, (f"{run}: {res['n_good']} sub-run(s) with data, "
                           f"{res['n_pending']} in progress")

        empties = [c["name"] for c in res["subruns"] if c["status"] == "empty"]
        severity = "critical" if res["n_empty"] >= crit_n else "alert"
        detail = (f"📼 <b>{run} is recording NO DATA</b> — {res['n_empty']} sub-run(s) "
                  f"finished with zero datrun bytes: " + ", ".join(f"<code>{e}</code>"
                                                                  for e in empties[:6]))
        if len(empties) > 6:
            detail += f" (+{len(empties) - 6} more)"
        if res["bad_feus"]:
            detail += (f"\nRunCtrl could not configure <b>FEU {res['bad_feus']}</b> — "
                       f"check the crate with <code>python3 feu_health.py</code>.")
        detail += ("\n⚠️ Sub-runs are still being marked complete. STOP THE RUN — every "
                   "further point is a lost grid point.")
        return severity, detail

    # ---- Run lifecycle (one-shot events, see _EVENT_RULES) -------------

    def rule_run_ended(self):
        """Notify once when a run finishes — daq_control transitions into "Run
        Complete" (the end-of-run "donzo"). One-shot event: it fires on the
        transition and clears immediately, so it neither nags nor sends a recovery.

        The very first check after the monitor starts never fires (no prior state),
        so a DAQ already sitting idle in "Run Complete" at startup won't false-trip."""
        info = get_daq_control_status()
        status = info["status"]
        prev = self._prev_daq_status
        self._prev_daq_status = status
        if status == "Run Complete" and prev is not None and prev != "Run Complete":
            run = status_field(info, "Run") or "?"
            return True, f"Run <b>{run}</b> has ended (DAQ reported Run Complete)."
        return False, f"daq_control: {status}"

    # Modes as they should read in a notification, with the icon each one gets.
    _MODE_LABEL = {"beam": ("🔆", "Back on BEAM"), "cosmics": ("🌌", "Now taking COSMICS")}

    def rule_mode_changeover(self):
        """Notify when the DAQ swaps between the beam and the cosmics trigger.

        Deliberately a SEPARATE notification from rule_beam_off. "The beam went away"
        and "we therefore traded the beam run for a cosmics run" are different facts:
        the first is the facility's, the second changes what is on our disk, and either
        can happen without the other (a manual switch; a beam gap short enough that
        beam_gate just parks the run). rule_beam_off says only that the beam is gone,
        and repeats while it stays gone; this rule fires exactly twice per episode —
        once when the DAQ goes to cosmics, once when it comes back to beam.

        Ground truth is the LIVE run's config filename (mode_watcher.current_mode(),
        /proc only — never a board poll), so an operator/GUI switch is announced exactly
        like an automatic one. mode_watcher_state.json is consulted only to attribute it
        and to quote the reason.

        Two events, both one-shot:
          info     — a beam<->cosmics transition completed.
          critical — mode_watcher recorded a FAILED changeover. That path is the one
                     from 2026-07-29: switch_mode exits non-zero, the DAQ may be in
                     NEITHER state, and nothing retries it. rule_no_active_run only
                     notices 5 min later, and only if no run is left running at all.

        A run stopping (mode -> None mid-changeover) is not a transition and is ignored;
        the first check after a monitor restart only primes the state.
        """
        mw = self._mode_watcher()
        mode, mode_detail = mw.current_mode()
        st = mw.load_state()
        ts = st.get("last_changeover_ts")

        if not self._mode_primed:
            self._mode_primed = True
            self._prev_run_mode = mode
            self._last_changeover_ts = ts
            return False, f"mode: {mode or 'no run live'} (priming)"

        prev_mode, prev_ts = self._prev_run_mode, self._last_changeover_ts
        self._last_changeover_ts = ts
        if mode is not None:
            self._prev_run_mode = mode

        result = str(st.get("last_result") or "")
        if ts is not None and ts != prev_ts and result != "ok":
            target = str(st.get("last_target") or "?").upper()
            return "critical", (
                f"🔁 <b>Auto-changeover to {target} FAILED</b> ({result}).\n"
                f"The DAQ may be in NEITHER trigger state and it will NOT be retried "
                f"automatically — check the Run Mode card and switch by hand.")

        if mode is None or prev_mode is None or mode == prev_mode:
            return False, f"mode: {mode or 'no run live'}"

        emoji, label = self._MODE_LABEL.get(mode, ("🔁", f"Now on {mode.upper()}"))
        run = self._run_name_from_argv(mode_detail)
        head = f"{emoji} <b>{label}</b>"
        if run:
            head += f" — {run}"
        return "info", head + ".\n" + self._changeover_context(mode, ts, st)

    @staticmethod
    def _run_name_from_argv(argv):
        """'daq_control.py run_config_cosmics_optimal_113.json' -> 'run_113'.

        The run number is the trailing number of the generated config filename (that is
        how switch_mode/run_num name them), so it needs no run-directory lookup and
        costs nothing in the fast lane."""
        m = re.search(r"_(\d+)\.json", argv or "")
        return f"run_{m.group(1)}" if m else None

    def _changeover_context(self, mode, ts, mw_state):
        """Why we are on this trigger now: what the beam is doing, and who switched."""
        bits = []
        try:
            with open(BEAM_STATE_FILE) as f:
                bst = json.load(f)
            gap = bst.get("seconds_since_pulse")
            if isinstance(gap, (int, float)):
                bits.append(f"Beam has been off {gap / 60:.1f} min."
                            if mode == "cosmics" else
                            f"Beam is back — last pulse {gap:.0f} s ago "
                            f"({bst.get('last_pulse_e10')}e10).")
        except Exception:                                       # noqa: BLE001
            pass

        # Attribution. mode_watcher stamps its state file when switch_mode returns,
        # within about a second of the new run appearing in /proc — so the stamp is
        # recent, names THIS mode, and has not already been spent explaining an earlier
        # transition. All three conditions matter: without the last one, an operator
        # who overrides a changeover minutes later gets it credited to the watcher.
        auto = (ts is not None
                and ts != self._attributed_changeover_ts
                and (time.time() - ts) < 600
                and mw_state.get("last_target") == mode)
        if auto:
            self._attributed_changeover_ts = ts
            reason = mw_state.get("last_reason")
            bits.append("Switched automatically by mode_watcher"
                        + (f" ({reason})." if reason else "."))
        else:
            bits.append("Not a mode_watcher changeover — switched from the GUI or by hand.")
        return " ".join(bits)

    def rule_long_run_warning(self):
        """For a run scheduled to last longer than an hour, send a single warning
        ~10 min before it is due to finish, so the shift crew can prep the next run.
        One-shot event keyed on run name (fires at most once per run).

        The scheduled length is the sum of the run's sub-run run_times (from its
        run_config.json); "remaining" is that total minus elapsed, so it counts down
        across all sub-runs, not per sub-run.

        Tunable via rule_options.rule_long_run_warning in monitor_config.json:
          min_run_minutes — only warn for runs scheduled longer than this (default 60)
          warn_before_min — minutes-before-end at which to warn (default 10)
        """
        opts = self.config.get("rule_options", {}).get("rule_long_run_warning", {})
        min_run = float(opts.get("min_run_minutes", 60))
        warn_before = float(opts.get("warn_before_min", 10))

        prog = get_run_progress()
        total = prog.get("total_min")
        elapsed = prog.get("elapsed_min")
        run = prog.get("run")
        if total is None or elapsed is None:
            # No active run, or between sub-runs (ramp/prep) — nothing to warn about,
            # and drop stale warned-run names so a re-run of the same name can warn again.
            if run is None:
                self._long_run_warned.clear()
            return False, "no active run progress"
        if total <= min_run:
            return False, f"run scheduled {total:.0f} min (<= {min_run:.0f} min)"

        remaining = total - elapsed
        if 0 < remaining <= warn_before and run not in self._long_run_warned:
            self._long_run_warned.add(run)
            return "warning", (f"Run <b>{run}</b> ends in ~{remaining:.0f} min "
                               f"({elapsed:.0f}/{total:.0f} min elapsed).")
        return False, f"run {run}: {remaining:.0f} min left of {total:.0f} min"
