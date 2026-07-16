# N1081B Time-Tag multi-section readout — investigation & verdict (2026-07-13)

**Question:** can we read time-tag timestamps from more than one section of a single
N1081B board *at the same time, with attribution* (which tag came from which section)?
Motivation: Module 5 (.244) has 4 sections × 6 lemo inputs = up to 24 timestamped
channels, but the streaming logger (`mod5_timetag_logger.py`) only reads one section.

**Verdict (updated 2026-07-14): NO clean *simultaneously-tapped* attributed stream exists
(you cannot tap 2+ sections at once and tell them apart — §1). BUT full multi-section
coverage with attribution IS achievable via the persistent-FIFO / dead-time round-robin
scheme — TESTED AND WORKING, see §5.** Each section, once set to Time-Tag, accumulates to
its own deep (~30 s) buffer concurrently; you drain them one-at-a-time and label by section.
§4's "UNTESTED / still-promising" framing is now superseded by §5's results (and its guess
that `reset_channel` clears the buffer was wrong — reset is what *dumps* it). Details below.

All tests run live on .244 (fw 2025.3.27.0) from mx17-daq. Board was returned to its
steady state (all 4 sections `counter`, lemo0-3, no gate) and verified counting after.
Scripts: `n1081b/tt_multisection_probe.py`, `n1081b/tt_panel_gating_test.py`.

---

## 1. Confirmed facts (three independent sources agree)

### 1a. The streamed packet carries NO section identifier
`send_data` packets have exactly two keys: `["command", "timetag_data"]`. Every tag is
exactly `[panel 1-6, timestamp_ns]` — nothing else. Verified by dumping *all* keys of
live packets.

- **SDK source** (`.venv/.../n1081b_sdk/N1081B_sdk.py`): the *commands*
  `configure_time_tagging` / `start_tt_data` / `stop_tt_data` each take a `section`
  param (you tell the board which section to start), but the *readout*
  `get_time_tag_data()` just does `ws.recv()` and returns `j["timetag_data"]` — it
  never reads a section field. The library author makes no attempt to attribute a
  packet to a section.
- **User manual** (`docs/WEB_UM8139_x1081B_rev1.pdf`): Time-Tag's *documented* readout
  is "Save timestamps on file … download via Web Interface", format `(CHx, timestamp)`.
  The websocket `start_tt_data`/`send_data` streaming path is **not documented at all** —
  it is reverse-engineered. So the single-section streaming limit is a property of an
  undocumented path, not a stated hardware limit.

### 1b. Two sections stream simultaneously but MERGE indistinguishably
Starting `start_tt_data` on sections A and B together: the board accepted both, ran
both (aggregate rate ≈ A+B), and broadcast all tags in one stream. Packet keys were
still only `["command","timetag_data"]`. No way to separate A from B.

### 1c. The enable mask is IGNORED — panel-partitioning is impossible
Tested on section B (fresh connections, real statistics):

| `configure_time_tagging` config | panels that emitted tags |
|---|---|
| all 6 enabled                    | 1, 2, 4, 5 |
| **panel 2 disabled** (en1=False) | 1, **2**, 4, 5  ← still present |
| **only panel 2** (en1=True)      | **1, 2, 4, 5**  ← not restricted |
| only panels 4,5                  | 1, 2, 4, 5 |

`configure_time_tagging` returns `Result:false` **and has no effect** — the Time-Tag
function always streams every channel that has signal. Input-channel `status=False`
also does NOT suppress emission (panel 2 streamed 113 tags/3s with its channel
disabled). Turning an input off does not change the packet structure either.

**Consequence:** the "off-channel fingerprint / disjoint-panel partition" idea (restrict
each section to a distinct panel set so the panel number reveals the section) **cannot
work** — there is no lever to restrict which panels a section emits.

### 1d. File-based readout is web-GUI-only (not reachable via websocket)
`get_config_file[time_tag|timetag|tt|time_tagging]` → `Result:true` but `data:""`
(empty). `save_tt_data`/`get_tt_file`/`download_tt` → `"invalid command"`. The manual's
file-save path exists only in the board's web GUI; our tooling can't get it over the
websocket. (SDK `download_function_file` supports only LUT/pattern/ToF, not Time-Tag.)

---

## 2. Board / protocol facts measured this session

