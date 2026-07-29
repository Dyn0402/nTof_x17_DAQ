# Test plan — "is it working?", fastest first

Ordered so that each test is cheap and each one *can* falsify the upgrade before you
spend beam time on the next. **Stop and diagnose at the first failure** — every later
test assumes the earlier ones passed.

Predictions are written down **before** the measurement. That is the point: a test you can
only interpret after the fact is not a test.

| # | Test | Needs | Time | Answers |
|---|---|---|---|---|
| 0 | Link saturation proof | **✅ already answered** | — | Is the 1 GbE link actually full? **Yes.** |
| 1 | Card enumerates + links | TX401 | 2 min | Did the hardware work at all? |
| 2 | Host↔switch throughput | TX401 + iperf peer | 10 min | Is the *path* 10 Gb, or just the card? |
| 3 | Jumbo end-to-end | TX401 + switch | 3 min | Will MTU 9000 survive the new switch? |
| 4 | FEU + N1081B reachability | switch swap | 5 min | Did the swap break anything? |
| 5 | Bench IPD ladder, no beam | switch swap | ~30 min | **Where is the new corruption threshold?** |
| 6 | Beam IPD ladder | beam | ~30 min | The physics number: ev/spill in band |
| 7 | Soak | beam | hours | Thermals, disk, slow-onset failure |

---

## Test 0 — ✅ ANSWERED, retroactively. The premise is confirmed.

No new measurement was needed: **`system_stats_watcher.py` has been logging per-interface
throughput at 2 Hz since 2026-07-15** to
`~/beam_july/slow_control/system_stats/system_stats_<day>.csv`. That log covers today's
IPD ladder, so the link load at every ladder point is already on disk.

```bash
scripts/analyze_link_load.py           # reruns the whole analysis
```

Result — integrating logged bytes over each point and dividing by recorded events gives
**311 kB/event across 8 FEUs (38.8 kB/FEU at n32)**, with the three clean points agreeing
to **0.1 %**. Converting to the instantaneous burst rate:

| IPD | demand | % of usable 941 Mb/s | verdict |
|---|---|---|---|
| 100 | 743 Mb/s | 79 % | clean |
| **75** | **975 Mb/s** | **104 %** | **clean — threshold** |
| 50 | 1420 Mb/s | 151 % | CORRUPT |
| 30 | 2235 Mb/s | 237 % | CORRUPT |
| 15 | 3924 Mb/s | 417 % | CORRUPT |

Solving demand = wire gives a predicted threshold of **IPD ≈ 78**; measured clean at 75,
corrupt at 50. **The corruption threshold sits exactly where demand crosses 1 GbE line
rate.** Full derivation in [`04_bandwidth_model.md`](04_bandwidth_model.md).

> ### ⚠ Do not read saturation off the GUI Overview plot
> The burst is ~100 ms inside a ~3.6 s spill period, so a 0.5 s log bin averages it down
> ~5×. The peak bin at IPD 75 is **198 Mb/s on a 1000 Mb/s link** — it reads as "80 %
> idle" while the link is momentarily **over 100 %**. The logger cannot be turned up
> (`MIN_SAMPLE_PERIOD_S = 0.5`), so this must be corrected by integration.
>
> Likewise, `eno1` reports **zero** rx_drop / rx_missed even in the corrupt cases — the
> loss is at the **switch's egress queue toward the host**, which the host cannot count.
> Neither the rate plot nor the drop counters will show you this problem.

**Post-upgrade, rerun the same analysis** as the cheap confirmation that the new link is
carrying what you think it is:

```bash
scripts/analyze_link_load.py --iface enp1s0 --line-mbps 10000 \
    --csv ~/beam_july/slow_control/system_stats/system_stats_<day>.csv \
    --points 'ipd010=HH:MM:SS,HH:MM:SS,10,<ev/spill>' ...
```

The event size must still come out at **311 kB** — if it does not, something other than
the link changed and the comparison is not clean.

