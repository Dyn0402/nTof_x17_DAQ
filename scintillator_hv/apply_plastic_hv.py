#!/usr/bin/env python3
"""Ramp the 8 plastic-PMT channels (CAEN slot 7, ch 0-7) to a new equalized
voltage set from a JSON file, using the same safe path as scint_hv_scan
(second CAEN session, disjoint-from-DAQ check, set_ch_v0 + vmon ramp verify).

Why (2026-07-19): re-equalize the plastics from the latest (Y88) calibration
before re-taking the plastic threshold ladder. This becomes the new operating
point, so --update-nominal rewrites scint_hv_config.py's nominal_v to match
(so a later scint_hv scan's end-restore keeps the new set, as on 2026-07-18).

Accepted file format (any of these key spellings per channel; all 8 required):
    {"plastic_A_L": 1310, "A_R": 1250, "BL": 1380, ...}
Values are volts. A hard range guard [--vmin,--vmax] refuses anything absurd.

Usage:
  .venv/bin/python scintillator_hv/apply_plastic_hv.py FILE.json --dry-run
  .venv/bin/python scintillator_hv/apply_plastic_hv.py FILE.json          # apply
  .venv/bin/python scintillator_hv/apply_plastic_hv.py FILE.json --update-nominal
"""
import argparse
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import scint_hv_config as cfg          # noqa: E402
import scint_hv_lib as lib             # noqa: E402


def _norm_key(k):
    """'plastic_A_L' / 'A_L' / 'AL' / 'a l' -> 'A_L'."""
    k = re.sub(r"(?i)^plastic[_\- ]?", "", str(k).strip())
    k = re.sub(r"[^A-Za-z]", "", k).upper()      # e.g. 'AL', 'AR'
    if len(k) == 2 and k[0] in "ABCD" and k[1] in "LR":
        return f"{k[0]}_{k[1]}"
    return k