- **Per-command latency on .244** (median over many round-trips):
  - `get_function_results`, `reset_channel`, raw sends: **~0.5 ms**
  - `set_section_function`: **~46 ms**  (FPGA function reconfiguration)
  - `configure_function` (counter/time_tagging): **~45 ms**
  - ⇒ switching one section into Time-Tag mode (`set_section_function` +
    `configure_time_tagging`) costs **~91 ms**, dominated by the two FPGA reconfigs.
- **Free-running board clock**, common to all sections, 10 ns steps, ~64-bit, does not
  reset on `reset_channel`. So timestamps from different sections/times are on one clock.
- **FIFO / batching:** packets batch up to ~1024 tags (one 1028-tag packet seen) →
  internal FIFO ≈ 1024 tags deep. Board flushes every ~10–100 ms (median 12 ms).
- **All 4 sections had live signal** during the test (counters incrementing; panels
  1,2,4,5 seen on A/B/C/D — the four scintillator walls).

## 3. Operational cautions (learned the hard way)

- **Reconnect churn wedges the embedded web server.** ~15+ rapid connect/login/
  disconnect cycles in a few seconds made .244 briefly refuse TCP connections
  (`ConnectionRefusedError`). It self-recovers in seconds. **Use one persistent
  connection; pace reconnects.**
- **A TT stream that dies mid-operation can wedge that section's TT path.** After a
  crash mid-stream on section A, section A returned 0 time tags while its *counters* on
  the same section kept working normally (and B/C/D TT worked fine). Only a **board
  reboot** clears the per-section TT wedge. Nothing depends on A's TT today.
- **Counters need `reset_channel(section, ch, FN_COUNTER)` to start/resume counting;**
  `start_acquisition` is a no-op for counters (returns `"{}"`). Restoring the function
  alone does not restart counting.
- **.244 steady state:** all 4 sections `FN_COUNTER`, lemo0-3 enabled, gate off; SEC_A
  input-channel status 1,3,4=on / 2,5,6=off, ch6 gate=9600. Restore to this after any TT work.

---

## 4. Section cycling — how it works and the ~1.2 s beam question

**How cycling works today:** `mod5_timetag_logger.py --section cycle --dwell N` rotates
through sections. Each dwell it calls `set_section_function(sec, TT)` +
`configure_time_tagging` (the ~91 ms switch), starts the stream, drains for `dwell`
seconds, stops, moves on. Only one section is in Time-Tag at a time; the other three
are **not capturing** during that dwell.

**Can we cycle sections fast enough to grab all sections within one ~30 ms spill?
No — two independent blockers:**

1. **Latency:** one section switch is **~91 ms** — already **3× the whole 30 ms beam
   window.** You cannot switch even once inside a spill, let alone four times.
2. **Exclusivity:** while section A is in Time-Tag, B/C/D are not capturing. Time-slicing
   the function *within* a spill means each section only sees its slice and you lose the
   rest — you never get the full spill on all sections this way.

**What cycling *can* do — per-spill rotation:** fully capture ONE section per spill and
rotate across spills (spill 1 → A, spill 2 → B, …). Because the clock is common and
free-running you accumulate all sections over many spills. Good for **rates/spectra**,
but the sections are captured on **different spills**, so you get no cross-section
coincidence within a single spill. (And the ~91 ms switch eats ~8% of a 1.2 s cycle if
you switch every cycle — tolerable but wasteful.)

**The still-promising path (UNTESTED): all sections persistent + dead-time readout.**
Set all 4 sections to Time-Tag **once** (4 × ~91 ms, paid a single time at startup, not
per spill), leaving them all capturing to their own FIFOs concurrently — we *proved*
sections capture concurrently (§1b, the merge test). Then, during the ~1.17 s of dead
time after each spill, read the sections out **one at a time** (`start_tt_data` A →
drain → `stop_tt_data` A → B → …), which gives clean per-section attribution. If it
works you capture the **entire spill on all 24 inputs, simultaneously acquired, with
attribution** — far better than time-slicing.

It hinges on assumptions that need one clean test:
- **(a)** Does a section's FIFO keep filling while its function is Time-Tag but it is
  NOT the actively-tapped stream? (If the FIFO only fills during an active
  `start_tt_data`, the un-tapped sections lose hits → back to exclusive.)
- **(b)** Can we tap a section's accumulated FIFO **without `reset_channel` clearing
  it**? (The normal start sequence sends `reset_channel` first — try `start_tt_data`
  alone to preserve buffered tags.)
- **(c)** FIFO depth ≈ 1024 tags/section vs hits-per-spill-per-section. If a wall gets
  >1024 hits in one spill, older tags are lost. Measure hits/spill first.

