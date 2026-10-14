"""Persistent Codex CLI settings and the generation-owned local router lifetime.

One journal owns the reversible settings change and private process control. It
never contains gateway/provider credentials or a copy of the user's document.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import tomllib

from .client_versions import SUPPORTED_CODEX_VERSION
from .config import GatewayConfig
from .policy_runtime import PolicyRuntime

LIMIT = 128 * 1024


class ConnectedClientError(RuntimeError):
    def __init__(self, code: str):
        self.code = "connected_client_" + code
        super().__init__(self.code)


def _read(path: Path, *, private=False) -> str | None:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ConnectedClientError("file_unsafe") from error
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1
                or info.st_mode & (0o077 if private else 0o022) or info.st_size > LIMIT):
            raise ConnectedClientError("file_unsafe")
        try:
            return stream.read(LIMIT + 1).decode("utf-8")
        except UnicodeError as error:
            raise ConnectedClientError("configuration_invalid") from error


def _directory(path: Path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ConnectedClientError("directory_unsafe")


def _write(path: Path, text: str, *, expected: str | None = None, compare=False):
    descriptor, temporary = tempfile.mkstemp(prefix=".hormuz-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(text.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        if compare and _read(path) != expected:
            raise ConnectedClientError("configuration_changed")
        os.replace(temporary, path)
        parent = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _document(text):
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ConnectedClientError("configuration_invalid") from error


def _setting(text, key, value):
    """Edit a simple top-level string token, keeping adjacent comments/bytes."""
    document = _document(text)
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.lstrip().startswith("["):
            break
        match = re.match(r"(\s*" + re.escape(key) + r"\s*=\s*)(\"(?:[^\"\\\n]|\\.)*\"|'[^'\n]*')(.*)$", line.rstrip("\r\n"))
        if match:
            if value is None:
                suffix = match[3].strip()
                lines[index] = (suffix + "\n") if suffix else ""
            else:
                ending = line[len(line.rstrip("\r\n")):]
                lines[index] = match[1] + json.dumps(value) + match[3] + ending
            result = "".join(lines)
            _document(result)
            return result
    if key in document:
        raise ConnectedClientError("configuration_shape_unsupported")
    if value is None:
        return text
    return key + " = " + json.dumps(value) + "\n" + text


def _control(record, command):
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(1)
    try:
        connection.connect(record["control"])
        connection.sendall(json.dumps({"generation": record["generation"], "command": command}).encode() + b"\n")
        with connection.makefile("rb") as stream:
            data = stream.readline(4097)
        if len(data) > 4096:
            raise ValueError()
        value = json.loads(data)
        if value.get("generation") != record["generation"]:
            raise ValueError()
        return value["router"]
    except (OSError, ValueError, KeyError):
        return "unavailable"
    finally:
        connection.close()


class ConnectedClientLifetime:
    def __init__(self, home: Path):
        if os.name != "posix":
            raise ConnectedClientError("platform_unsupported")
        if home.is_symlink():
            raise ConnectedClientError("directory_unsafe")
        self.home = home.expanduser().absolute().resolve()
        self.directory = self.home / ".hormuz-router"
        self.record_path = self.directory / "connection.json"
        self.config_path = self.home / "config.toml"

    @contextmanager
    def _transaction(self):
        import fcntl
        _directory(self.home)
        _directory(self.directory)
        descriptor = os.open(self.directory / "lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_mode & 0o077:
                raise ConnectedClientError("file_unsafe")
            deadline = time.monotonic() + 12
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise ConnectedClientError("busy")
                    time.sleep(0.05)
            yield
        finally:
            os.close(descriptor)

    def _record(self):
        text = _read(self.record_path, private=True)
        if text is None:
            return None
        try:
            record = json.loads(text)
            if record["schema_version"] != 1 or not re.fullmatch("[a-f0-9]{32}", record["generation"]):
                raise ValueError()
            return record
        except (ValueError, KeyError, TypeError) as error:
            raise ConnectedClientError("state_invalid") from error

    def _save(self, record):
        _write(self.record_path, json.dumps(record, sort_keys=True))

    def _status(self, record):
        if record is None:
            return {"schema_version": 1, "phase": "disconnected", "router": "stopped", "retained_settings": []}
        router = _control(record, "status") if record["phase"] != "disconnected" else "stopped"
        return {"schema_version": 1, "generation": record["generation"], "phase": record["phase"],
                "router": router, "client_version": record["client_version"],
                "owned_settings": list(record["installed"]),
                "retained_settings": record.get("retained_settings", [])}

    def status(self):
        with self._transaction():
            return self._status(self._record())

    def connect(self, config: GatewayConfig, *, executable: str, actor_id=None):
        with self._transaction():
            existing = self._record()
            if existing and existing["phase"] == "connected":
                if _control(existing, "status") != "ready":
                    raise ConnectedClientError("router_unavailable")
                return self._status(existing)
            if existing and existing["phase"] != "disconnected":
                raise ConnectedClientError("recovery_required")
            program = shutil.which(executable)
            if program is None:
                raise ConnectedClientError("client_unavailable")
            probe = subprocess.run([program, "--version"], capture_output=True, text=True, timeout=3,
                env={key: value for key, value in os.environ.items()
                     if key in {"PATH", "HOME", "USER", "TMPDIR", "LANG", "LC_ALL", "SYSTEMROOT"}})
            if probe.returncode or probe.stdout.strip() != "codex-cli " + SUPPORTED_CODEX_VERSION:
                raise ConnectedClientError("version_unsupported")
            if config.listen.host != "127.0.0.1" or config.ingress.mode != "local":
                raise ConnectedClientError("router_configuration_unsupported")
            identity = config.identities_by_actor.get(actor_id) if actor_id else next(iter(config.identities_by_actor.values()))
            if identity is None or not identity.token_env:
                raise ConnectedClientError("authentication_unsupported")
            policy = PolicyRuntime(config).snapshot_for(identity).effective_policy
            model = next((alias for alias, route in config.model_routes.items() if route.protocol == "openai"
                          and (policy.allowed_models is None or alias in policy.allowed_models)), None)
            if model is None:
                raise ConnectedClientError("model_unavailable")
            original_text = _read(self.config_path)
            text = original_text or ""
            document = _document(text)
            generation = secrets.token_hex(16)
            provider = "hormuz_" + generation
            installed = {"model": model, "model_provider": provider, "web_search": "disabled"}
            original = {key: document.get(key) for key in installed}
            for key, value in installed.items():
                text = _setting(text, key, value)
            block = ('\n[model_providers.' + provider + ']\nname = "Hormuz"\n'
                     + 'base_url = ' + json.dumps(f'http://127.0.0.1:{config.listen.port}/v1') + '\n'
                     + 'wire_api = "responses"\nrequires_openai_auth = false\n'
                     + 'env_key = ' + json.dumps(identity.token_env) + '\n')
            text += block
            _document(text)
            control_directory = Path(tempfile.gettempdir()).resolve() / ("hormuz-connected-" + str(os.getuid()))
            # macOS's user temporary directory may exceed sockaddr_un's limit.
            if len(str(control_directory)) > 50:
                control_directory = Path("/tmp").resolve() / ("hormuz-connected-" + str(os.getuid()))
            _directory(control_directory)
            record = {"schema_version": 1, "generation": generation, "phase": "prepared",
                      "client_version": SUPPORTED_CODEX_VERSION, "installed": installed, "original": original,
                      "provider_block": block, "gateway_config": str(config.source_path.resolve()),
                      "control": str(control_directory / (generation + ".sock"))}
            self._save(record)
            _write(self.config_path, text, expected=original_text, compare=True)
            process = subprocess.Popen([sys.executable, "-m", "hormuz.connected_client_runtime", str(self.record_path)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True)
            deadline = time.monotonic() + 5
            while _control(record, "status") != "ready":
                if process.poll() is not None or time.monotonic() >= deadline:
                    if process.poll() is None:
                        process.terminate()
                        process.wait(timeout=5)
                    self._restore(record)
                    record["phase"] = "disconnected"
                    self._save(record)
                    raise ConnectedClientError("router_start_failed")
                time.sleep(0.05)
            record["phase"] = "connected"
            self._save(record)
            return self._status(record)

    def _restore(self, record):
        current = _read(self.config_path)
        text = current or ""
        document = _document(text)
        retained = []
        for key, installed in record["installed"].items():
            if document.get(key) == installed:
                text = _setting(text, key, record["original"][key])
            elif document.get(key) != record["original"][key]:
                retained.append(key)
        if record["provider_block"] in text:
            text = text.replace(record["provider_block"], "", 1)
        elif record["installed"]["model_provider"] in document.get("model_providers", {}):
            retained.append("model_providers." + record["installed"]["model_provider"])
        _document(text)
        _write(self.config_path, text, expected=current, compare=True)
        record["retained_settings"] = retained

    def disconnect(self):
        with self._transaction():
            record = self._record()
            if record is None or record["phase"] == "disconnected":
                return self._status(record)
            record["phase"] = "disconnecting"
            self._save(record)
            self._restore(record)
            _control(record, "stop")
            deadline = time.monotonic() + 5
            while _control(record, "status") != "unavailable":
                if time.monotonic() >= deadline:
                    self._save(record)
                    return self._status(record)
                time.sleep(0.05)
            record["phase"] = "disconnected"
            self._save(record)
            return self._status(record)
