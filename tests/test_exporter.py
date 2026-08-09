from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path

from dutybell.exporter import export_room, verify_export
from tests.helpers import room_payload, temporary_service


class ExportTests(unittest.TestCase):
    @staticmethod
    def archive_members(path: Path) -> dict[str, bytes]:
        with zipfile.ZipFile(path) as archive:
            return {name: archive.read(name) for name in archive.namelist()}

    @staticmethod
    def write_members(path: Path, members: dict[str, bytes]) -> None:
        with zipfile.ZipFile(path, "w") as archive:
            for name, content in members.items():
                archive.writestr(name, content)

    def test_export_is_deterministic_when_inputs_and_time_are_fixed(self) -> None:
        with (
            temporary_service() as (service, _clock, _database),
            tempfile.TemporaryDirectory() as directory,
        ):
            room, key = service.create_room(**room_payload())
            first = Path(directory) / "first.zip"
            second = Path(directory) / "second.zip"
            export_room(
                service,
                room_id=room.room_id,
                access_key=key,
                output=first,
                generated_at="2026-01-01T00:00:00Z",
            )
            export_room(
                service,
                room_id=room.room_id,
                access_key=key,
                output=second,
                generated_at="2026-01-01T00:00:00Z",
            )
            self.assertEqual(first.read_bytes(), second.read_bytes())
            result = verify_export(first)
            self.assertTrue(result["ok"])
            self.assertEqual(result["events"], 1)

    def test_export_never_contains_access_key(self) -> None:
        with (
            temporary_service() as (service, _clock, _database),
            tempfile.TemporaryDirectory() as directory,
        ):
            room, key = service.create_room(**room_payload())
            archive = Path(directory) / "room.zip"
            export_room(service, room_id=room.room_id, access_key=key, output=archive)
            self.assertNotIn(key.encode(), archive.read_bytes())

    def test_verifier_detects_tampered_member(self) -> None:
        with (
            temporary_service() as (service, _clock, _database),
            tempfile.TemporaryDirectory() as directory,
        ):
            room, key = service.create_room(**room_payload())
            original = Path(directory) / "original.zip"
            tampered = Path(directory) / "tampered.zip"
            export_room(service, room_id=room.room_id, access_key=key, output=original)
            with zipfile.ZipFile(original) as source, zipfile.ZipFile(tampered, "w") as target:
                for info in source.infolist():
                    data = source.read(info.filename)
                    if info.filename == "events.csv":
                        data += b"tampered"
                    target.writestr(info.filename, data)
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                verify_export(tampered)

    def test_verifier_rejects_extra_and_unsafe_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "unsafe.zip"
            with zipfile.ZipFile(archive, "w") as target:
                target.writestr("../room.json", b"{}")
            with self.assertRaisesRegex(ValueError, "unsafe"):
                verify_export(archive)

    def test_verifier_rejects_oversize_duplicates_and_wrong_file_set(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            oversized = root / "oversized.zip"
            with oversized.open("wb") as handle:
                handle.seek(20 * 1024 * 1024)
                handle.write(b"x")
            with self.assertRaisesRegex(ValueError, "20 MiB"):
                verify_export(oversized)

            duplicate = root / "duplicate.zip"
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                with zipfile.ZipFile(duplicate, "w") as archive:
                    archive.writestr("room.json", b"{}")
                    archive.writestr("room.json", b"{}")
            with self.assertRaisesRegex(ValueError, "duplicate file"):
                verify_export(duplicate)

            incomplete = root / "incomplete.zip"
            self.write_members(incomplete, {"room.json": b"{}"})
            with self.assertRaisesRegex(ValueError, "file set mismatch"):
                verify_export(incomplete)

    def test_verifier_validates_manifest_and_schema(self) -> None:
        with (
            temporary_service() as (service, _clock, _database),
            tempfile.TemporaryDirectory() as directory,
        ):
            room, key = service.create_room(**room_payload())
            root = Path(directory)
            original = root / "original.zip"
            export_room(service, room_id=room.room_id, access_key=key, output=original)
            original_members = self.archive_members(original)
            manifest = original_members["SHA256SUMS"]

            variants = {
                "malformed": b"not a checksum line\n",
                "duplicate": manifest + manifest.splitlines(keepends=True)[0],
                "missing": b"".join(manifest.splitlines(keepends=True)[:-1]),
            }
            for name, changed_manifest in variants.items():
                with self.subTest(name=name):
                    path = root / f"{name}.zip"
                    members = dict(original_members)
                    members["SHA256SUMS"] = changed_manifest
                    self.write_members(path, members)
                    expected = "malformed|duplicate|expected files"
                    with self.assertRaisesRegex(ValueError, expected):
                        verify_export(path)

            unsupported = root / "unsupported.zip"
            members = dict(original_members)
            payload = json.loads(members["room.json"])
            payload["schema_version"] = 99
            members["room.json"] = json.dumps(
                payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            ).encode()
            digest = hashlib.sha256(members["room.json"]).hexdigest()
            manifest_lines = members["SHA256SUMS"].decode("ascii").splitlines()
            members["SHA256SUMS"] = (
                "\n".join(
                    f"{digest}  room.json" if line.endswith("  room.json") else line
                    for line in manifest_lines
                )
                + "\n"
            ).encode("ascii")
            self.write_members(unsupported, members)
            with self.assertRaisesRegex(ValueError, "schema"):
                verify_export(unsupported)


if __name__ == "__main__":
    unittest.main()
