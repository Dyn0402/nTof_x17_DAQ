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
import shutil
import threading
from datetime import datetime

import requests

from daq_status import (get_dream_daq_status, get_hv_control_status,
                        get_daq_control_status, get_processor_watcher_status,
                        get_qa_watcher_status, get_gas_watcher_status,
                        get_backup_watcher_status,
                        get_beam_watcher_status, get_stream1_watcher_status,
                        get_run_progress, status_field,
                        GAS_STATE_FILE, BEAM_STATE_FILE, STREAM1_STATE_FILE)

TELEGRAM_URL = "https://api.telegram.org/bot{token}/{method}"

_REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
SEVERITY_META = {
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
    _EVENT_RULES = {"rule_run_ended", "rule_long_run_warning"}

    # Per-rule recovery text, shown after the ✅ when an alert condition clears.
    # (The alert body itself is the rule's own returned `detail`, already tailored;
    # recovery has no detail, so it needs a tailored line of its own here.)
    # A rule with no entry falls back to "<rule label> is back to normal."
    _RECOVERY_MESSAGES = {
        "rule_dream_daq_session_dead": "dream_daq tmux session is back up.",
        "rule_daq_control_session_dead": "daq_control tmux session is back up.",
        "rule_hv_control_monitoring": "hv_control is monitoring HV again.",
        "rule_dream_daq_unknown_state": "dream_daq state is back to normal.",
        "rule_daq_control_unknown_state": "daq_control state is back to normal.",
        "rule_gas_watcher_dead": "gas_watcher is back up.",
        "rule_beam_watcher_dead": "beam_watcher is back up.",
        "rule_beam_off": "n_TOF beam is back.",
        "rule_stream1_watcher_dead": "stream1_watcher is back up.",
        "rule_backup_watcher_dead": "backup_watcher is back up — runs are reaching EOS again.",
        "rule_processor_watcher_dead": "processor_watcher is back up.",
        "rule_qa_watcher_dead": "qa_watcher is back up.",
        "rule_stream1_files_reduced": "n_TOF stream1 file sizes are back to the full level.",
        "rule_stream1_detector_gain": "n_TOF detector gains are back at nominal.",
        "rule_stream1_zeroed_channels": (
            "n_TOF wall/liquid/plastic channels are producing waveforms again."),
        "rule_gas_flow_starved": "gas flow is back to normal.",
        "rule_ssd_disk_space": "SSD (/) disk space is back to normal.",
        "rule_hdd_disk_space": "HDD (/mnt/data) disk space is back to normal.",
    }

    def __init__(self, config_path):
        self.config_path = config_path
        self.config = self._load_config()

        # State for the edge/event rules (see _EVENT_RULES).
        self._prev_daq_status = None    # last daq_control status seen by rule_run_ended
        self._long_run_warned = set()   # run names already given the 10-min warning

        self._thread = None
        self._stop_event = threading.Event()

        # Per-rule state
        self._alert_active = {}    # rule_name → bool
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
        """Return [{name, label, description, enabled}] for every rule method.

        `description` is the first paragraph of the rule method's docstring,
        collapsed to a single line.
        """
        rules = []
        for name in self._rule_names():
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

            rules.append({
                "name": name,
                "label": label,
                "description": description,
                "enabled": self._is_rule_enabled(name),
                "resend_mode": resend_mode,
                "resend_minutes": resend_minutes,
                "resend_effective_minutes": (
                    None if effective_secs is None else effective_secs / 60
                ),
            })
        return rules

    def set_rule_enabled(self, name, enabled):
        """Enable/disable a single rule and persist. Returns (ok, error)."""
        if name not in self._rule_names():
            return False, f"Unknown rule: {name}"
        self.config.setdefault("rules", {})[name] = bool(enabled)
        # Clear any live alert state so a just-disabled rule stops nagging and a
        # re-enabled one starts fresh rather than firing on stale state.
        self._alert_active.pop(name, None)
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

    def _rule_resend_interval_secs(self, name):
        """Seconds between repeated alerts for this rule, or None if repeats are
        disabled (a re-send only happens on recovery+re-trigger, or on a severity
        change for graded rules — see `escalated` in _check_all_rules). Falls back
        to the global resend_interval when the rule has no override."""
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
        while not self._stop_event.is_set():
            try:
                self._check_all_rules()
            except Exception as e:
                print(f"[monitor] Unhandled error in check loop: {e}")
            self._stop_event.wait(self.check_interval)

    def _check_all_rules(self):
        self.last_check_time = datetime.now()

        rules = {
            name: getattr(self, name)
            for name in sorted(dir(self))
            if name.startswith("rule_") and callable(getattr(self, name))
        }

        for name, fn in rules.items():
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
                    resend_secs = self._rule_resend_interval_secs(name)
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
                # Event rules self-clear each check; a "RECOVERED" for them is spurious.
                if was_alert and name not in self._EVENT_RULES:
                    self._send_recovery(name)
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

        Tunable via rule_options.rule_beam_off in monitor_config.json:
          thresholds — {severity: off_minutes} gradient, e.g.
                       {"warning": 1, "alert": 10}. The highest severity whose
                       minute threshold has been reached wins. Default: {"warning":
                       10} (must be comfortably above normal supercycle gaps and
                       NXCALS latency).
          off_minutes — legacy single-level form (used only if "thresholds" is
                        absent): pulse gap that counts as "beam down" at "warning".
        """
        opts = self.config.get("rule_options", {}).get("rule_beam_off", {})
        thresholds = opts.get("thresholds") or {"warning": opts.get("off_minutes", 10)}
        try:
            with open(BEAM_STATE_FILE) as f:
                st = json.load(f)
        except Exception:
            return False, "beam state not available (watcher not running)"
        if not st.get("connected"):
            return False, "beam watcher disconnected (covered by rule_beam_watcher_dead)"
        gap = st.get("seconds_since_pulse")
        if gap is None:
            return False, "no beam pulse seen yet since the watcher started"
        gap_min = gap / 60

        # Highest severity whose minute threshold has been reached.
        severity = None
        for sev, thr in sorted(thresholds.items(), key=lambda kv: kv[1]):
            if gap_min >= float(thr):
                severity = sev
        if severity:
            return severity, f"n_TOF beam has been OFF for {gap_min:.0f} min."
        return False, f"beam on (last pulse {gap:.0f}s ago)"

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
            return False, "no waveform nominal adopted yet — nothing to compare against"
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

    def rule_ssd_disk_space(self):
        """Alert as the OS/system SSD (/) fills up. Graded: warning at 70%, then
        alert / critical / emergency as it climbs (thresholds configurable via
        rule_options.rule_ssd_disk_space.thresholds)."""
        return self._disk_space_alert("rule_ssd_disk_space", "/", "SSD (/)")

    def rule_hdd_disk_space(self):
        """Alert as the data HDD (/mnt/data) fills up. Graded: warning at 70%, then
        alert / critical / emergency as it climbs (thresholds configurable via
        rule_options.rule_hdd_disk_space.thresholds)."""
        return self._disk_space_alert("rule_hdd_disk_space", "/mnt/data", "HDD (/mnt/data)")

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