---

## Test 1 — Card enumerates and links (2 min after install)

```bash
scripts/verify_10g_link.sh
```

Checks, all of which must pass:

| Check | Expected |
|---|---|
| `lspci -nn \| grep 1d6a` | `Ethernet controller … Aquantia … [1d6a:07b1]` |
| `lspci -k` driver in use | `atlantic` |
| `current_link_width` / `current_link_speed` | **×4 @ 8.0 GT/s** (see below) |
| `ethtool <if>` Speed | **10000Mb/s**, Duplex Full |
| `ip link` MTU | 9000 |
| `ping 192.168.10.<FEU>` | replies |

**PCIe width is the one that silently costs you performance.** ×4 @ 8 GT/s is the target,
and **SLOT4 delivers exactly that** — its root port `00:1d.0` reports `max_link_width = 4`,
`max_link_speed = 8.0 GT/s`, despite Dell's misleading `x4 PCI Express 2 x16` label.

If you see ×1 or gen1/gen2, the card is in the wrong connector: SLOT4 is the **long**
(×16-length) slot on the PCH side, easily confused with the short ×1 SLOT1 that now holds
the I210. Re-seat per [`00_system_readiness_2026-07-22.md`](00_system_readiness_2026-07-22.md) §3.
The card will *work* at ×1 (≈7.9 Gb/s) so nothing will look broken; only this check
catches it.

**If the link comes up at 1000 Mb/s instead of 10000:** the cable or the switch port, not
the card. 10GBASE-T needs **Cat6a** (Cat6 works only to ~55 m and only if quiet). Confirm
the switch port is a real 10 G port and not a 1 G port on a 10 G switch.

---

## Test 2 — Host↔switch throughput

`iperf3` is **not installed** on the DAQ PC. Install it while you still have the CERN
network up (`enp4s0` has the default route):

```bash
sudo apt install iperf3
```

You need a peer that can actually source ~10 Gb — another 10 G host, or the switch's own
diagnostics. If no 10 G peer exists, fall back to a **multi-stream test from several
FEUs**, or accept Test 5 as the real throughput proof.

```bash
iperf3 -c <peer> -t 30 -P 4          # TCP, 4 streams
iperf3 -c <peer> -t 30 -u -b 9G      # UDP — closer to how the FEUs actually behave
```

**PASS:** ≥ 9.0 Gb/s TCP. **UDP is the one that matters** — the FEU readout is UDP, and
UDP is where buffer overflow shows up as loss rather than as backpressure. Watch the
`iperf3` loss column, not just the rate.

**Marginal (6–9 Gb/s):** almost always CPU or offloads. Check:
```bash
ethtool -k <if> | grep -E 'checksum|gro|tso|gso'   # want these on
ethtool -l <if>                                    # want multiple RX queues (RSS)
ethtool -g <if>                                    # raise RX ring toward its max
```
Also watch `sar -u 1` / `mpstat -P ALL 1` for a single core pegged at 100 % in softirq —
that is an RSS problem, not a bandwidth problem.

---

## Test 3 — Jumbo frames survive the new switch

The single most likely thing to be silently wrong after a switch swap.

> ### ⛔ `ping -M do -s 8972` DOES NOT WORK ON THIS SEGMENT — measured 2026-07-22
>
> No endpoint on 192.168.10.0/24 can *echo* a 9000-byte frame, so this test returns a
> **false failure every time, on any switch**:
>
> | target | largest DF frame echoed |
> |---|---|
> | N1081B `.240` | **1500** — the CAEN board's own stack |
> | FEU `.83` / `.43` | **2023** — the FEU's ~2 kB ICMP echo buffer |
>
> It does prove the switch passes >1500 (2023 traverses host→switch→FEU→back), which is
> worth knowing. It cannot prove 9000. See `05_as_built_2026-07-22.md` §9.

