"""Domain types and validation for DutyBell rooms."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Literal

RoomStatus = Literal["idle", "running", "paused", "acknowledged"]


class DutyBellError(Exception):
    """Base class for expected DutyBell failures."""


class ValidationError(DutyBellError):
    """Raised when user input is not valid."""


class AuthenticationError(DutyBellError):
    """Raised when a room access key is missing or wrong."""


class NotFoundError(DutyBellError):
    """Raised when a room does not exist."""


class ConflictError(DutyBellError):
    """Raised when an action was based on an old room version."""

    def __init__(self, message: str, current: Room | None = None) -> None:
        super().__init__(message)
        self.current = current


class StateError(DutyBellError):
    """Raised when an action is invalid for the current room state."""


@dataclass(frozen=True, slots=True)
class Room:
    """Persistent synchronized timer state."""

    room_id: str
    name: str
    interval_seconds: int
    status: RoomStatus
    deadline_at_ms: int | None
    paused_remaining_ms: int | None
    repeat_on_ack: bool
    rotate_on_ack: bool
    participants: tuple[str, ...]
    assignee: str | None
    version: int
    created_at: str
    updated_at: str

    def evolve(self, **changes: Any) -> Room:
        """Return a new room value without mutating the prior version."""

        return replace(self, **changes)

    def to_public_dict(self, server_now_ms: int) -> dict[str, Any]:
        """Serialize state for clients without exposing authentication data."""

        remaining_ms: int | None
        if self.status == "running" and self.deadline_at_ms is not None:
            remaining_ms = max(0, self.deadline_at_ms - server_now_ms)
        elif self.status == "paused":
            remaining_ms = max(0, self.paused_remaining_ms or 0)
        else:
            remaining_ms = None

        return {
            "room_id": self.room_id,
            "name": self.name,
            "interval_seconds": self.interval_seconds,
            "status": self.status,
            "deadline_at_ms": self.deadline_at_ms,
            "paused_remaining_ms": self.paused_remaining_ms,
            "remaining_ms": remaining_ms,
            "is_due": bool(
                self.status == "running"
                and self.deadline_at_ms is not None
                and self.deadline_at_ms <= server_now_ms
            ),
            "repeat_on_ack": self.repeat_on_ack,
            "rotate_on_ack": self.rotate_on_ack,
            "participants": list(self.participants),
            "assignee": self.assignee,
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "server_now_ms": server_now_ms,
        }


@dataclass(frozen=True, slots=True)
class Event:
    """One append-only room transition."""

    event_id: int | None
    room_id: str
    version: int
    action: str
    actor: str
    created_at: str
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "room_id": self.room_id,
            "version": self.version,
            "action": self.action,
            "actor": self.actor,
            "created_at": self.created_at,
            "payload": self.payload,
        }


def clean_name(value: object, *, field: str = "name", maximum: int = 80) -> str:
    """Validate a short human-facing label."""

    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string")
    cleaned = " ".join(value.strip().split())
    if not cleaned:
        raise ValidationError(f"{field} must not be empty")
    if len(cleaned) > maximum:
        raise ValidationError(f"{field} must be at most {maximum} characters")
    if any(ord(character) < 32 for character in cleaned):
        raise ValidationError(f"{field} contains a control character")
    return cleaned


def clean_participants(value: object) -> tuple[str, ...]:
    """Validate and deduplicate an ordered participant list."""

    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValidationError("participants must be a list")
    if len(value) > 20:
        raise ValidationError("participants may contain at most 20 names")

    result: list[str] = []
    seen: set[str] = set()
    for raw_name in value:
        name = clean_name(raw_name, field="participant", maximum=40)
        folded = name.casefold()
        if folded not in seen:
            seen.add(folded)
            result.append(name)
    return tuple(result)


def clean_interval(value: object) -> int:
    """Validate a timer interval in seconds (1 second through 7 days)."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError("interval_seconds must be an integer")
    if not 1 <= value <= 604_800:
        raise ValidationError("interval_seconds must be between 1 and 604800")
    return value
