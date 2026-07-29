# run_configs/

Experiment-specific **run-config generators**. Each `run_config_*.py` here builds a
JSON into `config/json_run_configs/` (that JSON is what actually drives a run).

## How to run them
Always invoke **from the repo root** so the CWD-relative output path resolves:

```bash
.venv/bin/python run_configs/run_config_<name>.py
```

Each file carries a small `sys.path` shim so it can import the shared library
(`run_config_beam` / `run_config_base`), which live in the **repo root** — they are
imported by `daq_control.py` and `iterate_run_num.py` and intentionally stay there.

> Moved out of the repo root on 2026-07-24 for tidiness. Older docs/handoffs may still
> refer to these as `run_config_<name>.py` (bare) — prepend `run_configs/`.
