#!/usr/bin/env python3
"""Build twice, prove reproducibility, and stage verified release artifacts."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EPOCH = 1_767_225_600  # 2026-01-01T00:00:00Z


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def project_version() -> str:
    payload = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))
    return str(payload["project"]["version"])


def build_once(destination: Path, epoch: int) -> list[Path]:
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONHASHSEED": "0",
            "SOURCE_DATE_EPOCH": str(epoch),
            "TZ": "UTC",
        }
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--no-isolation",
            "--sdist",
            "--wheel",
            "--outdir",
            str(destination),
            str(ROOT),
        ],
        cwd=ROOT,
        env=environment,
        check=True,
    )
    artifacts = sorted(path for path in destination.iterdir() if path.is_file())
    for artifact in artifacts:
        if artifact.name.endswith(".tar.gz"):
            normalize_sdist(artifact, epoch)
    return artifacts


def normalize_sdist(path: Path, epoch: int) -> None:
    """Canonicalize tar metadata and gzip headers without extracting the sdist."""

    entries: list[tuple[tarfile.TarInfo, bytes | None]] = []
    with tarfile.open(path, "r:gz") as source:
        for member in source.getmembers():
            extracted = source.extractfile(member) if member.isfile() else None
            entries.append((member, extracted.read() if extracted is not None else None))

    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w", format=tarfile.PAX_FORMAT) as target:
        for member, data in sorted(entries, key=lambda entry: entry[0].name):
            normalized_member = tarfile.TarInfo(member.name)
            normalized_member.mode = member.mode & 0o777
            normalized_member.uid = 0
            normalized_member.gid = 0
            normalized_member.uname = ""
            normalized_member.gname = ""
            normalized_member.mtime = epoch
            normalized_member.type = member.type
            normalized_member.linkname = member.linkname
            normalized_member.size = len(data) if data is not None else 0
            target.addfile(
                normalized_member,
                io.BytesIO(data) if data is not None else None,
            )

    normalized = path.with_name(f".{path.name}.normalized")
    with (
        normalized.open("wb") as raw,
        gzip.GzipFile(filename="", mode="wb", compresslevel=9, fileobj=raw, mtime=epoch) as archive,
    ):
        archive.write(tar_buffer.getvalue())
    normalized.replace(path)


def compare_builds(first: list[Path], second: list[Path]) -> None:
    first_by_name = {path.name: path for path in first}
    second_by_name = {path.name: path for path in second}
    if first_by_name.keys() != second_by_name.keys():
        raise RuntimeError(
            "build artifact sets differ: "
            f"first={sorted(first_by_name)}, second={sorted(second_by_name)}"
        )
    mismatches = [
        name
        for name in sorted(first_by_name)
        if sha256(first_by_name[name]) != sha256(second_by_name[name])
    ]
    if mismatches:
        raise RuntimeError(f"builds are not byte-reproducible: {mismatches}")


def validate_archive_metadata(artifacts: list[Path], epoch: int) -> None:
    expected_zip_time = datetime.fromtimestamp(epoch, tz=UTC).timetuple()[:6]
    for artifact in artifacts:
        if artifact.suffix == ".whl":
            with zipfile.ZipFile(artifact) as archive:
                wheel_timestamps = {info.date_time for info in archive.infolist()}
            if wheel_timestamps != {expected_zip_time}:
                raise RuntimeError(
                    f"wheel contains non-canonical timestamps: {sorted(wheel_timestamps)}"
                )
        elif artifact.name.endswith(".tar.gz"):
            with tarfile.open(artifact, "r:gz") as archive:
                tar_timestamps = {member.mtime for member in archive.getmembers()}
            if tar_timestamps != {epoch}:
                raise RuntimeError(
                    f"sdist contains non-canonical timestamps: {sorted(tar_timestamps)}"
                )


def clean_known_artifacts(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    for path in output.iterdir():
        if path.is_file() and (
            path.name.startswith("dutybell-")
            or path.name in {"SHA256SUMS", "release-manifest.json"}
        ):
            path.unlink()


def stage_release(artifacts: list[Path], output: Path, epoch: int) -> dict[str, Any]:
    clean_known_artifacts(output)
    copied: list[Path] = []
    for artifact in artifacts:
        destination = output / artifact.name
        shutil.copyfile(artifact, destination)
        copied.append(destination)

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "project": "dutybell",
        "version": project_version(),
        "source_date_epoch": epoch,
        "artifacts": [
            {"name": path.name, "sha256": sha256(path), "size": path.stat().st_size}
            for path in copied
        ],
    }
    manifest_path = output / "release-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    checksummed = [*copied, manifest_path]
    checksum_path = output / "SHA256SUMS"
    checksum_path.write_text(
        "".join(f"{sha256(path)}  {path.name}\n" for path in sorted(checksummed)),
        encoding="ascii",
        newline="\n",
    )
    return {
        "ok": True,
        "version": manifest["version"],
        "reproducible": True,
        "source_date_epoch": epoch,
        "output": str(output),
        "files": [path.name for path in [*copied, manifest_path, checksum_path]],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--source-date-epoch", type=int, default=DEFAULT_EPOCH)
    parser.add_argument("--gap-seconds", type=float, default=2.1)
    args = parser.parse_args()
    if args.source_date_epoch < 315_532_800:
        parser.error("source date epoch must be at or after 1980-01-01 for ZIP support")
    if args.gap_seconds < 2:
        parser.error("build gap must be at least two seconds")

    with tempfile.TemporaryDirectory(prefix="dutybell-build-") as directory:
        root = Path(directory)
        first = build_once(root / "first", args.source_date_epoch)
        time.sleep(args.gap_seconds)
        second = build_once(root / "second", args.source_date_epoch)
        compare_builds(first, second)
        validate_archive_metadata(first, args.source_date_epoch)
        result = stage_release(first, args.output.resolve(), args.source_date_epoch)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
