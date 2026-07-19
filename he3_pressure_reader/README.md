# 3He target pressure reader

Reads the 3He target pressure gauge through a **Keithley 2000** multimeter over
**GPIB** (NI GPIB-USB-HS + open-source linux-gpib). Read-only: nothing is commanded,
so there is a published state file and a per-day CSV, but no command file.

Pressure is a linear map of the gauge's DC output voltage:

```
pressure = (V - PRESS_OFFSET_V) * PRESS_SLOPE       [bar]     (offset 1.0 V, slope 400.0)
```

## Layout

| File | Role |
|---|---|
| `../he3_pressure_watcher.py` | Entry point. The **sole owner** of the GPIB bus. Started in its own tmux session by `start_servers.sh` at boot (or the GUI "Start 3He Pressure Watcher" button); runs `He3PressureController().run_blocking()`. |
| `he3_pressure_controller.py` | The controller: one background thread owns all GPIB access — polls the voltage, converts to pressure, publishes state, appends the CSV. |
| `keithley2000_read.py` | One-shot bench tool: `*IDN?` + a single measurement. |
| `keithley2000_live.py` | Live scrolling matplotlib plot, optional CSV logging (`MPLBACKEND=TkAgg`). |
| `setup_gpib_dkms.sh` | Root installer that registers the GPIB kernel module with **DKMS** so it survives kernel upgrades (see below). |
| `KEITHLEY2000_GPIB_SETUP.md` | Full from-scratch GPIB stack build guide (linux-gpib, udev, permissions, addressing). |
| `udev/` | Reference copies of the udev rules. |

## Data flow (why the Flask app never touches the bus)

A GPIB instrument has a single owner. The **watcher** is that owner; everything else
reads its published output. Do **not** open the bus from the Flask app or a second
script — one process per board.

- **State file** (`config/he3_pressure_state.json`) — latest reading, written
  atomically each poll. Served by `/he3_pressure/status`.
- **Per-day CSV** (`~/beam_july/slow_control/he3_pressure/he3_pressure_<date>.csv`) —
  `timestamp,pressure_bar`. Lives on the data disk with the other slow-control logs,
  not in the repo.

Sample period is configurable via `config/he3_pressure_config.json` (`poll_s`),
clamped to `[0.5 s, 60 s]` (2 Hz ceiling); the GUI writes it, the watcher applies it
within one loop.

## Operating it

```bash
# is it running?
tmux has-session -t he3_pressure_watcher && tmux attach -t he3_pressure_watcher

# quick bench check (uses the same venv binding, needs the bus free)
.venv/bin/python he3_pressure_reader/keithley2000_read.py --pad 15
```

The controller keeps a live connection and auto-reconnects: if the bus/instrument
drops, it marks `connected=false`, records `last_error`, and retries every few
seconds — so once the device node is back it recovers on its own within ~5 s.

## Troubleshooting: "disconnected"

Read `config/he3_pressure_state.json` → `last_error` first. The usual causes:

| `last_error` / symptom | Cause & fix |
|---|---|
| `dev() error: No such file or directory (errno 2)`, `/dev/gpib0` missing | GPIB kernel module not loaded. **Most common after a reboot into a NEW kernel** — the module is compiled per-kernel. See "Kernel upgrades" below. |
| `... no listeners currently addressed` | Wrong GPIB address, cable, or DMM off. Rescan the bus (setup guide Step 9). |
| `linux-gpib python binding (Gpib) not installed` | Binding missing from the venv — setup guide Step 5. |
| Permission denied opening `/dev/gpib0` | udev perms rule not applied / user not in `plugdev` — setup guide Step 7. |

Hardware sanity: `lsusb | grep 3923:709b` should list the NI GPIB-USB-HS. If it's
there, the sensor side is fine and the problem is in the software stack.

### Kernel upgrades — the durable fix (DKMS)

The linux-gpib `.ko` is built against a specific kernel. A kernel upgrade (this bit us
2026-07-19: `6.17.0-35` → `7.0.0-28`) leaves the module unbuilt for the new kernel, so
`/dev/gpib0` never appears and the reader reports disconnected.

This is now handled by **DKMS**, which auto-rebuilds the module on every new kernel
install. A trimmed source tree (only `ni_usb_gpib` + `gpib_common`) plus `dkms.conf`
lives in `../gpib_build/dkms_src/`, installed via:

```bash
sudo bash he3_pressure_reader/setup_gpib_dkms.sh     # idempotent; prints a verify block
```

Confirm with `dkms status` → `linux-gpib-kernel/4.3.7, <kernel>: installed`. After a
kernel upgrade DKMS rebuilds automatically during `apt` — nothing to do. Only if
linux-gpib 4.3.7 ever fails to compile against a much newer kernel do you fall back to
rebuilding from a newer release (setup guide Steps 2–3) and re-run the DKMS setup.

> Note: `modules.alias` only gets the `3923p709B → ni_usb_gpib` auto-load entry if
> `depmod -a` ran after install. DKMS does this itself; a bare `make install` does not.

See `KEITHLEY2000_GPIB_SETUP.md` for the complete stack build and Step 8 for details.
