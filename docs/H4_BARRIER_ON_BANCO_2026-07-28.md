# The H4 Barrier panel on banco — as built, 2026-07-28

How the *H4 Barrier — T2 TAX* panel of the SPS tab was made to work on the P2
sibling DAQ (`banco@128.141.21.144:/local/home/banco/DAQ_Control_Dream_Beam`),
after several attempts to copy it there produced a panel with no data.

Physics / variable-choice half of the story: `H4_ACCESS_INFERENCE.md`.

## Outcome

banco does **not** hold TAX data. Its Flask forwards two endpoints to this DAQ,
which already polls NXCALS, writes the per-day CSVs and computes the spans:

```
banco /sps/tax_state    ──►  ntof-x17-daq:5001 /sps/status  → h4_tax block   (~300 B, tiles)
banco /sps/tax_history  ──►  ntof-x17-daq:5001 /sps/tax_history              (~100 kB, trace + spans)
```

Both degrade to the panel's existing empty shape if this box is unreachable, so
an x17 Flask restart blanks that one panel and nothing else on the tab.

## Why the straight copy could never work

Three independent gaps, none of them a copy failure:

1. **banco's `sps_monitor` is an independent re-port, not a copy** (34 kB vs our
   24 kB). It was built from an `XBH4.%` NXCALS survey, so its H4 story is
   `XBH4.BEND.022.*:I_MEAS` currents plus `XBH4.EXPT.*:COUNTS`, and it has no
   `H4_TAX_VAR`, `_poll_tax()` or `tax_blocked_intervals()` at all. The barrier
   is `XTAX_022_023:POSITION_MEAS` — **no `XBH4.` prefix** — so that survey
   structurally could not see it. Same naming trap as `H4_ACCESS_INFERENCE.md`
   §"Watch the name". The two H4 signals are complementary, not rivals: BEND
   currents say *is the line powered*, the TAX says *is the barrier across the
   beam*. Both are kept.
2. **banco cannot reach NXCALS.** Not a GPN-registration issue — banco is
   registered (`128.141.21.144/26`, gw `.21.129`). NXCALS is on the **Technical
   Network**, and banco is not TN-trusted, so `cs-ccr-nxcals{5,6,7,8}:19093` and
   `acc-py-repo.cern.ch:443` answer `No route to host`. Measured 07-26 and again
   07-28, unchanged. A LanDB TN-trust request is the open item.
   ⚠ `ithdp1001.cern.ch:8020` (HDFS) passes from banco, but it is `10.116.5.218`
   — not a TN host at all, so it is **not** a readiness signal. Only the four
   `:19093` ports are. Kerberos is fine on both boxes and never the blocker.
3. **The transport had no leg for it.** banco's SPS data arrives
   lxplus (`~/p2_beam_monitor`) → EOS → `beam_bridge.py`, whose file list carries
   only `sps_spill_` and `sps_profile_`. And the lxplus copy is the banco fork,
   so `h4_tax_*.csv` was never produced on EOS in the first place.

⚠ banco's controller docstring says *"NXCALS is only reachable from the CERN
network, so this does NOT run on banco"*. That wording is imprecise and produced
a wrong diagnosis once — probe, don't trust it.

## Ordering trap when porting the Flask side

banco's SPS import block pulls `H4_BEAM_COUNTERS, H4_COUNT_VARS`. Pasting this
repo's block (`H4_TAX_VAR`, `H4_TAX_OPEN_MAX`, `H4_TAX_BLOCK_MIN`,
`tax_blocked_intervals`) without the controller symbols behind it raises
`ImportError` at module scope and takes **all** of banco's Flask down, not just
the panel. The proxy design sidesteps this entirely — it imports nothing new.

## Freshness budget (measured 07-28)

The TAX is not a fast signal and cannot be made one:

| term | value | reducible? |
|---|---|---|
| NXCALS ingestion lag | **14–23 s** | no — the data is not in the archive sooner |
| x17 SPS poll phase | 0–27 s (`POLL_S=12` × `SPS_POLL_EVERY=2`, + ~3 s poll) | yes, marginally |
| banco tile refresh | 0–5 s | tuned |

End to end ~36 s mean. Polling NXCALS faster than its own ~20 s ingestion lag
buys nothing, and the barrier takes ~6 min to move while accesses last 60–100
min — so the poll cadence was deliberately left alone.

The panel's two feeds are polled separately on banco (tiles 5 s, 100 kB trace
60 s) rather than both riding the old 15 s status loop: fresher where it matters
and ~4x less traffic.

⚠ `sps_monitor.POLL_S = 30.0` here is **dead** — its comment claims it "matches
the beam watcher's cadence (it drives us)", but the watcher is 12 s and the real
gate is `SPS_POLL_EVERY` in `beam_monitor/beam_intensity_controller.py`.

## If banco ever becomes TN-trusted

The proxy keeps working and is arguably still the right design (one NXCALS
consumer, one Spark JVM). To cut the dependency instead: port the TAX block of
`sps_spill_controller.py` into banco's controller additively, run
`sps_monitor/backfill_tax_nxcals.py` there for history, and point the two routes
at local data. Nothing else changes.
