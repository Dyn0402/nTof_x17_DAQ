# N1081B wedge — root cause investigation & recovery (2026-07-15 evening)

**One-line status:** Both wedged boards (M3 .242, M5 .244) were **recovered remotely tonight —
no power cycle needed**. Root cause identified with high confidence: the boards' websocket
command daemon is built on **libwebsock 1.0.7**, an abandoned 2014 C library with a documented
deadlock triggered by client disconnects. The wedge is avoidable with client-side session
hygiene, and self-heals with hours of total isolation.

---

## 1. Recovery performed tonight (state as of ~23:00)

| Board | Was | Now |
|---|---|---|
| M3 (.242) | write-wedged since evening; SEC_A left `counter`→GUI `or` | **healthy**; SEC_A restored to build-sheet `and` (lemo 0+1, no bypass), verified by read-back; writes answer in 0.05 s |
| M5 (.244) | full command-interface hang since ~12:30 (login dead at 90 s) | **healthy**; login instant; all 4 sections restored `time_tag`→`counter`, counters verified counting under beam (walls at expected kHz-scale rates) |

- `.244` **re-added to `POLL_IPS`** in `n1081b/poll_modules.py`.
- Time-tag watcher **stays disabled** (start_servers.sh line still commented) until redesigned.
- Healing times observed: **~2.5 h** of zero contact for M3's write-wedge; **≲9 h** for M5's
  full login hang. Both then accepted one paced restore session cleanly.

## 2. Root cause

### The evidence chain
1. **Server fingerprint:** the websocket handshake on port 8080 returns
   `Server: libwebsock/1.0.7` (checked on .243). Stack: Zynq embedded Linux, Apache 2.4.23
   (static GUI only, port 80), the libwebsock-based command daemon on 8080, SSH on 22.
2. **libwebsock is abandonware:** by Payden Sutherland, v1.0.7 released 2014-01-12, last
   commit 2014-07-14; the author died 2014-09-24 and the repo has since vanished from GitHub
   (mirrors survive, e.g. github.com/EvaisaDev/libwebsock). No CVEs because no one ever
   audited it. The Janus WebRTC gateway shipped it in 2014 and fled to libwebsockets in 2015.
