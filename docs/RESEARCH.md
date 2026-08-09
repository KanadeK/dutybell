# Opportunity research

This note records the pre-build research performed on 2026-08-08. It supports a product decision;
it is not proof that no similar software exists or a promise of GitHub stars.

## Demand signal

A current niche-tool discussion included a concrete request for alarms, countdowns, and stopwatches
shared between two phones, where silencing either phone silences both; the example was coordinating
dog potty training. Earlier Android and cross-platform requests separately asked for alarms or
timers that either family device could dismiss:

- [Niche apps people wish existed](https://www.reddit.com/r/software/comments/1lzp0vp/what_are_some_very_niche_apps_or_tools_you_wish/)
- [Shared alarm between two devices](https://www.reddit.com/r/androidapps/comments/c6oeib)
- [Shared timer between Android and iPhones](https://www.reddit.com/r/productivity/comments/jrxa3w)

The recurring pattern is not simply “a timer.” It is a shared responsibility protocol: every
participant sees one canonical deadline, one person handles it, duplicate alerts end everywhere,
and the next duty can rotate.

## Competition sampling

GitHub searches sampled the phrases `shared duty timer`, `family relay timer`, `household handoff
timer`, `pet potty shared timer`, and `shared recurring timer acknowledge`; no exact public
repository was returned at the time. The broader phrase `multi device timer` did return
[gsmafra/multi-device-timer](https://github.com/gsmafra/multi-device-timer), a zero-star prototype
whose README describes mostly experimental development. [TimeMomo](https://timemomo.techformist.com/)
addresses synchronized presenter/stage timers rather than household acknowledgement and rotation.

Nearby ideas were rejected when direct competition was already strong or an exact product already
existed. “Block AI-generated web content” overlapped a repository with hundreds of stars; product
recall monitoring overlapped existing commercial services and public implementation plans. The
household relay timer was both technically deliverable without a shell UI and distinct from the
author's existing local project inventory.

## Why this implementation may earn attention

- The README can demonstrate an instantly understandable two-phone moment.
- Self-hosting, zero runtime dependencies, one SQLite file, and no analytics reduce adoption risk.
- Acknowledge-and-rotate is more specific than a generic timer and reusable beyond the motivating
  dog-care example.
- The project includes real operational details often missing from a prototype: conflicts,
  recovery, deterministic exports, Docker, CI, negative tests, and release checks.

Attention still depends on outreach, timing, screenshots, issue response, and continued usefulness.
The repository therefore avoids star forecasts and documents the research so future maintainers
can recheck the landscape rather than treating it as permanent fact.
