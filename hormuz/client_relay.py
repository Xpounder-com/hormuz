"""Launcher-owned loopback relay for client-side context optimization."""

from __future__ import annotations

import hmac
import http.client
import json
import os
import re
import secrets
import shutil
import ssl
import stat
import subprocess
import threading
import urllib.parse
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Literal, cast

from .compaction import MAX_REQUEST_BYTES, CompactionResult, optimize_request, serialize_request
from .client_versions import SUPPORTED_CLIENT_VERSIONS
from .compaction_formats import CompactionFormatError, strict_json_loads
from .compaction_contract import (
    CONTEXT_FORMAT_HEADER,
    CONTEXT_FORMAT_VERSION,
    CONTEXT_FORMATS_HEADER,
)
from .compaction_protocols import derive_selections
from .compaction_runtime import (
    ContextPreferenceStore,
    ContextRuntimeError,
    load_token_counters,
)
from .session_client import SessionClientError, validate_session_gateway


MAX_RELAY_BODY_BYTES = 25 * 1024 * 1024
RELAY_CHUNK_BYTES = 16 * 1024
_LOCAL_CREDENTIAL = re.compile(r"hox_l_[A-Za-z0-9_-]{43}")
_ACCESS_CREDENTIAL = re.compile(r"hox_a_[A-Za-z0-9_-]{43}")
_CLIENT_VERSION = re.compile(r"(?<!\d)(\d+\.\d+\.\d+)(?!\d)")
_HOP_HEADERS = frozenset(
    {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade"}
)


class ClientRelayError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RelayStatus:
    code: str
    changed_blocks: int = 0
    before_bytes: int | None = None
    after_bytes: int | None = None
    before_tokens: dict[str, int] | None = None
    after_tokens: dict[str, int] | None = None


@dataclass(frozen=True)
class SavedClientProfile:
    key: str
    gateway: str
    client: Literal["codex", "claude-code"]
    model: str
    allow_insecure_http: bool


class RelayOptimizer:
    def __init__(
        self,
        *,
        preference_store: ContextPreferenceStore,
        client: str,
        gateway_compatible: bool,
        counters: Mapping[str, Callable[[str], int]] | None = None,
    ):
        self.preference_store = preference_store
        self.client = client
        self.gateway_compatible = gateway_compatible
        self._counters = counters
        self._resources_checked = counters is not None
        self._lock = threading.Lock()
        self._status = RelayStatus("off")

    @property
    def status(self) -> RelayStatus:
        with self._lock:
            return self._status

    def prepare(self, body: bytes, path: str) -> tuple[bytes, dict[str, str]]:
        try:
            enabled = self.preference_store.load().enabled
        except ContextRuntimeError:
            self._set_status(RelayStatus("settings_invalid"))
            return body, {}
        if not enabled:
            self._set_status(RelayStatus("off"))
            return body, {}
        if self.client not in {"codex", "claude-code"}:
            self._set_status(RelayStatus("unsupported_client"))
            return body, {}
        if not self.gateway_compatible:
            self._set_status(RelayStatus("gateway_incompatible"))
            return body, {}
        protocol = _protocol_for_path(path)
        if protocol is None or len(body) > MAX_REQUEST_BYTES:
            self._set_status(RelayStatus("unsupported_history" if protocol is not None else "unsupported_client"))
            return body, {}
        try:
            payload = strict_json_loads(body.decode("utf-8"))
        except (CompactionFormatError, UnicodeDecodeError, RecursionError):
            self._set_status(RelayStatus("unsupported_history"))
            return body, {}
        if not isinstance(payload, dict):
            self._set_status(RelayStatus("unsupported_history"))
            return body, {}
        counters = self._token_counters()
        if counters is None:
            return body, {}
        typed_payload = cast(dict[str, object], payload)
        selections = derive_selections(typed_payload, protocol, client=self.client)
        result = optimize_request(typed_payload, protocol, selections, counters, enabled=True)
        self._record_result(result)
        if not result.changed:
            return body, {}
        changed = serialize_request(result.payload).encode("utf-8")
        return changed, {CONTEXT_FORMAT_HEADER: CONTEXT_FORMAT_VERSION}

    def note_oversized_passthrough(self) -> None:
        """Snapshot the toggle without buffering or inspecting a large body."""
        try:
            enabled = self.preference_store.load().enabled
        except ContextRuntimeError:
            self._set_status(RelayStatus("settings_invalid"))
            return
        if not enabled:
            self._set_status(RelayStatus("off"))
        elif self.client not in {"codex", "claude-code"}:
            self._set_status(RelayStatus("unsupported_client"))
        elif not self.gateway_compatible:
            self._set_status(RelayStatus("gateway_incompatible"))
        else:
            self._set_status(RelayStatus("unsupported_history"))

    def _token_counters(self) -> Mapping[str, Callable[[str], int]] | None:
        with self._lock:
            if self._resources_checked:
                return self._counters
            try:
                self._counters = load_token_counters()
            except ContextRuntimeError:
                self._status = RelayStatus("resources_unavailable")
                self._counters = None
            self._resources_checked = True
            return self._counters

    def _record_result(self, result: CompactionResult) -> None:
        code = "ready" if result.reason in {"compacted", "no_savings", "no_eligible_result"} else result.reason
        self._set_status(
            RelayStatus(
                code,
                changed_blocks=result.changed_blocks,
                before_bytes=result.before_bytes,
                after_bytes=result.after_bytes,
                before_tokens=result.before_tokens,
                after_tokens=result.after_tokens,
            )
        )

    def _set_status(self, status: RelayStatus) -> None:
        with self._lock:
            self._status = status


class LocalRelayServer(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True

    def __init__(
        self,
        *,
        gateway: str,
        client: Literal["codex", "claude-code"],
        local_credential: str,
        gateway_credential: Callable[[], str],
        optimizer: RelayOptimizer,
        timeout_seconds: float = 60,
    ):
        if _LOCAL_CREDENTIAL.fullmatch(local_credential) is None:
            raise ClientRelayError("invalid_local_credential")
        allow_insecure = urllib.parse.urlsplit(gateway).scheme == "http"
        try:
            self.gateway = validate_session_gateway(gateway, allow_insecure_http=allow_insecure)
        except SessionClientError as error:
            raise ClientRelayError("invalid_gateway") from error
        self.client_name = client
        self.local_credential = local_credential
        self.gateway_credential = gateway_credential
        self.optimizer = optimizer
        self.timeout_seconds = timeout_seconds
        super().__init__(("127.0.0.1", 0), LocalRelayHandler)

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.server_port}"


class LocalRelayHandler(BaseHTTPRequestHandler):
    server: LocalRelayServer
    protocol_version = "HTTP/1.1"

    def parse_request(self) -> bool:
        if not super().parse_request():
            return False
        hosts = self.headers.get_all("Host", [])
        origins = self.headers.get_all("Origin", [])
        allowed_hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        allowed_origins = {self.server.origin, f"http://localhost:{self.server.server_port}"}
        if (
            len(hosts) != 1
            or hosts[0] not in allowed_hosts
            or len(origins) > 1
            or (origins and origins[0] not in allowed_origins)
        ):
            self._error(HTTPStatus.FORBIDDEN, "local_origin_rejected")
            return False
        return True

    def do_POST(self) -> None:  # noqa: N802
        path = urllib.parse.urlsplit(self.path)
        if path.scheme or path.netloc or path.fragment or path.path not in _allowed_paths(self.server.client_name):
            self._error(HTTPStatus.NOT_FOUND, "local_route_not_found")
            return
        if not self._authenticated():
            self._error(HTTPStatus.UNAUTHORIZED, "local_authentication_failed")
            return
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1:
            self._error(HTTPStatus.LENGTH_REQUIRED, "local_content_length_required")
            return
        try:
            length = int(lengths[0])
        except ValueError:
            self._error(HTTPStatus.BAD_REQUEST, "local_content_length_invalid")
            return
        if length < 0 or length > MAX_RELAY_BODY_BYTES:
            self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "local_request_too_large")
            return
        if self.headers.get("Transfer-Encoding") is not None:
            self._error(HTTPStatus.BAD_REQUEST, "local_transfer_encoding_unsupported")
            return
        try:
            gateway_token = self.server.gateway_credential()
        except Exception:
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, "gateway_credential_unavailable")
            return
        if _ACCESS_CREDENTIAL.fullmatch(gateway_token) is None:
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, "gateway_credential_unavailable")
            return
        connection: http.client.HTTPConnection | None = None
        self._response_started = False
        try:
            connection = _gateway_connection(self.server.gateway, self.server.timeout_seconds)
            upstream_path = _gateway_path(self.server.gateway, path.path, path.query)
            if length <= MAX_REQUEST_BYTES:
                body = self.rfile.read(length)
                if len(body) != length:
                    self.close_connection = True
                    return
                changed, context_headers = self.server.optimizer.prepare(body, path.path)
                headers = _forward_headers(
                    self.headers, gateway_token, len(changed), context_headers
                )
                connection.request("POST", upstream_path, body=changed, headers=headers)
            else:
                self.server.optimizer.note_oversized_passthrough()
                headers = _forward_headers(self.headers, gateway_token, length, {})
                connection.putrequest("POST", upstream_path, skip_accept_encoding=True)
                for name, value in headers.items():
                    connection.putheader(name, value)
                connection.endheaders()
                remaining = length
                while remaining:
                    chunk = self.rfile.read(min(RELAY_CHUNK_BYTES, remaining))
                    if not chunk:
                        self.close_connection = True
                        return
                    connection.send(chunk)
                    remaining -= len(chunk)
            response = connection.getresponse()
            self._relay_response(response)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except (OSError, ssl.SSLError, http.client.HTTPException):
            if not self._response_started:
                self._error(HTTPStatus.BAD_GATEWAY, "gateway_unavailable")
            self.close_connection = True
        finally:
            if connection is not None:
                connection.close()

    def _relay_response(self, response: http.client.HTTPResponse) -> None:
        try:
            self._response_started = True
            self.send_response(response.status)
            for name, value in response.getheaders():
                lower = name.lower()
                if lower not in _HOP_HEADERS and lower != "set-cookie":
                    self.send_header(name, value)
            self.send_header("Connection", "close")
            self.end_headers()
            while chunk := response.read(RELAY_CHUNK_BYTES):
                self.wfile.write(chunk)
                self.wfile.flush()
        finally:
            response.close()

    def do_CONNECT(self) -> None:  # noqa: N802
        self._error(HTTPStatus.METHOD_NOT_ALLOWED, "local_method_not_allowed")

    def do_GET(self) -> None:  # noqa: N802
        self._error(HTTPStatus.METHOD_NOT_ALLOWED, "local_method_not_allowed")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._error(HTTPStatus.METHOD_NOT_ALLOWED, "local_method_not_allowed")

    def _authenticated(self) -> bool:
        values: list[str] = []
        authorizations = self.headers.get_all("Authorization", [])
        api_keys = self.headers.get_all("X-Api-Key", [])
        if len(authorizations) > 1 or len(api_keys) > 1:
            return False
        if authorizations:
            authorization = authorizations[0]
            if authorization.lower().startswith("bearer "):
                values.append(authorization[7:].strip())
        if api_keys and api_keys[0].strip():
            values.append(api_keys[0].strip())
        return len(values) == 1 and hmac.compare_digest(values[0], self.server.local_credential)

    def _error(self, status: HTTPStatus, code: str) -> None:
        body = json.dumps({"error": {"code": code}}, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        # Request paths and model content are intentionally absent from logs.
        return


def new_local_credential() -> str:
    return "hox_l_" + secrets.token_urlsafe(32)


def probe_gateway_capability(gateway: str, *, timeout_seconds: float = 5) -> bool:
    connection: http.client.HTTPConnection | None = None
    try:
        allow_insecure = urllib.parse.urlsplit(gateway).scheme == "http"
        gateway = validate_session_gateway(gateway, allow_insecure_http=allow_insecure)
        connection = _gateway_connection(gateway, timeout_seconds)
        path = _gateway_path(gateway, "/health", "")
        connection.request("GET", path, headers={"Accept": "application/json", "Cache-Control": "no-store"})
        response = connection.getresponse()
        response.read(128 * 1024 + 1)
        compatible = (
            response.status == HTTPStatus.OK
            and response.getheader(CONTEXT_FORMATS_HEADER) == CONTEXT_FORMAT_VERSION
        )
        response.close()
        return compatible
    except (OSError, ssl.SSLError, http.client.HTTPException, SessionClientError, ValueError):
        return False
    finally:
        if connection is not None:
            connection.close()


def load_saved_profile(directory: Path, profile: str) -> SavedClientProfile:
    try:
        root = directory.lstat()
        if (
            not stat.S_ISDIR(root.st_mode)
            or directory.is_symlink()
            or root.st_uid != os.getuid()
            or root.st_mode & 0o077
        ):
            raise ClientRelayError("profile_invalid")
        path = directory / "profile.json"
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_nlink != 1
                or info.st_mode & 0o077
                or info.st_size > 1024 * 1024
            ):
                raise ClientRelayError("profile_invalid")
            chunks: list[bytes] = []
            remaining = 1024 * 1024 + 1
            while remaining:
                chunk = os.read(fd, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
            if len(data) > 1024 * 1024:
                raise ClientRelayError("profile_invalid")
        finally:
            os.close(fd)
        value = strict_json_loads(data.decode("utf-8"))
    except ClientRelayError:
        raise
    except (OSError, CompactionFormatError, UnicodeDecodeError, RecursionError) as error:
        raise ClientRelayError("profile_unavailable") from error
    if not isinstance(value, dict):
        raise ClientRelayError("profile_invalid")
    required = {"id", "gateway", "organization", "client", "model", "allowLoopbackHTTP"}
    if not required <= set(value) or set(value) - (required | {"issuer", "setup"}):
        raise ClientRelayError("profile_invalid")
    identifier = value.get("id")
    organization = value.get("organization")
    issuer = value.get("issuer")
    client = value.get("client")
    model = value.get("model")
    allow = value.get("allowLoopbackHTTP")
    setup = value.get("setup")
    if (
        not isinstance(identifier, str)
        or _canonical_uuid(identifier) is None
        or identifier.lower() != profile.lower()
        or not _safe_profile_text(organization, maximum=200, required=True)
        or not _safe_profile_text(issuer, maximum=2048, required=False)
        or client not in {"codex", "claude-code"}
        or not isinstance(model, str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", model)
        or not isinstance(allow, bool)
        or (
            "setup" in value
            and (not isinstance(setup, str) or setup not in {"custom", "openai-pilot"})
        )
    ):
        raise ClientRelayError("profile_invalid")
    try:
        gateway = validate_session_gateway(value.get("gateway"), allow_insecure_http=allow)
    except (SessionClientError, TypeError) as error:
        raise ClientRelayError("profile_invalid") from error
    if setup == "openai-pilot" and (
        client != "codex"
        or model not in {"openai-primary", "openai-secondary"}
        or allow
        or urllib.parse.urlsplit(gateway).scheme != "https"
    ):
        raise ClientRelayError("profile_invalid")
    return SavedClientProfile(profile.lower(), gateway, client, model, allow)


def _canonical_uuid(value: str) -> str | None:
    try:
        parsed = str(uuid.UUID(value))
    except (ValueError, AttributeError):
        return None
    return parsed if value.lower() == parsed else None


def _safe_profile_text(value: object, *, maximum: int, required: bool) -> bool:
    if value is None:
        return not required
    return (
        isinstance(value, str)
        and bool(value)
        and len(value.encode("utf-8")) <= maximum
        and value == value.strip()
        and not any(character in value for character in ("\x00", "\r", "\n"))
    )


def run_client(
    *,
    profile: SavedClientProfile,
    state_directory: Path,
    credential_helper: Path,
    counters: Mapping[str, Callable[[str], int]] | None = None,
) -> int:
    if not credential_helper.is_absolute() or not credential_helper.is_file() or not os.access(credential_helper, os.X_OK):
        raise ClientRelayError("credential_helper_invalid")
    client_executable = supported_client_executable(profile.client)

    def gateway_credential() -> str:
        try:
            process = subprocess.run(
                [str(credential_helper), "credential", "--profile", profile.key,
                 "--state-directory", str(state_directory)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise ClientRelayError("gateway_credential_unavailable") from error
        if process.returncode != 0 or len(process.stdout) > 4096:
            raise ClientRelayError("gateway_credential_unavailable")
        return process.stdout.decode("utf-8", errors="strict").strip()

    preference = ContextPreferenceStore(state_directory, profile.key)
    optimizer = RelayOptimizer(
        preference_store=preference,
        client=profile.client,
        gateway_compatible=probe_gateway_capability(profile.gateway),
        counters=counters,
    )
    local_credential = new_local_credential()
    server = LocalRelayServer(
        gateway=profile.gateway,
        client=profile.client,
        local_credential=local_credential,
        gateway_credential=gateway_credential,
        optimizer=optimizer,
    )
    thread = threading.Thread(target=server.serve_forever, name="hormuz-context-relay", daemon=True)
    thread.start()
    try:
        command, environment = _client_command(
            profile, server.origin, local_credential, executable=client_executable
        )
        process = subprocess.run(command, env=environment, check=False)
        return int(process.returncode)
    except OSError as error:
        raise ClientRelayError("client_launch_failed") from error
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _client_command(
    profile: SavedClientProfile,
    relay_origin: str,
    local_credential: str,
    *,
    executable: str | None = None,
) -> tuple[list[str], dict[str, str]]:
    executable = executable or shutil.which("codex" if profile.client == "codex" else "claude")
    if executable is None:
        raise ClientRelayError("supported_client_not_installed")
    if profile.client == "codex":
        blocked_names = {
            "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_ORGANIZATION",
            "OPENAI_PROJECT", "CODEX_API_KEY",
        }
    else:
        blocked_names = {
            "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_CUSTOM_HEADERS",
            "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
        }
    environment = {
        name: value for name, value in os.environ.items()
        if name not in blocked_names
    }
    if profile.client == "codex":
        environment.update({"HORMUZ_LOCAL_RELAY_TOKEN": local_credential})
        provider = (
            '{name="Hormuz",base_url=' + json.dumps(relay_origin + "/v1")
            + ',wire_api="responses",requires_openai_auth=false,env_key="HORMUZ_LOCAL_RELAY_TOKEN"}'
        )
        return (
            [executable, "-c", 'model_provider="hormuz_context_relay"', "-c",
             "model_providers.hormuz_context_relay=" + provider, "-c", "model=" + json.dumps(profile.model)],
            environment,
        )
    environment.update(
        {
            "ANTHROPIC_BASE_URL": relay_origin,
            "ANTHROPIC_API_KEY": "",
            "ANTHROPIC_AUTH_TOKEN": local_credential,
            "ANTHROPIC_MODEL": profile.model,
            "ANTHROPIC_DEFAULT_OPUS_MODEL": profile.model,
            "ANTHROPIC_DEFAULT_SONNET_MODEL": profile.model,
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": profile.model,
        }
    )
    return [executable, "--model", profile.model], environment


def supported_client_executable(client: str) -> str:
    command = "codex" if client == "codex" else "claude" if client == "claude-code" else None
    expected = SUPPORTED_CLIENT_VERSIONS.get(client)
    executable = shutil.which(command) if command is not None else None
    if executable is None or expected is None:
        raise ClientRelayError("unsupported_client")
    try:
        completed = subprocess.run(
            [executable, "--version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
            check=False,
        )
        output = completed.stdout + b" " + completed.stderr
    except (OSError, subprocess.SubprocessError) as error:
        raise ClientRelayError("unsupported_client") from error
    if completed.returncode != 0 or len(output) > 4096:
        raise ClientRelayError("unsupported_client")
    try:
        match = _CLIENT_VERSION.search(output.decode("utf-8", errors="strict"))
    except UnicodeDecodeError as error:
        raise ClientRelayError("unsupported_client") from error
    if match is None or match.group(1) != expected:
        raise ClientRelayError("unsupported_client")
    return executable


def _protocol_for_path(path: str) -> Literal["responses", "anthropic"] | None:
    if path in {"/v1/responses", "/v1/responses/compact"}:
        return "responses"
    if path == "/v1/messages":
        return "anthropic"
    return None


def _allowed_paths(client: str) -> frozenset[str]:
    if client == "codex":
        return frozenset({"/v1/responses", "/v1/responses/compact"})
    return frozenset({"/v1/messages", "/v1/messages/count_tokens"})


def _gateway_connection(gateway: str, timeout: float) -> http.client.HTTPConnection:
    parsed = urllib.parse.urlsplit(gateway)
    if parsed.scheme == "https":
        return http.client.HTTPSConnection(parsed.hostname, parsed.port or 443, timeout=timeout, context=ssl.create_default_context())
    return http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=timeout)


def _gateway_path(gateway: str, path: str, query: str) -> str:
    base = urllib.parse.urlsplit(gateway).path.rstrip("/")
    result = base + path
    return result + ("?" + query if query else "")


def _forward_headers(
    headers: http.client.HTTPMessage,
    gateway_token: str,
    content_length: int,
    context_headers: Mapping[str, str],
) -> dict[str, str]:
    result = {
        "Authorization": "Bearer " + gateway_token,
        "Content-Type": headers.get("Content-Type", "application/json"),
        "Accept": headers.get("Accept", "application/json"),
        "Content-Length": str(content_length),
        "Cache-Control": "no-store",
    }
    for name in ("User-Agent", "OpenAI-Beta", "Anthropic-Version", "Anthropic-Beta", "X-Hormuz-Work-Attribution"):
        value = headers.get(name)
        if value is not None and "\r" not in value and "\n" not in value:
            result[name] = value
    result.update(context_headers)
    return result
