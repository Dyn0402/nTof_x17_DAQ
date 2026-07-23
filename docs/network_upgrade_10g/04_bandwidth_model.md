# Why the host link is the constraint, and what 10 GbE should buy

> **Updated 2026-07-22 with measured link-load data.** The premise is no longer an
> extrapolation — the DAQ's own `system_stats` log has 2 Hz per-interface throughput
> covering today's IPD ladder, and it confirms the model quantitatively. Reproduce with
> `scripts/analyze_link_load.py`.

## The claim

All 8 FEUs push RAW readout into **one** 1 GbE host port (`eno1`,
`ethtool`-confirmed 1000 Mb/s). Each FEU's *own* link is also 1 GbE and is *not* the
constraint — 8 × 1 Gb of sources feeding 1 × 1 Gb of sink. `Feu_InterPacket_Delay` (IPD)
paces injection down to what that sink can absorb. Set it too low, the aggregation point
overflows, datagrams drop, events are destroyed.

## The ladder (2026-07-22, n32 RAW, 8 FEUs, PS+singles beam trigger, 3 min/point)

| IPD | ev/spill | **ev/spill in 4–10 ms** | eventId gaps | verdict |
|---|---|---|---|---|
| 100 (a) | 30.4 | **11.00** | 0.000 % | clean |
| 100 (b) | 30.7 | **11.00** | 0.000 % | clean — bracket, no drift |
| **75** | 36.0 | **11.00** | 0.000 % | **clean — threshold** |
| 50 | 3.8 | 1.39 | 56.3 % | CORRUPT |
| 30 | 7.5 | 0.68 | 99.0 % | CORRUPT |
| 15 | 9.4 | 1.09 | 86.8 % | CORRUPT |

## The measured link load

`system_stats_watcher.py` logs `net_eno1_rx_bps` at 2 Hz. Integrating the logged bytes
over each ladder point and dividing by the events actually recorded gives the RAW event
size **on the wire** — a direct measurement, not an extrapolation from the n64 number:

| point | MB integrated | events | **kB/event** | kB/FEU |
|---|---|---|---|---|
| ipd100_a | 500.5 | 1611 | **310.6** | 38.8 |
| ipd075 | 593.2 | 1908 | **310.9** | 38.9 |
| ipd100_b | 505.8 | 1627 | **310.9** | 38.9 |

**311 kB/event across 8 FEUs = 38.8 kB/FEU at n32**, with the three clean points agreeing
to **0.1 %**. That agreement across two different IPD values and a bracketed pair is the
check that the method is sound.

> This supersedes the earlier estimate of 376 kB/event (naively half the measured 94 kB/FEU
> at n64). The real n32 RAW event is ~17 % smaller than half the n64 event, which resolves
> the ~20 % overshoot the first version of this document flagged as unexplained.

Applying the cycle model `cycle_us = n × (4.83 + 0.998 × IPD)` to get the burst duration,
the **instantaneous** rate demanded during the spill burst is:

| point | IPD | cycle µs | burst ms | burst MB | **demand Mb/s** | **% of usable 941 Mb/s** | verdict |
|---|---|---|---|---|---|---|---|
| ipd100_a | 100 | 3348 | 101.8 | 9.45 | 743 | **79 %** | clean |
| ipd100_b | 100 | 3348 | 102.8 | 9.54 | 743 | **79 %** | clean |
| **ipd075** | **75** | 2550 | 91.8 | 11.19 | **975** | **104 %** | **clean** |
| ipd050 | 50 | 1751 | 63.0 | 11.19 | 1420 | **151 %** | CORRUPT |
| ipd030 | 30 | 1113 | 40.1 | 11.19 | 2235 | **237 %** | CORRUPT |
| ipd015 | 15 | 634 | 22.8 | 11.19 | 3924 | **417 %** | CORRUPT |

Setting demand equal to the wire and solving for IPD gives a **predicted threshold of
IPD ≈ 78**. Measured: clean at 75, corrupt at 50.

**The corruption threshold sits exactly where the demanded burst rate crosses 1 GbE line
rate.** That is the whole argument, and it is now a measurement rather than an inference.

The ~4 % slack at IPD 75 (104 % of usable, yet clean) is within the model's error — a few
percent of FEU-side buffering over a 92 ms burst, or a slightly lower offered rate than
the average ev/spill implies. It does not change the picture; it does mean IPD 75 is
running with essentially **zero** margin, which is worth knowing independently of any
upgrade.

## Three further reasons this is a link limit, not a duty-cycle or FEU-internal one

1. **The threshold matches the 2026-07-07 saturated-generator ladder**
   (`deadtime_db.csv`: clean ≥ 75, corrupt ≤ 50) — completely different trigger duty
   cycle, same breaking point. A duty-cycle effect would have moved.
2. **It is a cliff, not a slope**: 0.000 % gaps at 75 → 56.3 % at 50. Buffer overflow
   behaves like this; contention and jitter do not.
3. **`deadtime_db.csv` shows n32 RAW at IPD 16 is CLEAN with 1–2 FEUs and CORRUPT with
   4–8.** Same per-FEU pacing, only the *aggregate* changes.

