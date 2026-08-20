from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from . import __version__
from .config import is_model_identifier
from .provider_conformance import (
    ProviderConformanceError,
    _strict_json_object,
    _validate_gateway,
    _valid_credential,
)


RESULT_SCHEMA = "hormuz.client-conformance.v1"
PROBE_VERSION = "hormuz-fixed-client-text-v1"
MAX_CLIENT_OUTPUT_BYTES = 1024 * 1024
MAX_FINAL_MESSAGE_BYTES = 4096
MAX_EXECUTABLE_BYTES = 512 * 1024 * 1024
_MARKER = "HORMUZ_CLIENT_OK_1"
_PROMPT = "Reply with exactly HORMUZ_CLIENT_OK_1 and do not call tools."
_CHILD_CREDENTIAL_ENV = "HORMUZ_CLIENT_CONFORMANCE_TOKEN"
_VERSION_PATTERN = re.compile(
    r"(?<![0-9])([0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?)(?![0-9])"
)
_SAFE_PARENT_ENV = (
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "PATH",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "SYSTEMROOT",
    "WINDIR",
)


class ClientConformanceError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class ClientConformanceRunner:
    """Exercise a stock Codex or Claude Code binary through Hormuz."""

    def __init__(
        self,
        client: str,
        *,
        gateway: str,
        credential: str,
        expected_version: str,
        expected_executable_sha256: str,
        timeout_seconds: float = 120,
        allow_insecure_http: bool = False,
        executable: str | None = None,
        clock: Callable[[], float] = time.monotonic,
        process_runner: Callable[..., tuple[int, bytes]] | None = None,
    ) -> None:
        if client not in {"codex", "claude"}:
            raise ClientConformanceError("invalid_client")
        if not _valid_credential(credential):
            raise ClientConformanceError("invalid_credential")
        if (
            not isinstance(expected_version, str)
            or _VERSION_PATTERN.fullmatch(expected_version) is None
        ):
            raise ClientConformanceError("invalid_expected_version")
        if (
            not isinstance(expected_executable_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", expected_executable_sha256) is None
        ):
            raise ClientConformanceError("invalid_expected_executable_sha256")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not 5 <= timeout_seconds <= 600
        ):
            raise ClientConformanceError("invalid_timeout")
        try:
            gateway_v1, gateway_transport = _validate_gateway(
                gateway,
                allow_insecure_http=allow_insecure_http,
            )
        except ProviderConformanceError as error:
            raise ClientConformanceError(error.code) from None
        self.client = client
        self.credential = credential
        self.timeout_seconds = float(timeout_seconds)
        self.gateway_v1 = gateway_v1
        self.gateway_root = gateway_v1.removesuffix("/v1")
        self.gateway_transport = gateway_transport
        self.executable = _resolve_executable(executable or client)
        self.expected_version = expected_version
        self.executable_sha256 = _executable_sha256(self.executable)
        if self.executable_sha256 != expected_executable_sha256:
            raise ClientConformanceError("executable_digest_mismatch")
        self._clock = clock
        self._process_runner = process_runner or _run_bounded

    def run(self, *, model: str) -> dict[str, Any]:
        if not isinstance(model, str) or not is_model_identifier(model):
            raise ClientConformanceError("invalid_model")
        try:
            with tempfile.TemporaryDirectory(prefix="hormuz-client-conformance-") as temporary:
                root = Path(temporary)
                version = self._client_version(root)
                environment = _isolated_environment(root)
                started_at = self._clock()
                if self.client == "codex":
                    final_message = root / "codex-final-message.txt"
                    client_home = root / "codex-home"
                    client_home.mkdir(mode=0o700)
                    environment["CODEX_HOME"] = str(client_home)
                    environment[_CHILD_CREDENTIAL_ENV] = self.credential
                    command = self._codex_command(
                        root=root,
                        final_message=final_message,
                        model=model,
                    )
                    return_code, _output = self._run_client(
                        command,
                        environment=environment,
                        cwd=root,
                    )
                    marker_verified = _codex_marker_verified(final_message)
                else:
                    client_home = root / "claude-home"
                    client_home.mkdir(mode=0o700)
                    environment["CLAUDE_CONFIG_DIR"] = str(client_home)
                    environment[_CHILD_CREDENTIAL_ENV] = self.credential
                    environment["ANTHROPIC_BASE_URL"] = self.gateway_root
                    environment["CLAUDE_CODE_API_KEY_HELPER_TTL_MS"] = "1000"
                    environment["CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY"] = "1"
                    environment["DISABLE_AUTOUPDATER"] = "1"
                    settings = root / "claude-settings.json"
                    helper = (
                        "import os,sys;"
                        f"sys.stdout.write(os.environ[{_CHILD_CREDENTIAL_ENV!r}])"
                    )
                    _write_private_json(
                        settings,
                        {"apiKeyHelper": shlex.join([sys.executable, "-c", helper])},
                    )
                    command = self._claude_command(model=model, settings=settings)
                    return_code, output = self._run_client(
                        command,
                        environment=environment,
                        cwd=root,
                    )
                    marker_verified = _claude_marker_verified(output)
                elapsed_milliseconds = max(
                    0,
                    min(2**31 - 1, int(round((self._clock() - started_at) * 1000))),
                )
        except ClientConformanceError:
            raise
        except OSError:
            raise ClientConformanceError("temporary_workspace_failed") from None

        if return_code != 0:
            raise ClientConformanceError("client_failed")
        if not marker_verified:
            raise ClientConformanceError("marker_mismatch")
        client_name = "OpenAI Codex CLI" if self.client == "codex" else "Anthropic Claude Code"
        provider_protocol = "openai" if self.client == "codex" else "anthropic"
        return {
            "schema_version": RESULT_SCHEMA,
            "status": "verified",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "runner": {
                "hormuz_version": __version__,
                "python_version": platform.python_version(),
            },
            "probe_version": PROBE_VERSION,
            "client": {
                "name": client_name,
                "version": version,
                "executable_sha256": self.executable_sha256,
            },
            "provider_protocol": provider_protocol,
            "gateway_interface": (
                "POST /v1/responses" if self.client == "codex" else "POST /v1/messages"
            ),
            "gateway_transport": self.gateway_transport,
            "requested_model": model,
            "client_exit_code": return_code,
            "latency_milliseconds": elapsed_milliseconds,
            "assurances": {
                "fixed_content_probe": True,
                "client_marker_verified": True,
                "client_version_verified": True,
                "executable_sha256_verified": True,
                "host_environment_sanitized": True,
                "isolated_empty_workspace": True,
                "client_persistence_disabled": True,
                "provider_credential_removed_from_client_environment": True,
                "employee_credential_retained": False,
                "gateway_url_retained": False,
                "prompt_retained": False,
                "response_content_retained": False,
                "client_output_retained": False,
            },
        }

    def _client_version(self, root: Path) -> str:
        environment = _isolated_environment(root)
        return_code, output = self._process_runner(
            [str(self.executable), "--version"],
            environment,
            root,
            min(15.0, self.timeout_seconds),
            MAX_FINAL_MESSAGE_BYTES,
        )
        if return_code != 0:
            raise ClientConformanceError("client_version_failed")
        match = _VERSION_PATTERN.search(output.decode("utf-8", errors="replace"))
        if match is None:
            raise ClientConformanceError("client_version_unrecognized")
        version = match.group(1)
        if version != self.expected_version:
            raise ClientConformanceError("client_version_mismatch")
        return version

    def _run_client(
        self,
        command: Sequence[str],
        *,
        environment: dict[str, str],
        cwd: Path,
    ) -> tuple[int, bytes]:
        return self._process_runner(
            command,
            environment,
            cwd,
            self.timeout_seconds,
            MAX_CLIENT_OUTPUT_BYTES,
        )

    def _codex_command(
        self,
        *,
        root: Path,
        final_message: Path,
        model: str,
    ) -> list[str]:
        helper = (
            "import os,sys;"
            f"sys.stdout.write(os.environ[{_CHILD_CREDENTIAL_ENV!r}])"
        )
        return [
            str(self.executable),
            "exec",
            "--ignore-user-config",
            "--strict-config",
            "--skip-git-repo-check",
            "--ephemeral",
            "--sandbox",
            "read-only",
            "--output-last-message",
            str(final_message),
            "-C",
            str(root),
            "-m",
            model,
            "-c",
            'model_provider="company_gateway"',
            "-c",
            'model_providers.company_gateway.name="Hormuz"',
            "-c",
            f"model_providers.company_gateway.base_url={json.dumps(self.gateway_v1)}",
            "-c",
            f"model_providers.company_gateway.auth.command={json.dumps(sys.executable)}",
            "-c",
            "model_providers.company_gateway.auth.args="
            + json.dumps(["-c", helper]),
            "-c",
            "model_providers.company_gateway.auth.refresh_interval_ms=1000",
            "-c",
            'model_providers.company_gateway.wire_api="responses"',
            "-c",
            "features.shell_tool=false",
            "-c",
            "features.multi_agent=false",
            "-c",
            'web_search="disabled"',
            "-c",
            "check_for_update_on_startup=false",
            "-c",
            "analytics.enabled=false",
            "-c",
            "feedback.enabled=false",
            _PROMPT,
        ]

    def _claude_command(self, *, model: str, settings: Path) -> list[str]:
        return [
            str(self.executable),
            "--bare",
            "-p",
            _PROMPT,
            "--output-format",
            "json",
            "--no-session-persistence",
            "--settings",
            str(settings),
            "--tools",
            "",
            "--permission-mode",
            "dontAsk",
            "--model",
            model,
        ]


