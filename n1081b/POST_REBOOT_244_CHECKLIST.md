# Post-reboot checklist — recover .244 (M5) after a reboot

> **ROUND 2 below is OBSOLETE (2026-07-17 midday):** the power-cycle happened and the
> "A/B per-section TT wedge" was disproven — TT silence is a live input-rate ceiling
> (~50 Hz streams, ~800 Hz silent), not a wedge; reboots are irrelevant to it. See
> `HANDOFF_2026-07-17_tt_rate_ceiling.md`. Steps 1–4 of ROUND 1 remain the valid
> post-reboot recovery runbook.

## ROUND 2 — 2026-07-17 physical access: POWER-CYCLE to clear the A/B per-section TT wedges

Found overnight 2026-07-17 while qualifying the v2 time-tag watcher: sections **A and B
never stream TT** (zero tags on every tap; `reset_channel` replies fine; counters count
normally), while **C and D stream fine**. This is the known **per-section TT wedge**
(TIMETAG_MULTISECTION_2026-07-13.md §3) left over from the 07-15 all-section stream kill —
the 07-16 **touchscreen reboot cleared C/D but not A/B**, and re-arming (counter→TT function
cycling) does NOT clear it. SEC_A is the four scintillator walls — the watcher's main target
— so this matters.

At the crate:
1. **POWER-CYCLE the NIM unit** (harder reset than the touchscreen reboot, which left A/B
   wedged). Then run steps 1–4 of the 07-16 checklist below (bounded probe → clear any
   quarantine → `restore_244_counters.py` → verify counting).
2. **Probe per-section TT health** (gentle, one tap per section, auto-restores counters):
   ```bash
   .venv/bin/python n1081b/tt_section_probe.py
   ```
   Every section should report tags (walls/scints/sectors/taps all have off-beam rate).
   A section with 0 tags but counting counters is still TT-wedged.
3. If A/B stream again → switch the watcher to all four sections (it was soak-tested on
   `--sections CD` overnight 07-17; see TIMETAG_WATCHER.md §Rollout). If a power-cycle does
   NOT clear them, that's strong evidence the wedge is in nonvolatile state → CAEN support
   question (reference the 2026-07-15 email draft).

---

## ROUND 1 — 2026-07-16 touchscreen reboot (executed; kept as runbook)

> **✅ EXECUTED & COMPLETE 2026-07-16.** Board was touchscreen-rebooted, then all steps
> below ran clean: bounded login probe replied in 0.0 s → quarantine cleared → all four
> sections restored to `counter` → verified counting (SEC_A ~700 Hz on all channels) →
> `.244` re-added to `poll_modules.POLL_IPS` (`240–245`) → Board Access card shows **free**.
> `n1081b/CLAUDE.md` board-state updated to HEALTHY. Kept here as the runbook for next time.

`.244` was probed 2026-07-16 10:41 and is **still wedged** at stage-3 (ws login timed
out after 8 s; did NOT self-heal in ~11.5 h). It needs a **physical touchscreen reboot**.
Run through this AT/AFTER the reboot, in order. All board contact here goes through the
`board_session()` gateway (safe: lock + bounded connect + clean close), so nothing here
can dirty-disconnect and re-wedge the freshly-recovered board.

`.244` is **walls-monitoring only** (zero trigger impact) — safe to work on. Do all
commands from the repo root: `cd ~/PycharmProjects/nTof_x17_DAQ`.

---

### 0. At the crate — reboot + observe
- Reboot `.244` via the front-panel touchscreen (Settings → reboot, or power-cycle the NIM unit).
- While there, note/photograph the **Settings / Version** pages for the runbook.
- Note whether it booted its **last-saved config** or a **default**. (An as-built config
  was saved on the board as `asbuilt_20260715`; if it booted to defaults it can be
  reloaded with `load_configuration_file('asbuilt_20260715')` via a `board_session`.)

### 1. Confirm the command interface is actually back (bounded probe)
```bash
.venv/bin/python -c "
from websocket import create_connection as c; import time; t=time.time()
ws=c('ws://192.168.10.244:8080/',timeout=8); ws.settimeout(8)
ws.send('{\"command\":\"login\",\"callback\":\"login\",\"pwd\":\"password\"}')
print('login', ws.recv()[:60], 'in', round(time.time()-t,1),'s'); ws.close()"
```
Expect an **instant** clean reply. If it still times out (8 s), the interface isn't up
yet — wait and retry; do NOT proceed.

### 2. Clear the quarantine (ONLY after step 1 answered cleanly)
```bash
.venv/bin/python -c "import sys;sys.path.insert(0,'.'); \
  from n1081b.n1081b_session import clear_quarantine; print('cleared', clear_quarantine('192.168.10.244'))"
```

### 3. Restore the four sections to COUNTER (safe, board_session-based)
```bash
.venv/bin/python n1081b/restore_244_counters.py
```
Expect: `OK: 192.168.10.244 all four sections restored to 'counter'.`
(This replaces the stale `n1081b_timetag_watcher.py --restore` reference — that file does
not exist, and the old restore path in `timetag_watcher_controller.py` used a raw
connection. Use this tool.)

### 4. Verify it's actually counting (beam on → deltas non-zero)
```bash
.venv/bin/python -c "
import sys,time; sys.path.insert(0,'n1081b')
from n1081b_session import board_session
from n1081b_sdk import N1081B
with board_session('192.168.10.244', purpose='verify counting', require_login=False, min_gap_s=0.0) as s:
    rd=lambda: {sec.name:{c['lemo']:c['value'] for c in s.call('get_function_results',sec)['data']['counters']} for sec in N1081B.Section}
    a=rd(); time.sleep(3); b=rd()
    for sec in a: print(sec, 'Δ', {k:b[sec][k]-a[sec][k] for k in a[sec]})
"
```
Non-zero deltas (with beam on) = counting. All-zero everywhere = investigate (cabling / no beam).

### 5. Re-add .244 to per-sub-run polling
Edit `n1081b/poll_modules.py`, `POLL_IPS` (~line 56): put `244` back in the tuple →
`(240, 241, 242, 243, 244, 245)`. **No restart needed** — the next run is a fresh
`daq_control.py` process and auto-loads it. (poll_modules auto-skips `.244` anyway if the
time-tag watcher tmux session is ever revived, so this is safe to leave in.)

### 6. Update the runbook state
In `n1081b/CLAUDE.md`, change the `## Current board state` `.244` line from
"STILL WEDGED — QUARANTINED …" to healthy (all 4 sections counter, counting, back in POLL_IPS).

### 7. Confirm on the dashboard
Open the GUI **Trigger tab → Board Access** card (port 5001): `.244 (M5)` should now show
**free** (not QUARANTINED). Optionally run `.venv/bin/python n1081b/dump_module_info.py`
and confirm `.244` reads back cleanly with 0 errors.

---

## Not tied to the reboot — do soon (untested write paths)
The board-hygiene migration was validated on read-only paths only (a live run blocked
write testing). The **first time** each of these migrated *write* scripts is run, watch it
(it read-back-verifies every write and aborts cleanly on a busy/wedged board):
`trigger_mode.py <mode>`, `systematic_threshold_scan_v3.py`, `measure_chain.py`
(zs_rate_scan), and any `setup_*.py` you use. See the plan's STATUS section.
