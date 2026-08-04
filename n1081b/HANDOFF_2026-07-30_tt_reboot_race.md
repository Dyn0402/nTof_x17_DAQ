# HANDOFF 2026-07-30 — a host reboot fakes a .244 wedge and stops the TT logger for good

> **STATUS: incident diagnosed and recovered (07-30 11:54). The board was never wedged and
> the trigger was never affected. The underlying race IS NOW FIXED (07-30 13:00) — see §5,
> which also corrects the fix originally proposed there: the `network-online.target`
> drop-in it recommended was already in place and did not help.**

## 1. Summary

A host reboot at **11:37:52** brought `tt_stream_supervisor.py` back up ~14 s later, before
the DREAM-subnet link was ready. Its first connect to `.244` got
`OSError(113, 'No route to host')`, which `n1081b_session` classifies as
`BoardWedgedError` → it wrote a **6 h quarantine** on `.244`. The supervisor then rested
10 min, made its one allowed retry, was blocked **by its own quarantine marker**, counted
that as harness alarm #2, and applied "two consecutive harness alarms ⇒ Telegram + stop".

Net effect: **the trigger-timestamp stream stops permanently and cannot self-recover**,
plus `.244` counter telemetry stops too (see §4). Total logging gap this time: **~17 min**,
only because it was noticed immediately. Unnoticed, it would have been 6 h+.

**This recurs on every reboot.** Same root cause as the 07-22 boot `kinit` failure:
`daq-startup.service` is `After=network.target`, which is reached before any IP exists
(`PLAN_2026-07-16_n1081b_hygiene_rollout.md` and memory `reboot-recovery-hardening`
§Still open both flag this as known-and-unapplied).

## 2. The evidence that it was NOT a wedge

Decisive, and worth internalizing because it generalizes — **check the previous segment's
`stats.json`**:

```json
// sup_secC_0730_0622_seg4/stats.json
"finished": "signal",  "restored": true,  "edges_total": 4358489,
"max_packet_gap_s": 1.2,  "gaps": [],  "alarm": null
```

- `"finished": "signal"` — the stream was ended by **SIGTERM** (the reboot), not by a
  socket error. A real wedge shows a socket error *mid-stream*.
- `"restored": true` — the restore-to-counter call **succeeded**, so the board was alive
  and answering commands at 11:37:24. This is a **clean** disconnect, i.e. the exact
  opposite of the dirty-disconnect pattern that actually wedges these boards
  (`HANDOFF_2026-07-15_wedge_root_cause.md`).
- `"post_rates_all_hz": null` on such a segment is **normal, not a symptom**:
  `tt_stream_qualify` skips the post-baseline when `_stop["flag"]` is set. Do not read it
  as "the restore failed". The supervisor's own
  `WARNING: segment did not verify the restore to counters` is likewise misleading here.

Timeline, all from `logs/n1081b_tt_stream.log` + `uptime -s` + `last -x reboot`:

| time | event |
|---|---|
| 11:37:24 | reboot SIGTERMs the logger; stream closes cleanly after 18840 s, section C restored to counter |
| 11:37:52 | host boot |
| 11:38:06 | `daq-startup.service` → `start_servers.sh` → supervisor starts (**t+14 s**) |
| 11:38:11 | connect to .244 → `OSError(113)` → `BoardWedgedError` → **6 h quarantine written** |
| 11:48:11 | one retry, blocked by its own quarantine → harness alarm #2 → **STOPPED** |
| 11:50 | .244 pings 0.1 ms, `ip neigh` shows a real `lladdr` — board is fine |
| 11:54:38 | recovered, streaming again |

## 3. Recovery runbook (executed 07-30, worked first time)

`.244` is walls-monitoring only — zero trigger impact (`n1081b/CLAUDE.md` rule 6). The
sector ANDs are produced by M3's NIM logic, which is unaffected.

```bash
cd ~/PycharmProjects/nTof_x17_DAQ

# 1. Is it a link problem or a board problem? A real lladdr (not <incomplete>/FAILED)
#    plus a ping reply = the board is up and the link is fine.
ping -c3 -W2 192.168.10.244 && ip neigh show 192.168.10.244

# 2. Nothing may hold the board before you connect.
ps aux | grep -E "tt_stream|timetag" | grep -v grep   # expect nothing
ss -tnp | grep 192.168.10.244                          # expect nothing

# 3. Clear the false quarantine (justified: post-power-cycle / verified reachable).
.venv/bin/python -c "import sys;sys.path.insert(0,'.'); \
  from n1081b.n1081b_session import clear_quarantine; print(clear_quarantine('192.168.10.244'))"

# 4. ONE read-only board_session to prove health: all four sections 'counter',
#    and counters actually advancing.
.venv/bin/python n1081b/restore_244_counters.py    # idempotent; or read-only check below
```