def _resolve_executable(value: str) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ClientConformanceError("invalid_executable")
    candidate = shutil.which(value)
    if candidate is None:
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise ClientConformanceError("client_not_found")
        candidate = str(path)
    try:
        path = Path(candidate).resolve(strict=True)
        mode = path.stat().st_mode
    except OSError:
        raise ClientConformanceError("client_not_found") from None
    if not stat.S_ISREG(mode) or not os.access(path, os.X_OK):
        raise ClientConformanceError("invalid_executable")
    return path


def _executable_sha256(path: Path) -> str:
    try:
        size = path.stat().st_size
        if not 1 <= size <= MAX_EXECUTABLE_BYTES:
            raise ClientConformanceError("invalid_executable_size")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    except ClientConformanceError:
        raise
    except OSError:
        raise ClientConformanceError("executable_digest_failed") from None
    return digest.hexdigest()


def _isolated_environment(root: Path) -> dict[str, str]:
    environment = {
        name: value
        for name in _SAFE_PARENT_ENV
        if (value := os.environ.get(name)) is not None
    }
    environment.update(
        {
            "HOME": str(root),
            "TMPDIR": str(root),
            "NO_COLOR": "1",
            "TERM": "dumb",
        }
    )
    if os.name == "nt":
        environment.update(
            {
                "APPDATA": str(root / "AppData"),
                "LOCALAPPDATA": str(root / "LocalAppData"),
                "USERPROFILE": str(root),
            }
        )
    return environment


