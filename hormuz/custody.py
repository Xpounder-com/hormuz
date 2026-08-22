"""Provider-neutral key custody and immutable audit-anchor contracts.

This module deliberately keeps provider SDKs outside the core contract.  A
``DataKeyProvider`` supplies envelope data keys while an ``AuditAnchorSink``
stores a complete, hash-chained audit artifact.  The first concrete provider
lives in :mod:`hormuz.aws_custody`; tests and future providers use the same
small interfaces here.

The module never serializes plaintext credentials.  Encrypted envelope files
contain an encrypted data key, nonce, and AES-GCM ciphertext only.  Audit
artifacts contain already metadata-only Hormuz audit events only.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol, Sequence

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .contracts import (
    AUDIT_ANCHOR_SCHEMA_ID,
    AUDIT_ANCHOR_SCHEMA_VERSION,
    AUDIT_EVENT_SCHEMA_ID,
    validate_audit_event,
)


ENCRYPTED_ENVELOPE_SCHEMA_ID = "hormuz.encrypted-envelope"
ENCRYPTED_ENVELOPE_SCHEMA_VERSION = 1
AUDIT_CHAIN_ENTRY_SCHEMA_ID = "hormuz.audit-chain-entry"
AUDIT_CHAIN_ENTRY_SCHEMA_VERSION = 1
AUDIT_CHAIN_ALGORITHM = "sha256"

KEY_PURPOSE_PROVIDER_CREDENTIAL = "provider_credential"
KEY_PURPOSE_IDENTITY_CONNECTOR_SECRET = "identity_connector_secret"
KEY_PURPOSE_SESSION_MATERIAL = "session_material"
KEY_PURPOSE_APPROVAL_FINGERPRINT = "approval_fingerprint"
KEY_PURPOSE_DATA_ENCRYPTION = "data_encryption"
KEY_PURPOSES = frozenset(
    {
        KEY_PURPOSE_PROVIDER_CREDENTIAL,
        KEY_PURPOSE_IDENTITY_CONNECTOR_SECRET,
        KEY_PURPOSE_SESSION_MATERIAL,
        KEY_PURPOSE_APPROVAL_FINGERPRINT,
        KEY_PURPOSE_DATA_ENCRYPTION,
    }
)

_MAX_ENVELOPE_PLAINTEXT_BYTES = 16 * 1024 * 1024
_MAX_ENVELOPE_SERIALIZED_BYTES = 32 * 1024 * 1024
_MAX_AUDIT_ARTIFACT_BYTES = 64 * 1024 * 1024
_AES_GCM_NONCE_BYTES = 12
_AES_256_KEY_BYTES = 32
_SHA256_HEX_LENGTH = 64


class CustodyError(RuntimeError):
    """A stable, content-free custody or audit-anchor failure."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class GeneratedDataKey:
    """One plaintext data key and its provider-encrypted representation.

    ``plaintext`` is intentionally never serialized or exposed by a Hormuz
    CLI command.  The caller drops it after the local AES-GCM operation.
    """

    key_reference: str
    plaintext: bytes
    encrypted: bytes


@dataclass(frozen=True)
class RewrappedDataKey:
    """A provider-side re-encryption result with no plaintext data key."""

    key_reference: str
    encrypted: bytes


class DataKeyProvider(Protocol):
    """Minimal envelope-key service contract.

    Implementations must bind every operation to the supplied encryption
    context.  The contract supports KMS key rotation through ``rewrap`` so an
    administrator never needs a secret or plaintext data key.
    """

    def generate_data_key(
        self,
        *,
        key_reference: str,
        encryption_context: Mapping[str, str],
    ) -> GeneratedDataKey: ...

    def decrypt_data_key(
        self,
        *,
        key_reference: str,
        encrypted: bytes,
        encryption_context: Mapping[str, str],
    ) -> bytes: ...

    def rewrap_data_key(
        self,
        *,
        source_key_reference: str,
        destination_key_reference: str,
        encrypted: bytes,
        encryption_context: Mapping[str, str],
    ) -> RewrappedDataKey: ...