def parse_file(path):
    """Return (kind, mapping):
      ('by_caen', {(slot, ch): volts})  -- calibration export with a 'pmts' map
          carrying an authoritative 'caen': 'slot:ch' and 'v_suggested'; or
      ('by_name', {'A_L': volts, ...})  -- flat {name: v} / {name: {v0: v}}."""
    with open(path) as f:
        raw = json.load(f)
    # Format A: calibration export (pmts.<PMT>.{caen, v_suggested})
    if isinstance(raw, dict) and isinstance(raw.get("pmts"), dict):
        out = {}
        for name, e in raw["pmts"].items():
            caen = str(e.get("caen", "")).strip()
            if ":" not in caen:
                raise SystemExit(f"pmt {name}: missing/blank 'caen' slot:channel")
            slot, ch = (int(x) for x in caen.split(":"))
            v = e.get("v_suggested", e.get("v0", e.get("voltage")))
            if v is None:
                raise SystemExit(f"pmt {name} ({caen}): no v_suggested/v0")
            out[(slot, ch)] = float(v)
        return "by_caen", out
    # Format B: flat {"channels": {...}} or {"plastic_A_L": v, ...}
    if isinstance(raw, dict) and isinstance(raw.get("channels"), dict):
        raw = raw["channels"]
    flat = {}
    for k, v in raw.items():
        if isinstance(v, dict):                  # {"A_L": {"v0": 1310}}
            v = v.get("v0", v.get("voltage", v.get("nominal_v")))
        flat[_norm_key(k)] = float(v)
    return "by_name", flat


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("file", help="JSON of new per-channel plastic voltages")
    ap.add_argument("--dry-run", action="store_true",
                    help="parse + validate + show deltas; DO NOT touch the crate")
    ap.add_argument("--update-nominal", action="store_true",
                    help="also rewrite scint_hv_config.py nominal_v to the new set")
    ap.add_argument("--vmin", type=float, default=1000.0)
    ap.add_argument("--vmax", type=float, default=1550.0)
    ap.add_argument("--daq-stopped", action="store_true",
                    help="skip the disjoint-from-DAQ check (plastics are DAQ-owned "
                         "in the config, but the run has ended so the CAEN session "
                         "is free). Guarded: refuses if daq_control.py is running.")
    args = ap.parse_args()

    if not os.path.exists(args.file):
        raise SystemExit(f"file not found: {args.file}")
    kind, newv = parse_file(args.file)

    # every configured plastic channel must be present, in range
    def _key(c):
        return (int(c["slot"]), int(c["channel"])) if kind == "by_caen" \
            else _norm_key(c["name"])
    targets, problems = [], []
    for c in cfg.SCINT_CHANNELS:
        k = _key(c)
        if k not in newv:
            problems.append(f"missing channel {c['name']} ({k}) in file")
            continue
        v = newv[k]
        if not (args.vmin <= v <= args.vmax):
            problems.append(f"{c['name']}={v:g} V outside guard "
                            f"[{args.vmin:g},{args.vmax:g}]")
        targets.append((c, v))
    extra = set(newv) - {_key(c) for c in cfg.SCINT_CHANNELS}
    if extra:
        problems.append(f"unrecognized channels in file: {sorted(extra)}")
    if problems:
        raise SystemExit("REFUSING TO APPLY:\n  - " + "\n  - ".join(problems))

    print(f"New plastic HV from {args.file}:")
    for c, v in targets:
        print(f"  {c['name']:>12} (7:{c['channel']})  {c['nominal_v']:>5} -> {v:>6.1f} V"
              f"   (Δ {v - c['nominal_v']:+.1f})")
    if args.dry_run:
        print("\n--dry-run: crate untouched. Re-run without --dry-run to ramp.")
        return

    if args.daq_stopped:
        import subprocess
        # NB: 'dream_daq_control.py' (the DREAM DAQ) is a DIFFERENT process and
        # does NOT own the CAEN session -- exclude it so it isn't a false match.
        out = subprocess.run(["pgrep", "-af", "daq_control.py"],
                             capture_output=True, text=True).stdout
        live = [ln for ln in out.splitlines()
                if ln.strip() and "dream_daq_control.py" not in ln]
        if live:
            raise SystemExit("--daq-stopped given but daq_control.py IS running:\n  "
                             + "\n  ".join(live)
                             + "\nrefusing to race the DAQ CAEN session")
        print("--daq-stopped: daq_control not running -> skipping disjoint check "
              "(crate session is free)")
    else:
        lib.validate_channels()   # aborts if any plastic channel overlaps the DAQ's
    from scint_hv_scan import _set_and_ramp   # noqa: E402
    with lib.open_session() as caen_hv:
        print("\nBefore (vmon):")
        for c, _ in targets:
            print(f"  {c['name']:>12}: {caen_hv.get_ch_vmon(7, c['channel']):.1f} V")
        print("\nRamping to new equalized set...")
        _set_and_ramp(caen_hv, targets)
        print("\nAfter (vmon):")
        for c, v in targets:
            print(f"  {c['name']:>12}: {caen_hv.get_ch_vmon(7, c['channel']):.1f} V (target {v:g})")

    if args.update_nominal:
        _rewrite_nominal(targets)
    print("\nDONE.")


def _rewrite_nominal(targets):
    """Rewrite each 'nominal_v': N in scint_hv_config.py to the applied value,
    matched by the channel 'name' on the same SCINT_CHANNELS line."""
    path = os.path.join(_HERE, "scint_hv_config.py")
    src = open(path).read()
    for c, v in targets:
        pat = (r"(\{'name':\s*'" + re.escape(c["name"]) +
               r"'[^}]*'nominal_v':\s*)\d+")
        src, n = re.subn(pat, lambda m: m.group(1) + str(int(round(v))), src)
        if n != 1:
            print(f"  WARN: nominal_v for {c['name']} not rewritten (matched {n})")
    open(path, "w").write(src)
    print(f"Updated nominal_v in {path}")


if __name__ == "__main__":
    main()
