from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import BinaryIO, Iterable


AUDIT_CHAIN_SCHEMA_VERSION = "hormuz.audit-chain.v1"
AUDIT_CHAIN_GENESIS_SHA256 = "0" * 64
AUDIT_CHAIN_MAX_EVENTS = 1_000_000
AUDIT_CHAIN_MAX_LINE_BYTES = 2 * 1024 * 1024
AUDIT_CHAIN_MAX_FILE_BYTES = 256 * 1024 * 1024
_AUDIT_EVENT_DOMAIN = b"hormuz.audit-event.v1\x00"
_AUDIT_CHAIN_DOMAIN = b"hormuz.audit-chain.v1\x00"
_CHAIN_FIELDS = {
    "schema_version",
    "sequence",
    "previous_chain_sha256",
    "event_sha256",
    "chain_sha256",
    "event",
}


class AuditChainError(RuntimeError):
    """Raised when chained audit evidence cannot be created or verified."""


@dataclass(frozen=True)
class AuditChainSummary:
    count: int
    head_sha256: str
    file_sha256: str


class _InvalidJSON(ValueError):
    pass


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise AuditChainError("audit chain value is not canonical JSON") from error


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _event_sha256(event: dict[str, object]) -> str:
    return _sha256(_AUDIT_EVENT_DOMAIN + _canonical_json(event))


def _chain_sha256(sequence: int, previous: str, event_sha256: str) -> str:
    return _sha256(
        _AUDIT_CHAIN_DOMAIN
        + sequence.to_bytes(8, "big")
        + bytes.fromhex(previous)
        + bytes.fromhex(event_sha256)
    )


def write_audit_chain(
    values: Iterable[dict[str, object]],
    stream: BinaryIO,
) -> AuditChainSummary:
    """Write deterministic chained JSONL and return its external anchor values."""

    previous = AUDIT_CHAIN_GENESIS_SHA256
    file_digest = hashlib.sha256()
    count = 0
    total_bytes = 0
    for value in values:
        count += 1
        if count > AUDIT_CHAIN_MAX_EVENTS:
            raise AuditChainError("audit chain event count exceeds the supported bound")
        if not isinstance(value, dict) or not all(
            isinstance(key, str) for key in value
        ):
            raise AuditChainError("audit chain event is not a JSON object")
        event_digest = _event_sha256(value)
        chain_digest = _chain_sha256(count, previous, event_digest)
        record = {
            "schema_version": AUDIT_CHAIN_SCHEMA_VERSION,
            "sequence": count,
            "previous_chain_sha256": previous,
            "event_sha256": event_digest,
            "chain_sha256": chain_digest,
            "event": value,
        }
        line = _canonical_json(record) + b"\n"
        if len(line) > AUDIT_CHAIN_MAX_LINE_BYTES:
            raise AuditChainError("audit chain record exceeds the supported bound")
        total_bytes += len(line)
        if total_bytes > AUDIT_CHAIN_MAX_FILE_BYTES:
            raise AuditChainError("audit chain file exceeds the supported bound")
        try:
            written = stream.write(line)
        except (OSError, ValueError) as error:
            raise AuditChainError("audit chain cannot be written") from error
        if written is not None and written != len(line):
            raise AuditChainError("audit chain write was incomplete")
        file_digest.update(line)
        previous = chain_digest
    return AuditChainSummary(
        count=count,
        head_sha256=previous,
        file_sha256=file_digest.hexdigest(),
    )


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _InvalidJSON("duplicate member")
        result[key] = value
    return result


def _invalid_constant(_value: str) -> object:
    raise _InvalidJSON("nonstandard constant")


def _strict_json(value: bytes) -> dict[str, object]:
    try:
        decoded = value.decode("utf-8")
        parsed = json.loads(
            decoded,
            object_pairs_hook=_strict_object,
            parse_constant=_invalid_constant,
        )
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise AuditChainError("audit chain record is not strict JSON") from error
    if not isinstance(parsed, dict):
        raise AuditChainError("audit chain record is not a JSON object")
    return parsed


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _validate_anchor(
    expected_head_sha256: str,
    expected_count: int,
    expected_file_sha256: str | None,
) -> None:
    if not _is_sha256(expected_head_sha256):
        raise AuditChainError("expected audit chain head is invalid")
    if (
        isinstance(expected_count, bool)
        or not isinstance(expected_count, int)
        or expected_count < 0
        or expected_count > AUDIT_CHAIN_MAX_EVENTS
    ):
        raise AuditChainError("expected audit chain count is invalid")
    if expected_file_sha256 is not None and not _is_sha256(
        expected_file_sha256
    ):
        raise AuditChainError("expected audit file digest is invalid")


