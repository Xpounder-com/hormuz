"""Explicit live conformance evidence for the first AWS custody backend.

This test never runs from ordinary CI. It requires an operator to opt in with
an ambient AWS workload identity and acknowledge that it writes one
COMPLIANCE-retained object to a customer-controlled test bucket.
"""

from __future__ import annotations

import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone

from hormuz.aws_custody import (
    AWSKMSKeyCustodian,
    S3ObjectLockAuditAnchorSink,
    verify_aws_kms_profile,
)
from hormuz.custody import (
    KEY_PURPOSE_DATA_ENCRYPTION,
    KEY_PURPOSE_PROVIDER_CREDENTIAL,
    EnvelopeCipher,
    build_audit_anchor_artifact,
    parse_audit_anchor_artifact,
    serialize_audit_anchor_artifact,
)


_OPT_IN_ENV = "HORMUZ_RUN_AWS_CUSTODY_CONFORMANCE"
_CONFIRMATION_ENV = "HORMUZ_AWS_CUSTODY_CONFIRMATION"
_CONFIRMATION_VALUE = "I_UNDERSTAND_OBJECT_LOCK_RETENTION"


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(f"{name} must be set when {_OPT_IN_ENV}=1")
    return value


def _conformance_event(organization_id: str) -> dict[str, object]:
    return {
        "schema_id": "hormuz.audit-event",
        "schema_version": 2,
        "event_type": "security.secret",
        "id": f"aws-conformance-{uuid.uuid4()}",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "organization_id": organization_id,
        "actor_id": "aws-conformance",
        "actor_name": "AWS conformance",
        "team_id": "platform",
        "team_name": "Platform",
        "identity_type": "service_account",
        "authentication_source": "aws-conformance",
        "client": "codex",
        "protocol": "openai",
        "requested_model": "conformance",
        "policy_version": "aws-conformance",
        "coverage": "gateway_captured_requests_only",
        "action": "redacted",
        "detection_count": 0,
        "rules": [],
    }


class AWSConformanceArtifactShapeTests(unittest.TestCase):
    """Keep the live test's metadata-only evidence shape locally executable."""

    def test_conformance_artifact_is_current_and_metadata_only(self) -> None:
        artifact = build_audit_anchor_artifact(
            [_conformance_event("aws-conformance-tenant")],
            organization_id="aws-conformance-tenant",
        )
        parsed = parse_audit_anchor_artifact(serialize_audit_anchor_artifact(artifact))
        self.assertEqual(parsed["event_count"], 1)
        self.assertNotIn("prompt", repr(parsed))
        self.assertNotIn("response", repr(parsed))


