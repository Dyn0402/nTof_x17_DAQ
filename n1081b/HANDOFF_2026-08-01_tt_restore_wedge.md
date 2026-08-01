# HANDOFF 2026-08-01 — .244 wedged on the segment-boundary *restore*, not on streaming

> **STATUS: board genuinely wedged (18:10:27). Supervisor stopped itself cleanly at
> 18:20:51 and nothing has touched `.244` since. No remote recovery attempted — user
> is power-cycling the crate on Monday 2026-08-03. The `rule_n1081b_tt_stream_dead`
> Telegram alert is DISABLED until then (see §6 — it must be re-enabled after recovery).**

## 1. Summary

Segment 11 streamed its full 6 h and closed cleanly. The **restore-to-counters call that
follows it** then failed with `ConnectionRefusedError(111)` on the websocket upgrade, which
`n1081b_session` classifies as `BoardWedgedError` → 6 h quarantine on `.244`
(18:10:27 → 2026-08-02 00:10:27). The supervisor rested 10 min, made its one allowed
retry, was blocked by its own quarantine marker, counted that as harness alarm #2, and
applied "two consecutive harness alarms ⇒ Telegram + stop".

That stop is **correct behaviour**, not a second failure. It is the same terminal path as
the 07-30 false wedge (`HANDOFF_2026-07-30_tt_reboot_race.md`), but the cause is the
opposite: on 07-30 the board was fine and the network wasn't up yet; here the network is
fine and the board's command interface is genuinely refusing connections.

## 2. The data through 18:10 is good

Streaming was never the problem — only teardown was:

```json
// sup_secC_0801_1209_seg11/stats.json
"finished": "clean",  "restored": false,  "edges_total": 4999831,
"max_packet_gap_s": 1.1,  "alarm": null
```

Full 21600 s, 4,999,831 edges, zero gaps > 2 s. Nine segments OK on the day, 41,312,197
edges total. `"post_rates_all_hz": null` here is a real symptom (unlike the 07-30 case,
where a SIGTERM legitimately skips the post-baseline) because `"finished": "clean"` means
the stream ended normally and the post-baseline *should* have been taken.

This is consistent with `HANDOFF_2026-07-15_wedge_root_cause.md`: these boards wedge on
command churn and dirty disconnects, not on stream load. The continuous-single-section
design is doing its job; the residual risk lives entirely in the per-segment start/stop.

## 3. ⚠ There was a 6-hour early warning: `restored=False` on a *clean* segment

In `logs/n1081b_timetag_watcher.log` this prints as:

```
RESTORE_UNVERIFIED | tt_super | seg=N | note=.244 left in wire/time_tag; next segment ensure_counter repairs
```

Timeline:

| time | seg | finished | restored | |
|---|---|---|---|---|
| 12:09:28 | 10 | clean | **false** | first of the run — the restore step already failing |
| 18:10:27 | 11 | clean | **false** | same step, now a hard `ConnectionRefused` |
| 18:10:51 | 12 | — | — | blocked by quarantine, harness alarm #1 |
| 18:20:51 | 13 | — | — | blocked again, alarm #2 → **STOP** |

So the failure announced itself **six hours ahead**, and today that only produces a log
`WARNING` — no alert, no state change. Worth wiring up.

**Do not over-read a single occurrence, though:** a lone `restored=False` on 2026-07-30
17:54 self-repaired on the very next segment (the `ensure_counter` path at segment start
does genuinely fix the common case). **Two consecutive** is the shape that preceded this
wedge; one is routine.

## 4. Evidence it is the board, not the link or the host NIC

Checked passively — no SDK session was opened, per `CLAUDE.md`:

- `ping 192.168.10.244` → 0% loss, ~0.2 ms.
- `ip neigh show 192.168.10.244` → resolved `lladdr 00:12:5e:00:1a:66`, **not**
  `<incomplete>`. That rules out the ARP/link-death signature of `m3-242-link-dead`
  (.242, 07-28→07-30).
- All five sibling boards (.240–.243, .245) answer.
- No host reboot preceded it — the supervisor had been up and streaming since 07-30, so
  the 07-30 cold-boot race is not an available explanation.

Network stack up + control plane refusing = the classic wedge. Expect it to need the
physical power cycle; there is no reliable remote reboot.

## 5. Current state — leave it alone

- **No process is talking to `.244`.** `tt_stream_supervisor.py` is not running; there is
  no `config/tt_stream_supervisor.stop` file (it stopped on its own alarm, not on request).
- `config/n1081b_access/192_168_10_244.quarantine.json` expires **2026-08-02 00:10:27** —
  i.e. it will NOT still be protecting the board on Monday.
- **The board is left in wire/time_tag mode, not counter mode.** M5 section-C counter
  telemetry reads nothing until it is reset; the power cycle clears this too.
- ⚠ `start_servers.sh` auto-starts the supervisor. **A host reboot, or any re-run of that
  script, will re-attempt `.244` once the quarantine lapses.** If the board must stay
  untouched before Monday, use the Soft Shutdown button or
  `touch config/tt_stream_supervisor.stop`.

## 6. Monday 2026-08-03 — after the power cycle

Follow `POST_REBOOT_244_CHECKLIST.md` (ROUND 1 is the runbook: bounded probe → clear
quarantine → restore four sections to COUNTER → verify counting → re-add to polling).
Two additions specific to this incident:

1. **Re-enable the alert.** It was disabled 08-01 via the running monitor's endpoint:
   ```bash
   curl -s -X POST http://127.0.0.1:5001/monitor/rule_toggle \
     -H 'Content-Type: application/json' \
     -d '{"name":"rule_n1081b_tt_stream_dead","enabled":true}'
   ```
   Do it through the endpoint or the Setup panel, **not** by editing
   `config/monitor_config.json` — the monitor holds its config in memory inside the Flask
   process and rewrites the file on the next `save_config()`, so a hand edit is silently
   lost. (The file is gitignored runtime state, which is why this change is not in the
   commit that added this document.)
2. **Consider alerting on the early warning in §3** — two consecutive `restored=False` on
   otherwise-clean segments. That would have given 6 h of notice here, in time to stop the
   chain gracefully before the boundary that actually wedged the board.
