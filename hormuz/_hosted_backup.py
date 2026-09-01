"""Encrypted, streaming off-disk archives for hosted staging state."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import secrets
import stat
import struct
import tempfile
from pathlib import Path
from typing import BinaryIO

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from ._hosted_config import HostedError
from ._hosted_state import (
    DATABASES,
    MARKER,
    SNAPSHOT,
    _parent,
    _private,
    _read,
    _sha256,
    restore,
    snapshot,
)
from .config import GatewayConfig


BACKUP_SCHEMA = "hormuz.hosted-offsite-backup"
BACKUP_MAGIC = b"HORMUZ-HOSTED-BACKUP\x00\x01"
BACKUP_FILES = (SNAPSHOT, MARKER, *DATABASES)
NONCE_BYTES = 12
TAG_BYTES = 16
CHUNK_BYTES = 1024 * 1024
MAX_MANIFEST_BYTES = 4096


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate_json_member")
        value[key] = item
    return value


def _decode_json(encoded: bytes, *, code: str) -> object:
    try:
        return json.loads(encoded, object_pairs_hook=_unique_object)
    except (ValueError, RecursionError):
        raise HostedError(code) from None


def _validated_key(value: bytes, *, session_master_key: bytes | None = None) -> bytes:
    if not isinstance(value, bytes) or len(value) != 32:
        raise HostedError("hosted_backup_key_invalid")
    if session_master_key is not None and hmac.compare_digest(value, session_master_key):
        raise HostedError("hosted_backup_key_reused")
    return value


def _open_private_input(path: Path, *, code: str) -> tuple[BinaryIO, os.stat_result]:
    try:
        if path != path.resolve():
            raise HostedError(code)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        raise HostedError(code) from None
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or info.st_nlink != 1
        ):
            raise HostedError(code)
        return os.fdopen(descriptor, "rb"), info
    except Exception:
        os.close(descriptor)
        raise


def read_backup_key(path: Path, *, session_master_key: bytes | None = None) -> bytes:
    """Read one canonical base64-encoded AES-256 key from an owner-only file."""

    source, info = _open_private_input(path, code="hosted_backup_key_file_invalid")
    with source:
        if info.st_size not in {44, 45}:
            raise HostedError("hosted_backup_key_invalid")
        encoded = source.read()
    if encoded.endswith(b"\n"):
        encoded = encoded[:-1]
    try:
        value = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise HostedError("hosted_backup_key_invalid") from None
    if base64.b64encode(value) != encoded:
        raise HostedError("hosted_backup_key_invalid")
    return _validated_key(value, session_master_key=session_master_key)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _archive_manifest(directory: Path) -> tuple[bytes, tuple[dict[str, object], ...]]:
    snapshot_manifest = _read(directory / SNAPSHOT)
    if (
        set(snapshot_manifest) != {"version", "files", "binding"}
        or type(snapshot_manifest["version"]) is not int
        or snapshot_manifest["version"] != 1
        or not isinstance(snapshot_manifest["files"], dict)
        or set(snapshot_manifest["files"]) != {*DATABASES, MARKER}
    ):
        raise HostedError("hosted_backup_snapshot_invalid")
    entries: list[dict[str, object]] = []
    for name in BACKUP_FILES:
        path = directory / name
        info = _private(path)
        digest = _sha256(path) if name == SNAPSHOT else snapshot_manifest["files"][name]
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise HostedError("hosted_backup_snapshot_invalid")
        entries.append({"name": name, "size": info.st_size, "sha256": digest})
    document = {"schema": BACKUP_SCHEMA, "version": 1, "files": entries}
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_MANIFEST_BYTES:
        raise HostedError("hosted_backup_manifest_invalid")
    return encoded, tuple(entries)


def _validate_output(path: Path, active: Path) -> None:
    if (
        path != path.resolve()
        or path == active
        or path.is_relative_to(active)
        or active.is_relative_to(path)
    ):
        raise HostedError("hosted_backup_output_invalid")
    _parent(path)


def _encrypt_snapshot(directory: Path, output: Path, key: bytes, *, active: Path) -> dict[str, object]:
    manifest, entries = _archive_manifest(directory)
    _validate_output(output, active)
    try:
        descriptor = os.open(
            output,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
        )
    except FileExistsError:
        raise HostedError("hosted_backup_output_exists") from None
    except OSError:
        raise HostedError("hosted_backup_output_invalid") from None

    completed = False
    archive_digest = hashlib.sha256()
    archive_size = 0
    nonce = secrets.token_bytes(NONCE_BYTES)
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(BACKUP_MAGIC)

    try:
        with os.fdopen(descriptor, "wb") as target:
            def write(value: bytes) -> None:
                nonlocal archive_size
                target.write(value)
                archive_digest.update(value)
                archive_size += len(value)

            write(BACKUP_MAGIC)
            write(nonce)
            write(encryptor.update(struct.pack(">I", len(manifest))))
            write(encryptor.update(manifest))
            for entry in entries:
                path = directory / str(entry["name"])
                digest = hashlib.sha256()
                size = 0
                with path.open("rb") as source:
                    while chunk := source.read(CHUNK_BYTES):
                        size += len(chunk)
                        digest.update(chunk)
                        write(encryptor.update(chunk))
                if size != entry["size"] or not hmac.compare_digest(digest.hexdigest(), str(entry["sha256"])):
                    raise HostedError("hosted_backup_snapshot_changed")
            write(encryptor.finalize())
            write(encryptor.tag)
            target.flush()
            os.fsync(target.fileno())
        _fsync_directory(output.parent)
        completed = True
    finally:
        if not completed:
            try:
                output.unlink()
                _fsync_directory(output.parent)
            except FileNotFoundError:
                pass
    return {
        "archive_bytes": archive_size,
        "archive_sha256": archive_digest.hexdigest(),
        "backup_schema": BACKUP_SCHEMA,
    }


class _ArchiveConsumer:
    def __init__(self, destination: Path | None):
        self.destination = destination
        self.buffer = bytearray()
        self.manifest_length: int | None = None
        self.entries: tuple[dict[str, object], ...] | None = None
        self.index = 0
        self.remaining = 0
        self.digest = None
        self.output: BinaryIO | None = None
        self.capture = bytearray()
        self.captured: dict[str, bytes] = {}

    def _parse_manifest(self, encoded: bytes) -> None:
        document = _decode_json(encoded, code="hosted_backup_manifest_invalid")
        if (
            not isinstance(document, dict)
            or set(document) != {"schema", "version", "files"}
            or document["schema"] != BACKUP_SCHEMA
            or type(document["version"]) is not int
            or document["version"] != 1
            or not isinstance(document["files"], list)
            or len(document["files"]) != len(BACKUP_FILES)
        ):
            raise HostedError("hosted_backup_manifest_invalid")
        entries: list[dict[str, object]] = []
        for expected_name, entry in zip(BACKUP_FILES, document["files"], strict=True):
            if (
                not isinstance(entry, dict)
                or set(entry) != {"name", "size", "sha256"}
                or entry["name"] != expected_name
                or isinstance(entry["size"], bool)
                or not isinstance(entry["size"], int)
                or entry["size"] < 0
                or not isinstance(entry["sha256"], str)
                or len(entry["sha256"]) != 64
                or any(character not in "0123456789abcdef" for character in entry["sha256"])
            ):
                raise HostedError("hosted_backup_manifest_invalid")
            entries.append(entry)
        self.entries = tuple(entries)
        self._start_entry()

    def _start_entry(self) -> None:
        while self.entries is not None and self.index < len(self.entries):
            entry = self.entries[self.index]
            self.remaining = int(entry["size"])
            self.digest = hashlib.sha256()
            self.capture = bytearray()
            if entry["name"] in {SNAPSHOT, MARKER} and not 1 <= self.remaining <= 4096:
                raise HostedError("hosted_backup_payload_invalid")
            if entry["name"] in DATABASES and self.remaining < 16:
                raise HostedError("hosted_backup_payload_invalid")
            if self.destination is not None:
                descriptor = os.open(
                    self.destination / str(entry["name"]),
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                    0o600,
                )
                self.output = os.fdopen(descriptor, "wb")
            if self.remaining:
                return
            self._finish_entry()

    def _finish_entry(self) -> None:
        assert self.entries is not None and self.digest is not None
        if self.output is not None:
            self.output.flush()
            os.fsync(self.output.fileno())
            self.output.close()
            self.output = None
        if not hmac.compare_digest(self.digest.hexdigest(), str(self.entries[self.index]["sha256"])):
            raise HostedError("hosted_backup_digest_mismatch")
        self.captured[str(self.entries[self.index]["name"])] = bytes(self.capture)
        self.index += 1
        self.digest = None

    def feed(self, data: bytes) -> None:
        if self.entries is None:
            self.buffer.extend(data)
            if self.manifest_length is None and len(self.buffer) >= 4:
                self.manifest_length = struct.unpack(">I", self.buffer[:4])[0]
                if not 1 <= self.manifest_length <= MAX_MANIFEST_BYTES:
                    raise HostedError("hosted_backup_manifest_invalid")
            if self.manifest_length is None or len(self.buffer) < 4 + self.manifest_length:
                if len(self.buffer) > 4 + MAX_MANIFEST_BYTES:
                    raise HostedError("hosted_backup_manifest_invalid")
                return
            end = 4 + self.manifest_length
            remainder = bytes(self.buffer[end:])
            encoded = bytes(self.buffer[4:end])
            self.buffer.clear()
            self._parse_manifest(encoded)
            data = remainder

        while data:
            if self.entries is None or self.index >= len(self.entries) or self.digest is None:
                raise HostedError("hosted_backup_payload_invalid")
            consumed = min(len(data), self.remaining)
            chunk, data = data[:consumed], data[consumed:]
            self.digest.update(chunk)
            name = str(self.entries[self.index]["name"])
            capture_limit = 4096 if name in {SNAPSHOT, MARKER} else 16
            if len(self.capture) < capture_limit:
                self.capture.extend(chunk[:capture_limit - len(self.capture)])
            if self.output is not None:
                self.output.write(chunk)
            self.remaining -= consumed
            if self.remaining == 0:
                self._finish_entry()
                self._start_entry()

    def finish(self) -> None:
        if self.entries is None or self.index != len(self.entries) or self.output is not None:
            raise HostedError("hosted_backup_payload_invalid")
        try:
            snapshot_document = _decode_json(self.captured[SNAPSHOT], code="hosted_backup_payload_invalid")
            marker_document = _decode_json(self.captured[MARKER], code="hosted_backup_payload_invalid")
        except (KeyError, HostedError):
            raise HostedError("hosted_backup_payload_invalid") from None
        if (
            not isinstance(snapshot_document, dict)
            or set(snapshot_document) != {"version", "files", "binding"}
            or type(snapshot_document["version"]) is not int
            or snapshot_document["version"] != 1
            or not isinstance(snapshot_document["files"], dict)
            or set(snapshot_document["files"]) != {*DATABASES, MARKER}
            or not isinstance(snapshot_document["binding"], str)
            or len(snapshot_document["binding"]) != 64
            or not isinstance(marker_document, dict)
            or set(marker_document) != {"version", "instance", "recovered_closed", "binding"}
            or type(marker_document["version"]) is not int
            or marker_document["version"] != 1
            or not isinstance(marker_document["instance"], str)
            or len(marker_document["instance"]) != 32
            or type(marker_document["recovered_closed"]) is not bool
            or not isinstance(marker_document["binding"], str)
            or len(marker_document["binding"]) != 64
        ):
            raise HostedError("hosted_backup_payload_invalid")
        entries = {str(entry["name"]): str(entry["sha256"]) for entry in self.entries}
        if any(
            not isinstance(digest, str) or not hmac.compare_digest(digest, entries[name])
            for name, digest in snapshot_document["files"].items()
        ):
            raise HostedError("hosted_backup_payload_invalid")
        for value in (
            snapshot_document["binding"], marker_document["binding"], marker_document["instance"],
        ):
            if any(character not in "0123456789abcdef" for character in value):
                raise HostedError("hosted_backup_payload_invalid")
        if any(self.captured[name] != b"SQLite format 3\x00" for name in DATABASES):
            raise HostedError("hosted_backup_payload_invalid")
        if self.destination is not None:
            _fsync_directory(self.destination)

    def close(self) -> None:
        if self.output is not None:
            self.output.close()
            self.output = None


def _decrypt_pass(path: Path, key: bytes, consumer: _ArchiveConsumer | None) -> dict[str, object]:
    source, info = _open_private_input(path, code="hosted_backup_archive_invalid")
    minimum_size = len(BACKUP_MAGIC) + NONCE_BYTES + TAG_BYTES + 5
    archive_digest = hashlib.sha256()
    try:
        with source:
            if info.st_size < minimum_size:
                raise HostedError("hosted_backup_archive_invalid")
            header = source.read(len(BACKUP_MAGIC) + NONCE_BYTES)
            if len(header) != len(BACKUP_MAGIC) + NONCE_BYTES or header[:len(BACKUP_MAGIC)] != BACKUP_MAGIC:
                raise HostedError("hosted_backup_archive_invalid")
            nonce = header[len(BACKUP_MAGIC):]
            ciphertext_size = info.st_size - len(header) - TAG_BYTES
            source.seek(info.st_size - TAG_BYTES)
            tag = source.read(TAG_BYTES)
            if len(tag) != TAG_BYTES:
                raise HostedError("hosted_backup_archive_invalid")
            source.seek(len(header))
            decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
            decryptor.authenticate_additional_data(BACKUP_MAGIC)
            archive_digest.update(header)
            remaining = ciphertext_size
            while remaining:
                chunk = source.read(min(CHUNK_BYTES, remaining))
                if not chunk:
                    raise HostedError("hosted_backup_archive_invalid")
                remaining -= len(chunk)
                archive_digest.update(chunk)
                plain = decryptor.update(chunk)
                if consumer is not None:
                    consumer.feed(plain)
            archive_digest.update(tag)
            try:
                final = decryptor.finalize()
            except InvalidTag:
                raise HostedError("hosted_backup_authentication_failed") from None
            if consumer is not None:
                consumer.feed(final)
                consumer.finish()
    finally:
        if consumer is not None:
            consumer.close()
    return {
        "archive_bytes": info.st_size,
        "archive_sha256": archive_digest.hexdigest(),
        "backup_schema": BACKUP_SCHEMA,
    }


def verify_backup(source: Path, key: bytes) -> dict[str, object]:
    """Authenticate and structurally validate an archive without writing plaintext."""

    key = _validated_key(key)
    summary = _decrypt_pass(source, key, None)
    _decrypt_pass(source, key, _ArchiveConsumer(None))
    return summary


def export_backup(config: GatewayConfig, output: Path, key: bytes) -> dict[str, object]:
    """Create one consistent snapshot and stream it into an encrypted archive."""

    key = _validated_key(key, session_master_key=config.session_broker.master_key)
    active = config.database_path.parent
    with tempfile.TemporaryDirectory(prefix=".hormuz-offsite-export-", dir=active.parent) as temporary:
        directory = Path(temporary) / "snapshot"
        snapshot(config, directory)
        return _encrypt_snapshot(directory, output, key, active=active)


def restore_backup(config: GatewayConfig, source: Path, key: bytes) -> dict[str, object]:
    """Verify an archive, decrypt privately, then invoke closed-state restore."""

    key = _validated_key(key, session_master_key=config.session_broker.master_key)
    destination = config.database_path.parent
    if destination.exists() or destination.is_symlink():
        raise HostedError("hosted_backup_destination_exists")
    _parent(destination)
    summary = _decrypt_pass(source, key, None)
    with tempfile.TemporaryDirectory(prefix=".hormuz-offsite-restore-", dir=destination.parent) as temporary:
        directory = Path(temporary)
        _decrypt_pass(source, key, _ArchiveConsumer(directory))
        recovery = restore(config, directory)
    return {**summary, **recovery}
