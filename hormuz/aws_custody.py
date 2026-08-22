"""AWS KMS and S3 Object Lock implementations of the custody contracts.

``boto3`` is imported only by the construction helpers.  The core Hormuz
wheel remains usable without AWS dependencies until an operator enables the
explicit AWS custody profile.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from .custody import (
    AuditAnchorReceipt,
    CustodyError,
    GeneratedDataKey,
    RewrappedDataKey,
    audit_anchor_summary,
    encryption_context,
    parse_audit_anchor_artifact,
    serialize_audit_anchor_artifact,
)


@dataclass(frozen=True)
class AWSKMSKeyStatus:
    """Safe metadata returned by a successful AWS KMS key verification."""

    purpose: str
    key_reference: str
    key_arn: str


class AWSKMSKeyCustodian:
    """Use AWS KMS data-key and re-encryption APIs without raw master keys."""

    def __init__(self, client: Any):
        self._client = client

    def generate_data_key(
        self,
        *,
        key_reference: str,
        encryption_context: Mapping[str, str],
    ) -> GeneratedDataKey:
        try:
            response = self._client.generate_data_key(
                KeyId=key_reference,
                KeySpec="AES_256",
                EncryptionContext=dict(encryption_context),
            )
            plaintext = response["Plaintext"]
            encrypted = response["CiphertextBlob"]
            resolved_reference = response.get("KeyId", key_reference)
        except (KeyError, TypeError, ValueError):
            raise CustodyError("aws_kms_response_invalid") from None
        except Exception as error:  # AWS SDK errors are normalized below.
            raise _aws_custody_error(error, operation="generate") from None
        if not isinstance(plaintext, bytes) or not isinstance(encrypted, bytes) or not isinstance(resolved_reference, str):
            raise CustodyError("aws_kms_response_invalid")
        return GeneratedDataKey(
            key_reference=resolved_reference,
            plaintext=plaintext,
            encrypted=encrypted,
        )

    def decrypt_data_key(
        self,
        *,
        key_reference: str,
        encrypted: bytes,
        encryption_context: Mapping[str, str],
    ) -> bytes:
        try:
            response = self._client.decrypt(
                CiphertextBlob=encrypted,
                KeyId=key_reference,
                EncryptionContext=dict(encryption_context),
            )
            plaintext = response["Plaintext"]
        except (KeyError, TypeError, ValueError):
            raise CustodyError("aws_kms_response_invalid") from None
        except Exception as error:
            raise _aws_custody_error(error, operation="decrypt") from None
        if not isinstance(plaintext, bytes):
            raise CustodyError("aws_kms_response_invalid")
        return plaintext

    def rewrap_data_key(
        self,
        *,
        source_key_reference: str,
        destination_key_reference: str,
        encrypted: bytes,
        encryption_context: Mapping[str, str],
    ) -> RewrappedDataKey:
        try:
            response = self._client.re_encrypt(
                CiphertextBlob=encrypted,
                SourceKeyId=source_key_reference,
                SourceEncryptionContext=dict(encryption_context),
                DestinationEncryptionContext=dict(encryption_context),
                DestinationKeyId=destination_key_reference,
            )
            rewrapped = response["CiphertextBlob"]
            resolved_reference = response.get("KeyId", destination_key_reference)
            resolved_source_reference = response["SourceKeyId"]
        except (KeyError, TypeError, ValueError):
            raise CustodyError("aws_kms_response_invalid") from None
        except Exception as error:
            raise _aws_custody_error(error, operation="rewrap") from None
        if (
            not isinstance(rewrapped, bytes)
            or not isinstance(resolved_reference, str)
            or not isinstance(resolved_source_reference, str)
            or not resolved_source_reference
        ):
            raise CustodyError("aws_kms_response_invalid")
        return RewrappedDataKey(key_reference=resolved_reference, encrypted=rewrapped)

    def verify_customer_managed_key(self, *, purpose: str, key_reference: str) -> AWSKMSKeyStatus:
        """Verify one configured key is enabled for symmetric encryption.

        This verifies a customer-managed KMS key.  It does not claim that a
        particular customer supplied imported external key material; AWS KMS
        origin choices remain an explicit deployment decision.
        """

        try:
            response = self._client.describe_key(KeyId=key_reference)
            metadata = response["KeyMetadata"]
        except (KeyError, TypeError, ValueError):
            raise CustodyError("aws_kms_response_invalid") from None
        except Exception as error:
            raise _aws_custody_error(error, operation="describe") from None
        if not isinstance(metadata, Mapping):
            raise CustodyError("aws_kms_response_invalid")
        if metadata.get("KeyState") != "Enabled":
            raise CustodyError("aws_kms_key_unavailable")
        if metadata.get("KeyManager") != "CUSTOMER":
            raise CustodyError("aws_kms_key_not_customer_managed")
        if metadata.get("KeyUsage") != "ENCRYPT_DECRYPT":
            raise CustodyError("aws_kms_key_usage_invalid")
        if metadata.get("KeySpec", "SYMMETRIC_DEFAULT") != "SYMMETRIC_DEFAULT":
            raise CustodyError("aws_kms_key_spec_invalid")
        arn = metadata.get("Arn")
        if not isinstance(arn, str) or not arn:
            raise CustodyError("aws_kms_response_invalid")
        return AWSKMSKeyStatus(purpose=purpose, key_reference=key_reference, key_arn=arn)


class S3ObjectLockAuditAnchorSink:
    """Anchor a verified artifact as a compliance-retained, SSE-KMS S3 object."""

    backend = "aws-s3-object-lock"

    def __init__(
        self,
        client: Any,
        *,
        region: str,
        bucket: str,
        prefix: str,
        encryption_key_reference: str,
    ):
        self._client = client
        self._region = region
        self._bucket = bucket
        self._prefix = prefix.strip("/")
        self._encryption_key_reference = encryption_key_reference

    def verify_configuration(self) -> None:
        """Fail closed unless the target bucket can retain locked versions."""

        try:
            versioning = self._client.get_bucket_versioning(Bucket=self._bucket)
            lock = self._client.get_object_lock_configuration(Bucket=self._bucket)
            location = self._client.get_bucket_location(Bucket=self._bucket)
        except Exception as error:
            raise _aws_custody_error(error, operation="audit_anchor_verify") from None
        if not isinstance(versioning, Mapping) or versioning.get("Status") != "Enabled":
            raise CustodyError("aws_s3_versioning_required")
        if not isinstance(lock, Mapping):
            raise CustodyError("aws_s3_object_lock_required")
        configuration = lock.get("ObjectLockConfiguration")
        if not isinstance(configuration, Mapping) or configuration.get("ObjectLockEnabled") != "Enabled":
            raise CustodyError("aws_s3_object_lock_required")
        bucket_region = _bucket_region(location)
        if bucket_region != self._region:
            raise CustodyError("aws_s3_region_mismatch")

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
        self.verify_configuration()
        digest = hashlib.sha256(artifact).digest()
        object_key = self._object_key(organization_id=organization_id, artifact_id=artifact_id)
        request: dict[str, object] = {
            "Bucket": self._bucket,
            "Key": object_key,
            "Body": artifact,
            "ContentType": "application/json",
            "ChecksumAlgorithm": "SHA256",
            "ChecksumSHA256": _base64(digest),
            "ServerSideEncryption": "aws:kms",
            "SSEKMSKeyId": self._encryption_key_reference,
            "BucketKeyEnabled": True,
            "ObjectLockMode": "COMPLIANCE",
            "ObjectLockRetainUntilDate": retention_until.astimezone(timezone.utc),
            "IfNoneMatch": "*",
            "Metadata": {
                "hormuz-schema-id": "hormuz.audit-anchor",
                "hormuz-schema-version": "1",
                "hormuz-artifact-sha256": digest.hex(),
                "hormuz-head-digest": head_digest,
            },
        }
        if legal_hold:
            request["ObjectLockLegalHoldStatus"] = "ON"
        try:
            response = self._client.put_object(**request)
        except Exception as error:
            raise _aws_custody_error(error, operation="audit_anchor_write") from None
        version = response.get("VersionId") if isinstance(response, Mapping) else None
        if version is not None and not isinstance(version, str):
            raise CustodyError("aws_s3_response_invalid")
        return AuditAnchorReceipt(
            backend=self.backend,
            artifact_id=artifact_id,
            artifact_sha256=digest.hex(),
            head_digest=head_digest,
            object_version=version,
        )

    def _object_key(self, *, organization_id: str, artifact_id: str) -> str:
        # Do not put the tenant's raw identifier in an S3 key where broad
        # bucket-list access could expose it.  The receipt retains the random
        # artifact ID and digest needed to locate/verify the version.
        organization_hash = hashlib.sha256(organization_id.encode("utf-8")).hexdigest()[:24]
        prefix = f"{self._prefix}/" if self._prefix else ""
        return f"{prefix}v1/{organization_hash}/{artifact_id}.json"


def create_aws_kms_key_custodian(*, region: str) -> AWSKMSKeyCustodian:
    """Create an AWS KMS adapter using the ambient workload credential chain."""

    session = _aws_session()
    return AWSKMSKeyCustodian(session.client("kms", region_name=region))


def create_s3_object_lock_anchor_sink(
    *,
    region: str,
    bucket: str,
    prefix: str,
    encryption_key_reference: str,
) -> S3ObjectLockAuditAnchorSink:
    """Create the first certified immutable audit anchor implementation."""

    session = _aws_session()
    return S3ObjectLockAuditAnchorSink(
        session.client("s3", region_name=region),
        region=region,
        bucket=bucket,
        prefix=prefix,
        encryption_key_reference=encryption_key_reference,
    )


def verify_aws_kms_profile(
    custodian: AWSKMSKeyCustodian,
    key_references: Mapping[str, str],
    *,
    organization_id: str,
) -> tuple[AWSKMSKeyStatus, ...]:
    """Exercise every configured purpose against its customer-managed KMS key.

    The probe performs a transient GenerateDataKey/Decrypt round trip for the
    real tenant/purpose encryption context after checking metadata. It creates
    no Hormuz object or stored secret, but it deliberately validates the
    workload principal's actual KMS permissions instead of reporting a
    describe-only false positive.
    """

    if not key_references:
        raise CustodyError("aws_kms_keys_unconfigured")
    if len(set(key_references.values())) != len(key_references):
        raise CustodyError("aws_kms_key_purposes_not_separated")
    statuses: list[AWSKMSKeyStatus] = []
    for purpose, reference in sorted(key_references.items()):
        status = custodian.verify_customer_managed_key(purpose=purpose, key_reference=reference)
        generated = custodian.generate_data_key(
            key_reference=reference,
            encryption_context=encryption_context(organization_id=organization_id, purpose=purpose),
        )
        recovered = custodian.decrypt_data_key(
            key_reference=generated.key_reference,
            encrypted=generated.encrypted,
            encryption_context=encryption_context(organization_id=organization_id, purpose=purpose),
        )
        if not hmac.compare_digest(generated.plaintext, recovered):
            raise CustodyError("aws_kms_response_invalid")
        statuses.append(status)
    return tuple(statuses)


def _aws_session() -> Any:
    try:
        import boto3
    except ImportError:
        raise CustodyError("aws_sdk_unavailable") from None
    try:
        return boto3.session.Session()
    except Exception as error:
        raise _aws_custody_error(error, operation="session") from None


def _bucket_region(value: object) -> str:
    if not isinstance(value, Mapping):
        raise CustodyError("aws_s3_response_invalid")
    location = value.get("LocationConstraint")
    if location is None:
        return "us-east-1"
    if location == "EU":
        return "eu-west-1"
    if not isinstance(location, str) or not location:
        raise CustodyError("aws_s3_response_invalid")
    return location


def _base64(value: bytes) -> str:
    """Encode a binary AWS request value without using a weak digest path."""

    import base64

    return base64.b64encode(value).decode("ascii")


def _aws_custody_error(error: BaseException, *, operation: str) -> CustodyError:
    """Map AWS SDK details to a stable error without leaking account metadata."""

    response = getattr(error, "response", None)
    aws_code: object = None
    if isinstance(response, Mapping):
        detail = response.get("Error")
        if isinstance(detail, Mapping):
            aws_code = detail.get("Code")
    if aws_code in {
        "AccessDenied",
        "AccessDeniedException",
        "UnauthorizedOperation",
    }:
        return CustodyError("aws_custody_access_denied")
    if aws_code in {
        "NotFoundException",
        "KMSInvalidStateException",
        "DisabledException",
        "InvalidKeyUsageException",
    }:
        return CustodyError("aws_kms_key_unavailable")
    if operation == "audit_anchor_write" and aws_code in {"PreconditionFailed", "ConditionalRequestConflict"}:
        return CustodyError("audit_anchor_object_conflict")
    if operation.startswith("audit_anchor"):
        return CustodyError("aws_s3_unavailable")
    return CustodyError("aws_kms_unavailable")
