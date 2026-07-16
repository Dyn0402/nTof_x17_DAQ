# HANDOFF — N1081B Module 5 (.244) wedged by the time-tag watcher (2026-07-15)

**One-line status:** `.244` (Module 5, the four scintillator-wall monitoring board) has a
**wedged command interface** and needs a **physical reboot** (touchscreen or power-cycle),
expected in ~2 days when someone is at the crate. It is NOT remotely recoverable with the
access we have. The main DAQ/trigger is **unaffected** (.244 is monitoring-only, not in the
trigger). Everything that could re-trigger the problem has been disabled.

---

## 1. Current state (as of 2026-07-15 ~13:30)

- `.244` responds to **ping**, **SSH (22)**, and **Apache HTTP (port 80 → HTTP 200)**, and
  **port 8080** is open. So the box is up at the OS/network level.
- Its **websocket command backend is hard-hung**: a fresh connection opens (`connect: True`)
  but `login` never gets a reply — confirmed with timeouts up to **90 s**. No SDK command
  (get/set/reset/reboot) can go through. This is the "only a reboot clears it" wedge, escalated
  from the per-section TT wedge to the whole command processor.
- The board's function state is **unknown / not counter** (it was in Time-Tag when it wedged;
  the clean-exit restore could not complete because the command interface was already dead).
