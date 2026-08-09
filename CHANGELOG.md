# Changelog

All notable changes use [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) conventions. This
project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-08-09

### Added

- Server-authoritative recurring timers with start, pause, resume, reset, stop, claim, configure,
  and acknowledge transitions.
- Shared acknowledgement and optional assignee rotation across long-polling clients.
- SQLite persistence, hashed room access keys, optimistic version conflicts, and append-only events.
- Responsive installable PWA with clock-offset correction, audio alerts, notifications, and history.
- Dependency-free JSON API and CLI, including diagnostics and deterministic verified exports.
- Python and JavaScript test suites, strict static checks, Docker packaging, CI, release automation,
  negative-path archive tests, and a reproducible-build gate.

[0.1.0]: https://github.com/KanadeK/dutybell/releases/tag/v0.1.0
