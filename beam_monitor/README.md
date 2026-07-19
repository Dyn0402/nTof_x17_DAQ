# Beam monitor — live n_TOF beam intensity from NXCALS/Timber

Logs the proton intensity delivered to the n_TOF target and tells the DAQ GUI
whether the beam is on. The data source is **NXCALS** (the database behind
Timber), queried directly from this machine — the same numbers you'd export
from timber.cern.ch by hand.

## What runs where

- `../beam_watcher.py` — standalone process in the `beam_watcher` tmux session
  (GUI button "Start Beam Watcher"). Sole owner of the NXCALS/Spark session.
- `beam_intensity_controller.py` — the actual monitor class + shared paths.
  Import-safe from the Flask venv (pytimber is only imported inside the
  watcher process).
- Per-day CSVs: `beam_monitor/logs/beam_intensity_YYYY-MM-DD.csv`
  (`timestamp, unix_ts, intensity_e10`) — every TOF cycle NXCALS logs,
  zeros included, so this is the same record Timber would give you.
- Published state: `../config/beam_state.json`, served by `/beam/status`
  and the Shift Overview "n_TOF Beam" card. History: `/beam/history?hours=6`.

## The variable

`FTN.BCT477:AcquisitionLatest:totalIntensity` — the **last** beam-current
transformer in the FTN line before the n_TOF target, i.e. protons actually
on target. Units: **1e10 protons per pulse** (dedicated pulse ≈ 850 = 8.5e12
p, parasitic ≈ 400–700). One point per TOF cycle (~2 s granularity), NXCALS
latency ~0.5–1 min. Points below `PULSE_THRESHOLD_E10` (50) are empty
cycles, not pulses.

**Do NOT use `F16.BCT372.TOF:INTENSITY` (or `CPS.NTOF:INTENSITY`) for beam
on/off** — they sit upstream (TT2 / PS extraction) and count TOF-destination
pulses that can be stopped before the target. Observed 2026-07-10
17:20–18:20: the target received nothing for an hour (BCT477 ≈ 0) while
BCT372.TOF kept logging ~6.9e12 pulses. The watcher used BCT372 until ~18:45
that day; that period's CSV is archived as `logs/bct372_beam_intensity_*.csv`.

Beam ON = a real pulse within `BEAM_OFF_GAP_S` (180 s — must stay above
NXCALS latency + normal supercycle gaps).

## The NXCALS venv (~/venvs/nxcals)

pytimber ≥4 drags in PySpark + a JVM bridge (~1 GB), so it lives in its own
venv, NOT the DAQ venv. Rebuild recipe (system python3.12 has no ensurepip,
hence get-pip):

```bash
python3 -m venv --without-pip ~/venvs/nxcals
curl -sS https://bootstrap.pypa.io/get-pip.py | ~/venvs/nxcals/bin/python
~/venvs/nxcals/bin/pip install setuptools pytimber \
    --index-url https://acc-py-repo.cern.ch/repository/vr-py-releases/simple \
    --extra-index-url https://pypi.org/simple \
    --trusted-host acc-py-repo.cern.ch
```

Gotchas learned the hard way (2026-07-10):

- `--trusted-host acc-py-repo.cern.ch` is required — the repo's CERN CA cert
  is not in this machine's trust store. Without it pip silently falls back to
  PyPI, whose `pytimber` is a stub that refuses to build.
- Do **NOT** install `pyarrow` in this venv. With pyarrow present, PySpark
  uses its Arrow path, which crashes on this box's Java 21
  (`sun.misc.Unsafe ... not available`). Without pyarrow it falls back to
  plain conversion, which is fine for our tiny queries.
- `setuptools` is needed (Python 3.12 removed distutils; PySpark still wants it).
- First query after startup ≈ 30–60 s (local Spark spin-up); after that each
  poll is ~1–2 s.

## Authentication (Kerberos) — reboot-safe via keytab

Queries authenticate with the user's Kerberos ticket in the default cache
(`/tmp/krb5cc_1000`), shared with the EOS backup. This is now **reboot-safe**
(set up 2026-07-19); before that a reboot wiped the ticket cache and the
watcher idled in a Spark auth error until someone ran `kinit` by hand.

How the ticket stays valid with no human in the loop:

- **Keytab** `~/.keytab/mx17_cern.keytab` — lets any process run
  `kinit -kt ~/.keytab/mx17_cern.keytab dneff@CERN.CH` with **no password,
  gpg, or tty**. This is what makes unattended boot work.
- **At boot** `../start_servers.sh` (run by `daq-startup.service`) does that
  `kinit -kt` *before* launching the watchers.
- **Renewal** a user crontab entry (`crontab -l`, fires at minute :17) re-runs
  `kinit -kt` hourly — each call is a fresh 25 h ticket, so it never lapses on
  long uptime. The watcher's own periodic `kinit -R` is now belt-and-suspenders.

**Regenerate the keytab after any CERN password change** (a rotated password
silently kills it — the 2026-07-19 outage):

```bash
bash_scripts/regen_cern_keytab.sh   # prompts for the CERN password once
```

⚠️ **AD salt gotcha:** the principal `dneff@CERN.CH` is a UPN *alias*; the
AD account's key salt is `CERN.CHdylan.neff` (from the sAMAccountName
`dylan.neff`), **not** ktutil's default `CERN.CHdneff`. A keytab built with
the default salt authenticates with the wrong key and fails preauth silently.
`regen_cern_keytab.sh` passes `-s CERN.CHdylan.neff` explicitly. To rediscover
the salt if the account ever changes:
`KRB5_TRACE=/dev/stdout kinit dneff@CERN.CH` and grep for `salt`.

The legacy `~/.cern_pass.gpg` password file remains only as a manual
last-resort fallback in `backup_watcher` (it needs a gpg-agent passphrase a
reboot un-caches, so it can't self-heal — that was the whole problem).

The state file exposes `krb_valid_until`; the shift card warns when < 12 h
remain, and the Telegram monitor's `rule_beam_watcher_dead` fires once
queries actually start failing.

> Heads-up: an intermittent `kinit: Password incorrect` on a *correct*
> password usually means a flaky KDC replica (`cerndc.cern.ch` resolves to
> several IPv6 KDCs), not a bad password — just retry.
