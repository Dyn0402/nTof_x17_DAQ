# FEU trigger-FIFO watermarks — mechanism, measurement, and two invalidated null results (2026-07-22)

**Read this before trusting any "we swept knob X and it did nothing" conclusion from the
DREAM DAQ.** Two of the 2026-07-19 comb study's null results were artifacts of the knob never
reaching the hardware, and the watermark null had *two* independent causes.

---

## TL;DR

1. **RunCtrl overwrites the cfg watermarks.** `Feu * Main_Trig_OvrWrnHwm/OvrWrnLwm` are clamped
   down to a cap RunCtrl derives from the DREAM derandomiser geometry. The clamp is
   **one-directional**, so values *below* the cap pass through untouched and values *above* it
   are silently forced to the same number.
2. **`dream_daq_control.py` is a long-lived server.** Sub-run cfg overrides added to the repo
   after the server started are silently dropped. This alone killed the `TrigVetoLen` and
   `SparseRd` sweeps.
3. **Measured 2026-07-22:** lowering the HWM works. `maxFIFOocc == Hwm` exactly, at every
   setting, with zero drops. Throughput is **flat from Hwm 11 down to Hwm 3** and only falls
   at Hwm 1.
4. **The famous `maxFIFOocc = 11` is the watermark**, not the DREAM SCA buffer depth.

---

## 1. What the watermarks actually do

Registers live in the FEU **TrigConfig** register, control-bus `0x100008` (FEU User's Manual
§3.1.3):

```
|   30    | 29-24     | 23-18     | 17-12     | 11-0      |
| LocThrot| OvrThersh | OvrWrnHwm | OvrWrnLwm | TimeStamp |
```

The FIFO in question is the **trigger de-randomisation FIFO, 64 entries deep**. It sits between
"a trigger was accepted" and "the SCA has been read out through the ADC and shipped over UDP".

**Readout is NOT gated by the HWM.** It is greedy: "if no events are queued in the system, the
readout of Dream ASICs starts immediately after the trigger" (manual §3.2.3). The readout engine
drains the queue continuously. The watermarks are pure **flow control / backpressure on the
trigger-acceptance side**:

| mark | meaning |
|---|---|
| `OvrWrnHwm` | queue got this full → raise **OverflowWarning** → FEU asserts BUSY → **TCM stops sending triggers** |
| `OvrWrnLwm` | queue drained back to this → clear OverflowWarning → TCM resumes |
| `OvrThersh` | hard **Overflow**; requires a reset/resync to clear (fault, not flow control) |
| `LocThrot` | if 1, the FEU also refuses triggers locally while in the OverflowWarning condition |

The Hwm/Lwm pair is a **hysteresis band** (a Schmitt trigger) so the BUSY line does not chatter
on every single event.

### Comparison sense — inferred from our data, not the manual

The manual says the flag is raised when occupancy *exceeds* Hwm and cleared when it becomes
*lower than* Lwm. Our measurements are inconsistent with that literal reading and imply:

```
assert OverflowWarning when  occ >= Hwm
clear  OverflowWarning when  occ <= Lwm
```

Two independent reasons:
- `maxFIFOocc == Hwm` exactly. If the flag needed `occ > Hwm`, the FIFO would have had to reach
  `Hwm+1` for the flag to fire, so the max-hold would read `Hwm+1`.
- `Hwm=1 / Lwm=0` sustains 2001 Hz indefinitely. Under a literal "clear when `occ < Lwm`" the
  warning could never clear at `Lwm=0` (occupancy cannot go below 0) and the FEU would wedge
  permanently BUSY after the first trigger. It does not.

Treat the off-by-one as empirical. It does not affect any conclusion below.

---

## 2. RunCtrl overwrites the cfg values

`~/Feu/Firmware5/Distribution/Sources/Software/RunCtrl/RunCtrl.c:998-1051`:

