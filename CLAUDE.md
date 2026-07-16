# nTOF x17 DAQ — repo guidance for Claude

## N1081B logic modules (192.168.10.240–.245) — HANDLE WITH CARE
These boards wedge (needing hours of isolation or a physical reboot) if talked to
carelessly, and there is no reliable remote reboot. **Any script that reaches a board
MUST go through `n1081b/n1081b_session.py`** (never open a raw `n1081b_sdk` connection in
a control script), **only one process may talk to a board at a time**, and **never
SIGKILL a process mid-session**. Full rules: **`n1081b/CLAUDE.md`** — read it before any
board work. Root cause + recovery: `n1081b/HANDOFF_2026-07-15_wedge_root_cause.md`.

Before launching a board-touching agent, check `config/n1081b_access/` (holder +
quarantine state) so two agents don't hit the same board at once.
