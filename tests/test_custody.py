from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping
from unittest.mock import patch

from hormuz.aws_custody import AWSKMSKeyCustodian, S3ObjectLockAuditAnchorSink, verify_aws_kms_profile
from hormuz.config import ConfigError, GatewayConfig
from hormuz.custody import (
    KEY_PURPOSE_DATA_ENCRYPTION,
    KEY_PURPOSE_PROVIDER_CREDENTIAL,
    AuditAnchorReceipt,
    CustodyError,
    DataKeyProvider,
    EnvelopeCipher,
    GeneratedDataKey,
    RewrappedDataKey,
    build_audit_anchor_artifact,
    parse_audit_anchor_artifact,
    parse_envelope,
    serialize_audit_anchor_artifact,
    serialize_envelope,
    verify_audit_anchor_artifact,
)
from hormuz.custody_runtime import read_envelope_file, resolve_upstream_credentials, write_envelope_file


class _MemoryDataKeyProvider(DataKeyProvider):
    def __init__(self) -> None:
        self._keys: dict[bytes, tuple[bytes, str, dict[str, str]]] = {}
        self.calls: list[tuple[str, str]] = []

    def generate_data_key(
        self,
        *,
        key_reference: str,
        encryption_context: Mapping[str, str],
    ) -> GeneratedDataKey:
        self.calls.append(("generate", key_reference))
        plaintext = b"K" * 32
        encrypted = f"wrapped:{len(self._keys)}".encode("ascii")
        self._keys[encrypted] = (plaintext, key_reference, dict(encryption_context))
        return GeneratedDataKey(key_reference=key_reference, plaintext=plaintext, encrypted=encrypted)

    def decrypt_data_key(
        self,
        *,
        key_reference: str,
        encrypted: bytes,
        encryption_context: Mapping[str, str],
    ) -> bytes:
        self.calls.append(("decrypt", key_reference))
        plaintext, expected_key, expected_context = self._keys[encrypted]
        if expected_key != key_reference or expected_context != dict(encryption_context):
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
        self.calls.append(("rewrap", destination_key_reference))
        plaintext, expected_key, expected_context = self._keys[encrypted]
        if expected_key != source_key_reference or expected_context != dict(encryption_context):
            raise CustodyError("memory_context_mismatch")
        result = encrypted + b":rewrapped"
        self._keys[result] = (plaintext, destination_key_reference, dict(encryption_context))
        return RewrappedDataKey(key_reference=destination_key_reference, encrypted=result)