Read-only health check used on 07-30 (`get_sections_function` + two
`get_function_results` snapshots 10 s apart). Expected beam-off cosmic aggregates —
compare against the last `stats.json` baseline, **scaled for beam state**:

| section | 07-30 06:22 beam-ON | 07-30 11:53 beam-OFF (cosmics) |
|---|---|---|
| A (walls) | 3470 Hz | 300 Hz |
| B (liq) | 1429 Hz | 108 Hz |
| C (sector ANDs) | 231 Hz | 28 Hz |
| D (singles) | 436 Hz | 81 Hz |

SEC_D's second channel reads ~0.7 Hz beam-off / ~2.8 Hz beam-on — that is **pre-existing**,
present in every segment baseline, not damage from the reboot.

Then relaunch. **The catch:** the crashed supervisor leaves its tmux session alive holding
an **idle bash** pane, so `start_tmux.sh` correctly refuses with "already exists". Verify
the pane really is idle and only then reap it:

```bash
tmux list-panes -t n1081b_timetag_watcher -F 'cmd=#{pane_current_command}'   # expect: bash
tmux kill-session -t n1081b_timetag_watcher
bash_scripts/start_tmux.sh n1081b_timetag_watcher \
    "$PWD/.venv/bin/python $PWD/n1081b/tt_stream_supervisor.py --section C" 5000
```

Killing an **idle** pane is safe — the never-`kill-session` rule is about killing
*mid-stream*, which is the dirty disconnect. Confirm recovery: the new segment's streamed
rate should match its own pre-stream counter baseline (07-30: 28 Hz streamed vs 26.9 Hz
baseline ⇒ no silent start).

## 4. Two consequences that are easy to miss

1. **M5 counter telemetry dies too.** `poll_modules` skips `.244` whenever the
   `n1081b_timetag_watcher` tmux **session** exists — it matches the session name, not
   whether the supervisor inside is alive. A self-stopped supervisor therefore leaves
   `.244` skipped in every per-sub-run `n1081b_config.json` *and* produces no `stats.json`
   baselines, so for the duration there is **no wall-rate record at all**.
2. **The crashed segment's `edges.csv` is left un-gzipped** — the supervisor gzips at
   segment end and never got there. 07-30: 142 MB → 23 MB by hand. Nothing gzips it
   retroactively, and nothing prunes it early. The data itself is intact (4,358,489 edges,
   0 gaps, max packet gap 1.2 s).

## 5. The fix — APPLIED 2026-07-30 13:00

### First, a correction to what §5 originally proposed

It named as the "cleanest root fix" `daq-startup.service` → `Wants=/After=network-online.target`.
**That drop-in already existed** (`/etc/systemd/system/daq-startup.service.d/10-network-online.conf`,
written 07-22 17:48 for the `kinit` failure) **and it did not prevent this.** Two
measurements from the boot journal say why, and they rule out the whole family of
"wait for the network" fixes:

| time (07-30) | event |
|---|---|
| 11:38:05.869 | `network-online.target` reached — `nm-online` satisfied by **eno1 (CERN)**; `enp4s0` was still `unmanaged → unavailable`, **no carrier** |
| 11:38:05.877 | `daq-startup.service` starts |
| 11:38:08.051 | `enp4s0` carrier up (10000 Mb/s) |
| 11:38:08.195 | `enp4s0` gets 192.168.10.8 |
| 11:38:11 | connect to `.244` fails with `OSError(113)` — **2.8 s AFTER the IP was up** |

So: `network-online.target` is satisfied by *whichever* interface comes up first, which
on this host is the wrong one. And **link-up is not sufficient either** — the IP existed
nearly 3 s before the failing connect, so what failed was **ARP**: the 10 GbE switch port
was not forwarding yet. A `ip link show enp4s0` pre-flight would have passed and the
connect would still have failed. Only **end-to-end reachability** is a valid gate.

### What was actually applied