3. **Our exact phenotype is a documented libwebsock bug.**
   [Issue #18](https://web.archive.org/web/20201022164416/https://github.com/payden/libwebsock/issues/18)
   (2014, open forever): *"every time a websocket client closes a connection … everything
   else works, but libwebsock won't handle new connections, and won't send/receive any more
   data from connections that were already being handled."* The author's reply: *"I haven't
   had time to work out all the pthreading issues … I'm also having issues with some memory
   corruption I have not been able to pin down."* The community workaround was literally a
   package called `libwebsock-nothreads` pinned to pre-thread v1.0.4.
4. **Code facts (from the 1.0.7 source):** one libevent I/O thread for all clients + a fresh
   pthread per message (no cap); client-teardown path has an unlocked thread-list walk and a
   possible double-`pthread_join` (deadlocks the whole event loop); one **global mutex around
   every malloc/free** in the daemon (poisoned forever if a thread dies holding it);
   **no server-side ping, no idle timeout, no SO_KEEPALIVE — dead clients are never reaped**;
   per-message heap leaks; unbounded output buffering toward stalled clients.
5. **Client side (vendor SDK):** `n1081b_sdk` is a blind send/recv wrapper — no keepalive, no
   timeout handling, no close on exception paths, and the TT start/stop helpers even desync
   (2 sends, 1 recv). The vendor's own examples tell users to open one connection per thread
   and reconfigure in infinite loops, and never close them cleanly. So abandoned half-open
   sessions are the *normal by-product* of using the vendor tooling as documented.

### The failure model
Every scripted connection that dies dirtily (timeout mid-command, exception before close,
SIGKILL) leaves a zombie client and exercises libwebsock's broken teardown path. Damage
accumulates monotonically under sustained automation: **(stage 1)** intermittent command
timeouts → **(stage 2)** writes fail while reads work → **(stage 3)** `login` never answers.
Ping/SSH/Apache/FPGA/touchscreen stay alive throughout — only the daemon is sick. Each
timeout makes clients abandon more connections, which deepens the wedge (positive feedback).

**Why it self-heals:** libwebsock has no reaper, so recovery is the *kernel* draining the
backlog — TCP retransmission timers (~15–30 min per stalled connection) eventually fire,
EOF/ERROR events drain serially, zombie states get cleaned one by one. Hours for a big
backlog, and **only if no new client contact re-arms the failure** — which is why "leave it
completely alone" works and "keep retrying" never does.

**Why the web GUI survived during M3's stage 2:** the GUI is the client profile the daemon
tolerates — six persistent sockets per tab, a 2 s `alive` keepalive, ~2 Hz polling, browsers
always complete the websocket Close handshake.

### Is the M5 "TT engine stopped emitting" the same bug?
Partly. The 0-edges wedge after tens of thousands of `start_tt_data`/`stop_tt_data` cycles is
plausibly daemon-state damage (leaked per-message allocations / stuck threads) rather than
FPGA failure — counters (FPGA logic) kept working. Whether .244 SEC_A's TT-stream path from
07-13 is still wedged is untested; a reboot while we have physical access clears the question.

## 3. What research found (and didn't)

- **Board firmware source is NOT public.** The vendor GitLab
  (gitlab.nuclearinstruments.eu/public-repo) contains only the Python SDK for the n1081
  family; GitHub likewise. (Other NI products do have public firmware — not this one.)
- **The websocket protocol is documented** in `SDK-n1081b.pdf` (in the SDK repo; local copy
  saved). It states *"N1081B API support multiple users simultaneously"* but documents **no
  session cap, no timeout, no keepalive, no reboot command**. GUI-only commands (`alive`,
  `check_auth`, `remote_click`, `screen_shot`, firmware-upload trio, `calibrate_delay_lines`)
  are undocumented.
- **Our failure mode has no public record anywhere** — no forum threads, no issues, no
  integration code that handles it. The only public N1081B automation (AMS-L0/PAN test beams)
  wraps board contact in retry-until-success loops with e-mail alerts — they clearly fought
  connect failures but never diagnosed deeper.
- **No public firmware downloads/changelog** — newer-than-2025.3.27.0 status unknowable
  without asking CAEN (MyCAEN portal) / Nuclear Instruments.
- Remote reboot possibilities: `apply_int_clk`/`apply_ext_clk` **reboot the unit** (per the
  GUI/manual) — a potential last-ditch remote reboot for a stage-1/2 board (needs a working
  write path; untested; unknown whether re-applying the current clock source is config-safe).
  The firmware-upload commands also end in a reboot. Neither is usable in stage 3.

## 4. Operating rules (the "avoid it" answer)

It is **not a polling-frequency problem per se** — the GUI polls at ~4 Hz continuously for
days without harm. It is a **session-hygiene** problem:

1. **One persistent connection** per board per script. Never reconnect-per-command.
2. **Always close cleanly** (`ws.close()` in a `finally:`) — the Close frame is what saves
   the daemon. A SIGKILLed or crashed script is a board-health event, not just a script bug.
3. **On timeout: back off, don't churn.** Close, rest ≥45 s, ONE gentle retry, then stop all
   contact. Never loop reconnect+retry into a slow board.
4. **Circuit breaker in every long-running script:** 2 consecutive timeouts ⇒ abort and stop
   all contact with that board (it needs hours of quiet; pushing deepens the wedge).
5. **Pace commands** (~0.3–1 s gaps for config writes; ≤ a few Hz sustained polling is fine
   on a persistent socket). Avoid connect/login churn (~15+ per second → ConnectionRefused).
6. **Recovery ladder:** total isolation 3–12 h → one paced probe → paced config restore with
   read-back verify → physical reboot only if that fails.

Implementation: **`n1081b/n1081b_session.py`** (new tonight) wraps all of this
(context manager, pacing, rested single retry, `BoardWedgedError` breaker). Smoke-tested
against .243. Call sites to migrate: `measure_chain.py`, `systematic_threshold_scan_v3.py`,
`poll_modules.py`, `trigger_mode.py`, `n1081b_scan_watcher.py`, timetag watcher (on redesign).

## 4b. Stress test on .244 — results (2026-07-15 evening, authorized)

Deliberate characterization on .244 (walls-only, physical access next day as backstop).
Three findings, in ascending importance:

1. **`apply_int_clk` does NOT reboot the board.** Sent it to .244 (already on internal
   clock); no reply, and the board never dropped ping/port-8080 over 68 s of monitoring.
   So the SDK clock command is **not** a usable remote-reboot lever (at least when the
   target clock == current clock). `apply_ext_clk` was NOT tested remotely — with no
   external clock present it risks booting the board into a no-clock state; do that only
   with hands-on access. **Conclusion: no reliable remote reboot exists via the SDK.**

2. **Rapid connect churn trips a benign, fast-healing throttle.** ~4 connects/s of
   login-then-drop produced `ConnectionRefused` almost immediately, and the board was
   fully healthy again ~10 s later. This is the board's embedded-webserver flood guard
   (also noted historically) — self-limiting, NOT the deep wedge. Earlier confusion
   between this and the real wedge is now resolved: they are different mechanisms.

3. **THE KEY NUMBER — a *handful* of dirty mid-command disconnects wedges a board in
   seconds.** At a deliberately gentle **1 event / 3 s** pace (well under the churn
   throttle), each event = connect + login + send a `get_function_results` + **close the
   socket without reading the reply and without a Close frame**. Starting from a verified-
   healthy board: by the **2nd** event, fresh logins were already timing out (6 s); within
   the first batch of 5, the board reached **stage 3 — the websocket UPGRADE handshake
   itself stopped answering** (TCP still accepts instantly; the libwebsock event loop is
   deadlocked, matching issue #18). Confirmed with a single bounded probe:
   `TCP connect OK (0.0s)` but `ws upgrade/login timed out (8.0s)`.

   Contrast with operational history: real scans of *clean, completed* commands ran ~2 h
   before M3 degraded. So **it is not volume or frequency — it is dirty disconnects**,
   and even a few are acutely toxic. This is the single most important operating fact and
   is why the mitigation centers on "always close cleanly / never SIGKILL", not "poll
   slower". .244 was left wedged (quarantined until ~07-16 17:09) for tomorrow's reboot.

## 4c. Enforcement built tonight (the "codify it" answer)

`n1081b/n1081b_session.py` upgraded from a hygiene wrapper into a **mandatory gateway**
that makes the failure modes structurally hard to hit. Verified against live .243:
- **Bounded connect** — wraps the websocket upgrade in a real timeout (the vendor
  `connect()` has none, so a wedged board otherwise hangs the caller forever).
- **Interprocess lock** (`flock` on `config/n1081b_access/<ip>.lock`) — a second process
  gets `BoardBusyError` naming the holder (pid + purpose + since). Self-clears on process
  exit even after SIGKILL (kernel releases flock). **This is the "two agents at once"
  guard.** Tested: child holds → parent rejected → parent reacquires after child exits.
- **Quarantine gate** (`config/n1081b_access/<ip>.quarantine.json`) — on a tripped
  breaker the board is marked resting (default 6 h); any later session refuses to connect
  with `BoardQuarantinedError` until it elapses (or `clear_quarantine(ip)` after verified
  healthy). **This is the "don't let the next dumb agent re-hammer a wedged board" guard.**
  Tested: quarantine blocks, `ignore_quarantine=True` overrides, clear works.
- **Holder registry** (`<ip>.holder.json`) — human/GUI-readable "who has the board now".
- Pacing + single rested retry + `BoardWedgedError` breaker (as before).

Rules written to **`n1081b/CLAUDE.md`** (+ root `CLAUDE.md` pointer) so every future
session — including less capable ones — gets them automatically.

## 5. Plan for tomorrow (physical access day, 2026-07-16)

Recovery no longer needs the power cycle — use the access for hardening instead.
Ordered; items 4–5 optional but valuable:

1. **Morning health check (remote, 2 min):** one paced probe per board
   (connect→login→`get_sections_function`→close). Confirm tonight's recovery held.
2. **Snapshot configs to the boards themselves (remote, before touching anything):**
   `save_configuration_file("asbuilt_2026-07-16")` on all six boards, so any reboot/default
   surprise is one `load_configuration_file` away. (Also take a fresh `dump_module_info.py`.)
3. **Orderly reboot of .244 via touchscreen** (walls-monitoring only, zero trigger impact):
   - clears any residual daemon heap damage + the 07-13 SEC_A TT-stream wedge;
   - answers "what config does a board boot into?" (default vs saved) — load/restore
     counters after and verify counting;
   - while at the crate, photograph/note the touchscreen Settings pages (reboot path,
     version info, anything about network services) for the runbook.
4. **Controlled wedge experiment on .244 (optional, time-boxed, only with someone at the
   crate):** reproduce stage 1 deliberately to calibrate the danger threshold — open
   connections and kill them WITHOUT a Close frame in batches (e.g. 10 dirty disconnects,
   then measure write latency from a separate clean session; repeat). Stop at first
   sustained degradation; recover by touchscreen reboot. Deliverable: "N dirty disconnects ⇒
   degradation" number that turns rule #2 from folklore into a spec. Do NOT run this on
   trigger boards.
5. **SSH:** don't guess passwords; ask CAEN/NI for service credentials (email below). If they
   provide them, `ssh root@board` + `reboot` becomes the clean remote-recovery path and we
   can read the daemon logs (`/var/log`, the websocket daemon binary) to confirm the
   libwebsock diagnosis first-hand.
6. **Adopt `n1081b_session.py`** in the call sites above; only then consider reviving the
   time-tag watcher (persistent-socket redesign + beam-on health check + multi-hour soak).

## 6. Vendor contact

Email draft: `n1081b/CAEN_email_draft_2026-07-15.md`. Send via the CAEN support portal
(MyCAEN ticket) and CC **Andrea Abba <abba@nuclearinstruments.eu>** (N1081B designer,
Nuclear Instruments — gave the 2021 CERN Electronics Pool talk on this module).

Firmware on our boards: software 2025.3.27.0, zynq 22.10.07.00, fpga 23.11.10.00
(.245 older: 2022.3.0.0). Serials: .242=49325, .243=49326 (others in snapshots).
