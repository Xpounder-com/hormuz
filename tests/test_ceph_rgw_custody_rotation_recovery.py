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
from pathlib import Path
from typing import Mapping

from hormuz.custody import CustodyError, DataKeyProvider, GeneratedDataKey, RewrappedDataKey
from hormuz.self_hosted_custody import EncryptedS3ObjectLockAuditAnchorSink


ROOT = Path(__file__).parents[1]
TOOL_PATH = ROOT / "tools" / "verify_ceph_rgw_custody_rotation_recovery.py"
_SPEC = importlib.util.spec_from_file_location("ceph_rgw_custody_rotation_recovery", TOOL_PATH)
assert _SPEC is not None and _SPEC.loader is not None
conformance = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = conformance
_SPEC.loader.exec_module(conformance)


@dataclass(frozen=True)
class _StoredDataKey:
    plaintext: bytes
    key_reference: str
    encryption_context: dict[str, str]


class _VersionedTransit:
    """Small Transit-shaped key authority that retains old versions for recovery."""

    def __init__(self) -> None:
        self.versions = {"provider-key": 1, "audit-key": 1}
        self._keys: dict[bytes, _StoredDataKey] = {}
        self._counter = 0
        self.rotations: list[str] = []
        self.rewraps = 0

    def generate(
        self,
        *,
        key_reference: str,
        encryption_context: Mapping[str, str],
    ) -> GeneratedDataKey:
        if key_reference not in self.versions:
            raise CustodyError("openbao_custody_key_unavailable")
        self._counter += 1
        plaintext = hashlib.sha256(f"{key_reference}:{self._counter}".encode("ascii")).digest()
        encrypted = f"vault:v{self.versions[key_reference]}:{self._counter}".encode("ascii")
        self._keys[encrypted] = _StoredDataKey(
            plaintext=plaintext,
            key_reference=key_reference,
            encryption_context=dict(encryption_context),
        )
        return GeneratedDataKey(key_reference=key_reference, plaintext=plaintext, encrypted=encrypted)

    def decrypt(
        self,
        *,
        key_reference: str,
        encrypted: bytes,
        encryption_context: Mapping[str, str],
    ) -> bytes:
        stored = self._keys.get(encrypted)
        if (
            stored is None
            or stored.key_reference != key_reference
            or stored.encryption_context != dict(encryption_context)
        ):
            raise CustodyError("openbao_custody_key_unavailable")
        return stored.plaintext

    def rewrap(
        self,
        *,
        source_key_reference: str,
        destination_key_reference: str,
        encrypted: bytes,
        encryption_context: Mapping[str, str],
    ) -> RewrappedDataKey:
        plaintext = self.decrypt(
            key_reference=source_key_reference,
            encrypted=encrypted,
            encryption_context=encryption_context,
        )
        if destination_key_reference not in self.versions:
            raise CustodyError("openbao_custody_key_unavailable")
        self._counter += 1
        rewrapped = f"vault:v{self.versions[destination_key_reference]}:{self._counter}".encode("ascii")
        self._keys[rewrapped] = _StoredDataKey(
            plaintext=plaintext,
            key_reference=destination_key_reference,
            encryption_context=dict(encryption_context),
        )
        self.rewraps += 1
        return RewrappedDataKey(key_reference=destination_key_reference, encrypted=rewrapped)

    def rotate(self, key_reference: str) -> None:
        if key_reference not in self.versions:
            raise CustodyError("openbao_custody_key_unavailable")
        self.versions[key_reference] += 1
        self.rotations.append(key_reference)


class _VersionedProvider(DataKeyProvider):
    def __init__(self, transit: _VersionedTransit) -> None:
        self._transit = transit

    def generate_data_key(
        self,
        *,
        key_reference: str,
        encryption_context: Mapping[str, str],
    ) -> GeneratedDataKey:
        return self._transit.generate(key_reference=key_reference, encryption_context=encryption_context)

    def decrypt_data_key(
        self,
        *,
        key_reference: str,
        encrypted: bytes,
        encryption_context: Mapping[str, str],
    ) -> bytes:
        return self._transit.decrypt(
            key_reference=key_reference,
            encrypted=encrypted,
            encryption_context=encryption_context,
        )

    def rewrap_data_key(
        self,
        *,
        source_key_reference: str,
        destination_key_reference: str,
        encrypted: bytes,
        encryption_context: Mapping[str, str],
    ) -> RewrappedDataKey:
        return self._transit.rewrap(
            source_key_reference=source_key_reference,
            destination_key_reference=destination_key_reference,
            encrypted=encrypted,
            encryption_context=encryption_context,
        )


