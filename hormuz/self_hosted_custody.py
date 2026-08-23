"""Self-hosted encrypted audit anchors for S3-compatible Object Lock stores.

The adapter deliberately separates key custody from object retention: a
``DataKeyProvider`` encrypts the complete metadata-only audit artifact before
it leaves Hormuz, while an S3-compatible Object Lock service retains the
ciphertext in Object Lock COMPLIANCE mode.  That keeps this profile independent
of AWS KMS and avoids relying on a storage provider's proprietary encryption
header for confidentiality.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import urlparse

from .contracts import AUDIT_ANCHOR_SCHEMA_ID, AUDIT_ANCHOR_SCHEMA_VERSION
from .custody import (
    ENCRYPTED_ENVELOPE_SCHEMA_ID,
    ENCRYPTED_ENVELOPE_SCHEMA_VERSION,
    KEY_PURPOSE_DATA_ENCRYPTION,
    AuditAnchorReceipt,
    CustodyError,
    DataKeyProvider,
    EnvelopeCipher,
    audit_anchor_summary,
    parse_audit_anchor_artifact,
    parse_envelope,
    serialize_audit_anchor_artifact,
    serialize_envelope,
)


_MAX_ENCRYPTED_ANCHOR_BYTES = 32 * 1024 * 1024


class EncryptedS3ObjectLockAuditAnchorSink:
    """Store only a sealed audit artifact in a compliant S3 Object Lock bucket."""

    backend = "s3-compatible-object-lock"

    def __init__(
        self,
        client: Any,
        *,
        region: str,
        bucket: str,
        prefix: str,
        key_provider: DataKeyProvider,
        encryption_key_reference: str,
    ) -> None:
        self._client = client
        self._region = region
        self._bucket = bucket
        self._prefix = prefix.strip("/")
        self._cipher = EnvelopeCipher(key_provider)
        self._encryption_key_reference = encryption_key_reference

    def verify_configuration(self) -> None:
        """Fail closed unless the target can retain locked object versions."""

        try:
            versioning = self._client.get_bucket_versioning(Bucket=self._bucket)
            lock = self._client.get_object_lock_configuration(Bucket=self._bucket)
            location = self._client.get_bucket_location(Bucket=self._bucket)
        except Exception as error:
            raise _s3_custody_error(error, operation="verify") from None
        if not isinstance(versioning, Mapping) or versioning.get("Status") != "Enabled":
            raise CustodyError("s3_object_lock_versioning_required")
        if not isinstance(lock, Mapping):
            raise CustodyError("s3_object_lock_required")
        configuration = lock.get("ObjectLockConfiguration")
        if not isinstance(configuration, Mapping) or configuration.get("ObjectLockEnabled") != "Enabled":
            raise CustodyError("s3_object_lock_required")
        if _bucket_region(location) != self._region:
            raise CustodyError("s3_object_lock_region_mismatch")

    def anchor(
        self,
        artifact: bytes,
        *,
        artifact_id: str,
        organization_id: str,
        head_digest: str,
        retention_until: datetime,
        legal_hold: bool,
    ) -> AuditAnchorReceipt:
        raw_digest = _validate_anchor_input(
            artifact,
            artifact_id=artifact_id,
            organization_id=organization_id,
            head_digest=head_digest,
            retention_until=retention_until,
        )
        sealed = self._cipher.seal(
            artifact,
            organization_id=organization_id,
            purpose=KEY_PURPOSE_DATA_ENCRYPTION,
            key_reference=self._encryption_key_reference,
        )
        payload = serialize_envelope(sealed)
        # Parse the just-serialized representation before egress so a future
        # serializer change cannot accidentally publish a non-portable payload.
        if not hmac.compare_digest(
            self._cipher.unseal(parse_envelope(payload)),
            artifact,
        ):
            raise CustodyError("audit_anchor_payload_integrity_invalid")
        payload_digest = hashlib.sha256(payload).digest()
        request: dict[str, object] = {
            "Bucket": self._bucket,
            "Key": self._object_key(organization_id=organization_id, artifact_id=artifact_id),
            "Body": payload,
            "ContentType": "application/json",
            "ChecksumAlgorithm": "SHA256",
            "ChecksumSHA256": _base64(payload_digest),
            "ObjectLockMode": "COMPLIANCE",
            "ObjectLockRetainUntilDate": retention_until.astimezone(timezone.utc),
            "IfNoneMatch": "*",
            "Metadata": {
                "hormuz-schema-id": ENCRYPTED_ENVELOPE_SCHEMA_ID,
                "hormuz-schema-version": str(ENCRYPTED_ENVELOPE_SCHEMA_VERSION),
                "hormuz-audit-anchor-schema-id": AUDIT_ANCHOR_SCHEMA_ID,
                "hormuz-audit-anchor-schema-version": str(AUDIT_ANCHOR_SCHEMA_VERSION),
                "hormuz-artifact-sha256": raw_digest.hex(),
                "hormuz-head-digest": head_digest,
                "hormuz-payload-sha256": payload_digest.hex(),
            },
        }
        if legal_hold:
            request["ObjectLockLegalHoldStatus"] = "ON"
        try:
            response = self._client.put_object(**request)
        except Exception as error:
            raise _s3_custody_error(error, operation="write") from None
        version = response.get("VersionId") if isinstance(response, Mapping) else None
        if version is not None and not isinstance(version, str):
            raise CustodyError("s3_object_lock_response_invalid")
        return AuditAnchorReceipt(
            backend=self.backend,
            artifact_id=artifact_id,
            artifact_sha256=raw_digest.hex(),
            head_digest=head_digest,
            object_version=version,
        )

    def recover(self, receipt: AuditAnchorReceipt, *, organization_id: str) -> bytes:
        """Recover and verify one exact encrypted Object Lock artifact version.

        The method is intentionally an adapter capability rather than a normal
        gateway route. It accepts only an exact receipt/object-version pair,
        keeps the recovered metadata-only artifact in process memory, and
        validates the envelope, object metadata, canonical artifact, and audit
        chain before returning it to a controlled recovery procedure.
        """

        _validate_recovery_receipt(receipt)
        if not isinstance(organization_id, str) or not organization_id:
            raise CustodyError("audit_anchor_recovery_receipt_invalid")
        try:
            object_key = self._object_key(organization_id=organization_id, artifact_id=receipt.artifact_id)
            response = self._client.get_object(
                Bucket=self._bucket,
                Key=object_key,
                VersionId=receipt.object_version,
            )
        except Exception as error:
            raise _s3_custody_error(error, operation="read") from None
        if not isinstance(response, Mapping):
            raise CustodyError("audit_anchor_recovery_object_invalid")
        body = response.get("Body")
        read = getattr(body, "read", None)
        if not callable(read):
            raise CustodyError("audit_anchor_recovery_object_invalid")
        try:
            payload = read(_MAX_ENCRYPTED_ANCHOR_BYTES + 1)
        except TypeError:
            try:
                payload = read()
            except Exception:
                raise CustodyError("audit_anchor_recovery_object_invalid") from None
        except Exception:
            raise CustodyError("audit_anchor_recovery_object_invalid") from None
        if not isinstance(payload, bytes) or not payload or len(payload) > _MAX_ENCRYPTED_ANCHOR_BYTES:
            raise CustodyError("audit_anchor_recovery_object_invalid")

        payload_digest = hashlib.sha256(payload).hexdigest()
        metadata = response.get("Metadata")
        if not isinstance(metadata, Mapping) or not _metadata_matches_receipt(
            metadata,
            receipt=receipt,
            payload_digest=payload_digest,
        ):
            raise CustodyError("audit_anchor_recovery_metadata_invalid")
        envelope = parse_envelope(payload)
        if envelope.organization_id != organization_id or envelope.purpose != KEY_PURPOSE_DATA_ENCRYPTION:
            raise CustodyError("audit_anchor_recovery_metadata_invalid")
        artifact = self._cipher.unseal(envelope)
        parsed = parse_audit_anchor_artifact(artifact)
        if not hmac.compare_digest(serialize_audit_anchor_artifact(parsed), artifact):
            raise CustodyError("audit_anchor_recovery_payload_invalid")
        artifact_id, head_digest, _ = audit_anchor_summary(parsed)
        artifact_digest = hashlib.sha256(artifact).hexdigest()
        if (
            artifact_id != receipt.artifact_id
            or not hmac.compare_digest(head_digest, receipt.head_digest)
            or not hmac.compare_digest(artifact_digest, receipt.artifact_sha256)
        ):
            raise CustodyError("audit_anchor_recovery_payload_invalid")
        return artifact

    def _object_key(self, *, organization_id: str, artifact_id: str) -> str:
        organization_hash = hashlib.sha256(organization_id.encode("utf-8")).hexdigest()[:24]
        prefix = f"{self._prefix}/" if self._prefix else ""
        return f"{prefix}v1/{organization_hash}/{artifact_id}.json"


def create_s3_compatible_object_lock_anchor_sink(
    *,
    endpoint_url: str,
    region: str,
    bucket: str,
    prefix: str,
    access_key: str,
    secret_key: str,
    key_provider: DataKeyProvider,
    encryption_key_reference: str,
) -> EncryptedS3ObjectLockAuditAnchorSink:
    """Create a path-style S3 client for an explicit self-hosted endpoint."""

    if not _service_origin(endpoint_url):
        raise CustodyError("s3_object_lock_endpoint_invalid")
    if not access_key or not secret_key:
        raise CustodyError("s3_object_lock_credentials_unavailable")
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        raise CustodyError("s3_object_lock_sdk_unavailable") from None
    try:
        client = boto3.session.Session(
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        ).client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region,
            config=Config(s3={"addressing_style": "path"}),
        )
    except Exception as error:
        raise _s3_custody_error(error, operation="session") from None
    return EncryptedS3ObjectLockAuditAnchorSink(
        client,
        region=region,
        bucket=bucket,
        prefix=prefix,
        key_provider=key_provider,
        encryption_key_reference=encryption_key_reference,
    )


def _validate_anchor_input(
    artifact: bytes,
    *,
    artifact_id: str,
    organization_id: str,
    head_digest: str,
    retention_until: datetime,
) -> bytes:
    if not isinstance(artifact, bytes) or not artifact:
        raise CustodyError("audit_anchor_artifact_invalid")
    if retention_until.tzinfo is None or retention_until <= datetime.now(timezone.utc):
        raise CustodyError("audit_anchor_retention_invalid")
    parsed = parse_audit_anchor_artifact(artifact)
    if not hmac.compare_digest(artifact, serialize_audit_anchor_artifact(parsed)):
        raise CustodyError("audit_anchor_artifact_noncanonical")
    actual_artifact_id, actual_head_digest, _ = audit_anchor_summary(parsed)
    if (
        parsed.get("organization_id") != organization_id
        or actual_artifact_id != artifact_id
        or actual_head_digest != head_digest
    ):
        raise CustodyError("audit_anchor_metadata_mismatch")
    return hashlib.sha256(artifact).digest()


def _validate_recovery_receipt(receipt: object) -> None:
    if not isinstance(receipt, AuditAnchorReceipt) or receipt.backend != EncryptedS3ObjectLockAuditAnchorSink.backend:
        raise CustodyError("audit_anchor_recovery_receipt_invalid")
    if not isinstance(receipt.object_version, str) or not receipt.object_version:
        raise CustodyError("audit_anchor_recovery_receipt_invalid")
    try:
        uuid.UUID(receipt.artifact_id)
    except (TypeError, ValueError, AttributeError):
        raise CustodyError("audit_anchor_recovery_receipt_invalid") from None
    for value in (receipt.artifact_sha256, receipt.head_digest):
        if not isinstance(value, str) or len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise CustodyError("audit_anchor_recovery_receipt_invalid")


def _metadata_matches_receipt(
    metadata: Mapping[object, object],
    *,
    receipt: AuditAnchorReceipt,
    payload_digest: str,
) -> bool:
    expected = {
        "hormuz-schema-id": ENCRYPTED_ENVELOPE_SCHEMA_ID,
        "hormuz-schema-version": str(ENCRYPTED_ENVELOPE_SCHEMA_VERSION),
        "hormuz-audit-anchor-schema-id": AUDIT_ANCHOR_SCHEMA_ID,
        "hormuz-audit-anchor-schema-version": str(AUDIT_ANCHOR_SCHEMA_VERSION),
        "hormuz-artifact-sha256": receipt.artifact_sha256,
        "hormuz-head-digest": receipt.head_digest,
        "hormuz-payload-sha256": payload_digest,
    }
    return all(
        isinstance(metadata.get(key), str) and hmac.compare_digest(metadata[key], value)
        for key, value in expected.items()
    )


def _bucket_region(value: object) -> str:
    if not isinstance(value, Mapping):
        raise CustodyError("s3_object_lock_response_invalid")
    location = value.get("LocationConstraint")
    if location is None:
        return "us-east-1"
    if location == "EU":
        return "eu-west-1"
    if not isinstance(location, str) or not location:
        raise CustodyError("s3_object_lock_response_invalid")
    return location


def _base64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _s3_custody_error(error: BaseException, *, operation: str) -> CustodyError:
    response = getattr(error, "response", None)
    code: object = None
    if isinstance(response, Mapping):
        detail = response.get("Error")
        if isinstance(detail, Mapping):
            code = detail.get("Code")
    if code in {"AccessDenied", "AccessDeniedException", "Unauthorized"}:
        return CustodyError("s3_object_lock_access_denied")
    if operation == "write" and code in {"PreconditionFailed", "ConditionalRequestConflict"}:
        return CustodyError("audit_anchor_object_conflict")
    return CustodyError("s3_object_lock_unavailable")


def _service_origin(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    parsed = urlparse(value)
    if not (
        parsed.scheme in {"http", "https"}
        and parsed.netloc
        and parsed.path in {"", "/"}
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    ):
        return False
    return parsed.scheme != "http" or parsed.hostname in {"127.0.0.1", "::1", "localhost"}
