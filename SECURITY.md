# Security Policy

## Supported versions

Security fixes are provided for the latest published minor version.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting for this repository. Do not open a public
issue containing an exploit, private join link, database, or access key. Include affected version,
deployment shape, reproduction steps, impact, and a minimal proof of concept. You should receive
an acknowledgement within seven days.

## Deployment boundary

DutyBell is designed for a trusted household network. A room key grants read/write access to one
room and cannot be revoked independently in v0.1.0; create a replacement room if a link leaks.
For internet access, terminate TLS at a maintained reverse proxy, restrict room creation with
`DUTYBELL_CREATE_TOKEN`, keep the SQLite file private, and back it up using SQLite-aware tooling.

DutyBell is not a certified alarm and must not be used for medication, fire, industrial, emergency,
or other safety-critical duties. See [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md).