class _RotationControl:
    def __init__(self, transit: _VersionedTransit, *, can_rotate: bool, can_use_data_keys: bool = False) -> None:
        self._transit = transit
        self._can_rotate = can_rotate
        self._can_use_data_keys = can_use_data_keys
        self.denied_checks: list[str] = []
        self.administrator_checks: list[str] = []

    def assert_rotation_denied(self, *, key_reference: str) -> None:
        if self._can_rotate:
            raise CustodyError("openbao_custody_runtime_rotation_authorized")
        self.denied_checks.append(key_reference)

    def assert_rotation_only_administrator(self, *, key_reference: str) -> None:
        if not self._can_rotate:
            raise CustodyError("openbao_custody_rotation_administrator_scope_invalid")
        if self._can_use_data_keys:
            raise CustodyError("openbao_custody_rotation_administrator_data_plane_authorized")
        self.administrator_checks.append(key_reference)

    def rotate_key_version(self, *, key_reference: str) -> None:
        if not self._can_rotate:
            raise CustodyError("openbao_custody_access_denied")
        self._transit.rotate(key_reference)


class _Body:
    def __init__(self, value: bytes) -> None:
        self._value = value

    def read(self, amount: int = -1) -> bytes:
        return self._value if amount < 0 else self._value[:amount]


@dataclass
class _StoredObject:
    body: bytes
    metadata: dict[str, str]


class _FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], _StoredObject] = {}
        self._sequence = 0

    def get_bucket_versioning(self, **_: object) -> dict[str, str]:
        return {"Status": "Enabled"}

    def get_object_lock_configuration(self, **_: object) -> dict[str, object]:
        return {"ObjectLockConfiguration": {"ObjectLockEnabled": "Enabled"}}

    def get_bucket_location(self, **_: object) -> dict[str, str]:
        return {"LocationConstraint": "us-east-1"}

    def put_object(self, **kwargs: object) -> dict[str, str]:
        self._sequence += 1
        key = kwargs["Key"]
        body = kwargs["Body"]
        metadata = kwargs["Metadata"]
        assert isinstance(key, str)
        assert isinstance(body, bytes)
        assert isinstance(metadata, dict)
        version = f"version-{self._sequence}"
        self.objects[(key, version)] = _StoredObject(body=body, metadata=dict(metadata))
        return {"VersionId": version}

    def get_object(self, **kwargs: object) -> dict[str, object]:
        key = kwargs["Key"]
        version = kwargs["VersionId"]
        assert isinstance(key, str)
        assert isinstance(version, str)
        stored = self.objects[(key, version)]
        return {"Body": _Body(stored.body), "Metadata": dict(stored.metadata)}


class _RecoveryRuntimeFactory:
    def __init__(self, *, runtime_can_rotate: bool = False, administrator_can_use_data_keys: bool = False) -> None:
        self.transit = _VersionedTransit()
        self.client = _FakeS3Client()
        self.runtime_can_rotate = runtime_can_rotate
        self.administrator_can_use_data_keys = administrator_can_use_data_keys
        self.providers: list[_VersionedProvider] = []
        self.runtime_controls: list[_RotationControl] = []
        self.administrator_controls: list[_RotationControl] = []

    def __call__(self, config: conformance.RecoveryConfig) -> conformance.RecoveryRuntime:
        provider = _VersionedProvider(self.transit)
        sink = EncryptedS3ObjectLockAuditAnchorSink(
            self.client,
            region=config.region,
            bucket=config.bucket,
            prefix=config.prefix,
            key_provider=provider,
            encryption_key_reference=config.data_key,
        )
        runtime_control = _RotationControl(self.transit, can_rotate=self.runtime_can_rotate)
        administrator_control = _RotationControl(
            self.transit,
            can_rotate=True,
            can_use_data_keys=self.administrator_can_use_data_keys,
        )
        self.providers.append(provider)
        self.runtime_controls.append(runtime_control)
        self.administrator_controls.append(administrator_control)
        return conformance.RecoveryRuntime(
            provider=provider,
            runtime_rotation_control=runtime_control,
            administrator_rotation_control=administrator_control,
            sink=sink,
        )