The jumbo path that matters is **one-directional, FEU → host**, up to the FEU frame cap of
8192 (`MultiPackThr` 4888) — exercised only during a run. Verify it by capture:

```bash
ip link set <if> mtu 9000
# during a short run:
sudo tcpdump -i <if> -n -c 200 'src net 192.168.10.0/24 and udp' -e | \
  awk '{print $NF}' | sort -n | tail -5
```

**PASS:** frame lengths well above 1500 (toward 8192). **FAIL:** everything clamped near
1500 → the switch is not passing jumbo. Enable jumbo on the switch (often global, often
needs a reboot) and retest. Do **not** proceed to Test 5 with this failing — you will
measure a corruption threshold that is an MTU artefact and conclude the upgrade failed.

Recall the FEU-side settings this must accommodate: frame cap **8192**, `MultiPackThr`
**4888**.

---

## Test 4 — Nothing else on the subnet broke

The 192.168.10.0/24 segment is **not FEU-only**. After any switch work:

```bash
for ip in 43 44 81 82 83 110 111 118; do ping -c1 -W1 192.168.10.$ip >/dev/null \
  && echo "FEU .$ip OK" || echo "FEU .$ip *** DOWN ***"; done
for ip in 240 241 242 243 244 245; do ping -c1 -W1 192.168.10.$ip >/dev/null \
  && echo "N1081B .$ip OK" || echo "N1081B .$ip *** DOWN ***"; done
```

**Ping only.** Do **not** open an N1081B session to "check" a board — see
[`02_feu_switch_migration.md`](02_feu_switch_migration.md). A board that does not ping
after a link drop needs the documented recovery procedure, not another connection attempt.

---

## Test 5 — Bench IPD ladder, no beam ★ the decisive test

This is the direct post-upgrade counterpart to the measurement that established the
problem, and it needs **no beam**. Use the saturated internal-trigger harness:

```
~/beam_july/test/deadtime_study/harness/run_matrix.py
                                 /lib_deadtime.py     # corruption criteria
                                 /verify_run.py
results/deadtime_db.csv                               # the 2026-07-07 baseline lives here
```

Configuration: **n32 RAW, all 8 FEUs, saturating internal trigger** — identical to the
2026-07-07 matrix, so the numbers are directly comparable.

IPD ladder to walk: `100, 75, 50, 30, 20, 15, 10, 7, 5` — plus a **bracket at 100 at both
ends** so a drift cannot fake a threshold.

Corruption criteria (unchanged, from `lib_deadtime.py`):
- eventId gap fraction > **0.1 %**, or
- cross-FEU event-count spread > **2 %**

### Predicted results

| | 1 GbE (measured, both 07-07 and 07-22) | 10 GbE (predicted) |
|---|---|---|
| Last clean IPD | **75** | **≈ 4** |
| First corrupt IPD | 50 | ≈ 3 |
| Cycle at threshold | 2550 µs | ~264 µs |

Derived from the measured 311 kB/event, not extrapolated — see
[`04_bandwidth_model.md`](04_bandwidth_model.md).

**PASS — upgrade works:** threshold moves to **IPD ≤ 5**. That is the ~10× the model
predicts, and it confirms linearity in bandwidth.

**PARTIAL (threshold lands 10–30):** real gain but less than 10×. Most likely causes, in
order: PCIe ×1 (Test 1), jumbo not enabled (Test 3), CPU/RSS (Test 2), or a 2.5G/5G switch
port negotiating below 10 G. All four are checkable in minutes — and note **2.5 GbE alone
predicts a threshold of IPD ≈ 28**, so a result near 28 points straight at NBASE-T
negotiation.

**Note the margin over the FEU floor shrinks to 1.7×** (264 µs vs the 155 µs
`n × 4.83 µs` term). If the cycle flattens out well above 264 µs, the floor term is
larger than modelled — that is a real finding, and it also means nothing faster than
10 GbE would be worth buying.

