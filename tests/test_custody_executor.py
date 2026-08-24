from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from uuid import uuid4

from hormuz.config import GatewayConfig, KeyCustodyConfig
from hormuz.custody import EnvelopeCipher, GeneratedDataKey, RewrappedDataKey
from hormuz.custody_execution_repository import (
    CustodyExecutionAttempt,
    CustodyExecutionEvent,
    CustodyExecutionRequest,
    execution_descriptor_sha256,
    protected_input_reference_sha256,
)
from hormuz.custody_executor import EnvelopeRoutineExecutor, OwnerOnlyFileProtectedInputResolver
from hormuz.custody_runtime import read_envelope_file


ROOT = Path(__file__).resolve().parents[1]


class _DataKeyProvider:
    def generate_data_key(self, *, key_reference: str, encryption_context: dict[str, str]) -> GeneratedDataKey:
        del encryption_context
        return GeneratedDataKey(key_reference=key_reference, plaintext=b"k" * 32, encrypted=b"wrapped-k")

    def decrypt_data_key(self, *, key_reference: str, encrypted: bytes, encryption_context: dict[str, str]) -> bytes:
        del key_reference, encryption_context
        if encrypted not in {b"wrapped-k", b"rewrapped-k"}:
            raise AssertionError("unexpected encrypted data key")
        return b"k" * 32

    def rewrap_data_key(
        self,
        *,
        source_key_reference: str,
        destination_key_reference: str,
        encrypted: bytes,
        encryption_context: dict[str, str],
    ) -> RewrappedDataKey:
        del source_key_reference, encrypted, encryption_context
        return RewrappedDataKey(key_reference=destination_key_reference, encrypted=b"rewrapped-k")


class CustodyExecutorContractTests(unittest.TestCase):
    def test_request_digests_are_canonical_and_protected_reference_never_enters_evidence(self) -> None:
        operation_id = str(uuid4())
        request = CustodyExecutionRequest(
            organization_id="xpounder",
            operation_id=operation_id,
            operation_type="seal_envelope",
            target={"path": "/private/target.envelope", "kind": "owner_only_file"},
            parameters={"purpose": "provider_credential"},
            protected_input_reference="/private/input.secret",
        )
        self.assertEqual(
            request.target_sha256,
            execution_descriptor_sha256({"kind": "owner_only_file", "path": "/private/target.envelope"}),
        )
        self.assertEqual(
            request.protected_input_ref_sha256,
            protected_input_reference_sha256("/private/input.secret"),
        )
        now = datetime.now(timezone.utc)
        execution_id = str(uuid4())
        pending = CustodyExecutionEvent(
            organization_id="xpounder",
            execution_id=execution_id,
            operation_id=operation_id,
            occurred_at=now,
            sequence=1,
            state="pending",
            reason_code=None,
        )
        attempt = CustodyExecutionAttempt(
            organization_id="xpounder",
            execution_id=execution_id,
            operation_id=operation_id,
            operation_type=request.operation_type,
            target_kind="envelope",
            target_sha256=request.target_sha256,
            parameters_sha256=request.parameters_sha256,
            protected_input_ref_sha256=request.protected_input_ref_sha256,
            claimed_at=now,
            events=(pending,),
        )
        serialized = json.dumps(
            {"attempt": attempt.contract_record(), "event": pending.contract_record()},
            sort_keys=True,
        )
        self.assertNotIn("/private/input.secret", serialized)
        self.assertNotIn("/private/target.envelope", serialized)
        self.assertNotIn("plaintext", serialized)

    def test_owner_only_resolver_and_envelope_runner_seal_rewrap_and_verify_restore(self) -> None:
        config = GatewayConfig.load(
            ROOT / "config.example.json",
            environ={"HORMUZ_TOKEN": "test-identity-token"},
        )
        config = replace(
            config,
            key_custody=KeyCustodyConfig(
                backend="openbao-transit",
                region=None,
                key_references={
                    "provider_credential": "provider-key",
                    "identity_connector_secret": "identity-key",
                    "session_material": "session-key",
                    "approval_fingerprint": "approval-key",
                    "data_encryption": "data-key",
                },
                endpoint_url="http://127.0.0.1:8200",
                token_env="HORMUZ_OPENBAO_TOKEN",
                transit_mount="transit",
            ),
        )
        provider = _DataKeyProvider()
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            protected_input = directory / "initial.secret"
            protected_input.write_bytes(b"secret-for-executor-only")
            os.chmod(protected_input, 0o600)
            sealed_path = directory / "sealed.envelope"
            rewrapped_path = directory / "rewrapped.envelope"
            resolver = OwnerOnlyFileProtectedInputResolver()
            with mock.patch("hormuz.custody_executor.create_data_key_provider", return_value=provider):
                runner = EnvelopeRoutineExecutor(config, protected_input_resolver=resolver)
                seal_request = CustodyExecutionRequest(
                    organization_id="xpounder",
                    operation_id=str(uuid4()),
                    operation_type="seal_envelope",
                    target={"kind": "owner_only_file", "path": str(sealed_path)},
                    parameters={"purpose": "provider_credential"},
                    protected_input_reference=str(protected_input),
                )
                runner.execute(seal_request)
                sealed = read_envelope_file(sealed_path)
                self.assertEqual(EnvelopeCipher(provider).unseal(sealed), b"secret-for-executor-only")

                rewrap_request = CustodyExecutionRequest(
                    organization_id="xpounder",
                    operation_id=str(uuid4()),
                    operation_type="rewrap_envelope",
                    target={"kind": "owner_only_file", "path": str(rewrapped_path)},
                    parameters={"source_envelope_path": str(sealed_path)},
                )
                runner.execute(rewrap_request)
                rewrapped = read_envelope_file(rewrapped_path)
                self.assertEqual(rewrapped.encrypted_data_key, b"rewrapped-k")
                self.assertEqual(EnvelopeCipher(provider).unseal(rewrapped), b"secret-for-executor-only")

                verify_request = CustodyExecutionRequest(
                    organization_id="xpounder",
                    operation_id=str(uuid4()),
                    operation_type="verify_restore",
                    target={"kind": "owner_only_file", "path": str(rewrapped_path)},
                    parameters={},
                )
                runner.execute(verify_request)


if __name__ == "__main__":
    unittest.main()
