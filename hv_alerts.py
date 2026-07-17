#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HV alerting for the CAEN drift/resist channels.

This piggy-backs on the 1 Hz reads that ``hv_control.monitor_hvs`` already does
during a run — it adds NO extra crate reads (only I0Set, read once per sub-run
for an over-current reference) and opens NO new CAEN session. All the alerter
does is evaluate cheap in-memory state and dispatch a Telegram message on a
short-lived daemon thread, so a slow/failed send can never stall HV monitoring
or hold ``caen_lock``.

Conditions (per channel, sustained for at least ``sustain_s`` seconds):
  drift  (card 9):  |vmon - vset| > drift.v_tol  OR  imon >= frac*I0Set  OR off
  resist (card 5):  |vmon - vset| > resist.v_tol  OR  imon >= frac*I0Set  OR off

Drift is meant to be rock-stable, so its sustain window is short (5 s). Resist
runs at compliance, so it alerts only when it sits at over-current for longer
(15 s). A hard trip (powered off while a setpoint is applied) alerts on its own
short window. Transient blips shorter than the window never alert, and a
"RECOVERED" note is sent when a firing condition clears.

Thresholds live in ``config/hv_alert_config.json`` and are re-read on mtime
change, so they can be tuned mid-run without restarting anything.
"""

import os
import json
import math
import threading
import time

import requests

_HERE = os.path.dirname(os.path.abspath(__file__))
ALERT_CONFIG_PATH = os.path.join(_HERE, "config", "hv_alert_config.json")
MONITOR_CONFIG_PATH = os.path.join(_HERE, "config", "monitor_config.json")
TELEGRAM_URL = "https://api.telegram.org/bot{token}/{method}"

# Card (a.k.a. slot) -> electrode class. This is the physical crate wiring:
# DREAM uses card 5 for resist meshes and card 9 for drift cathodes.
CARD_KIND = {5: "resist", 9: "drift"}

DEFAULT_CONFIG = {
    "enabled": True,
    "resend_interval_minutes": 15,
    # Suppress all alerts for this long after a sub-run's monitoring starts, so a
    # legitimate HV ramp (vmon climbing to setpoint over ~20-30 s) is not flagged
    # as a voltage deviation. In the normal run flow set_hvs already waits for the
    # ramp before monitoring begins, so this is a belt-and-suspenders guard.
    "ramp_grace_s": 30,
    # A trip powers the channel off while a setpoint is still applied — urgent,
    # so it fires on its own short window regardless of drift/resist.
    "poweroff_sustain_s": 5,
    # Over-current means truly current-limited: imon near the I0Set limit AND the
    # channel can no longer hold its setpoint (vmon drooped by > overcurrent_droop_v).
    # This is the CAEN "OvC" state and avoids false alarms from normal high current
    # draw while the channel is still regulating voltage. Set droop to 0 for a pure
    # imon>=frac*I0Set test. Uses only readings already in hand — no extra crate reads.
    "overcurrent_droop_v": 1.0,
    "drift":  {"v_tol": 2.0, "sustain_s": 5,  "overcurrent_frac": 0.90},
    "resist": {"v_tol": 5.0, "sustain_s": 15, "overcurrent_frac": 0.90},
}


def _is_num(x):
    try:
        return not math.isnan(float(x))
    except (TypeError, ValueError):
        return False


def _detector_letter(kind, channel):
    """Human label for a channel: drift card9 ch0-3 = A-D, resist card5 ch1-4 = A-D."""
    try:
        ch = int(channel)
    except (TypeError, ValueError):
        return None
    if kind == "drift" and 0 <= ch <= 3:
        return "ABCD"[ch]
    if kind == "resist" and 1 <= ch <= 4:
        return "ABCD"[ch - 1]
    return None


class HVAlerter:
    """Sustained-condition HV alerter. One instance lives across sub-runs so the
    resend throttle survives sub-run boundaries (a persistently-bad channel is
    not re-announced every sub-run)."""

    def __init__(self, config_path=ALERT_CONFIG_PATH, monitor_config_path=MONITOR_CONFIG_PATH):
        self.config_path = config_path
        self.monitor_config_path = monitor_config_path
        self._cfg = dict(DEFAULT_CONFIG)
        self._cfg_mtime = None
        self._load_config(force=True)

        self.token, self.chat_id = self._load_telegram_creds()

        # Per-channel state keyed by "slot:channel":
        #   {"bad_since": float|None, "category": str|None, "alerted": bool}
        self._state = {}
        # Resend throttle keyed by "slot:channel" -> last-send epoch. Survives
        # sub-run boundaries.
        self._last_sent = {}
        self._i0set = {}      # "slot:channel" -> float (current limit), or absent
        self._last_reason = {}  # "slot:channel" -> last firing reason (for recovery msg)
        self._sub_run = ""
        self._sub_run_start = None

    # ---------------------------------------------------------------- config
    def _load_config(self, force=False):
        """(Re)load the alert config if the file changed. Missing/invalid file
        falls back to DEFAULT_CONFIG so the alerter always runs."""
        try:
            mtime = os.path.getmtime(self.config_path)
        except OSError:
            if force:
                self._cfg = dict(DEFAULT_CONFIG)
            return
        if not force and mtime == self._cfg_mtime:
            return
        try:
            with open(self.config_path) as f:
                user = json.load(f)
            cfg = dict(DEFAULT_CONFIG)
            cfg.update({k: v for k, v in user.items() if k not in ("drift", "resist")})
            for kind in ("drift", "resist"):
                merged = dict(DEFAULT_CONFIG[kind])
                merged.update(user.get(kind, {}))
                cfg[kind] = merged
            self._cfg = cfg
            self._cfg_mtime = mtime
        except Exception as e:  # noqa: BLE001 — never let a bad config kill monitoring
            print(f"[hv_alerts] bad {self.config_path}, using defaults: {e}")
            self._cfg = dict(DEFAULT_CONFIG)

    def _load_telegram_creds(self):
        try:
            with open(self.monitor_config_path) as f:
                mc = json.load(f)
            return mc.get("telegram_token"), mc.get("telegram_chat_id")
        except Exception as e:  # noqa: BLE001
            print(f"[hv_alerts] no telegram creds ({e}); alerts will be logged only")
            return None, None

    # -------------------------------------------------------------- sub-run
    def begin_sub_run(self, sub_run_name, i0set):
        """Start of a new monitored sub-run. Reset per-channel duration tracking
        (fresh run = fresh timers) but KEEP the resend throttle so a channel that
        stays bad across a boundary is not re-announced. ``i0set`` maps
        "slot:channel" -> current limit (float) for the over-current test."""
        self._sub_run = sub_run_name
        self._state = {}
        self._i0set = dict(i0set or {})
        self._sub_run_start = None  # set on first evaluate() -> ramp-grace anchor

    # -------------------------------------------------------------- evaluate
    def _classify(self, slot, channel, power, vmon, imon, v0):
        """Return (bad, category, reason) or (False, None, "") for one channel.
        Returns None to mean 'no usable data this tick' (leave state unchanged)."""
        try:
            kind = CARD_KIND.get(int(slot))
        except (TypeError, ValueError):
            kind = None
        if kind is None:
            return (False, None, "")  # not a DREAM drift/resist channel

        cfg = self._cfg[kind]
        label = _detector_letter(kind, channel)
        tag = f"{kind.upper()} {label}" if label else f"{kind} {slot}:{channel}"

        # Hard trip: a setpoint is applied but the channel reads off.
        set_on = _is_num(v0) and float(v0) > 0
        if set_on and power not in ("", None) and int(power) == 0:
            return (True, "off", f"{tag} POWERED OFF (tripped?) — set {float(v0):.0f} V")

        # Everything below needs live readings; a failed read (nan/'') means
        # 'unknown', so we hold state rather than flag or clear.
        if not (_is_num(vmon) and _is_num(imon)):
            return None

        vmon, imon = float(vmon), float(imon)

        if set_on and abs(vmon - float(v0)) > cfg["v_tol"]:
            return (True, "vdev",
                    f"{tag} V deviation: vmon {vmon:.1f} V vs set {float(v0):.0f} V "
                    f"(> {cfg['v_tol']:.0f} V)")

        key = f"{slot}:{channel}"
        i0 = self._i0set.get(key)
        if _is_num(i0) and float(i0) > 0 and imon >= cfg["overcurrent_frac"] * float(i0):
            # Confirm the channel is actually current-limited (voltage drooped),
            # not just drawing high current while still holding setpoint.
            droop = self._cfg.get("overcurrent_droop_v", 1.0)
            current_limited = (not set_on) or (vmon < float(v0) - droop)
            if current_limited:
                return (True, "overcurrent",
                        f"{tag} over-current: imon {imon:.2f} µA ≥ "
                        f"{cfg['overcurrent_frac']*100:.0f}% of I0Set {float(i0):.2f} µA, "
                        f"vmon {vmon:.1f} < set {float(v0):.0f} V")

        return (False, None, "")

    def evaluate(self, readings, now=None):
        """Update state from one poll and fire/clear alerts.

        ``readings``: {(slot, channel): {"power","vmon","imon","v0"}}. Pure
        in-memory work plus, at most, a fire-and-forget send thread — safe to
        call from the monitor loop without holding ``caen_lock``."""
        self._load_config()
        if not self._cfg.get("enabled", True):
            return
        if now is None:
            now = time.time()

        # Ramp-settling grace: ignore the first ramp_grace_s of a sub-run so a
        # legitimate ramp to setpoint is not flagged as a deviation.
        if self._sub_run_start is None:
            self._sub_run_start = now
        if now - self._sub_run_start < self._cfg.get("ramp_grace_s", 30):
            return

        for (slot, channel), r in readings.items():
            key = f"{slot}:{channel}"
            res = self._classify(slot, channel, r.get("power"), r.get("vmon"),
                                 r.get("imon"), r.get("v0"))
            if res is None:
                continue  # no usable data; hold state
            bad, category, reason = res
            st = self._state.setdefault(key, {"bad_since": None, "category": None,
                                             "alerted": False})

            if not bad:
                if st["alerted"]:
                    self._send(key, f"✅ HV RECOVERED — {self._last_reason.get(key, key)}")
                st.update(bad_since=None, category=None, alerted=False)
                continue

            # A changed condition category restarts the sustain timer.
            if st["category"] != category:
                st.update(bad_since=now, category=category, alerted=False)

            sustain = self._sustain_for(slot, category)
            if (now - st["bad_since"]) >= sustain:
                # Fire once at the crossing, then _send's throttle re-reminds
                # every resend_interval while the condition persists.
                held = now - st["bad_since"]
                self._remember_reason(key, reason)
                self._send(key, f"⚠️ HV ALERT (ongoing {held:.0f} s): {reason}",
                           respect_throttle=True, now=now)
                st["alerted"] = True

    def _remember_reason(self, key, reason):
        self._last_reason[key] = reason

    def _sustain_for(self, slot, category):
        if category == "off":
            return self._cfg.get("poweroff_sustain_s", 5)
        try:
            kind = CARD_KIND.get(int(slot), "resist")
        except (TypeError, ValueError):
            kind = "resist"
        return self._cfg[kind]["sustain_s"]

    # --------------------------------------------------------------- dispatch
    def _send(self, key, text, respect_throttle=False, now=None):
        now = now if now is not None else time.time()
        if respect_throttle:
            resend_s = self._cfg.get("resend_interval_minutes", 15) * 60
            last = self._last_sent.get(key)  # None = never sent for this channel
            if last is not None and now - last < resend_s:
                return
            self._last_sent[key] = now
        stamped = f"{text}\n(sub-run {self._sub_run})" if self._sub_run else text
        print(f"[hv_alerts] {stamped}")
        if not (self.token and self.chat_id):
            return
        threading.Thread(target=self._send_telegram, args=(stamped,), daemon=True).start()

    def _send_telegram(self, text):
        try:
            r = requests.post(
                TELEGRAM_URL.format(token=self.token, method="sendMessage"),
                json={"chat_id": self.chat_id, "text": text}, timeout=10)
            r.raise_for_status()
        except Exception as e:  # noqa: BLE001 — send failures must stay silent to the loop
            print(f"[hv_alerts] telegram send failed: {e}")