@dataclass(frozen=True)
class EncryptedEnvelope:
    """Versioned, portable envelope ciphertext with no plaintext fields."""

    organization_id: str
    purpose: str
    key_reference: str
    encrypted_data_key: bytes
    nonce: bytes
    ciphertext: bytes

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_id": ENCRYPTED_ENVELOPE_SCHEMA_ID,
            "schema_version": ENCRYPTED_ENVELOPE_SCHEMA_VERSION,
            "algorithm": "AES-256-GCM",
            "organization_id": self.organization_id,
            "purpose": self.purpose,
            "key_reference": self.key_reference,
            "encrypted_data_key": _encode_bytes(self.encrypted_data_key),
            "nonce": _encode_bytes(self.nonce),
            "ciphertext": _encode_bytes(self.ciphertext),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EncryptedEnvelope":
        _exact_keys(
            value,
            {
                "schema_id",
                "schema_version",
                "algorithm",
                "organization_id",
                "purpose",
                "key_reference",
                "encrypted_data_key",
                "nonce",
                "ciphertext",
            },
            code="encrypted_envelope_malformed",
        )
        if value.get("schema_id") != ENCRYPTED_ENVELOPE_SCHEMA_ID:
            raise CustodyError("encrypted_envelope_schema_unsupported")
        if value.get("schema_version") != ENCRYPTED_ENVELOPE_SCHEMA_VERSION:
            raise CustodyError("encrypted_envelope_schema_unsupported")
        if value.get("algorithm") != "AES-256-GCM":
            raise CustodyError("encrypted_envelope_algorithm_unsupported")
        organization_id = _nonempty_string(value, "organization_id", code="encrypted_envelope_malformed")
        purpose = _nonempty_string(value, "purpose", code="encrypted_envelope_malformed")
        _validate_purpose(purpose)
        key_reference = _nonempty_string(value, "key_reference", code="encrypted_envelope_malformed")
        encrypted_data_key = _decode_bytes(value, "encrypted_data_key", code="encrypted_envelope_malformed")
        nonce = _decode_bytes(value, "nonce", code="encrypted_envelope_malformed")
        ciphertext = _decode_bytes(value, "ciphertext", code="encrypted_envelope_malformed")
        if not encrypted_data_key or len(nonce) != _AES_GCM_NONCE_BYTES or len(ciphertext) < 16:
            raise CustodyError("encrypted_envelope_malformed")
        return cls(
            organization_id=organization_id,
            purpose=purpose,
            key_reference=key_reference,
            encrypted_data_key=encrypted_data_key,
            nonce=nonce,
            ciphertext=ciphertext,
        )


class EnvelopeCipher:
    """AES-256-GCM payload encryption bound to a remote data-key provider."""

    def __init__(self, provider: DataKeyProvider):
        self._provider = provider

    def seal(
        self,
        plaintext: bytes,
        *,
        organization_id: str,
        purpose: str,
        key_reference: str,
    ) -> EncryptedEnvelope:
        _validate_organization_id(organization_id)
        _validate_purpose(purpose)
        _validate_key_reference(key_reference)
        if not isinstance(plaintext, bytes) or not plaintext or len(plaintext) > _MAX_ENVELOPE_PLAINTEXT_BYTES:
            raise CustodyError("encrypted_envelope_plaintext_invalid")
        context = encryption_context(organization_id=organization_id, purpose=purpose)
        generated = self._provider.generate_data_key(
            key_reference=key_reference,
            encryption_context=context,
        )
        _validate_generated_data_key(generated)
        nonce = os.urandom(_AES_GCM_NONCE_BYTES)
        ciphertext = AESGCM(generated.plaintext).encrypt(
            nonce,
            plaintext,
            _envelope_associated_data(
                organization_id=organization_id,
                purpose=purpose,
            ),
        )
        return EncryptedEnvelope(
            organization_id=organization_id,
            purpose=purpose,
            key_reference=generated.key_reference,
            encrypted_data_key=generated.encrypted,
            nonce=nonce,
            ciphertext=ciphertext,
        )

    def unseal(self, envelope: EncryptedEnvelope) -> bytes:
        _validate_envelope(envelope)
        context = encryption_context(
            organization_id=envelope.organization_id,
            purpose=envelope.purpose,
        )
        data_key = self._provider.decrypt_data_key(
            key_reference=envelope.key_reference,
            encrypted=envelope.encrypted_data_key,
            encryption_context=context,
        )
        if not isinstance(data_key, bytes) or len(data_key) != _AES_256_KEY_BYTES:
            raise CustodyError("encrypted_envelope_data_key_invalid")
        try:
            return AESGCM(data_key).decrypt(
                envelope.nonce,
                envelope.ciphertext,
                _envelope_associated_data(
                    organization_id=envelope.organization_id,
                    purpose=envelope.purpose,
                ),
            )
        except InvalidTag:
            raise CustodyError("encrypted_envelope_integrity_invalid") from None

    def rewrap(self, envelope: EncryptedEnvelope, *, destination_key_reference: str) -> EncryptedEnvelope:
        """Move an encrypted data key without handling plaintext secret material."""

        _validate_envelope(envelope)
        _validate_key_reference(destination_key_reference)
        result = self._provider.rewrap_data_key(
            source_key_reference=envelope.key_reference,
            destination_key_reference=destination_key_reference,
            encrypted=envelope.encrypted_data_key,
            encryption_context=encryption_context(
                organization_id=envelope.organization_id,
                purpose=envelope.purpose,
            ),
        )
        if (
            not isinstance(result, RewrappedDataKey)
            or not result.key_reference
            or not isinstance(result.encrypted, bytes)
            or not result.encrypted
        ):
            raise CustodyError("encrypted_envelope_rewrap_invalid")
        return EncryptedEnvelope(
            organization_id=envelope.organization_id,
            purpose=envelope.purpose,
            key_reference=result.key_reference,
            encrypted_data_key=result.encrypted,
            nonce=envelope.nonce,
            ciphertext=envelope.ciphertext,
        )