def _write_private_json(path: Path, value: dict[str, Any]) -> None:
    serialized = json.dumps(value, separators=(",", ":"), allow_nan=False)
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(serialized)
    except (OSError, TypeError, ValueError):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise ClientConformanceError("temporary_workspace_failed") from None


def _codex_marker_verified(path: Path) -> bool:
    try:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_FINAL_MESSAGE_BYTES:
            return False
        content = path.read_bytes()
    except OSError:
        return False
    try:
        return content.decode("utf-8").strip() == _MARKER
    except UnicodeDecodeError:
        return False


def _claude_marker_verified(output: bytes) -> bool:
    try:
        value = _strict_json_object(output)
    except ProviderConformanceError:
        return False
    return value.get("result") == _MARKER


def _run_bounded(
    command: Sequence[str],
    environment: dict[str, str],
    cwd: Path,
    timeout_seconds: float,
    maximum_output_bytes: int,
) -> tuple[int, bytes]:
    if maximum_output_bytes < 1:
        raise ClientConformanceError("invalid_output_limit")
    popen_kwargs: dict[str, Any] = {
        "cwd": cwd,
        "env": environment,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        popen_kwargs["start_new_session"] = True
    try:
        process = subprocess.Popen(list(command), **popen_kwargs)
    except (OSError, ValueError):
        raise ClientConformanceError("client_start_failed") from None
    assert process.stdout is not None
    chunks: list[bytes] = []
    output_size = 0
    output_too_large = threading.Event()
    reader_failed = threading.Event()

    def drain_output() -> None:
        nonlocal output_size
        try:
            while True:
                chunk = process.stdout.read(64 * 1024)
                if not chunk:
                    return
                output_size += len(chunk)
                if output_size > maximum_output_bytes:
                    output_too_large.set()
                    _terminate_process_tree(process)
                    return
                chunks.append(chunk)
        except OSError:
            reader_failed.set()
            _terminate_process_tree(process)

    reader = threading.Thread(target=drain_output, daemon=True)
    reader.start()
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        _terminate_process_tree(process)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        reader.join(timeout=5)
        process.stdout.close()
        raise ClientConformanceError("client_timeout") from None
    reader.join(timeout=5)
    if reader.is_alive() or reader_failed.is_set():
        _terminate_process_tree(process)
        process.stdout.close()
        raise ClientConformanceError("client_output_failed")
    if output_too_large.is_set():
        process.stdout.close()
        raise ClientConformanceError("client_output_too_large")
    process.stdout.close()
    return int(process.returncode), b"".join(chunks)


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    try:
        if os.name != "nt":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
    except (OSError, ProcessLookupError):
        pass
    except subprocess.TimeoutExpired:
        pass
    finally:
        if os.name == "nt" and process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass
