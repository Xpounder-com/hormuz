"""Explicit, offline lifecycle for the single-node hosted staging databases."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import shutil
import sqlite3
import stat
from contextlib import ExitStack, closing, contextmanager
from pathlib import Path

from ._hosted_config import HostedError, at_directory
from .config import GatewayConfig
from .onboarding import TeamDirectory
from .session_store import SQLiteSessionStore, SESSION_STORE_SCHEMA_VERSION, _isoformat
from .store import UsageStore


MARKER = "initialized.json"
SNAPSHOT = "snapshot.json"
DATABASES = ("sessions.sqlite3", "usage.sqlite3")


def _private(path: Path, *, directory: bool = False) -> os.stat_result:
    info = path.lstat()
    appropriate = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not appropriate or info.st_uid != os.getuid() or info.st_mode & 0o077 or (not directory and info.st_nlink != 1):
        raise HostedError("hosted_state_permissions_invalid")
    return info


def _parent(path: Path) -> None:
    parent = path.parent
    info = parent.lstat()
    if parent != parent.resolve() or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise HostedError("hosted_state_parent_unsafe")


@contextmanager
def state_lock(config: GatewayConfig, *, exclusive: bool = True):
    # Imported here so other Hormuz commands remain importable on Windows.
    import fcntl

    directory = config.database_path.parent
    _parent(directory)
    path = directory.parent / ("." + directory.name + ".hosted.lock")
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        _private(path)
        try:
            fcntl.flock(descriptor, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError:
            raise HostedError("hosted_state_in_use") from None
        yield
    finally:
        os.close(descriptor)


def _mac(config: GatewayConfig, purpose: str, document: dict) -> str:
    issuer = next(iter(config.oidc_issuers.values()))
    binding = [config.session_broker.public_base_url, issuer.issuer, issuer.login.client_id, document]
    encoded = json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(config.session_broker.master_key, b"hormuz/hosted/" + purpose.encode() + b"\x00" + encoded, hashlib.sha256).hexdigest()


def _write(path: Path, value: dict) -> None:
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as target:
        json.dump(value, target, sort_keys=True)
        target.write("\n")
        target.flush()
        os.fsync(target.fileno())
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _read(path: Path) -> dict:
    _private(path)
    with path.open("rb") as source:
        value = json.loads(source.read(4097))
    if not isinstance(value, dict) or path.stat().st_size > 4096:
        raise HostedError("hosted_state_manifest_invalid")
    return value


def sessions(config: GatewayConfig) -> SQLiteSessionStore:
    settings = config.session_broker
    return SQLiteSessionStore(settings.database_path, master_key=settings.master_key,
                              audience=settings.public_base_url, access_ttl_seconds=settings.access_ttl_seconds,
                              absolute_ttl_seconds=settings.absolute_ttl_seconds, enrollment_ttl_seconds=settings.enrollment_ttl_seconds)


def _marker(config: GatewayConfig, *, recovered: bool) -> dict:
    document = {"version": 1, "instance": secrets.token_hex(16), "recovered_closed": recovered}
    return {**document, "binding": _mac(config, "state/v1", document)}


def check_initialized(config: GatewayConfig) -> tuple[tuple[int, int], ...]:
    directory = config.database_path.parent
    _private(directory, directory=True)
    marker = _read(directory / MARKER)
    if set(marker) != {"version", "instance", "recovered_closed", "binding"} or marker["version"] != 1 or type(marker["recovered_closed"]) is not bool:
        raise HostedError("hosted_state_manifest_invalid")
    signature = marker.pop("binding")
    if not isinstance(signature, str) or not hmac.compare_digest(signature, _mac(config, "state/v1", marker)):
        raise HostedError("hosted_state_binding_mismatch")
    identities = []
    for name in (MARKER, *DATABASES):
        path = directory / name
        info = _private(path)
        identities.append((info.st_dev, info.st_ino))
        if name in DATABASES:
            with path.open("rb") as source:
                if source.read(16) != b"SQLite format 3\x00":
                    raise HostedError("hosted_state_database_invalid")
    # Read-only checks precede any constructor that could initialize a database.
    with closing(sqlite3.connect(config.session_broker.database_path.as_uri() + "?mode=ro", uri=True, timeout=5)) as connection:
        if connection.execute("PRAGMA user_version").fetchone()[0] != SESSION_STORE_SCHEMA_VERSION:
            raise HostedError("hosted_state_schema_mismatch")
        connection.execute("SELECT id FROM human_sessions LIMIT 0")
        connection.execute("SELECT id FROM onboarding_memberships LIMIT 0")
        connection.execute("SELECT id FROM console_grants LIMIT 0")
    UsageStore(config.database_path, read_only=True).verify_ready()
    return tuple(identities)


def initialize(config: GatewayConfig) -> None:
    with state_lock(config):
        directory = config.database_path.parent
        directory.mkdir(mode=0o700)  # Existing/partial state is never overwritten.
        for name in DATABASES:
            descriptor = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
        sessions(config)
        UsageStore(config.database_path).verify_ready()
        _write(directory / MARKER, _marker(config, recovered=False))
        check_initialized(config)


def _sha256(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def _destination(path: Path, active: Path) -> None:
    if path != path.resolve() or path == active or path.is_relative_to(active) or active.is_relative_to(path):
        raise HostedError("hosted_snapshot_path_invalid")
    _parent(path)
    path.mkdir(mode=0o700)


def snapshot(config: GatewayConfig, destination: Path) -> None:
    with state_lock(config), ExitStack() as stack:
        check_initialized(config)
        directory = config.database_path.parent
        _destination(destination, directory)
        # Hold write reservations on BOTH databases before taking either copy.
        # Backup uses separate readers to avoid backing up a write transaction.
        for name in DATABASES:
            writer = sqlite3.connect((directory / name).as_uri() + "?mode=rw", uri=True, timeout=5)
            stack.callback(writer.close)
            writer.execute("BEGIN IMMEDIATE")
        for name in DATABASES:
            path = destination / name
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
            reader = sqlite3.connect((directory / name).as_uri() + "?mode=ro", uri=True, timeout=5)
            target = sqlite3.connect(path)
            try:
                reader.backup(target)
                target.execute("PRAGMA journal_mode=DELETE")
            finally:
                reader.close()
                target.close()
            with path.open("rb") as completed:
                os.fsync(completed.fileno())
        _write(destination / MARKER, _read(directory / MARKER))
        document = {"version": 1, "files": {name: _sha256(destination / name) for name in (*DATABASES, MARKER)}}
        _write(destination / SNAPSHOT, {**document, "binding": _mac(config, "snapshot/v1", document)})


def restore(config: GatewayConfig, source: Path) -> None:
    with state_lock(config):
        _private(source, directory=True)
        if {path.name for path in source.iterdir()} != {SNAPSHOT, MARKER, *DATABASES}:
            raise HostedError("hosted_snapshot_files_invalid")
        manifest = _read(source / SNAPSHOT)
        if set(manifest) != {"version", "files", "binding"} or manifest["version"] != 1 or not isinstance(manifest["files"], dict) or set(manifest["files"]) != {*DATABASES, MARKER}:
            raise HostedError("hosted_snapshot_manifest_invalid")
        signature = manifest.pop("binding")
        if not isinstance(signature, str) or not hmac.compare_digest(signature, _mac(config, "snapshot/v1", manifest)):
            raise HostedError("hosted_snapshot_binding_mismatch")
        for name, digest in manifest["files"].items():
            _private(source / name)
            if not isinstance(digest, str) or not hmac.compare_digest(digest, _sha256(source / name)):
                raise HostedError("hosted_snapshot_digest_mismatch")
        check_initialized(at_directory(config, source))
        destination = config.database_path.parent
        _destination(destination, source)
        for name in DATABASES:
            descriptor = os.open(destination / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as output, (source / name).open("rb") as input_file:
                shutil.copyfileobj(input_file, output)
                output.flush()
                os.fsync(output.fileno())
            if not hmac.compare_digest(manifest["files"][name], _sha256(destination / name)):
                raise HostedError("hosted_snapshot_digest_mismatch")
        store = sessions(config)
        directory = TeamDirectory(config, store)
        with store._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for member in connection.execute("SELECT * FROM onboarding_memberships WHERE status != 'disabled'").fetchall():
                directory._disable(connection, member)
            # Defense for unbound flows and sessions, including older snapshots.
            now = _isoformat(store._now())
            connection.execute("UPDATE human_sessions SET revoked_at = ? WHERE revoked_at IS NULL", (now,))
            connection.execute("UPDATE onboarding_invitations SET status = 'revoked', secret_hash = NULL, completed_at = ? WHERE status = 'pending'", (now,))
            connection.execute("UPDATE session_enrollments SET status = 'failed', secret_hash = NULL, encrypted_flow = NULL, state_hash = NULL, browser_cookie_hash = NULL WHERE status IN ('pending', 'authorizing', 'exchanging', 'authorized')")
            connection.execute("UPDATE console_grants SET status = 'revoked', authorization_version = authorization_version + 1, updated_at = ? WHERE status = 'active'", (now,))
            connection.execute("UPDATE console_sessions SET revoked_at = ? WHERE revoked_at IS NULL", (now,))
            connection.execute("UPDATE console_login_flows SET status = 'failed', state_hash = NULL, browser_cookie_hash = NULL, encrypted_flow = NULL WHERE status IN ('pending', 'exchanging')")
        # A crash before this last write leaves an unactivatable partial restore.
        _write(destination / MARKER, _marker(config, recovered=True))
        check_initialized(config)
