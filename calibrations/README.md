# Detector calibrations for DAQ operation

One subdirectory per detector system / calibration type. Each holds:
machine-readable constants (`*.json`, `*.csv`) tagged by source run, the
operating figures, and a `README.md` that answers the operational questions
("what threshold do I set, what do I get for it") without needing the analysis
repo. Analyses live in `nTof_x17/mx_july_beam_qa/` (and successors); files here
are exported snapshots with provenance — regenerate rather than hand-edit.

## Contents

| dir | what | status |
|---|---|---|
| `wal_trigger/` | SiPM-wall trigger thresholds (top+bottom group sums, one per wall): recommended values + full threshold→efficiency/purity/rate scan | **filled** (run224460) |
| `wal_mip/` | Per-channel SiPM-wall MIP amplitude constants (ADC & mV) | **filled** (run224460) |
| `pss/` | Plastic PMT gain curves G∝V^n, HV equalization + global gain-slide table | **filled** (run224466 HV scan) |
| `pss_trigger/` | Plastic trigger thresholds vs the 10 mV discriminator floor: efficiencies + HV-recovery options | **filled** (run224466) |
| `liq/` | Liquid scintillators (LS): MIP / PSD calibration | placeholder — awaiting LIQ readout |
| `mm/` | Micromegas tracker HV operating points: per-detector resist-HV fine-scan ranges (drift/resist) | **filled** (run_67 resist zoom) |

Anticipated later: `sili/` (Si monitor), MM gain-vs-HV curves + per-strip threshold maps.

## Operational quick answer: "we need to drop the trigger rate"

1. Open `wal_trigger/threshold_scan_<run>.csv` (or the figures in
   `wal_trigger/figures/`).
2. Pick the new threshold per wall from the scan row that gives the rate you
   need; the same row tells you exactly the per-group MIP efficiency and purity
   you are giving up. Landmarks are in `wal_trigger/README.md`.
3. Rates in the table are *relative* (late-TOF sample, per bunch) — scale your
   currently observed rate by the ratio of table rates between old and new
   thresholds.

## Operational quick answer: "we want more/less plastic gain globally"

Open `pss/global_gain_slide_<run>.csv`, pick the column with the desired gain
factor g, set the 8 listed voltages (CAEN card 07). Each PMT moves by its own
amount (`V × g^(1/n)`, n per PMT) so the fleet stays equalized. Details and
caveats in `pss/README.md`.

## Provenance / regeneration

Every JSON carries a `provenance` block (run, date, selection, HV state,
caveats). Regenerate after any HV or hardware change:

```
cd ~/PycharmProjects/nTof_x17/mx_july_beam_qa
python 18_trigger_threshold.py <run_file>    # trigger sums cache
python 18c_export_daq_calib.py <run_stem>    # export here
```
