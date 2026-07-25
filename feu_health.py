#!/usr/bin/env python3
"""FEU / TCM crate reachability checks.

Written after the 2026-07-24 incident (docs/HANDOFF_2026-07-24_feu3_dropout.md):
FEU 3 dropped off the network mid-run, RunCtrl aborted configuration on every
subsequent sub-run, and daq_control marked all of them complete anyway — 51 of
run_75's grid points recorded nothing and nothing alarmed.

Two independent uses:

  * preflight  — `python3 feu_health.py` before starting a run; exit 0 only if
                 the whole crate answers. Non-zero means DO NOT start.
  * monitoring — `sweep()` feeds flask_app/monitor.py's rule_feu_unreachable.

Design notes
------------
* The FEU IP map is read from the live RunCtrl template
  (`<BASE_DATA_DIR>dream_config/Tcm_Mx17_July.cfg`), never hardcoded as a
  guess. FEU IPs are NON-CONTIGUOUS (.44 .83 .110 .111 .118 .43 .81 .82); the
  .101-.108 block looks like the obvious FEU range, answers nothing, and yields
  a false "whole crate is gone" diagnosis.
* ARP state is reported alongside ping because it separates fault classes: an
  `<incomplete>` ARP entry means the board is not answering ARP at all (dead at
  the link/board level), whereas a cached MAC with failing ping points at a hung
  IP stack. Only the former is a "power cycle will fix it" story.
* ICMP is necessary but NOT sufficient on this subnet — it has shown UDP dead
  while ICMP was fine (07-22 switch swap). A green sweep means "worth trying a
  configure", not "the data path works".
* The N1081B logic modules live on the SAME subnet at .240-.245 and wedge if
  talked to carelessly. They are explicitly excluded and must never be added
  here; see n1081b/CLAUDE.md.
"""

import argparse
import json
import os
import re
import subprocess
import sys

_REPO_DIR = os.path.dirname(os.path.abspath(__file__))

# The TCM's IP is not listed in the cfg (only `Tcm UdpPort`), so it is pinned
# here. Port 16000 is the command channel.
TCM_IP = "192.168.10.32"

# Never probe these: N1081B logic modules, same subnet, wedge easily.
FORBIDDEN_PREFIXES = tuple(f"192.168.10.{240 + i}" for i in range(6))

_FEU_IP_RE = re.compile(r"^\s*Feu\s+(\d+)\s+NetChan_Ip\s+([0-9.]+)", re.I)

# Fallback map, used only if the cfg cannot be read. Kept in sync with the cfg;
# the cfg always wins so a re-cable is picked up without editing this file.
_FALLBACK_FEU_MAP = {1: "192.168.10.44",  2: "192.168.10.83",  3: "192.168.10.110",
                     4: "192.168.10.111", 5: "192.168.10.118", 6: "192.168.10.43",
                     7: "192.168.10.81",  8: "192.168.10.82"}


def _default_cfg_path():
    """Path to the live RunCtrl template, resolved the same way run_config_beam does."""
    try:
        if _REPO_DIR not in sys.path:
            sys.path.append(_REPO_DIR)
        from run_config_beam import BASE_DATA_DIR
        return f"{BASE_DATA_DIR}dream_config/Tcm_Mx17_July.cfg"
    except Exception:
        return "/mnt/data/x17/beam_july/dream_config/Tcm_Mx17_July.cfg"


def load_feu_map(cfg_path=None):
    """{feu_number: ip} from the RunCtrl cfg. Commented-out FEUs are skipped, so a
    disabled `#Feu 9 ...` line never becomes a phantom board to alarm on.

    Falls back to _FALLBACK_FEU_MAP if the cfg is unreadable — a missing cfg must
    not silently produce an empty map, because "0 FEUs checked" would look like a
    clean sweep."""
    path = cfg_path or _default_cfg_path()
    feus = {}
    try:
        with open(path) as f:
            for line in f:
                if line.lstrip().startswith("#"):
                    continue
                m = _FEU_IP_RE.match(line)
                if m:
                    feus[int(m.group(1))] = m.group(2)
    except Exception:
        pass
    if not feus:
        return dict(_FALLBACK_FEU_MAP), False
    return feus, True


