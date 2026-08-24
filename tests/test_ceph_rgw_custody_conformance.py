from __future__ import annotations

import hashlib
import importlib.util
import json
import stat
import subprocess
import sys
import tempfile
import unittest
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping, Sequence
from unittest import mock

from hormuz.custody import AuditAnchorReceipt, CustodyError, DataKeyProvider, EnvelopeCipher, GeneratedDataKey, RewrappedDataKey, serialize_envelope


ROOT = Path(__file__).parents[1]
TOOL_PATH = ROOT / "tools" / "verify_ceph_rgw_custody_conformance.py"
_SPEC = importlib.util.spec_from_file_location("ceph_rgw_custody_conformance", TOOL_PATH)
assert _SPEC is not None and _SPEC.loader is not None
conformance = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = conformance
_SPEC.loader.exec_module(conformance)


class _MemoryDataKeyProvider(DataKeyProvider):
    def __init__(self) -> None:
        self._counter = 0
        self._keys: dict[bytes, tuple[bytes, str, dict[str, str]]] = {}

    def generate_data_key(
        self,
        *,
        key_reference: str,
        encryption_context: Mapping[str, str],
    ) -> GeneratedDataKey:
        self._counter += 1
        plaintext = bytes([64 + self._counter]) * 32
        encrypted = f"wrapped:{self._counter}".encode("ascii")
        self._keys[encrypted] = (plaintext, key_reference, dict(encryption_context))
        return GeneratedDataKey(key_reference=key_reference, plaintext=plaintext, encrypted=encrypted)

    def decrypt_data_key(
        self,
        *,
        key_reference: str,
        encrypted: bytes,
        encryption_context: Mapping[str, str],
    ) -> bytes:
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
        plaintext = self.decrypt_data_key(
            key_reference=source_key_reference,
            encrypted=encrypted,
            encryption_context=encryption_context,
        )
        self._counter += 1
        rewrapped = f"wrapped:{self._counter}".encode("ascii")
        self._keys[rewrapped] = (plaintext, destination_key_reference, dict(encryption_context))
        return RewrappedDataKey(key_reference=destination_key_reference, encrypted=rewrapped)


class _S3Error(Exception):
    def __init__(self, code: str) -> None:
        self.response = {"Error": {"Code": code}}


class _Body:
    def __init__(self, value: bytes) -> None:
        self.value = value

    def read(self) -> bytes:
        return self.value


@dataclass
class _Object:
    body: bytes
    retention_until: datetime
    metadata: dict[str, str]
    legal_hold: bool


class _FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], _Object] = {}
        self.control_versions: set[tuple[str, str]] = set()
        self.sequence = 0

    def head_object(self, *, Bucket: str, Key: str, VersionId: str) -> dict[str, object]:
        del Bucket
        record = self.objects[(Key, VersionId)]
        return {
            "ObjectLockMode": "COMPLIANCE",
            "ObjectLockRetainUntilDate": record.retention_until,
            "Metadata": record.metadata,
        }

    def get_object_retention(self, *, Bucket: str, Key: str, VersionId: str) -> dict[str, object]:
        del Bucket
        record = self.objects[(Key, VersionId)]
        return {"Retention": {"Mode": "COMPLIANCE", "RetainUntilDate": record.retention_until}}

    def get_object(self, *, Bucket: str, Key: str, VersionId: str) -> dict[str, object]:
        del Bucket
        return {"Body": _Body(self.objects[(Key, VersionId)].body)}

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str) -> dict[str, str]:
        del Bucket, Body, ContentType
        self.sequence += 1
        version = f"control-version-{self.sequence}"
        self.control_versions.add((Key, version))
        return {"VersionId": version}

    def put_object_retention(
        self,
        *,
        Bucket: str,
        Key: str,
        VersionId: str,
        Retention: Mapping[str, object],
    ) -> None:
        del Bucket
        record = self.objects[(Key, VersionId)]
        requested = Retention["RetainUntilDate"]
        if not isinstance(requested, datetime):
            raise _S3Error("InvalidRequest")
        if requested > record.retention_until:
            record.retention_until = requested
            return
        raise _S3Error("AccessDenied")

    def delete_object(self, *, Bucket: str, Key: str, VersionId: str) -> None:
        del Bucket
        control = (Key, VersionId)
        if control in self.control_versions:
            self.control_versions.remove(control)
            return
        raise _S3Error("AccessDenied")

    def get_object_legal_hold(self, *, Bucket: str, Key: str, VersionId: str) -> dict[str, object]:
        del Bucket
        record = self.objects[(Key, VersionId)]
        return {"LegalHold": {"Status": "ON" if record.legal_hold else "OFF"}}