class CephRGWCustodyRotationRecoveryTests(unittest.TestCase):
    def _environment(self) -> dict[str, str]:
        return {
            conformance.OPT_IN_ENV: "1",
            conformance.CONFIRMATION_ENV: conformance.CONFIRMATION_VALUE,
            "HORMUZ_CEPH_RGW_ENDPOINT": "http://127.0.0.1:7480",
            "HORMUZ_CEPH_RGW_REGION": "us-east-1",
            "HORMUZ_CEPH_RGW_BUCKET": "hormuz-rotation-recovery",
            "HORMUZ_CEPH_RGW_ACCESS_KEY": "test-access-key",
            "HORMUZ_CEPH_RGW_SECRET_KEY": "test-secret-key",
            "HORMUZ_CEPH_OPENBAO_ENDPOINT": "http://127.0.0.1:8200",
            "HORMUZ_CEPH_OPENBAO_RUNTIME_TOKEN": "runtime-token",
            "HORMUZ_CEPH_OPENBAO_ADMIN_TOKEN": "administrator-token",
            "HORMUZ_CEPH_OPENBAO_TRANSIT_MOUNT": "transit",
            "HORMUZ_CEPH_OPENBAO_PROVIDER_KEY": "provider-key",
            "HORMUZ_CEPH_OPENBAO_DATA_KEY": "audit-key",
            "HORMUZ_CEPH_OPENBAO_UNAVAILABLE_KEY": "unavailable-key",
        }

    def _config(self) -> conformance.RecoveryConfig:
        return conformance.configuration_from_environment(self._environment())

    @staticmethod
    def _target() -> dict[str, str]:
        return {
            "image_reference": conformance.TARGET_IMAGE_REFERENCE,
            "image_digest": conformance.TARGET_IMAGE_DIGEST,
            "release": conformance.TARGET_RELEASE,
            "platform": "linux/arm64",
        }

    @staticmethod
    def _runner() -> dict[str, str]:
        return {"image_digest": "sha256:" + "a" * 64, "platform": "linux/amd64"}

    def test_configuration_requires_separate_local_rotation_authority_and_key_purposes(self) -> None:
        self.assertEqual(self._config().retention_days, 1)

        environment = self._environment()
        environment["HORMUZ_CEPH_OPENBAO_ADMIN_TOKEN"] = environment["HORMUZ_CEPH_OPENBAO_RUNTIME_TOKEN"]
        with self.assertRaises(conformance.ConformanceFailure) as raised:
            conformance.configuration_from_environment(environment)
        self.assertEqual(raised.exception.code, "rotation_administrator_not_separate")

        environment = self._environment()
        environment["HORMUZ_CEPH_OPENBAO_UNAVAILABLE_KEY"] = environment["HORMUZ_CEPH_OPENBAO_DATA_KEY"]
        with self.assertRaises(conformance.ConformanceFailure) as raised:
            conformance.configuration_from_environment(environment)
        self.assertEqual(raised.exception.code, "key_purposes_not_separated")

        environment = self._environment()
        environment["HORMUZ_CEPH_OPENBAO_ENDPOINT"] = "https://openbao.example.test"
        with self.assertRaises(conformance.ConformanceFailure) as raised:
            conformance.configuration_from_environment(environment)
        self.assertEqual(raised.exception.code, "local_endpoint_required")

    def test_pre_attested_target_and_runner_are_pinned(self) -> None:
        target_environment = {
            "HORMUZ_CEPH_RGW_TARGET_ATTESTED": "1",
            "HORMUZ_CEPH_RGW_TARGET_IMAGE_REFERENCE": conformance.TARGET_IMAGE_REFERENCE,
            "HORMUZ_CEPH_RGW_TARGET_IMAGE_DIGEST": conformance.TARGET_IMAGE_DIGEST,
            "HORMUZ_CEPH_RGW_TARGET_RELEASE": conformance.TARGET_RELEASE,
            "HORMUZ_CEPH_RGW_TARGET_PLATFORM": "linux/arm64",
        }
        self.assertEqual(conformance.attest_target_from_environment(target_environment), self._target())

        runner_environment = {
            "HORMUZ_CEPH_RGW_RUNNER_IMAGE_DIGEST": "sha256:" + "b" * 64,
            "HORMUZ_CEPH_RGW_RUNNER_PLATFORM": "linux/amd64",
        }
        self.assertEqual(conformance.attest_runner_from_environment(runner_environment)["platform"], "linux/amd64")

        target_environment["HORMUZ_CEPH_RGW_TARGET_RELEASE"] = "20.2.2"
        with self.assertRaises(conformance.ConformanceFailure) as raised:
            conformance.attest_target_from_environment(target_environment)
        self.assertEqual(raised.exception.code, "pre_attested_target_invalid")

    def test_same_named_key_versions_recover_pre_rotation_material_with_a_fresh_runtime(self) -> None:
        runtime_factory = _RecoveryRuntimeFactory()
        evidence = conformance.run_conformance(
            self._config(),
            attest_target=self._target,
            attest_runner=self._runner,
            runtime_factory=runtime_factory,
        )

        conformance.validate_evidence(evidence)
        self.assertEqual(evidence["checks"], list(conformance._REQUIRED_CHECKS))
        self.assertEqual(runtime_factory.transit.versions, {"provider-key": 2, "audit-key": 2})
        self.assertEqual(runtime_factory.transit.rotations, ["provider-key", "audit-key"])
        self.assertEqual(runtime_factory.transit.rewraps, 1)
        self.assertEqual(len(runtime_factory.providers), 2)
        self.assertEqual(runtime_factory.runtime_controls[0].denied_checks, ["provider-key", "audit-key"])
        self.assertEqual(runtime_factory.administrator_controls[0].administrator_checks, ["provider-key", "audit-key"])
        self.assertTrue(runtime_factory.client.objects)
        self.assertTrue(
            all(conformance._CREDENTIAL_FIXTURE not in stored.body for stored in runtime_factory.client.objects.values())
        )
        serialized = json.dumps(evidence, sort_keys=True)
        for forbidden in (
            conformance._CREDENTIAL_FIXTURE.decode("ascii"),
            "127.0.0.1",
            "hormuz-rotation-recovery",
            "runtime-token",
            "administrator-token",
            "test-secret-key",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_runtime_rotation_authority_fails_closed(self) -> None:
        with self.assertRaises(conformance.ConformanceFailure) as raised:
            conformance.run_conformance(
                self._config(),
                attest_target=self._target,
                attest_runner=self._runner,
                runtime_factory=_RecoveryRuntimeFactory(runtime_can_rotate=True),
            )
        self.assertEqual(raised.exception.code, "runtime_rotation_authorized")

    def test_rotation_administrator_data_key_authority_fails_closed(self) -> None:
        with self.assertRaises(conformance.ConformanceFailure) as raised:
            conformance.run_conformance(
                self._config(),
                attest_target=self._target,
                attest_runner=self._runner,
                runtime_factory=_RecoveryRuntimeFactory(administrator_can_use_data_keys=True),
            )
        self.assertEqual(raised.exception.code, "rotation_administrator_scope_unverified")

    def test_evidence_is_private_strict_and_not_overwritable(self) -> None:
        evidence = conformance.run_conformance(
            self._config(),
            attest_target=self._target,
            attest_runner=self._runner,
            runtime_factory=_RecoveryRuntimeFactory(),
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "evidence.json"
            conformance.write_evidence(output, evidence)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["status"], "passed")
            with self.assertRaises(conformance.ConformanceFailure) as raised:
                conformance.write_evidence(output, evidence)
            self.assertEqual(raised.exception.code, "evidence_output_exists")

            invalid = dict(evidence)
            invalid["checks"] = []
            with self.assertRaises(conformance.ConformanceFailure) as raised:
                conformance.validate_evidence(invalid)
            self.assertEqual(raised.exception.code, "evidence_invalid")

    def test_pinned_runner_launcher_cannot_mount_the_docker_socket_or_run_the_wrong_tool(self) -> None:
        launcher = ROOT / "tools" / "run_ceph_rgw_custody_rotation_recovery_container.sh"
        parsed = subprocess.run(["bash", "-n", str(launcher)], check=False, capture_output=True, text=True)
        self.assertEqual(parsed.returncode, 0, parsed.stderr)
        contents = launcher.read_text(encoding="utf-8")
        self.assertIn('readonly RUNNER_PLATFORM="linux/amd64"', contents)
        self.assertIn('--read-only', contents)
        self.assertIn('--cap-drop ALL', contents)
        self.assertIn('--security-opt no-new-privileges', contents)
        self.assertIn('--entrypoint python', contents)
        self.assertIn('/opt/hormuz/tools/verify_ceph_rgw_custody_rotation_recovery.py', contents)
        self.assertNotIn('/var/run/docker.sock', contents)

        dockerfile = (ROOT / "Dockerfile.ceph-rgw-conformance").read_text(encoding="utf-8")
        self.assertIn(
            "COPY tools/verify_ceph_rgw_custody_rotation_recovery.py "
            "./tools/verify_ceph_rgw_custody_rotation_recovery.py",
            dockerfile,
        )


if __name__ == "__main__":
    unittest.main()
