#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
n1081b_module_map — the *design model* of the six N1081B trigger boards, plus the
merge that overlays a live read-back snapshot on top of it.

This is the single source of truth the DAQ-GUI "Trigger" tab and the standalone
diagram exporter both render.  It is derived from the build sheet
``~/Documents/ntof_trigger_logic/TRIGGER_SETUP_2026-07.md`` (§0.5 as-built table +
the per-module fix list) and the run_30 scan schedule
``config/n1081b_scan_schedule.json``.

Two layers:
  * DESIGN  — what each board / section / LEMO is *supposed* to do: role, physics
    meaning, where each input comes from and where each output goes, the intended
    threshold / monostable / gate&delay.  Static, hand-maintained here.
  * LIVE    — what the board actually reads back right now, taken from a snapshot
    JSON (either the per-sub-run ``n1081b_config.json`` written by daq_control via
    poll_modules, or a manual ``snapshots/dump_*.json``).  Overlaid per channel.

``build_state(snapshot, scan_active)`` returns a JSON-serialisable dict the front
ends render directly, so all the board knowledge lives in Python (testable, one
place) and the renderers stay dumb.

No board access, no heavy deps — safe to import from Flask.
"""
from __future__ import annotations

# --------------------------------------------------------------------------- codes
# Input signal standard code -> label (see summarize_dump.py).
STANDARD = {0: "NIM", 1: "TTL", 2: "DISCR"}

# Role -> accent colour (signal category). The panel chrome is always CAEN red;
# these tint the section header / signal chips so categories read at a glance.
ROLE_COLORS = {
    "sipm":   "#39c5b2",   # SiPM walls (teal)
    "liq":    "#a879e6",   # liquid-scint / plastic L1 (violet)
    "coinc":  "#f0c040",   # per-sector coincidence (amber)
    "phys":   "#3ddc84",   # physics triggers (green)
    "veto":   "#ef5b5b",   # veto (red)
    "scaler": "#4dabe8",   # scalers (blue)
    "pulse":  "#f2913d",   # pulser / gamma-flash countermeasure (orange)
}

CAEN_RED = "#c02434"


# --------------------------------------------------------------------------- helpers
def _in(lemo, src="", on=True, standard="DISCR", threshold=None, gd=False,
        gate=None, delay=0, invert=False, note=""):
    """One designed INPUT channel."""
    return {"lemo": lemo, "src": src, "on": on, "standard": standard,
            "threshold": threshold, "gd": gd, "gate": gate, "delay": delay,
            "invert": invert, "note": note}


def _out(lemo, dst="", on=True, mono=None, invert=False, note=""):
    """One designed OUTPUT channel."""
    return {"lemo": lemo, "dst": dst, "on": on, "mono": mono, "invert": invert,
            "note": note}


def _unused_in(lemo):
    return _in(lemo, src="—", on=False, note="unused")


def _unused_out(lemo):
    return _out(lemo, dst="—", on=False, note="unused")


def _sec(sid, role, color, physics, fn, summary, inputs, outputs,
         frm=None, to=None, note=""):
    """One section (A-D). ``frm``/``to`` are lists of {module?, section?, label}
    connector chips drawn on the left/right edge of the section."""
    return {"id": sid, "role": role, "color": color, "physics": physics,
            "fn_design": fn, "summary": summary,
            "inputs": inputs, "outputs": outputs,
            "from": frm or [], "to": to or [], "note": note}


# ============================================================================ DESIGN
# Board-net order 1..6 == 192.168.10.240..245 (consecutive, §0.5).
#
# Convention for connector chips: {"module": N} links to another N1081B page;
# {"module": None, "label": ...} is an external element (428F, detector, N93B,
# DREAM DAQ, PS pickup).

def _module1():
    """.240 — SiPM wall OR. Back ONLINE 2026-07-14 (reachable and reconfigurable);
    was offline 2026-07-11→14."""
    secs = []
    for sid, wall in zip("ABCD", (1, 2, 3, 4)):
        ins = [_in(i, src=f"428F#{wall} Σ seg{i+1} (T{i+1}+B{i+1})",
                   standard="DISCR", threshold=30) for i in range(4)]
        ins += [_unused_in(4), _unused_in(5)]
        outs = [
            _out(0, dst=f"M3.{sid} in0 — wall leg", mono=50),
            _out(1, dst="M5.A scaler tap", mono=50),
            _unused_out(2), _unused_out(3),
        ]
        secs.append(_sec(
            sid, role=f"Wall {wall} (S{wall})", color="sipm",
            physics=f"SiPM wall {wall} fired",
            fn="OR", summary=f"OR(seg1..4) → wall{wall}",
            inputs=ins, outputs=outs,
            frm=[{"module": None, "label": f"428F#{wall} — 4 segment-sums"}],
            to=[{"module": 3, "section": sid, "label": f"sector-{wall} AND"},
                {"module": 5, "label": "scaler"}],
        ))
    return {
        "n": 1, "ip": "192.168.10.240", "role": "SiPM wall OR",
        "color": "sipm", "role_long": "Discriminate + OR the 428F segment-sums → one 'wall fired' per wall",
        "online_expected": True,
        "note": "Back online 2026-07-14 (reachable and reconfigurable again). Was "
                "offline 2026-07-11→14 (network dead, ARP INCOMPLETE); during that "
                "outage the 20 ns coincidence window was moved downstream to M3 "
                "input G&D, where it remains.",
        "sections": secs,
    }


def _module2():
    """.241 — L1 discriminator. 2 plastics/wall on ALL four sections (lemo 0-1,
    th -15 mV), reverted 2026-07-14 via setup_plastic_pairs.py (undid the 07-13
    rolling plastic->liquid swap)."""
    secs = []
    for sid, sect in zip("ABCD", (1, 2, 3, 4)):
        ins = [_in(0, src=f"plastic A L1 S{sect} (BNC-T)", standard="DISCR", threshold=-15),
               _in(1, src=f"plastic B L1 S{sect} (BNC-T)", standard="DISCR", threshold=-15)]
        ins += [_unused_in(i) for i in range(2, 6)]
        outs = [
            _out(0, dst=f"M3.{sid} in1 — liq leg", mono=50),
            _out(1, dst="M5.B scaler tap", mono=100),
            _unused_out(2), _unused_out(3),
        ]
        secs.append(_sec(
            sid, role=f"L1 sector {sect} (2 plastics)", color="liq",
            physics=f"liq{sect} discriminated",
            fn="OR", summary=f"OR(L1 in) → liq{sect}",
            inputs=ins, outputs=outs,
            frm=[{"module": None, "label": f"L1 sector {sect} — BNC-T from detector"}],
            to=[{"module": 3, "section": sid, "label": f"sector-{sect} AND"},
                {"module": 5, "label": "scaler"}],
        ))
    return {
        "n": 2, "ip": "192.168.10.241", "role": "L1 discriminator",
        "color": "liq", "role_long": "Discriminate the layer-1 scintillator (behind each wall) → one 'liq fired' per sector",
        "online_expected": True,
        "note": "2 plastics/wall (lemo 0-1 OR) on all four sections at -15 mV, "
                "reverted 2026-07-14 via setup_plastic_pairs.py. Threshold is "
                "per-section; the two plastic inputs of a section share its level. "
                "This is the scint singles-rate knob for the ZS-vs-rate study.",
        "sections": secs,
    }


def _module3():
    """.242 — per-sector coincidence AND. Wall (in0) AND liq (in1), NIM in. The
    20 ns coincidence window lives on the INPUT gate&delay of both legs (moved
    here during M1's 07-11→14 outage; kept there). Output mono 30 ns."""
    secs = []
    for sid, sect in zip("ABCD", (1, 2, 3, 4)):
        ins = [
            _in(0, src=f"M1.{sid} — wall{sect}", standard="NIM", gd=True, gate=20, delay=0,
                note="20 ns coincidence window (G&D)"),
            _in(1, src=f"M2.{sid} — liq{sect}", standard="NIM", gd=True, gate=20, delay=0,
                note="20 ns coincidence window (G&D)"),
        ]
        ins += [_unused_in(i) for i in range(2, 6)]
        outs = [
            _out(0, dst=f"M4 — sector {sect}", mono=30),
            _out(1, dst="M5.C scaler tap", mono=30),
            _unused_out(2), _unused_out(3),
        ]
        secs.append(_sec(
            sid, role=f"Sector {sect} coincidence", color="coinc",
            physics=f"sector {sect} = wall{sect} ∧ liq{sect}",
            fn="AND", summary=f"wall{sect} AND liq{sect} → sector{sect}",
            inputs=ins, outputs=outs,
            frm=[{"module": 1, "section": sid, "label": f"wall{sect}"},
                 {"module": 2, "section": sid, "label": f"liq{sect}"}],
            to=[{"module": 4, "label": f"sector {sect}"},
                {"module": 5, "label": "scaler"}],
        ))
    return {
        "n": 3, "ip": "192.168.10.242", "role": "Sector coincidence (AND)",
        "color": "coinc", "role_long": "Per-sector AND of wall ∧ L1-scint → four sector triggers",
        "online_expected": True,
        "note": "20 ns coincidence window imposed at the INPUT gate&delay of BOTH "
                "legs (gate=20, delay=0) — moved here from the M1/M2 output monos "
                "during M1's 07-11→14 outage; kept here even though M1 is back "
                "online. Plateau scan: all four |center| ≤ 10 ns, so "
                "no per-sector delay trim. LA input-trigger mode is broken on this "
                "board — trigger on outputs.",
        "sections": secs,
    }


def _module4():
    """.243 — physics triggers + veto. A Singles / B Doubles (coincidence-gate
    >=2-of-4) / C or_veto / D final gated OR -> DREAM DAQ. This board is the one the
    run_30 scan schedule modulates (c_in0/1/4, d_in0/1)."""
    # A: Singles = OR(sec 1..4) on lemos 0,1,3,4 (panels 1,2,4,5).
    a_in = [
        _in(0, src="M3 — sector 1", standard="NIM"),
        _in(1, src="M3 — sector 2", standard="NIM"),
        _unused_in(2),
        _in(3, src="M3 — sector 3", standard="NIM"),
        _in(4, src="M3 — sector 4", standard="NIM"),
        _unused_in(5),
    ]
    a_out = [_out(0, dst="M4.C in0 — Singles", mono=50),
             _out(1, dst="M5.D scaler tap", mono=50), _unused_out(2), _unused_out(3)]
    secA = _sec("A", "Singles = OR(sectors)", "phys", "≥1 sector = Single",
                "OR", "OR(sec1..4) → Singles", a_in, a_out,
                frm=[{"module": 3, "label": "sectors 1..4"}],
                to=[{"module": 4, "section": "C", "label": "Singles"},
                    {"module": 5, "label": "scaler"}])

    # B: Doubles = COINCIDENCE GATE >=2-of-4 (FIRST opens 50 ns window), inputs
    # lemos 0,1,3,4. Outputs CH1,3 = pulse per coincidence; CH2,4 = the window.
    b_in = [
        _in(0, src="M3 — sector 1", standard="NIM"),
        _in(1, src="M3 — sector 2", standard="NIM"),
        _unused_in(2),
        _in(3, src="M3 — sector 3", standard="NIM"),
        _in(4, src="M3 — sector 4", standard="NIM"),
        _unused_in(5),
    ]
    b_out = [_out(0, dst="M4.C in1 — Doubles", mono=50, note="coincidence pulse"),
             _out(1, dst="window out", mono=50, note="gate window (do not tap)"),
             _out(2, dst="M5.D scaler tap", mono=50, note="coincidence pulse"),
             _unused_out(3)]
    secB = _sec("B", "Doubles ≥2-of-4", "phys", "≥2 sectors within 50 ns = Double",
                "COINCIDENCE GATE", "≥2-of-4 (FIRST, 50 ns window) → Doubles", b_in, b_out,
                frm=[{"module": 3, "label": "sectors 1..4"}],
                to=[{"module": 4, "section": "C", "label": "Doubles"},
                    {"module": 5, "label": "scaler"}],
                note="MAJORITY has no settable level (strict ≥3-of-4); Doubles uses "
                     "COINCIDENCE GATE instead. Counters = [TOTAL, CH1, CH2, CH4, CH5].")

    # C: or_veto — OR(Singles, Doubles, pulser) then GATED by lemo5 veto.
    c_in = [
        _in(0, src="M4.A — Singles", standard="NIM", note="scan: c_in0"),
        _in(1, src="M4.B — Doubles", standard="NIM", on=False, note="scan: c_in1 (off in run_30)"),
        _unused_in(2),
        _unused_in(3),
        _in(4, src="M6.D — random pulser (Poisson ~667 Hz)", standard="NIM", note="scan: c_in4"),
        _in(5, src="N93B timer — 30 ms enable gate (inv-NIM)", standard="NIM", invert=True,
            note="VETO input: HIGH in-window = ENABLE, LOW outside = veto"),
    ]
    c_out = [_out(0, dst="M4.D in1 — gated trigger", mono=50),
             _out(1, dst="M5 scaler tap", mono=50), _unused_out(2), _unused_out(3)]
    secC = _sec("C", "OR + veto gate", "veto", "physics OR, gated by PS-window / DREAM-busy veto",
                "OR_VETO", "OR(Singles,Doubles,pulser) GATED by veto", c_in, c_out,
                frm=[{"module": 4, "section": "A", "label": "Singles"},
                     {"module": 4, "section": "B", "label": "Doubles"},
                     {"module": 6, "section": "D", "label": "pulser"},
                     {"module": None, "label": "N93B 30 ms gate"}],
                to=[{"module": 4, "section": "D", "label": "gated trigger"}],
                note="SDK reports or_veto's function name as 'or'. lemo5 reads ~100% "
                     "high on an LA triggered on Singles (window-gated) = ENABLE, not stuck.")

    # D: final OR(S,D) gated -> DREAM DAQ. in0 = PS/gamma-flash line, in1 = M4.C.
    d_in = [
        _in(0, src="PS / γ-flash trigger line", standard="NIM",
            note="scan: d_in0 (delayed +1980 ns G&D in scint scans)"),
        _in(1, src="M4.C — gated trigger", standard="NIM", note="scan: d_in1"),
        _unused_in(2), _unused_in(3), _unused_in(4), _unused_in(5),
    ]
    d_out = [_out(0, dst="DREAM DAQ — master trigger", mono=50),
             _out(1, dst="M5 scaler tap", mono=50), _unused_out(2), _unused_out(3)]
    secD = _sec("D", "Master trigger → DREAM", "phys", "final trigger to the DREAM DAQ",
                "OR", "OR(PS line, gated physics) → DREAM", d_in, d_out,
                frm=[{"module": None, "label": "PS / γ-flash line"},
                     {"module": 4, "section": "C", "label": "gated trigger"}],
                to=[{"module": None, "label": "DREAM DAQ"},
                    {"module": 5, "label": "scaler"}])

    return {
        "n": 4, "ip": "192.168.10.243", "role": "Physics triggers + veto",
        "color": "phys", "role_long": "Build Singles / Doubles, gate with the PS-window veto, emit the DREAM master trigger",
        "online_expected": True,
        "note": "The scan watcher modulates THIS board per sub-run (targets "
                "c_in0/c_in1/c_in4, d_in0/d_in1) — see the active-scan banner. "
                "d_in0 gets a +1980 ns G&D delay only in the scint scans.",
        "sections": [secA, secB, secC, secD],
        "scan_targets": {  # target-name -> (section, channel) for scan highlighting
            "c_in0": ("C", 0), "c_in1": ("C", 1), "c_in4": ("C", 4),
            "d_in0": ("D", 0), "d_in1": ("D", 1),
        },
    }


def _module5():
    """.244 — scalers (monitoring only): 4x free-running counter tapping every
    upstream stage. Also the board mod5_timetag_logger streams."""
    taps = {
        "A": ("walls (M1)", ["wall1", "wall2", "wall3", "wall4"]),
        "B": ("liq (M2)", ["liq1", "liq2", "liq3", "liq4"]),
        "C": ("sectors (M3)", ["sector1", "sector2", "sector3", "sector4"]),
        "D": ("physics (M4)", ["Singles", "Doubles", "PS/pulser", "spare"]),
    }
    src_mod = {"A": 1, "B": 2, "C": 3, "D": 4}
    secs = []
    for sid in "ABCD":
        label, chans = taps[sid]
        ins = [_in(i, src=chans[i], standard="NIM") if i < 4 else _unused_in(i)
               for i in range(6)]
        outs = [_unused_out(i) for i in range(4)]
        secs.append(_sec(
            sid, role=f"Scaler — {label}", color="scaler",
            physics=f"count rates of {label}",
            fn="COUNTER", summary=f"free-running counters: {', '.join(chans[:4])}",
            inputs=ins, outputs=outs,
            frm=[{"module": src_mod[sid], "label": label}],
            to=[{"module": None, "label": "rate monitoring / time-tag"}],
        ))
    return {
        "n": 5, "ip": "192.168.10.244", "role": "Scalers",
        "color": "scaler", "role_long": "Free-running counters tapping every upstream stage — monitoring only, not in the trigger path",
        "online_expected": True,
        "note": "Do NOT poll while mod5_timetag_logger is streaming this board "
                "(broadcast send_data would desync reads). A ch2 once read status "
                "False yet counted — verify the panel↔SDK mapping.",
        "sections": secs,
    }


def _module6():
    """.245 — pulser / gamma-flash countermeasure. Old fw 2022.3.0.0, sn 23011.
    A fanout(PS/T0, delay 9600) / B fanout mesh injection (4 outs) / C fanout SiPM
    enable (inv TTL) / D pulse_generator."""
    a_in = [_in(0, src="PS / T0 (TTL)", standard="TTL", gd=True, gate=15, delay=9600,
                note="9.6 µs delay — skip γ-flash + fast n")]
    a_in += [_unused_in(i) for i in range(1, 6)]
    a_out = [_out(i, dst="PS/T0 fan-out", mono=None) for i in range(4)]
    secA = _sec("A", "PS/T0 fan-out + 9.6 µs delay", "pulse",
                "delayed PS/T0 distribution", "FANOUT",
                "fan-out PS/T0, ch0 G&D delay 9600 ns", a_in, a_out,
                frm=[{"module": None, "label": "PS / T0 pickup"}],
                to=[{"module": None, "label": "downstream PS chain"}])

    b_in = [_in(0, src="mesh trigger source", standard="TTL", gd=True, gate=100, delay=1260,
                note="injection delay 1260 ns (pre-run)")]
    b_in += [_unused_in(i) for i in range(1, 6)]
    b_out = [_out(i, dst="Micromegas mesh charge-inject", mono=500,
                  note="scan: mesh_b (on/off per sub-run)") for i in range(4)]
    secB = _sec("B", "Mesh charge-injection (4 outs)", "pulse",
                "inject a mesh pulse to counteract the γ-flash", "FANOUT",
                "fan-out mono 500 ns → 4 mesh-injection outputs", b_in, b_out,
                frm=[{"module": None, "label": "mesh trigger source"}],
                to=[{"module": None, "label": "Micromegas mesh ×4"}],
                note="Toggled ON/OFF each sub-run by the scan schedule (mesh_b).")

    c_in = [_in(0, src="flash-window source", standard="NIM")]
    c_in += [_unused_in(i) for i in range(1, 6)]
    c_out = [_out(0, dst="SiPM enable / blank", mono=1000, invert=True),
             _out(1, dst="SiPM enable / blank", mono=1000, invert=True),
             _unused_out(2), _unused_out(3)]
    secC = _sec("C", "SiPM enable / blank (inv TTL)", "pulse",
                "blank SiPM readout during the γ-flash", "FANOUT",
                "inverted TTL out, mono 1000 ns → SiPM enable (2 used)", c_in, c_out,
                frm=[{"module": None, "label": "flash window"}],
                to=[{"module": None, "label": "SiPM enable ×2"}])

    d_in = [_unused_in(i) for i in range(6)]
    d_out = [_out(0, dst="M4.C in4 — random pulser", mono=100,
                  note="period 1.5 ms / width 100 ns ≈ 667 Hz"),
             _unused_out(1), _unused_out(2), _unused_out(3)]
    secD = _sec("D", "Test / random pulser", "pulse",
                "Poisson ~667 Hz pulser → gated at M4.C", "PULSE_GENERATOR",
                "pulse-gen ~667 Hz → M4.C in4", d_in, d_out,
                to=[{"module": 4, "section": "C", "label": "random pulser"}])

    return {
        "n": 6, "ip": "192.168.10.245", "role": "Pulser / γ-flash countermeasure",
        "color": "pulse", "role_long": "Not in the main trigger path — PS/T0 fan-out, Micromegas mesh charge-injection, SiPM blanking, and the random test pulser",
        "online_expected": True,
        "note": "6th board (sn 23011), still on old firmware 2022.3.0.0. SDK "
                "configure_or / logic6 calls time out on this fw (fanout / pulse-gen "
                "fine). B (mesh injection) is toggled per sub-run by the scan schedule.",
        "sections": [secA, secB, secC, secD],
        "scan_targets": {"mesh_b": ("B", None)},
    }


def design_modules():
    """The full static design model, modules 1..6."""
    return [_module1(), _module2(), _module3(), _module4(), _module5(), _module6()]


# The PS-flash veto chain and detector front-end, for the overview page.
EXTERNAL_CHAIN = {
    "ps_veto": [
        {"label": "PS pickup", "detail": "proton-synchrotron flash pickup"},
        {"label": "ext N1081B .A", "detail": "delay 9.6 µs — skip γ-flash + fast n"},
        {"label": "N93B dual timer", "detail": "30 ms NIM gate = trigger-enable window"},
        {"label": "invert → M4.C veto", "detail": "HIGH in-window = enable, LOW = veto"},
    ],
}


# ============================================================================ MERGE
def _unwrap(node):
    """SDK envelope -> its .data payload (or {} on error/missing)."""
    if isinstance(node, dict):
        return node.get("data", {}) or {}
    return {}


def _boards_from_snapshot(snapshot):
    """Accept either poll_modules format ({polled_at,label,boards:{ip:board}}) or the
    raw dump_module_info format ({ip:board}); return {ip: board} + meta."""
    if not isinstance(snapshot, dict):
        return {}, {}
    if "boards" in snapshot and isinstance(snapshot["boards"], dict):
        meta = {k: snapshot.get(k) for k in ("polled_at", "label")}
        return snapshot["boards"], meta
    # raw dump: top-level keys are IPs
    if all(isinstance(v, dict) and ("sections" in v or "ip" in v)
           for v in snapshot.values() if isinstance(v, dict)) and snapshot:
        return snapshot, {}
    return {}, {}


_SEC_INDEX = {"A": 0, "B": 1, "C": 2, "D": 3}


def _live_section(board, sid):
    """Pull the live read-back for one section from a snapshot board node.
    Returns (fn_name, {ch: in_data}, {ch: out_data}, sec_input_cfg) or Nones."""
    if not isinstance(board, dict):
        return None
    secs = board.get("sections") or {}
    node = secs.get(f"SEC_{sid}")
    if not node:
        return None
    # function name from the board-level all-sections list
    fn = None
    sf = board.get("sections_function")
    for r in _unwrap(sf).get("data", []) if isinstance(_unwrap(sf), dict) else []:
        pass
    sf_data = sf.get("data") if isinstance(sf, dict) else None
    if isinstance(sf_data, list):
        for r in sf_data:
            if r.get("section") == _SEC_INDEX[sid]:
                fn = r.get("function_name")
    in_ch, out_ch = {}, {}
    for ch, cc in (node.get("input_channels") or {}).items():
        in_ch[int(ch)] = _unwrap(cc)
    for ch, cc in (node.get("output_channels") or {}).items():
        out_ch[int(ch)] = _unwrap(cc)
    sec_in = _unwrap(node.get("input_configuration"))
    return {"fn": fn, "in_ch": in_ch, "out_ch": out_ch, "sec_in": sec_in}


def _merge_section(sec, live):
    """Overlay a live section read-back onto a design section. Adds `live` fields to
    each input/output and a section-level status."""
    sec = dict(sec)
    threshold = live.get("sec_in", {}).get("threshold") if live else None
    imp = live.get("sec_in", {}).get("imp") if live else None
    std_code = live.get("sec_in", {}).get("standard") if live else None
    sec["fn_live"] = (live or {}).get("fn")
    sec["live_threshold"] = threshold
    sec["live_impedance"] = ("50Ω" if imp in (True, "true", 1) else "HI-Z") if imp is not None else None
    sec["live_standard"] = STANDARD.get(std_code, std_code) if std_code is not None else None
    sec["has_live"] = live is not None

    out_inputs = []
    for din in sec["inputs"]:
        m = dict(din)
        lc = (live or {}).get("in_ch", {}).get(din["lemo"]) if live else None
        if lc:
            m["live"] = {
                "on": bool(lc.get("status")),
                "gd": bool(lc.get("enable_gd")),
                "gate": lc.get("gate"),
                "delay": lc.get("delay"),
                "invert": bool(lc.get("invert")),
            }
            # a live threshold is per-section
            if threshold is not None:
                m["live"]["threshold"] = threshold
        out_inputs.append(m)
    out_outputs = []
    for dout in sec["outputs"]:
        m = dict(dout)
        lc = (live or {}).get("out_ch", {}).get(dout["lemo"]) if live else None
        if lc:
            m["live"] = {
                "on": bool(lc.get("status")),
                "mono": lc.get("mono_value") if lc.get("enable_mono") else None,
                "enable_mono": bool(lc.get("enable_mono")),
                "invert": bool(lc.get("invert")),
            }
        out_outputs.append(m)
    sec["inputs"] = out_inputs
    sec["outputs"] = out_outputs
    return sec


def build_state(snapshot=None, scan_active=None, source_meta=None):
    """Merge a live snapshot (optional) over the design model.

    Parameters
    ----------
    snapshot : dict or None
        Parsed n1081b_config.json / dump_*.json, or None for design-only.
    scan_active : dict or None
        Contents of config/n1081b_scan_active.json (tag/note/at), or None.
    source_meta : dict or None
        Extra provenance to echo back (path, kind, age_s).

    Returns a JSON-serialisable dict for the renderers.
    """
    boards, snap_meta = _boards_from_snapshot(snapshot or {})
    scan_active = scan_active or None
    active_targets = _resolve_active_targets(scan_active)

    modules = []
    for mod in design_modules():
        board = boards.get(mod["ip"])
        online = bool(board) and not board.get("errors", {}).get("connect") \
            and not board.get("errors", {}).get("fatal")
        fw = sn = None
        if board:
            ver = _unwrap(board.get("version"))
            fw = ver.get("software_version")
            sn = ver.get("serial_number")
        m = {k: mod[k] for k in ("n", "ip", "role", "role_long", "color", "note")}
        m["online_expected"] = mod.get("online_expected", True)
        m["online"] = online
        m["has_live"] = board is not None
        m["fw"] = fw
        m["sn"] = sn
        merged_secs = []
        for sec in mod["sections"]:
            live = _live_section(board, sec["id"]) if board else None
            msec = _merge_section(sec, live)
            # mark scan-target channels active for this module
            _tag_scan_targets(mod, msec, active_targets)
            merged_secs.append(msec)
        m["sections"] = merged_secs
        modules.append(m)

    return {
        "success": True,
        "modules": modules,
        "external_chain": EXTERNAL_CHAIN,
        "active_scan": scan_active,
        "role_colors": ROLE_COLORS,
        "source": dict(source_meta or {}, **snap_meta),
    }


def _resolve_active_targets(scan_active):
    """From the scan-active file, get the per-target overrides currently applied
    (so the UI can highlight which channels the scan is driving)."""
    if not scan_active:
        return {}
    # scan_active may carry the resolved 'cfg' (target-name -> override dict)
    cfg = scan_active.get("cfg") or {}
    return cfg


def _tag_scan_targets(mod, msec, active_targets):
    """Annotate input/output channels that the active scan is currently driving."""
    targets = mod.get("scan_targets") or {}
    if not targets or not active_targets:
        return
    for tname, (sec_id, ch) in targets.items():
        if sec_id != msec["id"]:
            continue
        ov = active_targets.get(tname)
        if ov is None:
            continue
        # input_status / delay / enable_gd / gate touch inputs; output_status touches outputs
        touch_in = any(k in ov for k in ("input_status", "delay", "enable_gd", "gate"))
        touch_out = "output_status" in ov
        for coll, touch in (("inputs", touch_in), ("outputs", touch_out)):
            if not touch:
                continue
            for c in msec[coll]:
                if ch is None or c["lemo"] == ch:
                    c["scan"] = {"target": tname, "override": ov}


if __name__ == "__main__":
    import json
    import sys
    snap = None
    if len(sys.argv) > 1:
        with open(sys.argv[1]) as f:
            snap = json.load(f)
    print(json.dumps(build_state(snap), indent=2, default=str))
