# DutyBell

**One household timer. Every screen in sync. One acknowledgement ends the bell for everyone.**

[![CI](https://github.com/KanadeK/dutybell/actions/workflows/ci.yml/badge.svg)](https://github.com/KanadeK/dutybell/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/KanadeK/dutybell)](https://github.com/KanadeK/dutybell/releases/latest)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776ab)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-0f766e.svg)](LICENSE)

[简体中文](README.zh-CN.md) · [Architecture](docs/ARCHITECTURE.md) · [API](docs/API.md) · [Troubleshooting](docs/TROUBLESHOOTING.md)

DutyBell is a tiny, dependency-free, self-hosted web app for recurring responsibilities that
cross phones and people: dog breaks, laundry checks, oven checks, plant rounds, or any other
non-safety-critical household duty. It is a working synchronization service, not a UI mockup:
the Python server owns the clock, SQLite persists state and an append-only history, long polling
updates every client, and optimistic versions prevent two devices from silently overwriting each
other.

## Why it exists

Ordinary timers belong to one device. Shared to-do lists record a task, but do not behave like a
bell. DutyBell fills the small gap in between:

- Start, pause, resume, reset, stop, or acknowledge from any connected screen.
- Acknowledge once; every client sees the new state and stops alerting.
- Repeat automatically and rotate the next duty holder.
- Keep an auditable event history without an account, cloud database, or analytics SDK.
- Export a deterministic ZIP with JSON, CSV, HTML, and SHA-256 verification.
- Install the responsive web app on a phone; no app-store package is required.

DutyBell is **not** for medication, fire, industrial, emergency, or other safety-critical alarms.
Browsers and networks may suspend or disconnect. Use a certified alarm for consequences involving
health or safety.

## Five-minute start

You need Python 3.11 or newer.

```bash
git clone https://github.com/KanadeK/dutybell.git
cd dutybell
python -m venv .venv
```

Activate the environment, then install and run:

```bash
python -m pip install .
dutybell doctor --database ./data/dutybell.db
dutybell serve --host 0.0.0.0 --port 8742 --database ./data/dutybell.db
```

Open `http://127.0.0.1:8742` on the host. On another device on the same trusted network, open
`http://HOST-LAN-IP:8742`, create or join a room, and save the generated private join link.

The link secret lives after `#`, so browsers do not send it in ordinary HTTP requests or server
logs. The app sends it as an authorization header after loading. Plain HTTP still exposes traffic
to the local network: use HTTPS through a reverse proxy before exposing DutyBell beyond a trusted
LAN.

### Docker Compose

```bash
docker compose up --build
```

Data persists in the named `dutybell-data` volume. To restrict who can create rooms:

```bash
DUTYBELL_CREATE_TOKEN=choose-a-long-random-value docker compose up --build
```

Existing room access still requires each room's private join key.

## Command line

```text
dutybell serve   Run the API and installable web app
dutybell create  Create a room directly in SQLite
dutybell status  Read current synchronized state
dutybell action  Apply a version-checked state transition
dutybell export  Build a portable, checksummed room archive
dutybell verify  Verify an archive without extracting it
dutybell doctor  Check SQLite, packaged assets, and local socket binding
```

Create a running relay timer without the browser:

```bash
dutybell create "Dog break" --seconds 7200 --participants "Alex,Sam" --start
```

The response contains a room code, a one-time plaintext access key, and a private join URL. SQLite
stores only a SHA-256 digest of the key. See [examples/README.md](examples/README.md) for API-ready
sample payloads.

## What “synchronized” means

The server stores an absolute UTC deadline, not a client-side countdown. Every response includes
server time; clients estimate clock offset from the request midpoint. A room has a monotonically
increasing version. Mutations must include the last version the caller observed, so simultaneous
actions yield one accepted update and one explicit `409 conflict` rather than lost data.

```mermaid
sequenceDiagram
    participant A as Phone A
    participant S as DutyBell
    participant D as SQLite
    participant B as Phone B
    A->>S: acknowledge(expected_version=7)
    S->>D: BEGIN IMMEDIATE; update to v8; append event
    D-->>S: committed room v8
    S-->>A: room v8, next assignee
    B->>S: long poll after=7
    S-->>B: changed=true, room v8
    B->>B: stop local alert and render v8
```

## Data and privacy

- All state stays in the configured SQLite file.
- There are no user accounts, cookies, trackers, third-party fonts, CDNs, or runtime dependencies.
- Room IDs are discoverability hints, not secrets. The access key is the authorization secret.
- Anyone holding a private join link can read and change that room; rotate by creating a new room.
- Event history records the actor name supplied by the client. It is attribution, not identity
  proof.
- Room exports deliberately exclude the access key.

Read the complete [threat model](docs/THREAT_MODEL.md) before public deployment.

## Acceptance gate

Install the pinned developer tools and run the single fail-fast release check:

```bash
python -m pip install -r requirements-dev.lock
python scripts/release_check.py
```

It verifies formatting, lint, strict typing, 90% branch-aware coverage, browser-core tests, secret
scanning, documentation/workflow syntax and local links, two deliberately separated byte-identical
builds, archive timestamps, clean-wheel installation, CLI diagnostics, and a real HTTP
create/read/ack/conflict flow. Successful output ends with `RELEASE CHECK PASSED` and places
verified artifacts plus `SHA256SUMS` in `dist/`.

Fast local checks while developing:

```bash
python -m ruff format --check .
python -m ruff check .
python -m mypy src tests scripts
python -m coverage run -m unittest discover -s tests -v
python -m coverage report -m
node --test web_tests/*.test.mjs
```

If the gate fails, do not skip the check. Start with the named failing stage and follow the
[repair matrix](docs/TROUBLESHOOTING.md#release-gate-repair-matrix), then rerun the complete command.

## Project status

Version 0.1.0 is intentionally small but complete: one process, one SQLite file, multi-client
synchronization, installable PWA, CLI, deterministic exports, tests, Docker packaging, and CI.
See [CHANGELOG.md](CHANGELOG.md) for shipped behavior and [the research note](docs/RESEARCH.md) for
the demand/competition evidence and its limits.

Contributions are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and
the [Code of Conduct](CODE_OF_CONDUCT.md) first.