class AWSLiveCustodyConformanceTests(unittest.TestCase):
    """Run against a non-production, customer-controlled AWS environment."""

    @classmethod
    def setUpClass(cls) -> None:
        if os.environ.get(_OPT_IN_ENV) != "1":
            raise unittest.SkipTest(
                f"set {_OPT_IN_ENV}=1 with a dedicated Object Lock test bucket to run AWS conformance"
            )
        if os.environ.get(_CONFIRMATION_ENV) != _CONFIRMATION_VALUE:
            raise RuntimeError(
                f"set {_CONFIRMATION_ENV}={_CONFIRMATION_VALUE} to acknowledge the retained test object"
            )
        try:
            import boto3
        except ImportError as error:
            raise RuntimeError("install hormuz[aws] before running AWS custody conformance") from error

        cls.region = _required_environment("HORMUZ_AWS_CUSTODY_REGION")
        cls.bucket = _required_environment("HORMUZ_AWS_CUSTODY_BUCKET")
        cls.provider_key = _required_environment("HORMUZ_AWS_CUSTODY_PROVIDER_KEY")
        cls.data_key = _required_environment("HORMUZ_AWS_CUSTODY_DATA_KEY")
        cls.organization_id = os.environ.get("HORMUZ_AWS_CUSTODY_ORGANIZATION", "aws-conformance-tenant")
        retention_days = int(os.environ.get("HORMUZ_AWS_CUSTODY_RETENTION_DAYS", "1"))
        if retention_days < 1 or retention_days > 36500:
            raise RuntimeError("HORMUZ_AWS_CUSTODY_RETENTION_DAYS must be between 1 and 36500")
        cls.retention_days = retention_days
        cls.prefix = os.environ.get("HORMUZ_AWS_CUSTODY_PREFIX", "hormuz/conformance").strip("/")
        if not cls.prefix:
            raise RuntimeError("HORMUZ_AWS_CUSTODY_PREFIX must be non-empty")

        session = boto3.session.Session()
        cls.kms = AWSKMSKeyCustodian(session.client("kms", region_name=cls.region))
        cls.s3 = session.client("s3", region_name=cls.region)
        cls.sink = S3ObjectLockAuditAnchorSink(
            cls.s3,
            region=cls.region,
            bucket=cls.bucket,
            prefix=cls.prefix,
            encryption_key_reference=cls.data_key,
        )

    def test_customer_managed_keys_perform_tenant_bound_data_key_round_trip(self) -> None:
        statuses = verify_aws_kms_profile(
            self.kms,
            {
                KEY_PURPOSE_PROVIDER_CREDENTIAL: self.provider_key,
                KEY_PURPOSE_DATA_ENCRYPTION: self.data_key,
            },
            organization_id=self.organization_id,
        )
        self.assertEqual(
            {status.purpose for status in statuses},
            {KEY_PURPOSE_PROVIDER_CREDENTIAL, KEY_PURPOSE_DATA_ENCRYPTION},
        )

        cipher = EnvelopeCipher(self.kms)
        envelope = cipher.seal(
            b"hormuz-aws-conformance-value",
            organization_id=self.organization_id,
            purpose=KEY_PURPOSE_PROVIDER_CREDENTIAL,
            key_reference=self.provider_key,
        )
        self.assertEqual(cipher.unseal(envelope), b"hormuz-aws-conformance-value")
        rewrapped = cipher.rewrap(envelope, destination_key_reference=self.provider_key)
        self.assertEqual(cipher.unseal(rewrapped), b"hormuz-aws-conformance-value")

    def test_object_lock_anchor_is_retained_and_sse_kms_encrypted(self) -> None:
        self.sink.verify_configuration()
        artifact = build_audit_anchor_artifact(
            [_conformance_event(self.organization_id)],
            organization_id=self.organization_id,
        )
        encoded = serialize_audit_anchor_artifact(artifact)
        retention_until = datetime.now(timezone.utc) + timedelta(days=self.retention_days)
        receipt = self.sink.anchor(
            encoded,
            artifact_id=artifact["artifact_id"],  # type: ignore[arg-type]
            organization_id=self.organization_id,
            head_digest=artifact["head_digest"],  # type: ignore[arg-type]
            retention_until=retention_until,
            legal_hold=False,
        )
        self.assertIsNotNone(receipt.object_version)
        object_key = self.sink._object_key(  # noqa: SLF001 - direct retained-object verification is intentional.
            organization_id=self.organization_id,
            artifact_id=receipt.artifact_id,
        )
        head = self.s3.head_object(
            Bucket=self.bucket,
            Key=object_key,
            VersionId=receipt.object_version,
        )
        self.assertEqual(head.get("ObjectLockMode"), "COMPLIANCE")
        self.assertEqual(head.get("ServerSideEncryption"), "aws:kms")
        self.assertTrue(head.get("SSEKMSKeyId"))
        self.assertGreaterEqual(
            head["ObjectLockRetainUntilDate"],
            retention_until - timedelta(seconds=5),
        )
        metadata = head.get("Metadata", {})
        self.assertEqual(metadata.get("hormuz-artifact-sha256"), receipt.artifact_sha256)
        self.assertEqual(metadata.get("hormuz-head-digest"), receipt.head_digest)


if __name__ == "__main__":
    unittest.main()