class _FakeSink:
    def __init__(self, client: _FakeS3Client, provider: _MemoryDataKeyProvider) -> None:
        self.client = client
        self.provider = provider
        self.sequence = 0
        self.verified = False

    def verify_configuration(self) -> None:
        self.verified = True

    def _object_key(self, *, organization_id: str, artifact_id: str) -> str:
        return f"conformance/{hashlib.sha256(organization_id.encode()).hexdigest()[:16]}/{artifact_id}.json"

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
        self.sequence += 1
        version = f"version-{self.sequence}"
        sealed = serialize_envelope(
            EnvelopeCipher(self.provider).seal(
                artifact,
                organization_id=organization_id,
                purpose="data_encryption",
                key_reference="audit-key",
            )
        )
        artifact_sha256 = hashlib.sha256(artifact).hexdigest()
        self.client.objects[(self._object_key(organization_id=organization_id, artifact_id=artifact_id), version)] = _Object(
            body=sealed,
            retention_until=retention_until,
            metadata={
                "hormuz-artifact-sha256": artifact_sha256,
                "hormuz-head-digest": head_digest,
            },
            legal_hold=legal_hold,
        )
        return AuditAnchorReceipt(
            backend="s3-compatible-object-lock",
            artifact_id=artifact_id,
            artifact_sha256=artifact_sha256,
            head_digest=head_digest,
            object_version=version,
        )


class CephRGWConfigurationTests(unittest.TestCase):
    def _environment(self) -> dict[str, str]:
        return {
            conformance.OPT_IN_ENV: "1",
            conformance.CONFIRMATION_ENV: conformance.CONFIRMATION_VALUE,
            "HORMUZ_CEPH_RGW_ENDPOINT": "http://127.0.0.1:7480",
            "HORMUZ_CEPH_RGW_REGION": "us-east-1",
            "HORMUZ_CEPH_RGW_BUCKET": "hormuz-ceph-conformance",
            "HORMUZ_CEPH_RGW_ACCESS_KEY": "test-access-key",
            "HORMUZ_CEPH_RGW_SECRET_KEY": "test-secret-key",
            "HORMUZ_CEPH_RGW_CONTAINER": "ceph-rgw-0",
            "HORMUZ_CEPH_OPENBAO_ENDPOINT": "http://127.0.0.1:8200",
            "HORMUZ_CEPH_OPENBAO_CONTAINER": "openbao-0",
            "HORMUZ_CEPH_OPENBAO_TOKEN": "test-openbao-token",
            "HORMUZ_CEPH_OPENBAO_PROVIDER_KEY": "provider-key",
            "HORMUZ_CEPH_OPENBAO_DATA_KEY": "audit-key",
        }

    def test_configuration_is_explicit_loopback_and_retention_acknowledged(self) -> None:
        config = conformance.configuration_from_environment(self._environment())
        self.assertEqual(config.rgw_endpoint, "http://127.0.0.1:7480")
        self.assertEqual(config.retention_days, 1)
        self.assertEqual(config.prefix, "hormuz/conformance")

        environment = self._environment()
        environment["HORMUZ_CEPH_RGW_ENDPOINT"] = "https://rgw.example.test"
        with self.assertRaises(conformance.ConformanceFailure) as raised:
            conformance.configuration_from_environment(environment)
        self.assertEqual(raised.exception.code, "local_endpoint_required")

        environment = self._environment()
        environment[conformance.CONFIRMATION_ENV] = "no"
        with self.assertRaises(conformance.ConformanceFailure) as raised:
            conformance.configuration_from_environment(environment)
        self.assertEqual(raised.exception.code, "retention_acknowledgement_required")

        environment = self._environment()
        environment["HORMUZ_CEPH_OPENBAO_DATA_KEY"] = environment["HORMUZ_CEPH_OPENBAO_PROVIDER_KEY"]
        with self.assertRaises(conformance.ConformanceFailure) as raised:
            conformance.configuration_from_environment(environment)
        self.assertEqual(raised.exception.code, "key_purposes_not_separated")

        environment = self._environment()
        environment["HORMUZ_CEPH_OPENBAO_CONTAINER"] = "not/a-container"
        with self.assertRaises(conformance.ConformanceFailure) as raised:
            conformance.configuration_from_environment(environment)
        self.assertEqual(raised.exception.code, "openbao_container_invalid")


