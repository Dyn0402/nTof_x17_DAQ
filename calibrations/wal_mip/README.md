# SiPM-wall per-channel MIP amplitude constants

**Source: run224460, wall–plastic coincidence MIP peaks (sideband-subtracted,
duplication-vetoed; `nTof_x17/mx_july_beam_qa/07` + `17`).**

`mip_<run>.json` gives, per channel `WAL<arm><1-8>`: `mip_peak_adc`,
`mip_peak_mV`, and the `adc_to_mV` factor (from DAQsettings full scale).

Use for: energy scale (amplitude → MIP units), channel-gain monitoring between
runs, HV-trim targets (equalize `mip_peak_mV` across channels/walls).

Known channel states at this calibration (see analysis slides):

- **WALA (all 8)**: ~30% low — MIP 22–28 mV vs 31–43 mV on B/C/D.
- **WALD ch7/ch8**: genuinely weak (MIP ~21/27 mV).
- WALA 5↔7, WALD 2↔4, 5↔7 carry equal-amplitude duplicated pulses (analog
  short); constants here are duplication-vetoed so they represent the true
  channel response.

Regenerate: `python 18c_export_daq_calib.py <run_stem>` after re-running the
pipeline on a new run.
