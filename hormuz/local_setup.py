"""One setup owner for CLI and skill callers using existing verified files."""
from contextlib import contextmanager, nullcontext
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import secrets
import socket
import stat
import tempfile
import time

from .client_versions import SUPPORTED_CODEX_VERSION
from .compaction_formats import strict_json_loads
from .connected_client import ConnectedClientError, _directory, _lock_descriptor, _read
from .contracts import ContractValidationError, LOCAL_SETUP_SCHEMA_ID, contract_envelope, validate_contract
from .managed_llama import ManagedLlamaError, ManagedLlamaProfile
from .local_credentials import LocalCredentialError, read_local_credential


DEFAULT_PROFILE = "local-setup-codex-0147-llama-b11617-qwen3-4b-v1.json"


def _profile(path):
    try:
        value = strict_json_loads(_read(Path(path).expanduser() if path else Path(__file__).with_name(DEFAULT_PROFILE)))
        if type(value) is not dict:
            raise ValueError()
        validate_contract(value)
        if value["schema_id"] != "hormuz.local-setup-profile":
            raise ValueError()
    except (ContractValidationError, ValueError, TypeError, KeyError, RecursionError):
        raise ConnectedClientError("router_configuration_unsupported") from None
    if value["client_version"] != SUPPORTED_CODEX_VERSION:
        raise ConnectedClientError("version_unsupported")
    if (value["system"], value["machine"]) != (platform.system(), platform.machine()):
        raise ConnectedClientError("platform_unsupported")
    return value


def _absolute(value):
    if not value or len(value) > 4096 or "\x00" in value:
        raise ConnectedClientError("file_unsafe")
    path = Path(value).expanduser().absolute()
    # Canonicalize ancestors and '..', retaining the final entry for O_NOFOLLOW.
    try:
        return str(path.parent.resolve() / path.name)
    except (OSError, RuntimeError, ValueError):
        raise ConnectedClientError("file_unsafe") from None


def _stage_prefix(path):
    return ".hormuz-setup-v1-" + hashlib.sha256(str(path).encode()).hexdigest()[:16] + "-"


