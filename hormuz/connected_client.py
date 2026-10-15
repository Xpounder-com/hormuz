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
import selectors
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
from urllib.parse import urlsplit

from .client_versions import SUPPORTED_CODEX_VERSION
from .config import GatewayConfig
from .compaction_formats import strict_json_loads
from .contracts import (
    CONNECTED_CLIENT_STATUS_SCHEMA_ID, CONNECTED_CLIENT_PREVIEW_SCHEMA_ID,
    CONNECTED_CLIENT_ERROR_SCHEMA_ID, CONNECTED_CLIENT_ERROR_ACTIONS, contract_envelope,
    validate_contract,
)
from .policy_runtime import PolicyRuntime
from .managed_llama import ManagedLlamaError

LIMIT = 128 * 1024
JOURNAL_SCHEMA_ID = "hormuz.connected-client-journal"
JOURNAL_SCHEMA_VERSION = 1


class ConnectedClientError(RuntimeError):
    def __init__(self, code: str):
        self.code = "connected_client_" + code
        self.action = CONNECTED_CLIENT_ERROR_ACTIONS[self.code]
        super().__init__(self.code)


def lifetime_error(error):
    code = error.code if isinstance(error, ConnectedClientError) else "connected_client_setup_unavailable"
    return contract_envelope(CONNECTED_CLIENT_ERROR_SCHEMA_ID,
        {"error": {"code": code, "action": CONNECTED_CLIENT_ERROR_ACTIONS[code]}})


def _lifetime_envelope(schema_id, value, managed=False):
    if not managed:
        return contract_envelope(schema_id, value)
    result = {**value, "schema_id": schema_id, "schema_version": 2}
    validate_contract(result)
    return result


def _control_directory():
    return Path("/tmp").resolve() / ("hormuz-connected-" + str(os.getuid()))


def _provider_block(provider, origin, credential_env=None, *, helper=None, generation=None):
    block = ('\n[model_providers.' + provider + ']\nname = "Hormuz"\n'
            + 'base_url = ' + json.dumps(origin + '/v1') + '\n'
            + 'wire_api = "responses"\n')
    if helper is None:
        return block + 'requires_openai_auth = false\n' + 'env_key = ' + json.dumps(credential_env) + '\n'
    return (block + '\n[model_providers.' + provider + '.auth]\n'
            + 'command = ' + json.dumps(helper["command"]) + '\n'
            + 'args = ' + json.dumps(["-I", helper["entrypoint"], "--client-home", helper["home"],
                                     "--generation", generation]) + '\n'
            + 'timeout_ms = 5000\nrefresh_interval_ms = 0\n')


