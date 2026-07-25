# HANDOFF 2026-07-24 — FEU 3 dropout during run_75 (51 empty sub-runs)

**Status:** RESOLVED (hardware). Data loss NOT yet recovered — 51 grid points still need re-taking.

## One-line summary

FEU 3 (`192.168.10.110`) fell off the network mid-run at ~20:15. `daq_control` kept
marking sub-runs complete anyway, so run_75 wrote **51 empty 304 MB sub-runs** before
self-terminating at 21:21:43. The board returned at 22:13:20 and verified fully healthy.

## Identification

| field | value |
|---|---|
| Logical name | FEU 3 |
| IP | `192.168.10.110` |
| `Feu_RunCtrl_Id` | 98 |
| MAC | `00:0a:35:02:36:6e` |
| Host NIC | `enp4s0` (10 GbE / DREAM card, host `192.168.10.8`) |

IP map source: `~/july_dream/zs_ipd_ssd/Tcm_Mx17_July.cfg`. The FEU IPs are
**non-contiguous** — `.44 .83 .110 .111 .118 .43 .81 .82` for FEUs 1–8. TCM is `.32`.
Do not assume `.101–.108` are FEUs; they are not, and probing them fakes a crate outage.

## Timeline (2026-07-24, local)

| time | event |
|---|---|
| 20:14:53 | last GOOD sub-run `acmeshOff_dr600_ri3_0008` — 5835 MB |
| ~20:15–20:19 | **FEU 3 dies.** The gap to the next sub-run is 4 min vs the normal ~72 s cadence — consistent with RunCtrl burning time on timeouts |
| 20:18:57 | first EMPTY sub-run `acmeshOff_dr600_ri4_0009` — 305 MB |
| 20:18:57 → 21:20:50 | 51 consecutive empty sub-runs written, run marked "complete" throughout |
| 21:21:43 | run_75 self-terminates: "Run complete, closing down subsystems / donzo" |
| ~22:10 | operator notices FEU 3 does not ping |
| 22:13:20 | **FEU 3 answers again**, correct MAC |
| 22:13:33 | full sweep: all 8 FEUs + TCM present |
| 22:14:00 | fresh pedestal set started |
| 22:16 | pedestal set completes clean, FEU 3 verified healthy |

**Beam was ON for the entire window** (~850e10/pulse). Roughly two hours of beam were
lost — ~1 h writing empties, ~1 h idle after the run self-terminated.

## Diagnosis

At fault time FEU 3's ARP entry was `<incomplete>` — it was not answering ARP at all,
i.e. dead at the link/board level, not a hung IP stack. All other 7 FEUs and the TCM
replied normally in ~0.25 ms, so this was **not** a switch, cable-plant or NIC problem.

### ⚠ Unresolved: it may have self-recovered

FEU 3 came back **~90 s after monitoring started**, which may be faster than a walk to
the crate. If power was not actually cut, **the board recovered on its own** — which
means the fault can recur silently and the crate should not be fully trusted. This was
not conclusively established at the time and should be nailed down if it happens again.

## Why this cost 51 sub-runs (the real bug)

This is the known `daq_control` failure mode, now seen twice:

- A dead FEU aborts RunCtrl configuration (`PedMemInit failed`).
- `daq_control` does **not** check that outcome. It marks the sub-run complete anyway.
- The scan grid therefore races through every remaining point at ~72 s each, writing
  305 MB / 16-fdf skeletons with no events.
- Because `.subrun_complete` is written, a later `resume=True` will **skip** these
  points — the gaps become permanent unless the markers and dirs are purged.

**305 MB is the empty signature. Real sub-runs here are ~6 GB (SSD) / ~9-10 GB (HDD).**

That 305 MB is not partial data — it is the 8 copied pedestal files
(`Mx17_pedestals_pedthr_*.fdf`, ~39.8 MB each) that are staged into
`raw_daq_data/` before beam data arrives. Zero `*datrun*.fdf` bytes ever appear.
**This is why any check must count datrun bytes, never directory size.**

