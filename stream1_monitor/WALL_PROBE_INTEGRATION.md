# Implementation brief: waveform-level wall probe for the stream1 monitor

**For whoever owns `stream1_monitor/`.** This adds a *direct* measurement of SiPM-wall
liveness to the monitor, replacing inference from file size. Two new read-only files are
already here and tested against live EOS; nothing in the existing monitor has been
touched, so wiring it in is your call.

Background: `docs/HANDOFF_2026-07-22_sipm_wall_dropouts.md` (why),
`docs/NTOF_RAW_FORMAT.md` (the file format), `~/beam_july/analysis/sipm_wall_filesize/`
(offline study).

## Why bother — the size proxy has two real weaknesses

1. **The baseline moves.** Full level was ~1.9 GiB in runs 224524–526 and ~2.4 GiB in
   224530/531 — different run configurations, same healthy walls. Hence the trailing-window
   baseline, and hence the two failure modes already in your README: a config change
   briefly mis-flags, and an outage longer than the window becomes the new normal.
2. **It cannot say *what* stopped.** Size is the summed hit multiplicity of all 51
   channels.

The waveform probe has neither problem. Every proton pulse produces a gamma flash, and the
DAQ *always* keeps the first 30 µs of every channel (zero suppression can't remove it). So
the flash amplitude is an absolute, per-channel, configuration-independent reference:

| state | wall flash amplitude | note |
|---|---|---|
| live | ~34 000 ADC counts | rails to the bottom of the ADC |
| collapsed | 350 – 1 200 | measured 07-22 across all 32 channels |

Two orders of magnitude apart. No baseline, no window, no tuning.

## What you have been given

| file | role |
|---|---|
| `stream1_monitor/ntof_raw.py` | raw-file reader (banks, ZS blocks, waveforms). Copy of the analysis one; keep them in sync or import one from the other. |
| `stream1_monitor/wall_probe.py` | the probe itself — `probe_latest()`, `probe_file()`, plus a CLI |

Both are read-only, own no hardware, and need only the Kerberos ticket the monitor already
requires for `xrdfs`.

```python
from wall_probe import probe_latest
res = probe_latest()          # newest .finished file of the newest run
res['verdict']                # 'live' | 'dead' | 'degraded' | 'unknown'
res['flash_median']           # median flash amplitude over the channels measured
res['dets_measured']          # e.g. ['WALA', 'WALB', 'WALC']
res['channels']               # per channel: det, chan, flash_amplitude, baseline,
                              # rms, bank_bytes, zs_blocks
res['budget_truncated']       # True = byte budget hit, later walls not reached
```

CLI: `python3 wall_probe.py`, `python3 wall_probe.py 224528`, `python3 wall_probe.py 224528 10`.

## Cost — measured, not estimated

Reads use `xrdfs cat | head -c N`, which closes the pipe once N bytes have arrived. The
cost is dominated by connection setup, not bytes:

| budget | wall time | what it reaches (live file) | what it reaches (dead file) |
|---|---|---|---|
| `xrdfs ls -l` (today) | 0.03 s | — | — |
| **2 MiB (default)** | **0.23 s** | WALC, 1 channel | WALA+WALB+WALC, 24 channels |
| 24 MiB | 0.54 s | WALA+WALB+WALC, 22 channels | same |
| 48 MiB | ~1 s | all four walls | all four walls |

The asymmetry is free information and works in your favour: a live channel's ACQC bank is
~1 MB, a dead one ~60 kB, so the same budget reaches **more** channels exactly when
something is wrong. But note the corollary:

> **A `live` verdict from a 2 MiB probe covers WALC only.** It is not a clearance for all
> four walls. `WALD` is never reached under ~35 MiB.

Practical recommendation: probe at 2 MiB every poll; if the verdict is `live` **and** you
want whole-wall coverage, re-probe at 48 MiB on a slower cadence (every Nth poll, or on
transition). At 0.2–1 s against a 120 s loop, even the wide probe is negligible.

## Suggested integration

Minimal, additive — the size logic keeps working exactly as it does:

1. **Poll loop** (`stream1_size_controller.py`): after the existing listing, if a *new*
   `.finished` file appeared, call `probe_file()` on it. Keep it in a `try/except`: a probe
   failure must never take down size monitoring, which is the cheaper and more robust
   signal.
2. **Per-file CSV**: add `flash_median`, `wall_verdict`, `wall_channels_measured`,
   `dets_measured`. Then the offline history gains a physical quantity, not just bytes.
3. **State JSON** (`config/stream1_filesize_state.json`): add a `walls` block —
   `{verdict, flash_median, channels_measured, dets, probed_file, probed_at}`. Flask and
   the Telegram monitor read the same file, so both get it for free.
4. **File Sizes tab**: flash amplitude on a second y-panel under the size plot, and a
   verdict chip on the status card. Flash is the physical quantity; size is the proxy —
   consider leading with flash.
5. **Telegram**: a `rule_wall_flash_collapsed` firing on `verdict == 'dead'` for 2
   consecutive probes is far crisper than the 5-file size warning, because it has no
   baseline to drift. Suggest keeping the size rules as the safety net.

Thresholds live at the top of `wall_probe.py` (`FLASH_DEAD_BELOW = 5000`,
`FLASH_LIVE_ABOVE = 20000`). Move them into `config/stream1_filesize_config.json` if you
want them tunable without a restart, matching how you handle the size options.

## Guardrails and failure modes

- **Only probe `.finished` files.** A file still being written can end mid-bank. The reader
  stops cleanly at a truncated tail, but a partial *first* event yields `unknown`.
- **`unknown` is not `dead`.** It means no wall bank with a valid flash block was found —
  a truncated read, a changed channel map, or an EOS hiccup. Alert on `dead`, not on the
  absence of `live`.
- **Don't probe every file in a listing** — one probe per poll on the newest file. Probing
  a 150-file backlog at 0.2 s each would be a pointless minute of EOS load.
- **The channel map can change between runs.** `MODH` is parsed per file, so the probe
  follows it; but if n_TOF renames or re-cables the walls, `WALL_DETS` in `wall_probe.py`
  needs updating. A sudden `unknown` streak across a run boundary is the symptom.
- **Partial collapses exist.** On 07-22, WALD.5 kept 337 ZS blocks and WALB.4 kept 66 while
  their neighbours kept 1. A *median* over channels is the right summary (already used),
  but per-channel values are in `res['channels']` if you want to surface stragglers.
- **`degraded`** (flash between 5 000 and 20 000) has never actually been observed. If it
  starts appearing, that is itself interesting — it would mean a partial gain loss rather
  than the clean two-state behaviour seen so far.

## Validation already done

- Reader parses 150 MB each of a live file (224524_0) and a collapsed one (224528_10) with
  zero bank-boundary errors, and 2 MB of several others.
- Probe on 224524_0 → `live`, flash 34 256, WALC.2, bank 1003 kB, 487 ZS blocks, 0.23 s.
- Probe on 224528_10 → `dead`, flash 598.5, 24 channels across WALA/B/C, banks 60–130 kB,
  1–45 blocks, 0.32 s.
- Cross-checked against the full offline decode of all 51 channels
  (`~/beam_july/analysis/sipm_wall_filesize/wall_channel_check.json`), where the walls read
  0.017–0.021× and every other detector reads 0.99–2.7×.

## UPDATE (07-22 afternoon): the dropouts are ours, and two things follow

`RMPA`/`RMPC` turned out to be **our own** mesh charge-injection ramp trigger (A and C),
and wall gain tracks it with φ = +0.947 — the walls collapse in every `randOff` / `m05Off`
sub-run because `scan_control` disables .245 M6.B outputs 0-3. Full story:
`docs/HANDOFF_2026-07-22_sipm_wall_dropouts.md`.

Two consequences for the monitor:

1. **Alerting must not fire on our own scans.** A mesh-OFF sub-run legitimately collapses
   the walls, so a bare "walls dead" alert will page constantly during any mesh-modulated
   run. Gate it on our own state — the current sub-run tag, or better, cross-check RMP:
   *walls collapsed **while** RMP is firing* is the genuinely anomalous combination, and it
   is the one that never occurred in 312 files. That is the alert worth having.
2. **Beware EOS time.** `xrdfs` mtimes are **UTC**; every one of our logs is local CEST.
   Comparing them raw is a silent 2 h error — it is what hid this for a day. Convert with
   `plot_sizes.eos_utc_to_local()` (or the equivalent) anywhere the monitor joins EOS
   timestamps to our run/sub-run bookkeeping.

Adding RMP to the probe is now clearly worth it: 15 kB banks, unambiguous, and it is the
variable that makes the alert specific. It sits ~27 MB into a live file and ~1.8 MB into a
collapsed one, so a wide probe reaches it cheaply exactly when the walls look wrong.
