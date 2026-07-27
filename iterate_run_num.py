#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RETIRED 2026-07-27 — do not use. Superseded by run_num.py.

This script picked the next run number by REWRITING the `self.run_name = '...'` line in the
tracked `run_config_beam.py` source. That was wrong in three ways:

  * it only ever worked for the base config — every generator in `run_configs/` sets its
    own `run_name` after calling super(), so editing the base changed nothing for them;
  * it left the repo dirty on every single run start;
  * Flask invoked it as `Popen(...)` + `sleep(0.2)` and read the resulting name back in a
    SEPARATE request, so the confirmation popup could name a different run than the one
    that actually started.

That is why `bash_scripts/start_run.sh` carried it commented out as "Not working".

Replacement: `run_num.py` (allocate / peek, monotonic, both run trees + a high-water mark
that survives space_watcher deleting the newest runs), plus the `RUN_NUM` environment
variable that every config generator now honours — so the number is chosen once and no
source file is touched. The GUI uses it via `/run/prepare`; switch_mode --go uses it via
`run_num.allocate()`.

Kept as a tombstone rather than deleted so that anyone following an old doc or an old shell
history lands here instead of on a mysteriously missing file.
"""
import sys

print(__doc__, file=sys.stderr)
print('RETIRED: use run_num.py (see the note above). Nothing was changed.', file=sys.stderr)
sys.exit(2)
