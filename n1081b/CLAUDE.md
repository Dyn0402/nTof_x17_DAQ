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

   ⚠ **The board's own WEB GUI is a second controller and the `flock` cannot see it.**
   Close the GUI tab before scripted board work; two writers on this old firmware is a
   wedge risk on boards that sit in the live trigger. The GUI is also **1-based on
   channels** where the SDK is 0-based (GUI "Output 1" = SDK lemo/ch `0`).

3. **TWO ENABLE LAYERS — a signal needs BOTH.** Learned the hard way on 2026-07-22
   (see `docs/HANDOFF_2026-07-22_m6_enable_layers.md`):

   | layer | read | write | who uses it |
   |---|---|---|---|
   | per-channel `status` | `get_{input,output}_channel_configuration` | `set_…` | `scan_watcher` (`mesh_b`, `input_status`/`output_status`), our M6 scripts |
   | function `lemo_enables` | `get_function_configuration` | `configure_or` / `configure_or_veto` / … | `trigger_mode.py`, **and the web GUI** |

   They are **separate registers, ANDed**. A channel with `status=True` but its lemo
   disabled passes nothing — and each side's readback looks perfectly healthy. **For a
   FANOUT section the SDK can only READ `lemo_enables`**: there is no `FN_FANOUT` in
   `N1081B.FunctionType` and no `configure_fanout`, so on M6.A/B/C that layer is
   **GUI-only**. An evening was lost with one side setting `status` and the other setting
   `lemo_enables`, both reporting success, while the hardware stayed dark.
   **Always check both layers before concluding anything about board state.**

