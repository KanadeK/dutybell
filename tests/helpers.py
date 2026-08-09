from __future__ import annotations

import json
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from dutybell.service import DutyBellService
from dutybell.store import SQLiteStore


class FrozenClock:
    def __init__(self, milliseconds: int = 1_800_000_000_000) -> None:
        self.milliseconds = milliseconds

    def __call__(self) -> int:
        return self.milliseconds

    def advance(self, milliseconds: int) -> None:
        self.milliseconds += milliseconds


@contextmanager
def temporary_service() -> Iterator[tuple[DutyBellService, FrozenClock, Path]]:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "dutybell.db"
        clock = FrozenClock()
        yield DutyBellService(SQLiteStore(path), now_ms=clock), clock, path


def room_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": "Dog break",
        "interval_seconds": 120,
        "participants": ["Alex", "Sam"],
        "repeat_on_ack": True,
        "rotate_on_ack": True,
        "start": True,
        "actor": "Alex",
    }
    payload.update(overrides)
    return payload


def json_bytes(value: Any) -> bytes:
    return json.dumps(value).encode("utf-8")