def _parse_record(text):
    try:
        record = strict_json_loads(text)
        # The unreleased first v1 journal omitted its identifier. Preserve its
        # owned generation and recovery intent; the next normal save completes
        # the envelope rather than discarding or replacing the installation.
        if (isinstance(record, dict) and "schema_id" not in record
                and record.get("schema_version") == 1):
            record = {"schema_id": JOURNAL_SCHEMA_ID, **record}
        required = {"schema_id", "schema_version", "generation", "phase", "client_version", "installed", "original",
                    "provider_block", "gateway_config", "gateway_digest", "actor_id", "control"}
        if (not isinstance(record, dict) or not required <= record.keys()
                or record.keys() - required - {"retained_settings", "managed_llama", "credential_helper"}
                or record["schema_id"] != JOURNAL_SCHEMA_ID
                or type(record["schema_version"]) is not int
                or record["schema_version"] != (3 if "credential_helper" in record else 2 if "managed_llama" in record else 1)
                or ("managed_llama" in record and record["managed_llama"] is not True)
                or not isinstance(record["generation"], str) or not re.fullmatch("[a-f0-9]{32}", record["generation"])
                or record["phase"] not in {"prepared", "connected", "disconnecting", "restore_required", "disconnected"}
                or not isinstance(record["client_version"], str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", record["client_version"])
                or not isinstance(record["gateway_config"], str) or not Path(record["gateway_config"]).is_absolute()
                or not isinstance(record["gateway_digest"], str) or not re.fullmatch("[a-f0-9]{64}", record["gateway_digest"])
                or not isinstance(record["actor_id"], str) or not 1 <= len(record["actor_id"]) <= 256
                or record["control"] != str(_control_directory() / (record["generation"] + ".sock"))):
            raise ValueError()
        fields = {"model", "model_provider", "web_search"}
        if (not isinstance(record["installed"], dict) or record["installed"].keys() != fields
                or not isinstance(record["original"], dict) or record["original"].keys() != fields
                or any(not isinstance(value, str) or not 1 <= len(value) <= 2048 for value in record["installed"].values())
                or any(value is not None and (not isinstance(value, str) or len(value) > 2048) for value in record["original"].values())
                or record["installed"]["model_provider"] != "hormuz_" + record["generation"]
                or record["installed"]["web_search"] != "disabled"):
            raise ValueError()
        table = _document(record["provider_block"])["model_providers"][record["installed"]["model_provider"]]
        helper = record.get("credential_helper")
        if "credential_helper" in record:
            if (type(helper) is not dict or set(helper) != {"command", "home", "entrypoint", "credential_sha256"}
                    or any(type(helper[key]) is not str or not 1 <= len(helper[key]) <= 4096 or "\x00" in helper[key]
                           or not Path(helper[key]).is_absolute() for key in ("command", "home", "entrypoint"))
                    or type(helper["credential_sha256"]) is not str
                    or re.fullmatch(r"[a-f0-9]{64}", helper["credential_sha256"]) is None):
                raise ValueError()
        endpoint = urlsplit(table["base_url"])
        if (endpoint.scheme != "http" or endpoint.hostname != "127.0.0.1" or endpoint.port is None
                or not 1 <= endpoint.port <= 65535 or endpoint.path != "/v1" or endpoint.query or endpoint.fragment
                or endpoint.username is not None
                or (helper is None and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table["env_key"]))
                or record["provider_block"] != _provider_block(record["installed"]["model_provider"],
                    f"http://127.0.0.1:{endpoint.port}", table.get("env_key"), helper=helper,
                    generation=record["generation"])):
            raise ValueError()
        retained = record.get("retained_settings", [])
        if (not isinstance(retained, list) or len(retained) > 4 or len(set(retained)) != len(retained)
                or any(value not in fields | {"model_providers." + record["installed"]["model_provider"]} for value in retained)):
            raise ValueError()
        return record
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError, ConnectedClientError) as error:
        raise ConnectedClientError("state_invalid") from error


def _lock_descriptor(path):
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_mode & 0o077:
        os.close(descriptor)
        raise ConnectedClientError("file_unsafe")
    return descriptor


def _qualify_client(program):
    environment = {}
    for variable in ("PATH", "HOME", "USER", "TMPDIR", "LANG", "LC_ALL", "SYSTEMROOT"):
        value = os.environ.get(variable)
        if value is not None:
            environment[variable] = value
    process = subprocess.Popen([program, "--version"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               stdin=subprocess.DEVNULL, env=environment)
    deadline = time.monotonic() + 3
    output = bytearray()
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(timeout=remaining):
                    raise ConnectedClientError("version_unsupported")
                data = os.read(process.stdout.fileno(), 4097 - len(output))
                if not data:
                    break
                output.extend(data)
                if len(output) > 4096:
                    raise ConnectedClientError("version_unsupported")
        if (process.wait(timeout=max(0.01, deadline - time.monotonic()))
                or bytes(output).strip() != ("codex-cli " + SUPPORTED_CODEX_VERSION).encode("ascii")):
            raise ConnectedClientError("version_unsupported")
    finally:
        process.stdout.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)


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
            data = stream.read(LIMIT + 1)
            if len(data) > LIMIT:
                raise ConnectedClientError("file_unsafe")
            return data.decode("utf-8")
        except UnicodeError as error:
            raise ConnectedClientError("configuration_invalid") from error


