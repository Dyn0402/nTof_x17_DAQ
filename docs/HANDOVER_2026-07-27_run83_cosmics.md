# Handover 2026-07-27 — run_83 cosmics live, run_84 beam ready

**Supersedes `docs/RESTORE_run79.md`** for the current beam↔cosmics pair. That file describes
the run_79/run_80 pair at Hwm 2; the operating point has since moved to **Hwm 1 / Lwm 0** on
the run_82 result (`docs/PLAN_comb_spikiness_2026-07-27.md` §4d).

---

## What is running now

**`run_83` — beam-off cosmic reference**, started 14:21 on 2026-07-27.

| | |
|---|---|
| config | `run_config_cosmics_optimal_83.json` (from `run_configs/run_config_cosmics_optimal_80.py`, `RUN_NUM=83`) |
| trigger | UNGATED scint Singles — M4.C plain `or[0]` (veto OPEN), M4.D `or[1]`, PS leg dropped |
| readout | RAW, latency 27, 20 smp × 60 ns, IPD 5, **Hwm 1 / Lwm 0** |
| HV | drift 700 V all four, resist A540/B540/C525/D520 — same as the beam point |
| plastics | **0.50 MIP** (A−65/B−78/C−86/D−83), tag `cosbounce`, re-asserted per sub-run |
| schedule | 24 × 15 min = 6 h, **stop-anywhere** |

Verified on the applied cfg: `Main_Trig_OvrWrnHwm 1 / OvrWrnLwm 0`, `NbOfSamples 20`,
`InterPacket_Delay 5`, `Dream * 12 0x001B` (= latency 27). FEU pre-flight passed 8/8 + TCM.

**Why 0.50 MIP and not the beam run's 0.90:** cosmics *are* MIPs (Landau MPV = 1 MIP), so the
0.90 MIP beam threshold would cut most of them. The `cosbounce` tag handles this automatically
and the beam run's `stat090` tag puts 0.90 MIP back — neither needs a manual step.

**Why Hwm 1 here too:** so the cosmic reference differs from the beam run it references in
*nothing but the trigger*. At ~25 Hz cosmic rate the watermark is completely inert (0.5 % duty
at 196 µs/event), so this does not affect comparability with run_80, which ran at Hwm 2.

---

## Going back to beam — one command

The beam config is already generated and `switch_mode.py`'s defaults already point at it.

```bash
# 1. wait for a REAL pulse — daq_control has no beam-gating of its own
cat config/beam_state.json          # beam_on: true, recent last_pulse_time

# 2. stop the cosmic run (the operator's action; switch_mode refuses to touch a live run)
./bash_scripts/stop_run.sh

# 3. one command: re-trigger + verify + launch
./switch_mode.py beam --start
```

That applies `n1081b/trigger_mode.py scint --singles --ps-pickup`, reads the routing back and
**checks** it (non-zero exit if the hardware did not land right), reports the M4.D in0 PS
gate&delay, and launches `run_config_stats_optimized_84.json`.

**`run_84` = the new production point:** latency 27, n_samples 20, IPD 5, RAW, **Hwm 1 /
Lwm 0**, drift 700, resist A540/B540/C525/D520, plastics 0.90 MIP, mesh off, 60 × 1 h
open-ended.

### The PS delay does not need restoring

`setup_cosmics_singles_ungated.py` does **not** touch the M4.D1 gate&delay, so the 1440 ns
that frames the gamma flash to sample ~5 survives the cosmic detour. Confirmed on hardware at
both the run_82→cosmics changeover (14:21) and before it. `switch_mode.py` re-reads and prints
it every time rather than assuming — if it ever reads anything other than 1440, fix it with
`n1081b/set_ps_trigger_delay.py --delay 1440` before starting the beam run.

### If the beam comes back mid-sub-run

A manual stop leaves the in-flight sub-run **without** a `.subrun_complete` marker (that is by
design — `daq_control.py:364`). For a cosmic run that costs nothing; just be aware the last
`cosbounce_cos_*` directory is short and unmarked, and it pins run_83 against `space_watcher`
cleanup until dealt with.

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