run_75 final tally, counted on the HDD (the durable record — the SSD staging area is
pruned by space_watcher once a sub-run is backed up, so counting there under-reports
the good ones): **9 good sub-runs (0000-0008) of a 60-point grid → 51 lost.**

## Post-recovery verification (all PASSED)

Sweep script kept at `scratchpad/feu_check.sh` (ICMP + ARP for all 8 + TCM; refuses to
bless a partial crate). Result at 22:13:33: all 8 FEUs + TCM present, 8 distinct MACs.

Fresh pedestal set `pedestals_07-24-26_22-14-00`:

- All 8 `.fdf` present, byte-identical size 39,813,886 — including `_03`.
- **Zero zero-byte files** (the condition that deadlocks `processor_watcher`).
- 1033 events, 0 bad events, 32 samples/event on every FEU.
- HV settled to 199.75 V by 22:14:13; FDFs written 22:15:54–57, i.e. **100 s after
  settling** — no high-gain contamination.

### FEU 3 pedestal values vs the 17:49 reference set

Compared with `scratchpad/ped_compare.py` (parses `*_ped.prg` hex values):

```
 FEU      N  mean_new  mean_ref   dmean  sd_new  sd_ref
   3    512   16191.8   16198.8    -7.1  1243.6  1243.9
```

σ matches to four significant figures and the mean moved −7 ADC. **FEU 3's pedestal
distribution is statistically indistinguishable from before the fault.** The board is
genuinely healthy, not merely pingable.

### Confirmed by pedestal QA

`ped_qa/summary.json` for the new set vs the 17:49 reference:

```
FEU    ok  /ref  noisy  /ref  dead  /ref  cnsRMS   /ref  lvl
  3   447   447      1     1    64    64   10.03   9.86  warn   bad_dreams {7: disconnected}
```

**FEU 3 is channel-for-channel identical to the reference** — same ok/noisy/dead counts,
same single disconnected DREAM 7. Recovery is fully verified.

Overall: `warn`, 461/4096 bad (ref `warn`, 445/4096).

## Secondary finding — FEU 2 (NOT related to FEU 3)

**FEU 2 degraded:** noisy channels 166 → 186, and DREAM 6 newly flagged noisy
(`{4,7}` → `{4,6,7}`). This accounts for essentially the whole 445 → 461 rise in
total bad channels. All other FEUs are unchanged. Worth tracking across the next few
pedestal sets to see if it drifts further.

### ⚠ Methodological note — raw `ped.prg` statistics can mislead

An initial pass using mean/σ of the raw `*_ped.prg` values flagged FEU 7 (σ 3769→4570)
and FEU 4 (σ 721→41) as anomalies. **Both were artifacts.** The QA — which applies
common-noise subtraction and per-channel classification — rates both `good` and
unchanged (FEU 7: 475 ok / 2 noisy / 35 dead, identical to reference; FEU 4: 480 vs 476
ok). The raw statistic has no CNS and is skewed by dead channels sitting at 0.

Use raw `ped.prg` comparison only as a *fast same-board sanity check* (as for FEU 3
above, where σ reproducing to 4 s.f. is strong evidence). For cross-board or
"is this board degrading" judgements, use `ped_qa/summary.json`.

## Defences added 2026-07-24 (in response to this incident)

Four layers, deliberately redundant — the cause-side checks can miss a variant of
the failure, the consequence-side checks cannot.

### 1. `feu_health.py` — crate reachability (cause)

Reads the FEU map from the live RunCtrl cfg (never guesses), pings all 8 FEUs + TCM,
reports ARP state. Exit 0 only if the whole crate answers.

```
python3 feu_health.py            # human report, exit 1 if anything is missing
python3 feu_health.py --json     # for scripting
```

Distinguishes `<incomplete>` ARP (dead at link level → power cycle) from a cached MAC
with failing ping (IP-stack hang). Explicitly excludes the N1081B boards on .240-.245.

### 2. `subrun_health.py` — did bytes land (consequence)

Cause-agnostic. Classifies each sub-run `good` / `empty` / `pending`.

