# HANDOFF 2026-08-12 — finishing the July campaign's DREAM processing on EOS

**What this is.** The campaign is over and all DREAM data is on
`/eos/experiment/ntof/data/x17/july_beam/`. A survey found runs that were not
fully decoded/combined. Finishing them turned up a second, worse problem: a
class of **combined_hits files that mix two different acquisitions**. Both come
from the same root cause, and both are now fixed.

Everything below was done on **lxplus against EOS**; nothing was run on the DAQ
machine, and no raw data was touched.

---

## 1. Root cause: the pipeline keys on the file number, not the acquisition

`processor_watcher` identifies a unit of work by the 3-digit *file number* in the
DREAM filename. A sub-run that is **stopped and restarted reuses those numbers**,
so one sub-run can hold several acquisitions all called `b000`, distinguished
only by the `<yymmdd>_<HH>H<MM>` timestamp.

Three consequences, all present on EOS:

| where | what it does |
|---|---|
| `_get_processed_file_nums` | marks `b000` done as soon as **one** acquisition has a combined file — the others are never revisited |
| `_get_feu_hits_map(hits_dir, fnum)` | collapses **every** acquisition's hits into one `{FEU: path}` dict, last-one-wins per FEU |
| `reprocess_run.py` | inherits both, so pointing it at these runs rewrites the same mixture |

The second one is the damaging one. The combiner concatenates whatever it is
handed, so the single file it writes per file number can hold FEUs from two
different acquisitions. Measured, not inferred:

```
run_9/scan10_dr800_A495_04
  acquisition 12H41  per-FEU hits [326113, 404655, 130, 104, 106512, 89635, 184227, 231187]
  acquisition 14H23  per-FEU hits [138958, 184062, 10429, 17834, 395311, 380597, 204566, 263536]
  combined (named 12H41)          [326113, 404655, 130, 104, 395311, 380597, 184227, 263536]
                                                        ^^^^^^  ^^^^^^          ^^^^^^
                                   FEUs 5, 6 and 8 come from the 14H23 acquisition,
                                   1 h 42 min later
```

Since the combiner appends rather than matching on `eventId`, the result is one
file holding two unrelated trigger streams whose event IDs both start at 1.

