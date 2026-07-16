#!/usr/bin/env python3
"""Shared helpers for the M3 (.242) coincidence-timing work (Tasks 2-3), used with
M1 offline: the 20 ns coincidence window and the delay sweep are both imposed by M3's
input Gate&Delay stage (wall=ch0, scint=ch1) rather than M1/M2 output monostables.

  * connect(ip)                         - connect+login an N1081B
  * snapshot_m3_inputs() / restore(...) - capture/restore M3 in-ch0..1 all sectors
  * set_m3_gd(d, ch, enable_gd, gate, delay, sections) - set one leg on given sectors,
                                          preserving status/invert, verified by readback
  * read_m5_rates(duration)             - per-section per-channel Hz on M5 (.244) scalers
                                          (SEC_A=walls B=scints C=sectors D=M4 taps)

Run on mx17-daq (board net). All writes are read-back verified; callers must restore.
"""
import time

from n1081b_sdk import N1081B

M3_IP = "192.168.10.242"
M5_IP = "192.168.10.244"
SECTIONS = ["SEC_A", "SEC_B", "SEC_C", "SEC_D"]
WALL_CH, SCINT_CH = 0, 1


def connect(ip):
    d = N1081B(ip)
    if not d.connect():
        raise RuntimeError(f"connect to {ip} failed")
    d.ws.settimeout(8)
    if not d.login("password"):
        raise RuntimeError(f"login to {ip} failed")
    return d


def _sec(d, name):
    return getattr(N1081B.Section, name)


def snapshot_m3_inputs(d):
    """{section_name: {ch: full_input_cfg_dict}} for ch0,ch1 all sectors."""
    snap = {}
    for sname in SECTIONS:
        s = _sec(d, sname)
        snap[sname] = {}
        for ch in (WALL_CH, SCINT_CH):
            snap[sname][ch] = d.get_input_channel_configuration(s, ch)['data']
    return snap


def restore_m3_inputs(d, snap):
    """Re-apply a snapshot to M3 in-ch0/ch1 all sectors; return True if all verify."""
    ok = True
    for sname, chans in snap.items():
        s = _sec(d, sname)
        for ch, c in chans.items():
            d.set_input_channel_configuration(
                s, int(ch), c['status'], c['enable_gd'], c['gate'], c['delay'], c['invert'])
            r = d.get_input_channel_configuration(s, int(ch))['data']
            ok = ok and (r['enable_gd'] == c['enable_gd'] and r['gate'] == c['gate']
                         and r['delay'] == c['delay'] and r['status'] == c['status'])
    return ok


def set_m3_gd(d, ch, enable_gd, gate, delay, sections=None):
    """Set G&D on input `ch` for the given sections (default all), preserving
    status/invert. Returns True only if every section's readback matches."""
    sections = sections or SECTIONS
    ok = True
    for sname in sections:
        s = _sec(d, sname)
        c = d.get_input_channel_configuration(s, ch)['data']
        d.set_input_channel_configuration(s, ch, c['status'], enable_gd, gate, delay, c['invert'])
        r = d.get_input_channel_configuration(s, ch)['data']
        good = (r['enable_gd'] == enable_gd and r['gate'] == gate and r['delay'] == delay)
        ok = ok and good
        if not good:
            print(f"    !! {sname} in-ch{ch} verify FAILED: "
                  f"gd={r['enable_gd']} gate={r['gate']} delay={r['delay']}")
    return ok


def read_m5_rates(duration, d=None):
    """Count M5 (.244) scalers over `duration` s. Returns {section_name: {ch: Hz}} for
    ch0-3 (lemo 0-3, the 4 sectors/walls/scints). Opens/closes its own connection
    unless a live one is passed."""
    own = d is None
    if own:
        d = connect(M5_IP)
    def snap():
        out = {}
        for s in N1081B.Section:
            res = d.get_function_results(s)
            data = (res or {}).get('data') or {}
            ctrs = data.get('counters') or []
            out[s.name] = [c.get('value') for c in ctrs]
        return out
    r0 = snap()
    t0 = time.time()
    time.sleep(duration)
    dt = time.time() - t0
    r1 = snap()
    rates = {}
    for s in N1081B.Section:
        a, b = r0[s.name], r1[s.name]
        n = min(len(a), len(b), 4)
        rates[s.name] = {i: round((b[i] - a[i]) / dt, 3) for i in range(n)}
    if own:
        d.disconnect()
    return rates
