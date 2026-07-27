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

## Statistics and projection

The page also carries cumulative statistics and the frozen projection from
`../projections/` — beam triggers recorded, projected total, how far ahead or
behind the projection we are, a separate cosmics counter, and the cumulative plot
as `progress.png`.

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
