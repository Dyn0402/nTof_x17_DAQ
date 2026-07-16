# Execution plan — N1081B communication-hygiene rollout (2026-07-16)

**Audience:** a Claude session (possibly less capable than the author) tasked with
finishing the N1081B wedge-prevention work. This document is self-contained. Read it fully
before doing anything, then execute the tasks **in order**. Each task has a goal, concrete
steps, and an acceptance check. **Do not skip the guardrails in §0 — violating them can
wedge a board for hours and there is no reliable remote reboot.**

---

## STATUS — updated 2026-07-16 (execution session)

- **Task 1 (.244 recovery): BLOCKED — needs physical reboot.** Probed `.244` once
  (bounded ws login) at 10:41: timed out after 8 s → it did NOT self-heal after ~11.5 h
  at deep stage-3. Per the "no reboot" constraint we stopped all contact. Re-quarantined
  it (until 2026-07-18 ~11:16, accurate reason) so it doesn't falsely read "free" when the
  old marker expired at 17:09. `.244` stays out of `POLL_IPS`. **Remaining human step:**
  physical touchscreen reboot, then `clear_quarantine` → restore counters → re-add to
  `POLL_IPS`.
- **Task 2 (daemons): DONE + deployed automatically.** `poll_modules.py` and the
  `n1081b_scan_watcher.py` primitives (also used in-process by `scan_control.py`) now go
  through `board_session()`. `n1081b_session.py` gained `auto_quarantine` (read-only
  telemetry won't 6 h-lock a live trigger board) + `login_ok`. Validated at the run_44→45
  idle boundary: full read-only poll of all 5 live boards, login=True incl old-fw .245,
  0 errors, clean close. **No restart needed** — each run is a fresh `python daq_control.py`
  process (start_run.sh) that exits at "donzo", so the next run auto-loads the new code.
- **Task 3 (scripts): actively-used ones DONE.** Migrated `dump_module_info.py`,
  `trigger_mode.py`, `systematic_threshold_scan_v3.py` (breaker-aborts the sweep; held-M4
  safety check replaces the subprocess `trigger_mode.py status` which would deadlock on the
  lock), `measure_chain.py` (zs_rate_scan), all 7 `setup_*.py`, and the two scripts a
  regression touched (see below). Reusable pattern = a `_Board` adapter (`__getattr__`
  forwards `d.method(args)`→`session.call(...)`; an explicit `.call` passthrough is added
  when the script also feeds its board to `trigger_mode`'s session-based helpers).
  **REGRESSION found + fixed:** migrating `trigger_mode.py` removed its `connect()`, which
  broke `veto_gate_test.py` and `verify_trigger_paths.py` (they do `d = tm.connect()`).
  Fixed by re-exposing a session-backed `trigger_mode.connect()` + `_Board` and repointing
  those scripts' `disconnect()`→`close()`. **Untested write paths:** trigger_mode `apply`,
  systematic v3, measure_chain, all setup_* — code-migrated but NOT hardware-exercised
  (live run); run each operator-observed the first time. ~21 raw-SDK files remain: one-off
  diagnostics, the SUPERSEDED `systematic_threshold_scan` v1/v2 (v3 replaces them), and the
  timetag pair (`mod5_timetag_logger`, `timetag_watcher_controller` — migrate on watcher
  revival). Migrate as touched.
- **Task 4 (Flask card): DONE + live.** `Board Access` card in the Trigger tab
  (`/n1081b/access`), + `clear_quarantine` button behind a confirm
  (`/n1081b/access/clear_quarantine`). Flask restarted (port 5001). Acceptance verified:
  free / IN USE (pid+purpose) / QUARANTINED (countdown) all render; clears on release.
- **Task 5 (CAEN email): DRAFT FINALIZED, NOT SENT** (per operator). Added a "before you
  send" checklist + corrected the stage-3 recovery figure to match the 07-16 11.5 h
  observation. **Remaining human step:** fill the signature block + send via MyCAEN, CC
  Andrea Abba.
- **Task 6 (SSH reboot): unchanged** — blocked on CAEN credentials (Task 5).

---

## 0. Guardrails — read first, these are hard rules

The six N1081B logic modules (`192.168.10.240`–`.245`) run their websocket command server
on **libwebsock 1.0.7**, an abandoned 2014 C library that **deadlocks when a client
disconnects dirtily** (drops the socket without reading the reply / without a Close frame)
and has **no** server-side keepalive, idle timeout, or dead-client reaping.

**Reproduced on hardware 2026-07-15:** ~5 dirty mid-command disconnects wedged a healthy
board in **seconds** (logins time out, then the websocket upgrade itself stops answering).
Recovery then needs **hours of total isolation or a physical reboot**. There is **NO
reliable remote reboot** (`apply_int_clk` does not reboot; the GUI reboot needs the wedged
websocket). **Prevention is the only defense.** Background: `n1081b/CLAUDE.md`,
`n1081b/HANDOFF_2026-07-15_wedge_root_cause.md`, memory `n1081b-wedge-root-cause`.

Rules while doing this work:
1. **Every board contact goes through `n1081b/n1081b_session.py`** (`board_session(ip)`).
   Never `from n1081b_sdk import N1081B` + open your own connection in a control script.
2. **One process per board.** The wrapper takes an exclusive `flock`; respect
   `BoardBusyError`, never bypass it. Daemons `poll_modules` (inside the `daq_control`
   tmux) and `n1081b_scan_watcher` (tmux `scan_watcher`) touch boards on their own.
3. **Never SIGKILL / `kill -9` a process mid-session** or kill a tmux pane running one — a
   killed connection is a dirty disconnect, the exact poison. Use SIGINT/SIGTERM.
4. **On `BoardWedgedError`/`BoardQuarantinedError`: STOP.** Do not retry, do not "just
   check." Let the board rest for hours.
5. **Trigger boards (.240–.243, .245) are in the LIVE trigger.** Only `.244` (walls
   monitoring) is safe to reboot/experiment on. Never reproduce a wedge on a trigger board.
6. Test new code against a **healthy** board doing **reads only** first (`.243` is a good
   choice). Reads are cheap; only writes/dirty-disconnects are dangerous.

Check board access/quarantine state any time:
```bash
python -c "import sys;sys.path.insert(0,'.'); from n1081b.n1081b_session import quarantine_status as q; \
  [print(ip, q(ip)) for ip in ['192.168.10.%d'%n for n in range(240,246)]]"
```
Runtime state (gitignored): `config/n1081b_access/<ip>.{lock,holder.json,quarantine.json}`.

---

## 1. Recover `.244` (walls board) — do this at the crate, first

`.244` was deliberately stress-wedged on 2026-07-15 evening and quarantined until
~2026-07-16 17:09. It is **walls-monitoring only — zero trigger impact.** It may have
self-healed overnight; the reboot is the guaranteed-clean path either way.

Steps:
1. **Probe first (bounded, one shot).** With physical access available as backstop:
   ```bash
   python -c "from websocket import create_connection as c; import time; t=time.time(); \
     ws=c('ws://192.168.10.244:8080/',timeout=8); ws.settimeout(8); \
     ws.send('{\"command\":\"login\",\"callback\":\"login\",\"pwd\":\"password\"}'); \
     print('login',ws.recv()[:60],'in',round(time.time()-t,1),'s'); ws.close()"
   ```
   - If it answers cleanly → it self-healed; you may skip the reboot (but the reboot is
     still a good chance to see boot-time config). If it hangs (8 s timeout) → reboot.
2. **Reboot `.244` via the front-panel touchscreen** (Settings → reboot; or power-cycle
   the NIM unit). While there, note/photograph the Settings/Version pages for the runbook.
3. **Wait for it to come up** (ping, then port 80, then a websocket login answers).
4. **Clear the quarantine and restore counters:**
   ```bash
   python -c "import sys;sys.path.insert(0,'.'); from n1081b.n1081b_session import clear_quarantine; \
     print('cleared', clear_quarantine('192.168.10.244'))"
   .venv/bin/python n1081b/restore_244_counters.py   # SAFE board_session restore -> all 4 sections counter
   ```
   Expect `OK: 192.168.10.244 all four sections restored to 'counter'.`
   (NOTE 2026-07-16: the old `n1081b_timetag_watcher.py --restore` command in this step was
   stale — that file does not exist, and `timetag_watcher_controller.restore_counters()`
   uses a RAW connection, risky on a just-recovered board. Use `restore_244_counters.py`,
   which goes through `board_session`. **Full step-by-step: `n1081b/POST_REBOOT_244_CHECKLIST.md`.**)
5. **Verify counting** (beam on → deltas non-zero): read counters twice a few seconds
   apart via `board_session("192.168.10.244")` + `get_function_results` on each section,
   or a quick `poll_modules` after step 6.
6. **Re-add `.244` to polling:** in `n1081b/poll_modules.py`, `POLL_IPS` → put `244` back
   into the tuple `(240, 241, 242, 243, 244, 245)`.
7. **Answer the boot-config question for the runbook:** note whether the board booted into
   its last-saved config or a default. (An as-built config was saved on the board as
   `asbuilt_20260715` via `save_configuration_file`; `load_configuration_file` can restore
   it if a board ever boots to defaults.)

**Acceptance:** `.244` answers websocket login instantly, all four sections read
`counter`, counters increment under beam, quarantine cleared, `.244` back in `POLL_IPS`,
`n1081b/CLAUDE.md` "Current board state" line updated to reflect `.244` healthy.

---

## 2. Migrate the always-on daemons to `board_session()` — the real concurrency fix

**Why this matters most:** the interprocess lock only prevents collisions between
processes that *take the lock*. Today `poll_modules` and `n1081b_scan_watcher` still use
the raw SDK, so an ad-hoc agent using `board_session()` and a daemon can still hit one
board at once. Migrating these two closes that gap. **Do this with the DAQ observable
(watch the `daq_control` and `scan_watcher` tmux windows react); it touches live daemons.**

### 2a. `n1081b/poll_modules.py`
- Runs inside the `daq_control` tmux, writing per-subrun `n1081b_config.json` snapshots.
  It already coordinates with the scan watcher via a `.pause_run` mechanism — **preserve
  that behavior.**
- Replace each `N1081B(ip)` + `connect`/`login` + calls + `disconnect` block with a
  `with board_session(ip, purpose="per-subrun snapshot") as s:` context, calling
  `s.call(...)` for every command.
- **Handle the new exceptions gracefully — a snapshot must never crash or hang the run:**
  wrap each board in try/except for `BoardBusyError` (another process has it — skip this
  board this cycle, log it), `BoardQuarantinedError` (skip — board resting), and
  `BoardWedgedError` (skip + log; do NOT retry). Snapshots are best-effort telemetry;
  fault-isolate per board.
- Keep `.244` out of `POLL_IPS` until Task 1 is done.

### 2b. `n1081b/n1081b_scan_watcher.py`
- This is the **trigger scan watcher** (tmux `scan_watcher`) — higher stakes. Understand
  its snapshot/restore + `.pause_run` flow before editing.
- Same migration: route all board access through `board_session()`; handle `BoardBusyError`
  by deferring (do not force); on `BoardWedgedError` stop touching that board and alert
  rather than looping.
- **Verify carefully:** its snapshot-restore-verify cycle must still pass. Test on a
  non-beam window if possible.

**Acceptance:** both daemons run through `board_session()`; a manual `board_session()`
from a shell while a daemon is active yields `BoardBusyError` on the same board (proving
the lock now spans daemon + ad-hoc use); the scan watcher's restore-verify still passes;
no board gets wedged during a soak of at least a few normal sub-runs.

---

## 3. Migrate the setup/scan/measurement scripts

~36 scripts import the SDK directly. Migrate the actively-used ones to `board_session()`;
lower-priority/one-off scripts can be migrated as touched. Priority order:
`systematic_threshold_scan_v3.py`, `trigger_mode.py`, `measure_chain.py` (in
`~/beam_july/test/zs_rate_scan`), `setup_*` scripts, `dump_module_info.py`,
`mod5_timetag_logger.py`, `timetag_watcher_controller.py` (on any watcher revival).

For each: replace raw-SDK connect blocks with `board_session()`, use `s.call()`, add a
circuit-breaker-aware abort (on `BoardWedgedError`, stop the whole scan — do not continue
to the next point). `systematic_threshold_scan_v3.py` especially should abort the sweep on
the first `BoardWedgedError` and leave the board at a sane config.

**Acceptance:** `grep -rl 'from n1081b_sdk import' --include=*.py . | grep -v .venv` shrinks
to only intentional low-level tools; the migrated scans run a short sweep without wedging.

---

## 4. Flask GUI — "N1081B board access" visibility card (human-facing collision guard)

**Goal:** let the operator glance at the dashboard and see, *before* launching an agent,
whether a board is in use or resting — the human half of "don't accidentally run two
agents at once."

- Add a status helper (in `flask_app/daq_status.py`, alongside the existing watcher-status
  cards) that reads `config/n1081b_access/` for each board `.240`–`.245` and reports:
  **IN USE** (from `<ip>.holder.json`: pid, purpose, since) / **QUARANTINED** (from
  `<ip>.quarantine.json`: reason, minutes left) / **free**.
- Add a route + a compact card in the dashboard (keep it uncluttered per the project's GUI
  conventions — small card, one row per board, color by state). Reuse the existing
  watcher-card pattern.
- Requires a Flask restart (tmux `flask_server`) to go live.

**Acceptance:** the card shows all six boards; holding a board from a shell
(`board_session(...)`) makes its row flip to IN USE with the right pid/purpose; a
quarantined board shows the countdown; card clears when released.

**Optional stretch:** a "clear quarantine" button (calls `clear_quarantine(ip)`) guarded
behind a confirm — only usable after the operator has verified the board healthy.

---

## 5. Send the CAEN support email

Draft ready at `n1081b/CAEN_email_draft_2026-07-15.md`. Fill in the operator name /
institute / serial numbers / MyCAEN account, send via the MyCAEN support portal, and CC
**Andrea Abba <abba@nuclearinstruments.eu>** (the N1081B designer at Nuclear Instruments).
It asks about: max concurrent sessions, server-side idle-session reaping, the role of the
GUI `alive` keepalive, newer firmware (ours is 2025.3.27.0), and SSH service credentials
for an orderly remote reboot. **This is the only path to a real fix** (the board firmware
is closed-source; our mitigations are all client-side).

---

## 6. (Optional / only with CAEN's help) SSH-based remote reboot

Port 22 is open on the boards but the service credentials are unknown (the web password
`"password"` is rejected). **Do not brute-force.** If CAEN/NI provide credentials
(requested in Task 5), then `ssh <user>@192.168.10.<n>` + `reboot` becomes a clean remote
recovery path, and reading `/var/log` + the daemon binary would let us confirm the
libwebsock diagnosis first-hand. Until then, physical touchscreen reboot is the only
guaranteed recovery.

---

## Reference — what already exists (built 2026-07-15, do not redo)
- **`n1081b/n1081b_session.py`** — the mandatory gateway: bounded connect, `flock`
  interprocess lock (`BoardBusyError`), quarantine gate (`BoardQuarantinedError`,
  auto-set on breaker trip), holder registry, pacing, rested retry, `BoardWedgedError`
  breaker. API: `board_session(ip, purpose=...)`, `quarantine_status(ip)`,
  `set_quarantine(ip, reason, window_s)`, `clear_quarantine(ip)`. Verified live on `.243`.
- **`n1081b/CLAUDE.md`** + root **`CLAUDE.md`** — the hard rules (auto-loaded for future
  sessions).
- **`n1081b/HANDOFF_2026-07-15_wedge_root_cause.md`** — root cause, research, stress-test
  results, tomorrow's plan.
- **`n1081b/CAEN_email_draft_2026-07-15.md`** — vendor email.
- **`n1081b/docs/SDK-n1081b_websocket_api.pdf`** — official websocket protocol reference.
- Memory: `n1081b-wedge-root-cause`, `n1081b-m3-stress-signal`, `n1081b-timetag-watcher`,
  `n1081b-sdk-gotchas`.
