# Reboot recovery — what comes back by itself, and what does not

Written after the **2026-07-22** reboot, where several things stayed down silently. Read
this before concluding "the DAQ is broken after a reboot"; most of it is a short checklist.

`daq-startup.service` runs `start_servers.sh` at boot, which starts every tmux session
listed below. Everything else is manual.

---

## 1. Quick check after any reboot

```bash
tmux ls                      # expect the 12 sessions below
klist                        # a valid dneff@CERN.CH ticket
curl -s localhost:5001/monitor/status
```

Expected sessions: `hv_control`, `dream_daq`, `daq_control`, `flask_server`,
`gas_watcher`, `he3_pressure_watcher`, `system_stats_watcher`, `beam_watcher`,
`stream1_watcher`, `backup_watcher`, `processor_watcher`, `qa_watcher`.

(`pedestal_watcher` is deliberately manual — a per-pedestal-run tool, and an empty 0-byte
pedestal FDF deadlocks it. `n1081b_timetag_watcher` is deliberately disabled; see
`n1081b/TIMETAG_WATCHER.md`.)

**To fill a gap, just re-run the boot script.** It is idempotent — every session that is
already up is skipped, and nothing is regenerated for it:

```bash
bash start_servers.sh
```

> This was **not** safe before 2026-07-22 — see trap 1 below.

---

## 2. Things that are NOT restarted by a reboot

Unaffected by a reboot of this PC, because they live elsewhere:

- **Gas flow.** The Bronkhorst MFCs hold their setpoints in hardware. `gas_watcher` only
  reads/logs/applies; its absence does not change the flow.
- **HV.** The CAEN crate is at `128.141.177.244` on the **CERN** network, independent of
  the DREAM subnet and of this PC's uptime. Channels stay as they were.
- **N1081B lock files.** `config/n1081b_access/*.lock` use `flock`, so locks held by
  processes the reboot killed are already released. Leftover `.lock` files are normal and
  harmless; only `*.quarantine.json` (with a future `until`) actually gates access.

---

## 3. The traps (all found on 2026-07-22)

### Trap 1 — re-running `start_servers.sh` used to type into live panes

`bash_scripts/start_tmux.sh` guarded against an existing session with `return 1`, but the
script is *executed*, not sourced. Bash refuses `return` outside a function, printed
`can only 'return' from a function or sourced script`, and **carried on**: `new-session`
failed as a duplicate and `send-keys` then delivered the command string into the existing
pane — i.e. straight into the live program's stdin (`hv_control`, RunCtrl via
`dream_daq_control.py`) — while printing `✅ Started` and exiting **0**.

Fixed (`exit 1`). Verify with a probe session whose pane is `cat > /tmp/x`; the file must
stay empty. Note `Restart=on-failure` on the unit made this reachable with no human at all.

### Trap 2 — restarting `flask_server` by hand leaves Flask DOWN

The tmux pane runs a **login shell**, which re-reads the profile and drops any venv the
caller had active. At boot this is invisible because `start_servers.sh` sources the venv
before the tmux server exists and all sessions inherit it — but killing and recreating just
`flask_server` from an ordinary shell gives `flask: command not found`, the pane falls back
to a prompt, and the GUI **and all Telegram alerting** are silently dead.

Fixed: `flask_app/start_flask.sh` now activates the venv and `cd`s to the repo root itself,
so this works from any shell:

```bash
tmux kill-session -t flask_server
bash_scripts/start_tmux.sh flask_server "flask_app/start_flask.sh" 5000
```

**Do not confirm Flask is up with `pgrep -f "flask run"`** — that pattern matches your own
shell command line and reports a healthy server when there is none. Check the listener:

```bash
ss -lptn 'sport = :5001'
```

### Trap 3 — the Kerberos keytab goes stale on every CERN password rotation

`kinit -kt` then fails with `Preauthentication failed` and **nothing warns you**: existing
tickets keep working on `kinit -R` renewals, so `beam_watcher`/`backup_watcher`/
`stream1_watcher` all look fine until the renewable lifetime (5 days) runs out and they
fail together.

The tell, without reading any log: **`klist`'s `renew until` stops advancing.** A
successful fresh kinit always opens a new 5-day window, so a frozen value means every
kinit since has failed.

Fix: `bash_scripts/regen_cern_keytab.sh` (prompts for the current CERN password).
To distinguish a stale key from the flaky-KDC false alarm, use
`KRB5_TRACE=/dev/stdout kinit -kt ~/.keytab/mx17_cern.keytab dneff@CERN.CH`: correct salt
(`CERN.CHdylan.neff`), correct etype, key `Retrieving ... 0/Success`, then a clean single
`Preauthentication failed` = the password was rotated. A flaky replica instead retries
across several KDC addresses and eventually succeeds.

### Trap 4 — processes that latch the host IP at start

`beam_watcher`'s PySpark resolves the driver IP once at JVM start and never re-resolves.
If the machine's IP changes afterwards, every poll fails forever with:

```
java.net.BindException: Cannot assign requested address:
Service 'sparkDriver' failed after 99 retries (starting from 5011)
```

The 99 retries make it look like a port clash. It is not — "cannot assign address" means
the address is gone. **Fix = restart the `beam_watcher` session**, nothing else.

This is why `daq-startup.service` must be ordered after the network is genuinely up:

```ini
[Unit]
Wants=network-online.target
After=network-online.target
```

Applied 2026-07-22 as `/etc/systemd/system/daq-startup.service.d/10-network-online.conf`.
Plain `network.target` is reached before any IP exists — the 07-22 boot ran its kinit at
15:44:00 and failed with `Resource temporarily unavailable` (KDC unreachable) six seconds
before `NetworkManager-wait-online` finished. The wait is cheap (6 s that boot, bounded by
`nm-online`'s 30 s timeout).

---

## 4. Alerting on a watcher that never came back

`flask_app/monitor.py` rules, auto-discovered by the `rule_` prefix; a rule absent from
`config/monitor_config.json` defaults to **enabled**.

| Rule | Default | Why |
|---|---|---|
| `rule_backup_watcher_dead` | **on** | Dead backup = completed runs never reach EOS. The only data-loss exposure of the three. |
| `rule_processor_watcher_dead` | off | Routinely stopped by hand during studies; delays work, loses nothing. |
| `rule_qa_watcher_dead` | off | Same. |

Toggle them in the GUI's monitor card. **Rule changes need a `flask_server` restart** — the
monitor runs inside the Flask process (see trap 2 for how to restart it correctly).