class CephRGWAttestationTests(unittest.TestCase):
    def test_attestation_requires_exact_digest_and_release(self) -> None:
        image_id = "sha256:local-image"
        responses = {
            ("docker", "inspect", "--format", "{{.State.Running}}|{{.Image}}", "ceph-rgw-0"): f"true|{image_id}",
            ("docker", "image", "inspect", "--format", "{{json .RepoDigests}}", image_id): json.dumps(
                [conformance.TARGET_IMAGE_REFERENCE]
            ),
            ("docker", "exec", "ceph-rgw-0", "ceph", "--version"): conformance.TARGET_VERSION_OUTPUT,
            ("docker", "image", "inspect", "--format", "{{.Os}}/{{.Architecture}}", image_id): "linux/arm64",
        }

        def command_output(command: Sequence[str]) -> str:
            return responses[tuple(command)]

        target = conformance.attest_local_rgw_container("ceph-rgw-0", command_output=command_output)
        self.assertEqual(target["release"], "20.2.3")
        self.assertEqual(target["image_digest"], conformance.TARGET_IMAGE_DIGEST)

        responses[("docker", "exec", "ceph-rgw-0", "ceph", "--version")] = "ceph version 20.2.2"
        with self.assertRaises(conformance.ConformanceFailure) as raised:
            conformance.attest_local_rgw_container("ceph-rgw-0", command_output=command_output)
        self.assertEqual(raised.exception.code, "candidate_release_mismatch")

    def test_pre_attested_target_must_match_the_exact_candidate(self) -> None:
        environment = {
            "HORMUZ_CEPH_RGW_TARGET_IMAGE_REFERENCE": conformance.TARGET_IMAGE_REFERENCE,
            "HORMUZ_CEPH_RGW_TARGET_IMAGE_DIGEST": conformance.TARGET_IMAGE_DIGEST,
            "HORMUZ_CEPH_RGW_TARGET_RELEASE": conformance.TARGET_RELEASE,
            "HORMUZ_CEPH_RGW_TARGET_PLATFORM": "linux/arm64",
        }
        self.assertEqual(
            conformance.attest_target_from_environment(environment)["image_digest"], conformance.TARGET_IMAGE_DIGEST
        )

        environment["HORMUZ_CEPH_RGW_TARGET_RELEASE"] = "20.2.2"
        with self.assertRaises(conformance.ConformanceFailure) as raised:
            conformance.attest_target_from_environment(environment)
        self.assertEqual(raised.exception.code, "pre_attested_target_invalid")

    def test_openbao_attestation_requires_exact_container_image_platform_and_version(self) -> None:
        image_id = "sha256:openbao-image"
        responses = {
            ("docker", "inspect", "--format", "{{.State.Running}}|{{.Image}}", "openbao-0"): f"true|{image_id}",
            ("docker", "image", "inspect", "--format", "{{json .RepoDigests}}", image_id): json.dumps(
                [conformance.OPENBAO_TARGET_IMAGE_REFERENCE]
            ),
            ("docker", "exec", "openbao-0", "bao", "version"): conformance.OPENBAO_TARGET_VERSION_OUTPUT,
            ("docker", "image", "inspect", "--format", "{{.Os}}/{{.Architecture}}", image_id): "linux/arm64",
        }

        def command_output(command: Sequence[str]) -> str:
            return responses[tuple(command)]

        self.assertEqual(
            conformance.attest_local_openbao_container("openbao-0", command_output=command_output),
            {
                "image_reference": conformance.OPENBAO_TARGET_IMAGE_REFERENCE,
                "image_digest": conformance.OPENBAO_TARGET_IMAGE_DIGEST,
                "version": conformance.OPENBAO_TARGET_VERSION_OUTPUT,
                "platform": conformance.OPENBAO_TARGET_PLATFORM,
            },
        )

        responses[("docker", "inspect", "--format", "{{.State.Running}}|{{.Image}}", "openbao-0")] = f"false|{image_id}"
        with self.assertRaises(conformance.ConformanceFailure) as raised:
            conformance.attest_local_openbao_container("openbao-0", command_output=command_output)
        self.assertEqual(raised.exception.code, "openbao_container_unverified")

        responses[("docker", "inspect", "--format", "{{.State.Running}}|{{.Image}}", "openbao-0")] = f"true|{image_id}"
        responses[("docker", "image", "inspect", "--format", "{{json .RepoDigests}}", image_id)] = json.dumps([])
        with self.assertRaises(conformance.ConformanceFailure) as raised:
            conformance.attest_local_openbao_container("openbao-0", command_output=command_output)
        self.assertEqual(raised.exception.code, "openbao_digest_mismatch")

        responses[("docker", "image", "inspect", "--format", "{{json .RepoDigests}}", image_id)] = json.dumps(
            [conformance.OPENBAO_TARGET_IMAGE_REFERENCE]
        )
        responses[("docker", "exec", "openbao-0", "bao", "version")] = "OpenBao v2.5.3"
        with self.assertRaises(conformance.ConformanceFailure) as raised:
            conformance.attest_local_openbao_container("openbao-0", command_output=command_output)
        self.assertEqual(raised.exception.code, "openbao_release_mismatch")

        responses[("docker", "exec", "openbao-0", "bao", "version")] = conformance.OPENBAO_TARGET_VERSION_OUTPUT
        responses[("docker", "image", "inspect", "--format", "{{.Os}}/{{.Architecture}}", image_id)] = "linux/amd64"
        with self.assertRaises(conformance.ConformanceFailure) as raised:
            conformance.attest_local_openbao_container("openbao-0", command_output=command_output)
        self.assertEqual(raised.exception.code, "openbao_platform_mismatch")

    def test_pre_attested_openbao_target_must_match_the_exact_candidate(self) -> None:
        environment = {
            "HORMUZ_CEPH_OPENBAO_TARGET_ATTESTED": "1",
            "HORMUZ_CEPH_OPENBAO_TARGET_IMAGE_REFERENCE": conformance.OPENBAO_TARGET_IMAGE_REFERENCE,
            "HORMUZ_CEPH_OPENBAO_TARGET_IMAGE_DIGEST": conformance.OPENBAO_TARGET_IMAGE_DIGEST,
            "HORMUZ_CEPH_OPENBAO_TARGET_VERSION": conformance.OPENBAO_TARGET_VERSION_OUTPUT,
            "HORMUZ_CEPH_OPENBAO_TARGET_PLATFORM": conformance.OPENBAO_TARGET_PLATFORM,
        }
        self.assertEqual(
            conformance.attest_openbao_target_from_environment(environment)["image_digest"],
            conformance.OPENBAO_TARGET_IMAGE_DIGEST,
        )

        for field, wrong_value in (
            ("HORMUZ_CEPH_OPENBAO_TARGET_IMAGE_REFERENCE", "openbao/openbao@sha256:" + "0" * 64),
            ("HORMUZ_CEPH_OPENBAO_TARGET_IMAGE_DIGEST", "sha256:" + "0" * 64),
            ("HORMUZ_CEPH_OPENBAO_TARGET_VERSION", "OpenBao v2.5.3"),
            ("HORMUZ_CEPH_OPENBAO_TARGET_PLATFORM", "linux/amd64"),
        ):
            candidate = dict(environment)
            candidate[field] = wrong_value
            with self.assertRaises(conformance.ConformanceFailure) as raised:
                conformance.attest_openbao_target_from_environment(candidate)
            self.assertEqual(raised.exception.code, "pre_attested_openbao_target_invalid")


