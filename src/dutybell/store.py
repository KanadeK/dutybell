"""SQLite persistence and optimistic concurrency for DutyBell."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from dutybell.models import (
    AuthenticationError,
    ConflictError,
    Event,
    NotFoundError,
    Room,
)


def hash_access_key(access_key: str) -> str:
    """Hash a room access key before persistence."""

    return hashlib.sha256(access_key.encode("utf-8")).hexdigest()


class SQLiteStore:
    """A small transactional store safe for the threaded HTTP server."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._changed = threading.Condition()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS rooms (
                    room_id TEXT PRIMARY KEY,
                    access_key_hash TEXT NOT NULL,
                    name TEXT NOT NULL,
                    interval_seconds INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    deadline_at_ms INTEGER,
                    paused_remaining_ms INTEGER,
                    repeat_on_ack INTEGER NOT NULL,
                    rotate_on_ack INTEGER NOT NULL,
                    participants_json TEXT NOT NULL,
                    assignee TEXT,
                    version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    room_id TEXT NOT NULL REFERENCES rooms(room_id) ON DELETE CASCADE,
                    version INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    UNIQUE(room_id, version)
                );

                CREATE INDEX IF NOT EXISTS idx_events_room_version
                    ON events(room_id, version);
                """
            )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _room_from_row(row: sqlite3.Row) -> Room:
        participants = tuple(json.loads(str(row["participants_json"])))
        return Room(
            room_id=str(row["room_id"]),
            name=str(row["name"]),
            interval_seconds=int(row["interval_seconds"]),
            status=str(row["status"]),  # type: ignore[arg-type]
            deadline_at_ms=(
                int(row["deadline_at_ms"]) if row["deadline_at_ms"] is not None else None
            ),
            paused_remaining_ms=(
                int(row["paused_remaining_ms"]) if row["paused_remaining_ms"] is not None else None
            ),
            repeat_on_ack=bool(row["repeat_on_ack"]),
            rotate_on_ack=bool(row["rotate_on_ack"]),
            participants=participants,
            assignee=str(row["assignee"]) if row["assignee"] is not None else None,
            version=int(row["version"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> Event:
        return Event(
            event_id=int(row["event_id"]),
            room_id=str(row["room_id"]),
            version=int(row["version"]),
            action=str(row["action"]),
            actor=str(row["actor"]),
            created_at=str(row["created_at"]),
            payload=json.loads(str(row["payload_json"])),
        )

    def create_room(self, room: Room, access_key: str, event: Event) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO rooms (
                    room_id, access_key_hash, name, interval_seconds, status,
                    deadline_at_ms, paused_remaining_ms, repeat_on_ack, rotate_on_ack,
                    participants_json, assignee, version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    room.room_id,
                    hash_access_key(access_key),
                    room.name,
                    room.interval_seconds,
                    room.status,
                    room.deadline_at_ms,
                    room.paused_remaining_ms,
                    int(room.repeat_on_ack),
                    int(room.rotate_on_ack),
                    json.dumps(room.participants, ensure_ascii=False),
                    room.assignee,
                    room.version,
                    room.created_at,
                    room.updated_at,
                ),
            )
            self._insert_event(connection, event)
        self._notify_change()

    def get_room(self, room_id: str) -> Room:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM rooms WHERE room_id = ?", (room_id,)).fetchone()
        if row is None:
            raise NotFoundError("room not found")
        return self._room_from_row(row)

    def authenticate(self, room_id: str, access_key: str) -> Room:
        supplied_hash = hash_access_key(access_key)
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM rooms WHERE room_id = ?", (room_id,)).fetchone()
        if row is None:
            raise NotFoundError("room not found")
        if not hmac.compare_digest(str(row["access_key_hash"]), supplied_hash):
            raise AuthenticationError("room access key is invalid")
        return self._room_from_row(row)

    def update_room(
        self,
        room: Room,
        event: Event,
        *,
        expected_version: int,
    ) -> Room:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM rooms WHERE room_id = ?", (room.room_id,)
            ).fetchone()
            if row is None:
                raise NotFoundError("room not found")
            current = self._room_from_row(row)
            if current.version != expected_version:
                raise ConflictError(
                    "expected room version "
                    f"{expected_version}, current version is {current.version}",
                    current,
                )
            if room.version != expected_version + 1:
                raise ValueError("updated room must increment version exactly once")

            cursor = connection.execute(
                """
                UPDATE rooms SET
                    name = ?, interval_seconds = ?, status = ?, deadline_at_ms = ?,
                    paused_remaining_ms = ?, repeat_on_ack = ?, rotate_on_ack = ?,
                    participants_json = ?, assignee = ?, version = ?, updated_at = ?
                WHERE room_id = ? AND version = ?
                """,
                (
                    room.name,
                    room.interval_seconds,
                    room.status,
                    room.deadline_at_ms,
                    room.paused_remaining_ms,
                    int(room.repeat_on_ack),
                    int(room.rotate_on_ack),
                    json.dumps(room.participants, ensure_ascii=False),
                    room.assignee,
                    room.version,
                    room.updated_at,
                    room.room_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                latest_row = connection.execute(
                    "SELECT * FROM rooms WHERE room_id = ?", (room.room_id,)
                ).fetchone()
                latest = self._room_from_row(latest_row) if latest_row is not None else None
                raise ConflictError("room changed during update", latest)
            self._insert_event(connection, event)
        self._notify_change()
        return room

    @staticmethod
    def _insert_event(connection: sqlite3.Connection, event: Event) -> None:
        connection.execute(
            """
            INSERT INTO events (room_id, version, action, actor, created_at, payload_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event.room_id,
                event.version,
                event.action,
                event.actor,
                event.created_at,
                json.dumps(event.payload, ensure_ascii=False, sort_keys=True),
            ),
        )

    def list_events(self, room_id: str) -> list[Event]:
        self.get_room(room_id)
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM events WHERE room_id = ? ORDER BY version", (room_id,)
            ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def wait_for_version(self, room_id: str, after_version: int, timeout: float) -> Room:
        """Block until a room advances, or return its state after the timeout."""

        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            room = self.get_room(room_id)
            if room.version > after_version:
                return room
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return room
            with self._changed:
                self._changed.wait(timeout=remaining)

    def _notify_change(self) -> None:
        with self._changed:
            self._changed.notify_all()

    def database_health(self) -> dict[str, Any]:
        with self._connection() as connection:
            integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
            room_count = int(connection.execute("SELECT COUNT(*) FROM rooms").fetchone()[0])
            event_count = int(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0])
        return {
            "database": str(self.path.resolve()),
            "integrity": integrity,
            "rooms": room_count,
            "events": event_count,
        }
