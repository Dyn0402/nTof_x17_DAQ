# HANDOFF 2026-07-21 — processing chain silently produced partial data

**Status: three failure modes found, all three patched, damaged data being
rebuilt. One question is UNRESOLVED and is the reason for this handoff — see
§6.**

**The detectors and the DAQ are fine.** Every raw FDF is complete and decodes to
the full event count. Everything below happened *after* acquisition, in
`processor_watcher.py` and the decode step it drives. Nothing was lost.

---

## 1. How it showed up

A tracking-efficiency analysis of `run_61` returned a physically impossible
result: every detector's HV optimum sat exactly on the grid corner (drift 700 /
resist 560) with single-cell 1σ plateaus, and the drift dependence *alternated*
(500 and 700 high; 600, 400, 200 low). Those groups are precisely `run_61`'s
coarse-first interleave order (700/500/300 first, then 600/400, then 200) — a
time trend masquerading as a drift dependence.

Root of it: detectors were missing from events. Per-detector "live" fractions
across `run_61` were A 0.44, B 0.41, C 0.42, D 0.34; only **23.4 %** of events
had all 8 FEUs. `run_58` is clean in all 63 sub-runs (max zero-hit fraction 0.00
per detector), so this was new.

## 2. Failure mode 1 — copy_on_fly race (FIXED)

`_scan_once` decided a file-group was ready from *"sizes stable across two 10 s
polls"*, looking only at the FDFs **that currently exist**. `copy_on_fly` streams
the 8 FDFs across progressively, so one FEU's file can be complete and stable
while the others have not landed.

Smoking gun — `run_63/dblPS_dr600_r540_004`:

```
decoded FEU06   09:52:18      <- watcher fired on ONE FEU
combined        09:53:13      <- wrote a 1-FEU "combined" file
raw FDFs 01-08  10:01:00 … 10:01:28   <- other 7 arrived NINE MINUTES later
```

`_get_processed_file_nums` then treated the group as done because a
`feu-combined` file existed, so it was never revisited.

Damage (file-groups with < 8 decoded): run_58 0/76, run_60 0/44, **run_61
48/113, run_62 12/25, run_63 10/18**.

## 3. Failure mode 2 — truncated decodes (FIXED)

`_decode_file` ran `subprocess.run(...)` with **no return-code check**, and
Step 1 skips any output that already exists. A decode process that died part-way
left a short ROOT file that was then permanent and invisible.

Verified at FDF level on `run_62/sng_dr700_r560_000` b000 — every FEU's decoded
stream is **perfectly contiguous from event 1**, no interior gaps; the bad ones
simply stop:

```
FEU01/02/05/06/07/08  2601 events  IDs 1-2601   contiguous
FEU03                 2458 events  IDs 1-2458   contiguous  <- stops early
FEU04                   77 events  IDs 1-77     contiguous  <- stops early
```

**Proof the hardware is innocent:** re-decoding the same FDFs by hand gives
FEU04 = **2601** events (rc 0), and for `run_61/sngPS_dr500_r520_009` b000 all
eight FEUs give **exactly 2599** events (rc 0) against stored files of FEU04=810,
FEU05=1166, FEU06=416, FEU08=2040.

Truncation damage: run_58 0/76 groups, run_60 4/44 (3 447 events), **run_61
32/109 (68 609 events)**, run_62 11/25 (10 232), run_63 0/19.

## 4. Failure mode 3 — stale combines (FIXED)

Step 3 combined whatever hits files existed at that moment and wrote the output
only *if absent*, so a combine that ran before every FEU was analyzed was frozen
in place even after the missing FEUs appeared.

## 5. What was changed

`processor_watcher.py` (backup: `scratchpad/processor_watcher.py.bak`):

- `_read_expected_feus()` reads `dream_daq_info.included_feus`; a new
  **FEU-completeness guard** in the main loop waits for the full set.
  `INCOMPLETE_GRACE_S = 900` then processes anyway with a WARNING, so a genuinely
  lost FEU cannot stall the pipeline forever.
- `_decode_file` captures stdout, checks the return code and non-empty output,
  deletes truncated output, and returns the decoder's own
  `Events analysed : N`.
- **Truncation guard** after Step 1: all FEUs of a group must report the same
  event count; any short one is deleted and re-decoded once, with a loud warning
  if it stays short.
- Step 3 rebuilds the combined file when any input hits file is newer than it.
- `_get_processed_file_nums(expected_feus, raw_dir)` demotes groups whose per-FEU
  decode is incomplete → damaged groups **self-heal**. Verified: it demotes
  `run_61` sub-018 group 0 and leaves clean `run_60` untouched.

New tools:

- `repair_truncated_decodes.py` — finds decoded ROOTs shorter than their group
  max (all FEUs see the same triggers, so counts must agree) and deletes them
  plus their hits so the watcher rebuilds. **Applied: 208 files, 82 280 events
  recovered.** Run it from the *analysis* venv (`~/PycharmProjects/nTof_x17/.venv`)
  — `uproot` is not in the DAQ venv.
- `reprocess_run.py` — drives `processor_watcher._process_file_num` directly over
  one run's incomplete groups, because the watcher walks newest-first and never
  reaches an older run's backlog while a newer run is taking data. **Add the run
  to `exclude_runs` and restart the watcher first**, so the two cannot decode the
  same file at once.

Separately: `pedestals_07-20-26_11-40-44/pedestals/` held **two complete pedestal
sets** (11H43 and 11H46). `_analyze_file` skips any FEU with >1 matching
pedestal, so 11 `run_58` sub-runs were in an infinite retry loop flooding the log
with `Multiple pedestals … skipping` and burning watcher cycles. The sets were
equivalent (identical `.fdf`/`.prg` sizes, `.root` within 0.01 %) → kept the later
**11H46**, moved 11H43 to `pedestals/superseded_11H43/` (reversible;
`os.listdir` ignores subdirectories).