class CephRGWRunnerAttestationTests(unittest.TestCase):
    def test_runner_requires_a_content_addressed_x86_64_image(self) -> None:
        digest = "sha256:" + "a" * 64
        runner = conformance.attest_runner_from_environment(
            {
                "HORMUZ_CEPH_RGW_RUNNER_IMAGE_DIGEST": digest,
                "HORMUZ_CEPH_RGW_RUNNER_PLATFORM": "linux/amd64",
            }
        )
        self.assertEqual(runner, {"image_digest": digest, "platform": "linux/amd64"})

        with self.assertRaises(conformance.ConformanceFailure) as raised:
            conformance.attest_runner_from_environment(
                {
                    "HORMUZ_CEPH_RGW_RUNNER_IMAGE_DIGEST": digest,
                    "HORMUZ_CEPH_RGW_RUNNER_PLATFORM": "linux/arm64",
                }
            )
        self.assertEqual(raised.exception.code, "runner_platform_invalid")

    def test_pinned_runner_launcher_attests_openbao_without_mounting_the_docker_socket(self) -> None:
        launcher = ROOT / "tools" / "run_ceph_rgw_custody_conformance_container.sh"
        parsed = subprocess.run(["bash", "-n", str(launcher)], check=False, capture_output=True, text=True)
        self.assertEqual(parsed.returncode, 0, parsed.stderr)
        contents = launcher.read_text(encoding="utf-8")
        self.assertIn('readonly OPENBAO_TARGET_IMAGE_REFERENCE=', contents)
        self.assertIn('HORMUZ_CEPH_OPENBAO_CONTAINER', contents)
        self.assertIn('docker exec "${openbao_container}" bao version', contents)
        self.assertIn('HORMUZ_CEPH_OPENBAO_TARGET_ATTESTED=1', contents)
        self.assertIn('openbao_platform_mismatch', contents)
        self.assertIn("evidence_owner=\"$(stat --format '%u:%g'", contents)
        self.assertIn('--user "${evidence_owner}"', contents)
        self.assertNotIn('/var/run/docker.sock', contents)