def encryption_context(*, organization_id: str, purpose: str) -> dict[str, str]:
    """Return the immutable context every KMS operation must authenticate."""

    _validate_organization_id(organization_id)
    _validate_purpose(purpose)
    return {
        "hormuz:schema": ENCRYPTED_ENVELOPE_SCHEMA_ID,
        # AWS records encryption context in service audit logs. Bind the
        # tenant deterministically without writing its raw identifier there.
        # This is an authorization binding, not a secrecy claim: an operator
        # with a small tenant-ID domain could still guess a digest.
        "hormuz:organization_sha256": hashlib.sha256(organization_id.encode("utf-8")).hexdigest(),
        "hormuz:purpose": purpose,
    }


def serialize_envelope(envelope: EncryptedEnvelope) -> bytes:
    """Serialize a validated envelope deterministically without plaintext."""

    _validate_envelope(envelope)
    return _canonical_json_bytes(envelope.as_dict())


def parse_envelope(value: bytes | str) -> EncryptedEnvelope:
    """Strictly parse a serialized envelope with duplicate-key rejection."""

    parsed = _strict_json(value, maximum_bytes=_MAX_ENVELOPE_SERIALIZED_BYTES, code="encrypted_envelope_malformed")
    if not isinstance(parsed, Mapping):
        raise CustodyError("encrypted_envelope_malformed")
    return EncryptedEnvelope.from_dict(parsed)


@dataclass(frozen=True)
class AuditAnchorReceipt:
    """Metadata-only receipt returned by an external immutable anchor."""

    backend: str
    artifact_id: str
    artifact_sha256: str
    head_digest: str
    object_version: str | None = None


class AuditAnchorSink(Protocol):
    """A provider-neutral immutable storage boundary for audit artifacts."""

    def anchor(
        self,
        artifact: bytes,
        *,
        artifact_id: str,
        organization_id: str,
        head_digest: str,
        retention_until: datetime,
        legal_hold: bool,
    ) -> AuditAnchorReceipt: ...