**Recommended next test (≈20 min, board free, ideally reboot .244 first to clear A's TT
wedge):** put all 4 sections in TT, drive a known pulse train (or just take beam), and
check whether reading section B's FIFO in dead time returns tags that accumulated while
A was being tapped — i.e. verify (a) and (b). If yes, extend `mod5_timetag_logger.py`
with a `--persist-all` readout mode. If no, per-spill rotation (or a firmware feature
request to CAEN to add a `section` field to `send_data`) is the ceiling.

**Alternative for true simultaneity now:** put the channels you need to correlate on
**separate boards** — each board is its own websocket, so per-board attribution is free.

---

## 5. PERSISTENT-FIFO TEST RESULTS (2026-07-14, live on .244, sections B & C)

Ran the recommended test. **The persistent-FIFO dead-time readout scheme WORKS** —
but the `reset_channel` mechanism is the *opposite* of what §4 assumed, and the buffer
is far deeper (in time) than feared. Scripts: `n1081b/tt_persist_fifo.py`,
`n1081b/tt_persist_confirm.py`.

### 5a. Mechanism (confirmed)
- **`start_tt_data` ALONE returns 0 tags.** Without a preceding `reset_channel`, tapping
  a section yields nothing (seen every time: test-1 phases 2 & 4). `reset_channel` is
  **mandatory** and is what actually opens/flushes the stream.
- **`reset_channel` does NOT clear the buffer — it DUMPS it.** After 2 s un-tapped,
  `reset_channel`+`start_tt_data` returned **2097 tags spanning ~5 s** of board clock in a
  0.6 s drain — i.e. the whole history since the previous reset, not just live tags. So
  the §4 assumption "reset_channel wipes the FIFO" is **wrong**: reset is the trigger that
  releases the accumulated backlog.
- **Sections accumulate independently & concurrently while in TT, even when NOT the
  actively-tapped stream.** In the confirmation run B and C were both armed, left un-tapped,
  then drained one-at-a-time: **each returned its own section's data** (distinct per-panel
  distributions — B: {5:117,1:88,4:41,2:20}; C: {4:217,1:51,2:73,5:88}), and C's data still
  covered the interval during which B was being read. Independent per-section buffers. ✓

### 5b. Attribution
Clean **by construction**: tap ONE section at a time (`reset`+`start` → drain → `stop`),
label every drained tag with that section. (Panels are 1,2,4,5 on *every* section — the
panel number does NOT identify the section; the section you tapped does.) Do NOT tap two
sections at once — that reproduces the indistinguishable merge (§1b).

### 5c. Buffer is a DEEP rolling window — no 1.2 s real-time pressure
Drained spans were **~26–39 s of board time**, not the few-second dead times. The buffer
holds roughly the **last ~30 s** of edges (rate-independent: quiet section ≈266 tags/30 s
@ ~9 Hz; busy section ≈4371 tags/30 s @ ~148 Hz — both ≈30 s). Consequences:
- **After a spill you have ~tens of seconds to read every section out** — the ~1.2 s
  supercycle is irrelevant; poll each section once per second and you cannot miss a spill.
- Reads return an **overlapping rolling window** (not clean since-last-read), so a real
  logger MUST **dedup by (section, panel, absolute-timestamp)** across polls. The common
  free-running clock makes this exact.
- **Depth caveat:** at high instantaneous rate the buffer is *depth*-limited (~4000+ tags
  seen). If a single 30 ms spill puts >~4000 edges on one section, the oldest drop. Measure
  peak edges/spill/section under beam before trusting completeness on the hottest wall.

### 5d. Recommended `--persist-all` design (viable — build it)
1. Arm every usable section (skip A until a reboot clears its TT wedge) to `FN_TIME_TAG`
   once at startup.
2. Round-robin forever: for each section `reset_channel`+`start_tt_data` → `drain_until_quiet`
   → `stop_tt_data`; tag records with the section; union into a timestamp-keyed set (dedup).
3. Poll each section every ≤~1 s (well inside the ~30 s buffer horizon).
4. On exit restore all sections to `FN_COUNTER` + `reset_channel` per lemo to restart counts.

Open follow-ups (not blockers): exact buffer discipline (time-window vs depth) and the hard
depth cap; whether reads are destructive (affects whether dedup is required or just safety);
peak edges/spill/section under real beam. Section A's TT path is still wedged (counters fine)
— a .244 power-cycle clears it; the scheme runs on B/C/D meanwhile.
