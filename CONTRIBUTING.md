# Contributing

Thanks for helping DutyBell stay small, dependable, and understandable.

## Before opening a change

1. Search existing issues and describe the household workflow, not only the proposed UI.
2. Keep the server authoritative for time and state. A client-only state transition is a bug.
3. Do not add a runtime dependency without explaining why the standard library cannot safely do
   the job and what the maintenance cost is.
4. Never position DutyBell as a safety-critical alarm.

## Development setup

```bash
python -m venv .venv
python -m pip install -r requirements-dev.lock
python -m pip install -e .
python scripts/release_check.py --skip-build
```

The default branch requires the CI workflow. Add a regression test for behavior changes. HTTP
changes should test success, authentication failure, validation failure, and version conflicts as
applicable. State-machine changes should test persistence and event history.

Before submitting, run `python scripts/release_check.py`. A pull request should explain observable
behavior, data migration impact, threat-model impact, and the exact acceptance commands used.

## Commit and review hygiene

- Keep commits focused and use an imperative summary.
- Do not commit databases, private join links, tokens, generated archives, or virtual environments.
- Do not weaken the coverage, typing, secret, or reproducibility gates to make a change pass.
- Maintainers may ask for a smaller patch when an unrelated refactor obscures a behavior change.
