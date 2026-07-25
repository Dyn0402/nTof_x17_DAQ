#!/usr/bin/env python3
"""Did a sub-run actually record data?

Companion to feu_health.py, written after the 2026-07-24 FEU 3 incident
(docs/HANDOFF_2026-07-24_feu3_dropout.md). feu_health answers "can we reach the
crate"; this answers the question that actually matters — "did bytes land on
disk" — and so catches the same class of failure no matter what caused it (dead
FEU, dead TCM, bad config, RunCtrl abort).

The 07-24 signature, for reference:

    RunCtrl log:  PedMemInit: UdpSocket_ReqResp failed for feu_id=3 port=1398
                  FeuConfig: PedMemInit failed for feu_id=3 port=1398 with -4
    on disk:      Mx17_..._datrun_..._NN.fdf present but ZERO BYTES
    sub-run dir:  ~305 MB (pedestal/threshold staging only) vs ~5-6 GB real

Two independent detectors are used because either alone can be fooled:
  * the RunCtrl log error   — immediate and names the guilty FEU, but only
                              covers failures RunCtrl itself reports;
  * zero datrun bytes       — catches everything else, but needs a grace period
                              so a sub-run that has only just started (files
                              created, not yet filled) is not called dead.
"""

import argparse
import json
import os
import re
import sys
import time

DREAM_RUN_DIR = "/home/mx17/july_dream/dream_run"

# RunCtrl's own words when a FEU does not answer during configuration — the FATAL
# pedestal-memory init failure only. The feu_id reported is the cfg slot number
# (1-8), which maps straight onto feu_health.load_feu_map().
#
# ⚠️ Deliberately narrow. RunCtrl logs plenty of *recoverable* per-DREAM SPI
# complaints on a perfectly healthy run, e.g. observed live on 2026-07-24:
#     DreamConfigCheck: DreamRead failed for feu_id=6 Dream=7 reg=10 ...
#     FeuConfig: DreamSpiConfigCheck failed for feu_id=6 with -2; attempt to
#                reconfigure with slow DreamConfig
# It retries, succeeds, and the sub-run records normally. An earlier, looser
# pattern here matched those and would have paged the shift crew on a good run.
# Match ONLY PedMemInit, which is the failure that actually aborts configuration.
_RUNCTRL_FAIL_RE = re.compile(
    r"(?:PedMemInit:\s*UdpSocket_ReqResp\s+failed|FeuConfig:\s*PedMemInit\s+failed)"
    r".*?feu_id=(\d+)", re.I)

# A sub-run whose newest file is younger than this may legitimately still be
# filling, so zero bytes is not yet evidence of failure. Only used by the
# byte-count detector; a RunCtrl error is conclusive immediately.
DEFAULT_GRACE_S = 180

STATUS_GOOD, STATUS_EMPTY, STATUS_PENDING = "good", "empty", "pending"


def _datrun_bytes(subrun_dir):
    """(total bytes, file count) across this sub-run's datrun .fdf files.

    Only `*datrun*.fdf` counts. The pedestal/threshold `.prg` staging files are
    written even by a failed sub-run — counting the whole directory is what makes
    a dead sub-run look like a plausible 305 MB instead of the zero it really is.
    """
    total, n = 0, 0
    try:
        for name in os.listdir(subrun_dir):
            if "datrun" in name and name.endswith(".fdf"):
                try:
                    total += os.path.getsize(os.path.join(subrun_dir, name))
                    n += 1
                except OSError:
                    pass
    except OSError:
        pass
    return total, n


def datrun_bytes_any(*dirs):
    """(total datrun bytes, dir that had them) across several candidate locations.

    A sub-run's data exists in two places at different times: RunCtrl writes it to
    the SSD staging dir (`~/july_dream/dream_run/<run>/<subrun>/`) while the sub-run
    is live, and it lands in the HDD `<run>/<subrun>/raw_daq_data/` at the end.
    Checking both means the caller does not have to care which stage it is at.

    Note the HDD raw_daq_data dir is pre-populated with the ~8 copied pedestal
    `Mx17_pedestals_pedthr_*.fdf` files (~305 MB) before any beam data arrives —
    which is exactly why this counts only `*datrun*.fdf` and never directory size.
    """
    for d in dirs:
        if not d:
            continue
        total, _ = _datrun_bytes(d)
        if total:
            return total, d
    return 0, None


def _runctrl_failure(subrun_dir):
    """(feu_id, line) for the first RunCtrl configuration failure, else (None, None)."""
    try:
        logs = [f for f in os.listdir(subrun_dir)
                if f.startswith("RunCtrl") and f.endswith(".log")]
    except OSError:
        return None, None
    for log in sorted(logs):
        try:
            with open(os.path.join(subrun_dir, log), errors="replace") as fh:
                for line in fh:
                    m = _RUNCTRL_FAIL_RE.search(line)
                    if m:
                        return int(m.group(1)), line.strip()
        except OSError:
            continue
    return None, None


def _newest_mtime(subrun_dir):
    newest = 0
    try:
        for name in os.listdir(subrun_dir):
            try:
                newest = max(newest, os.path.getmtime(os.path.join(subrun_dir, name)))
            except OSError:
                pass
    except OSError:
        pass
    return newest


