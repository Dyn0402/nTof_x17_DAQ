#!/usr/bin/env python3
"""In-process N1081B scan control for daq_control.

Replaces the standalone ``n1081b_scan_watcher.py`` PROCESS for data runs.
daq_control applies each sub-run's trigger/mesh config ITSELF, right before
taking data, so the modulation is part of the run and can never be "forgotten"
— the failure that silently corrupted run_30 and run_33 (Singles/Doubles leaked
into the veto-gated "random" trigger because the watcher was never started; see
the ``randomizer-pathology-root-cause`` note).

Because the daq_control loop already sequences the sub-runs, NONE of the
standalone watcher's inter-process handshake is needed here — no ``.pause_run``
lever, no ``.subrun_complete`` boundary polling. This class just:

  * decides whether the run needs modulation at all (``mode='auto'`` -> yes iff a
    sub-run's leading scan tag maps to a ``scans`` entry in the schedule; else
    honour an explicit ``on``/``off``),
  * snapshots the live board state once at run start (for restore-on-exit),
  * applies the per-sub-run tag config, verified by read-back, and
  * restores the board to its found state on exit.

Tag mapping is identical to the watcher: ``tag = sub_run_name.split('_')[0]`` and
``cfg = n1081b_scan_schedule.json['scans'][tag]``. The board primitives
(snapshot / apply / restore, all read-back verified, multi-board) are reused
straight from ``n1081b_scan_watcher`` so there is a single source of truth for
how the hardware is driven.

The standalone ``n1081b_scan_watcher.py`` is retained for MANUAL board setup /
restore / dry-run (and as a fallback). Do NOT run it during a daq_control run
now that control is inline — both would drive the same board.
"""
import json
import os
import sys

_N1081B_DIR = os.path.dirname(os.path.abspath(__file__))
if _N1081B_DIR not in sys.path:
    sys.path.insert(0, _N1081B_DIR)


