# Plastic scintillator (PSS) trigger thresholds

**Source: run224466 HV scan analysis (wall-tagged coincident spectra as the
particle-like reference population); `nTof_x17/mx_july_beam_qa/12*`. Companion
to `../pss/` (gain curves / equalization) — same provenance, same caveats.**

**Hardware constraint: the discriminator minimum is 10 mV.** There is no
signal/noise valley in the plastic spectra (they rise monotonically down to
the PSA acquisition cutoff at ~1.5–2 mV), so the threshold choice is purely
efficiency-driven. Unconstrained, 4–5 mV would keep ≥99% of the wall-tagged
population on every PMT; the 10 mV floor costs efficiency.

## What you get at the 10 mV floor (current plan: accept this)

Tagged-population efficiency above 10 mV, per PMT (from
`thresholds_run224466.json`):

| gains | worst PMT | range over 8 PMTs |
|---|---|---|
| current operating HVs | PSSD2: 0.86 | 0.86 – 0.98 |
| after `../pss/` equalization | PSSC1: 0.86 | 0.86 – 0.97 |

Full curve: `threshold_scan_run224466.csv` (post-equalization scale) and
`figures/threshold_scan_run224466.png`.

## Option for later: raise HV to push events over the floor

Scaling all gains by a common factor g (each PMT via its own power law,
`V = V_eq · g^(1/n)`, see `../pss/`) slides the spectra up so 10 mV behaves
like a lower threshold. From `thresholds_run224466.json` (`hv_to_recover`):

- **99% tagged eff at 10 mV**: g ≈ 2.35 → voltages 1408–1614 V
  (only PSSD2 slightly above the validated 1600 V; flagged).
- **95% tagged eff at 10 mV**: g ≈ 1.59 → voltages 1298–1520 V (all validated).

**Status: option only — deferred until a true plastic MIP calibration (LIQ
triple coincidence) sets the absolute scale.** Until then we run at the
equalized voltages and accept ~86% worst-PMT tagged efficiency at 10 mV.

## Caveats

- Thresholds are digitizer-equivalent mV — map to hardware discriminator
  units before setting.
- Efficiencies are for the wall-tagged (particle-like) population, not a true
  plastic-MIP efficiency; rates in the CSV are the relative late-TOF sample
  (the γ-flash adds a much larger instantaneous rate).
- Raising gains ×2.35 also multiplies the accepted hit rate and moves the
  tagged median to ~105 mV — recheck pile-up and dynamic range (16-bit,
  ~2 V full scale) before adopting.

## Regeneration

```
cd ~/PycharmProjects/nTof_x17/mx_july_beam_qa
python 12_plastic_hv_scan.py <run_file>
python 12b_hv_scan_plots.py <run_stem>        # includes threshold_scan figure
python 12c_export_pss_calib.py <run_stem>     # exports pss/ AND pss_trigger/
```
