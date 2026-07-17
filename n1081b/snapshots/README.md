# Snapshots — which dump is current?

**Canonical restore point: `dump_2026-07-18_postfifo_canonical.json`**
(taken 2026-07-18 00:1x, right after the 07-17 night post-FIFO recalibration;
M5/.244 sections absent — held by a TT qualification at dump time, its config
is counters ×4 and not part of the trigger).

Standing values it records (verified on-board 2026-07-17 23:47):
M1 walls +15/+16/+15/+16 mV · M2 plastics −30/−30/−30/−38 mV · M3 wall-leg
(ch0) G&D delay +20 ns, scint leg 0, gates 20 ns.

⚠ **Every `dump_*.json` older than 2026-07-18 is STALE for M1/M2/M3**: the
plastics moved to linear fan-in/fan-out on 2026-07-17 (~2× amplitude), so older
dumps carry ~2×-too-shallow plastic thresholds (−15 era; wall D would be DEAD —
its broken D1 input kills it ≤ −24 mV), pre-recalibration wall thresholds, and
M3 delay=0 (which silently undoes the +20 ns FIFO-lateness compensation).
Older dumps remain valid for M4/M5/M6 layout reference only.

Full story: `../HANDOFF_2026-07-17_night_trigger_scans.md`; canonical layout
doc: `../RUN_MODES_2026-07.md`.
