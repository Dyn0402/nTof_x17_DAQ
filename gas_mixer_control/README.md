# Gas Mixer Control

Control and monitoring for the two Bronkhorst **mass-flow controllers (MFCs)** that
mix **isobutane into argon** for the detector gas. Provides a web tab (in the DAQ
Flask GUI) to set the mixture, live readback of both controllers, and continuous
CSV logging.

The two knobs, exposed independently:

- **Total argon flow rate** — set directly, in **ln/h**.
- **Isobutane percentage of the total mixture** — the isobutane flow is derived so
  that isobutane is `p` % *of the total*: `iso = argon · p / (100 − p)`.

## Hardware

Both controllers are **daisy-chained on one FLOW-BUS** serial port
(`/dev/ttyUSB0`) and speak the Bronkhorst **propar** protocol. They **self-identify**
by their calibrated `Fluid Name`, so no manual "which unit is which" test is needed.

| Role | Node | Serial | Model | Fluid | Full scale | Native unit | Calib. P in/out |
|------|------|--------|-------|-------|-----------|-------------|-----------------|
| **Argon** | 4 | `M14210735A` | F-201CI-100-RGD-33-E | `Ar` | **7.591 ln/h** (≈126.5 mln/min) | ln/h | 3.0 / 1.5 bar-a |
| **Isobutane** | 3 | `M10211716B` | F-201DI-PAD | `C4H10 #2` | **0.175 ln/min = 10.5 ln/h** (175 mln/min) | ln/min | not set (0/0) |

Identification order: `Fluid Name` first (`Ar` → argon, `C4H10`/butane → isobutane),
falling back to a serial-number map (`SERIAL_ROLE` in `flow_controller.py`). **Update
`SERIAL_ROLE` if a controller is ever swapped or re-flashed.**

### Max achievable mixtures

Argon is the smaller controller, so **argon full scale (7.591 ln/h) is the ceiling**
for the practical isobutane range — isobutane has huge headroom (it wouldn't become
limiting until ~58 % of total):

| Iso % of total | Max argon | Isobutane there | Max total |
|----------------|-----------|-----------------|-----------|
| 5 %  | 7.591 ln/h | 0.400 ln/h | 7.99 ln/h |
| 10 % | 7.591 ln/h | 0.843 ln/h | 8.43 ln/h |
| 20 % | 7.591 ln/h | 1.898 ln/h | 9.49 ln/h |

## Installation

- Python deps: **`bronkhorst-propar`** (`pip install bronkhorst-propar`), plus the
  Flask app's usual deps (`flask`, `flask-socketio`, `pandas`). Install into the
  repo venv (`.venv`).
- Serial access: the user running Flask must be in the **`dialout`** group
  (`sudo usermod -aG dialout $USER`, then re-login).
- No other process may hold `/dev/ttyUSB0` while Flask owns it — see *Troubleshooting*.

The controller starts automatically when the Flask app imports it
(`get_controller()` in `app.py`); it's safe if the hardware is absent (it reports
disconnected and keeps retrying discovery).

## How measurement / control works

These are **thermal** MFCs: they measure *flow* (thermally), **not pressure** — there
is no live pressure sensor.

- **You only ever set the flow setpoint** (propar setpoint, raw `0..32000` = `0..100 %`
  of capacity). The controller runs its own PID loop and drives the valve to make
  *measured* flow match the setpoint.
- **Valve % is an output, not an input** — it's how far the proportional valve is
  open (`Valve Output` register, raw `0..2²⁴` = `0..100 %`). You cannot command it.
- **Control mode** is forced to `0` (digital/RS-232 setpoint) on connect so digital
  setpoints are honored.

Readback per poll (all normalized to **ln/h**): measured flow (`Fmeasure`, dde 205),
setpoint (`Fsetpoint`, dde 206), valve output (dde 55), temperature (dde 142). Static
identity + calibration read once at discovery.

### ⚠️ Temporary argon cap (low supply pressure)

The argon supply is currently **~0.7 bar**, well below the controller's **~3.0 bar-a
calibrated inlet**, so argon **can't reach full scale** — the valve pins fully open
and flow plateaus around ~5.3 ln/h. While this is the case, argon is **software-capped
to 4.0 ln/h**:

