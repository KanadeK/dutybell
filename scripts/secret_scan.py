#!/usr/bin/env python3
"""Fail when publishable files contain common credential material."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_FILE_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class Finding:
    file: str
    line: int
    rule: str


PATTERNS = {
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "github-token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    "aws-access-key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "slack-token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    "coauthor-trailer": re.compile(r"Co-" r"authored-by:", re.IGNORECASE),
    "literal-secret": re.compile(
        r"(?i)\b(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)\b"
        r"\s*[:=]\s*['\"]([A-Za-z0-9_./+=-]{16,})['\"]"
    ),
}

TEXT_SUFFIXES = {
    ".css",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".mjs",
    ".py",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
TEXT_NAMES = {"Dockerfile", "LICENSE"}


def candidate_files(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    paths = result.stdout.decode("utf-8").split("\0")
    return [root / path for path in paths if path]


def scan(root: Path) -> tuple[list[Finding], int]:
    findings: list[Finding] = []
    scanned = 0
    for path in candidate_files(root):
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in TEXT_NAMES:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        scanned += 1
        relative = path.relative_to(root).as_posix()
        for line_number, line in enumerate(content.splitlines(), start=1):
            for rule, pattern in PATTERNS.items():
                if pattern.search(line):
                    findings.append(Finding(relative, line_number, rule))
    return findings, scanned


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit machine-readable output")
    args = parser.parse_args()
    findings, scanned = scan(ROOT)
    payload = {
        "ok": not findings,
        "scanned_files": scanned,
        "findings": [asdict(finding) for finding in findings],
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif findings:
        for finding in findings:
            print(f"{finding.file}:{finding.line}: {finding.rule}")
    else:
        print(f"Secret scan passed ({scanned} publishable text files).")
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
