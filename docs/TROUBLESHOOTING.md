# Troubleshooting and repair

Start every diagnosis with the exact Python environment and database you intend to run:

```bash
python --version
python -m pip show dutybell
dutybell doctor --database /path/to/dutybell.db
```

`doctor` checks packaged web assets, SQLite integrity, and whether the requested host can bind a
local probe port. It does not alter existing rooms beyond initializing a new empty database when
the path does not exist.

## Runtime symptoms

### Another phone cannot connect

Confirm the server uses `--host 0.0.0.0`, use the host's LAN address rather than `127.0.0.1`, and
allow inbound TCP 8742 in the host firewall only for the trusted network. From the second device,
open `/healthz` first. If a reverse proxy is used, confirm it permits requests lasting at least 35
seconds so long polling is not cut off early.

### The room opens but authorization fails

Use the complete private link, including everything after `#`. Chat apps sometimes truncate or
rewrite fragments. The key cannot be recovered from SQLite; create a replacement room when the
link is lost or leaked.

### A button reports that someone else changed the timer

This is the expected conflict defense. DutyBell has already refreshed the current state. Check
that the original action is still appropriate and press it again. Do not automate retries without
reviewing the returned version and state.

### Alerts are late or silent

Keep the PWA open, grant notification permission, disable battery optimization for the browser if
appropriate, and perform one user gesture so browser audio can start. These steps improve delivery
but cannot make a browser a certified alarm. Use a dedicated alarm for safety-critical timing.

### SQLite reports busy, locked, or corrupt

Stop duplicate DutyBell processes first. Preserve the database and any `-wal` and `-shm` companions
before repair. Run `sqlite3 dutybell.db "PRAGMA integrity_check;"`. If it is not `ok`, restore a
known-good backup to a new path, run `dutybell doctor` against that path, and only then change the
service configuration. Never experiment on the only copy.

## Release gate repair matrix

| Failed stage | Likely cause | Repair, then rerun the whole gate |
| --- | --- | --- |
| `ruff format` | Formatting drift | Run `python -m ruff format .`; inspect the diff |
| `ruff check` | Lint or unsafe pattern | Fix the cited rule; do not blanket-ignore it |
| `mypy` | An interface no longer agrees with callers | Correct annotations and runtime behavior together |
| Python tests | State, persistence, HTTP, CLI, or archive regression | Rerun the named test with `python -m unittest tests.test_module.Test.test -v` |
| Coverage below 90% | New behavior lacks exercised success/failure paths | Add meaningful tests; do not lower `fail_under` |
| Node tests | Join-link, clock, duration, or participant regression | Run `node --test web_tests/*.test.mjs` and fix `core.mjs` |
| Secret scan | Credential-like content or attribution trailer | Remove/rotate the secret; inspect Git history before publishing |
| Reproducible build | Timestamp, order, locale, or generated metadata drift | Keep `SOURCE_DATE_EPOCH`; compare archives and normalize the producer |
| Clean-wheel install | Missing package data or undeclared dependency | Fix `pyproject.toml`; rebuild from a clean tree |
| Doctor | Bad SQLite path, package data, or bind address | Correct permissions/path/host and rerun `dutybell doctor` |
| HTTP smoke | Installed artifact differs from source or server failed | Inspect the captured subprocess output; test the wheel, not editable source |

If dependencies are missing, recreate rather than patching an unknown environment:

```bash
python -m venv .venv
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.lock
python -m pip install -e .
python scripts/release_check.py
```

Delete only the disposable `.venv` after confirming it is inside the project; never delete the
database or `dist/` evidence as a first troubleshooting step.
