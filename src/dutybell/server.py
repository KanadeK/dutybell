"""Dependency-free threaded HTTP API and PWA host."""

from __future__ import annotations

import json
import logging
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any, ClassVar, cast
from urllib.parse import parse_qs, urlsplit

from dutybell import __version__
from dutybell.models import (
    AuthenticationError,
    ConflictError,
    DutyBellError,
    NotFoundError,
    StateError,
    ValidationError,
)
from dutybell.service import DutyBellService

LOGGER = logging.getLogger("dutybell.http")
MAX_BODY_BYTES = 64 * 1024
STATIC_NAMES = {
    "index.html",
    "app.js",
    "core.mjs",
    "styles.css",
    "manifest.webmanifest",
    "sw.js",
    "icon.svg",
}


class DutyBellHTTPServer(ThreadingHTTPServer):
    """HTTP server carrying application dependencies."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        service: DutyBellService,
        *,
        create_token: str | None = None,
    ) -> None:
        self.service = service
        self.create_token = create_token
        super().__init__(server_address, DutyBellRequestHandler)


class DutyBellRequestHandler(BaseHTTPRequestHandler):
    """JSON API plus an allowlisted package-static host."""

    server_version = f"DutyBell/{__version__}"
    error_content_type = "application/json"
    protocol_version = "HTTP/1.1"
    _static_cache: ClassVar[dict[str, bytes]] = {}

    @property
    def app_server(self) -> DutyBellHTTPServer:
        return cast(DutyBellHTTPServer, self.server)

    def do_GET(self) -> None:
        self._dispatch(send_body=True)

    def do_HEAD(self) -> None:
        self._dispatch(send_body=False)

    def do_POST(self) -> None:
        try:
            self._handle_post()
        except Exception as error:  # API boundary converts expected errors to JSON.
            self._handle_error(error)

    def do_OPTIONS(self) -> None:
        self._send_json(
            HTTPStatus.METHOD_NOT_ALLOWED,
            {"error": "cross-origin requests are not supported"},
            extra_headers={"Allow": "GET, HEAD, POST"},
        )

    def _dispatch(self, *, send_body: bool) -> None:
        try:
            parsed = urlsplit(self.path)
            path = parsed.path
            if path == "/healthz":
                self._send_json(
                    HTTPStatus.OK,
                    {"ok": True, "version": __version__},
                    send_body=send_body,
                )
                return
            if path == "/api/meta":
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "version": __version__,
                        "create_requires_token": self.app_server.create_token is not None,
                    },
                    send_body=send_body,
                )
                return
            if path.startswith("/api/rooms/"):
                self._handle_room_get(path, parse_qs(parsed.query), send_body=send_body)
                return
            self._serve_static(path, send_body=send_body)
        except Exception as error:  # HTTP boundary.
            self._handle_error(error, send_body=send_body)

    def _handle_post(self) -> None:
        path = urlsplit(self.path).path
        body = self._read_json_body()
        if path == "/api/rooms":
            required_token = self.app_server.create_token
            if required_token is not None:
                supplied = self.headers.get("X-DutyBell-Create-Token", "")
                import hmac

                if not hmac.compare_digest(required_token, supplied):
                    raise AuthenticationError("room creation token is invalid")
            room, access_key = self.app_server.service.create_room(
                name=body.get("name"),
                interval_seconds=body.get("interval_seconds"),
                participants=body.get("participants"),
                repeat_on_ack=body.get("repeat_on_ack", True),
                rotate_on_ack=body.get("rotate_on_ack", True),
                start=body.get("start", False),
                actor=body.get("actor", "owner"),
            )
            self._send_json(
                HTTPStatus.CREATED,
                {
                    "room": self.app_server.service.snapshot(room),
                    "access_key": access_key,
                },
            )
            return

        parts = self._room_path_parts(path)
        if len(parts) == 4 and parts[3] == "actions":
            room_id = parts[2]
            room = self.app_server.service.perform(
                room_id,
                self._bearer_token(),
                action=body.get("action"),
                actor=body.get("actor"),
                expected_version=body.get("expected_version"),
                data=body.get("data") if isinstance(body.get("data"), dict) else {},
            )
            self._send_json(HTTPStatus.OK, {"room": self.app_server.service.snapshot(room)})
            return
        raise NotFoundError("endpoint not found")

    def _handle_room_get(self, path: str, query: dict[str, list[str]], *, send_body: bool) -> None:
        parts = self._room_path_parts(path)
        if len(parts) not in {3, 4}:
            raise NotFoundError("endpoint not found")
        room_id = parts[2]
        access_key = self._bearer_token()
        room = self.app_server.service.get_room(room_id, access_key)

        if len(parts) == 3:
            self._send_json(
                HTTPStatus.OK,
                {"room": self.app_server.service.snapshot(room)},
                send_body=send_body,
            )
            return
        if parts[3] == "events":
            events = [
                event.to_dict() for event in self.app_server.service.store.list_events(room.room_id)
            ]
            self._send_json(HTTPStatus.OK, {"events": events}, send_body=send_body)
            return
        if parts[3] == "wait":
            after = self._query_int(query, "after", default=room.version, minimum=0, maximum=2**31)
            timeout = self._query_int(query, "timeout", default=25, minimum=0, maximum=30)
            latest = self.app_server.service.store.wait_for_version(room.room_id, after, timeout)
            self._send_json(
                HTTPStatus.OK,
                {
                    "changed": latest.version > after,
                    "room": self.app_server.service.snapshot(latest),
                },
                send_body=send_body,
            )
            return
        raise NotFoundError("endpoint not found")

    @staticmethod
    def _room_path_parts(path: str) -> list[str]:
        return path.strip("/").split("/")

    @staticmethod
    def _query_int(
        query: dict[str, list[str]],
        name: str,
        *,
        default: int,
        minimum: int,
        maximum: int,
    ) -> int:
        raw = query.get(name, [str(default)])[0]
        try:
            value = int(raw)
        except ValueError as error:
            raise ValidationError(f"{name} must be an integer") from error
        if not minimum <= value <= maximum:
            raise ValidationError(f"{name} must be between {minimum} and {maximum}")
        return value

    def _bearer_token(self) -> str:
        header = self.headers.get("Authorization", "")
        scheme, separator, value = header.partition(" ")
        if separator != " " or scheme.lower() != "bearer" or not value.strip():
            raise AuthenticationError("Authorization: Bearer <room-key> is required")
        if len(value) > 256:
            raise AuthenticationError("room access key is invalid")
        return value.strip()

    def _read_json_body(self) -> dict[str, Any]:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise ValidationError("Content-Type must be application/json")
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ValidationError("Content-Length is required")
        try:
            length = int(raw_length)
        except ValueError as error:
            raise ValidationError("Content-Length must be an integer") from error
        if not 0 <= length <= MAX_BODY_BYTES:
            raise ValidationError(f"request body must not exceed {MAX_BODY_BYTES} bytes")
        raw = self.rfile.read(length)
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValidationError("request body must be valid UTF-8 JSON") from error
        if not isinstance(value, dict):
            raise ValidationError("request body must be a JSON object")
        return value

    def _serve_static(self, path: str, *, send_body: bool) -> None:
        name = "index.html" if path in {"", "/"} else path.removeprefix("/")
        if name not in STATIC_NAMES:
            raise NotFoundError("page not found")
        data = self._static_cache.get(name)
        if data is None:
            resource = files("dutybell").joinpath("static", name)
            data = resource.read_bytes()
            self._static_cache[name] = data
        content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        if name.endswith(".mjs"):
            content_type = "text/javascript"
        if name.endswith(".webmanifest"):
            content_type = "application/manifest+json"
        self._send_bytes(
            HTTPStatus.OK,
            data,
            content_type=f"{content_type}; charset=utf-8"
            if content_type.startswith("text/")
            else content_type,
            send_body=send_body,
            extra_headers={"Cache-Control": "no-cache"},
        )

    def _handle_error(self, error: Exception, *, send_body: bool = True) -> None:
        status = HTTPStatus.INTERNAL_SERVER_ERROR
        code = "internal_error"
        payload: dict[str, Any]
        if isinstance(error, ValidationError):
            status, code = HTTPStatus.BAD_REQUEST, "validation_error"
        elif isinstance(error, AuthenticationError):
            status, code = HTTPStatus.UNAUTHORIZED, "authentication_error"
        elif isinstance(error, NotFoundError):
            status, code = HTTPStatus.NOT_FOUND, "not_found"
        elif isinstance(error, ConflictError):
            status, code = HTTPStatus.CONFLICT, "version_conflict"
        elif isinstance(error, StateError):
            status, code = HTTPStatus.UNPROCESSABLE_ENTITY, "state_error"

        if isinstance(error, DutyBellError):
            payload = {"error": code, "message": str(error)}
            if isinstance(error, ConflictError) and error.current is not None:
                payload["room"] = self.app_server.service.snapshot(error.current)
        else:
            LOGGER.exception("Unhandled request failure")
            payload = {"error": code, "message": "internal server error"}
        self._send_json(status, payload, send_body=send_body)

    def _send_json(
        self,
        status: HTTPStatus,
        payload: dict[str, Any],
        *,
        send_body: bool = True,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        data = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        self._send_bytes(
            status,
            data,
            content_type="application/json; charset=utf-8",
            send_body=send_body,
            extra_headers=extra_headers,
        )

    def _send_bytes(
        self,
        status: HTTPStatus,
        data: bytes,
        *,
        content_type: str,
        send_body: bool,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; "
            "frame-ancestors 'none'; form-action 'self'",
        )
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if send_body:
            self.wfile.write(data)

    def log_message(self, format_string: str, *args: object) -> None:
        safe_path = urlsplit(self.path).path
        LOGGER.info("%s %s %s", self.command, safe_path, format_string % args)


def serve(
    service: DutyBellService,
    *,
    host: str = "127.0.0.1",
    port: int = 8742,
    create_token: str | None = None,
) -> None:
    """Run the blocking DutyBell HTTP server."""

    server = DutyBellHTTPServer((host, port), service, create_token=create_token)
    LOGGER.warning("DutyBell %s listening on http://%s:%s", __version__, host, server.server_port)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        LOGGER.warning("DutyBell stopping")
    finally:
        server.server_close()


def static_directory() -> Path:
    """Return a concrete static path in editable/source installations."""

    return Path(str(files("dutybell").joinpath("static")))
