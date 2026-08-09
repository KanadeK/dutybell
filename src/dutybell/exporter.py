"""Portable, deterministic DutyBell room evidence packs."""

from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import re
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dutybell.service import DutyBellService

PACK_FILES = ("README.txt", "events.csv", "room.json", "summary.html")
_HASH_LINE = re.compile(r"^([0-9a-f]{64})  ([A-Za-z0-9_.-]+)$")


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def _events_csv(events: list[dict[str, Any]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=("version", "created_at", "action", "actor", "payload_json"),
        lineterminator="\n",
    )
    writer.writeheader()
    for event in events:
        writer.writerow(
            {
                "version": event["version"],
                "created_at": event["created_at"],
                "action": event["action"],
                "actor": event["actor"],
                "payload_json": json.dumps(
                    event["payload"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ),
            }
        )
    return output.getvalue().encode("utf-8")


def _summary_html(room: dict[str, Any], events: list[dict[str, Any]], generated_at: str) -> bytes:
    rows = "".join(
        "<tr>"
        f"<td>{event['version']}</td>"
        f"<td>{html.escape(str(event['created_at']))}</td>"
        f"<td>{html.escape(str(event['action']))}</td>"
        f"<td>{html.escape(str(event['actor']))}</td>"
        "</tr>"
        for event in events
    )
    people = ", ".join(html.escape(str(item)) for item in room["participants"]) or "None"
    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>DutyBell export - {html.escape(str(room["name"]))}</title>
  <style>
    body {{ max-width: 920px; margin: 2rem auto; padding: 0 1rem; color: #1f2933;
      font: 16px/1.55 system-ui, sans-serif; }}
    h1 {{ margin-bottom: .2rem; }} .meta {{ color: #607080; }}
    dl {{ display: grid; grid-template-columns: max-content 1fr; gap: .35rem 1rem; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 1rem; }}
    th, td {{ border-bottom: 1px solid #d8dee6; padding: .55rem; text-align: left; }}
    th {{ background: #f3f6f8; }}
  </style>
</head>
<body>
  <h1>{html.escape(str(room["name"]))}</h1>
  <p class="meta">DutyBell room {html.escape(str(room["room_id"]))} · export {generated_at}</p>
  <dl>
    <dt>Status</dt><dd>{html.escape(str(room["status"]))}</dd>
    <dt>Interval</dt><dd>{room["interval_seconds"]} seconds</dd>
    <dt>Assignee</dt><dd>{html.escape(str(room["assignee"] or "None"))}</dd>
    <dt>Participants</dt><dd>{people}</dd>
    <dt>Version</dt><dd>{room["version"]}</dd>
  </dl>
  <h2>Event history</h2>
  <table>
    <thead><tr><th>Version</th><th>Time (UTC)</th><th>Action</th><th>Actor</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</body>
</html>
"""
    return document.encode("utf-8")


def export_room(
    service: DutyBellService,
    *,
    room_id: str,
    access_key: str,
    output: str | Path,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Export one authenticated room and its append-only history."""

    room = service.get_room(room_id, access_key)
    generated = generated_at or _utc_now()
    events = [event.to_dict() for event in service.store.list_events(room.room_id)]
    snapshot = service.snapshot(room)
    snapshot.pop("server_now_ms", None)
    snapshot.pop("remaining_ms", None)
    snapshot.pop("is_due", None)

    files: dict[str, bytes] = {
        "README.txt": b"".join(
            part.encode()
            for part in (
                "DutyBell portable room export\n"
                "\n"
                "This archive contains room state, event history, and a SHA-256 manifest.\n"
                "It does not contain the room access key. Verify it with: "
                "dutybell verify <archive>\n"
                "Times are recorded in UTC. This is an operational history, "
                "not a safety certification.\n"
            )
        ),
        "events.csv": _events_csv(events),
        "room.json": _json_bytes(
            {"schema_version": 1, "generated_at": generated, "room": snapshot, "events": events}
        ),
        "summary.html": _summary_html(snapshot, events, generated),
    }
    manifest = "".join(f"{_sha256(files[name])}  {name}\n" for name in sorted(files))
    files["SHA256SUMS"] = manifest.encode("ascii")

    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        output_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9, strict_timestamps=True
    ) as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            info.create_system = 3
            archive.writestr(info, files[name])

    return {
        "output": str(output_path.resolve()),
        "room_id": room.room_id,
        "events": len(events),
        "sha256": _sha256(output_path.read_bytes()),
        "files": sorted(files),
    }


def verify_export(path: str | Path) -> dict[str, Any]:
    """Verify archive structure and every hash without extracting files."""

    archive_path = Path(path)
    if archive_path.stat().st_size > 20 * 1024 * 1024:
        raise ValueError("archive exceeds the 20 MiB verification limit")

    with zipfile.ZipFile(archive_path, "r") as archive:
        names = [info.filename for info in archive.infolist()]
        if len(names) != len(set(names)):
            raise ValueError("archive contains duplicate file names")
        if any("/" in name or "\\" in name or name in {".", ".."} for name in names):
            raise ValueError("archive contains an unsafe file name")
        expected_names = set(PACK_FILES) | {"SHA256SUMS"}
        if set(names) != expected_names:
            missing = sorted(expected_names - set(names))
            extra = sorted(set(names) - expected_names)
            raise ValueError(f"archive file set mismatch; missing={missing}, extra={extra}")
        if sum(info.file_size for info in archive.infolist()) > 50 * 1024 * 1024:
            raise ValueError("archive expands beyond the 50 MiB verification limit")

        manifest_text = archive.read("SHA256SUMS").decode("ascii")
        declared: dict[str, str] = {}
        for line in manifest_text.splitlines():
            match = _HASH_LINE.fullmatch(line)
            if match is None:
                raise ValueError("SHA256SUMS contains a malformed line")
            digest, name = match.groups()
            if name in declared:
                raise ValueError("SHA256SUMS contains a duplicate entry")
            declared[name] = digest
        if set(declared) != set(PACK_FILES):
            raise ValueError("SHA256SUMS does not declare the expected files")

        for name, expected_digest in declared.items():
            actual_digest = _sha256(archive.read(name))
            if not hmac_compare(actual_digest, expected_digest):
                raise ValueError(f"hash mismatch for {name}")

        room_payload = json.loads(archive.read("room.json"))
        if room_payload.get("schema_version") != 1:
            raise ValueError("unsupported room export schema")

    return {
        "ok": True,
        "archive": str(archive_path.resolve()),
        "sha256": _sha256(archive_path.read_bytes()),
        "room_id": room_payload["room"]["room_id"],
        "events": len(room_payload["events"]),
        "verified_files": sorted(declared),
    }


def hmac_compare(left: str, right: str) -> bool:
    """Constant-time text comparison kept small for isolated testing."""

    import hmac

    return hmac.compare_digest(left, right)
