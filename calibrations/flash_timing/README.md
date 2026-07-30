# γ-flash time constants (per channel, referenced to the beam pickup)

**Source: runs 224356–224360 (2026-07-11) and 224464, 224466 (2026-07-16) —
the only seven runs of the campaign taken with the SiPM-wall divert disabled.
Analysis: `nTof_x17/ntof_processing/flash_timing/`
(report: `latex/flash_timing_calibration.pdf`).**

## What this gives you

The time at which the γ-flash arrives in each detector channel, in the same time
base as the hits:

```
t_flash_at_channel(bunch)  =  tof_PKUP(bunch)  +  C_ns[channel]
t_since_flash(hit)         =  hit.tof - t_flash_at_channel(hit.bunch)
```

`tof_PKUP` is the `tof` of that bunch's PKUP hit (equal to its `tflash`). `C_ns`
is **negative** — the flash reaches the detectors ~1.72 µs *before* the pickup
pulse appears in the digitiser window.

## ⚠ Use all 32 constants, one per channel

Not one per wall. The four wall means agree to 0.7 ns, while channels *within* a
wall differ by up to 13.3 ns, so grouping by detector captures none of the real
structure:

| scheme | rms residual | max residual |
|---|---|---|
| one constant for all 32 channels | 3.43 ns | 7.18 ns |
| one constant per wall | **3.42 ns** | **7.07 ns** |
| **per channel** | **0** | **0** |

The channel-to-channel structure (3.43 ns rms) is 6× its own within-epoch
reproducibility (0.61 ns), i.e. real and worth correcting. It is cable length
plus per-SiPM front end.

## ⚠ Do not use the PSA's stored `tflash`

In the **official** processed files the wall flash finder is bistable *within a
single run*: it tags the divert-gate transient in some bunches and a clamped
leak in others, so no per-run offset repairs it. The pickup finder never fails
(usable in 98.4 % of bunches; the 1.6 % that fail are all parasitic pulses —
drop those bunches rather than falling back on `tflash`).

The 2026-07-28 reprocessing (`nTof_x17/ntof_processing/userinputs/`,
`v4_walshapes`) repairs the stored `tflash`, and it reproduces this calibration
to 2.07 ns rms on the walls and 0.27 ns rms on the liquids. Even so, prefer
PKUP + C: it is measured on the *undiverted* flash, whereas a reprocessed wall
`tflash` still times the clamped leak of a gated signal.

## Which epoch

| runs | column |
|---|---|
| ≥ 224400 | `C_ns` (2026-07-16) — the state that held to the end of the campaign |
| < 224400 | `C_ns_epoch_2026_07_11` |

The shift between the two epochs is **per-channel** (WALB ch3 −0.6 ns, WALD ch7
−6.9 ns) — it is the wall HV/threshold equalisation of 07-15/16. Do not mix them.

## Accuracy

| effect | ns |
|---|---|
| run-to-run reproducibility within an epoch | 0.5 |
| time-base stability across the campaign (LIQ, fixed intensity) | 0.4–0.8 |
| transport from the 07-16 epoch to the end of the campaign | 1.2 |
| 07-11 vs 07-16 epoch difference (real hardware change) | 3.9 |
| beam-intensity walk, parasitic → dedicated | 5.0 |
| single-bunch spread, one channel | 3.5 |
| single-bunch spread, 32-channel average | 2.5 |

**Beam intensity:** add −5.0 ns going from parasitic (4.1e12 p) to dedicated
(8.5e12 p). This is front-end saturation changing the pulse shape (risetime
20.5 → 15.4 ns), *not* a change in when the flash arrives — the pickup, which
timestamps the protons that make the flash, moves by only −0.45 ns. The values
here are the run-average of a mixed-intensity sample; apply per bunch from
`PulseIntensity` if you need better than 5 ns.

## Live monitoring of the time base

**LIQ and PSS are never blanked**, so they see the flash in every run and can be
used to check that the time base has not moved:

| | C [ns] | stability over the campaign |
|---|---|---|
| LIQA | −1708.22 | 0.73 ns rms (39 runs, 224400–224584) |
| LIQB | −1710.30 | 0.73 ns |
| LIQC | −1695.56 | 0.77 ns |
| LIQD | −1701.56 | 1.48 ns |

The four cells move *together* (within-run spread of their deviations 0.22 ns,
LIQA–LIQC correlation +0.92), so a common excursion means the time base moved,
not a detector. Largest seen in the campaign: −2.2 ns (run 224531). **PSS is not
stable** — it moves tens of ns across the campaign (plastic HV equalisation
07-16, FIFO fan-in 07-17); take plastic constants per run, never per epoch.

## Files

| file | what |
|---|---|
| `flash_time_constants.json` | the constants + usage, epochs, corrections, LIQ monitor values, provenance |
| `flash_time_constants_per_channel.csv` | the same 32 wall constants, flat |

## Caveats

- Measured on the seven divert-off runs; **every other run has the walls blanked**
  and cannot show the flash directly.
- The wall front end saturates on the undiverted flash — these runs give
  **timing only**, no amplitude/energy information (for that see `../wal_mip/`
  and the Y-88 runs 224476–224479).
- Valid for the 2026 EAR2 X17 hardware state. **Re-measure after any change to
  wall HV, thresholds or cabling** — the 07-15/16 equalisation moved individual
  channels by up to 6.9 ns.

## Regenerate

```
cd ~/PycharmProjects/nTof_x17
.venv/bin/python ntof_processing/flash_timing/scripts/export_daq_calib.py
```
