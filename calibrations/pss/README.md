# Plastic scintillator (PSS) PMT gain calibration

**Source: run224466 HV plateau scan (2026-07-16), wall-tagged coincident plastic
spectra; analysis `nTof_x17/mx_july_beam_qa/12*`, HV log
`~/beam_july/scint_hv_scan/2026-07-16_13-32-34_plastic_scan_1/`.**

Each PMT follows a clean power law over the scanned 1200–1600 V range:

```
response(V) = response(V_ref) × (V / V_ref)^n      n = 4.8–6.9, per PMT (see JSON)
```

"Response" is the median of the wall-tagged, sideband-subtracted plastic
spectrum — a *relative* gain standard (the plastic sits behind the wall, so
this is not a plastic-MIP energy scale; that awaits the LIQ triple
coincidence).

## Files

- `gain_vs_hv_run224466.json` — per-PMT fitted indices (combined + per pass),
  the full measured curve per scan step, operating point, CAEN channel
  (card 07), ADC→mV.
- `hv_equalization_run224466.json` — recommended per-PMT voltages that
  equalize all 8 responses to the fleet geometric mean (1461 ADC ≈ 45 mV).
  Target = "average of where we already operate": minimal net HV movement,
  NOT rate-derived (rates never plateau) and not an absolute energy target.
- `global_gain_slide_run224466.csv` — **the global-gain knob** (see below).
- `figures/` — gain curves per PMT, the equalization construction, spectra.

## Operational quick answer: "we want more/less plastic gain globally"

Because n differs per PMT (4.8–6.9), a common ΔV would *de*-equalize the
fleet. To scale every PMT's response by the same factor g while staying
equalized, each PMT moves by its own amount:

```
V_new = V_current × g^(1/n)        (n per PMT from gain_vs_hv_*.json)
```

`global_gain_slide_run224466.csv` has this precomputed from the equalized
baseline for g = 0.5 … 2.0 — pick a column, set the 8 voltages, done.
Values outside the scanned/validated 1200–1600 V range are marked `*`
(extrapolation; notably C-L flattens above ~1500 V and its low-gain settings
fall below 1200 V).

Rule of thumb (fleet-average n ≈ 6): ×2 gain ≈ +12% on every V,
×1.25 ≈ +3.8%, ×0.8 ≈ −3.6%. Use the CSV, not the rule, when actually
setting voltages.

## Recommended equalized settings (from current operating point)

| PMT | CAEN | V_now | V_equalized | ΔV |
|-----|------|-------|-------------|----|
| PSSA1 (A-L) | 7:0 | 1325 | 1303 | −22 |
| PSSA2 (A-R) | 7:1 | 1275 | 1242 | −33 |
| PSSB1 (B-L) | 7:2 | 1325 | 1376 | +51 |
| PSSB2 (B-R) | 7:3 | 1300 | 1279 | −21 |
| PSSC1 (C-L) | 7:4 | 1300 | 1180 | −120 |
| PSSC2 (C-R) | 7:5 | 1300 | 1307 | +7 |
| PSSD1 (D-L) | 7:6 | 1300 | 1303 | +3 |
| PSSD2 (D-R) | 7:7 | 1300 | 1417 | +117 |

Current spread is 2.8× (C-L highest, D-R lowest). The SiPM-wall MIP
calibration is unaffected by any of these moves (verified across the whole
scan: wall MIP peak stable to ±2.6%).

## Caveats

- Power laws validated only inside 1200–1600 V; `*`-flagged values are
  extrapolations. C-L (PSSC1, n = 4.8) visibly saturates above ~1500 V.
- Pass 1 ran with SiPM flash-gating OFF, pass 2 with it ON; the fits agree
  between passes, so the curves hold in either state.
- Plastics are at the BACK of each arm stack, so wall–plastic coincidences
  MIP-select the *wall*, not the plastic. True MIP constants per bar still
  require the LIQ readout (wall × plastic × LS triple coincidence); when
  available, an absolute target (e.g. MIP at a chosen mV) replaces the
  geometric-mean convention — these same power laws convert it to voltages.

## Regeneration

```
cd ~/PycharmProjects/nTof_x17/mx_july_beam_qa
./run_readpass.sh <run.root>                  # if caches don't exist yet
python 12_plastic_hv_scan.py <run_file>       # needs an HV-scan run + its log
python 12b_hv_scan_plots.py <run_stem>        # figures
python 12c_export_pss_calib.py <run_stem>     # export here
```