**FAIL (threshold still ~75):** the host link was never the constraint, *or* the switch
uplink is still 1 Gb. Check the switch's uplink port speed first — this is by far the most
common version of this failure, and it is why the host NIC alone is not enough.

**Watch for a new failure mode:** if corruption at low IPD now shows up as *host*
`rx_missed_errors` / `rx_over_errors` (which were flatly zero on 1 GbE), the bottleneck
has moved into the host — NIC ring buffers or CPU — and the fix is `ethtool -G` and RSS
tuning, not more network.

---

## Test 6 — Beam IPD ladder, the physics number

Re-run the existing config, unchanged except for the ladder points:

```
run_config_raw_ipd_ladder.py
```
Analysis: `~/beam_july/analysis/flash_comb/tools/raw_ipd_analysis.py`

Same settings as 2026-07-22 so it is a true A/B: n32, lat 35, sample_period 60,
PS + singles co-framed, HV drift 600 / resist 530, 3 min/point, brackets at both ends.
New ladder: `100 (bracket), 30, 20, 15, 10, 5, 100 (bracket)`.

### Predicted results

| Metric | 07-22 baseline | 10 GbE prediction |
|---|---|---|
| Clean threshold | IPD 75 | IPD ≈ 4 |
| **RAW ev/spill in 4–10 ms, at IPD 10** | **11.00** | **~23–24** |
| ZS reference, same band | 23.45 @ IPD 10 | (unchanged) |

**PASS:** RAW reaches **parity with today's ZS** — ~23–24 events/spill in band, but with
full waveforms. RAW at IPD 10 on 10 GbE is 474 µs/event, marginally *faster* than today's
ZS at 504 µs/event, so parity is the floor of the expectation rather than the ceiling.
That is the entire point of the upgrade and the number to quote.

Also run `scripts/analyze_link_load.py` over the same windows: the event size must come
back at **311 kB**, and link utilisation at IPD 10 should read ~**56 %** of the new link.

**Two traps carried over from 07-22, both of which have burned this measurement before:**

- A point reading ~1.0 ev/spill is likely a **SiPM wall dropout**, not an IPD effect.
  Check the wall gain before interpreting (`sipm-wall-dropouts-0722`).
- **`live_windows`-derived "cycle µs" is meaningless here** — it returns intra-burst
  arrival spacing (~27 µs), not the readout cycle. Trust **ev/spill** and **band/spill**.

Do not run production at the threshold. Back off to **IPD 10–15** whatever the ladder
says, given how catastrophic the sub-threshold cliff is.

---

## Test 7 — Soak

Everything above is short. The failures that will actually bite are slow:

- **Thermal throttle.** AQC107 is passively cooled in a cramped desktop. Watch over hours:
  ```bash
  watch -n30 'sensors; ethtool -S <if> | grep -iE "drop|err|miss|over"'
  ```
  A link that is clean for 10 min and dirty after 2 h is thermal until proven otherwise.
- **`/mnt/data` free space** — *not* write rate. The logged `disk_hdd_write_bps` during
  the ladder was only 6.7–9.4 MB/s mean; duty is ~2 %, so even a 10× event rate leaves the
  disk far from its limit. What does bite is **capacity**: 1.4 T free goes quickly at
  RAW/low-IPD rates. Watch `df`, not throughput.
- **Counter drift.** `rx_missed_errors`, `rx_over_errors`, `rx_no_dma_resources` should
  stay at **0**. Any non-zero value that grows is the host dropping packets.
- **N1081B stability.** The boards share this segment. If a board wedges in the days after
  a switch swap, the swap is a suspect — record the switch model, port assignments, and
  any flow-control/storm-control settings so that is diagnosable later.

---

## Recording results

Append to this directory as `results_<date>_<step>.md`, and for the bench ladder append to
the existing `~/beam_july/test/deadtime_study/results/deadtime_db.csv` so the 1 GbE and
10 GbE matrices sit in one table with identical criteria. Do not start a new schema.
