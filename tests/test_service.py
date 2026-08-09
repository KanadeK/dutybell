from __future__ import annotations

import threading
import time
import unittest

from dutybell.models import (
    AuthenticationError,
    ConflictError,
    StateError,
    ValidationError,
)
from tests.helpers import room_payload, temporary_service


class ServiceTests(unittest.TestCase):
    def test_create_room_starts_and_persists_without_plain_key(self) -> None:
        with temporary_service() as (service, clock, database):
            room, key = service.create_room(**room_payload())
            self.assertEqual(room.status, "running")
            self.assertEqual(room.deadline_at_ms, clock() + 120_000)
            self.assertEqual(room.assignee, "Alex")
            self.assertRegex(key, r"\A[0-9a-f]{48}\Z")
            self.assertEqual(service.get_room(room.room_id.lower(), key), room)
            self.assertNotIn(key.encode(), database.read_bytes())
            self.assertEqual(service.store.database_health()["integrity"], "ok")

    def test_wrong_access_key_and_room_id_are_rejected(self) -> None:
        with temporary_service() as (service, _clock, _database):
            room, _key = service.create_room(**room_payload())
            with self.assertRaises(AuthenticationError):
                service.get_room(room.room_id, "wrong")
            with self.assertRaises(ValidationError):
                service.get_room("not-a-room", "wrong")

    def test_pause_resume_and_reset_use_server_clock(self) -> None:
        with temporary_service() as (service, clock, _database):
            room, key = service.create_room(**room_payload(interval_seconds=10))
            clock.advance(2_500)
            paused = service.perform(
                room.room_id,
                key,
                action="pause",
                actor="Alex",
                expected_version=room.version,
            )
            self.assertEqual(paused.paused_remaining_ms, 7_500)
            clock.advance(20_000)
            resumed = service.perform(
                room.room_id,
                key,
                action="resume",
                actor="Alex",
                expected_version=paused.version,
            )
            self.assertEqual(resumed.deadline_at_ms, clock() + 7_500)
            reset = service.perform(
                room.room_id,
                key,
                action="reset",
                actor="Sam",
                expected_version=resumed.version,
            )
            self.assertEqual(reset.deadline_at_ms, clock() + 10_000)

    def test_acknowledgement_repeats_and_rotates(self) -> None:
        with temporary_service() as (service, clock, _database):
            room, key = service.create_room(**room_payload(interval_seconds=5))
            clock.advance(5_100)
            updated = service.perform(
                room.room_id,
                key,
                action="ack",
                actor="Alex",
                expected_version=room.version,
            )
            self.assertEqual(updated.status, "running")
            self.assertEqual(updated.assignee, "Sam")
            self.assertEqual(updated.deadline_at_ms, clock() + 5_000)
            event = service.store.list_events(room.room_id)[-1]
            self.assertEqual(event.payload["acknowledged_by"], "Alex")
            self.assertTrue(event.payload["restarted"])

    def test_acknowledgement_can_end_without_repeating(self) -> None:
        with temporary_service() as (service, _clock, _database):
            room, key = service.create_room(
                **room_payload(repeat_on_ack=False, rotate_on_ack=False)
            )
            updated = service.perform(
                room.room_id,
                key,
                action="ack",
                actor="Alex",
                expected_version=room.version,
            )
            self.assertEqual(updated.status, "acknowledged")
            self.assertEqual(updated.assignee, "Alex")
            self.assertIsNone(updated.deadline_at_ms)

    def test_claim_requires_configured_participant(self) -> None:
        with temporary_service() as (service, _clock, _database):
            room, key = service.create_room(**room_payload(start=False))
            with self.assertRaises(ValidationError):
                service.perform(
                    room.room_id,
                    key,
                    action="claim",
                    actor="Taylor",
                    expected_version=room.version,
                )
            claimed = service.perform(
                room.room_id,
                key,
                action="claim",
                actor="sam",
                expected_version=room.version,
            )
            self.assertEqual(claimed.assignee, "Sam")

    def test_configure_updates_rotation_and_recovers_assignee(self) -> None:
        with temporary_service() as (service, _clock, _database):
            room, key = service.create_room(**room_payload(start=False))
            configured = service.perform(
                room.room_id,
                key,
                action="configure",
                actor="Alex",
                expected_version=room.version,
                data={
                    "name": "Oven turn",
                    "interval_seconds": 45,
                    "participants": ["Taylor", "Morgan"],
                    "repeat_on_ack": False,
                    "rotate_on_ack": False,
                },
            )
            self.assertEqual(configured.name, "Oven turn")
            self.assertEqual(configured.assignee, "Taylor")
            self.assertFalse(configured.repeat_on_ack)

    def test_stale_version_does_not_append_event(self) -> None:
        with temporary_service() as (service, _clock, _database):
            room, key = service.create_room(**room_payload(start=False))
            started = service.perform(
                room.room_id,
                key,
                action="start",
                actor="Alex",
                expected_version=room.version,
            )
            with self.assertRaises(ConflictError) as context:
                service.perform(
                    room.room_id,
                    key,
                    action="stop",
                    actor="Sam",
                    expected_version=room.version,
                )
            self.assertEqual(context.exception.current, started)
            self.assertEqual(len(service.store.list_events(room.room_id)), 2)

    def test_invalid_state_transitions_are_explicit(self) -> None:
        with temporary_service() as (service, _clock, _database):
            room, key = service.create_room(**room_payload(start=False))
            for action in ("pause", "resume", "ack"):
                with self.subTest(action=action), self.assertRaises(StateError):
                    service.perform(
                        room.room_id,
                        key,
                        action=action,
                        actor="Alex",
                        expected_version=room.version,
                    )

    def test_wait_returns_after_another_thread_changes_room(self) -> None:
        with temporary_service() as (service, _clock, _database):
            room, key = service.create_room(**room_payload(start=False))

            def change_room() -> None:
                time.sleep(0.05)
                service.perform(
                    room.room_id,
                    key,
                    action="start",
                    actor="Alex",
                    expected_version=room.version,
                )

            thread = threading.Thread(target=change_room)
            thread.start()
            changed = service.store.wait_for_version(room.room_id, room.version, 1.0)
            thread.join()
            self.assertEqual(changed.version, room.version + 1)

    def test_wait_timeout_returns_unchanged_room(self) -> None:
        with temporary_service() as (service, _clock, _database):
            room, _key = service.create_room(**room_payload(start=False))
            unchanged = service.store.wait_for_version(room.room_id, room.version, 0.01)
            self.assertEqual(unchanged.version, room.version)


if __name__ == "__main__":
    unittest.main()