```c
drm_derand_buf = 512 - TRIGLAT;                     // TRIGLAT = cfg latency (dream_reg[12])
drm_evt_buf    = drm_derand_buf / Main_Conf_Samples;

if( drm_evt_buf > 20 ) { Hwm = buf-4; Lwm = buf-16; }   // DEAD: overridden by the next line
if( drm_evt_buf > 16 ) { Hwm = buf-4; Lwm = buf-8;  }   // plain `if`, not `else if`
else if( buf > 8 )     { Hwm = buf-3; Lwm = buf-6;  }
else if( buf > 4 )     { Hwm = buf-2; Lwm = buf-4;  }
else if( buf > 2 )     { Hwm = 2;     Lwm = 1;      }
else if( buf > 0 )     { Hwm = 1;     Lwm = 0;      }

if (cfg_OvrWrnHwm > Feu_TrgFifo_Hwm) cfg_OvrWrnHwm = Feu_TrgFifo_Hwm;   // ONE-DIRECTIONAL
if (cfg_OvrWrnLwm > Feu_TrgFifo_Lwm) cfg_OvrWrnLwm = Feu_TrgFifo_Lwm;
```

Note the `buf > 20` branch is **dead code** — the following plain `if (buf > 16)` overwrites it
unconditionally, so for `buf > 20` you get `Lwm = buf-8`, never `buf-16`.

`OvrThersh` and `LocThrot` are **not** clamped and pass through unchanged.

RunCtrl logs the receipt to its log file:
`"Warning Feu TrgFifo_Hwm will be forced to %d instead of requested %d"`.

### Verification

- Reproduces **all ten** `drm_evt_buf` values recorded in
  `~/beam_july/test/deadtime_study/results/deadtime_db.csv` exactly (61/30/15/10/7/5/3/2/1/1 at
  latency 24), confirming `TRIGLAT` == the cfg `latency` value.
- Live read of `0x100008` during a run at **latency 35 / n32** → `drm_evt_buf = (512-35)//32 = 14`
  → cap `Hwm = 14-3 = 11`, `Lwm = 14-6 = 8`. Hardware held exactly **11 / 8** while the cfg said
  **20 / 16**.

### Effective cap vs n_samples

| n_samples | `drm_evt_buf` (lat 35) | cap Hwm | cap Lwm |
|---|---|---|---|
| 8 | 59 | 55 | 51 |
| 16 | 29 | 25 | 21 |
| 32 | 14 | **11** | **8** |

---

## 3. The other trap: a stale `dream_daq_control.py`

`dream_daq_control.py` runs as a **long-lived server** (tmux `dream_daq`, listens on port 1101).
It builds each sub-run cfg from the template using **the code it was started with**. Any
`make_config_from_template` parameter added to the repo after the server started is silently
dropped — no error, no warning; the run proceeds with the template default.

**Confirmed no-ops** — the archived per-sub-run cfgs
(`~/july_dream/dream_run/<run>/<subrun>/*.cfg`) show the template default, identical at every
point of the sweep:

| run | swept param | archived cfgs | verdict |
|---|---|---|---|
| `zs_vetolen` | `Trig_Conf_TrigVetoLen` 0/250/500/1000 | **all 5 = 0** | never happened |
| `zs_sparserd` | `Main_Conf_SparseRd` 0/1/3 | **all 3 = 0** | never happened |
| (2026-07-22) | `inter_packet_delay` 10 | stayed 100 | never happened |
| (2026-07-22) | `ovr_wrn_hwm` | stayed 20 | never happened |

So **"TrigVetoLen is inert under external triggering"** and **"SparseRd does nothing"** are both
unsupported. Those knobs were never applied.

**Still valid: the `NbOfSamples` sweep.** It travels via `Sys NbOfSamples` (the archived cfgs
really do read 32/16/8) and RunCtrl copies it into `Main_Conf_Samples` at configure time, which
is why the per-FEU line reads 32 everywhere. The 9.6/6.2/2.9 ms comb scaling is real.

