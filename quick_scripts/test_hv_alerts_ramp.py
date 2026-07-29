#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Scenario tests for the hv_alerts ramp phase. No crate, no network — feeds
synthetic readings to HVAlerter and checks what it would have sent.

The archived hv_monitor.csv files cover healthy ramps and real trips, but
contain no stalled or failed ramp, so those paths are exercised here.

    python3 quick_scripts/test_hv_alerts_ramp.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hv_alerts import HVAlerter  # noqa: E402

DRIFT_A = (9, 0)   # card 9 = drift, ch 0 = detector A
RESIST_A = (5, 1)  # card 5 = resist, ch 1 = detector A


def make_alerter():
    a = HVAlerter()
    a.token = a.chat_id = None  # never actually send
    a._cfg["ramp_slow_notice_s"] = 0  # off unless a test wants it
    sent = []
    orig = a._send

    def spy(key, text, respect_throttle=False, now=None):
        before = a._last_sent.get(key)
        orig(key, text, respect_throttle=respect_throttle, now=now)
        if not respect_throttle or a._last_sent.get(key) != before:
            sent.append(text)
    a._send = spy
    return a, sent


def reading(vmon, v0, power=1, imon=0.1):
    return {"power": power, "vmon": vmon, "imon": imon, "v0": v0}


def feed(alerter, t_start, seconds, fn, step=1):
    """Call evaluate() once per second for `seconds`; fn(t_rel) -> readings."""
    for dt in range(0, seconds, step):
        alerter.evaluate(fn(dt), now=t_start + dt)


def check(name, sent, must_have=(), must_not_have=()):
    ok = True
    for frag in must_have:
        if not any(frag in s for s in sent):
            print(f"FAIL {name}: expected a message containing {frag!r}")
            ok = False
    for frag in must_not_have:
        if any(frag in s for s in sent):
            print(f"FAIL {name}: unexpected message containing {frag!r}: "
                  f"{[s for s in sent if frag in s]}")
            ok = False
    print(f"{'PASS' if ok else 'FAIL'} {name}"
          f"  ({len(sent)} msg{'' if len(sent) == 1 else 's'})")
    for s in sent:
        print(f"       | {s.splitlines()[0]}")
    return ok


def test_healthy_ramp_is_silent():
    """600 -> 800 V at 4 V/s, the archive's typical rate. Nothing should fire."""
    a, sent = make_alerter()
    a.begin_sub_run("healthy", {})
    a.begin_ramp(now=0)
    feed(a, 0, 60, lambda dt: {DRIFT_A: reading(min(600 + 4 * dt, 800), 800)})
    a.end_ramp(ok=True, now=60)
    feed(a, 60, 60, lambda dt: {DRIFT_A: reading(800, 800)})
    return check("healthy ramp is silent", sent, must_not_have=("ALERT", "STALL"))


def test_slow_ramp_beyond_old_grace_is_silent():
    """0.9 V/s over 100 s — the slowest real ramp seen, and 3x the old 30 s
    grace. This is the case that used to cry wolf every time."""
    a, sent = make_alerter()
    a.begin_sub_run("slow", {})
    a.begin_ramp(now=0)
    feed(a, 0, 100, lambda dt: {RESIST_A: reading(440 + 0.9 * dt, 530)})
    a.end_ramp(ok=True, now=100)
    return check("slow (100 s) ramp is silent", sent,
                 must_not_have=("ALERT", "STALL"))


def test_stalled_ramp_alerts():
    """Ramp starts, then sticks 60 V short of setpoint (e.g. current-limited).
    Must alert, and well before set_hvs's 180 s timeout."""
    a, sent = make_alerter()
    a.begin_sub_run("stalled", {})
    a.begin_ramp(now=0)
    feed(a, 0, 120, lambda dt: {RESIST_A: reading(min(440 + 4.0 * dt, 470), 530)})
    return check("stalled ramp alerts", sent, must_have=("RAMP STALLED",))


def test_trip_during_ramp_alerts():
    """Channel ramps, comes on, then trips off mid-ramp. Must alert."""
    a, sent = make_alerter()
    a.begin_sub_run("trip", {})
    a.begin_ramp(now=0)
    feed(a, 0, 60, lambda dt: ({DRIFT_A: reading(600 + 4 * dt, 800)} if dt < 30
                               else {DRIFT_A: reading(0.0, 800, power=0)}))
    return check("trip during ramp alerts", sent, must_have=("TRIPPED DURING RAMP",))


def test_power_on_lag_is_not_a_trip():
    """A channel that was off starts the sub-run reading power=0 with the new
    setpoint already applied, until set_hvs powers it on. Not a trip."""
    a, sent = make_alerter()
    a.begin_sub_run("poweron", {})
    a.begin_ramp(now=0)
    feed(a, 0, 60, lambda dt: ({DRIFT_A: reading(0.0, 800, power=0)} if dt < 8
                               else {DRIFT_A: reading(min(4 * (dt - 8), 800), 800)}))
    a.end_ramp(ok=True, now=60)
    return check("power-on lag is not a trip", sent, must_not_have=("ALERT",))


def test_ramp_failure_alerts_with_detail():
    """set_hvs gave up (HVRampError -> daq_control skips the sub-run). This must
    always be announced, naming the off-target channels."""
    a, sent = make_alerter()
    a.begin_sub_run("failed", {})
    a.begin_ramp(now=0)
    feed(a, 0, 180, lambda dt: {DRIFT_A: reading(min(600 + 4.0 * dt, 700), 800),
                                RESIST_A: reading(530, 530)})
    a.end_ramp(ok=False, detail="HV did not ramp within 180s", now=180)
    return check("ramp failure alerts with off-target detail", sent,
                 must_have=("RAMP FAILED", "SKIPPED", "DRIFT A: 700.0 / 800 V"))


def test_deviation_after_ramp_still_alerts():
    """The whole point of the alerter must survive: a channel that sags after a
    good ramp still fires on the normal sustain window."""
    a, sent = make_alerter()
    a.begin_sub_run("sag", {})
    a.begin_ramp(now=0)
    feed(a, 0, 60, lambda dt: {DRIFT_A: reading(min(600 + 4 * dt, 800), 800)})
    a.end_ramp(ok=True, now=60)
    feed(a, 60, 120, lambda dt: {DRIFT_A: reading(800 if dt < 30 else 740, 800)})
    return check("post-ramp sag still alerts", sent, must_have=("V deviation",))


def test_dead_man_expiry():
    """If end_ramp() never arrives, alerting must come back, not stay blind.
    Drift A stalls during the phase; resist A is healthy until after the phase
    expires, so its deviation proves normal alerting resumed."""
    a, sent = make_alerter()
    a._cfg["ramp_max_s"] = 60
    a.begin_sub_run("deadman", {})
    a.begin_ramp(now=0)
    feed(a, 0, 200,
         lambda dt: {DRIFT_A: reading(740, 800),
                     RESIST_A: reading(530 if dt < 90 else 500, 530)}, step=5)
    return check("ramp phase expires and alerting resumes", sent,
                 must_have=("RAMP STALLED", "ramp phase expired",
                            "RESIST A V deviation"))


def test_slow_but_successful_ramp_notices():
    a, sent = make_alerter()
    a._cfg["ramp_slow_notice_s"] = 150
    a.begin_sub_run("slownotice", {})
    a.begin_ramp(now=0)
    a.end_ramp(ok=True, now=170)
    return check("slow-but-ok ramp gets a notice", sent, must_have=("🐢",))


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    results = [t() for t in tests]
    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)
