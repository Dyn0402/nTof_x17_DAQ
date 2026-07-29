# Switch-swap runbook — 2026-07-22

Everything from "the new switch is cabled" to "RAW at low IPD is confirmed good", in order.
Each step is cheap and each one *can* falsify the upgrade before the next one costs beam.
**Stop at the first failure.**

Prerequisite state, verified 2026-07-22 16:30 (nothing to do here — it is already done):

| | |
|---|---|
| DREAM net | `enp4s0` (AQC113, `atlantic`), 192.168.10.8/24, **MTU 9000**, PCIe **x4 @ 8 GT/s** |
| CERN net | `eno1` (I219-LM), 128.141.177.103 — **unaffected by the FEU switch swap** |
| Link speed | **1000 Mb/s** — correct; the link partner advertises only 10/100/1000, i.e. it is still the old 1 G switch port |
| Reachability | all 8 FEUs + all 6 N1081B boards ping |
| Baseline | `baseline_preswitch_2026-07-22/baseline.md` — **MAC↔IP map**, link state, counters |

---

## Step 0 — before you pull any cable

```bash
cat config/current_run_state.json          # DAQ stopped?
ls -la config/n1081b_access/               # no session held? (.244 is quarantined)
```

Stop the DAQ and everything that polls the boards (`poll_modules.py`, the time-tag watcher,
the scan watcher). **Never SIGKILL them** — a dirty disconnect is exactly how an N1081B
wedges. Label and photograph every cable.

Optional but do it now, it needs the CERN net which the swap does not touch:

```bash
sudo apt install iperf3
```

Configure the new switch **on the bench**: jumbo enabled, and confirm the uplink port is a
real 10 G port. Do not configure it in place with the FEUs hanging off it.

---

## Step 1 — "Can I talk to everything, and do I have the speed?" ⟵ your two questions

One command answers both:

```bash
docs/network_upgrade_10g/scripts/post_switch_check.sh
sudo docs/network_upgrade_10g/scripts/post_switch_check.sh --tune    # also raises the RX ring
```

It checks, and fails loudly on, in this order:

1. **All 8 FEUs + all 6 N1081B boards ping.** Ping only — it never opens a board session.
2. **MAC identity against the pre-swap baseline** — catches "it pings, but that IP is now a
   different box" after a re-patch. This is the check a plain ping sweep misses.
