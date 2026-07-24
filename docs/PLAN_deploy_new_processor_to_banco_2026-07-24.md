# PLAN: deploy the rewritten waveform processor to the banco P2 machine

**For:** a follow-up agent executing on `ssh banco_cern` (host `dedippcq196.extra.cea.fr`,
user `banco`). Written 2026-07-24 after the local upgrade + P2 validation.
**Context docs:** `banco_vs_local_mm_reconstruction_2026-07-24.md` (same folder) and
`/media/dylan/data/P2/OLD_VS_NEW_PROCESSOR.md` (laptop).

## Goal

Replace the banco fork's patched-but-still-lossy `analyze_waveforms` with the
rewritten `mm_strip_reconstruction` (GitHub `Dyn0402/mm_strip_reconstruction`,
main @ `722df6b` or later), wire it into their watcher, and reprocess their
existing beam data. Their 17:05 seed patch is **fully subsumed** by the rewrite,
including their `--zs-baseline` flag which is now ported upstream (`722df6b`)
with identical CLI semantics — so their existing watcher can drive our binary
unmodified.

## Hard rules

1. **Do not modify anything under `/local/home/banco/mm_dream_reconstruction/`.**
   It is not a git repo; it is their working fork with `.bak_20260724` originals
   and it must stay as-is (fallback + provenance). Deploy into a NEW directory.
2. **Do not touch raw data** (`raw_daq_data/`, `*.fdf`, `*.hang`, pedestals).
3. **This is a live DAQ at the SPS.** Before restarting anything, check whether
   a run is in progress (`tmux capture-pane -pt dream_daq | tail -20`, and look
   at the newest `runs/*/` mtimes). Do the watcher restart between runs, and if
   in doubt stop and ask the user.
4. Their watcher (`DAQ_Control_Dream_Beam/processor_watcher.py`) is THEIRS —
   it already has the decode watchdog and `--zs-baseline` auto-detection. Do
   not overwrite it; only the three executable paths in the processor config
   change.

## Machine facts (verified 2026-07-24)

- Watcher: tmux session `processor_watcher`, running
  `python processor_watcher.py config/processor_config.json` from
  `/local/home/banco/DAQ_Control_Dream_Beam/`.
- `config/processor_config.json` is **generated** — do not hand-edit. It is
  produced by `processor_config.py`, which takes the build dir from
  `RECONSTRUCTION_BUILD` = `SITES[SITE]['reconstruction_build']` in
  `run_config_beam.py` (SITE from env `DAQ_SITE`, `'sps'` on banco). The flask
  UI "Start Processor" button re-reads the JSON.
- Current exes: `/local/home/banco/mm_dream_reconstruction/build/{decoder/decode,
  waveform_analysis/analyze_waveforms,feu_hit_combiner/combine_feus_hits}`.
  NB their `build/` has empty `CMAKE_BUILD_TYPE` → **-O0**, same debug-build
  trap we hit on mx17-daq.
- Toolchain: cmake 3.16.3 (ours needs ≥3.15 ✓), `/usr/bin/c++`, ROOT 6.32.02 at
  `/local/home/banco/opt/root_v6.32.02/` (`source .../bin/thisroot.sh` to build;
  binaries run without it if RUNPATH-linked, but verify with `ldd`).
- Their config runs `common_noise_subtraction: false` and their watcher passes
  `--cns 0` + auto `--zs-baseline 1` (from run_config.json
  `zero_suppress && pedestal_subtraction`) + `--tps` — all accepted verbatim by
  the new CLI. The new analyzer would auto-force CNS off on ZS anyway.
- Data layout: `/local/home/banco/P2_data/TB_July2026_H4/runs/<run>/<subrun>/
  {raw_daq_data,decoded_root,hits_root,combined_hits_root}`, pedestals under
  `.../pedestals/`, `pedestal_loc: 'find'` via `pedestal_run.txt`.

## Steps

### 1. Clone + build (no service impact)

```bash
cd /local/home/banco
git clone https://github.com/Dyn0402/mm_strip_reconstruction.git
cd mm_strip_reconstruction
source /local/home/banco/opt/root_v6.32.02/bin/thisroot.sh
cmake -S . -B cmake-build-release -DCMAKE_BUILD_TYPE=Release
cmake --build cmake-build-release -j8
```

(If the machine has no outbound GitHub, rsync the repo from the laptop:
`rsync -a --exclude cmake-build\* ~/CLionProjects/mm_strip_reconstruction/ banco_cern:/local/home/banco/mm_strip_reconstruction/` — run from the laptop.)

### 2. Validate the new binary against known numbers (no service impact)

Pick an already-processed subrun, e.g.
`runs/beam_nominal_meshscan_1/nominal_00/`, FEU 04 (P2_MID), and run the new
analyzer to a scratch file with the same options their watcher would use:

