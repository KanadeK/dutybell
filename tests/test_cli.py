from __future__ import annotations

import argparse
import io
import json
import runpy
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from dutybell import __version__, cli
from dutybell.models import ValidationError


class CLITests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.directory = Path(self.temporary_directory.name)
        self.database = self.directory / "dutybell.db"
        self.parser = cli.build_parser()

    def invoke(self, arguments: list[str]) -> tuple[int, dict[str, Any]]:
        output = io.StringIO()
        with redirect_stdout(output):
            result = cli.run(self.parser.parse_args(arguments))
        return result, cast(dict[str, Any], json.loads(output.getvalue()))

    def create_room(self) -> dict[str, Any]:
        result, payload = self.invoke(
            [
                "create",
                "Dog break",
                "--database",
                str(self.database),
                "--seconds",
                "90",
                "--participants",
                " Alex, Sam, ",
                "--actor",
                "Alex",
                "--base-url",
                "https://timer.example/",
                "--start",
                "--no-repeat",
                "--no-rotate",
            ]
        )
        self.assertEqual(result, 0)
        self.assertEqual(payload["room"]["participants"], ["Alex", "Sam"])
        self.assertTrue(str(payload["join_url"]).startswith("https://timer.example/#room="))
        return payload

    def test_create_status_action_export_and_verify_commands(self) -> None:
        created = self.create_room()
        room = created["room"]
        room_id = str(room["room_id"])
        key = str(created["access_key"])

        result, status = self.invoke(
            ["status", room_id, "--key", key, "--database", str(self.database)]
        )
        self.assertEqual(result, 0)
        self.assertEqual(status["room"]["status"], "running")

        result, action = self.invoke(
            [
                "action",
                room_id,
                "pause",
                "--key",
                key,
                "--actor",
                "Sam",
                "--expected-version",
                "1",
                "--data",
                "{}",
                "--database",
                str(self.database),
            ]
        )
        self.assertEqual(result, 0)
        self.assertEqual(action["room"]["status"], "paused")

        archive = self.directory / "room.zip"
        result, exported = self.invoke(
            [
                "export",
                room_id,
                str(archive),
                "--key",
                key,
                "--database",
                str(self.database),
            ]
        )
        self.assertEqual(result, 0)
        self.assertEqual(exported["events"], 2)
        result, verified = self.invoke(["verify", str(archive)])
        self.assertEqual(result, 0)
        self.assertTrue(verified["ok"])

    def test_doctor_checks_database_static_assets_and_socket(self) -> None:
        result, payload = self.invoke(
            ["doctor", "--database", str(self.database), "--host", "127.0.0.1"]
        )
        self.assertEqual(result, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["version"], __version__)
        self.assertEqual(payload["database"]["integrity"], "ok")
        self.assertEqual(payload["missing_static"], [])

        empty_static = self.directory / "empty-static"
        empty_static.mkdir()
        with patch.object(cli, "static_directory", return_value=empty_static):
            result, payload = self.invoke(
                ["doctor", "--database", str(self.database), "--host", "127.0.0.1"]
            )
        self.assertEqual(result, 1)
        self.assertIn("index.html", payload["missing_static"])

    def test_serve_validates_port_and_passes_configuration(self) -> None:
        invalid = self.parser.parse_args(["serve", "--port", "70000"])
        with self.assertRaisesRegex(ValidationError, "port"):
            cli.run(invalid)

        arguments = self.parser.parse_args(
            [
                "serve",
                "--database",
                str(self.database),
                "--host",
                "0.0.0.0",
                "--port",
                "0",
                "--create-token",
                "private",
                "--verbose",
            ]
        )
        with patch.object(cli, "serve") as mocked_serve:
            self.assertEqual(cli.run(arguments), 0)
        mocked_serve.assert_called_once()
        self.assertEqual(mocked_serve.call_args.kwargs["host"], "0.0.0.0")
        self.assertEqual(mocked_serve.call_args.kwargs["port"], 0)
        self.assertEqual(mocked_serve.call_args.kwargs["create_token"], "private")

        no_token = self.parser.parse_args(
            ["serve", "--database", str(self.database), "--port", "0", "--create-token", ""]
        )
        with patch.object(cli, "serve") as no_token_serve:
            self.assertEqual(cli.run(no_token), 0)
        self.assertIsNone(no_token_serve.call_args.kwargs["create_token"])

    def test_action_rejects_invalid_or_non_object_json(self) -> None:
        created = self.create_room()
        base = [
            "action",
            str(created["room"]["room_id"]),
            "start",
            "--key",
            str(created["access_key"]),
            "--actor",
            "Alex",
            "--expected-version",
            "1",
            "--database",
            str(self.database),
        ]
        with self.assertRaisesRegex(ValidationError, "valid JSON"):
            cli.run(self.parser.parse_args([*base, "--data", "{"]))
        with self.assertRaisesRegex(ValidationError, "JSON object"):
            cli.run(self.parser.parse_args([*base, "--data", "[]"]))

    def test_main_converts_domain_and_operating_errors_to_exit_two(self) -> None:
        for error in (ValidationError("bad input"), OSError("disk unavailable")):
            error_output = io.StringIO()
            with (
                patch.object(cli, "run", side_effect=error),
                redirect_stderr(error_output),
                self.assertRaises(SystemExit) as raised,
            ):
                cli.main(["doctor"])
            self.assertEqual(raised.exception.code, 2)
            self.assertIn(str(error), error_output.getvalue())

    def test_version_module_entrypoint_and_unknown_command(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit) as raised:
            cli.main(["--version"])
        self.assertEqual(raised.exception.code, 0)
        self.assertIn(__version__, output.getvalue())

        with (
            patch("sys.argv", ["dutybell", "--version"]),
            redirect_stdout(io.StringIO()),
            self.assertRaises(SystemExit) as module_exit,
        ):
            runpy.run_module("dutybell.__main__", run_name="__main__")
        self.assertEqual(module_exit.exception.code, 0)

        with self.assertRaisesRegex(AssertionError, "unhandled command"):
            cli.run(argparse.Namespace(command="mystery"))


if __name__ == "__main__":
    unittest.main()
