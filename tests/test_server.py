from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from dutybell.server import DutyBellHTTPServer
from dutybell.service import DutyBellService
from dutybell.store import SQLiteStore


class ServerHarness:
    def __init__(self, database: Path, *, create_token: str | None = None) -> None:
        service = DutyBellService(SQLiteStore(database))
        self.server = DutyBellHTTPServer(("127.0.0.1", 0), service, create_token=create_token)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self) -> ServerHarness:
        self.thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        body: dict[str, Any] | None = None,
        key: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], Any]:
        data = json.dumps(body).encode() if body is not None else None
        request_headers = dict(headers or {})
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        if key is not None:
            request_headers["Authorization"] = f"Bearer {key}"
        request = urllib.request.Request(
            self.base_url + path, data=data, method=method, headers=request_headers
        )
        try:
            response = urllib.request.urlopen(request, timeout=3)
        except urllib.error.HTTPError as error:
            response = error
        raw = response.read()
        response_headers = {name.lower(): value for name, value in response.headers.items()}
        content_type = response_headers.get("content-type", "")
        payload = json.loads(raw) if "application/json" in content_type and raw else raw
        return response.status, response_headers, payload


class ServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "server.db"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def create_payload() -> dict[str, Any]:
        return {
            "name": "Laundry",
            "interval_seconds": 30,
            "participants": ["Alex", "Sam"],
            "repeat_on_ack": True,
            "rotate_on_ack": True,
            "start": True,
            "actor": "Alex",
        }

    def test_complete_create_read_action_and_wait_flow(self) -> None:
        with ServerHarness(self.database) as server:
            status, headers, created = server.request(
                "/api/rooms", method="POST", body=self.create_payload()
            )
            self.assertEqual(status, 201)
            self.assertEqual(headers["x-frame-options"], "DENY")
            room = created["room"]
            key = created["access_key"]

            status, _headers, read = server.request(f"/api/rooms/{room['room_id']}", key=key)
            self.assertEqual(status, 200)
            self.assertEqual(read["room"]["version"], 1)

            status, _headers, action = server.request(
                f"/api/rooms/{room['room_id']}/actions",
                method="POST",
                key=key,
                body={"action": "pause", "actor": "Alex", "expected_version": 1, "data": {}},
            )
            self.assertEqual(status, 200)
            self.assertEqual(action["room"]["status"], "paused")

            status, _headers, waited = server.request(
                f"/api/rooms/{room['room_id']}/wait?after=1&timeout=0", key=key
            )
            self.assertEqual(status, 200)
            self.assertTrue(waited["changed"])

            status, _headers, events = server.request(
                f"/api/rooms/{room['room_id']}/events", key=key
            )
            self.assertEqual(
                [event["action"] for event in events["events"]], ["create_and_start", "pause"]
            )

    def test_auth_failure_and_stale_write_have_specific_statuses(self) -> None:
        with ServerHarness(self.database) as server:
            _, _, created = server.request("/api/rooms", method="POST", body=self.create_payload())
            room_id, key = created["room"]["room_id"], created["access_key"]
            status, _, payload = server.request(f"/api/rooms/{room_id}", key="wrong")
            self.assertEqual(status, 401)
            self.assertEqual(payload["error"], "authentication_error")
            status, _, payload = server.request(
                f"/api/rooms/{room_id}/actions",
                method="POST",
                key=key,
                body={"action": "stop", "actor": "Alex", "expected_version": 999},
            )
            self.assertEqual(status, 409)
            self.assertEqual(payload["room"]["version"], 1)

    def test_creation_token_is_enforced(self) -> None:
        with ServerHarness(self.database, create_token="server-secret") as server:
            status, _, _ = server.request("/api/rooms", method="POST", body=self.create_payload())
            self.assertEqual(status, 401)
            status, _, payload = server.request(
                "/api/rooms",
                method="POST",
                body=self.create_payload(),
                headers={"X-DutyBell-Create-Token": "server-secret"},
            )
            self.assertEqual(status, 201)
            self.assertIn("access_key", payload)

    def test_health_static_and_traversal_boundaries(self) -> None:
        with ServerHarness(self.database) as server:
            status, headers, payload = server.request("/healthz")
            self.assertEqual(status, 200)
            self.assertTrue(payload["ok"])
            self.assertIn("default-src 'self'", headers["content-security-policy"])

            status, headers, body = server.request("/")
            self.assertEqual(status, 200)
            self.assertIn(b"DutyBell", body)
            self.assertIn("text/html", headers["content-type"])
            self.assertEqual(headers["cache-control"], "no-cache")

            status, headers, body = server.request("/styles.css")
            self.assertEqual(status, 200)
            self.assertIn(b"[hidden]", body)
            self.assertEqual(headers["cache-control"], "no-cache")

            status, _, payload = server.request("/../pyproject.toml")
            self.assertEqual(status, 404)
            self.assertEqual(payload["error"], "not_found")

    def test_invalid_json_and_state_error_are_not_hidden(self) -> None:
        with ServerHarness(self.database) as server:
            request = urllib.request.Request(
                server.base_url + "/api/rooms",
                data=b"not-json",
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with self.assertRaises(urllib.error.HTTPError) as context:
                urllib.request.urlopen(request, timeout=3)
            self.assertEqual(context.exception.code, 400)

            _, _, created = server.request(
                "/api/rooms", method="POST", body={**self.create_payload(), "start": False}
            )
            status, _, payload = server.request(
                f"/api/rooms/{created['room']['room_id']}/actions",
                method="POST",
                key=created["access_key"],
                body={"action": "pause", "actor": "Alex", "expected_version": 1},
            )
            self.assertEqual(status, 422)
            self.assertEqual(payload["error"], "state_error")


if __name__ == "__main__":
    unittest.main()
