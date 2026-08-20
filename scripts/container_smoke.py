#!/usr/bin/env python3
"""Run the Hormuz container under the documented restricted runtime contract."""

from __future__ import annotations

import argparse
import http.client
import json
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[1]
IDENTITY_TOKEN = "smoke-hormuz-identity-token-00000001"
OPENAI_KEY = "smoke-openai-provider-key-00000001"
ANTHROPIC_KEY = "smoke-anthropic-provider-key-000001"


class SmokeFailure(RuntimeError):
    """Raised when the container does not satisfy the deployment contract."""


def _docker(
    arguments: list[str],
    *,
    check: bool = True,
    timeout: float = 45,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            ["docker", *arguments],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SmokeFailure(f"docker command failed: {arguments[0]}") from error
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        suffix = f": {detail[-1]}" if detail else ""
        raise SmokeFailure(f"docker command failed: {arguments[0]}{suffix}")
    return result


def _write_runtime_config(path: Path) -> None:
    config = json.loads((PROJECT_ROOT / "config.example.json").read_text())
    config["listen"] = {
        "host": "0.0.0.0",
        "port": 8787,
        "shutdown_grace_seconds": 5,
    }
    config["database"] = "/var/lib/hormuz/usage.sqlite3"
    config["context_database"] = "/var/lib/hormuz/context.sqlite3"
    path.write_text(json.dumps(config, indent=2) + "\n")
    path.chmod(0o644)


def _mapped_port(container: str) -> int:
    output = _docker(["port", container, "8787/tcp"]).stdout.strip()
    if not output:
        raise SmokeFailure("container has no loopback port mapping")
    try:
        return int(output.rsplit(":", 1)[1])
    except (IndexError, ValueError) as error:
        raise SmokeFailure("container returned an invalid port mapping") from error


def _request_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 2,
) -> tuple[dict[str, Any], bytes]:
    request = Request(url, headers=headers or {})
    with urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise SmokeFailure(f"unexpected HTTP status for {request.full_url}")
        raw = response.read()
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SmokeFailure("container returned invalid JSON") from error
    if not isinstance(value, dict):
        raise SmokeFailure("container returned a non-object JSON response")
    return value, raw


def _wait_until_ready(container: str, port: int) -> None:
    deadline = time.monotonic() + 45
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            ready, _ = _request_json(
                f"http://127.0.0.1:{port}/health/ready", timeout=1
            )
            health = _docker(
                ["inspect", "--format", "{{.State.Health.Status}}", container]
            ).stdout.strip()
            if ready == {
                "schema": "hormuz.health.v1",
                "status": "ready",
                "service": "hormuz",
            } and health == "healthy":
                return
        except (
            SmokeFailure,
            URLError,
            TimeoutError,
            ConnectionError,
            http.client.HTTPException,
        ) as error:
            last_error = error
        time.sleep(0.25)
    raise SmokeFailure("container did not become healthy") from last_error


def _assert_runtime_contract(container: str) -> None:
    inspection = json.loads(_docker(["inspect", container]).stdout)[0]
    if inspection["Config"]["User"] != "65532:65532":
        raise SmokeFailure("container is not configured for UID/GID 65532")
    host = inspection["HostConfig"]
    if host["ReadonlyRootfs"] is not True:
        raise SmokeFailure("container root filesystem is writable")
    if "ALL" not in (host.get("CapDrop") or []):
        raise SmokeFailure("container did not drop all Linux capabilities")
    if not any(
        value.startswith("no-new-privileges")
        for value in (host.get("SecurityOpt") or [])
    ):
        raise SmokeFailure("container allows privilege escalation")

    process = json.loads(
        _docker(
            [
                "exec",
                container,
                "python",
                "-c",
                (
                    "import json, os; "
                    "print(json.dumps({'uid': os.getuid(), 'gid': os.getgid(), "
                    "'usage': os.path.isfile('/var/lib/hormuz/usage.sqlite3'), "
                    "'context': os.path.isfile('/var/lib/hormuz/context.sqlite3'), "
                    "'pyc': any(name.endswith('.pyc') for _, _, names in "
                    "os.walk('/opt/hormuz') for name in names)}))"
                ),
            ]
        ).stdout
    )
    if process != {
        "uid": 65532,
        "gid": 65532,
        "usage": True,
        "context": False,
        "pyc": False,
    }:
        raise SmokeFailure(
            "non-root gateway did not initialize only the supported usage store"
        )


def run_smoke(image: str) -> None:
    suffix = uuid4().hex[:12]
    container = f"hormuz-smoke-{suffix}"
    volume = f"hormuz-smoke-data-{suffix}"
    created_container = False
    created_volume = False
    with tempfile.TemporaryDirectory(prefix="hormuz-container-smoke-") as temporary:
        config_path = Path(temporary) / "hormuz.json"
        _write_runtime_config(config_path)
        try:
            _docker(["volume", "create", "--label", "dev.hormuz.smoke=true", volume])
            created_volume = True
            created_container = True
            _docker(
                [
                    "run",
                    "--detach",
                    "--name",
                    container,
                    "--label",
                    "dev.hormuz.smoke=true",
                    "--read-only",
                    "--tmpfs",
                    "/tmp:rw,noexec,nosuid,nodev,size=16m",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges:true",
                    "--publish",
                    "127.0.0.1::8787",
                    "--mount",
                    f"type=bind,src={config_path},dst=/etc/hormuz/hormuz.json,readonly",
                    "--mount",
                    f"type=volume,src={volume},dst=/var/lib/hormuz",
                    "--env",
                    f"HORMUZ_TOKEN={IDENTITY_TOKEN}",
                    "--env",
                    f"OPENAI_API_KEY={OPENAI_KEY}",
                    "--env",
                    f"ANTHROPIC_API_KEY={ANTHROPIC_KEY}",
                    image,
                ],
                timeout=60,
            )
            port = _mapped_port(container)
            _wait_until_ready(container, port)
            _assert_runtime_contract(container)

            live, live_raw = _request_json(
                f"http://127.0.0.1:{port}/health/live"
            )
            if live != {
                "schema": "hormuz.health.v1",
                "status": "live",
                "service": "hormuz",
            }:
                raise SmokeFailure("liveness response violated its fixed schema")

            models, models_raw = _request_json(
                f"http://127.0.0.1:{port}/v1/models?limit=1000",
                headers={"X-Api-Key": IDENTITY_TOKEN},
            )
            if not isinstance(models.get("data"), list) or not models["data"]:
                raise SmokeFailure("authenticated model discovery returned no models")
            forbidden = (IDENTITY_TOKEN, OPENAI_KEY, ANTHROPIC_KEY)
            if any(value.encode() in live_raw + models_raw for value in forbidden):
                raise SmokeFailure("a synthetic credential appeared in an HTTP response")

            _docker(["stop", "--time", "15", container], timeout=25)
            state = json.loads(
                _docker(
                    ["inspect", "--format", "{{json .State}}", container]
                ).stdout
            )
            if state["Status"] != "exited" or state["ExitCode"] != 0:
                raise SmokeFailure("SIGTERM did not produce a clean bounded exit")
        finally:
            if created_container:
                _docker(["rm", "--force", container], check=False)
            if created_volume:
                _docker(["volume", "rm", "--force", volume], check=False)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--image",
        default="hormuz:container-smoke",
        help="already-built local Hormuz image to verify",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        run_smoke(args.image)
    except SmokeFailure as error:
        print(f"container smoke failed: {error}")
        return 1
    print("container smoke passed: non-root, restricted, healthy, and graceful")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
