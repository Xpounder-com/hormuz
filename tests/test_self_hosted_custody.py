from __future__ import annotations

import base64
import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping
from unittest.mock import patch
from urllib.parse import unquote, urlparse
from urllib.request import Request

from hormuz.config import ConfigError, GatewayConfig
from hormuz.cli import _custody_verify
from hormuz.custody import (
    AuditAnchorReceipt,
    CustodyError,
    DataKeyProvider,
    EnvelopeCipher,
    GeneratedDataKey,
    RewrappedDataKey,
    build_audit_anchor_artifact,
    parse_envelope,
    serialize_audit_anchor_artifact,
)
from hormuz.self_hosted_custody import EncryptedS3ObjectLockAuditAnchorSink, create_s3_compatible_object_lock_anchor_sink
from hormuz.custody_runtime import create_audit_anchor_sink, create_data_key_provider
from hormuz.openbao_custody import OpenBaoTransitDataKeyProvider


class _MemoryDataKeyProvider(DataKeyProvider):
    def __init__(self) -> None:
        self._keys: dict[bytes, tuple[bytes, str, dict[str, str]]] = {}
        self._next = 0

    def generate_data_key(
        self,
        *,
        key_reference: str,
        encryption_context: Mapping[str, str],
    ) -> GeneratedDataKey:
        self._next += 1
        encrypted = f"wrapped:{self._next}".encode("ascii")
        plaintext = bytes([65 + self._next]) * 32
        self._keys[encrypted] = (plaintext, key_reference, dict(encryption_context))
        return GeneratedDataKey(key_reference=key_reference, plaintext=plaintext, encrypted=encrypted)

    def decrypt_data_key(
        self,
        *,
        key_reference: str,
        encrypted: bytes,
        encryption_context: Mapping[str, str],
    ) -> bytes:
        plaintext, expected_reference, expected_context = self._keys[encrypted]
        if expected_reference != key_reference or expected_context != dict(encryption_context):
            raise CustodyError("memory_context_mismatch")
        return plaintext

    def rewrap_data_key(
        self,
        *,
        source_key_reference: str,
        destination_key_reference: str,
        encrypted: bytes,
        encryption_context: Mapping[str, str],
    ) -> RewrappedDataKey:
        plaintext = self.decrypt_data_key(
            key_reference=source_key_reference,
            encrypted=encrypted,
            encryption_context=encryption_context,
        )
        self._next += 1
        rewrapped = f"wrapped:{self._next}".encode("ascii")
        self._keys[rewrapped] = (plaintext, destination_key_reference, dict(encryption_context))
        return RewrappedDataKey(key_reference=destination_key_reference, encrypted=rewrapped)