```
python3 subrun_health.py             # most recent run
python3 subrun_health.py run_75      # exit 1 if any sub-run recorded nothing
```

Two detectors: the fatal `PedMemInit` RunCtrl error (immediate, names the FEU) and
zero datrun bytes past a grace period (catches everything else).

> ⚠️ **Tuning lesson, found while testing against a live run.** RunCtrl logs
> *recoverable* failures constantly on a perfectly healthy run:
> ```
> DreamConfigCheck: DreamRead failed for feu_id=6 Dream=7 reg=10 ...
> FeuConfig: DreamSpiConfigCheck failed for feu_id=6 with -2; attempt to
>            reconfigure with slow DreamConfig
> ```
> It retries and succeeds. The first version of the regex matched these and flagged a
> good sub-run as failed. The pattern is now restricted to `PedMemInit` only, **and
> bytes-on-disk always win** — a sub-run with data is `good` no matter what the log
> says. Any future change here must be re-tested against a known-good RunCtrl log.

### 3. `daq_control.py` — fail closed (prevention)

* **Pre-flight:** the crate is swept before the sub-run loop; a missing FEU/TCM
  refuses to start the run, mirroring the existing N1081B trigger-control guard.
  Override with `skip_feu_preflight=True` in the run config. Fails *open* if the
  checker itself errors — a checker bug must never block a beam run.
* **Post-sub-run:** `.subrun_complete` is now written **only if the sub-run recorded
  datrun bytes**. An empty sub-run is left unmarked, so a resume re-takes it instead
  of skipping it forever. After `MAX_EMPTY_SUBRUNS` (2) consecutive empties the run
  aborts rather than burning the remaining grid.

This closes the root cause: on 07-24 this would have stopped run_75 after sub-run
0010 instead of 0059, losing 2 points rather than 51.

### 4. Telegram rules (alerting)

Both enabled in `config/monitor_config.json`, new "DREAM crate & data integrity"
section in the monitor Setup panel:

| rule | fires when | severity |
|---|---|---|
| `rule_feu_unreachable` | any FEU/TCM stops answering | `critical` if TCM down, 2+ FEUs down, or a run is live; else `alert` |
| `rule_subrun_no_data` | current run's sub-runs finish with zero datrun bytes | `critical` at 3+ empties, else `alert` |

Options under `rule_options`: `min_duration_seconds` (90 / 60), `grace_seconds` (180),
`critical_count` (3). Verified against the live crate and against synthetic failures.

## Outstanding actions

1. ~~**Re-take the 51 lost grid points.**~~ **IN PROGRESS** — run_75 was resumed at
   22:24 and restarted cleanly on sub-run 0009, the first lost point. Because the
   failed sub-runs never got an HDD directory, they never got a `.subrun_complete`
   marker either (only the 9 good ones did), so `resume=True` re-takes them naturally
   — no purge was needed. Verified at 22:28: 0009 recording normally, 1.5 GB and
   climbing. Grid due to finish ~11:10.
2. ~~**Fix the root cause.**~~ **DONE** — see "Defences added" above. `daq_control`
   now pre-flights the crate, refuses to mark an empty sub-run complete, and aborts
   after 2 consecutive empties.
3. ~~**Alerting.**~~ **DONE** — `rule_feu_unreachable` + `rule_subrun_no_data`.
4. **Determine whether FEU 3 was actually power-cycled** or self-recovered (see above).
   Still open, and it matters: a board that recovers on its own can drop again.
5. **Watch FEU 2.** Unrelated to this incident but flagged by the same pedestal QA
   comparison — noisy channels 166 → 186, DREAM 6 newly noisy. Track it over the next
   few pedestal sets.
6. ICMP is necessary but not sufficient on this subnet — UDP has been dead while ICMP
   was fine before (see the 07-22 switch swap). Trust a real configure + first sub-run;
   `feu_health.py` says so in its own output.

## Related

- `docs/` — `daq_control` silent-config-failure notes
- `n1081b/CLAUDE.md` — unrelated to this incident, but the N1081B boards share subnet
  `192.168.10.x` (`.240–.245`); do not sweep them casually