Corroborating: `eno1` shows **zero** rx_errors / rx_missed / rx_over / CRC across 127 M
packets and 500 GB, including during the corrupt points. The link is healthy; it is
simply full, and the loss happens at the **switch's egress queue toward the host**, which
the host cannot count. Do not go looking for it in host counters.

## ⚠ The 2 Hz log will never *look* saturated — you must integrate

The readout burst is ~100 ms inside a ~3.6 s spill period. A 0.5 s log bin therefore
averages the burst down by ~5×, and the whole-point mean by ~35×:

| point | mean (whole point) | peak 0.5 s bin | burst duty in a bin | **true burst rate** |
|---|---|---|---|---|
| ipd100 | 20.8 Mb/s | 163.7 Mb/s | 20 % | **743 Mb/s** |
| ipd075 | 24.9 Mb/s | 198.5 Mb/s | 18 % | **975 Mb/s** |

A peak bin of ~200 Mb/s on a 1000 Mb/s link reads as *"the link is 80 % idle"* on the
GUI Overview plot. It is not — it is momentarily **over** 100 %. The sample period is
floored at 0.5 s by `MIN_SAMPLE_PERIOD_S` in `system_monitor/system_stats_controller.py`,
so this cannot be fixed by turning the logger up; it has to be corrected by integration.
**Anyone diagnosing readout corruption from the Overview network plot will reach the
wrong conclusion.**

Note also step 4 of the analysis: **CPU climbs monotonically as IPD drops** (8 % → 27 %
→ 32 % → 36 % → 45 %) even as *delivered* bytes fall. That is the host burning cycles on
packets that are about to become useless events.

## What 10 GbE should buy

Same arithmetic, 9410 Mb/s usable instead of 941:

```
burst_need = 36 ev x 311 kB x 8 / 9.41e9  = 9.5 ms
cycle_need = 9.5 ms / 36 ev               = 264 us/event
IPD_min    = (264/32 - 4.83) / 0.998      ~ 3.4
```

| | 1 GbE (measured) | 10 GbE (predicted) |
|---|---|---|
| Threshold IPD | ~78 predicted / 75 clean, 50 corrupt measured | **≈ 3–4** |
| Cycle at threshold | 2642 µs | 264 µs |
| Margin over the `n × 4.83 µs` floor | 17× | **1.7×** |

**Recommended operating point: IPD 10** — cycle 474 µs, demanding 5.24 Gb/s = **56 % of a
10 GbE link**. Comfortable margin, and:

> **RAW at IPD 10 on 10 GbE (474 µs/event) is marginally *faster* than today's ZS at
> IPD 10 (504 µs/event), which yields 23.45 ev/spill in the 4–10 ms band. So RAW should
> reach ~23–24 ev/spill in band — parity with ZS, but with full waveforms.**

IPD 15 is the conservative fallback: 634 µs/event, 42 % link utilisation, ~20 ev/spill.

Note the margin over the FEU floor drops from 17× to **1.7×**. The upgrade is still
link-limited rather than floor-limited, so the gain is real and not clipped — but a
further upgrade beyond 10 GbE would buy very little. **10 GbE is the right size; 25 GbE
would be wasted.**

### If the switch only does 2.5G/5G (NBASE-T)

Not a disaster: 2.5 GbE usable ≈ 2353 Mb/s → threshold IPD ≈ 28, cycle ~1058 µs. Still
**2.7× better** than today. Worth knowing before you reject a switch on price.

## Derating — do not plan to run at IPD 3.4

Unvalidated at 10× the packet rate: host CPU and IRQ load (i5-8400, 6 cores — already at
45 % during the corrupt low-IPD points), NIC ring buffers, PCIe. **Plan for IPD 10–15.**
And remember the sub-threshold cliff is catastrophic, not gradual — in-band yield
collapses 11.00 → 1.39. Margin is not optional.

**Jumbo frames matter.** `eno1` is MTU 9000 today; FEU frame cap 8192, `MultiPackThr`
4888. The new NIC and switch must both pass 9000-byte frames, or the packet rate — and
CPU cost per byte — rises ~6×, eating a chunk of the win and possibly manufacturing a new
threshold that looks like the old one.

## Where the constraint moves next

1. **The `n × 4.83 µs` FEU floor** — 155 µs at n32, 310 µs at n64. This is now the nearest
   wall, only 1.7× away.
2. **Host CPU / IRQ.** Already 45 % at high packet rates on 1 GbE. This is the most likely
   thing to bite before the floor does. Check RSS queues and ring sizes after install.
3. **~~Disk~~ — not the wall it looked like.** The logged `disk_hdd_write_bps` during the
   ladder is only **6.7–9.4 MB/s mean** (against 3.1 MB/s of mean network). Scaling to
   IPD 10 with roughly double the events/spill gives ~6 MB/s of network mean and perhaps
   ~15 MB/s of disk write — trivial for `sda4`. The **burst** needs ~22 MB of RAM
   buffering per spill, which is nothing on a 15 GB box. The earlier "10 Gb ≈ 1.25 GB/s so
   storage is the next wall" concern was based on line rate; the actual **duty is ~2 %**,
   so it does not apply. Watch free space (1.4 T) rather than write rate.
4. **Detector flash-blindness below ~4.5 ms** — physics, not fixable by DAQ.