## 6. UNRESOLVED — for the next session

**Why did the decode processes die part-way?** This is the important open
question; everything above only stops a dead decode from becoming permanent
damage, it does not stop the dying.

- No error is recorded anywhere: `dream_daq.log` for the affected sub-runs is
  clean (122 lines, no error), and the old watcher printed nothing because it
  never checked the return code.
- `dmesg` is not readable without sudo on this account (`read kernel buffer
  failed: Operation not permitted`), so **OOM kills could not be confirmed or
  excluded** — that is the first thing to check with privileges
  (`journalctl -k | grep -i oom`, or `/var/log/kern.log`).
- Circumstantial: the box is 6 cores / 15 GB and runs DAQ + decode + analysis
  concurrently. `free_threads: 2` → 4 decode threads, each ~283 MB RSS measured.
  Truncation is heavy in run_61/62 (overnight, sustained acquisition) and absent
  in run_58/63. An analysis job of mine (3 parallel reco processes) was running
  during run_63's worst *race* sub-runs, which will have widened that window —
  but run_61 degraded long before that, so load is at most an aggravator.
- Worth checking: whether decode ever hits a file-size or 32-bit offset limit.
  run_63's FDFs are 201 MB where run_60/61's were 39.8 MB, yet run_63 shows no
  truncation, so this looks unlikely.

### STRONG new evidence (2026-07-21 afternoon): memory pressure, and NO SWAP

While rebuilding the run_61 analysis cache, a `ProcessPoolExecutor` job died
twice with **`BrokenProcessPool: A process in the process pool was terminated
abruptly`** — i.e. a worker killed from outside, with no Python traceback of its
own. **That is the same signature as the decode deaths**: a child process
vanishing mid-work, leaving a truncated output and no error anywhere.

It happened at `--jobs 3` (29/60 done) and again at `--jobs 2` (47/60), and only
completed at `--jobs 1`. Machine state at the time:

```
Mem: 15.8 GB total, 10.0 GB used, 5.7 GB available
swapon --show  ->  (no swap configured)      <-- NO SWAP AT ALL
top RSS: NXCALS java 1.4 GB, beam_watcher 1.1 GB,
         4x detA_doubletrack/scan.py 0.7-0.8 GB each (~3 GB, another session),
         claude 0.5 GB
```

With **zero swap**, any transient over-commit means the kernel OOM-kills a
process immediately — no paging, no slowdown, no warning. The decoder was
measured at ~283 MB RSS per process and the watcher runs 4 of them
(`free_threads: 2` on 6 cores), so decode alone is ~1.1 GB on top of whatever
else is running. run_61/62 (the truncation-heavy runs) were taken overnight
during sustained acquisition; run_58/63 are clean.

Concrete things for the next session:
1. Confirm with privileges: `journalctl -k | grep -i -E "oom|killed process"` or
   `/var/log/kern.log`. This should settle it in one command.
2. **Consider adding swap** — even a few GB would convert a hard kill into a
   slowdown, which is the single highest-value change if OOM is confirmed.
3. Consider lowering decode parallelism (`free_threads`) during acquisition, and
   coordinating analysis jobs so several memory-heavy pipelines do not overlap
   (multiple sessions were running analyses concurrently on this box today).
4. The watcher's new truncation guard catches a short decode *within one pass*,
   but cannot detect a pre-existing short file sitting next to freshly decoded
   ones. `repair_truncated_decodes.py` is the tool for that case, and it must be
   run **after** any missing files are restored — otherwise the group maximum is
   itself truncated and the deficit is invisible (this is exactly why run_61
   needed two repair passes; the second found 8 more groups / 2502 events).

**Second, smaller oddity:** the decoder prints the FEU id it reads from the file
header, and it is not what one would expect — `reading FEU 99` for the FEU04
file, `reading FEU 32` for FEU01. Both decode correctly, so it may just be an
internal/encoded id, but nobody has confirmed what that field means.

**Also worth a look:** `stale_run_days: 1` means a run is skipped permanently
once it has had no new FDFs for a day. Combined with newest-first ordering, a
backlog on an older run can silently pass the deadline and never be processed.
Consider raising it or making "stale" not apply to runs with known-incomplete
groups.

## 7. Verifying the state of any run

```bash
# per-group: are all FEUs decoded?
python3 - <<'PY'
import glob,os,re,collections
run='run_61'; rd=f'/mnt/data/x17/beam_july/runs/{run}'
for sub in sorted(os.listdir(rd)):
    sd=os.path.join(rd,sub)
    if not os.path.isdir(sd): continue
    raw=collections.defaultdict(set); dec=collections.defaultdict(set)
    for f in glob.glob(sd+'/raw_daq_data/*datrun*.fdf'):
        m=re.search(r'_(\d\d\d)_(\d\d)\.fdf$',f); raw[m.group(1)].add(m.group(2))
    for f in glob.glob(sd+'/decoded_root/*datrun*.root'):
        m=re.search(r'_(\d\d\d)_(\d\d)\.root$',f); dec[m.group(1)].add(m.group(2))
    for b,fs in sorted(raw.items()):
        if dec.get(b,set())!=fs:
            print(f'{sub} b{b}: MISSING {sorted(fs-dec.get(b,set()))}')
PY

# truncation check (analysis venv — needs uproot)
~/PycharmProjects/nTof_x17/.venv/bin/python repair_truncated_decodes.py run_61
```

Any per-trigger efficiency computed from affected data **must** cut on per-event
FEU presence — see `ntof_july_analysis/run61_scan/feu_presence.py`, which asks
only "was this detector read out in this event" and so handles all three failure
modes.
