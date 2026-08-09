#!/usr/bin/env python3
"""Run DutyBell's fail-fast source, package, and installed-runtime release gate."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import venv
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]


def announce(label: str) -> None:
    print(f"\n==> {label}", flush=True)


def run(label: str, command: list[str], *, environment: dict[str, str] | None = None) -> None:
    announce(label)
    subprocess.run(command, cwd=ROOT, env=environment, check=True)


def source_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")
    return environment


def available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def request_json(
    method: str,
    url: str,
    *,
    body: dict[str, Any] | None = None,
    token: str | None = None,
) -> tuple[int, dict[str, Any], dict[str, str]]:
    data = None if body is None else json.dumps(body).encode()
    headers = {"Content-Type": "application/json"} if data is not None else {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        response = urllib.request.urlopen(request, timeout=5)
    except urllib.error.HTTPError as error:
        payload = cast(dict[str, Any], json.loads(error.read()))
        return error.code, payload, dict(error.headers.items())
    with response:
        payload = cast(dict[str, Any], json.loads(response.read()))
        return response.status, payload, dict(response.headers.items())


def wait_until_ready(base_url: str, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            _stdout, stderr = process.communicate()
            raise RuntimeError(f"installed server exited before readiness:\n{stderr}")
        try:
            status, payload, _headers = request_json("GET", f"{base_url}/healthz")
            if status == 200 and payload.get("ok") is True:
                return
        except OSError:
            pass
        time.sleep(0.1)
    raise RuntimeError("installed server did not become ready within 10 seconds")


def installed_http_smoke(python: Path, working_directory: Path) -> None:
    port = available_port()
    base_url = f"http://127.0.0.1:{port}"
    database = working_directory / "runtime.db"
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        [
            str(python),
            "-m",
            "dutybell",
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--database",
            str(database),
        ],
        cwd=working_directory,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=creation_flags,
    )
    try:
        wait_until_ready(base_url, process)
        status, created, headers = request_json(
            "POST",
            f"{base_url}/api/rooms",
            body={
                "name": "Release smoke",
                "interval_seconds": 2,
                "participants": ["Alex", "Sam"],
                "repeat_on_ack": True,
                "rotate_on_ack": True,
                "start": True,
                "actor": "Alex",
            },
        )
        if status != 201 or headers.get("X-Content-Type-Options") != "nosniff":
            raise RuntimeError(f"room creation contract failed: status={status}, headers={headers}")
        room = cast(dict[str, Any], created["room"])
        room_id = str(room["room_id"])
        key = str(created["access_key"])

        for _client in range(2):
            read_status, read, _headers = request_json(
                "GET", f"{base_url}/api/rooms/{room_id}", token=key
            )
            if read_status != 200 or cast(dict[str, Any], read["room"])["version"] != 1:
                raise RuntimeError("two-client read contract failed")

        action_status, acknowledged, _headers = request_json(
            "POST",
            f"{base_url}/api/rooms/{room_id}/actions",
            token=key,
            body={"action": "ack", "actor": "Alex", "expected_version": 1, "data": {}},
        )
        next_room = cast(dict[str, Any], acknowledged["room"])
        if (
            action_status != 200
            or next_room["version"] != 2
            or next_room["assignee"] != "Sam"
            or next_room["status"] != "running"
        ):
            raise RuntimeError(f"acknowledgement contract failed: {acknowledged}")

        wait_status, waited, _headers = request_json(
            "GET", f"{base_url}/api/rooms/{room_id}/wait?after=1&timeout=0", token=key
        )
        if wait_status != 200 or waited["changed"] is not True:
            raise RuntimeError(f"long-poll change contract failed: {waited}")

        stale_status, stale, _headers = request_json(
            "POST",
            f"{base_url}/api/rooms/{room_id}/actions",
            token=key,
            body={"action": "stop", "actor": "Sam", "expected_version": 1, "data": {}},
        )
        if stale_status != 409 or cast(dict[str, Any], stale["room"])["version"] != 2:
            raise RuntimeError(f"stale-write contract failed: status={stale_status}, body={stale}")
    finally:
        process.terminate()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)


def venv_python(directory: Path) -> Path:
    return directory / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def verify_installed_wheel(wheel: Path) -> None:
    announce("clean-wheel installation and real HTTP smoke")
    with tempfile.TemporaryDirectory(prefix="dutybell-install-") as directory:
        root = Path(directory)
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)
        venv.EnvBuilder(with_pip=True, clear=True).create(root / "venv")
        python = venv_python(root / "venv")
        subprocess.run(
            [str(python), "-m", "pip", "install", "--no-deps", str(wheel)],
            cwd=root,
            env=environment,
            check=True,
        )
        subprocess.run(
            [str(python), "-m", "dutybell", "--version"],
            cwd=root,
            env=environment,
            check=True,
        )
        subprocess.run(
            [
                str(python),
                "-m",
                "dutybell",
                "doctor",
                "--database",
                str(root / "doctor.db"),
            ],
            cwd=root,
            env=environment,
            check=True,
        )
        installed_http_smoke(python, root)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="run source checks only; never use this option for a release",
    )
    args = parser.parse_args()
    environment = source_environment()

    run("format check", [sys.executable, "-m", "ruff", "format", "--check", "."])
    run("lint", [sys.executable, "-m", "ruff", "check", "."])
    run("strict typing", [sys.executable, "-m", "mypy", "src", "tests", "scripts"])
    run("coverage reset", [sys.executable, "-m", "coverage", "erase"], environment=environment)
    run(
        "Python tests",
        [
            sys.executable,
            "-m",
            "coverage",
            "run",
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-v",
        ],
        environment=environment,
    )
    run(
        "90% branch-aware coverage",
        [sys.executable, "-m", "coverage", "report", "-m"],
        environment=environment,
    )
    node = shutil.which("node")
    if node is None:
        raise RuntimeError("Node.js is required for browser-core tests")
    web_tests = sorted(str(path) for path in (ROOT / "web_tests").glob("*.test.mjs"))
    run("browser-core tests", [node, "--test", *web_tests])
    run("documentation and workflow syntax", [sys.executable, "scripts/docs_check.py"])
    run("credential and attribution scan", [sys.executable, "scripts/secret_scan.py"])

    if not args.skip_build:
        run("two-pass reproducible package", [sys.executable, "scripts/package_release.py"])
        wheels = sorted((ROOT / "dist").glob("dutybell-*.whl"))
        if len(wheels) != 1:
            raise RuntimeError(
                f"expected one wheel in dist, found {[path.name for path in wheels]}"
            )
        verify_installed_wheel(wheels[0])

    print("\nRELEASE CHECK PASSED", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
