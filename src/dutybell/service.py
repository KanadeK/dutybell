"""DutyBell timer state machine."""

from __future__ import annotations

import base64
import secrets
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, ClassVar

from dutybell.models import (
    ConflictError,
    Event,
    Room,
    StateError,
    ValidationError,
    clean_interval,
    clean_name,
    clean_participants,
)
from dutybell.store import SQLiteStore


def _system_now_ms() -> int:
    return time.time_ns() // 1_000_000


def _iso_from_ms(milliseconds: int) -> str:
    return datetime.fromtimestamp(milliseconds / 1000, tz=UTC).isoformat().replace("+00:00", "Z")


class DutyBellService:
    """Validates and applies every room transition in one place."""

    ACTIONS: ClassVar[frozenset[str]] = frozenset(
        {"start", "pause", "resume", "reset", "ack", "stop", "claim", "configure"}
    )

    def __init__(
        self,
        store: SQLiteStore,
        *,
        now_ms: Callable[[], int] = _system_now_ms,
    ) -> None:
        self.store = store
        self.now_ms = now_ms
        self.store.initialize()

    @staticmethod
    def _new_room_id() -> str:
        return base64.b32encode(secrets.token_bytes(5)).decode("ascii").rstrip("=")

    def create_room(
        self,
        *,
        name: object,
        interval_seconds: object,
        participants: object = None,
        repeat_on_ack: object = True,
        rotate_on_ack: object = True,
        start: object = False,
        actor: object = "owner",
    ) -> tuple[Room, str]:
        room_name = clean_name(name, maximum=80)
        interval = clean_interval(interval_seconds)
        people = clean_participants(participants)
        repeat = self._require_bool(repeat_on_ack, "repeat_on_ack")
        rotate = self._require_bool(rotate_on_ack, "rotate_on_ack")
        should_start = self._require_bool(start, "start")
        clean_actor = clean_name(actor, field="actor", maximum=40)
        now = self.now_ms()
        room_id = self._new_room_id()
        access_key = secrets.token_hex(24)
        assignee = people[0] if people else None
        room = Room(
            room_id=room_id,
            name=room_name,
            interval_seconds=interval,
            status="running" if should_start else "idle",
            deadline_at_ms=now + interval * 1000 if should_start else None,
            paused_remaining_ms=None,
            repeat_on_ack=repeat,
            rotate_on_ack=rotate,
            participants=people,
            assignee=assignee,
            version=1,
            created_at=_iso_from_ms(now),
            updated_at=_iso_from_ms(now),
        )
        event = Event(
            event_id=None,
            room_id=room_id,
            version=1,
            action="create_and_start" if should_start else "create",
            actor=clean_actor,
            created_at=room.created_at,
            payload={
                "interval_seconds": interval,
                "participants": list(people),
                "repeat_on_ack": repeat,
                "rotate_on_ack": rotate,
            },
        )
        self.store.create_room(room, access_key, event)
        return room, access_key

    def get_room(self, room_id: str, access_key: str) -> Room:
        return self.store.authenticate(self._clean_room_id(room_id), access_key)

    def snapshot(self, room: Room) -> dict[str, Any]:
        return room.to_public_dict(self.now_ms())

    def perform(
        self,
        room_id: str,
        access_key: str,
        *,
        action: object,
        actor: object,
        expected_version: object,
        data: Mapping[str, Any] | None = None,
    ) -> Room:
        clean_room_id = self._clean_room_id(room_id)
        current = self.store.authenticate(clean_room_id, access_key)
        clean_action = self._clean_action(action)
        clean_actor = clean_name(actor, field="actor", maximum=40)
        expected = self._clean_version(expected_version)
        if expected != current.version:
            raise ConflictError(
                f"expected room version {expected}, current version is {current.version}", current
            )
        payload = dict(data or {})
        now = self.now_ms()
        updated, event_payload = self._transition(current, clean_action, clean_actor, payload, now)
        updated = updated.evolve(version=current.version + 1, updated_at=_iso_from_ms(now))
        event = Event(
            event_id=None,
            room_id=current.room_id,
            version=updated.version,
            action=clean_action,
            actor=clean_actor,
            created_at=updated.updated_at,
            payload=event_payload,
        )
        return self.store.update_room(updated, event, expected_version=current.version)

    def _transition(
        self,
        room: Room,
        action: str,
        actor: str,
        data: dict[str, Any],
        now: int,
    ) -> tuple[Room, dict[str, Any]]:
        if action == "start":
            interval = clean_interval(data.get("interval_seconds", room.interval_seconds))
            return (
                room.evolve(
                    interval_seconds=interval,
                    status="running",
                    deadline_at_ms=now + interval * 1000,
                    paused_remaining_ms=None,
                ),
                {"interval_seconds": interval},
            )

        if action == "pause":
            if room.status != "running" or room.deadline_at_ms is None:
                raise StateError("only a running timer can be paused")
            remaining = max(0, room.deadline_at_ms - now)
            return (
                room.evolve(status="paused", deadline_at_ms=None, paused_remaining_ms=remaining),
                {"remaining_ms": remaining},
            )

        if action == "resume":
            if room.status != "paused" or room.paused_remaining_ms is None:
                raise StateError("only a paused timer can be resumed")
            remaining = max(0, room.paused_remaining_ms)
            return (
                room.evolve(
                    status="running",
                    deadline_at_ms=now + remaining,
                    paused_remaining_ms=None,
                ),
                {"remaining_ms": remaining},
            )

        if action == "reset":
            return (
                room.evolve(
                    status="running",
                    deadline_at_ms=now + room.interval_seconds * 1000,
                    paused_remaining_ms=None,
                ),
                {"interval_seconds": room.interval_seconds},
            )

        if action == "ack":
            if room.status not in {"running", "paused"}:
                raise StateError("only an active timer can be acknowledged")
            next_assignee = room.assignee
            if room.rotate_on_ack:
                next_assignee = self._next_assignee(room.participants, room.assignee)
            if room.repeat_on_ack:
                return (
                    room.evolve(
                        status="running",
                        deadline_at_ms=now + room.interval_seconds * 1000,
                        paused_remaining_ms=None,
                        assignee=next_assignee,
                    ),
                    {
                        "acknowledged_by": actor,
                        "next_assignee": next_assignee,
                        "restarted": True,
                    },
                )
            return (
                room.evolve(
                    status="acknowledged",
                    deadline_at_ms=None,
                    paused_remaining_ms=None,
                    assignee=next_assignee,
                ),
                {
                    "acknowledged_by": actor,
                    "next_assignee": next_assignee,
                    "restarted": False,
                },
            )

        if action == "stop":
            return (
                room.evolve(status="idle", deadline_at_ms=None, paused_remaining_ms=None),
                {},
            )

        if action == "claim":
            if room.participants:
                match = next(
                    (name for name in room.participants if name.casefold() == actor.casefold()),
                    None,
                )
                if match is None:
                    raise ValidationError("actor must be one of the configured participants")
                actor = match
            return room.evolve(assignee=actor), {"assignee": actor}

        if action == "configure":
            name = clean_name(data.get("name", room.name), maximum=80)
            interval = clean_interval(data.get("interval_seconds", room.interval_seconds))
            participants = clean_participants(data.get("participants", room.participants))
            repeat = self._require_bool(
                data.get("repeat_on_ack", room.repeat_on_ack), "repeat_on_ack"
            )
            rotate = self._require_bool(
                data.get("rotate_on_ack", room.rotate_on_ack), "rotate_on_ack"
            )
            assignee = room.assignee
            if participants and not any(
                name.casefold() == (assignee or "").casefold() for name in participants
            ):
                assignee = participants[0]
            if not participants:
                assignee = None
            return (
                room.evolve(
                    name=name,
                    interval_seconds=interval,
                    participants=participants,
                    repeat_on_ack=repeat,
                    rotate_on_ack=rotate,
                    assignee=assignee,
                ),
                {
                    "name": name,
                    "interval_seconds": interval,
                    "participants": list(participants),
                    "repeat_on_ack": repeat,
                    "rotate_on_ack": rotate,
                },
            )

        raise AssertionError(f"unhandled action: {action}")

    @staticmethod
    def _next_assignee(participants: tuple[str, ...], current: str | None) -> str | None:
        if not participants:
            return None
        if current is None:
            return participants[0]
        for index, name in enumerate(participants):
            if name.casefold() == current.casefold():
                return participants[(index + 1) % len(participants)]
        return participants[0]

    @classmethod
    def _clean_action(cls, value: object) -> str:
        if not isinstance(value, str) or value not in cls.ACTIONS:
            raise ValidationError(f"action must be one of: {', '.join(sorted(cls.ACTIONS))}")
        return value

    @staticmethod
    def _clean_version(value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValidationError("expected_version must be a positive integer")
        return value

    @staticmethod
    def _require_bool(value: object, field: str) -> bool:
        if not isinstance(value, bool):
            raise ValidationError(f"{field} must be a boolean")
        return value

    @staticmethod
    def _clean_room_id(value: object) -> str:
        if not isinstance(value, str):
            raise ValidationError("room_id must be a string")
        cleaned = value.strip().upper()
        if len(cleaned) != 8 or any(
            character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567" for character in cleaned
        ):
            raise ValidationError("room_id must be an 8-character base32 code")
        return cleaned