class _FakeS3Failure(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class _Body:
    def __init__(self, value: bytes) -> None:
        self._value = value

    def read(self, amount: int = -1) -> bytes:
        return self._value if amount < 0 else self._value[:amount]


class _FakeS3Client:
    def __init__(self) -> None:
        self.puts: list[dict[str, object]] = []
        self.objects: dict[tuple[str, str], dict[str, object]] = {}
        self.versioning: object = {"Status": "Enabled"}
        self.lock: object = {"ObjectLockConfiguration": {"ObjectLockEnabled": "Enabled"}}
        self.location: object = {"LocationConstraint": "us-east-1"}
        self.failure: str | None = None

    def get_bucket_versioning(self, **kwargs: object) -> object:
        return self.versioning

    def get_object_lock_configuration(self, **kwargs: object) -> object:
        return self.lock

    def get_bucket_location(self, **kwargs: object) -> object:
        return self.location

    def put_object(self, **kwargs: object) -> dict[str, object]:
        if self.failure is not None:
            raise _FakeS3Failure(self.failure)
        self.puts.append(copy.deepcopy(kwargs))
        version = "version-1"
        key = kwargs["Key"]
        body = kwargs["Body"]
        metadata = kwargs["Metadata"]
        assert isinstance(key, str)
        assert isinstance(body, bytes)
        assert isinstance(metadata, dict)
        self.objects[(key, version)] = {"Body": body, "Metadata": copy.deepcopy(metadata)}
        return {"VersionId": version}

    def get_object(self, **kwargs: object) -> dict[str, object]:
        if self.failure is not None:
            raise _FakeS3Failure(self.failure)
        key = kwargs["Key"]
        version = kwargs["VersionId"]
        assert isinstance(key, str)
        assert isinstance(version, str)
        record = self.objects[(key, version)]
        body = record["Body"]
        assert isinstance(body, bytes)
        return {"Body": _Body(body), "Metadata": copy.deepcopy(record["Metadata"])}


class _TransitVerificationResponse:
    def __init__(self, payload: object) -> None:
        self._payload = json.dumps(payload, sort_keys=True).encode("utf-8")

    def read(self, amount: int = -1) -> bytes:
        return self._payload if amount < 0 else self._payload[:amount]

    def close(self) -> None:
        return None


class _TransitVerificationTransport:
    """Small protocol-shaped Transit fake used only for CLI verification."""

    def __init__(self) -> None:
        self.operations: list[str] = []

    def __call__(self, request: Request, timeout_seconds: float) -> _TransitVerificationResponse:
        del timeout_seconds
        parts = [unquote(part) for part in urlparse(request.full_url).path.split("/") if part]
        operation = "/".join(parts[2:-1])
        self.operations.append(operation)
        if operation == "datakey/plaintext":
            return _TransitVerificationResponse(
                {
                    "data": {
                        "plaintext": base64.b64encode(b"T" * 32).decode("ascii"),
                        "ciphertext": f"vault:v1:{len(self.operations)}",
                    }
                }
            )
        if operation == "decrypt":
            return _TransitVerificationResponse(
                {"data": {"plaintext": base64.b64encode(b"T" * 32).decode("ascii")}}
            )
        raise AssertionError(f"unexpected Transit operation: {operation}")


def _usage_event() -> dict[str, object]:
    return {
        "schema_id": "hormuz.audit-event",
        "schema_version": 2,
        "event_type": "usage",
        "id": "event-1",
        "occurred_at": "2026-08-21T00:00:00+00:00",
        "organization_id": "xpounder",
        "actor_id": "alice",
        "actor_name": "Alice Example",
        "team_id": "engineering",
        "team_name": "Engineering",
        "identity_type": "human",
        "authentication_source": "oidc:https://id.example",
        "client": "codex",
        "protocol": "openai",
        "requested_model": "gpt-fast",
        "resolved_alias": "gpt-fast",
        "routed_model": "gpt-provider-fast",
        "provider_reported_model": "gpt-provider-fast",
        "policy_version": "policy-v1",
        "policy_action": "allowed",
        "status": "succeeded",
        "input_tokens": 1,
        "output_tokens": 2,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "reasoning_tokens": 0,
        "cost_microusd": 3,
        "cost_basis": "configured_rate_card_estimate",
        "allocation_basis": "direct_gateway_request",
        "coverage": "gateway_captured_requests_only",
        "provider_request_id": "request-1",
        "redaction_count": 0,
        "redaction_rules": [],
    }


class SelfHostedAuditAnchorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = _MemoryDataKeyProvider()
        self.client = _FakeS3Client()
        self.sink = EncryptedS3ObjectLockAuditAnchorSink(
            self.client,
            region="us-east-1",
            bucket="hormuz-audit-bucket",
            prefix="immutable/audit",
            key_provider=self.provider,
            encryption_key_reference="audit-data",
        )

    def test_anchor_encrypts_the_complete_artifact_before_object_lock_egress(self) -> None:
        artifact = build_audit_anchor_artifact(
            [_usage_event()],
            organization_id="xpounder",
            created_at=datetime(2026, 8, 21, tzinfo=timezone.utc),
            artifact_id="16ab5178-397e-4624-9431-1a40fbf8f09f",
        )
        encoded = serialize_audit_anchor_artifact(artifact)
        receipt = self.sink.anchor(
            encoded,
            artifact_id=artifact["artifact_id"],  # type: ignore[arg-type]
            organization_id="xpounder",
            head_digest=artifact["head_digest"],  # type: ignore[arg-type]
            retention_until=datetime.now(timezone.utc) + timedelta(days=30),
            legal_hold=True,
        )
        self.assertEqual(receipt.backend, "s3-compatible-object-lock")
        self.assertEqual(receipt.object_version, "version-1")
        request = self.client.puts[0]
        payload = request["Body"]
        self.assertIsInstance(payload, bytes)
        self.assertNotIn(b"Alice Example", payload)
        self.assertNotIn(b"event-1", payload)
        self.assertEqual(EnvelopeCipher(self.provider).unseal(parse_envelope(payload)), encoded)
        self.assertEqual(request["ObjectLockMode"], "COMPLIANCE")
        self.assertEqual(request["ObjectLockLegalHoldStatus"], "ON")
        self.assertEqual(request["IfNoneMatch"], "*")
        self.assertNotIn("ServerSideEncryption", request)
        self.assertNotIn("xpounder", request["Key"])  # type: ignore[operator]
        metadata = request["Metadata"]  # type: ignore[assignment]
        self.assertEqual(metadata["hormuz-artifact-sha256"], receipt.artifact_sha256)  # type: ignore[index]
        self.assertEqual(metadata["hormuz-head-digest"], artifact["head_digest"])  # type: ignore[index]

    def test_configuration_and_storage_fail_closed(self) -> None:
        self.client.lock = {"ObjectLockConfiguration": {"ObjectLockEnabled": "Disabled"}}
        with self.assertRaises(CustodyError) as raised:
            self.sink.verify_configuration()
        self.assertEqual(raised.exception.code, "s3_object_lock_required")

        self.client.lock = {"ObjectLockConfiguration": {"ObjectLockEnabled": "Enabled"}}
        self.client.location = {"LocationConstraint": "eu-west-1"}
        with self.assertRaises(CustodyError) as raised:
            self.sink.verify_configuration()
        self.assertEqual(raised.exception.code, "s3_object_lock_region_mismatch")

        self.client.location = {"LocationConstraint": "us-east-1"}
        self.client.failure = "PreconditionFailed"
        artifact = build_audit_anchor_artifact([_usage_event()], organization_id="xpounder")
        with self.assertRaises(CustodyError) as raised:
            self.sink.anchor(
                serialize_audit_anchor_artifact(artifact),
                artifact_id=artifact["artifact_id"],  # type: ignore[arg-type]
                organization_id="xpounder",
                head_digest=artifact["head_digest"],  # type: ignore[arg-type]
                retention_until=datetime.now(timezone.utc) + timedelta(days=30),
                legal_hold=False,
            )
        self.assertEqual(raised.exception.code, "audit_anchor_object_conflict")

    def test_recovery_reads_one_exact_version_through_a_fresh_sink(self) -> None:
        artifact = build_audit_anchor_artifact(
            [_usage_event()],
            organization_id="xpounder",
            created_at=datetime(2026, 8, 21, tzinfo=timezone.utc),
            artifact_id="16ab5178-397e-4624-9431-1a40fbf8f09f",
        )
        encoded = serialize_audit_anchor_artifact(artifact)
        receipt = self.sink.anchor(
            encoded,
            artifact_id=artifact["artifact_id"],  # type: ignore[arg-type]
            organization_id="xpounder",
            head_digest=artifact["head_digest"],  # type: ignore[arg-type]
            retention_until=datetime.now(timezone.utc) + timedelta(days=30),
            legal_hold=False,
        )
        recovery_sink = EncryptedS3ObjectLockAuditAnchorSink(
            self.client,
            region="us-east-1",
            bucket="hormuz-audit-bucket",
            prefix="immutable/audit",
            key_provider=self.provider,
            encryption_key_reference="audit-data",
        )

        self.assertEqual(recovery_sink.recover(receipt, organization_id="xpounder"), encoded)

    def test_recovery_rejects_corrupted_payload_or_receipt(self) -> None:
        artifact = build_audit_anchor_artifact([_usage_event()], organization_id="xpounder")
        receipt = self.sink.anchor(
            serialize_audit_anchor_artifact(artifact),
            artifact_id=artifact["artifact_id"],  # type: ignore[arg-type]
            organization_id="xpounder",
            head_digest=artifact["head_digest"],  # type: ignore[arg-type]
            retention_until=datetime.now(timezone.utc) + timedelta(days=30),
            legal_hold=False,
        )
        key = self.sink._object_key(  # noqa: SLF001 - test controls the retained fake version.
            organization_id="xpounder",
            artifact_id=receipt.artifact_id,
        )
        record = self.client.objects[(key, "version-1")]
        payload = record["Body"]
        assert isinstance(payload, bytes)
        record["Body"] = payload[:-1] + bytes([payload[-1] ^ 1])

        with self.assertRaises(CustodyError):
            self.sink.recover(receipt, organization_id="xpounder")

        malformed_receipt = AuditAnchorReceipt(
            backend=receipt.backend,
            artifact_id=receipt.artifact_id,
            artifact_sha256=receipt.artifact_sha256,
            head_digest=receipt.head_digest,
            object_version=None,
        )
        with self.assertRaisesRegex(CustodyError, "audit_anchor_recovery_receipt_invalid"):
            self.sink.recover(malformed_receipt, organization_id="xpounder")

    def test_factory_refuses_implicit_or_missing_storage_credentials(self) -> None:
        with self.assertRaises(CustodyError) as raised:
            create_s3_compatible_object_lock_anchor_sink(
                endpoint_url="http://127.0.0.1:9000",
                region="us-east-1",
                bucket="hormuz-audit-bucket",
                prefix="audit",
                access_key="",
                secret_key="",
                key_provider=self.provider,
                encryption_key_reference="audit-data",
            )
        self.assertEqual(raised.exception.code, "s3_object_lock_credentials_unavailable")

        with self.assertRaises(CustodyError) as raised:
            create_s3_compatible_object_lock_anchor_sink(
                endpoint_url="http://object-lock.internal.example",
                region="us-east-1",
                bucket="hormuz-audit-bucket",
                prefix="audit",
                access_key="dedicated-audit-user",
                secret_key="dedicated-audit-secret",
                key_provider=self.provider,
                encryption_key_reference="audit-data",
            )
        self.assertEqual(raised.exception.code, "s3_object_lock_endpoint_invalid")


class SelfHostedCustodyConfigurationTests(unittest.TestCase):
    def _configuration(self) -> dict[str, object]:
        raw = json.loads((Path(__file__).parents[1] / "config.example.json").read_text(encoding="utf-8"))
        raw["key_custody"] = {
            "backend": "openbao-transit",
            "endpoint_url": "http://127.0.0.1:8200",
            "token_env": "HORMUZ_OPENBAO_TOKEN",
            "transit_mount": "transit",
            "key_references": {
                "provider_credential": "hormuz-provider",
                "data_encryption": "hormuz-audit",
            },
        }
        raw["audit_anchor"] = {
            "backend": "s3-compatible-object-lock",
            "endpoint_url": "http://127.0.0.1:9000",
            "region": "us-east-1",
            "bucket": "hormuz-immutable-audit",
            "prefix": "hormuz/audit",
            "retention_days": 365,
            "legal_hold": True,
            "access_key_env": "HORMUZ_AUDIT_S3_ACCESS_KEY",
            "secret_key_env": "HORMUZ_AUDIT_S3_SECRET_KEY",
        }
        return raw

    def _load(self, raw: dict[str, object]) -> GatewayConfig:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "hormuz.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            return GatewayConfig.load(
                path,
                environ={"HORMUZ_TOKEN": "identity-token-with-sufficient-length"},
            )

    def test_openbao_and_s3_object_lock_profile_uses_named_environment_sources_only(self) -> None:
        config = self._load(self._configuration())
        self.assertEqual(config.key_custody.backend, "openbao-transit")  # type: ignore[union-attr]
        self.assertEqual(config.audit_anchor.backend, "s3-compatible-object-lock")  # type: ignore[union-attr]
        self.assertEqual(config.key_custody.token_env, "HORMUZ_OPENBAO_TOKEN")  # type: ignore[union-attr]
        self.assertEqual(config.audit_anchor.access_key_env, "HORMUZ_AUDIT_S3_ACCESS_KEY")  # type: ignore[union-attr]

        provider = create_data_key_provider(
            config,
            environ={"HORMUZ_OPENBAO_TOKEN": "one-time-transit-token"},
        )
        self.assertIsInstance(provider, OpenBaoTransitDataKeyProvider)
        with patch("hormuz.custody_runtime.create_s3_compatible_object_lock_anchor_sink") as factory:
            create_audit_anchor_sink(
                config,
                environ={
                    "HORMUZ_OPENBAO_TOKEN": "one-time-transit-token",
                    "HORMUZ_AUDIT_S3_ACCESS_KEY": "dedicated-audit-user",
                    "HORMUZ_AUDIT_S3_SECRET_KEY": "dedicated-audit-secret",
                },
            )
        kwargs = factory.call_args.kwargs
        self.assertEqual(kwargs["endpoint_url"], "http://127.0.0.1:9000")
        self.assertEqual(kwargs["access_key"], "dedicated-audit-user")
        self.assertEqual(kwargs["secret_key"], "dedicated-audit-secret")
        self.assertIsInstance(kwargs["key_provider"], OpenBaoTransitDataKeyProvider)
        self.assertNotIn("one-time-transit-token", repr(config))
        self.assertNotIn("dedicated-audit-secret", repr(config))

    def test_openbao_and_s3_profile_rejects_unsafe_or_mismatched_configuration(self) -> None:
        raw = self._configuration()
        raw["key_custody"]["endpoint_url"] = "http://openbao.internal:8200"  # type: ignore[index]
        with self.assertRaises(ConfigError) as raised:
            self._load(raw)
        self.assertIn("requires HTTPS", str(raised.exception))

        raw = self._configuration()
        raw["key_custody"]["key_references"]["data_encryption"] = "audit/current"  # type: ignore[index]
        with self.assertRaises(ConfigError) as raised:
            self._load(raw)
        self.assertIn("safe OpenBao Transit key name", str(raised.exception))

        raw = self._configuration()
        raw["key_custody"]["token"] = "never-in-config"  # type: ignore[index]
        with self.assertRaises(ConfigError) as raised:
            self._load(raw)
        self.assertEqual(str(raised.exception), "configuration_unsupported_fields")

        raw = self._configuration()
        raw["audit_anchor"].pop("endpoint_url")  # type: ignore[index]
        raw["audit_anchor"].pop("access_key_env")  # type: ignore[index]
        raw["audit_anchor"].pop("secret_key_env")  # type: ignore[index]
        raw["audit_anchor"]["backend"] = "aws-s3-object-lock"  # type: ignore[index]
        with self.assertRaises(ConfigError) as raised:
            self._load(raw)
        self.assertIn("requires key_custody.backend aws-kms", str(raised.exception))

    def test_runtime_fails_closed_when_a_named_secret_is_missing(self) -> None:
        config = self._load(self._configuration())
        with self.assertRaises(CustodyError) as raised:
            create_data_key_provider(config, environ={})
        self.assertEqual(raised.exception.code, "openbao_custody_token_unavailable")

        with self.assertRaises(CustodyError) as raised:
            create_audit_anchor_sink(config, environ={"HORMUZ_OPENBAO_TOKEN": "token"})
        self.assertEqual(raised.exception.code, "s3_object_lock_credentials_unavailable")

    def test_custody_verify_exercises_the_self_hosted_profile_without_an_audit_write(self) -> None:
        config = self._load(self._configuration())
        transit = _TransitVerificationTransport()
        provider = OpenBaoTransitDataKeyProvider(
            endpoint_url="http://127.0.0.1:8200",
            token="one-time-transit-token",
            transport=transit,
        )
        storage = _FakeS3Client()
        sink = EncryptedS3ObjectLockAuditAnchorSink(
            storage,
            region="us-east-1",
            bucket="hormuz-immutable-audit",
            prefix="hormuz/audit",
            key_provider=provider,
            encryption_key_reference="hormuz-audit",
        )
        output = io.StringIO()
        with (
            patch("hormuz.cli.create_data_key_provider", return_value=provider),
            patch("hormuz.cli.create_audit_anchor_sink", return_value=sink),
            redirect_stdout(output),
        ):
            self.assertEqual(_custody_verify(config), 0)
        self.assertIn("key_custody=openbao-transit", output.getvalue())
        self.assertIn("audit_anchor=s3-compatible-object-lock", output.getvalue())
        self.assertEqual(transit.operations, ["datakey/plaintext", "decrypt", "datakey/plaintext", "decrypt"])
        self.assertEqual(storage.puts, [])


if __name__ == "__main__":
    unittest.main()