class CephRGWLiveHarnessShapeTests(unittest.TestCase):
    def _config(self) -> object:
        return conformance.ConformanceConfig(
            rgw_endpoint="http://127.0.0.1:7480",
            region="us-east-1",
            bucket="hormuz-ceph-conformance",
            prefix="hormuz/conformance",
            access_key="test-access-key",
            secret_key="test-secret-key",
            rgw_container="ceph-rgw-0",
            openbao_container="openbao-0",
            openbao_endpoint="http://127.0.0.1:8200",
            openbao_token="test-openbao-token",
            transit_mount="transit",
            provider_key="provider-key",
            data_key="audit-key",
            retention_days=1,
        )

    def test_successful_live_shape_records_only_content_free_evidence(self) -> None:
        provider = _MemoryDataKeyProvider()
        client = _FakeS3Client()
        sink = _FakeSink(client, provider)

        evidence = conformance.run_conformance(
            self._config(),
            attest=lambda container: {
                "image_reference": conformance.TARGET_IMAGE_REFERENCE,
                "image_digest": conformance.TARGET_IMAGE_DIGEST,
                "release": "20.2.3",
                "platform": "linux/arm64",
            },
            attest_openbao=lambda container: {
                "image_reference": conformance.OPENBAO_TARGET_IMAGE_REFERENCE,
                "image_digest": conformance.OPENBAO_TARGET_IMAGE_DIGEST,
                "version": conformance.OPENBAO_TARGET_VERSION_OUTPUT,
                "platform": conformance.OPENBAO_TARGET_PLATFORM,
            },
            attest_runner=lambda: {"image_digest": "sha256:" + "b" * 64, "platform": "linux/amd64"},
            runtime_factory=lambda config: conformance.ConformanceRuntime(provider=provider, sink=sink, client=client),
        )

        self.assertTrue(sink.verified)
        self.assertEqual(evidence["schema_id"], conformance.SCHEMA_ID)
        self.assertEqual(evidence["schema_version"], 3)
        self.assertEqual(evidence["status"], "passed")
        self.assertEqual(evidence["runner"]["platform"], "linux/amd64")
        self.assertEqual(evidence["openbao_target"]["version"], conformance.OPENBAO_TARGET_VERSION_OUTPUT)
        self.assertEqual(len(evidence["retained_artifacts"]), 2)
        serialized = json.dumps(evidence, sort_keys=True)
        self.assertNotIn("127.0.0.1", serialized)
        self.assertNotIn("hormuz-ceph-conformance", serialized)
        self.assertNotIn("test-secret-key", serialized)
        self.assertIn("retention_reduction_denied", evidence["checks"])
        self.assertIn("protected_version_deletion_denied", evidence["checks"])
        self.assertIn("unprotected_control_version_deletion_permitted", evidence["checks"])
        self.assertIn("retention_extension_permitted", evidence["checks"])

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "evidence.json"
            conformance.write_evidence(output, evidence)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["status"], "passed")

            invalid = dict(evidence)
            invalid["checks"] = ["retention_reduction_denied"]
            with self.assertRaises(conformance.ConformanceFailure) as raised:
                conformance.write_evidence(output, invalid)
            self.assertEqual(raised.exception.code, "evidence_invalid")

            previous = dict(evidence)
            previous["schema_version"] = 2
            previous["checks"] = list(conformance._PREVIOUS_REQUIRED_CHECKS)
            del previous["openbao_target"]
            conformance.write_evidence(output, previous)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["schema_version"], 2)

            legacy = dict(previous)
            legacy["schema_version"] = 1
            legacy["nonclaims"] = list(conformance._NONCLAIMS)
            del legacy["runner"]
            conformance.write_evidence(output, legacy)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["schema_version"], 1)

            invalid_runner = dict(evidence)
            invalid_runner["runner"] = {"image_digest": "sha256:" + "b" * 64, "platform": "linux/arm64"}
            with self.assertRaises(conformance.ConformanceFailure) as raised:
                conformance.write_evidence(output, invalid_runner)
            self.assertEqual(raised.exception.code, "evidence_invalid")

    def test_negative_retention_checks_require_proven_control_permissions(self) -> None:
        provider = _MemoryDataKeyProvider()
        client = _FakeS3Client()
        sink = _FakeSink(client, provider)
        runtime_factory = lambda config: conformance.ConformanceRuntime(provider=provider, sink=sink, client=client)
        target = lambda container: {
            "image_reference": conformance.TARGET_IMAGE_REFERENCE,
            "image_digest": conformance.TARGET_IMAGE_DIGEST,
            "release": "20.2.3",
            "platform": "linux/arm64",
        }
        runner = lambda: {"image_digest": "sha256:" + "c" * 64, "platform": "linux/amd64"}
        openbao_target = lambda container: {
            "image_reference": conformance.OPENBAO_TARGET_IMAGE_REFERENCE,
            "image_digest": conformance.OPENBAO_TARGET_IMAGE_DIGEST,
            "version": conformance.OPENBAO_TARGET_VERSION_OUTPUT,
            "platform": conformance.OPENBAO_TARGET_PLATFORM,
        }

        with mock.patch.object(client, "delete_object", side_effect=_S3Error("AccessDenied")):
            with self.assertRaises(conformance.ConformanceFailure) as raised:
                conformance.run_conformance(
                    self._config(), attest=target, attest_openbao=openbao_target, attest_runner=runner, runtime_factory=runtime_factory
                )
        self.assertEqual(raised.exception.code, "control_delete_not_permitted")

        provider = _MemoryDataKeyProvider()
        client = _FakeS3Client()
        sink = _FakeSink(client, provider)
        runtime_factory = lambda config: conformance.ConformanceRuntime(provider=provider, sink=sink, client=client)
        with mock.patch.object(client, "put_object_retention", side_effect=_S3Error("AccessDenied")):
            with self.assertRaises(conformance.ConformanceFailure) as raised:
                conformance.run_conformance(
                    self._config(), attest=target, attest_openbao=openbao_target, attest_runner=runner, runtime_factory=runtime_factory
                )
        self.assertEqual(raised.exception.code, "retention_extension_not_permitted")


if __name__ == "__main__":
    unittest.main()