def _recover_publication(path):
    """Remove only a matching private staging link; retain final file contents."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if (info.st_nlink != 2 or not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid() or info.st_mode & 0o077):
        return
    prefix = _stage_prefix(path)
    with os.scandir(path.parent) as entries:
        for index, entry in enumerate(entries):
            if index >= 256:
                break
            if not re.fullmatch(re.escape(prefix) + r"[A-Za-z0-9_-]{8,64}", entry.name):
                continue
            staged = entry.stat(follow_symlinks=False)
            if (staged.st_dev, staged.st_ino) != (info.st_dev, info.st_ino):
                continue
            if path.lstat() != info or staged != info:
                raise ConnectedClientError("file_unsafe")
            os.unlink(entry.path)
            parent = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
            return


@contextmanager
def _configuration_lock(path):
    import fcntl
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise ConnectedClientError("directory_unsafe")
    descriptor = _lock_descriptor(str(path) + ".setup-lock")
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


def _ports(existing):
    if existing is not None:
        from urllib.parse import urlsplit
        try:
            value = strict_json_loads(existing)
            endpoint = value["upstreams"]["local-llama"]["base_url"]
            if type(endpoint) is not str:
                raise ValueError()
            ports = value["listen"]["port"], urlsplit(endpoint).port
            if any(type(port) is not int or not 1 <= port <= 65535 for port in ports) or ports[0] == ports[1]:
                raise ValueError()
            return ports
        except (ValueError, TypeError, KeyError, RecursionError):
            raise ConnectedClientError("setup_changed") from None
    with socket.socket() as router, socket.socket() as engine:
        router.bind(("127.0.0.1", 0))
        engine.bind(("127.0.0.1", 0))
        return router.getsockname()[1], engine.getsockname()[1]


def _configuration(path, profile, managed, credential_env, listen_port, *, credential_file=None, shadow_router=False):
    revision = "sha256:" + profile["model_sha256"]
    state = path.with_name(path.name + ".state")
    value = {
        "listen": {"host": "127.0.0.1", "port": listen_port},
        "database": str(state / "usage.sqlite3"),
        "max_request_bytes": profile["max_request_bytes"],
        "upstream_timeout_seconds": profile["upstream_timeout_seconds"],
        "upstreams": {"local-llama": {"protocol": "openai", "base_url": f"http://127.0.0.1:{managed.port}/v1",
                    "locality": "local", "authentication": "none"}},
        "identities": [{**({"token_env": credential_env} if credential_env else
                          {"token_file": str(credential_file or state / "router-credential-v1.json")}),
                       "actor_id": "local-user", "actor_name": "Local user",
                       "team_id": "local", "team_name": "Local", "allowed_clients": ["codex"]}],
        "model_routes": {profile["model"]: {"protocol": "openai", "destination": "local-llama",
                        "upstream_model": profile["model"], "model_revision": revision, "capabilities": profile["capabilities"]}},
        "policies": {"organization": {"allowed_clients": ["codex"], "allowed_models": [profile["model"]],
                                    "max_output_tokens": profile["max_output_tokens"]}},
        "route_admission": {"mode": "local_only", "local_routes": {profile["model"]: {
            "qualification_id": profile["qualification_id"], "model_revision": revision,
            "max_request_bytes": profile["max_request_bytes"], "max_output_tokens": profile["max_output_tokens"],
            "task_types": profile["task_types"]}}},
        "managed_llama": {"schema_version": 1, **{key: value for key, value in asdict(managed).items()
                                               if key not in {"port", "alias", "output_tokens"}}},
        "ai_work": {"enabled": True, "database": str(state / "work.sqlite3"),
                    "tool_capable_aliases": [profile["model"]] if "tools" in profile["capabilities"] else []},
    }
    if profile["schema_version"] == 2:
        value["route_admission"]["local_routes"][profile["model"]]["coding_evidence"] = profile["coding_evidence"]
    if shadow_router:
        value["shadow_router"] = {"schema_version": 1, "mode": "shadow", "model": profile["model"],
                                  "local_model": profile["model"]}
    return value


@contextmanager
def _credential_candidate(state, *, configuration_exists):
    _directory(state)
    credential = state / "router-credential-v1.json"
    _recover_publication(credential)
    if credential.exists():
        try:
            read_local_credential(credential)
        except LocalCredentialError:
            raise ConnectedClientError("authentication_unsupported") from None
        yield credential, None
        return
    if configuration_exists:
        raise ConnectedClientError("authentication_unsupported")
    descriptor, staged = tempfile.mkstemp(prefix=_stage_prefix(credential), dir=state)
    try:
        with os.fdopen(descriptor, "w") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump({"schema_id": "hormuz.local-router-credential", "schema_version": 1,
                       "token": secrets.token_hex(32)}, stream)
            stream.flush()
            os.fsync(stream.fileno())

        def publish():
            os.link(staged, credential)
            os.unlink(staged)
            parent = os.open(state, os.O_RDONLY)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)

        yield Path(staged), publish
    finally:
        if os.path.exists(staged):
            os.unlink(staged)


def setup_local(lifetime, args):
    profile = _profile(args.qualification)
    if args.credential_env is not None and (re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", args.credential_env) is None
            or not os.environ.get(args.credential_env)):
        raise ConnectedClientError("authentication_unsupported")
    path = Path(_absolute(args.config))
    try:
        canonical = path.resolve()
    except (OSError, RuntimeError, ValueError):
        raise ConnectedClientError("file_unsafe") from None
    if canonical == lifetime.config_path or canonical.is_relative_to(lifetime.directory):
        raise ConnectedClientError("file_unsafe")
    managed = ManagedLlamaProfile(
        route=profile["model"], executable=_absolute(args.llama_executable),
        runtime_archive=_absolute(args.runtime_archive), runtime_sha256=profile["runtime_sha256"],
        model_file=_absolute(args.model_file), model_sha256=profile["model_sha256"],
        startup_timeout_seconds=profile["startup_timeout_seconds"], port=1,
        alias=profile["model"], output_tokens=profile["max_output_tokens"],
    )
    try:
        managed.verify()
    except ManagedLlamaError as error:
        raise ConnectedClientError(error.reason) from None
    with _configuration_lock(path):
        _recover_publication(path)
        existing = _read(path, private=True)
        listen_port, runtime_port = _ports(existing)
        managed = replace(managed, port=runtime_port)
        text = json.dumps(_configuration(path, profile, managed, args.credential_env, listen_port,
                         shadow_router=args.shadow_router), sort_keys=True, indent=2) + "\n"
        if existing is not None and existing != text:
            raise ConnectedClientError("setup_changed")
        state = path.with_name(path.name + ".state")
        try:
            info = state.lstat()
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ConnectedClientError("directory_unsafe")
        credentials = (_credential_candidate(state, configuration_exists=existing is not None)
                       if args.credential_env is None else nullcontext((None, None)))
        with credentials as (candidate, publish_credential):
            if existing is not None:
                if not args.dry_run:
                    _directory(state)
                connection = lifetime.connect(path, executable=args.executable, actor_id=args.actor, preview=args.dry_run)
            else:
                descriptor, temporary = tempfile.mkstemp(prefix=_stage_prefix(path), dir=path.parent)
                try:
                    preview_text = (json.dumps(_configuration(path, profile, managed, args.credential_env, listen_port,
                                    credential_file=candidate, shadow_router=args.shadow_router), sort_keys=True, indent=2) + "\n")
                    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                        os.fchmod(stream.fileno(), 0o600)
                        stream.write(preview_text)
                        stream.flush()
                        os.fsync(stream.fileno())
                    connection = lifetime.connect(Path(temporary), executable=args.executable, actor_id=args.actor, preview=True)
                    if not args.dry_run:
                        if publish_credential is not None:
                            publish_credential()
                            with open(temporary, "w", encoding="utf-8") as stream:
                                stream.write(text)
                                stream.flush()
                                os.fsync(stream.fileno())
                        # Atomic create-only publication: an existing file is never replaced.
                        try:
                            os.link(temporary, path)
                        except FileExistsError:
                            raise ConnectedClientError("setup_changed") from None
                        os.unlink(temporary)
                        parent = os.open(path.parent, os.O_RDONLY)
                        try:
                            os.fsync(parent)
                        finally:
                            os.close(parent)
                        _directory(state)
                        connection = lifetime.connect(path, executable=args.executable, actor_id=args.actor)
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
        result = {
            "schema_id": LOCAL_SETUP_SCHEMA_ID, "schema_version": 3 if args.shadow_router else 1 if args.credential_env else 2,
            "configuration": {"operation": "reuse" if existing is not None else "create", "mode": "local_only",
                "qualification_id": profile["qualification_id"],
                **({"credential_env": args.credential_env} if args.credential_env else {"authentication": "managed_local"}),
                "model": profile["model"], "listen_port": listen_port, "runtime_port": runtime_port},
            "connection": connection,
        }
        if args.shadow_router:
            result["configuration"]["shadow_router"] = "shadow"
        validate_contract(result)
        return result