**`UdpChan_MultiPackThr` has NOT been re-checked** — verify before trusting that null too.

### Rules that follow

1. After editing `dream_daq_control.py`, **restart the server** or the change does nothing.
2. **Never believe a null** without checking the swept parameter in the archived per-sub-run cfg.
3. For FEU registers, also **read them back off the hardware** — the cfg is not the last word,
   because RunCtrl rewrites some values (watermarks, `Main_Conf_Samples`, `RdClk_Div`).

---

## 4. Measurements (2026-07-22, beam off)

Runs `wm_pulser2` and `wm_pulser_base4k`. Fixed pulser on M4.C ← M6.D, n32 / lat35 / IPD10,
ZS k8. All 8 FEUs agreed to within a few counts.

| cfg Hwm | hardware Hwm/Lwm | pulser | maxFIFOocc | accepted | closeDrop | fifoDrop |
|---|---|---|---|---|---|---|
| none (20) | **11 / 8** | 4 kHz | **11** | 3079.2 Hz | 0 | 0 |
| 6 | **6 / 3** | 1 kHz (unsaturated) | — | 1000.6 Hz | 0 | 0 |
| 3 | **3 / 1** | 4 kHz | **3** | 3079.3 Hz | 0 | 0 |
| 1 | **1 / 0** | 4 kHz | **1** | 2001.2 Hz | 0 | 0 |

(The `Hwm=6` row straddles the 1→4 kHz pulser change and is only a control for the
non-saturating regime; its occupancy is not a watermark measurement.)

### Findings

- **The clamp is one-directional, confirmed on hardware.** 20 and 48 both get forced to 11;
  6, 3, 1 pass through untouched. This is precisely why the 07-19 sweep was null.
- **`maxFIFOocc == Hwm` exactly**, every time. The occupancy is pinned by the watermark.
- **Always 0 drops**, at every setting. The FEU never refuses anything it is *sent* — the
  throttle acts as FEU BUSY → TCM veto, and the TCM discards the vetoed triggers upstream.
  Consistent with the comb study's TCM `event rx` vs `event tx` result.
- **Throughput is flat from Hwm 11 down to Hwm 3** (3079.2 vs 3079.3 Hz — identical) and only
  collapses at **Hwm 1** (2001 Hz = 500 µs/event). 500 µs ≈ the fully-serialised per-event time
  `n × (4.83 + 0.998 × IPD) = 32 × 14.81 = 474 µs`.
  → the derandomiser buys ~1.5× by overlapping SCA readout with UDP transmission of the previous
  event, and **that benefit saturates at depth 3**. Depths 4–11 contribute nothing to sustained
  throughput.

### Bonus: independent confirmation of the IPD model

The same 1 kHz pulser gave **298 Hz at IPD 100** and **1000 Hz (100% acceptance) at IPD 10**,
matching `interval = n × (4.83 + 0.998 × IPD) µs` fitted from `deadtime_db.csv`. The first
attempt's 298 Hz was the stale server pinning IPD at the template's 100.

---

## 5. What this does to the comb arithmetic

The comb dead cycle is `N_accepted(n) × t_readout(n)`. The `N_accepted` factor was previously
hand-waved as "SCA depth 14.9, tightened by delayed cell release to ~11". It is now exact — it
is the **RunCtrl-derived HWM**:

| n_samples | firmware Hwm | measured `N_accepted` | binding constraint |
|---|---|---|---|
| 32 | **12** (lat 24) / 11 (lat 35) | **12.2** | the watermark |
| 16 | 26 | 15.8 | flash multiplicity (~15–16) |
| 8 | 57 | 14.8 | flash multiplicity (~15–16) |

At n32 the watermark binds. At n16/n8 the cap is far above the number of triggers the γ-flash
actually delivers, so the flash itself binds. That is why 32→16 is only ×1.55 while 16→8 is a
near-clean ×2.14. Same conclusion as before, now derived rather than asserted.