def build_audit_anchor_artifact(
    events: Sequence[Mapping[str, Any]],
    *,
    organization_id: str,
    created_at: datetime | None = None,
    artifact_id: str | None = None,
) -> dict[str, object]:
    """Build a strict, metadata-only, hash-chained export artifact.

    The artifact detects alterations, deletions, reorderings, duplicates, and
    cross-tenant rows after it is created.  An immutable sink is required to
    protect the artifact header and chain head from replacement.
    """

    _validate_organization_id(organization_id)
    if not events:
        raise CustodyError("audit_anchor_events_empty")
    created = _utc_isoformat(created_at or datetime.now(timezone.utc))
    identifier = artifact_id or str(uuid.uuid4())
    _validate_uuid(identifier, code="audit_anchor_malformed")

    entries: list[dict[str, object]] = []
    event_ids: set[str] = set()
    previous_digest: str | None = None
    for sequence, raw_event in enumerate(events, start=1):
        event = _normalized_audit_event(raw_event, organization_id=organization_id)
        event_id = event["id"]
        assert isinstance(event_id, str)
        if event_id in event_ids:
            raise CustodyError("audit_anchor_event_duplicate")
        event_ids.add(event_id)
        digest = _chain_digest(
            organization_id=organization_id,
            sequence=sequence,
            previous_digest=previous_digest,
            event=event,
        )
        entries.append(
            {
                "schema_id": AUDIT_CHAIN_ENTRY_SCHEMA_ID,
                "schema_version": AUDIT_CHAIN_ENTRY_SCHEMA_VERSION,
                "sequence": sequence,
                "previous_digest": previous_digest,
                "event_digest": digest,
                "event": event,
            }
        )
        previous_digest = digest

    artifact: dict[str, object] = {
        "schema_id": AUDIT_ANCHOR_SCHEMA_ID,
        "schema_version": AUDIT_ANCHOR_SCHEMA_VERSION,
        "artifact_id": identifier,
        "organization_id": organization_id,
        "created_at": created,
        "chain_algorithm": AUDIT_CHAIN_ALGORITHM,
        "event_count": len(entries),
        "entries": entries,
        "head_digest": previous_digest,
    }
    verify_audit_anchor_artifact(artifact)
    return artifact


def serialize_audit_anchor_artifact(artifact: Mapping[str, Any]) -> bytes:
    """Verify then canonically serialize an immutable audit artifact."""

    verify_audit_anchor_artifact(artifact)
    return _canonical_json_bytes(artifact)


def parse_audit_anchor_artifact(value: bytes | str) -> dict[str, object]:
    """Strictly parse and cryptographically verify an anchored audit artifact."""

    parsed = _strict_json(value, maximum_bytes=_MAX_AUDIT_ARTIFACT_BYTES, code="audit_anchor_malformed")
    if not isinstance(parsed, Mapping):
        raise CustodyError("audit_anchor_malformed")
    normalized = dict(parsed)
    verify_audit_anchor_artifact(normalized)
    return normalized