3. `enp4s0` is still the `atlantic` card (guards the [name trap](05_as_built_2026-07-22.md#3-the-name-trap--read-before-editing-any-config)).
4. **Link speed 10000 Mb/s**, plus what the link partner advertises.
5. MTU 9000, PCIe still x4 @ 8 GT/s (a re-seat during switch work can silently drop it).
6. RX ring (raise 2048 → 8184 before any low-IPD ladder; `--tune` does it, not persistent).
7. Host drop counters + the jumbo counter.

Run it **today, before the swap**, to see what a failing run looks like: it correctly
reports `FAIL link speed 1000 Mb/s — NOTHING CHANGED`, which is exactly the discriminating
behaviour you want after the swap.

### Reading the speed result

| Reads | Meaning |
|---|---|
| **10000 Mb/s** | goal. Proceed. |
| 5000 / 2500 | NBASE-T fallback — cable not Cat6a, or port not really 10 G. A later IPD threshold near **28** confirms 2.5 G. |
| **1000 Mb/s** | the DAQ uplink is still in a 1 G port. Nothing was gained. |

**Link speed is necessary, not sufficient** — it proves the host↔switch segment, not the
switch's internal path or its uplink. If a synthetic number is wanted and a 10 G peer
exists, `iperf3 -c <peer> -t 30 -P 4` (PASS ≥ 9.0 Gb/s) and `-u -b 9G` (watch the **loss**
column — the readout is UDP). With no peer, the bench IPD ladder is the real throughput
proof and needs no peer and no beam.

### ⛔ Two traps

**Do not test jumbo with ping.** `ping -M do -s 8972` is a **false failure on this segment
on any switch** — N1081B boards echo at most 1500 bytes, FEUs at most 2023. This is
measured, not theoretical. `post_switch_check.sh` deliberately omits it.

**`atlantic` does not use the Intel counter names.** `03_test_plan.md` Tests 5 and 7 tell
you to watch `rx_missed_errors` / `rx_over_errors` / `rx_no_dma_resources`. **Those do not
exist on this card** — grepping for them returns nothing, which reads as "clean" when it is
really "absent". The equivalents are **`InErrors`, `InDroppedDma`, `Queue[N] InErrors`,
`Queue[N] AllocFails`**. The check script uses the right ones.

**If an N1081B does not ping:** check the port link LED first. Do **not** retry connections
and do **not** open a session "to see if it's OK" — that extends the wedge. Follow
`n1081b/HANDOFF_2026-07-15_wedge_root_cause.md`.

---

## Step 2 — jumbo, the silent killer

A switch that silently defaults to MTU 1500 clamps every readout frame near 1500. That
**looks like data corruption, not like a config error**, and it will make a perfectly good
10 G link produce a garbage IPD ladder.

The cheapest check needs no root and no capture — `atlantic` counts jumbo frames itself.
**During a run:**

```bash
watch -n2 'ethtool -S enp4s0 | grep InJumboPackets'     # must be CLIMBING
```

Climbing ⇒ jumbo works. Stuck at 0 while data flows ⇒ the switch is clamping. For the size
distribution, or if that is ambiguous:

```bash
sudo docs/network_upgrade_10g/scripts/jumbo_capture.sh    # during a run
```

PASS = max frame well above 1500, toward the FEU cap of 8192 (`MultiPackThr` 4888).

---

## Step 3 — a known-good run before any ladder

Restart the DAQ and take a short pedestal or a normal ZS run. **Confirm normal is normal
again** before changing anything. If this is not clean, the problem is the swap, and every
later number would be uninterpretable.

---

## Step 4 — RAW at low IPD, the actual goal

**Smoke point first** — one 3-minute RAW subrun at IPD 10, the intended operating value and
the one that was catastrophically corrupt (99 % eventId gaps) on 1 GbE:

```bash
.venv/bin/python run_config_raw_ipd_10g.py --smoke
.venv/bin/python daq_control.py run_config_raw_ipd_10g_smoke.json
# while it runs:
watch -n2 'ethtool -S enp4s0 | grep InJumboPackets'
```

Then the full ladder — held **identical** to this morning's 1 GbE `raw_ipd_ladder` so it is
a true A/B (n32 RAW, lat 35, sample_period 60, PS+singles co-framed, drift 600 / resist 530,
3 min/point, brackets at IPD 100 both ends). Points: `100, 30, 15, 10, 5, 3, 100` ≈ 21 min.

```bash
.venv/bin/python run_config_raw_ipd_10g.py
.venv/bin/python daq_control.py run_config_raw_ipd_10g.json
```

**"Good reads" is defined before the measurement** (`lib_deadtime.py`):
CLEAN = eventId gap fraction ≤ 0.1 % **and** cross-FEU count spread ≤ 2 %.

| | 1 GbE, measured today | 10 GbE, predicted |
|---|---|---|
| Clean threshold | **IPD 75** | **IPD ≈ 4–5** |
| gaps at IPD 10 | **99 % (at IPD 30) / 86.8 % (at 15)** | **0.000 %** |
| RAW ev/spill in 4–10 ms @ IPD 10 | 11.00 (only reachable at IPD 75) | **~23–24** |
| ZS reference, same band | 23.45 @ IPD 10 | unchanged |

**PASS = RAW reaches parity with today's ZS, with full waveforms.** That is the number to
quote and the entire point of the upgrade.

Analysis:

```bash
~/beam_july/analysis/flash_comb/tools/raw_ipd_analysis.py
docs/network_upgrade_10g/scripts/analyze_link_load.py --iface enp4s0 --line-mbps 10000
```

**`--iface enp4s0`, not `eno1`.** The NICs swapped roles while `enp4s0` kept its name; the
1 GbE analysis used `eno1` and post-swap that column is CERN traffic. Event size must still
come back at **311 kB** — if it does not, something other than the link changed and the A/B
is not clean.

### Traps that have burned this measurement before

- A point reading **~1.0 ev/spill** is likely a **SiPM wall dropout**, not an IPD effect.
  Check wall gain before interpreting.
- **`live_windows`-derived "cycle µs" is meaningless here** — it returns intra-burst arrival
  spacing (~27 µs), not the readout cycle. Trust **ev/spill** and **band/spill** only.
- **Do not run production at the measured threshold.** Sub-threshold is a **cliff, not a
  slope** (0.000 % → 56 % gaps between IPD 75 and 50 on 1 GbE) and in-band yield collapses
  below it. Back off to **IPD 10–15** whatever the ladder says.

---

## Step 5 — record it

Record in this directory: **switch model, firmware, port assignments, jumbo setting,
flow-control setting.** If an N1081B wedges in the days after the swap, the swap is a
suspect and you will want those details. Add the switch's **per-port discard counters** to
what you check when readout looks wrong — with `atlantic` exporting little, that counter is
now the most direct view of the exact failure mode this upgrade exists to fix.