**Scope, measured across all 161 runs:** 101 acquisitions sit in a colliding file
number. Of the combined files they produced, **21 were verified mixtures**
(entry count ≠ sum of that acquisition's own per-FEU hits) and 46 were verified
clean; 50 acquisitions had no combined file at all.

Affected runs: **3, 9, 33, 34, 37, 71**. Runs 34 and 71 were reported *complete*
by the QA table, because both of their acquisitions did get a (mixed) combined
file — the deficit that flags a run as "partial" never appeared.

## 2. What was incomplete, and why

Sub-runs whose backlog the watcher never returned to (`stale_run_days: 1` plus
newest-first ordering) simply stopped where they were. Broken down by acquisition
across all 161 runs:

| class | count | runs |
|---|---|---|
| never decoded | 37 | run_39 (1), run_54 (36) |
| partly decoded (some FEUs) | 14 | run_2, run_3, run_18, run_31, run_37 ×2, run_43, run_52 ×5, run_54, run_73 |
| decoded + analysed, never combined | 13 | run_3 ×2, run_9 ×5, run_33 ×3, run_37, run_71 ×2 |
| **raw exists but is 0 bytes** | 8 | run_19, run_49, run_66, run_68, run_104 ×2, run_124, run_161 |
| sub-run directory with no raw at all | 11 | run_4, run_6, run_10, run_20, run_21 ×2, run_22 ×2, run_53, run_55, run_148 |

The last two classes are **not recoverable and not a defect**: the DAQ opened an
acquisition that never wrote data (all eight FDFs 0 bytes), or a sub-run
directory that was abandoned. They should be recorded as "no data", not as
"unprocessed" — counting them as missing is what makes a healthy run look short.

### run_54 specifically — a second pedestal set

`run_54` stalled for a reason of its own. Its pedestal directory
`pedestals/pedestals_07-18-26_14-06-43/pedestals/` holds **two complete 8-FEU
sets**:

- `Mx17_pedestals_pedthr_260718_14H07_*` — taken 12 min before run_54 started
- `Mx17_pedestals_pedthr_260720_11H37_*` — taken **two days later**; a 07-20
  pedestal run was written into the 07-18 directory

`_analyze_file` skips any FEU with more than one matching pedestal, silently and
forever, so every unfinished acquisition of run_54 was stuck. This is the same
trap as the `pedestals_07-20-26_11-40-44` case in
`HANDOFF_2026-07-21_processing_chain_partial_data.md` §5; that one was tidied on
the DAQ disk but **both sets are still side by side on EOS**.

**Decision (2026-08-12): run_54 uses `260718_14H07`** — contemporaneous with the
run, and the set its already-processed 24 acquisitions used (their hits predate
07-20), as did runs 55-58. Runs 55-58 need nothing; they finished before the
second set landed.

## 3. Which recipe the new products were made with

The analyser and combiner both changed **on 2026-07-24**, in the middle of the
campaign:

- `8ec6769` rewrote pulse finding (unified trigger, unbiased baseline, full-span
  integral); `781c5b6`/`018f575` added the matched-filter gate and the
  `significance` branch. **`--mf 0` does not undo this** — it restores the gate,
  not the pulse-finding rewrite.
- CNS was off from `ca7baed` (07-02) until `13dc32f` (07-25).
- The combiner gained `trunc_left` / `trunc_right` / `significance`, defaulted
  for older inputs.

Verified from the files themselves: hits in runs ≤66 have no `significance` /
`trunc_*` and no `rms_gate`; runs ≥71 have all of them. Runs 67-69 were
re-analysed with the new recipe by `reprocess_all_cns.sh`. So the campaign
already carries a recipe boundary at **run_70**.

**Decision (2026-08-12): match each run, do not modernise it.** The nine runs
needing new hits (2, 3, 18, 31, 37, 39, 43, 52, 54) are all pre-07-24, so they
were finished with a build of `8ec6769^` (= `f404575`) and `--cns 0`, **and with
that build's combiner**, so no sub-run ends up holding two tree schemas. run_71
and run_73 are post-boundary and were finished with the current build.

The analyser was unchanged from before run_2 until 07-24, so one old build covers
all nine runs. Build recipe on lxplus:

```bash
git archive 8ec6769^ | tar -x -C mm_old
source /cvmfs/sft.cern.ch/lcg/views/LCG_106/x86_64-el9-gcc13-opt/setup.sh
cmake -S mm_old -B build_old -DCMAKE_BUILD_TYPE=Release && make -C build_old -j8
```

## 4. The tool

`reprocess_acquisitions.py` (this repo) replaces `reprocess_run.py` for anything
involving a restarted sub-run. It keys on `(date, time, file_num)`, so each
acquisition decodes, analyses and combines strictly against its own FEU files.
Decode and combine call `processor_watcher`'s own functions unchanged — same hang
watchdog, same cross-FEU truncation guard — so the operations are identical; only
the grouping differs. Analysis mirrors `_analyze_file` with `--mf` added, and
**refuses** on a multi-set pedestal directory instead of silently skipping,
demanding an explicit `--ped-pick`.

```bash
# combine-only, old-recipe run
python3 reprocess_acquisitions.py run_9 --only-combine \
        --combine-exe build_old/feu_hit_combiner/combine_feus_hits

# full chain, old recipe, explicit pedestal
python3 reprocess_acquisitions.py run_54 --cns 0 --ped-pick 260718_14H07 \
        --analyze-exe build_old/waveform_analysis/analyze_waveforms \
        --combine-exe build_old/feu_hit_combiner/combine_feus_hits \
        --verify-json verify_combines.json --check-decodes --jobs 4
```

`--verify-json` takes the measured mixture list so verified-clean combined files
are left alone rather than needlessly rewritten. `--check-decodes` compares the
event counts of decodes already on disk within each acquisition and drops any
that are short, which makes an interrupted run safe to resume.

## 5. DO NOT run `repair_truncated_decodes.py` on these runs

It groups decoded files by **file number** (`groups[m.group(1)]`), so on a
sub-run that was stopped and restarted it compares two unrelated acquisitions,
finds the shorter one "truncated", and **deletes its decodes and hits**. It is
safe only on runs where every file number belongs to a single acquisition. The
equivalent per-acquisition check now lives in `final_check.py`.

## 6. Housekeeping found along the way

- `run_2/run2/combined_hits_root/` holds **7 hidden `.Mx17_*.<random>` files**,
  0.6 GB, all identical in size to the real output — partial xrdcp/EOS uploads
  from a retried copy on 07-10. `ls` hides them, `find` does not, which is why
  the earlier survey counted 9 combined files where 2 exist. Campaign-wide there
  are exactly 7, all in run_2. Safe to delete; nothing references them.
- `config/processor_config.json` on the DAQ still carries
  `exclude_runs: [run_67, run_67_recon, run_68, run_69, run_74, run_70]` from the
  CNS reprocessing campaign.
## 7. The other campaign trees — surveyed, not touched

The same acquisition-keyed survey was run over the other trees on EOS. Nothing
was processed there; this is only so the numbers exist.

| tree | runs | real acquisitions | undecoded | partly decoded | never combined | in a colliding file number |
|---|---|---|---|---|---|---|
| `feb_beam` | 143 | 9402 | 517 | 286 | 1 | 2 (run_139 only) |
| `may_beam` | 73 | 1479 | 326 | 10 | 1 | 0 |
| `p2_sps_july` | 52 | 0 | — | — | — | — |

Two things to note before reading anything into this:

- **The mixture bug is essentially absent** from these trees — restarted
  sub-runs are a July-campaign habit. Only `feb_beam/run_139` has a collision at
  all, so no combined file elsewhere is suspect on these grounds.
- The large undecoded counts are **not necessarily a defect**. These are earlier
  campaigns whose processing may have been deliberately partial. Nobody asked for
  them to be finished, and they were not.
- `p2_sps_july` holds 198 sub-run directories and **no raw FDFs at all**; its
  data lives somewhere else.