class _FakeAWSFailure(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class _FakeKMSClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.failure: str | None = None

    def _respond(self, name: str, value: dict[str, object]) -> dict[str, object]:
        if self.failure:
            raise _FakeAWSFailure(self.failure)
        self.calls.append((name, value))
        return value

    def generate_data_key(self, **kwargs: object) -> dict[str, object]:
        self._respond("generate_data_key", dict(kwargs))
        return {
            "Plaintext": b"D" * 32,
            "CiphertextBlob": b"wrapped-key",
            "KeyId": "arn:aws:kms:us-east-1:111122223333:key/current",
        }

    def decrypt(self, **kwargs: object) -> dict[str, object]:
        self._respond("decrypt", dict(kwargs))
        return {"Plaintext": b"D" * 32}

    def re_encrypt(self, **kwargs: object) -> dict[str, object]:
        self._respond("re_encrypt", dict(kwargs))
        return {
            "CiphertextBlob": b"rewrapped-key",
            "KeyId": "arn:aws:kms:us-east-1:111122223333:key/next",
            "SourceKeyId": "arn:aws:kms:us-east-1:111122223333:key/current",
        }

    def describe_key(self, **kwargs: object) -> dict[str, object]:
        self._respond("describe_key", dict(kwargs))
        return {
            "KeyMetadata": {
                "KeyState": "Enabled",
                "KeyManager": "CUSTOMER",
                "KeyUsage": "ENCRYPT_DECRYPT",
                "KeySpec": "SYMMETRIC_DEFAULT",
                "Arn": "arn:aws:kms:us-east-1:111122223333:key/current",
            }
        }


class _FakeS3Client:
    def __init__(self) -> None:
        self.puts: list[dict[str, object]] = []
        self.versioning: object = {"Status": "Enabled"}
        self.lock: object = {"ObjectLockConfiguration": {"ObjectLockEnabled": "Enabled"}}
        self.location: object = {"LocationConstraint": "us-east-1"}

    def get_bucket_versioning(self, **kwargs: object) -> object:
        return self.versioning

    def get_object_lock_configuration(self, **kwargs: object) -> object:
        return self.lock

    def get_bucket_location(self, **kwargs: object) -> object:
        return self.location

    def put_object(self, **kwargs: object) -> dict[str, object]:
        self.puts.append(dict(kwargs))
        return {"VersionId": "version-1"}


def _usage_event(event_id: str = "event-1", *, organization_id: str = "xpounder") -> dict[str, object]:
    return {
        "schema_id": "hormuz.audit-event",
        "schema_version": 2,
        "event_type": "usage",
        "id": event_id,
        "occurred_at": "2026-08-21T00:00:00+00:00",
        "organization_id": organization_id,
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


class EnvelopeCipherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = _MemoryDataKeyProvider()
        self.cipher = EnvelopeCipher(self.provider)

    def test_round_trip_serialization_never_contains_plaintext(self) -> None:
        envelope = self.cipher.seal(
            b"provider-secret-value",
            organization_id="xpounder",
            purpose=KEY_PURPOSE_PROVIDER_CREDENTIAL,
            key_reference="kms-provider-current",
        )
        encoded = serialize_envelope(envelope)
        self.assertNotIn(b"provider-secret-value", encoded)
        parsed = parse_envelope(encoded)
        self.assertEqual(self.cipher.unseal(parsed), b"provider-secret-value")
        self.assertEqual(self.provider.calls, [("generate", "kms-provider-current"), ("decrypt", "kms-provider-current")])

    def test_tampering_and_context_swaps_fail_closed(self) -> None:
        envelope = self.cipher.seal(
            b"provider-secret-value",
            organization_id="xpounder",
            purpose=KEY_PURPOSE_PROVIDER_CREDENTIAL,
            key_reference="kms-provider-current",
        )
        modified = envelope.as_dict()
        modified["purpose"] = KEY_PURPOSE_DATA_ENCRYPTION
        with self.assertRaises(CustodyError) as raised:
            self.cipher.unseal(parse_envelope(json.dumps(modified)))
        self.assertEqual(raised.exception.code, "memory_context_mismatch")

        corrupt = envelope.as_dict()
        corrupt["ciphertext"] = "AAAAAAAAAAAAAAAAAAAAAA=="
        with self.assertRaises(CustodyError) as raised:
            self.cipher.unseal(parse_envelope(json.dumps(corrupt)))
        self.assertEqual(raised.exception.code, "encrypted_envelope_integrity_invalid")

    def test_rewrap_never_requests_plaintext_secret_or_data_key(self) -> None:
        envelope = self.cipher.seal(
            b"provider-secret-value",
            organization_id="xpounder",
            purpose=KEY_PURPOSE_PROVIDER_CREDENTIAL,
            key_reference="kms-provider-current",
        )
        rewrapped = self.cipher.rewrap(envelope, destination_key_reference="kms-provider-next")
        self.assertEqual(rewrapped.key_reference, "kms-provider-next")
        self.assertEqual(self.cipher.unseal(rewrapped), b"provider-secret-value")
        self.assertEqual(
            self.provider.calls,
            [
                ("generate", "kms-provider-current"),
                ("rewrap", "kms-provider-next"),
                ("decrypt", "kms-provider-next"),
            ],
        )

    def test_duplicate_envelope_json_members_fail_closed(self) -> None:
        envelope = self.cipher.seal(
            b"provider-secret-value",
            organization_id="xpounder",
            purpose=KEY_PURPOSE_PROVIDER_CREDENTIAL,
            key_reference="kms-provider-current",
        )
        encoded = serialize_envelope(envelope).decode("utf-8")
        duplicate = encoded[:-1] + ',"purpose":"data_encryption"}'
        with self.assertRaises(CustodyError) as raised:
            parse_envelope(duplicate)
        self.assertEqual(raised.exception.code, "encrypted_envelope_malformed")


class AuditAnchorTests(unittest.TestCase):
    def _artifact(self) -> dict[str, object]:
        return build_audit_anchor_artifact(
            [_usage_event("event-1"), _usage_event("event-2")],
            organization_id="xpounder",
            created_at=datetime(2026, 8, 21, tzinfo=timezone.utc),
            artifact_id="16ab5178-397e-4624-9431-1a40fbf8f09f",
        )

    def test_round_trip_is_canonical_and_metadata_only(self) -> None:
        artifact = self._artifact()
        encoded = serialize_audit_anchor_artifact(artifact)
        self.assertEqual(parse_audit_anchor_artifact(encoded), artifact)
        self.assertNotIn(b"prompt", encoded)
        self.assertNotIn(b"response", encoded)

    def test_altered_deleted_reordered_and_duplicate_entries_fail(self) -> None:
        altered = self._artifact()
        altered["entries"][0]["event"]["input_tokens"] = 99  # type: ignore[index]
        self._assert_anchor_error(altered, "audit_anchor_digest_invalid")

        deleted = self._artifact()
        deleted["entries"].pop()  # type: ignore[index]
        self._assert_anchor_error(deleted, "audit_anchor_count_invalid")

        reordered = self._artifact()
        reordered["entries"].reverse()  # type: ignore[index]
        self._assert_anchor_error(reordered, "audit_anchor_sequence_invalid")

        duplicate = self._artifact()
        duplicate["entries"][1]["event"] = copy.deepcopy(duplicate["entries"][0]["event"])  # type: ignore[index]
        self._assert_anchor_error(duplicate, "audit_anchor_event_duplicate")

    def test_cross_tenant_and_legacy_event_rejected_before_anchor(self) -> None:
        with self.assertRaises(CustodyError) as raised:
            build_audit_anchor_artifact(
                [_usage_event(organization_id="other")],
                organization_id="xpounder",
            )
        self.assertEqual(raised.exception.code, "audit_anchor_tenant_mismatch")

        legacy = {
            "schema_version": 1,
            "event_type": "usage",
            "id": "legacy-event",
            "occurred_at": "2026-08-21T00:00:00+00:00",
            "actor_id": "alice",
            "actor_name": "Alice Example",
            "team_id": "engineering",
            "team_name": "Engineering",
            "client": "codex",
            "protocol": "openai",
            "requested_model": "gpt-fast",
            "resolved_alias": "gpt-fast",
            "upstream_model": "gpt-provider-fast",
            "policy_action": "allowed",
            "status": "succeeded",
            "input_tokens": 1,
            "output_tokens": 2,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "reasoning_tokens": 0,
            "cost_microusd": 3,
            "provider_request_id": "request-1",
            "redaction_count": 0,
            "redaction_rules": [],
        }
        with self.assertRaises(CustodyError) as raised:
            build_audit_anchor_artifact([legacy], organization_id="xpounder")
        self.assertEqual(raised.exception.code, "audit_anchor_event_schema_unsupported")

    def _assert_anchor_error(self, artifact: dict[str, object], expected: str) -> None:
        with self.assertRaises(CustodyError) as raised:
            verify_audit_anchor_artifact(artifact)
        self.assertEqual(raised.exception.code, expected)


class AWSCustodyAdapterTests(unittest.TestCase):
    def test_kms_adapter_uses_bound_context_and_customer_key_verification(self) -> None:
        client = _FakeKMSClient()
        custodian = AWSKMSKeyCustodian(client)
        cipher = EnvelopeCipher(custodian)
        envelope = cipher.seal(
            b"provider-secret-value",
            organization_id="xpounder",
            purpose=KEY_PURPOSE_PROVIDER_CREDENTIAL,
            key_reference="alias/hormuz-provider-current",
        )
        self.assertEqual(cipher.unseal(envelope), b"provider-secret-value")
        rewrapped = cipher.rewrap(envelope, destination_key_reference="alias/hormuz-provider-next")
        self.assertEqual(rewrapped.key_reference, "arn:aws:kms:us-east-1:111122223333:key/next")
        statuses = verify_aws_kms_profile(
            custodian,
            {
                KEY_PURPOSE_PROVIDER_CREDENTIAL: "alias/hormuz-provider-current",
                KEY_PURPOSE_DATA_ENCRYPTION: "alias/hormuz-data-current",
            },
            organization_id="xpounder",
        )
        self.assertEqual(len(statuses), 2)
        generate = client.calls[0][1]
        self.assertEqual(generate["KeySpec"], "AES_256")
        self.assertEqual(
            generate["EncryptionContext"],
            {
                "hormuz:schema": "hormuz.encrypted-envelope",
                "hormuz:organization_sha256": "20c0b98845690961f6b248aacbba7ec73e7e780b883eef898632d576d75c525f",
                "hormuz:purpose": "provider_credential",
            },
        )
        self.assertEqual(client.calls[2][0], "re_encrypt")
        self.assertEqual(client.calls[2][1]["SourceKeyId"], envelope.key_reference)
        self.assertEqual(
            len([name for name, _ in client.calls if name == "generate_data_key"]),
            3,
        )
        self.assertEqual(
            len([name for name, _ in client.calls if name == "decrypt"]),
            3,
        )
        self.assertNotIn(b"provider-secret-value", repr(client.calls).encode("utf-8"))

    def test_kms_failure_is_content_free(self) -> None:
        client = _FakeKMSClient()
        client.failure = "AccessDeniedException"
        custodian = AWSKMSKeyCustodian(client)
        with self.assertRaises(CustodyError) as raised:
            custodian.generate_data_key(
                key_reference="alias/hormuz-provider-current",
                encryption_context={"hormuz:purpose": "provider_credential"},
            )
        self.assertEqual(raised.exception.code, "aws_custody_access_denied")

    def test_unavailable_kms_key_fails_closed_with_a_stable_code(self) -> None:
        client = _FakeKMSClient()
        client.failure = "KMSInvalidStateException"
        custodian = AWSKMSKeyCustodian(client)
        with self.assertRaises(CustodyError) as raised:
            custodian.generate_data_key(
                key_reference="alias/hormuz-provider-current",
                encryption_context={"hormuz:purpose": "provider_credential"},
            )
        self.assertEqual(raised.exception.code, "aws_kms_key_unavailable")

    def test_s3_object_lock_anchor_uses_compliance_and_sse_kms(self) -> None:
        client = _FakeS3Client()
        sink = S3ObjectLockAuditAnchorSink(
            client,
            region="us-east-1",
            bucket="hormuz-audit-bucket",
            prefix="immutable/audit",
            encryption_key_reference="arn:aws:kms:us-east-1:111122223333:key/audit-data",
        )
        sink.verify_configuration()
        artifact = build_audit_anchor_artifact([_usage_event()], organization_id="xpounder")
        encoded = serialize_audit_anchor_artifact(artifact)
        receipt = sink.anchor(
            encoded,
            artifact_id=artifact["artifact_id"],  # type: ignore[arg-type]
            organization_id="xpounder",
            head_digest=artifact["head_digest"],  # type: ignore[arg-type]
            retention_until=datetime.now(timezone.utc) + timedelta(days=30),
            legal_hold=True,
        )
        self.assertEqual(receipt.backend, "aws-s3-object-lock")
        self.assertEqual(receipt.object_version, "version-1")
        request = client.puts[0]
        self.assertEqual(request["ObjectLockMode"], "COMPLIANCE")
        self.assertEqual(request["ObjectLockLegalHoldStatus"], "ON")
        self.assertEqual(request["IfNoneMatch"], "*")
        self.assertEqual(request["ChecksumAlgorithm"], "SHA256")
        self.assertEqual(request["ServerSideEncryption"], "aws:kms")
        self.assertEqual(request["SSEKMSKeyId"], "arn:aws:kms:us-east-1:111122223333:key/audit-data")
        self.assertNotIn("xpounder", request["Key"])  # type: ignore[operator]

    def test_s3_without_object_lock_fails_closed(self) -> None:
        client = _FakeS3Client()
        client.lock = {"ObjectLockConfiguration": {"ObjectLockEnabled": "Disabled"}}
        sink = S3ObjectLockAuditAnchorSink(
            client,
            region="us-east-1",
            bucket="hormuz-audit-bucket",
            prefix="audit",
            encryption_key_reference="alias/data",
        )
        with self.assertRaises(CustodyError) as raised:
            sink.verify_configuration()
        self.assertEqual(raised.exception.code, "aws_s3_object_lock_required")

    def test_s3_anchor_rejects_mismatched_metadata_and_region(self) -> None:
        client = _FakeS3Client()
        client.location = {"LocationConstraint": "eu-west-1"}
        sink = S3ObjectLockAuditAnchorSink(
            client,
            region="us-east-1",
            bucket="hormuz-audit-bucket",
            prefix="audit",
            encryption_key_reference="alias/data",
        )
        with self.assertRaises(CustodyError) as raised:
            sink.verify_configuration()
        self.assertEqual(raised.exception.code, "aws_s3_region_mismatch")

        client.location = {"LocationConstraint": "us-east-1"}
        artifact = build_audit_anchor_artifact([_usage_event()], organization_id="xpounder")
        with self.assertRaises(CustodyError) as raised:
            sink.anchor(
                serialize_audit_anchor_artifact(artifact),
                artifact_id="16ab5178-397e-4624-9431-1a40fbf8f09f",
                organization_id="xpounder",
                head_digest=artifact["head_digest"],  # type: ignore[arg-type]
                retention_until=datetime.now(timezone.utc) + timedelta(days=30),
                legal_hold=False,
            )
        self.assertEqual(raised.exception.code, "audit_anchor_metadata_mismatch")

    def test_s3_anchor_rejects_a_noncanonical_artifact(self) -> None:
        client = _FakeS3Client()
        sink = S3ObjectLockAuditAnchorSink(
            client,
            region="us-east-1",
            bucket="hormuz-audit-bucket",
            prefix="audit",
            encryption_key_reference="alias/data",
        )
        artifact = build_audit_anchor_artifact([_usage_event()], organization_id="xpounder")
        with self.assertRaises(CustodyError) as raised:
            sink.anchor(
                json.dumps(artifact, indent=2).encode("utf-8"),
                artifact_id=artifact["artifact_id"],  # type: ignore[arg-type]
                organization_id="xpounder",
                head_digest=artifact["head_digest"],  # type: ignore[arg-type]
                retention_until=datetime.now(timezone.utc) + timedelta(days=30),
                legal_hold=False,
            )
        self.assertEqual(raised.exception.code, "audit_anchor_artifact_noncanonical")


class CustodyRuntimeTests(unittest.TestCase):
    def _config(self, root: Path, *, envelope: str | None = None) -> GatewayConfig:
        raw = json.loads((Path(__file__).parents[1] / "config.example.json").read_text(encoding="utf-8"))
        raw["database"] = str(root / "usage.sqlite3")
        raw["key_custody"] = {
            "backend": "aws-kms",
            "region": "us-east-1",
            "key_references": {
                "provider_credential": "alias/hormuz-provider",
                "data_encryption": "alias/hormuz-data",
            },
        }
        raw["audit_anchor"] = {
            "backend": "aws-s3-object-lock",
            "region": "us-east-1",
            "bucket": "hormuz-audit-bucket",
            "prefix": "immutable/audit",
            "retention_days": 365,
            "legal_hold": False,
        }
        if envelope is not None:
            raw["upstreams"]["openai"].pop("api_key_env")
            raw["upstreams"]["openai"]["api_key_envelope"] = envelope
        config_path = root / "hormuz.json"
        config_path.write_text(json.dumps(raw), encoding="utf-8")
        return GatewayConfig.load(
            config_path,
            environ={
                "HORMUZ_TOKEN": "identity-token-with-sufficient-length",
                "OPENAI_API_KEY": "unused-openai-source",
                "ANTHROPIC_API_KEY": "anthropic-secret",
            },
        )

    def test_encrypted_provider_envelope_is_owner_only_and_resolves_in_memory(self) -> None:
        provider = _MemoryDataKeyProvider()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "openai.envelope"
            config = self._config(root, envelope=str(output))
            envelope = EnvelopeCipher(provider).seal(
                b"openai-encrypted-secret",
                organization_id="xpounder",
                purpose=KEY_PURPOSE_PROVIDER_CREDENTIAL,
                key_reference="alias/hormuz-provider",
            )
            write_envelope_file(output, envelope)
            self.assertEqual(os.stat(output).st_mode & 0o777, 0o600)
            with patch("hormuz.custody_runtime.create_data_key_provider", return_value=provider):
                credentials = resolve_upstream_credentials(config, environ={"ANTHROPIC_API_KEY": "anthropic-secret"})
            self.assertEqual(credentials, {"openai": "openai-encrypted-secret", "anthropic": "anthropic-secret"})
            self.assertNotIn("openai-encrypted-secret", output.read_text(encoding="utf-8"))

    def test_unsafe_envelope_permissions_fail_before_key_service_use(self) -> None:
        provider = _MemoryDataKeyProvider()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "openai.envelope"
            envelope = EnvelopeCipher(provider).seal(
                b"openai-encrypted-secret",
                organization_id="xpounder",
                purpose=KEY_PURPOSE_PROVIDER_CREDENTIAL,
                key_reference="alias/hormuz-provider",
            )
            write_envelope_file(output, envelope)
            os.chmod(output, 0o644)
            with self.assertRaises(CustodyError) as raised:
                read_envelope_file(output)
            self.assertEqual(raised.exception.code, "encrypted_envelope_file_permissions_invalid")

    def test_envelope_write_refuses_replacement_then_force_replaces_complete_file(self) -> None:
        provider = _MemoryDataKeyProvider()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "openai.envelope"
            first = EnvelopeCipher(provider).seal(
                b"first-encrypted-secret",
                organization_id="xpounder",
                purpose=KEY_PURPOSE_PROVIDER_CREDENTIAL,
                key_reference="alias/hormuz-provider",
            )
            second = EnvelopeCipher(provider).seal(
                b"second-encrypted-secret",
                organization_id="xpounder",
                purpose=KEY_PURPOSE_PROVIDER_CREDENTIAL,
                key_reference="alias/hormuz-provider",
            )
            write_envelope_file(output, first)
            original = output.read_bytes()
            with self.assertRaises(CustodyError) as raised:
                write_envelope_file(output, second)
            self.assertEqual(raised.exception.code, "encrypted_envelope_file_write_failed")
            self.assertEqual(output.read_bytes(), original)

            write_envelope_file(output, second, force=True)
            self.assertEqual(EnvelopeCipher(provider).unseal(read_envelope_file(output)), b"second-encrypted-secret")
            self.assertEqual(list(root.glob(".openai.envelope.*")), [])

    def test_configuration_requires_distinct_purpose_keys_and_single_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = json.loads((Path(__file__).parents[1] / "config.example.json").read_text(encoding="utf-8"))
            raw["key_custody"] = {
                "backend": "aws-kms",
                "region": "us-east-1",
                "key_references": {
                    "provider_credential": "alias/shared",
                    "data_encryption": "alias/shared",
                },
            }
            raw["audit_anchor"] = {
                "backend": "aws-s3-object-lock",
                "region": "us-east-1",
                "bucket": "hormuz-audit-bucket",
                "prefix": "immutable/audit",
                "retention_days": 365,
            }
            path = root / "invalid.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(ConfigError) as raised:
                GatewayConfig.load(path, environ={"HORMUZ_TOKEN": "identity-token-with-sufficient-length"})
            self.assertIn("distinct keys", str(raised.exception))

            raw["key_custody"]["key_references"]["data_encryption"] = "alias/data"
            raw["upstreams"]["openai"]["api_key_envelope"] = "./openai.envelope"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(ConfigError) as raised:
                GatewayConfig.load(path, environ={"HORMUZ_TOKEN": "identity-token-with-sufficient-length"})
            self.assertIn("exactly one", str(raised.exception))

            raw["upstreams"]["openai"].pop("api_key_env")
            raw["audit_anchor"]["prefix"] = "immutable\ninvalid"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(ConfigError) as raised:
                GatewayConfig.load(path, environ={"HORMUZ_TOKEN": "identity-token-with-sufficient-length"})
            self.assertIn("safe non-empty object-key prefix", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