def verify_audit_chain(
    path: Path,
    *,
    expected_head_sha256: str,
    expected_count: int,
    expected_file_sha256: str | None = None,
) -> AuditChainSummary:
    """Verify canonical chain structure against an externally retained anchor."""

    _validate_anchor(
        expected_head_sha256,
        expected_count,
        expected_file_sha256,
    )
    try:
        if path.is_symlink():
            raise AuditChainError("audit chain cannot be opened safely")
    except OSError as error:
        raise AuditChainError("audit chain cannot be opened safely") from error
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise AuditChainError("audit chain input is not a regular file")
        if details.st_size > AUDIT_CHAIN_MAX_FILE_BYTES:
            raise AuditChainError("audit chain file exceeds the supported bound")
        stream = os.fdopen(descriptor, "rb")
    except AuditChainError:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise
    except OSError as error:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise AuditChainError("audit chain cannot be opened safely") from error

    previous = AUDIT_CHAIN_GENESIS_SHA256
    count = 0
    file_digest = hashlib.sha256()
    try:
        with stream:
            while True:
                line = stream.readline(AUDIT_CHAIN_MAX_LINE_BYTES + 1)
                if not line:
                    break
                if len(line) > AUDIT_CHAIN_MAX_LINE_BYTES:
                    raise AuditChainError(
                        "audit chain record exceeds the supported bound"
                    )
                if not line.endswith(b"\n"):
                    raise AuditChainError("audit chain record is not newline terminated")
                file_digest.update(line)
                count += 1
                if count > AUDIT_CHAIN_MAX_EVENTS:
                    raise AuditChainError(
                        "audit chain event count exceeds the supported bound"
                    )
                record = _strict_json(line[:-1])
                if set(record) != _CHAIN_FIELDS:
                    raise AuditChainError("audit chain record fields are invalid")
                if record.get("schema_version") != AUDIT_CHAIN_SCHEMA_VERSION:
                    raise AuditChainError("audit chain schema version is unsupported")
                sequence = record.get("sequence")
                if (
                    isinstance(sequence, bool)
                    or not isinstance(sequence, int)
                    or sequence != count
                ):
                    raise AuditChainError("audit chain sequence is invalid")
                event = record.get("event")
                if not isinstance(event, dict) or not all(
                    isinstance(key, str) for key in event
                ):
                    raise AuditChainError("audit chain event is not a JSON object")
                previous_digest = record.get("previous_chain_sha256")
                event_digest = record.get("event_sha256")
                chain_digest = record.get("chain_sha256")
                if not all(
                    _is_sha256(value)
                    for value in (previous_digest, event_digest, chain_digest)
                ):
                    raise AuditChainError("audit chain digest is invalid")
                if previous_digest != previous:
                    raise AuditChainError("audit chain predecessor is invalid")
                if event_digest != _event_sha256(event):
                    raise AuditChainError("audit chain event digest is invalid")
                if chain_digest != _chain_sha256(count, previous, event_digest):
                    raise AuditChainError("audit chain link digest is invalid")
                if _canonical_json(record) + b"\n" != line:
                    raise AuditChainError("audit chain record is not canonical")
                previous = chain_digest
    except OSError as error:
        raise AuditChainError("audit chain cannot be read") from error

    actual_file_sha256 = file_digest.hexdigest()
    if count != expected_count:
        raise AuditChainError("audit chain count does not match the external anchor")
    if previous != expected_head_sha256:
        raise AuditChainError("audit chain head does not match the external anchor")
    if (
        expected_file_sha256 is not None
        and actual_file_sha256 != expected_file_sha256
    ):
        raise AuditChainError("audit file digest does not match the external anchor")
    return AuditChainSummary(
        count=count,
        head_sha256=previous,
        file_sha256=actual_file_sha256,
    )