def ping(ip, count=2, timeout_s=1):
    """True if the host answers ICMP. `count` tries so a single dropped packet on a
    busy 10 GbE link does not read as a dead board."""
    try:
        return subprocess.run(["ping", "-c", str(count), "-W", str(timeout_s), ip],
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL,
                              timeout=count * timeout_s + 3).returncode == 0
    except Exception:
        return False


def arp_mac(ip):
    """Cached MAC for `ip`, or None if the ARP entry is absent/incomplete."""
    try:
        out = subprocess.run(["arp", "-n", ip], capture_output=True, text=True,
                             timeout=5).stdout
    except Exception:
        return None
    m = re.search(r"([0-9a-f]{2}(?::[0-9a-f]{2}){5})", out, re.I)
    return m.group(1) if m else None


def sweep(cfg_path=None, include_tcm=True):
    """Probe every FEU (and the TCM) once.

    Returns {ok, missing, feus: {n: {...}}, tcm: {...}|None, cfg_ok, summary}.
    `missing` is the list of human labels that failed, ready to drop into an alert.
    """
    feu_map, cfg_ok = load_feu_map(cfg_path)
    feus, missing = {}, []

    for n in sorted(feu_map):
        ip = feu_map[n]
        if ip.startswith(FORBIDDEN_PREFIXES):
            continue                      # never touch the N1081B boards
        alive = ping(ip)
        mac = arp_mac(ip)
        feus[n] = {"ip": ip, "ping": alive, "mac": mac,
                   # No ARP entry at all is the strong signal: the board is not
                   # answering ARP, so it is down at the link level rather than
                   # merely unresponsive at the IP layer.
                   "arp_incomplete": mac is None}
        if not alive:
            missing.append(f"FEU {n} ({ip})")

    tcm = None
    if include_tcm:
        alive = ping(TCM_IP)
        tcm = {"ip": TCM_IP, "ping": alive, "mac": arp_mac(TCM_IP)}
        if not alive:
            missing.append(f"TCM ({TCM_IP})")

    n_ok = sum(1 for v in feus.values() if v["ping"])
    summary = f"{n_ok}/{len(feus)} FEUs" + (
        f" + TCM {'OK' if tcm and tcm['ping'] else 'DOWN'}" if tcm else "")
    return {"ok": not missing, "missing": missing, "feus": feus, "tcm": tcm,
            "cfg_ok": cfg_ok, "summary": summary}


def format_report(res):
    lines = []
    if not res["cfg_ok"]:
        lines.append("!! could not read the FEU map from the cfg — using the built-in "
                     "fallback; verify the cfg path before trusting this")
    for n in sorted(res["feus"]):
        d = res["feus"][n]
        state = "OK  " if d["ping"] else "DOWN"
        mac = d["mac"] or "<incomplete>"
        lines.append(f"FEU {n}  {d['ip']:<15} ping={state} mac={mac}")
    if res["tcm"]:
        lines.append(f"TCM    {res['tcm']['ip']:<15} "
                     f"ping={'OK' if res['tcm']['ping'] else 'DOWN'}")
    lines.append("-" * 46)
    if res["ok"]:
        lines.append(f"ALL PRESENT ({res['summary']}) — safe to configure")
        lines.append("NOTE: ICMP only. A green sweep does not prove the UDP data path; "
                     "check the first sub-run is full-size before trusting the grid.")
    else:
        lines.append("!! MISSING: " + ", ".join(res["missing"]))
        lines.append("!! DO NOT START A RUN — RunCtrl will abort configuration and "
                     "daq_control will still mark sub-runs complete (empty data).")
        dead_link = [f"FEU {n}" for n, d in res["feus"].items()
                     if not d["ping"] and d["arp_incomplete"]]
        if dead_link:
            lines.append("   " + ", ".join(dead_link) +
                         " not answering ARP -> dead at the link/board level "
                         "(power cycle territory, not an IP-stack hang).")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="Check every FEU + the TCM is reachable.")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--quiet", action="store_true", help="print nothing, just set exit code")
    ap.add_argument("--cfg", default=None, help="override the Tcm_*.cfg path")
    ap.add_argument("--no-tcm", action="store_true", help="skip the TCM check")
    a = ap.parse_args()

    res = sweep(cfg_path=a.cfg, include_tcm=not a.no_tcm)
    if a.json:
        print(json.dumps(res, indent=2))
    elif not a.quiet:
        print(format_report(res))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
