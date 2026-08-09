"""Command-line interface for DutyBell."""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import sys
from typing import Any, NoReturn

from dutybell import __version__
from dutybell.exporter import export_room, verify_export
from dutybell.models import DutyBellError, ValidationError
from dutybell.server import serve, static_directory
from dutybell.service import DutyBellService
from dutybell.store import SQLiteStore

DEFAULT_DATABASE = os.environ.get("DUTYBELL_DATABASE", "dutybell.db")


def _service(database: str) -> DutyBellService:
    return DutyBellService(SQLiteStore(database))


def _participants(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _fail(message: str, code: int = 2) -> NoReturn:
    print(f"dutybell: {message}", file=sys.stderr)
    raise SystemExit(code)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dutybell",
        description="Self-hosted synchronized household relay timers.",
    )
    parser.add_argument("--version", action="version", version=f"DutyBell {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", help="run the API and PWA server")
    serve_parser.add_argument("--database", default=DEFAULT_DATABASE)
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8742)
    serve_parser.add_argument("--create-token", default=os.environ.get("DUTYBELL_CREATE_TOKEN"))
    serve_parser.add_argument("--verbose", action="store_true")

    create_parser = subparsers.add_parser("create", help="create a room directly in the database")
    create_parser.add_argument("name")
    create_parser.add_argument("--database", default=DEFAULT_DATABASE)
    create_parser.add_argument("--seconds", type=int, required=True)
    create_parser.add_argument("--participants", default="")
    create_parser.add_argument("--actor", default="owner")
    create_parser.add_argument("--base-url", default="http://127.0.0.1:8742")
    create_parser.add_argument("--no-repeat", action="store_true")
    create_parser.add_argument("--no-rotate", action="store_true")
    create_parser.add_argument("--start", action="store_true")

    status_parser = subparsers.add_parser("status", help="read one room")
    status_parser.add_argument("room_id")
    status_parser.add_argument("--key", required=True)
    status_parser.add_argument("--database", default=DEFAULT_DATABASE)

    action_parser = subparsers.add_parser("action", help="apply a room action")
    action_parser.add_argument("room_id")
    action_parser.add_argument("action")
    action_parser.add_argument("--key", required=True)
    action_parser.add_argument("--actor", required=True)
    action_parser.add_argument("--expected-version", type=int, required=True)
    action_parser.add_argument("--data", default="{}", help="JSON object for start/configure")
    action_parser.add_argument("--database", default=DEFAULT_DATABASE)

    export_parser = subparsers.add_parser("export", help="create a portable verified room archive")
    export_parser.add_argument("room_id")
    export_parser.add_argument("output")
    export_parser.add_argument("--key", required=True)
    export_parser.add_argument("--database", default=DEFAULT_DATABASE)

    verify_parser = subparsers.add_parser("verify", help="verify a DutyBell export archive")
    verify_parser.add_argument("archive")

    doctor_parser = subparsers.add_parser(
        "doctor", help="check database, static files, and binding"
    )
    doctor_parser.add_argument("--database", default=DEFAULT_DATABASE)
    doctor_parser.add_argument("--host", default="127.0.0.1")

    return parser


def run(args: argparse.Namespace) -> int:
    if args.command == "serve":
        if not 0 <= args.port <= 65535:
            raise ValidationError("port must be between 0 and 65535")
        logging.basicConfig(
            level=logging.INFO if args.verbose else logging.WARNING,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        serve(
            _service(args.database),
            host=args.host,
            port=args.port,
            create_token=args.create_token or None,
        )
        return 0

    if args.command == "create":
        service = _service(args.database)
        room, key = service.create_room(
            name=args.name,
            interval_seconds=args.seconds,
            participants=_participants(args.participants),
            repeat_on_ack=not args.no_repeat,
            rotate_on_ack=not args.no_rotate,
            start=args.start,
            actor=args.actor,
        )
        base_url = args.base_url.rstrip("/")
        _print_json(
            {
                "room": service.snapshot(room),
                "access_key": key,
                "join_url": f"{base_url}/#room={room.room_id}&key={key}",
            }
        )
        return 0

    if args.command == "status":
        service = _service(args.database)
        _print_json({"room": service.snapshot(service.get_room(args.room_id, args.key))})
        return 0

    if args.command == "action":
        service = _service(args.database)
        try:
            data = json.loads(args.data)
        except json.JSONDecodeError as error:
            raise ValidationError("--data must be valid JSON") from error
        if not isinstance(data, dict):
            raise ValidationError("--data must be a JSON object")
        room = service.perform(
            args.room_id,
            args.key,
            action=args.action,
            actor=args.actor,
            expected_version=args.expected_version,
            data=data,
        )
        _print_json({"room": service.snapshot(room)})
        return 0

    if args.command == "export":
        result = export_room(
            _service(args.database),
            room_id=args.room_id,
            access_key=args.key,
            output=args.output,
        )
        _print_json(result)
        return 0

    if args.command == "verify":
        _print_json(verify_export(args.archive))
        return 0

    if args.command == "doctor":
        service = _service(args.database)
        static_path = static_directory()
        required_static = [
            "index.html",
            "app.js",
            "core.mjs",
            "styles.css",
            "manifest.webmanifest",
            "sw.js",
            "icon.svg",
        ]
        missing = [name for name in required_static if not (static_path / name).is_file()]
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind((args.host, 0))
            bind_address = f"{args.host}:{probe.getsockname()[1]}"
        result = {
            "ok": not missing and service.store.database_health()["integrity"] == "ok",
            "version": __version__,
            "python": sys.version.split()[0],
            "database": service.store.database_health(),
            "static_directory": str(static_path.resolve()),
            "missing_static": missing,
            "bind_probe": bind_address,
        }
        _print_json(result)
        return 0 if result["ok"] else 1

    raise AssertionError(f"unhandled command: {args.command}")


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        raise SystemExit(run(args))
    except DutyBellError as error:
        _fail(str(error))
    except (OSError, ValueError) as error:
        _fail(str(error))


if __name__ == "__main__":
    main()
