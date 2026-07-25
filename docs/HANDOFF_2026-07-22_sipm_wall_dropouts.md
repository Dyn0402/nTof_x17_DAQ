# SiPM wall dropouts — SOLVED: they are our mesh charge-injection circuit (2026-07-22)

Analysis: `~/beam_july/analysis/sipm_wall_filesize/` (`FINDINGS.md` + scripts).
Raw-file format: `docs/NTOF_RAW_FORMAT.md`. Online monitor: `stream1_monitor/`.

> **This document was rewritten on 2026-07-22 afternoon.** The first version concluded
> "not us — look at the wall supply", and listed an unexplained ~11.6 min period. That was
> wrong, caused by a timezone bug (below). The walls are being switched by our own
> `scan_control` mesh-injection setting.

## The bug that hid it

**EOS reports file mtimes in UTC; every one of our logs (`hv_monitor.csv`, sub-run names,
beam CSVs) is local CEST.** The first analysis compared them directly — a 2 h error.
Verified: newest EOS mtime 12:04:31 while the local clock read 14:05:08.

Fixed centrally in `plot_sizes.py::eos_utc_to_local()`; every script imports it. If you
write anything new that joins EOS timestamps to our logs, use it.

## What is actually happening

`RMPA` / `RMPC` in the n_TOF stream are the amplified TTL trigger going into our mesh
charge-injection ramp circuits on detectors A and C. They are therefore an independent
readback, recorded in n_TOF's data next to the walls, of whether **our** circuit fired.

run_65 modulated exactly one setting per sub-run — `config/n1081b_scan_schedule.json`:

    randOn  -> mesh_b (.245 M6.B outputs 0-3) ENABLED
    randOff -> mesh_b DISABLED

Nothing else differs between the two tags. 34 sub-runs of ~5.4 min, alternating. Probing
369 n_TOF files across it (`probe_ramp_series.py` → `ramp_series.json`,
`figures/ramp_vs_walls.png`):

| our tag | n files | RMP trigger present | walls at full gain |
|---|---|---|---|
| `randOn` / `m05On` (mesh ON) | 50 | 86 % | **84 %** |
| `randOff` / `m05Off` (mesh OFF) | 31 | 16 % | **10 %** |

φ(RMP firing, walls at full gain) = **+0.947** over 312 files with both measured.

    walls at full gain while RMP not firing:   0 cases
    walls collapsed while RMP firing:          5 cases

Per sub-run it is essentially binary — every `randOn` sits at wall flash ~34 140 ADC, every
`randOff` at ~570–680.

## It is a gain collapse, not a severed signal path

In a mesh-OFF file the walls **still see the gamma flash, at the right time**:

| channel | mesh ON | mesh OFF |
|---|---|---|
| WALA.2 peak | 34 291 ADC @ 11.922 µs | 877 ADC @ 11.648 µs (46σ) |
| WALB peak | 33 771 @ 11.915 µs | 512 @ 11.646 µs (24σ) |
| PSSA.1 peak | 34 577 @ 11.650 µs | 34 601 @ 11.926 µs |
| RMPA.1 peak | 39 540 @ 3.103 µs | 129 @ 3.021 µs (3.9σ = noise) |

The flash is still there in the walls, at the same time as the plastics' flash, ~40× smaller
(and the ON value is railed, so the true ratio is larger). Baseline and noise RMS are
unchanged. A disconnected cable would give nothing at the flash time; this is the walls
running at drastically reduced gain. `RMPA` by contrast goes to pure noise — its trigger is
simply absent, exactly as expected when M6.B is disabled.

**Working hypothesis (needs a hardware check, not in the data):** the wall SiPM bias
depends on the injection circuit being actively triggered — e.g. a switched / charge-pumped
bias rail that decays when the trigger stops. SiPM gain is exponentially sensitive to
overvoltage, so a modest rail sag gives a ~40× gain loss with an unchanged DC baseline.
Note **all four walls** collapse even though the mesh is cabled to A+C only, so whatever is
shared is common to all four.

## This explains every feature we chased

- **The ~11.6 min "mystery period"** = our own randOn+randOff cycle (5.4 + 5.4 min plus
  ~0.2 min turnaround). The earlier "not phase-locked to our sub-runs" claim compared the
  toggles against **run_64** boundaries under a 2 h time shift; with the timezone fixed and
  the correct run (run_65) it is a exact match. There is no external 11 min cycle.
- **The 7.1 h "outage" (00:33 → 07:40 CEST)** = `scan_control` stuck in sub-run
  `randOn_r532.5_dr700_022`, which ran 00:37:49 → 07:45:38 (427.8 min) against the outage's
  426.3 min. In that sub-run the tag says mesh ON but **RMP shows the circuit was not firing
  (1 % of files)** — the `randOn` was never applied, so M6.B stayed disabled from the
  preceding `randOff_021` for 7.1 h. Recovery at 07:45 is the run resuming.
- **The 2.7 h episode (09:45 → 12:26)** and the short ones follow run_66's `m05On`/`m05Off`
  the same way.

## Still true: our Micromegas HV is not involved

Re-run with corrected timestamps (`correlate_hv.py`, `within_run_test.py`):

- Zero MM HV faults in 24 h — no resist channel powered off or collapsed.
- Within run_64 (resist scanned 520–570 V, drift 200–700 V, dozens of times):
  r(size, resist) = **+0.048**, r(size, drift) = **+0.044**. Median file size by drift
  setpoint 200/300/400/500/600/700 V = 1.88/1.80/1.87/1.86/1.85/1.87 GiB — flat.
- The 24 h r(reduced, drift) = +0.58 remains an artifact of run_65 holding drift at 700 V
  through the stuck sub-run.

## What to do

1. **Decide whether the mesh-OFF sub-runs are usable at all.** In every `randOff` /
   `m05Off` sub-run the walls run at ~1/40 gain, so any wall-based trigger or coincidence
   from those sub-runs is not comparable to its mesh-ON partner. That breaks the intended
   "same HV, same beam epoch, mesh on vs off" A/B design of run_65 and run_66 — the mesh
   comparison is confounded by a wall-gain change.
2. **Find the coupling.** Trace what the .245 M6.B outputs 0–3 feed besides the two ramp
   circuits, and how the wall SiPM bias is generated. If the bias rail is pumped by the
   trigger, that is the mechanism, and it is a design issue to fix rather than work around.
3. **run_65 sub-run 022 needs re-taking** — 7.1 h of data with walls at 1/40 gain, and its
   `randOn` label is wrong.
4. **Make `scan_control` fail loudly.** A sub-run whose tag was never applied ran for 7.1 h
   silently mislabelled. Whatever stalled it should abort or alert, not continue.
5. Optional but cheap: add RMPA/RMPC amplitude to the online monitor as a direct readback
   that the injection actually fired — it is a 15 kB bank and unambiguous.

## Reproducing

    cd ~/beam_july/analysis/sipm_wall_filesize
    ./collect_sizes.sh                       # refresh the EOS listing
    python3 plot_sizes.py                    # size vs time (times now CEST-correct)
    python3 correlate_hv.py                  # MM HV + beam overlay
    python3 within_run_test.py               # HV causality test
    python3 probe_ramp_series.py --step 2    # ~370 files, RMP + wall flash per file
    python3 plot_ramp_series.py              # the result figure
