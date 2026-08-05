#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Beam-state classification: WHY is the beam off?

Distinguishes, minute by minute, three ways of having no beam on target:

    on        n_TOF-destination cycles are carrying beam out of the PS
    off_ntof  the machine is delivering beam to OTHER users but NTOF is
              absent from the supercycle (or scheduled and dry) -> the stop
              is n_TOF-side: a requested access, an operator removal, or an
              n_TOF-specific interlock. The telegram cannot tell which.
    off_ps    NOBODY on the ejection line is getting beam -> machine-side
              (PS complex / injector) problem. NOTE: a concurrent n_TOF
              access is invisible behind this state (2026-08-04 afternoon:
              access 08:43-19:38 overlapped a complex-wide outage 11:30-18:00).
    no_data   no telegram cycles logged for the minute (NXCALS gap).

Sources (see memory ps-outage-vs-ntof-access-discrimination):

    CPS.TGM:DEST               PS telegram, one point per ~1.2 s basic period.
    F16.BCT372.TOF:INTENSITY   per-cycle ejected intensity (1e10 p). Despite
                               the .TOF name it logs nonzero on NTOF, FTARGET,
                               TT2_D3 and EAST_* cycles alike, so it doubles as
                               the "is the PS delivering to anyone" signal.

The purpose-built variables are dead in NXCALS (CIW.269.TT2NTOF:ST_PASS last
logged 2025-12; CPS.NTOF:CYCLE_STATUS empty since <=2022), so the telegram is
the only live discriminator.

Intensity points are matched to telegram cycles at < MATCH_TOL_S: the basic
period is 1.2 s, so a tolerance of 2 s silently relabels neighbouring cycles.
"""

import bisect
from datetime import datetime

TGM_DEST_VAR = "CPS.TGM:DEST"
BCT372_TOF_VAR = "F16.BCT372.TOF:INTENSITY"
CLASS_VARIABLES = [TGM_DEST_VAR, BCT372_TOF_VAR]

# A pulse below this (1e10 protons) is electronics noise, not beam: real TOF
# pulses are 400-900, and the parked/dump pulses seen so far are >600.
BEAM_PULSE_MIN_E10 = 50.0
MATCH_TOL_S = 0.5

CLASS_ON = "on"
CLASS_OFF_NTOF = "off_ntof"
CLASS_OFF_PS = "off_ps"
CLASS_NO_DATA = "no_data"

# Human wording shared by the GUI, Telegram alerts and the public page.
CLASS_LABELS = {
    CLASS_ON: "beam on",
    CLASS_OFF_NTOF: "nTOF-side stop (access / removed from supercycle, "
                    "other users getting beam)",
    CLASS_OFF_PS: "PS/machine-side outage (no beam to any user)",
    CLASS_NO_DATA: "no telegram data",
}
CLASS_LABELS_SHORT = {
    CLASS_ON: "on",
    CLASS_OFF_NTOF: "nTOF stop",
    CLASS_OFF_PS: "PS outage",
    CLASS_NO_DATA: "no data",
}

CLASS_CSV_FIELDS = ["timestamp", "unix_ts", "cycles", "ntof_sched",
                    "ntof_beam", "other_beam", "ntof_mean_e10", "beam_class"]


def classify_counts(cycles, ntof_sched, ntof_beam, other_beam):
    """Class of one aggregation bin from its cycle/pulse counts."""
    if cycles == 0:
        return CLASS_NO_DATA
    if ntof_beam > 0:
        return CLASS_ON
    if other_beam > 0:
        return CLASS_OFF_NTOF
    return CLASS_OFF_PS


def aggregate_minutes(dest_ts, dest_vals, int_ts, int_vals):
    """Fold raw telegram + per-cycle intensity into per-minute class rows.

    Returns {minute_unix_ts: row-dict} with CLASS_CSV_FIELDS keys. Timestamps
    may be numpy scalars straight from pytimber; everything is coerced here.
    """
    dts = []
    dvs = []
    for t, v in zip(dest_ts, dest_vals):
        try:
            dts.append(float(t))
        except (TypeError, ValueError):
            continue
        dvs.append(str(v))

    bins = {}

    def _bin(t):
        b = int(t // 60) * 60
        if b not in bins:
            bins[b] = {"cycles": 0, "ntof_sched": 0, "ntof_beam": 0,
                       "other_beam": 0, "ntof_sum": 0.0}
        return bins[b]

    for t, v in zip(dts, dvs):
        row = _bin(t)
        row["cycles"] += 1
        if v == "NTOF":
            row["ntof_sched"] += 1

    for t, v in zip(int_ts, int_vals):
        try:
            t = float(t)
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f <= BEAM_PULSE_MIN_E10:
            continue
        i = bisect.bisect_left(dts, t)
        best = None
        for j in (i - 1, i):
            if 0 <= j < len(dts):
                d = abs(dts[j] - t)
                if best is None or d < best[0]:
                    best = (d, j)
        if best is None or best[0] >= MATCH_TOL_S:
            # beam with no matchable telegram cycle: machine is alive, but
            # the destination is unknown -> count as non-NTOF (conservative:
            # never claims n_TOF beam that BCT477 would not confirm).
            _bin(t)["other_beam"] += 1
            continue
        row = _bin(t)
        if dvs[best[1]] == "NTOF":
            row["ntof_beam"] += 1
            row["ntof_sum"] += f
        else:
            row["other_beam"] += 1

    out = {}
    for b in sorted(bins):
        r = bins[b]
        mean = r["ntof_sum"] / r["ntof_beam"] if r["ntof_beam"] else 0.0
        out[b] = {
            "timestamp": datetime.fromtimestamp(b).isoformat(),
            "unix_ts": b,
            "cycles": r["cycles"],
            "ntof_sched": r["ntof_sched"],
            "ntof_beam": r["ntof_beam"],
            "other_beam": r["other_beam"],
            "ntof_mean_e10": round(mean, 2),
            "beam_class": classify_counts(r["cycles"], r["ntof_sched"],
                                          r["ntof_beam"], r["other_beam"]),
        }
    return out


def fetch_class_minutes(db, t0, t1):
    """Query NXCALS and return per-minute class rows for [t0, t1]."""
    res = db.get(CLASS_VARIABLES, t0, t1)
    dest_ts, dest_vals = res.get(TGM_DEST_VAR, ([], []))
    int_ts, int_vals = res.get(BCT372_TOF_VAR, ([], []))
    return aggregate_minutes(dest_ts, dest_vals, int_ts, int_vals)