def _directory(path: Path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ConnectedClientError("directory_unsafe")


def _write(path: Path, text: str, *, expected: str | None = None, compare=False):
    if len(text.encode("utf-8")) > LIMIT:
        raise ConnectedClientError("file_unsafe")
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


def _require_unmanaged(home):
    candidates = [home / "requirements.toml", home / "managed_config.toml",
                  Path("/etc/codex/requirements.toml"), Path("/etc/codex/managed_config.toml")]
    if sys.platform == "darwin":
        candidates.extend([Path("/Library/Managed Preferences/com.openai.codex.plist"),
                           Path("/Library/Managed Preferences") / home.owner() / "com.openai.codex.plist"])
    for path in candidates:
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        except OSError as error:
            raise ConnectedClientError("managed_configuration_unknown") from error
        raise ConnectedClientError("managed_configuration")


def _document(text):
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ConnectedClientError("configuration_invalid") from error


def _document_lines(lines):
    quote = None
    for index, line in enumerate(lines):
        if quote is None:
            yield index, line
        offset = 0
        while offset < len(line):
            if quote is None:
                if line[offset] == "#":
                    break
                if line[offset] in ("'", '"'):
                    quote = line[offset] * (3 if line.startswith(line[offset] * 3, offset) else 1)
                    offset += len(quote)
                    continue
            elif quote[0] == '"' and line[offset] == "\\":
                offset += 2
                continue
            elif line.startswith(quote, offset):
                # Four/five closing quotes include a literal quote or two.
                size = len(quote)
                if size == 3:
                    while offset + size < len(line) and line[offset + size] == quote[0]:
                        size += 1
                offset += size
                quote = None
                continue
            offset += 1


def _top_level_lines(lines):
    for index, line in _document_lines(lines):
        if line.lstrip().startswith("["):
            return
        yield index, line


def _remove_provider_table(text, record):
    lines = text.splitlines(keepends=True)
    positions = []
    offset = 0
    for line in lines:
        positions.append(offset)
        offset += len(line)
    tables = [(positions[index], line.strip()) for index, line in _document_lines(lines)
              if line.lstrip().startswith("[")]
    header = "[model_providers." + record["installed"]["model_provider"] + "]"
    for index, (start, label) in enumerate(tables):
        if label != header:
            continue
        end = next((position for position, following in tables[index + 1:]
                    if not following.startswith(header[:-1] + ".")), len(text))
        block = record["provider_block"][1:]
        section = text[start:end]
        if not section.startswith(block) or section[len(block):].strip():
            return text, False
        # Native Codex can append a blank separator and a project trust table.
        # Remove exactly our bytes, preserving the caller's appended document.
        end = start + len(block)
        if start and text[start - 1] == "\n":
            start -= 1
        return text[:start] + text[end:], True
    return text, False


def _setting(text, key, value):
    """Edit a simple top-level string token, keeping adjacent comments/bytes."""
    document = _document(text)
    lines = text.splitlines(keepends=True)
    for index, line in _top_level_lines(lines):
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
        if not isinstance(value, dict) or value.get("generation") != record["generation"] or value.get("router") not in {"ready", "stopping", "unavailable"}:
            raise ValueError()
        return value["router"]
    except (OSError, ValueError, KeyError, TypeError):
        return "unavailable"
    finally:
        connection.close()


class ConnectedClientLifetime:
    def __init__(self, home: Path):
        if os.name != "posix":
            raise ConnectedClientError("platform_unsupported")
        home = home.expanduser().absolute()
        if home.is_symlink():
            raise ConnectedClientError("directory_unsafe")
        self.home = home.resolve()
        self.directory = self.home / ".hormuz-router"
        self.record_path = self.directory / "connection.json"
        self.config_path = self.home / "config.toml"

    @contextmanager
    def _transaction(self):
        import fcntl
        _directory(self.home)
        _directory(self.directory)
        descriptor = _lock_descriptor(self.directory / "lock")
        try:
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

    def cloud_readiness(self, config_path: Path, *, executable="codex", actor_id=None, surface="cli"):
        """Inspect the same setup boundary without entering a write transaction."""
        from .cloud_readiness import inspect_cloud_readiness

        return inspect_cloud_readiness(self.home, config_path, executable=executable,
                                       actor_id=actor_id, surface=surface)

    def _record(self):
        text = _read(self.record_path, private=True)
        if text is None:
            return None
        record = _parse_record(text)
        if record.get("credential_helper", {}).get("home", str(self.home)) != str(self.home):
            raise ConnectedClientError("state_invalid")
        return record

    def _runtime_active(self):
        import fcntl
        descriptor = _lock_descriptor(str(self.record_path) + ".runtime-lock")
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return False
            except BlockingIOError:
                return True
        finally:
            os.close(descriptor)

    def _save(self, record):
        text = json.dumps(_parse_record(json.dumps(record)), sort_keys=True)
        _write(self.record_path, text)

    def _configuration_state(self, record):
        if record["phase"] == "disconnected":
            return "not_owned"
        try:
            document = _document(_read(self.config_path) or "")
            table = _document(record["provider_block"])["model_providers"][record["installed"]["model_provider"]]
            matches = (all(document.get(key) == value for key, value in record["installed"].items())
                       and document.get("model_providers", {}).get(record["installed"]["model_provider"]) == table)
            return "installed" if matches else "changed"
        except (ConnectedClientError, OSError, AttributeError):
            return "unavailable"

    def _status(self, record):
        if record is None:
            return contract_envelope(CONNECTED_CLIENT_STATUS_SCHEMA_ID,
                {"phase": "disconnected", "router": "stopped", "retained_settings": [],
                 "scope": "codex_user_defaults", "configuration": "not_owned"})
        router = _control(record, "status") if record["phase"] not in {"disconnected", "restore_required"} else "stopped"
        managed = record.get("managed_llama", False)
        return _lifetime_envelope(CONNECTED_CLIENT_STATUS_SCHEMA_ID,
                {"generation": record["generation"], "phase": record["phase"],
                "router": router, "client_version": record["client_version"],
                "scope": "codex_user_defaults", "configuration": self._configuration_state(record),
                "owned_settings": list(record["installed"]),
                "retained_settings": record.get("retained_settings", []),
                **({"local_runtime": {"ownership": "owned", "state": router}} if managed else {})}, managed)

    def status(self):
        with self._transaction():
            return self._status(self._record())

    def local_token(self, generation):
        with self._transaction():
            record = self._record()
            if (record is None or record.get("credential_helper") is None
                    or record["generation"] != generation or record["phase"] != "connected"
                    or self._configuration_state(record) != "installed" or _control(record, "status") != "ready"):
                raise ConnectedClientError("authentication_unsupported")
            path = Path(record["gateway_config"])
            text = _read(path)
            if text is None or hashlib.sha256(text.encode()).hexdigest() != record["gateway_digest"]:
                raise ConnectedClientError("setup_changed")
            config = GatewayConfig.load(path)
            identity = config.identities_by_actor.get(record["actor_id"])
            if (identity is None or identity.token_file is None or _read(path) != text
                    or not secrets.compare_digest(hashlib.sha256(identity.token.encode()).hexdigest(),
                                                  record["credential_helper"]["credential_sha256"])):
                raise ConnectedClientError("authentication_unsupported")
            return identity.token

    def connect(self, config_path: Path, *, executable: str, actor_id=None, preview=False):
        with self._transaction():
            _require_unmanaged(self.home)
            config_path = config_path.expanduser().absolute().resolve()
            gateway_text = _read(config_path)
            if gateway_text is None:
                raise ConnectedClientError("router_configuration_unavailable")
            config = GatewayConfig.load(config_path)
            if _read(config_path) != gateway_text:
                raise ConnectedClientError("setup_changed")
            digest = hashlib.sha256(gateway_text.encode()).hexdigest()
            intent = {"gateway_config": str(config_path), "gateway_digest": digest,
                      "actor_id": actor_id or next(iter(config.identities_by_actor.values())).actor_id}
            existing = self._record()
            if existing and existing["phase"] != "disconnected" and existing.get("credential_helper"):
                identity = config.identities_by_actor.get(existing["actor_id"])
                if (identity is None or identity.token_file is None
                        or not secrets.compare_digest(hashlib.sha256(identity.token.encode()).hexdigest(),
                                                      existing["credential_helper"]["credential_sha256"])):
                    raise ConnectedClientError("authentication_unsupported")
            program = shutil.which(executable)
            if program is None:
                raise ConnectedClientError("client_unavailable")
            _qualify_client(program)
            if _read(config_path) != gateway_text:
                raise ConnectedClientError("setup_changed")
            if existing and existing["phase"] != "disconnected" and any(existing[key] != value for key, value in intent.items()):
                raise ConnectedClientError("setup_changed")
            if preview and existing and existing["phase"] in {"prepared", "disconnecting", "restore_required"}:
                raise ConnectedClientError("recovery_required")
            if existing and existing["phase"] in {"prepared", "disconnecting"}:
                if (existing["phase"] == "prepared" and self._configuration_state(existing) == "installed"
                        and _control(existing, "status") == "ready"):
                    existing["phase"] = "connected"
                    self._save(existing)
                elif self._runtime_active():
                    raise ConnectedClientError("recovery_required")
                else:
                    self._restore(existing)
                    existing["phase"] = "disconnected"
                    self._save(existing)
            if existing and existing["phase"] == "connected":
                if self._configuration_state(existing) != "installed":
                    raise ConnectedClientError("configuration_changed")
                if _control(existing, "status") != "ready":
                    raise ConnectedClientError("router_unavailable")
                if preview:
                    return _lifetime_envelope(CONNECTED_CLIENT_PREVIEW_SCHEMA_ID,
                           {"phase": "preview", "scope": "codex_user_defaults",
                            "client_version": SUPPORTED_CODEX_VERSION, "settings": existing["installed"],
                            "changes": {}, "router": "ready",
                            **({"local_runtime": {"ownership": "owned", "state": "ready"}}
                               if existing.get("managed_llama") else {})}, existing.get("managed_llama", False))
                return self._status(existing)
            if (existing and existing["phase"] != "disconnected") or self._runtime_active():
                raise ConnectedClientError("recovery_required")
            if config.listen.host != "127.0.0.1" or config.ingress.mode != "local":
                raise ConnectedClientError("router_configuration_unsupported")
            identity = config.identities_by_actor.get(actor_id) if actor_id else next(iter(config.identities_by_actor.values()))
            if identity is None or (not identity.token_env and identity.token_file is None):
                raise ConnectedClientError("authentication_unsupported")
            policy = PolicyRuntime(config).snapshot_for(identity).effective_policy
            if ((identity.allowed_clients and "codex" not in identity.allowed_clients)
                    or (policy.allowed_clients is not None and "codex" not in policy.allowed_clients)):
                raise ConnectedClientError("client_not_allowed")
            model = next((alias for alias, route in config.model_routes.items() if route.protocol == "openai"
                          and (policy.allowed_models is None or alias in policy.allowed_models)), None)
            if model is None:
                raise ConnectedClientError("model_unavailable")
            if len(model) > 2048:
                raise ConnectedClientError("configuration_shape_unsupported")
            managed = config.managed_llama
            if managed is not None:
                if policy.allowed_models is not None and managed.route not in policy.allowed_models:
                    raise ConnectedClientError("model_unavailable")
                try:
                    managed.verify()
                    managed.require_free_endpoint()
                except (ManagedLlamaError, OSError, ValueError) as error:
                    raise ConnectedClientError(error.reason if isinstance(error, ManagedLlamaError) else "runtime_invalid") from None
            original_text = _read(self.config_path)
            text = original_text or ""
            document = _document(text)
            generation = secrets.token_hex(16)
            provider = "hormuz_" + generation
            installed = {"model": model, "model_provider": provider, "web_search": "disabled"}
            original = {key: document.get(key) for key in installed}
            for key, value in installed.items():
                text = _setting(text, key, value)
            if any(value is not None and not isinstance(value, str) for value in original.values()):
                raise ConnectedClientError("configuration_shape_unsupported")
            helper = {"command": sys.executable, "home": str(self.home),
                      "entrypoint": str(Path(__file__).with_name("_local_auth_helper.py").resolve()),
                      "credential_sha256": hashlib.sha256(identity.token.encode()).hexdigest()} if identity.token_file is not None else None
            block = _provider_block(provider, f"http://127.0.0.1:{config.listen.port}", identity.token_env,
                                    helper=helper, generation=generation)
            text += block
            _document(text)
            control_directory = _control_directory()
            _directory(control_directory)
            record = {"schema_id": JOURNAL_SCHEMA_ID, "schema_version": JOURNAL_SCHEMA_VERSION,
                      "generation": generation, "phase": "prepared",
                      "client_version": SUPPORTED_CODEX_VERSION, "installed": installed, "original": original,
                      "provider_block": block, **intent,
                      "control": str(control_directory / (generation + ".sock"))}
            if managed is not None:
                record.update(schema_version=2, managed_llama=True)
            if helper is not None:
                record.update(schema_version=3, credential_helper=helper)
            if preview:
                return _lifetime_envelope(CONNECTED_CLIENT_PREVIEW_SCHEMA_ID,
                       {"phase": "preview", "scope": "codex_user_defaults",
                        "client_version": SUPPORTED_CODEX_VERSION, "settings": installed,
                        "changes": {**{key: "replace" if key in document else "add" for key in installed},
                                    "provider_table": "add"}, "router": "not_started",
                        **({"local_runtime": {"ownership": "planned", "state": "not_started"}}
                           if managed is not None else {})}, managed is not None)
            self._save(record)
            _write(self.config_path, text, expected=original_text, compare=True)
            process = subprocess.Popen([sys.executable, "-m", "hormuz.connected_client_runtime", str(self.record_path), generation],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True)
            deadline = time.monotonic() + (managed.startup_timeout_seconds + 35 if managed is not None else 5)
            while _control(record, "status") != "ready":
                if process.poll() is not None or time.monotonic() >= deadline:
                    if process.poll() is None:
                        process.terminate()
                        process.wait(timeout=5)
                    cleanup_deadline = time.monotonic() + 7
                    while managed is not None and self._runtime_active() and time.monotonic() < cleanup_deadline:
                        time.sleep(0.05)
                    self._restore(record)
                    record["phase"] = "disconnecting" if self._runtime_active() else "disconnected"
                    self._save(record)
                    raise ConnectedClientError("runtime_start_failed" if managed is not None else "router_start_failed")
                time.sleep(0.05)
            record["phase"] = "connected"
            self._save(record)
            return self._status(record)

    def _restore(self, record):
        current = _read(self.config_path)
        text = current or ""
        document = _document(text)
        if not isinstance(document.get("model_providers", {}), dict):
            raise ConnectedClientError("configuration_shape_unsupported")
        retained = []
        for key, installed in record["installed"].items():
            if document.get(key) == installed:
                text = _setting(text, key, record["original"][key])
            elif document.get(key) != record["original"][key]:
                retained.append(key)
        installed_table = _document(record["provider_block"])["model_providers"][record["installed"]["model_provider"]]
        current_table = document.get("model_providers", {}).get(record["installed"]["model_provider"])
        removed = False
        if current_table == installed_table:
            text, removed = _remove_provider_table(text, record)
        if not removed and record["installed"]["model_provider"] in document.get("model_providers", {}):
            retained.append("model_providers." + record["installed"]["model_provider"])
        _document(text)
        _write(self.config_path, text, expected=current, compare=True)
        record["retained_settings"] = retained

    def disconnect(self, *, generation=None):
        with self._transaction():
            record = self._record()
            if generation is not None and (record is None or generation != record["generation"]):
                raise ConnectedClientError("generation_mismatch")
            if record is None or record["phase"] == "disconnected":
                return self._status(record)
            record["phase"] = "disconnecting"
            self._save(record)
            restoration_error = None
            try:
                self._restore(record)
            except Exception as error:
                # Restoration must never bypass process cleanup, including an
                # unexpected semantic document shape. Keep output content-free.
                restoration_error = error if isinstance(error, (ConnectedClientError, OSError)) else ConnectedClientError("restoration_unavailable")
            _control(record, "stop")
            deadline = time.monotonic() + (12 if record.get("managed_llama") else 7)
            while self._runtime_active():
                if time.monotonic() >= deadline:
                    self._save(record)
                    if restoration_error is not None:
                        raise restoration_error
                    return self._status(record)
                time.sleep(0.05)
            try:
                info = Path(record["control"]).lstat()
            except FileNotFoundError:
                pass
            else:
                if stat.S_ISSOCK(info.st_mode) and info.st_uid == os.getuid():
                    Path(record["control"]).unlink()
            record["phase"] = "restore_required" if restoration_error is not None else "disconnected"
            self._save(record)
            if restoration_error is not None:
                raise restoration_error
            return self._status(record)