---

## 6. The actionable hypothesis (UNTESTED — needs beam)

Lowering the HWM to ~3 costs **zero** sustained throughput but caps how many events one burst
can seize at once. Against a real γ-flash this should **chop the single ~9.6 ms post-flash
blackout into shorter, interleaved BUSY blocks**, letting tail physics triggers in earlier —
exactly the comb study's "stop spending the readout budget on the flash burst, spread it over
the tail" objective.

**This is an inference, not a measurement.** A continuous 4 kHz pulser always refills the FIFO,
so it cannot exhibit interleaving. The test is a beam run at `ovr_wrn_hwm: 3` versus a `Hwm=11`
control, comparing the event distribution vs time-since-flash.

---

## 7. Tooling

- **`dream_scripts/feu_trig_counters.py`** — per-FEU read of Hwm/Lwm/OvrThersh/LocThrot plus
  accepted / closeDrop / fifoDrop / maxFIFOocc, over slow-control UDP (port 1300 + FeuId).
  Read-only by default; `--latch` is opt-in because `LatchStat` is a *write*.
  **Statistics are stale until you latch** — an unlatched read returns whatever was frozen last.
  **Parser gotcha:** the FEU replies `00018 = 0x00000238`. Parse the value *after* the `=`; a
  naive token scan picks up the **address echo** and silently reads `0x100018` as the number 18.
- **`dream_daq_control.py`** — `ovr_wrn_hwm`, `ovr_wrn_lwm`, `loc_throt` are now settable
  per-run and per-sub-run (alongside `inter_packet_delay`, `sparse_rd`, `rdclk_div`,
  `trig_veto_len`). Remember rule 1 in §3.
- **`run_config_wm_pulser.py`** — the 4-point sweep; prints predicted `maxFIFOocc` per point.
- **`n1081b/set_veto_open.py`** — see §8.

## 8. Beam-off gotcha: `flash_random` blocks the pulser

`trigger_mode.py flash_random` leaves M4.C in `FN_OR_VETO`, so the pulser only passes while the
PS/beam-derived veto line is LOW. **With beam off that line never opens** and a DREAM run records
0 events at `IntRate 0.00 Hz` — with no error anywhere.

`n1081b/set_veto_open.py` sets C to plain `FN_OR` (which ignores the veto line entirely),
touching **only** section C, so the restore is a single `trigger_mode.py` call. Note the M4.C
gotcha applies: `set_section_function` must be called *before* `configure_or`, or the function
type silently stays whatever it was.

**Restore after any pulser work:**
```
n1081b/trigger_mode.py scint --singles --ps-pickup     # also restores C to FN_OR_VETO
n1081b/set_pulser.py                                   # back to Poisson 1.5 ms
```
Verify with `set_veto_open.py --show`, `set_ps_trigger_delay.py --show` (expect delay 1800),
`set_pulser.py --show`.

## 9. Operational notes

- Launch runs with `.venv/bin/python daq_control.py <bare-config-name>.json`. There is no
  `start_run.sh` in this repo despite older notes referring to one.
- Starting a run immediately after the previous one can hit
  `ConnectionResetError` on the HV client — the listen backlog on port 1100 is 1 and the
  previous run's HV-monitor teardown races the new connection. Harmless; just retry.

## 10. See also

- `docs/DREAM_flash_comb_study_2026-07-19.md` — the comb study. Its `TrigVetoLen`, `SparseRd`
  and watermark rows are **invalidated** by §3 above; its `NbOfSamples`, TCM and FEU-drop
  results stand.
- `~/beam_july/test/deadtime_study/` — the `drm_evt_buf` / rate-vs-IPD dataset used above.
- FEU User's Manual §3.1.3 (`~/Documents/dream/221217_FeuUsersManual.pdf`).