```python
# flow_controller.py
ARGON_MAX_LNH = 4.0        # set to None to remove the cap
ARGON_LIMIT_NOTE = "low inlet pressure (~0.7 bar) — temporary, remove when pressure restored"
```

`apply_mix()` clamps to this and warns; the GUI shows the cap and the *Max Argon*
button targets it. **To restore full range: raise the argon inlet regulator toward
~3 bar-a, set `ARGON_MAX_LNH = None`, and restart Flask.**

> Consequence while capped/pressure-limited: because isobutane still reaches its
> setpoint but argon may undershoot, the **real** isobutane fraction can exceed the
> requested %. The number computed from *measured* flows ("Isobutane % (measured)")
> is the trustworthy one.

## Architecture

- **`flow_controller.py`** — `FlowController`, a process-wide singleton
  (`get_controller()`). One **background thread owns all serial access**: it polls
  both controllers every `poll_s` (2 s), caches the readback (so web reads never touch
  the bus or collide), and appends a row to the per-day CSV every `log_s` (2 s).
  A lock serializes the poll reads and the setpoint writes from web requests.
  Discovery retries transient dropped reads so a single `None` can't crash the thread.
- **Flask routes** (`flask_app/app.py`):
  - `GET  /gas/status` — latest cached readback (both controllers, derived totals/%,
    argon limit). Never hits the bus.
  - `POST /gas/apply` — `{argon_lnh, iso_percent}` → command the mixture.
  - `POST /gas/zero` — emergency stop (both setpoints → 0).
  - `GET  /gas/history?hours=N` — downsampled flow history from the CSV for the plots.
- **GUI** (`flask_app/templates/`): a **Gas Mixer** tab (controls, per-controller
  readback cards, two trend plots) plus a **Gas Mixer** panel and live Total-flow /
  Isobutane-% tiles on the **Overview** tab. Plots are CSV-driven (last 6 h), render
  on tab-show, and auto-refresh every 10 s while visible.

## CSV logs

Per-day file: `gas_mixer_control/logs/gas_flow_YYYY-MM-DD.csv`, one row every ~2 s
(0.5 Hz — the full acquisition rate). Columns (all flows in ln/h):

```
timestamp,
argon_set_lnh, argon_flow_lnh, argon_valve_pct, argon_temp_c,
iso_set_lnh,   iso_flow_lnh,   iso_valve_pct,   iso_temp_c,
iso_pct_set, iso_pct_meas, total_flow_lnh
```

`logs/` is git-ignored (data, not code). Note: the **isobutane unit's temperature**
reads ~0.6 °C — that model has no real temperature sensor; ignore it. Argon temp is
real (room temperature).

## Usage

**Normal use:** open the DAQ GUI → **Gas Mixer** tab. Enter argon flow (ln/h) and
isobutane % → **Apply Mixture** (confirms first). **Max Argon** jumps argon to its
max commandable flow at the current %. **Emergency Zero** closes both. Watch
"Isobutane % (measured)" converge as the flows settle.

**Standalone check** (read-only, no setpoint change) — connect, print one readback:

```bash
python gas_mixer_control/flow_controller.py
```

**Other scripts in this directory:**
- `mixer_control_check.py` — bus/device diagnostic; blips each controller to 10 % to
  confirm it responds, then zeros it.
- `max_flow_test.py` — drives both controllers to 100 % for 30 s (bench test), then
  zeros. **Do not run while Flask owns the bus.**

## Safety notes

- A **Flask restart does not auto-apply or auto-zero** — the MFCs hold their own
  setpoints in hardware, and the tab reflects the true hardware setpoint. If gas was
  flowing, it keeps flowing across a restart.
- **Isobutane is flammable** — ensure ventilation/exhaust and leak-checked fittings.
- If valves have wound open with no supply and you then open the gas, expect a brief
  flow surge before the controllers settle.

## Troubleshooting

- **Blank plot** — Plotly can't size a plot inside a hidden (`display:none`) tab;
  the plots re-render on tab-show. Hard-refresh the browser after template changes.
- **Bus contention / garbage or `None` reads** — only one process may use
  `/dev/ttyUSB0` at a time. Don't run the standalone scripts while the Flask
  `flow_controller` poll thread is running; they will fight over the serial port.
- **Applying code changes** — restart the `flask_server` tmux session
  (`flask_app/start_flask.sh`). The controller re-reads setpoints from hardware
  (which persist), so the current mixture is preserved.
