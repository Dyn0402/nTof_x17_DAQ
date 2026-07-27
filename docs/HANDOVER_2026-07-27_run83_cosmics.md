# Handover 2026-07-27 — beam↔cosmics changeover is one command

**Supersedes `docs/RESTORE_run79.md`** for the current beam↔cosmics pair. That file describes
the run_79/run_80 pair at Hwm 2; the operating point has since moved to **Hwm 1 / Lwm 0** on
the run_82 result (`docs/PLAN_comb_spikiness_2026-07-27.md` §4d).

---

## State as of 2026-07-27 15:10

**`run_84` — production beam statistics, LIVE** since 14:37:52. The post-run_82 point:
RAW, latency 27, 20 smp × 60 ns, IPD 5, **Hwm 1 / Lwm 0**, drift 700 V all four, resist
A540/B540/C525/D520, plastics 0.90 MIP (`stat090` tag), mesh off, 120 × 1 h open-ended.
Verified on the applied cfg *and* on all 8 FEUs. `beam_gate.py` is running alongside it.

**`run_83` — beam-off cosmic reference, DONE.** 14:23–14:37, one 15 min sub-run stopped early
when beam returned: 4.3 GB, 8/8 FEUs, 22 428 events/FEU, 0.0000 % loss, 27.3 Hz (matches the
25.6 Hz run_72 reference). Marked complete by hand after that verification, so it is eligible
for the normal backup/cleanup pipeline. Trigger was UNGATED scint Singles (M4.C `or[0]` veto
OPEN, M4.D `or[1]`, PS leg dropped) with plastics at **0.50 MIP** via the `cosbounce` tag.

**Why cosmics use 0.50 MIP and beam uses 0.90:** cosmics *are* MIPs (Landau MPV = 1 MIP), so
the 0.90 MIP beam bar would cut most of them. The tags handle it in both directions —
`cosbounce` sets 0.50, `stat090` sets 0.90 — so neither changeover needs a manual step.

**Why cosmics also run Hwm 1:** so the cosmic reference differs from the beam run it
references in *nothing but the trigger*. At ~25 Hz the watermark is inert (0.5 % duty at
196 µs/event), so this does not break comparability with run_80 at Hwm 2.

---

## Changing mode — ONE COMMAND, nothing to decide

```bash
./switch_mode.py beam --go        # cosmics -> beam
./switch_mode.py cosmics --go     # beam -> cosmics
./switch_mode.py status           # read-only
```

**That is the entire procedure.** `--go` stops whatever run is live and waits for daq_control
to exit, allocates the next run number, regenerates the config at the settled operating point
(Hwm 1 / Lwm 0, IPD 5), applies the routing, reads it back and *checks* it, starts the run,
starts `beam_gate.py` for beam mode, and asserts the cfg RunCtrl actually received. It exits
non-zero if any step fails. A warm changeover is ~60 s.

**Do not** pick a run number, regenerate a config by hand, or grep the applied cfg afterwards
— `--go` does all of it, and doing it by hand is what used to make a changeover take 10–15
minutes of deliberation before anyone hit start.

⚠ **If the operating point ever moves, change it in `MODES` in `switch_mode.py` and nowhere
else.** Both directions and the post-start verification all read from that one place.

Without `--go`, a live run is still a hard refusal rather than something stopped as a side
effect — that guard is deliberate. `--force` overrides only the beam/mode sanity check, never
the live-run or board-lock guards.

### Unattended: start the beam run the moment beam returns

```bash
nohup .venv/bin/python beam_return_watcher.py > logs/beam_return_watcher.log 2>&1 &
```

Waits for beam confirmed back (beam_on **and** a pulse fresher than 60 s, on 3 consecutive
polls — `null`/stale/unreadable all count as *not* back), then runs `switch_mode.py beam --go`.
It pins whichever run is live when armed and aborts if a different one is live by the time
beam returns. One-shot; safe to kill.

### The PS delay does not need restoring

`setup_cosmics_singles_ungated.py` does **not** touch the M4.D1 gate&delay, so the 1440 ns
that frames the gamma flash to sample ~5 survives the cosmic detour. Confirmed on hardware at
both the run_82→cosmics changeover (14:21) and before it. `switch_mode.py` re-reads and prints
it every time rather than assuming — if it ever reads anything other than 1440, fix it with
`n1081b/set_ps_trigger_delay.py --delay 1440` before starting the beam run.

### If the beam comes back mid-sub-run

A manual stop leaves the in-flight sub-run **without** a `.subrun_complete` marker (by design —
`daq_control.py:364`), and an unmarked sub-run pins the whole run against `space_watcher`
cleanup. For a cosmic run the data is usually still complete and worth keeping, so **verify it
and then mark it by hand**:

```bash
/home/mx17/ana/.venv/bin/python ~/beam_july/analysis/flash_comb/tools/eventid_integrity.py <subrun_dir>
du -cb <subrun_dir>/raw_daq_data/*datrun*        # count DATRUN bytes, not dir size
touch <subrun_dir>/.subrun_complete
```

Done for run_83's `cosbounce_cos_0000` on 2026-07-27 after it came back 0.0000 % clean.

### Spotty beam

`beam_gate.py` holds `.pause_run` while beam is off, so a beam run will not *start* a sub-run
into a gap. Launch it alongside a beam run when the beam is unreliable:

```bash
.venv/bin/python beam_gate.py &          # stop with SIGTERM when the run ends
.venv/bin/python beam_gate.py --status   # read-only: what would it do right now?
```

It is co-operative — it only ever releases a hold recorded in its own `.beam_gate_hold`
sidecar, so it cannot clear an operator pause or daq_control's n1081b-apply hold, and it
releases on exit so a Ctrl-C cannot pin the run. `beam_on: null` or a stale `beam_state.json`
counts as UNKNOWN, not OFF: it holds its current state rather than pausing on a monitor
glitch. It cannot rescue a gap that opens *mid* sub-run — that point just gets fewer flashes,
which is why every comb metric is per-flash-anchored. **Judge points by flash count, never
wall-clock.**

---

## Analysis of the cosmic data

Same tooling as the beam runs (needs `/home/mx17/ana/.venv/bin/python` for uproot):

```bash
/home/mx17/ana/.venv/bin/python \
    ~/beam_july/analysis/flash_comb/tools/eventid_integrity.py \
    /mnt/data/x17/beam_july/runs/run_83/cosbounce_cos_*
```

⚠ The flash-anchored comb tools do **not** apply to cosmics — there is no gamma flash to
anchor on. For cosmic tracking/efficiency use the run_72 recipe
(`docs/METHOD_track_rate_vs_hv_time_intensity.md`); note the run_72 caveat that most of the
apparent HV dependence at `MIN_HIT_AMP=200` was a threshold artefact (operating threshold is
~50 ADC).

---

**Related:** `docs/PLAN_comb_spikiness_2026-07-27.md` (the run_82 result and why Hwm 1),
`docs/METHOD_readout_window_optimization.md` (the window recipe, and the warning that the
0.5 ms-bin CV alone is untrustworthy), `docs/RESTORE_run79.md` (the previous, Hwm-2 pair).
