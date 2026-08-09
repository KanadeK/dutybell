#!/usr/bin/env python3
"""Validate local Markdown links and machine-readable project documents."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from urllib.parse import unquote

import yaml

ROOT = Path(__file__).resolve().parents[1]
LINK_PATTERN = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")


def markdown_files() -> list[Path]:
    return sorted(
        path
        for path in ROOT.rglob("*.md")
        if not any(part.startswith(".") and part != ".github" for part in path.parts)
        and not any(part in {"build", "dist"} or part.endswith(".egg-info") for part in path.parts)
    )


def check_markdown_links() -> tuple[int, list[str]]:
    checked = 0
    errors: list[str] = []
    for document in markdown_files():
        text = document.read_text(encoding="utf-8")
        for match in LINK_PATTERN.finditer(text):
            raw_target = match.group(1).strip().strip("<>")
            target_without_anchor = raw_target.split("#", 1)[0]
            if (
                not target_without_anchor
                or "://" in target_without_anchor
                or target_without_anchor.startswith("mailto:")
            ):
                continue
            path = (document.parent / unquote(target_without_anchor)).resolve()
            checked += 1
            try:
                path.relative_to(ROOT)
            except ValueError:
                errors.append(
                    f"{document.relative_to(ROOT)}: link escapes repository: {raw_target}"
                )
                continue
            if not path.exists():
                errors.append(f"{document.relative_to(ROOT)}: missing link target: {raw_target}")
    return checked, errors


def check_structured_documents() -> tuple[int, list[str]]:
    errors: list[str] = []
    checked = 0
    structured = [
        *sorted((ROOT / ".github").rglob("*.yml")),
        ROOT / "docker-compose.yml",
        ROOT / "CITATION.cff",
    ]
    for path in structured:
        checked += 1
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as error:
            errors.append(f"{path.relative_to(ROOT)}: invalid YAML: {error}")
            continue
        if not isinstance(payload, dict):
            errors.append(f"{path.relative_to(ROOT)}: top-level YAML value must be a mapping")

    for path in sorted((ROOT / "examples").glob("*.json")):
        checked += 1
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            errors.append(f"{path.relative_to(ROOT)}: invalid JSON: {error}")
            continue
        if not isinstance(payload, dict):
            errors.append(f"{path.relative_to(ROOT)}: example JSON must be an object")

    checked += 1
    try:
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        if pyproject.get("project", {}).get("name") != "dutybell":
            errors.append("pyproject.toml: project.name must be dutybell")
    except tomllib.TOMLDecodeError as error:
        errors.append(f"pyproject.toml: invalid TOML: {error}")
    return checked, errors


def main() -> int:
    links_checked, link_errors = check_markdown_links()
    documents_checked, document_errors = check_structured_documents()
    errors = [*link_errors, *document_errors]
    if errors:
        for error in errors:
            print(error)
        return 1
    print(
        f"Documentation check passed ({links_checked} local links, "
        f"{documents_checked} structured documents)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