- **(a) Reachability gate — `tt_stream_supervisor.py`.** New `_reachable_ok()`, called in
  the main loop next to `_disk_ok()`: **no board session is opened until `.244` answers
  ICMP.** At boot it waits patiently (`REACH_WAIT_S` 180 s, polling every 3 s); if the
  board stays unreachable it rests `UNREACH_REST_S` (10 min) and re-checks, with a
  Telegram that says *link problem, not a wedge*. Ping is deliberate: ICMP never touches
  libwebsock, so this is not the reconnect churn rule 7 forbids. Shaped as a gate rather
  than an alarm so a link failure (cf. `.242`) can never burn alarm strikes or stop the
  chain permanently.
- **(b) Own-quarantine is not an independent alarm — `tt_stream_supervisor.py`.** New
  `_own_quarantine()` (a marker whose `since` postdates `self.t_start` is ours). On an
  alarm verdict the supervisor now waits the window out instead of spending its one retry
  inside it and stopping for good. Capped at 2 waits so a genuinely wedged board still
  ends in a human-facing alarm.
- **(c) Unreachable ≠ wedged — `n1081b_session.py`.** New `BoardUnreachableError` raised
  from `_connect()` when the errno is one of EHOSTUNREACH / ENETUNREACH / ENETDOWN /
  EHOSTDOWN / EADDRNOTAVAIL, and **no quarantine is written** for those. Rationale: if the
  SYN never reached the board, no libwebsock session can have leaked, so there is nothing
  to heal — quarantining is not merely useless, it blocks recovery.
  Three deliberate choices, since this touches the wedge classifier:
  - It is a **subclass of `BoardWedgedError`**, so every existing `except BoardWedgedError`
    handler keeps working unchanged and fails safe. Callers that care catch it first.
  - `ECONNREFUSED` is **excluded** — an RST means the host's stack answered, so the board
    is reachable (and per rule 7 reconnect churn produces transient ConnectionRefused).
  - Only the **first** connect is classified this way. If the link drops mid-session the
    normal wedge path still applies, because by then a real session existed on the board
    and losing it *is* the dirty disconnect.
- **(d) `poll_modules` liveness (was §4.1).** `_tt_watcher_running()` now requires the tmux
  session **and** a live `tt_stream_supervisor.py` process, so a self-stopped supervisor no
  longer silently drops `.244` from every snapshot. Fails safe: if the process check itself
  errors it assumes streaming and skips the board.

Verified: `OSError(113, 'No route to host')` against an unused DREAM address now raises
`BoardUnreachableError` and writes **no** quarantine file; `ECONNREFUSED` and
`socket.timeout` still classify as wedges; a marker predating the supervisor is not
treated as its own.

### Side effect: the same reboot fixed `.242`

Fix (c) was motivated partly by `.242`/M3 being mislabelled a wedge. Worth recording that
**this reboot silently cured it.** M3 had been ARP-`FAILED` since 07-28 15:30 and was
written off as needing physical attention; nobody went to the crate, and at 13:00 it pinged
at 0.135 ms. A read-only `poll_modules._dump_board` check at 13:15 returned **0 errors** and
**zero config differences** against `snapshots/run79_asbuilt_2026-07-27.json`, with
identical firmware and serial — so the board never lost state and never rebooted. The only
thing that changed was the host's 10 GbE NIC being re-initialised
(`atlantic … link change old 0 new 10000`).

**The generalisable lesson:** ARP `FAILED` correctly rules out a libwebsock wedge, but it
does **not** localise the fault to the far end. Before concluding "someone has to go to the
crate", try bouncing the host's DREAM interface. See `n1081b/CLAUDE.md` current board state
for M3's verified condition.

### Still open

- `daq-startup.service` still uses the deprecated `KillMode=none`, and systemd warns about
  it on every boot.
- Nothing gzips a crashed segment's `edges.csv` retroactively (§4.2 is unchanged).
- M3 was dropped from nothing programmatically (the scan schedule simply never targeted it),
  but if you want its per-sub-run snapshot rows back there is nothing to re-enable — it is
  in `POLL_IPS` and will be read again on the next sub-run.

## 6. Cross-references

- Standing config + operating notes: `n1081b/TIMETAG_WATCHER.md` §The standing configuration
- Why dirty disconnects are the poison: `HANDOFF_2026-07-15_wedge_root_cause.md`
- Post-*board*-reboot (physical) runbook: `POST_REBOOT_244_CHECKLIST.md` — a different
  situation from this one; see the note at its top
- What else a reboot leaves down: memory `reboot-recovery-hardening`
- Memory for this incident: `tt-supervisor-reboot-race-false-wedge`