```bash
cd /local/home/banco/P2_data/TB_July2026_H4/runs/beam_nominal_meshscan_1/nominal_00
PED=/local/home/banco/P2_data/TB_July2026_H4/pedestals/<ped_run_from_raw_daq_data/pedestal_run.txt>/pedestals
/local/home/banco/mm_strip_reconstruction/cmake-build-release/waveform_analysis/analyze_waveforms \
  decoded_root/<...>_000_04.root /tmp/new_04_hits.root \
  $PED/<...pedthr..._04.root> --tps 60 --cns 0 --zs-baseline 1
```

Acceptance (from the laptop validation on this same dataset):
- Console shows: "ZS-baseline mode: data baseline forced to 256", "forcing CNS
  OFF" (ZS auto-detect), "Matched-filter gate disabled".
- New hits ≈ 2.2–2.7× their old `hits_root` count for the same FEU 04 file
  (laptop meshscan_01: old 1.07M → new 2.60M with ped+zs-baseline).
- Event fraction with an in-time (`max_sample` in [4.5,9)) hit ≥200 ADC on
  FEU 04 nominal_00 ≈ **0.75** (old files give ≈0.49; raw-truth bound 0.747).
- Runtime: a ~2M-event ZS FEU file should take minutes, not tens of minutes
  (Release build).

If any of these fail, stop and report — do not switch the pipeline over.

### 3. Switch the watcher to the new build (between runs)

1. Edit `/local/home/banco/DAQ_Control_Dream_Beam/run_config_beam.py`: in
   `SITES['sps']`, set
   `reconstruction_build = '/local/home/banco/mm_strip_reconstruction/cmake-build-release/'`.
   (Keep the trailing slash — paths are f-string concatenated.)
2. Regenerate: `cd DAQ_Control_Dream_Beam && python processor_config.py`, then
   diff `config/processor_config.json` — ONLY the three executable paths should
   change.
3. Restart the watcher in its tmux session (`processor_watcher`): Ctrl-C the
   running `python processor_watcher.py ...` and relaunch the same command (or
   use the flask UI Start Processor button if the session is flask-managed —
   check `tmux capture-pane -pt processor_watcher` first to see how it was
   started).
4. Watch one new file_num get processed end-to-end; confirm the analyze lines
   show the new binary path and `--zs-baseline 1`, and hits files appear.

### 4. Reprocess the backlog (coordinate scope with the P2 crew)

All hits produced before the switch are from the buggy analyzer (and the
morning runs also predate their own 17:05 patch). For each run to redo
(`beam_nominal_meshscan_1`, `drift_scan_1`, `drift_scan_2`, + anything newer):

- Move aside rather than delete: `mv hits_root hits_root.old_analyzer` (same
  for `combined_hits_root`) in each subrun. `decoded_root` is fine to keep —
  decoding is unchanged (same DreamDecoder source).
- Their watcher's `stale_run_days: 1` will skip runs older than a day — either
  bump it temporarily in the generator + regenerate + restart, or reprocess by
  hand with a loop over decoded files calling the new `analyze_waveforms` +
  `combine_feus_hits` (combiner input: text file of `<hits_path> <feu>` lines;
  see `_combine_hits` in their watcher for the exact call).
- Spot-check after: per-detector in-time hit fractions on nominal_00 ≈
  IN 0.09 / MID 0.75 / OUT 0.82 at ≥200 ADC (laptop reference; IN is genuinely
  near-dead in the raw stream — that is the detector, not the processing).

### 5. Downstream heads-up (tell the P2 crew)

- `max_sample` is now a **float** (refined peak position): in-time cuts must be
  `>= 4.5 && < 9.0`, not `<= 8`.
- `time_over_threshold`/`left_sample`/`right_sample` are interpolated floats;
  new branches `trunc_left`, `trunc_right`, `significance` are available.
- Hit multiplicity roughly doubles at the 5σ threshold (real small hits plus
  noise-level ones on recorded ZS channels); cut on amplitude downstream.
- Old and new hit sets must never be mixed in one analysis.

### 6. Wrap-up

- Copy any `*.hang` FDFs (their quarantined decoder-hang reproducers) back to
  the laptop for the DreamDecoder infinite-loop debug
  (`find /local/home/banco/P2_data -name '*.hang'`).
- Report: build hash deployed, validation numbers from step 2, which runs were
  reprocessed, and any deviations.

## Rollback

Point `reconstruction_build` back to
`/local/home/banco/mm_dream_reconstruction/build/`, regenerate the JSON,
restart the watcher, and restore any `hits_root.old_analyzer` directories.
Nothing in this plan modifies their fork, so rollback is config-only.
