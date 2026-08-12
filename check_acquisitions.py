#!/usr/bin/env python3
"""Acceptance check for the finished runs.

Three things have to hold, per sub-run:

  1. completeness  -- every acquisition with non-empty raw has a decode and a
                      hits file for each of its FEUs, and one combined file;
  2. purity        -- each combined file's entry count equals the sum of its OWN
                      acquisition's per-FEU hits, so it is not a mixture;
  3. no truncation -- every FEU of one acquisition decoded to the SAME number of
                      events, since they all see the same triggers. NB this must
                      be checked per acquisition, never per file number:
                      `repair_truncated_decodes.py` groups by file number, so on
                      a sub-run that was stopped and restarted it compares two
                      unrelated acquisitions and would DELETE the shorter one's
                      decodes as "truncated". Do not run it on these runs.
  4. schema        -- every combined file in a sub-run has the same branch set.
                      The 2026-07-24 combiner adds trunc_left/trunc_right/
                      significance, so a run finished with the wrong build would
                      leave a sub-run holding two schemas and break any TChain
                      over it.

    python3 final_check.py RUNSROOT run_2 run_3 ...
"""
import collections
import json
import os
import re
import sys

import ROOT
ROOT.gErrorIgnoreLevel = ROOT.kFatal

FDF_RE = re.compile(r"^Mx17_(?P<tag>.+)_datrun_(?P<date>\d{6})_(?P<time>\d+H\d+)_"
                    r"(?P<num>\d{3})_(?P<feu>\d{2})\.fdf$")
ROOT_RE = re.compile(r"^Mx17_(?P<tag>.+)_datrun_(?P<date>\d{6})_(?P<time>\d+H\d+)_"
                     r"(?P<num>\d{3})_(?P<feu>\d{2})(_hits)?\.root$")
CMB_RE = re.compile(r"^Mx17_(?P<tag>.+)_datrun_(?P<date>\d{6})_(?P<time>\d+H\d+)_"
                    r"(?P<num>\d{3})_feu-combined_hits\.root$")


def ls(p):
    try:
        return sorted(os.listdir(p))
    except OSError:
        return []


def main():
    root = sys.argv[1]
    problems = collections.defaultdict(list)
    stats = collections.Counter()
    for run in sys.argv[2:]:
        rd = os.path.join(root, run)
        for sub in ls(rd):
            sd = os.path.join(rd, sub)
            if not os.path.isdir(sd):
                continue
            raw, dec, hits = (collections.defaultdict(dict) for _ in range(3))
            for f in ls(os.path.join(sd, "raw_daq_data")):
                m = FDF_RE.match(f)
                if not m or "_pedestals_" in f:
                    continue
                key = (m.group("date"), m.group("time"), m.group("num"))
                raw[key][int(m.group("feu"))] = os.path.getsize(
                    os.path.join(sd, "raw_daq_data", f))
            for inner, store in (("decoded_root", dec), ("hits_root", hits)):
                for f in ls(os.path.join(sd, inner)):
                    m = ROOT_RE.match(f)
                    if not m or "_pedestals_" in f:
                        continue
                    key = (m.group("date"), m.group("time"), m.group("num"))
                    store[key][int(m.group("feu"))] = os.path.join(sd, inner, f)

            cmb = {}
            schemas = {}
            for f in ls(os.path.join(sd, "combined_hits_root")):
                m = CMB_RE.match(f)
                if not m or "_pedestals_" in f:
                    continue
                key = (m.group("date"), m.group("time"), m.group("num"))
                path = os.path.join(sd, "combined_hits_root", f)
                tf = ROOT.TFile.Open(path)
                t = tf.Get("hits") if tf and not tf.IsZombie() else None
                if t is None:
                    problems["unreadable_combined"].append(f"{run}/{sub}/{f}")
                    if tf:
                        tf.Close()
                    continue
                cmb[key] = t.GetEntries()
                schemas[key] = tuple(sorted(b.GetName()
                                            for b in t.GetListOfBranches()))
                tf.Close()

            for key, feus in sorted(raw.items()):
                tag = f"{run}/{sub}/{'|'.join(key)}"
                if all(v == 0 for v in feus.values()):
                    stats["acq_zero_byte"] += 1
                    continue
                stats["acq"] += 1
                counts = {}
                for feu, p in sorted(dec.get(key, {}).items()):
                    tf = ROOT.TFile.Open(p)
                    t = tf.Get("nt") if tf and not tf.IsZombie() else None
                    counts[feu] = t.GetEntries() if t else None
                    if tf:
                        tf.Close()
                good = [c for c in counts.values() if c]
                if good and min(good) != max(good):
                    problems["truncated_decode"].append(
                        f"{tag} events per FEU {dict(sorted(counts.items()))}")
                if set(feus) - set(dec.get(key, {})):
                    problems["missing_decode"].append(
                        f"{tag} feus {sorted(set(feus) - set(dec.get(key, {})))}")
                if set(feus) - set(hits.get(key, {})):
                    problems["missing_hits"].append(
                        f"{tag} feus {sorted(set(feus) - set(hits.get(key, {})))}")
                if key not in cmb:
                    problems["missing_combined"].append(tag)
                    continue
                exp = 0
                for p in hits.get(key, {}).values():
                    tf = ROOT.TFile.Open(p)
                    t = tf.Get("hits")
                    exp += t.GetEntries() if t else 0
                    tf.Close()
                if cmb[key] != exp:
                    problems["mixed_combined"].append(
                        f"{tag} combined={cmb[key]} own_hits={exp}")
                else:
                    stats["combined_pure"] += 1

            if len(set(schemas.values())) > 1:
                byschema = collections.defaultdict(list)
                for k, s in schemas.items():
                    byschema[s].append("|".join(k))
                problems["schema_split"].append(
                    f"{run}/{sub}: " + " || ".join(
                        f"{len(v)} files with {len(s)} branches" +
                        ("(+significance)" if "significance" in s else "")
                        for s, v in byschema.items()))
        print(f"checked {run}", flush=True)

    print("\n==== STATS ====")
    for k, v in sorted(stats.items()):
        print(f"  {k}: {v}")
    print("\n==== PROBLEMS ====")
    if not problems:
        print("  none")
    for k, v in sorted(problems.items()):
        print(f"  {k}: {len(v)}")
        for x in v[:10]:
            print(f"     {x}")
        if len(v) > 10:
            print(f"     ... {len(v) - 10} more")
    json.dump({k: v for k, v in problems.items()},
              open(os.path.expanduser("~/x17proc/final_check.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