def verify_audit_anchor_artifact(artifact: Mapping[str, Any]) -> None:
    """Validate artifact structure and every sequence/digest relationship."""

    _exact_keys(
        artifact,
        {
            "schema_id",
            "schema_version",
            "artifact_id",
            "organization_id",
            "created_at",
            "chain_algorithm",
            "event_count",
            "entries",
            "head_digest",
        },
        code="audit_anchor_malformed",
    )
    if artifact.get("schema_id") != AUDIT_ANCHOR_SCHEMA_ID:
        raise CustodyError("audit_anchor_schema_unsupported")
    if artifact.get("schema_version") != AUDIT_ANCHOR_SCHEMA_VERSION:
        raise CustodyError("audit_anchor_schema_unsupported")
    if artifact.get("chain_algorithm") != AUDIT_CHAIN_ALGORITHM:
        raise CustodyError("audit_anchor_algorithm_unsupported")
    artifact_id = _nonempty_string(artifact, "artifact_id", code="audit_anchor_malformed")
    _validate_uuid(artifact_id, code="audit_anchor_malformed")
    organization_id = _nonempty_string(artifact, "organization_id", code="audit_anchor_malformed")
    _validate_organization_id(organization_id)
    _validate_utc_timestamp(_nonempty_string(artifact, "created_at", code="audit_anchor_malformed"))
    event_count = artifact.get("event_count")
    if isinstance(event_count, bool) or not isinstance(event_count, int) or event_count <= 0:
        raise CustodyError("audit_anchor_malformed")
    entries = artifact.get("entries")
    if not isinstance(entries, list) or len(entries) != event_count:
        raise CustodyError("audit_anchor_count_invalid")

    event_ids: set[str] = set()
    previous_digest: str | None = None
    for expected_sequence, entry in enumerate(entries, start=1):
        if not isinstance(entry, Mapping):
            raise CustodyError("audit_anchor_entry_malformed")
        _exact_keys(
            entry,
            {"schema_id", "schema_version", "sequence", "previous_digest", "event_digest", "event"},
            code="audit_anchor_entry_malformed",
        )
        if entry.get("schema_id") != AUDIT_CHAIN_ENTRY_SCHEMA_ID:
            raise CustodyError("audit_anchor_entry_schema_unsupported")
        if entry.get("schema_version") != AUDIT_CHAIN_ENTRY_SCHEMA_VERSION:
            raise CustodyError("audit_anchor_entry_schema_unsupported")
        sequence = entry.get("sequence")
        if isinstance(sequence, bool) or sequence != expected_sequence:
            raise CustodyError("audit_anchor_sequence_invalid")
        if entry.get("previous_digest") != previous_digest:
            raise CustodyError("audit_anchor_predecessor_invalid")
        event = entry.get("event")
        if not isinstance(event, Mapping):
            raise CustodyError("audit_anchor_entry_malformed")
        normalized_event = _normalized_audit_event(event, organization_id=organization_id)
        event_id = normalized_event["id"]
        assert isinstance(event_id, str)
        if event_id in event_ids:
            raise CustodyError("audit_anchor_event_duplicate")
        event_ids.add(event_id)
        digest = entry.get("event_digest")
        if not isinstance(digest, str) or _is_sha256_hex(digest) is False:
            raise CustodyError("audit_anchor_digest_invalid")
        expected_digest = _chain_digest(
            organization_id=organization_id,
            sequence=expected_sequence,
            previous_digest=previous_digest,
            event=normalized_event,
        )
        if not _constant_time_equal(digest, expected_digest):
            raise CustodyError("audit_anchor_digest_invalid")
        previous_digest = digest

    head_digest = artifact.get("head_digest")
    if not isinstance(head_digest, str) or not _is_sha256_hex(head_digest):
        raise CustodyError("audit_anchor_digest_invalid")
    if not _constant_time_equal(head_digest, previous_digest or ""):
        raise CustodyError("audit_anchor_head_invalid")


def audit_anchor_summary(artifact: Mapping[str, Any]) -> tuple[str, str, int]:
    """Return safe receipt fields after verification."""

    verify_audit_anchor_artifact(artifact)
    artifact_id = artifact["artifact_id"]
    head_digest = artifact["head_digest"]
    event_count = artifact["event_count"]
    assert isinstance(artifact_id, str)
    assert isinstance(head_digest, str)
    assert isinstance(event_count, int)
    return artifact_id, head_digest, event_count


def _normalized_audit_event(event: Mapping[str, Any], *, organization_id: str) -> dict[str, Any]:
    normalized = _strict_json(
        _canonical_json_bytes(event),
        maximum_bytes=_MAX_AUDIT_ARTIFACT_BYTES,
        code="audit_anchor_event_malformed",
    )
    if not isinstance(normalized, dict):
        raise CustodyError("audit_anchor_event_malformed")
    try:
        validate_audit_event(normalized)
    except (TypeError, ValueError):
        raise CustodyError("audit_anchor_event_malformed") from None
    if normalized.get("schema_id") != AUDIT_EVENT_SCHEMA_ID or normalized.get("schema_version") != 2:
        raise CustodyError("audit_anchor_event_schema_unsupported")
    if normalized.get("organization_id") != organization_id:
        raise CustodyError("audit_anchor_tenant_mismatch")
    return normalized


def _chain_digest(
    *,
    organization_id: str,
    sequence: int,
    previous_digest: str | None,
    event: Mapping[str, Any],
) -> str:
    body = {
        "domain": "hormuz.audit-chain.entry.v1",
        "organization_id": organization_id,
        "sequence": sequence,
        "previous_digest": previous_digest,
        "event": event,
    }
    return hashlib.sha256(_canonical_json_bytes(body)).hexdigest()


def _envelope_associated_data(*, organization_id: str, purpose: str) -> bytes:
    return _canonical_json_bytes(
        {
            "schema_id": ENCRYPTED_ENVELOPE_SCHEMA_ID,
            "schema_version": ENCRYPTED_ENVELOPE_SCHEMA_VERSION,
            "organization_id": organization_id,
            "purpose": purpose,
        }
    )


