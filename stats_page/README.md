# Public statistics page

A read-only webpage showing what the DAQ is currently doing: current run, events
this run, trigger rate, sub-run progress and beam state.

**Live at https://dylan-neff.web.cern.ch/x17/**

**Status: prototype.** Triggers/events/beam are real and wired up end to end.
Tracks are a labelled placeholder — see [Tracks](#tracks) below.

## Shape

```
DAQ machine (CERN)                          webeos (dylan-neff)
┌──────────────────────────┐                ┌──────────────────────────┐
│ flask /status :5001      │   xrdcp every  │ /eos/user/d/dneff/www/x17│
│ config/beam_state.json   │ ─── 60 s ────► │   index.html  (the page) │
│ config/sps_state.json    │                │   data.json   (the data) │
│   ↑ stats_collector.py   │                └──────────────────────────┘
└──────────────────────────┘                  Apache serves it; the page
      outbound only                           polls data.json every 20 s
```

The DAQ machine only ever makes **outbound** requests. Nothing opens a port, no
inbound firewall exception is needed, and nothing on the internet can reach back
into the DAQ network. If the DAQ box goes down the page still loads and says how
old its numbers are — which is the information you want at 3 a.m.

Deliberately **not** a tunnel onto the existing Flask app: that app can start and
stop runs, drive HV and fire the e-stop, and none of that belongs on a public
webpage. This publishes a flat JSON summary and nothing else.

The page lives in a `x17/` subdirectory, so the existing landing page and
`trigger_scheme.html` at the site root are untouched.

## Files

| File | What it is |
|---|---|
| `stats_collector.py` | Builds the payload and publishes it. `--dry-run`, `--once`, `--html` |
| `page.html` | The page. Static; polls `data.json`. Uploaded as `index.html` |
| `../stats_page_watcher.py` | tmux entry point, matching the other watchers |
| `../config/stats_page_config.example.json` | Copy to `stats_page_config.json` |

Two files are published, not one: `data.json` (live status, every 60 s) and
`runs.json` (the run-history timeline, only when it changes) — see below.

## Tabs

Two views, because live status and the run record are different questions asked at
different cadences:

| Tab | What is on it |
|---|---|
| **Live** | the hero, the tiles, the projection plot, IPC yield, services, field dump |
| **Runs** | *Recent activity* (the sub-run timeline) and *All runs* (the run list) |

The selected tab is in the hash (`#live` / `#runs`), so a view is linkable and
survives a reload. The staleness banner sits above both — it applies to everything.

## Run history

The **Runs** tab holds two things, from one `runs.json`.

**Recent activity** — every sub-run of the last week on a **true wall-clock axis**:
one column per sub-run, width = how long it actually ran, height = events banked,
blue for beam and orange for cosmics, with measured beam-off periods shaded behind.
Hovering a column names its run.

**All runs** — every run in the ledger, one row each, sortable on any column and
filterable by mode or run name. Totals follow the filter. The ledger begins at
run_67; earlier runs were deleted before it existed, so there is nothing older to
list.

Built by `projections/live.py: run_history()` off the ledger. Note the two spans in
one block: `runs` is **every** run (the list), `subs` is only the last
`run_history_days` (the timeline) — a wall-clock timeline over the whole ledger
would be unreadable, and the per-sub-run rows are the bulky part of the payload.

Two corrections it makes that matter, because the raw ledger will otherwise mislead
you:

- **`hours` is a nominal window, not elapsed time.** When `mode_watcher` changes
  over mid-sub-run it stops the run where it stands, but the sub-run keeps its full
  nominal `hours`. run_131 is logged as 0.25 h and actually lived 62 seconds — at
  face value its 285 events read as a catastrophic detector failure instead of a
  perfectly normal changeover. So every run also carries **`h_air`** (wall-clock to
  the next run's start) and every rate is computed on that; `trunc` marks the runs
  where the two disagree, shown as † in the table.
- **Consecutive runs overlap in the ledger** for the same reason, so each sub-run's
  drawn width is clipped to its run's real end. Without that the bars of a
  changeover pair are drawn on top of each other.

Beam-off bands come from `run_stats.load_actual_downtime()` — the same
logger-gap-guarded derivation as the stop-duration study, so a dead logger can't
read as downtime. Intervals shorter than `run_history_min_gap_min` (5 min) are
counted in the totals but not drawn, since at a week per screen they are hairlines.

**Non-physics runs are listed but not counted.** The saturating-pulser DAQ
characterisations (run_90 / 92 / 94) carry `physics: false`, appear in the list under
a Pulser chip, and are excluded from every total — a run list that silently skips
three run numbers is confusing, but folding 200 kHz pulser sub-runs into the
statistics would wreck them. This is why the list reads `runs.json` from
`run_stats.load_ledger()` directly rather than `load_stats()`, which filters them out.

Run mode is taken by **majority** of a run's sub-runs, not from the first one: a few
rows carry `beam_type: unknown` because their run_config could not be read at scan
time (9 today, in run_72 and run_76), and those default to `is_cosmic=False`. One
unreadable first sub-run would otherwise relabel a whole cosmics run as beam.

### Why it is a separate file

A week of sub-runs is ~15 kB and it only changes when a sub-run completes (~hourly),
while `data.json` is rewritten every 60 s. Folding it in would double `data.json`
and — since **EOS versions every overwrite** — bank ~1,440 versioned copies a day of
something that changed once an hour. So it rides in its own `runs.json`, pushed only
when its stamp changes, exactly like the plot. `data.json` carries only the stamp
(`run_history_stamp`); the page re-fetches `runs.json` when it sees that change, and
otherwise leaves the card alone.

Config: `run_history_enabled`, `run_history_days` (7), `run_history_min_gap_min` (5).
The whole block is wrapped — if it fails, the card hides itself and the rest of the
page is unaffected.

## Statistics and projection

The page also carries cumulative statistics and the frozen projection from
`../projections/` — beam triggers recorded, projected total, how far ahead or
behind the projection we are, a separate cosmics counter, and the cumulative plot
as `progress.png`.

### Grading against the best achieved

The trigger and beam tiles carry a "% of best" grade against
`projections/reference_maxima.json`, regenerated by
`projections/reference_maxima.py`:

| | best achieved | basis |
|---|---|---|
| trigger rate | **32.4 Hz** = 2.8M/day | p99 of 299 beam sub-runs (peak 33.1 Hz, run_79/stat090_0010) |
| beam delivery | **1.86×10¹⁷ p/day** | p99 of 3,050 ten-minute buckets over 27 days (peak 1.88×10¹⁷) |

Both are **p99, not the outright maximum**. One lucky sub-run or one exceptional
bucket is not a standard worth grading against, and a ceiling that ratchets on noise
makes every later measurement look worse for nothing. Bands: ≥85% good, 60–85% fair,
below that low — with the percentage and "of best" in text, so colour never carries
the meaning alone.

⚠ **The trigger reference was set at Hwm 2** (run_79). Production has since moved to
Hwm 1, which deliberately gives ~16% fewer triggers in exchange for a far flatter
comb, so the current configuration cannot reach 100% by design. A reading around
60% typically decomposes as ~0.84 (Hwm 1) × the beam grade — check the beam tile
before reading a low trigger grade as a fault.

Re-run `reference_maxima.py` occasionally; the references only ratchet upward.

### What updates when

Three cadences, because the three things move at completely different speeds:

| | every | why |
|---|---|---|
| live status (run, rate, services) | **60 s** push | cheap — one HTTP GET plus two small JSON reads |
| beam-intensity trace | **300 s** | tail-reads the ~1.3 MB daily beam CSV |
| statistics + projection numbers | **600 s** | imports pandas, walks the run dirs, syncs the ledger (~2 s) |
| **cumulative plot** | **only when a sub-run completes** | a 200 kB matplotlib render; nothing about it changes until then |

The plot is gated on `(last_subrun_end, projection created)`, so an unchanged PNG is
never re-rendered and never re-uploaded — that matters because EOS versions every
overwrite. The browser reloads the image on the same stamp rather than on a timer.

The rest of that block is on its own cadence because it is much more expensive than
the live status (it imports pandas and walks every run directory) and much slower
moving (it only changes when a sub-run completes):

- recomputed every `projection_interval_s` (default 600 s), not every push;
- the **plot** is re-rendered and re-uploaded only when its inputs actually change
  — a newly completed sub-run or a newly frozen projection — so an unchanged
  135 kB PNG is not written to EOS six times an hour;
- the page reloads the image only when that same stamp changes;
- the whole block is wrapped: if pandas or matplotlib breaks, the projection
  section hides itself and the live status page carries on.

Set `projection_enabled: false` to turn it off.

Note the two event counts on the page have different scopes and are labelled that
way: **"Events this run"** is the current run only (from the DAQ GUI), while
**"Beam triggers recorded"** is the cumulative total since run_79, beam runs only.

## Running it

```bash
python stats_page/stats_collector.py --dry-run              # the payload, publish nothing
python stats_page/stats_collector.py --html /tmp/stats.html # render locally, publish nothing
python stats_page/stats_collector.py --once                 # one real publish
```

Continuously: **started at boot by `start_servers.sh`** alongside the other
watchers. To restart it by hand:

```bash
tmux kill-session -t stats_page_watcher
tmux new-session -d -s stats_page_watcher \
  'source .venv/bin/activate && python stats_page_watcher.py'
```

`page.html` is re-uploaded on the first push of each session, so editing the page
and restarting the watcher is the whole deploy loop.

Not in the Flask GUI — there is no start/stop button for it yet.

## Making the page public

The site is SSO-gated by default: an anonymous request gets a 302 to
`auth.cern.ch`, so today it is readable by anyone with a CERN (or eduGAIN) account
and nobody else.

To drop the login, enable **Guest Access** on the site in the CERN Web Services
Portal (the webEOS management screen for `dylan-neff`). The docs describe it as:
when enabled, "access to the site is allowed for unauthenticated users (`guest`
users), no SSO login screen will appear." It is off by default because the CERN
Security Team's default advice is to keep sites SSO-protected — enable it only
because this content genuinely doesn't need protecting, which is the case here
(no control path, no personal data; the worst case is someone learns the trigger
rate).

Note that Guest Access applies to the **whole site**, including the existing
landing page and `trigger_scheme.html`. If you'd rather only `x17/` be public,
enable **Use .htaccess files** in the same screen and put the authentication
requirement back on the directories you want to keep private.

## Tracks

Not wired up. Nothing counts tracks online today; it happens offline over
`combined_hits_root` per sub-run (the `build_track_cache.py` recipe in
`~/beam_july/analysis/detA_track_freq_run70/`, documented in
`docs/METHOD_track_rate_vs_hv_time_intensity.md`).

Wiring it up means a watcher that runs that pass per completed sub-run, behind the
processor, and caches a count. The payload already has a `tracks` slot for it:

```json
"tracks": {"count": 12345, "note": "run_80, through sub-run 3"}
```

Fill that in and the tile lights up on its own. Note it will always lag by one
sub-run — tracks aren't available until the sub-run is processed — so it should be
labelled with the sub-run it covers rather than presented as live.

## Known caveats

- **EOS writes are not atomic.** A reader can catch a half-written `data.json` and
  will retry on its next 20 s poll. The page treats a failed fetch as "cannot reach
  the stats host" rather than blanking the numbers.
- **Needs a valid Kerberos ticket** — the same keytab-seeded one the backup watcher
  uses. When the keytab lapses, pushes fail and the page goes stale with its banner
  up, rather than showing wrong numbers.
- **`data.json` is cache-busted** with a `_=<timestamp>` query param, because
  webeos is Apache serving a static file and would otherwise be free to cache it
  past the push interval.
- **`Int Rate` is the DREAM instantaneous rate** as the GUI reports it, not a
  trigger-scaler reading. It is the right number for "is data flowing", not for a
  rate measurement.
- **The publisher trusts `/status`.** If Flask is down the payload carries an
  `error` field and the page goes stale rather than showing wrong numbers.
- **EOS versions every overwrite** into `.sys.v#.data.json/`, one file per push.
  Whether that is capped could not be checked from here (no `eos` CLI on the DAQ
  machine — `xrdfs` can't read the `sys.versioning` attribute). If it is *not*
  capped, expect ~1,440 versioned copies/day; `data.json` plateaus around 14 kB
  once the 240-sample history fills, so roughly 20 MB/day against a CERNBox quota
  measured in TB — untidy rather than dangerous, but worth checking the version
  count after a day and setting `sys.versioning` low on that directory if it is
  climbing without bound.