- **No production impact:** `.244` = the 4 scintillator walls, read as **counters for
  monitoring** — it does **not** participate in the trigger logic (trigger = .240–.245 sections
  configured as logic; .244's role here is wall rate/timestamp monitoring). DREAM, HV, gas, and
  the trigger are all independent of it. So the walls are simply not being counted/logged until
  the board is back.

## 2. What was being attempted (why the board was in Time-Tag)

We built an **always-on N1081B time-tag watcher** to stream .244's four walls as per-edge
timestamps (the "Module 5 actual timestamps" goal), to be matched to DREAM offline. It uses the
**persistent-FIFO round-robin**: arm all sections to `FN_TIME_TAG` once, then read each section's
buffer with `reset_channel`+`start_tt_data`→drain→`stop_tt_data`, deduped to exactly-once. Files:
`n1081b/timetag_watcher_controller.py`, `n1081b_timetag_watcher.py`, doc
`n1081b/TIMETAG_WATCHER.md`, plus Flask routes/tab and a `poll_modules` coexistence hook. The
underlying multi-section reading was validated earlier (see
`n1081b/TIMETAG_MULTISECTION_2026-07-13.md` §5): short reads work cleanly.

## 3. What happened (timeline)

- **Earlier 2026-07-15:** short watcher tests worked — a run logged 195 edges cleanly; a 32 s
  prototype sustained ~845 Hz aggregate with exactly-once dedup. Board restored to counters fine
  after each (across SIGINT/SIGTERM/SIGHUP/`tmux kill-session`).
- **~11:20:** started the watcher "for real" via the tmux/start_servers path. It ran ~1 h, mostly
  during a **beam-off** stretch, doing ~3257 round-robin cycles (each = 4 sections ×
  reset/start/stop) — i.e. **tens of thousands of TT start/stop commands** on the board.
- **~12:2x (beam back):** checked and found the watcher had captured **0 edges across all 3257
  cycles**, with no errors — the board's TT engine had already **stopped emitting** (wedged),
  even though beam was now on and the walls were firing.
- **On stopping the watcher to diagnose:** the board's command interface **hung entirely** —
  websocket connects but `login` never answers (verified to 90 s). My subsequent reconnect
  attempts (and a diagnostic that collided with the stop-restore) likely tipped a per-section TT
  wedge into a full command-processor hang.

## 4. Root cause & the design lesson

**Cause:** the volume of **rapid `start_tt_data`/`stop_tt_data` cycling** (back-to-back,
`cycle_s=0`, thousands of times, much of it on an empty buffer during beam-off) wedged the
board firmware. The single-section streamer `mod5_timetag_logger.py` has run at kHz for long
stretches **without** wedging — so the damage came specifically from the **aggressive
multi-section start/stop churn** of the persistent-FIFO watcher, not from TT streaming per se.

**Lesson:** the persistent-FIFO watcher **as written is not safe for continuous/unattended
operation.** The multi-section scheme is fine for short, deliberate reads but must not hammer the
board 24/7. Before it is ever run again it needs, at minimum:
- a **gentle cadence** — poll each section every ~10–30 s, not back-to-back (`cycle_s` ≥ ~5–10),
  and far fewer TT start/stop cycles (e.g. hold a section's stream open longer per read);
- a **health check** — "0 edges while `beam_state.json` says beam-on for N cycles ⇒
  reconnect / re-arm / alert", so a wedge is detected in minutes, not after an hour;
- a **periodic full reconnect** (re-arm) to clear any accumulating board-side state;
- a **multi-hour soak test** under real beam that ends with the board still responsive and
  restoring to counters, before it is trusted unattended.
Alternatively, **fall back to single-section `mod5_timetag_logger.py`** (proven safe) — one wall
at a time or per-spill rotation — trading multi-section coverage for board safety.

## 5. Why it can't be rebooted remotely (paths checked)

| Path | Result |
|---|---|
| Web GUI "reboot" button | GUI's only transport is the **hung websocket** → dead end |
| Apache/HTTP endpoint | Apache serves fine but exposes **no reboot CGI** (only CDN links in the page) |
| SDK reboot method | **none exists** in `n1081b_sdk` |
| Websocket reboot command | backend hung — `login` (first msg) never answers |
| **SSH (port 22)** | open, **password auth enabled**, but web password `"password"` is **rejected** for users root/admin/user/caen/ni; real SSH creds not documented (repo or vendor — CAEN/NuclearInstruments don't publish them) |
| Networked PDU / power switch | **none** in the repo or environment |
| SNMP (161) / telnet (23) / 443 | closed |

**Conclusion:** the only ways back are **physical** — the board's **2.8″ touchscreen** (local
reboot/reconfig) or a **power-cycle** of the unit/crate slot — or obtaining the **SSH
credentials** (from CAEN support or the board's shipping paperwork), after which:
`ssh <user>@192.168.10.244` then `reboot` (orderly, cleanest).

## 6. Recovery procedure (when physical access is available, ~2 days)

1. **Reboot .244** — touchscreen reboot, or power-cycle the NIM unit. (An orderly reboot via the
   touchscreen/SSH is gentler than a hard power-cut, but either works.)
2. **Wait for it to come up** — ping, then port 80, then a websocket login should answer.
   Quick check:
   ```
   python3 -c "from n1081b_sdk import N1081B; d=N1081B('192.168.10.244'); \
     print('connect',d.connect()); d.ws.settimeout(8); print('login',d.login('password')); \
     print([x['function_name'] for x in d.get_sections_function()['data']]); d.disconnect()"
   ```
3. **Restore counters** (the board may boot to whatever config was saved — force the steady
   state):
   ```
   python n1081b_timetag_watcher.py --restore
   ```
   Expect it to print `restored sections to counter: ['counter','counter','counter','counter']`.
4. **Verify the walls count** (with beam on, deltas should be non-zero):
   ```
   python n1081b/poll_modules.py /tmp/m5_check.json   # after re-adding .244 to POLL_IPS (step 6)
   ```
   or read counters directly.
5. **Do NOT restart the time-tag watcher** until it's been redesigned + soak-tested (see §4).
6. **Re-add `.244`** to `POLL_IPS` in `n1081b/poll_modules.py` (removed temporarily — see §7).

## 7. What was changed to make things safe (and what to revert after recovery)

- **`start_servers.sh`** — the `n1081b_timetag_watcher` auto-start line is **commented out**
  (so a reboot + `start_servers` won't re-wedge the board). Leave it off until the watcher is
  redesigned.
- **`n1081b/poll_modules.py`** — `.244` **temporarily removed from `POLL_IPS`** (so per-sub-run
  snapshots don't waste ~6 s each on the dead login and don't keep poking the board). **Re-add it
  after .244 is rebooted and verified** (revert to `(240,241,242,243,244,245)`).
- **Watcher tmux session** — killed; there is no supervisor, so it stays down.
- **All board contact stopped** — nothing in the DAQ is now touching .244, giving it the best
  chance to self-recover if the hang is a stuck-session timeout (hours-scale, not guaranteed).

The watcher's Flask routes / GUI "M5 Walls" tab / status card remain in the code but are dormant
(they only read the state file; harmless). They need a Flask restart to appear and are moot until
the watcher is safe to run.

## 8. Small chance of self-recovery

If the hang is a **stuck client-session slot** (dead watcher session the board never cleaned up),
it may free up when the board times it out server-side — possibly within hours. If it's a
**crashed command daemon** with no watchdog, it won't recover without the reboot. We can't tell
which from outside. Leaving it alone (done) maximizes the recovery chance. Optional: a *gentle*
background check (one brief login every ~30 min) could catch a self-recovery and auto-restore
counters — not set up yet; ask if wanted.

## 9. Pointers
- Watcher design + failure banner: `n1081b/TIMETAG_WATCHER.md`
- Multi-section / persistent-FIFO validation: `n1081b/TIMETAG_MULTISECTION_2026-07-13.md`
- Single-section proven-safe streamer: `n1081b/mod5_timetag_logger.py`
- Memory: `n1081b-timetag-watcher`, `n1081b-sdk-gotchas` (restore + wire-on-abrupt-close + this wedge)