def classify_subrun(subrun_dir, grace_s=DEFAULT_GRACE_S, min_bytes=1):
    """Classify one sub-run staging directory.

    Returns {status, reason, feu_id, datrun_bytes, n_datrun, age_s, dir}.
      good    — datrun bytes >= min_bytes
      empty   — RunCtrl reported a config failure, OR no datrun bytes and the
                directory has been quiet longer than `grace_s`
      pending — too young to judge (still being written)
    """
    out = {"dir": subrun_dir, "status": STATUS_PENDING, "reason": "", "feu_id": None}

    feu_id, line = _runctrl_failure(subrun_dir)
    total, n = _datrun_bytes(subrun_dir)
    age = time.time() - (_newest_mtime(subrun_dir) or time.time())
    out.update({"datrun_bytes": total, "n_datrun": n, "age_s": round(age)})

    # Bytes on disk always win. A sub-run that recorded data is good even if the
    # log grumbled — RunCtrl recovers from most complaints, and reporting a healthy
    # sub-run as failed is the fastest way to get an alert channel ignored.
    if total >= min_bytes:
        out["status"] = STATUS_GOOD
        if feu_id is not None:
            out["reason"] = f"recorded data despite RunCtrl warning for feu_id={feu_id}"
        return out

    # No data AND RunCtrl aborted configuration: conclusive, skip the grace period,
    # and name the guilty board. This is the detector that would have caught the
    # 07-24 incident on the very first lost sub-run instead of the 51st.
    if feu_id is not None:
        out.update({"status": STATUS_EMPTY, "reason": "runctrl_config_abort",
                    "feu_id": feu_id, "log_line": line})
        return out

    if age > grace_s:
        out.update({"status": STATUS_EMPTY,
                    "reason": "no_datrun_bytes" if n else "no_datrun_files"})
        return out

    out["reason"] = "still writing"
    return out


def scan_run(run_name, dream_run_dir=DREAM_RUN_DIR, grace_s=DEFAULT_GRACE_S):
    """Classify every sub-run staging dir of `run_name`.

    Note the SSD staging area is pruned by space_watcher once a sub-run is backed
    up, so a *missing* directory means "already archived", NOT "failed" — absent
    dirs are simply not reported. Judge only what is still on the SSD.
    """
    root = os.path.join(dream_run_dir, run_name)
    res = {"run": run_name, "root": root, "subruns": [],
           "n_good": 0, "n_empty": 0, "n_pending": 0, "bad_feus": set()}
    try:
        names = sorted(d for d in os.listdir(root)
                       if os.path.isdir(os.path.join(root, d)))
    except OSError:
        res["bad_feus"] = []
        return res

    for name in names:
        c = classify_subrun(os.path.join(root, name), grace_s=grace_s)
        c["name"] = name
        res["subruns"].append(c)
        res[f"n_{c['status']}"] += 1
        if c["status"] == STATUS_EMPTY and c["feu_id"] is not None:
            res["bad_feus"].add(c["feu_id"])
    res["bad_feus"] = sorted(res["bad_feus"])
    return res


def latest_run(dream_run_dir=DREAM_RUN_DIR):
    """Most recently modified run_* directory, or None."""
    try:
        runs = [(os.path.getmtime(os.path.join(dream_run_dir, d)), d)
                for d in os.listdir(dream_run_dir)
                if d.startswith("run_") and os.path.isdir(os.path.join(dream_run_dir, d))]
    except OSError:
        return None
    return max(runs)[1] if runs else None


def main():
    ap = argparse.ArgumentParser(description="Check whether sub-runs recorded data.")
    ap.add_argument("run", nargs="?", help="run name (default: most recent)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--grace", type=int, default=DEFAULT_GRACE_S,
                    help=f"seconds before a quiet empty sub-run counts as failed "
                         f"(default {DEFAULT_GRACE_S})")
    ap.add_argument("--dir", default=DREAM_RUN_DIR,
                    help=f"staging root holding <run>/<subrun>/ (default {DREAM_RUN_DIR})")
    a = ap.parse_args()

    run = a.run or latest_run(a.dir)
    if not run:
        print("no run found", file=sys.stderr)
        return 2

    res = scan_run(run, dream_run_dir=a.dir, grace_s=a.grace)
    if a.json:
        print(json.dumps(res, indent=2))
        return 1 if res["n_empty"] else 0

    print(f"run {res['run']}: {res['n_good']} good, {res['n_empty']} EMPTY, "
          f"{res['n_pending']} pending  (SSD staging only; archived sub-runs are "
          f"pruned and not shown)")
    for c in res["subruns"]:
        if c["status"] == STATUS_EMPTY:
            extra = f" feu_id={c['feu_id']}" if c["feu_id"] is not None else ""
            print(f"  EMPTY  {c['name']}  ({c['reason']}{extra}, "
                  f"{c['n_datrun']} datrun files, {c['datrun_bytes']} bytes)")
    if res["n_empty"]:
        if res["bad_feus"]:
            print(f"\n!! RunCtrl could not configure FEU(s) {res['bad_feus']} — "
                  f"run `python3 feu_health.py` and fix the crate before re-taking.")
        print("!! These sub-runs recorded NOTHING. Re-take them; see "
              "docs/HANDOFF_2026-07-24_feu3_dropout.md")
    return 1 if res["n_empty"] else 0


if __name__ == "__main__":
    sys.exit(main())