4. **Never SIGKILL a process mid-session** (or `kill -9` a tmux pane running one). A
   killed connection is a DIRTY disconnect — the exact thing that wedges boards. Let
   scripts exit cleanly (SIGINT/SIGTERM, which the wrapper's `finally` handles).

5. **On `BoardWedgedError` / `BoardQuarantinedError`: STOP.** The board needs hours of
   zero contact to self-heal. Do **not** retry in a loop, do **not** "just check on it."
   The wrapper auto-writes a quarantine marker; respect it. Only `clear_quarantine(ip)`
   after the board is verified healthy (e.g. post-reboot).

6. **Never reproduce a wedge without physical access** to the crate that same
   session/day, and never on a **trigger** board (.240–.243, .245). `.244` is
   walls-monitoring only (safe-ish to reboot); the others are in the live trigger.

7. **Reads are cheap; dirty disconnects are the poison.** It is NOT a
   polling-frequency limit (the web GUI polls ~4 Hz for days). Pace config writes
   ~0.3–1 s apart and never churn reconnects (~15+/s → transient ConnectionRefused).

## Check board access state before launching an agent

Runtime state lives in `config/n1081b_access/` (gitignored):
`<ip>.holder.json` = who holds the lock now; `<ip>.quarantine.json` = board resting.
Quick check: `python -c "from n1081b.n1081b_session import quarantine_status; print(quarantine_status('192.168.10.244'))"`.

## Current board state (update when it changes)
- **M6 (.245) SEC_B/SEC_C enable ALIASING + 07-23 mesh re-cabling.** On SEC_C's Out 4, the
  NIM/TTL type and the invert follow **Section C** (correct) but the **enable bit follows
  Section B's Out 4** — confirmed after a hard power cycle. SEC_B's status switches still
  drive SEC_B's own outputs, so the enable is **GANGED, not displaced**: one bit gates
  **both `B Out N` and `C Out N`**. Index-preserving ⇒ B Out 1/2 gate the two *cabled* SiPM
  enables on C Out 1/2. **⚠ B Out 1/2 must therefore be left ENABLED even though they now
  carry only a scope — disabling them kills the SiPM walls (61× collapse).** The alias is
  **one-way B → C** (C Out 1 status OFF does not affect B Out 1), and **SEC_C's own output
  `status` is a DEAD REGISTER** — a C leg is gated *solely* by the same-numbered B status
  bit. So SEC_B's mesh legs are safe from anything done on SEC_C, and **any script writing
  SEC_C output `status` is a no-op** (`set_m6_secC_sipm_enable.py --outputs-off` included —
  audit before reuse). Applies to the `status` layer only; the separate `lemo_enables` layer
  (rule 3) is untested here.
  The mesh was re-cabled 2026-07-23 to
  **SEC_B Out 3 = det A, Out 4 = det C** (SDK `out2`/`out3`); **dets B/D unplugged**;
  **Out 1/2 (SDK `out0`/`out1`) now carry only a scope**.
  ⚠ **`set_mesh_injection.py` (`OUT_CHS=(0,1,2,3)`) and the `mesh_b` scan target
  (`n1081b_module_map.py`, `("B", None)`) still drive ALL FOUR legs — under the alias that
  disables the SiPM enables and reproduces the 07-22 wall collapse. Pass `--outputs 2 3`
  until those defaults are fixed.** Whether the alias really holds for the cabled C Out 1/2
  is **still unverified**, as is the prediction that this restores a mesh ON/OFF axis.
  Docs: `docs/HANDOFF_2026-07-23_m6_secBC_control_aliasing.md`,
  `docs/HANDOFF_2026-07-23_m6_mesh_recable_out34.md`.
- **Acceptance window (N93B veto gate, M4.C lemo5):** **~1 → 81 ms** after the γ-flash as
  of **2026-07-22** (start 1 ms, width 80 ms — only the START moved; the width is
  unchanged). Moved down from a 5 ms start to match the `t > 1 ms` thermal gate
  of the GEANT trigger study (`~/CLionProjects/MX17_Full_Geant`
  `.claude/al_pair_background/PLASTIC_THRESHOLD.md`), so the measured in-gate trigger rate
  is directly comparable to that study's per-pulse background budget. History:
  ~30 ms → ~5–85 ms (07-21) → ~1 ms start (07-22). **SCOPE-MEASURED on 2026-07-22 (delay to leading edge 1 ms, allow-pulse width 80 ms -> accept 1-81 ms). The N93B has no front panel and no software interface: it is not settable or readable from here, but it IS confirmable after the fact from the DREAM event time-since-flash distribution (the PS/flash pickup is co-framed at 1800 ns), which should show a hard turn-on at 1 ms and turn-off at 81 ms.** Data taken before 07-22 evening used the 5 ms start (see
  `HANDOFF_2026-07-22_recov_trigger_scan_analysis.md`).
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
- **`.244` (M5) IS IN USE 24/7 as of 2026-07-29 — do not connect to it.** The trigger-
  timestamp stream (`n1081b/tt_stream_supervisor.py --section C`, tmux session
  `n1081b_timetag_watcher`, auto-started by `start_servers.sh`) holds **section C in
  Time-Tag continuously** in chained 6 h segments and owns the board's flock. A
  `send_data` stream is broadcast to every client, so a second connection both
  corrupts the capture and is a wedge risk. `poll_modules` already skips .244 whenever
  that tmux session is alive — **do not rename the session**. To do board work on
  .244, stop it first (`touch config/tt_stream_supervisor.stop`, wait for the pane to
  report the verified restore) — **never SIGKILL and never `tmux kill-session` it
  mid-stream**; both are the dirty disconnect that wedges these boards.
  Sections A/B/D stay counters and are sampled at each 6 h segment boundary (rates in
  the segment's `stats.json`). Design + operating notes: `n1081b/TIMETAG_WATCHER.md`
  §The standing configuration.
  **A HOST reboot used to fake a .244 wedge — FIXED 2026-07-30 13:00.** The supervisor
  autostarted ~14 s after boot, before the DREAM link was up, got
  `OSError(113, 'No route to host')` → `BoardWedgedError` → **6 h quarantine**, and its one
  retry was then blocked by its own marker ⇒ two alarms ⇒ **permanent stop**, taking M5
  counter telemetry with it. Now: a **reachability gate** (`_reachable_ok()`) opens no
  board session until `.244` answers ICMP; an unreachable host raises the new
  `BoardUnreachableError` and writes **no quarantine**; and a quarantine the chain wrote
  itself no longer counts as an independent alarm. ⚠ Note for anyone re-diagnosing this:
  `network-online.target` does **not** help (it is satisfied by the CERN NIC while
  enp4s0 has no carrier) and neither does link-up (the failing connect was 2.8 s *after*
  enp4s0 got its IP — it was ARP). If you do see it, the board is healthy: the tell is the
  previous segment's `stats.json` — `"finished": "signal"` + `"restored": true` means a
  clean signalled shutdown with a verified restore (and `post_rates_all_hz: null` there is
  normal, not a failed restore). Runbook + applied fix:
  `n1081b/HANDOFF_2026-07-30_tt_reboot_race.md`.
  **To reboot the host, use the GUI's Soft Shutdown button** (Overview → Run Control →
  Host) rather than rebooting straight away: it stops the run, then gives the supervisor as
  long as it needs to restore `.244` to counters. A plain `reboot` relies on the stream
  winding down inside systemd's 90 s stop timeout, and a SIGKILL at that deadline is the
  dirty disconnect that wedges the board. `bash_scripts/soft_shutdown.sh`.
- **`.242` (M3) is FULLY HEALTHY again — verified 2026-07-30 13:15.** It had been
  **LINK-DEAD since ~07-28 15:30** (ARP `FAILED`, no ping, `OSError(113)`) and was diagnosed
  as needing physical attention. **Nobody went to the crate.** It came back across the
  **07-30 11:37 host reboot**, which re-initialised the 10 GbE NIC (`atlantic` driver,
  `link change old 0 new 10000`).
  Health check run through `poll_modules._dump_board` (read-only, `auto_quarantine=False`,
  the same path every per-sub-run snapshot uses), two passes 12 s apart:
  **0 errors**, `login=True`, serial 49325, sw 2025.3.27.0 / zynq 22.10.07.00 /
  fpga 23.11.10.00, all four sections `and`, `ip neigh` `lladdr 00:12:5e:00:1b:80 REACHABLE`
  at 0.135 ms. Wall-leg ch0 reads `gate 20, delay 20, status True, invert False` on all four
  sections — the standing `+20 ns` config, intact.
  **Diffed against `snapshots/run79_asbuilt_2026-07-27.json` (pre-outage): ZERO config
  differences**, and identical firmware/serial. So the board never lost its state and never
  rebooted ⇒ **the fault was on the network path, host-side, not the board or the cable.**
  ⚠ Generalise this: ARP `FAILED` correctly rules out a libwebsock wedge, but it does **not**
  establish that the far end is at fault. Before concluding "needs someone at the crate",
  consider bouncing the host's DREAM interface.
  Note `get_function_results` returns `{"result": "none"}` on M3 — that is **normal for an
  `and` section**, not a fault; there is no rate readout outside counter mode (getting rates
  from M3 needs a counter-mode flip, which is a WRITE — see
  memory `n1081b-m5-bypass-measurement-technique`). M3's logic liveness is instead proven
  independently by **M5.C**, which streamed its four sector ANDs at ~67 Hz throughout.
  Its **NIM logic ran throughout**, so the trigger was never affected; what was lost during
  the outage was remote read/write of M3 (its per-sub-run snapshot rows and any scan target
  on it; the scan schedule during the outage touched only .241/.243/.245).
  **M3 can go back into the scan schedule / snapshot set.**
- `.244` (M5) history: **fully HEALTHY** (2026-07-17 midday: power-cycled at closeout, standard
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
