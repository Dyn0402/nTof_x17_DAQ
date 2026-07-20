# N1081B board control — MANDATORY rules (read before touching a board)

The six N1081B logic modules (`192.168.10.240`–`.245`) run their websocket command
server on **libwebsock 1.0.7**, an abandoned 2014 library with a deadlock triggered by
dirty client disconnects and **no** server-side keepalive/timeout/dead-client reaping.
**Reproduced on hardware 2026-07-15:** a *handful* of dirty mid-command disconnects
(send a command, drop the socket without reading the reply / without a Close frame)
wedges a board in **seconds**; recovery then takes **hours of total isolation or a
physical reboot**. There is **no reliable remote reboot** (`apply_int_clk` does not
reboot; the GUI reboot needs the wedged websocket). **Prevention is the only defense.**
Full story: `n1081b/HANDOFF_2026-07-15_wedge_root_cause.md`, memory `n1081b-wedge-root-cause`.

## The rules (do not violate)

1. **ALWAYS go through `n1081b/n1081b_session.py`.** Never
   `from n1081b_sdk import N1081B` and open your own connection in a control/write
   script. The wrapper enforces: one bounded-timeout connection, a guaranteed clean
   close, an interprocess lock, a quarantine gate, pacing, and a circuit breaker.

   ```python
   from n1081b.n1081b_session import board_session, BoardBusyError, BoardWedgedError
   with board_session("192.168.10.242", purpose="what you're doing") as s:
       s.call("get_sections_function")
       s.call("set_section_function", N1081B.Section.SEC_A, N1081B.FunctionType.FN_AND)
   ```

2. **One process per board, ever.** The wrapper takes an exclusive `flock`; a second
   process gets `BoardBusyError`. **Do NOT bypass it.** Before starting board work,
   assume a daemon may hold it — `poll_modules` (inside `daq_control`) and
   `n1081b_scan_watcher` talk to boards on their own schedule.

3. **Never SIGKILL a process mid-session** (or `kill -9` a tmux pane running one). A
   killed connection is a DIRTY disconnect — the exact thing that wedges boards. Let
   scripts exit cleanly (SIGINT/SIGTERM, which the wrapper's `finally` handles).

4. **On `BoardWedgedError` / `BoardQuarantinedError`: STOP.** The board needs hours of
   zero contact to self-heal. Do **not** retry in a loop, do **not** "just check on it."
   The wrapper auto-writes a quarantine marker; respect it. Only `clear_quarantine(ip)`
   after the board is verified healthy (e.g. post-reboot).

5. **Never reproduce a wedge without physical access** to the crate that same
   session/day, and never on a **trigger** board (.240–.243, .245). `.244` is
   walls-monitoring only (safe-ish to reboot); the others are in the live trigger.

6. **Reads are cheap; dirty disconnects are the poison.** It is NOT a
   polling-frequency limit (the web GUI polls ~4 Hz for days). Pace config writes
   ~0.3–1 s apart and never churn reconnects (~15+/s → transient ConnectionRefused).

## Check board access state before launching an agent

Runtime state lives in `config/n1081b_access/` (gitignored):
`<ip>.holder.json` = who holds the lock now; `<ip>.quarantine.json` = board resting.
Quick check: `python -c "from n1081b.n1081b_session import quarantine_status; print(quarantine_status('192.168.10.244'))"`.

## Current board state (update when it changes)
- **Standing front-end config (post-FIFO; walls + plastic HV updated 2026-07-19):**
  M1 walls **+25/+35/+34/+36 mV** (Y88 half-MIP, `daq/calibrations/wal_trigger/
  thresholds_halfMIP_run224503.json`, adopted 2026-07-19; **was +15/+16/+15/+16**);
  M2 plastics **−65/−78/−86/−83 mV** (0.5-MIP Y88, `daq/calibrations/pss/
  mip_thresholds_y88.json` per-arm avg of the two bars, D=D_R only; adopted
  2026-07-19, **was −30/−30/−30/−38**) (**M2 D1 broken — wall D dead ≤ −24 mV, never
  shallower than ~−36**); M3 wall-leg (ch0) G&D delay **+20 ns** all sectors
  (scint leg 0, gates 20 ns). Plastic PMT HV = **Y88 equalized set** (see memory
  `plastic-hv-y88-equalization`; NOT the run224466 set). **Do NOT restore
  any pre-07-18 dump onto M1/M2/M3** — stale ~2×-shallow thresholds + delay=0.
  ⚠ The canonical dump `snapshots/dump_2026-07-18_postfifo_canonical.json`
  predates the 07-19 change — it still records walls +15/+16/+15/+16 (and old
  plastic HV); do NOT blindly restore it onto M1 or re-snapshot before updating.
  Details: `HANDOFF_2026-07-17_night_trigger_scans.md`, `RUN_MODES_2026-07.md` §top.
- `.244` (M5): **fully HEALTHY** (2026-07-17 midday: power-cycled at closeout, standard
  cabling, all four sections `counter` + verified counting, login 0.06 s). The
  **"per-section TT wedge" turned out not to exist** — TT sections silently emit zero
  tags whenever their input rate is above a live ceiling (~50 Hz streams, ~800 Hz
  silent, bracketed 2026-07-17) and stream fine below it; nothing is wedged and
  reboots are irrelevant to it. Walls (A) / liq (B) at current beam-off kHz rates
  cannot be TT-streamed. Full story + evidence table:
  `HANDOFF_2026-07-17_tt_rate_ceiling.md` (supersedes the per-section-wedge passages
  in TIMETAG_WATCHER.md, POST_REBOOT_244_CHECKLIST.md ROUND 2, and
  TIMETAG_MULTISECTION_2026-07-13.md §3). Probe TT health only with
  `n1081b/tt_probe_v2.py` (rate-aware); the single-tap `tt_section_probe.py` verdict
  is unreliable. Lessons that stand: a deep stage-3 wedge needs a physical reboot —
  there is no reliable remote reboot.
- `.240–.243, .245`: healthy, in the live trigger. Board access now goes through
  `board_session()` for the daemons (`poll_modules`, `scan_watcher`/`scan_control`)
  AND the migrated scripts — the interprocess lock spans them all. Watch the Trigger
  tab's **Board Access** card before launching a board-touching agent.