class N1081BScanControl:
    """Applies per-sub-run N1081B trigger/mesh config inline.

    Construction is cheap and board-free (only reads the schedule JSON and the
    run's sub-run tags) — the N1081B SDK is touched only by :meth:`start`,
    :meth:`apply_for` and :meth:`restore`, which reach the board network. A run
    that needs no modulation (``needed`` is False) is a total no-op.

    ``mode`` (run config ``n1081b_scan``) sets the guarantee level:
      * ``'on'``  — the run DECLARES it is a scan run. Fully fail-closed: a
        missing/corrupt schedule or any sub-run tag with no schedule entry makes
        the caller REFUSE to start. Use this for every real scan run.
      * ``'auto'`` (default) — best-effort: modulate iff a sub-run tag matches a
        schedule scan. It CANNOT tell a typo'd/mis-cased scan run (no tag matches)
        from a genuine non-scan run (also no tag matches), so it never blocks and
        never strongly guarantees. Scan runs must therefore use ``'on'``, not rely
        on ``'auto'``.
      * ``'off'`` — unconditional no-op; never imports board code or reads the
        schedule, so it always constructs (deliberate escape hatch).
    """

    def __init__(self, sub_runs, schedule_path=None, mode='auto', dry_run=False,
                 log=print):
        self._log = log
        self.dry_run = dry_run
        self.mode = str(mode).lower()
        self.sched = None
        self.snap = None
        self.active = False        # snapshot taken -> restore owed on exit
        self._last_tag = None
        self.tags = [sr['sub_run_name'].split('_')[0] for sr in (sub_runs or [])]
        # Set before the mode='off' early return so summary()/repr never touch an
        # unset attribute (the non-off path overrides this with the resolved default).
        self.schedule_path = schedule_path

        # mode='off' is an unconditional no-op: never import board code or read the
        # schedule, so it ALWAYS constructs even if those are broken. This is the
        # deliberate escape hatch for running without trigger modulation.
        if self.mode in ('off', 'false', 'none', '0'):
            self.needed = False
            return

        # Reuse the watcher's verified board primitives (pure-python import; the
        # SDK is imported lazily inside these, so importing here needs no board).
        from n1081b_scan_watcher import (DEFAULT_SCHEDULE, apply_scan,
                                         snapshot_targets, restore_snapshot,
                                         _fmt_cfg)
        self._apply_scan = apply_scan
        self._snapshot_targets = snapshot_targets
        self._restore_snapshot = restore_snapshot
        self._fmt_cfg = _fmt_cfg

        self.schedule_path = schedule_path or DEFAULT_SCHEDULE
        # A missing OR corrupt schedule means we have no scan definitions. Under
        # forced-ON that is fatal (a declared scan run cannot control its trigger) ->
        # RAISE so the caller refuses to start. Under 'auto' it is downgraded to a
        # no-op so a genuine NON-scan run (which never needed the schedule) is not
        # blocked by an unrelated bad file — 'auto' is best-effort; 'on' is the
        # guarantee (see class docstring / run config n1081b_scan).
        try:
            with open(self.schedule_path) as f:
                self.sched = json.load(f)
        except (FileNotFoundError, ValueError) as e:
            self.sched = None
            forced_on = self.mode in ('on', 'true', '1')
            kind = 'missing' if isinstance(e, FileNotFoundError) else f'CORRUPT ({e})'
            if forced_on:
                raise RuntimeError(f'n1081b_scan forced ON but schedule '
                                   f'{self.schedule_path} is {kind} — cannot control '
                                   f'the trigger.')
            self._log(f'[n1081b] schedule {self.schedule_path} is {kind} — scan '
                      f'control disabled for this run. If this IS a scan run, fix the '
                      f'schedule and set n1081b_scan="on".')

        self.needed = self._decide(self.mode)

    # ------------------------------------------------------------------ decision
    def _decide(self, mode):
        m = str(mode).lower()
        if m in ('off', 'false', 'none', '0'):
            return False
        if self.sched is None:
            return False   # forced-on + no schedule already raised in __init__
        scans = set(self.sched.get('scans', {}))
        if m in ('on', 'true', '1'):
            return True
        # auto: needed iff any sub-run's leading tag maps to a scan entry
        return bool(set(self.tags) & scans)

    def unknown_tags(self):
        """Run tags with NO schedule entry (would be left un-modulated). Empty
        unless the run mixes scan tags with plain tags."""
        if not self.needed:
            return []
        scans = set(self.sched.get('scans', {}))
        return sorted(t for t in dict.fromkeys(self.tags) if t not in scans)

    def summary(self):
        seen = list(dict.fromkeys(self.tags))
        scans = set(self.sched.get('scans', {})) if self.sched else set()
        modu = [t for t in seen if t in scans]
        sched_name = os.path.basename(self.schedule_path) if self.schedule_path else 'none'
        return (f'needed={self.needed} tags={seen} modulated={modu} '
                f'schedule={sched_name}')

    # ------------------------------------------------------------------- actions
    def start(self):
        """Snapshot the live board state once for restore-on-exit. Raises on board
        failure so the caller can refuse to start a scan run blind."""
        if not self.needed or self.dry_run:
            return
        self.snap = self._snapshot_targets(self.sched)
        self.active = True
        n = sum(len(chs) for chs in self.snap.values())
        self._log(f'[n1081b] snapshotted {n} target channel(s) for restore-on-exit.')

    def apply_for(self, sub_run):
        """Apply this sub-run's tag config, verified by read-back. Returns True if
        applied/verified (or not needed / tag not in schedule); False if the apply
        did not verify (caller should hold the run and retry)."""
        if not self.needed:
            return True
        tag = sub_run['sub_run_name'].split('_')[0]
        scans = self.sched.get('scans', {})
        if tag not in scans:
            self._log(f'[n1081b] {sub_run["sub_run_name"]}: tag {tag!r} not in schedule '
                      f'— leaving trigger AS-IS.')
            return True
        cfg = scans[tag]
        self._log(f'[n1081b] applying {tag}: {self._fmt_cfg(cfg)}')
        ok = self._apply_scan(self.sched, cfg, dry_run=self.dry_run)
        if ok:
            self._last_tag = tag
        return ok

    def restore(self):
        """Restore the board to its snapshotted found-state. Safe to call always.
        Surfaces failure LOUDLY — a swallowed restore failure would leave the boards
        in the last scan config, silently mis-triggering the NEXT run."""
        if self.active and self.snap is not None and not self.dry_run:
            self._log('[n1081b] restoring board to found state.')
            ok = False
            try:
                ok = self._restore_snapshot(self.sched, self.snap, dry_run=self.dry_run)
            except Exception as e:  # noqa: BLE001
                self._log(f'[n1081b] !! restore raised: {e!r}')
            if not ok:
                self._log('[n1081b] !!!! BOARD NOT RESTORED to found state — the '
                          'trigger may be left in the last scan config. Reset the '
                          'N1081B before the next run (e.g. '
                          'n1081b_scan_watcher.py --restore-baseline).')
        self.active = False
