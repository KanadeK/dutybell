from __future__ import annotations

import unittest

from dutybell.models import Room, ValidationError, clean_interval, clean_name, clean_participants


class ValidationTests(unittest.TestCase):
    def test_clean_name_normalizes_whitespace(self) -> None:
        self.assertEqual(clean_name("  Dog\t break  "), "Dog break")

    def test_clean_name_rejects_invalid_values(self) -> None:
        for value in (None, "", "x" * 81, "bad\u0007name"):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                clean_name(value)

    def test_participants_keep_order_and_remove_casefold_duplicates(self) -> None:
        self.assertEqual(clean_participants([" Alex ", "sam", "ALEX"]), ("Alex", "sam"))

    def test_participants_reject_wrong_shape_and_size(self) -> None:
        with self.assertRaises(ValidationError):
            clean_participants("Alex")
        with self.assertRaises(ValidationError):
            clean_participants([str(index) for index in range(21)])

    def test_interval_bounds_and_bool_rejection(self) -> None:
        self.assertEqual(clean_interval(1), 1)
        self.assertEqual(clean_interval(604_800), 604_800)
        for value in (True, 0, 604_801, 1.5, "60"):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                clean_interval(value)


class RoomTests(unittest.TestCase):
    def make_room(self, **changes: object) -> Room:
        values = {
            "room_id": "ABC234DE",
            "name": "Laundry",
            "interval_seconds": 60,
            "status": "running",
            "deadline_at_ms": 11_000,
            "paused_remaining_ms": None,
            "repeat_on_ack": True,
            "rotate_on_ack": True,
            "participants": ("Alex", "Sam"),
            "assignee": "Alex",
            "version": 2,
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:01Z",
        }
        values.update(changes)
        return Room(**values)  # type: ignore[arg-type]

    def test_public_snapshot_calculates_due_state(self) -> None:
        snapshot = self.make_room().to_public_dict(12_000)
        self.assertTrue(snapshot["is_due"])
        self.assertEqual(snapshot["remaining_ms"], 0)

    def test_paused_snapshot_uses_stored_remaining_time(self) -> None:
        snapshot = self.make_room(
            status="paused", deadline_at_ms=None, paused_remaining_ms=7_500
        ).to_public_dict(99_000)
        self.assertFalse(snapshot["is_due"])
        self.assertEqual(snapshot["remaining_ms"], 7_500)

    def test_idle_snapshot_has_no_remaining_time(self) -> None:
        snapshot = self.make_room(status="idle", deadline_at_ms=None).to_public_dict(1)
        self.assertIsNone(snapshot["remaining_ms"])


if __name__ == "__main__":
    unittest.main()