def _validate_envelope(envelope: EncryptedEnvelope) -> None:
    if not isinstance(envelope, EncryptedEnvelope):
        raise CustodyError("encrypted_envelope_malformed")
    _validate_organization_id(envelope.organization_id)
    _validate_purpose(envelope.purpose)
    _validate_key_reference(envelope.key_reference)
    if (
        not isinstance(envelope.encrypted_data_key, bytes)
        or not envelope.encrypted_data_key
        or not isinstance(envelope.nonce, bytes)
        or len(envelope.nonce) != _AES_GCM_NONCE_BYTES
        or not isinstance(envelope.ciphertext, bytes)
        or len(envelope.ciphertext) < 16
    ):
        raise CustodyError("encrypted_envelope_malformed")


def _validate_generated_data_key(value: GeneratedDataKey) -> None:
    if (
        not isinstance(value, GeneratedDataKey)
        or not isinstance(value.key_reference, str)
        or not value.key_reference
        or not isinstance(value.plaintext, bytes)
        or len(value.plaintext) != _AES_256_KEY_BYTES
        or not isinstance(value.encrypted, bytes)
        or not value.encrypted
    ):
        raise CustodyError("encrypted_envelope_data_key_invalid")


def _validate_organization_id(value: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 512 or any(character in value for character in "\x00\r\n"):
        raise CustodyError("encrypted_envelope_organization_invalid")


def _validate_purpose(value: str) -> None:
    if value not in KEY_PURPOSES:
        raise CustodyError("encrypted_envelope_purpose_invalid")


def _validate_key_reference(value: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 2048 or any(character in value for character in "\x00\r\n"):
        raise CustodyError("encrypted_envelope_key_reference_invalid")


def _validate_uuid(value: str, *, code: str) -> None:
    try:
        parsed = uuid.UUID(value)
    except (TypeError, ValueError, AttributeError):
        raise CustodyError(code) from None
    if str(parsed) != value.lower():
        raise CustodyError(code)


def _validate_utc_timestamp(value: str) -> None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise CustodyError("audit_anchor_malformed") from None
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise CustodyError("audit_anchor_malformed")


def _utc_isoformat(value: datetime) -> str:
    if value.tzinfo is None:
        raise CustodyError("audit_anchor_malformed")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError):
        raise CustodyError("audit_anchor_malformed") from None


def _strict_json(value: bytes | str, *, maximum_bytes: int, code: str) -> Any:
    try:
        raw = value.encode("utf-8") if isinstance(value, str) else value
    except UnicodeEncodeError:
        raise CustodyError(code) from None
    if not isinstance(raw, bytes) or not raw or len(raw) > maximum_bytes:
        raise CustodyError(code)
    try:
        text = raw.decode("utf-8")
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_json_members,
            parse_constant=_reject_nonfinite_json_constant,
        )
    except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        raise CustodyError(code) from None


def _reject_duplicate_json_members(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _reject_nonfinite_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _exact_keys(value: Mapping[str, Any], expected: set[str], *, code: str) -> None:
    if set(value) != expected:
        raise CustodyError(code)


def _nonempty_string(value: Mapping[str, Any], name: str, *, code: str) -> str:
    result = value.get(name)
    if not isinstance(result, str) or not result or len(result) > 8192 or any(character in result for character in "\x00\r\n"):
        raise CustodyError(code)
    return result


def _encode_bytes(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _decode_bytes(value: Mapping[str, Any], name: str, *, code: str) -> bytes:
    encoded = value.get(name)
    if not isinstance(encoded, str) or not encoded or len(encoded) > _MAX_ENVELOPE_SERIALIZED_BYTES:
        raise CustodyError(code)
    try:
        return base64.b64decode(encoded.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError):
        raise CustodyError(code) from None


def _is_sha256_hex(value: str) -> bool:
    if len(value) != _SHA256_HEX_LENGTH:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _constant_time_equal(left: str, right: str) -> bool:
    # ``compare_digest`` accepts text but importing hmac solely for one
    # comparison would obscure why this equality must not be early-exit.
    import hmac

    return hmac.compare_digest(left, right)
