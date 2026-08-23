#!/usr/bin/env python3
"""Prove self-hosted encrypted custody recovery after Transit key rotation.

This opt-in release-gate harness extends the certified local Ceph RGW/OpenBao
reference with one narrow recovery proof. It creates only synthetic, in-memory
provider-credential fixture data and a metadata-only audit artifact, rotates
the same named Transit keys with a separately supplied lab administrator token,
then validates recovery through fresh data-plane clients. The durable output is
a strict content-free evidence record only.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlparse

from hormuz.custody import (
    KEY_PURPOSE_DATA_ENCRYPTION,
    KEY_PURPOSE_PROVIDER_CREDENTIAL,
    CustodyError,
    EncryptedEnvelope,
    EnvelopeCipher,
    build_audit_anchor_artifact,
    parse_audit_anchor_artifact,
    parse_envelope,
    serialize_audit_anchor_artifact,
    serialize_envelope,
)
from hormuz.openbao_custody import (
    OpenBaoTransitDataKeyProvider,
    OpenBaoTransitKeyRotationControl,
    verify_openbao_transit_profile,
)
from hormuz.self_hosted_custody import create_s3_compatible_object_lock_anchor_sink

try:
    from tools._verification_runtime import is_sha256_digest, write_private_json_evidence
except ModuleNotFoundError:  # Direct execution resolves helpers beside this script.
    from _verification_runtime import is_sha256_digest, write_private_json_evidence  # type: ignore[no-redef]


SCHEMA_ID = "hormuz.ceph-rgw-custody-rotation-recovery"
SCHEMA_VERSION = 1
SCOPE = "single_host_ceph_rgw_openbao_transit_rotation_recovery_only"
TARGET_RELEASE = "20.2.3"
TARGET_IMAGE_DIGEST = "sha256:d195020de02512030118e772cef7859e92904e91eb4cb21acb503f8b94118137"
TARGET_IMAGE_REFERENCE = f"quay.io/ceph/ceph@{TARGET_IMAGE_DIGEST}"
RUNNER_PLATFORM = "linux/amd64"
OPT_IN_ENV = "HORMUZ_RUN_CEPH_CUSTODY_ROTATION_RECOVERY"
CONFIRMATION_ENV = "HORMUZ_CEPH_CUSTODY_ROTATION_RECOVERY_CONFIRMATION"
CONFIRMATION_VALUE = "I_UNDERSTAND_DISPOSABLE_OBJECT_LOCK_RETENTION_AND_TRANSIT_ROTATION"
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_KEY_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_HEX_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_REQUIRED_CHECKS = (
    "runtime_rotation_capability_denied",
    "rotation_administrator_scope_verified",
    "purpose_separated_data_keys_verified",
    "pre_rotation_provider_envelope_recovered",
    "mutable_provider_envelope_rewrapped",
    "pre_rotation_audit_artifact_recovered_and_verified",
    "unavailable_key_fails_closed",
    "tenant_context_mismatch_fails_closed",
    "altered_encrypted_material_fails_closed",
    "invalid_audit_chain_fails_closed",
)
_DURATION_KEYS = (
    "pre_rotation_setup",
    "key_version_rotation",
    "fresh_recovery",
    "failure_checks",
    "total",
)
_NONCLAIMS = (
    "not_openbao_backend_backup_or_master_key_recovery",
    "not_customer_rpo_rto_or_production_key_management_certification",
    "not_host_root_or_disk_administrator_protection",
    "not_multi_host_availability_or_disaster_recovery_certification",
    "not_native_arm64_runtime_conformance",
)
_CREDENTIAL_FIXTURE = b"hormuz-disposable-custody-rotation-fixture"
_ORGANIZATION_ID = "hormuz-ceph-custody-rotation-recovery"


class ConformanceFailure(RuntimeError):
    """A stable, content-free failure safe for release-gate reporting."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RecoveryConfig:
    """Explicit disposable inputs for one local Ceph/OpenBao recovery proof."""

    rgw_endpoint: str
    region: str
    bucket: str
    prefix: str
    access_key: str
    secret_key: str
    openbao_endpoint: str
    runtime_token: str
    administrator_token: str
    transit_mount: str
    provider_key: str
    data_key: str
    unavailable_key: str
    retention_days: int


@dataclass(frozen=True)
class RecoveryRuntime:
    """Live dependencies created once for the isolated conformance run."""

    provider: OpenBaoTransitDataKeyProvider
    runtime_rotation_control: OpenBaoTransitKeyRotationControl
    administrator_rotation_control: OpenBaoTransitKeyRotationControl
    sink: Any


def _required(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name, "")
    if not isinstance(value, str) or not value:
        raise ConformanceFailure("configuration_missing")
    return value


def _loopback_endpoint(value: str) -> bool:
    parsed = urlparse(value)
    return bool(
        parsed.scheme in {"http", "https"}
        and parsed.hostname in _LOOPBACK_HOSTS
        and parsed.netloc
        and parsed.path in {"", "/"}
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


def _key_name(value: str, *, code: str) -> str:
    if not _KEY_NAME.fullmatch(value):
        raise ConformanceFailure(code)
    return value


def configuration_from_environment(environ: Mapping[str, str]) -> RecoveryConfig:
    """Load one deliberately narrow, local, explicitly acknowledged run."""

    if environ.get(OPT_IN_ENV) != "1":
        raise ConformanceFailure("not_opted_in")
    if environ.get(CONFIRMATION_ENV) != CONFIRMATION_VALUE:
        raise ConformanceFailure("retention_and_rotation_acknowledgement_required")
    rgw_endpoint = _required(environ, "HORMUZ_CEPH_RGW_ENDPOINT")
    openbao_endpoint = _required(environ, "HORMUZ_CEPH_OPENBAO_ENDPOINT")
    if not _loopback_endpoint(rgw_endpoint) or not _loopback_endpoint(openbao_endpoint):
        raise ConformanceFailure("local_endpoint_required")
    try:
        retention_days = int(environ.get("HORMUZ_CEPH_RGW_RETENTION_DAYS", "1"))
    except ValueError:
        raise ConformanceFailure("retention_days_invalid") from None
    if retention_days < 1 or retention_days > 36499:
        raise ConformanceFailure("retention_days_invalid")
    prefix = environ.get("HORMUZ_CEPH_RGW_PREFIX", "hormuz/rotation-recovery").strip("/")
    if not prefix:
        raise ConformanceFailure("prefix_invalid")
    mount = _key_name(environ.get("HORMUZ_CEPH_OPENBAO_TRANSIT_MOUNT", "transit"), code="transit_mount_invalid")
    provider_key = _key_name(_required(environ, "HORMUZ_CEPH_OPENBAO_PROVIDER_KEY"), code="key_name_invalid")
    data_key = _key_name(_required(environ, "HORMUZ_CEPH_OPENBAO_DATA_KEY"), code="key_name_invalid")
    unavailable_key = _key_name(_required(environ, "HORMUZ_CEPH_OPENBAO_UNAVAILABLE_KEY"), code="key_name_invalid")
    if len({provider_key, data_key, unavailable_key}) != 3:
        raise ConformanceFailure("key_purposes_not_separated")
    runtime_token = _required(environ, "HORMUZ_CEPH_OPENBAO_RUNTIME_TOKEN")
    administrator_token = _required(environ, "HORMUZ_CEPH_OPENBAO_ADMIN_TOKEN")
    if hmac.compare_digest(runtime_token, administrator_token):
        raise ConformanceFailure("rotation_administrator_not_separate")
    return RecoveryConfig(
        rgw_endpoint=rgw_endpoint,
        region=_required(environ, "HORMUZ_CEPH_RGW_REGION"),
        bucket=_required(environ, "HORMUZ_CEPH_RGW_BUCKET"),
        prefix=prefix,
        access_key=_required(environ, "HORMUZ_CEPH_RGW_ACCESS_KEY"),
        secret_key=_required(environ, "HORMUZ_CEPH_RGW_SECRET_KEY"),
        openbao_endpoint=openbao_endpoint,
        runtime_token=runtime_token,
        administrator_token=administrator_token,
        transit_mount=mount,
        provider_key=provider_key,
        data_key=data_key,
        unavailable_key=unavailable_key,
        retention_days=retention_days,
    )


def attest_target_from_environment(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Require the container launcher to attest the exact local Ceph target."""

    source = os.environ if environ is None else environ
    target = {
        "image_reference": source.get("HORMUZ_CEPH_RGW_TARGET_IMAGE_REFERENCE", ""),
        "image_digest": source.get("HORMUZ_CEPH_RGW_TARGET_IMAGE_DIGEST", ""),
        "release": source.get("HORMUZ_CEPH_RGW_TARGET_RELEASE", ""),
        "platform": source.get("HORMUZ_CEPH_RGW_TARGET_PLATFORM", ""),
    }
    if (
        source.get("HORMUZ_CEPH_RGW_TARGET_ATTESTED") != "1"
        or target["image_reference"] != TARGET_IMAGE_REFERENCE
        or target["image_digest"] != TARGET_IMAGE_DIGEST
        or target["release"] != TARGET_RELEASE
        or target["platform"] not in {"linux/amd64", "linux/arm64"}
    ):
        raise ConformanceFailure("pre_attested_target_invalid")
    return target


def attest_runner_from_environment(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Require an immutable x86_64 runner provenance record from the launcher."""

    source = os.environ if environ is None else environ
    digest = source.get("HORMUZ_CEPH_RGW_RUNNER_IMAGE_DIGEST", "")
    platform = source.get("HORMUZ_CEPH_RGW_RUNNER_PLATFORM", "")
    if not is_sha256_digest(digest):
        raise ConformanceFailure("runner_attestation_invalid")
    if platform != RUNNER_PLATFORM:
        raise ConformanceFailure("runner_platform_invalid")
    return {"image_digest": digest, "platform": platform}


def create_runtime(config: RecoveryConfig) -> RecoveryRuntime:
    """Construct a data-plane client and a separately credentialed admin control."""

    provider = OpenBaoTransitDataKeyProvider(
        endpoint_url=config.openbao_endpoint,
        token=config.runtime_token,
        mount=config.transit_mount,
    )
    sink = create_s3_compatible_object_lock_anchor_sink(
        endpoint_url=config.rgw_endpoint,
        region=config.region,
        bucket=config.bucket,
        prefix=config.prefix,
        access_key=config.access_key,
        secret_key=config.secret_key,
        key_provider=provider,
        encryption_key_reference=config.data_key,
    )
    return RecoveryRuntime(
        provider=provider,
        runtime_rotation_control=OpenBaoTransitKeyRotationControl(
            endpoint_url=config.openbao_endpoint,
            token=config.runtime_token,
            mount=config.transit_mount,
        ),
        administrator_rotation_control=OpenBaoTransitKeyRotationControl(
            endpoint_url=config.openbao_endpoint,
            token=config.administrator_token,
            mount=config.transit_mount,
        ),
        sink=sink,
    )


def _conformance_event() -> dict[str, object]:
    return {
        "schema_id": "hormuz.audit-event",
        "schema_version": 2,
        "event_type": "security.secret",
        "id": f"ceph-custody-rotation-{uuid.uuid4()}",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "organization_id": _ORGANIZATION_ID,
        "actor_id": "ceph-custody-rotation",
        "actor_name": "Ceph custody rotation conformance",
        "team_id": "platform",
        "team_name": "Platform",
        "identity_type": "service_account",
        "authentication_source": "ceph-custody-rotation",
        "client": "codex",
        "protocol": "openai",
        "requested_model": "conformance",
        "policy_version": "ceph-custody-rotation",
        "coverage": "gateway_captured_requests_only",
        "action": "redacted",
        "detection_count": 0,
        "rules": [],
    }


def _duration_ms(started_ns: int) -> int:
    return (time.monotonic_ns() - started_ns) // 1_000_000


def _expect_custody_failure(operation: Callable[[], object], failure_code: str) -> None:
    try:
        operation()
    except CustodyError:
        return
    raise ConformanceFailure(failure_code)


def _require_runtime_rotation_denied(runtime: RecoveryRuntime, key_reference: str) -> None:
    try:
        runtime.runtime_rotation_control.assert_rotation_denied(key_reference=key_reference)
    except CustodyError as error:
        if error.code == "openbao_custody_runtime_rotation_authorized":
            raise ConformanceFailure("runtime_rotation_authorized") from None
        raise ConformanceFailure("runtime_rotation_capability_unverified") from None


def _require_rotation_administrator_scope(runtime: RecoveryRuntime, key_reference: str) -> None:
    try:
        runtime.administrator_rotation_control.assert_rotation_only_administrator(key_reference=key_reference)
    except CustodyError:
        raise ConformanceFailure("rotation_administrator_scope_unverified") from None


def _rotate_key_versions(runtime: RecoveryRuntime, config: RecoveryConfig) -> None:
    try:
        runtime.administrator_rotation_control.rotate_key_version(key_reference=config.provider_key)
        runtime.administrator_rotation_control.rotate_key_version(key_reference=config.data_key)
    except CustodyError:
        raise ConformanceFailure("key_version_rotation_failed") from None


def _require_key_profile(runtime: RecoveryRuntime, config: RecoveryConfig) -> None:
    try:
        verified = verify_openbao_transit_profile(
            runtime.provider,
            {
                KEY_PURPOSE_PROVIDER_CREDENTIAL: config.provider_key,
                KEY_PURPOSE_DATA_ENCRYPTION: config.data_key,
            },
            organization_id=_ORGANIZATION_ID,
        )
        runtime.sink.verify_configuration()
    except CustodyError:
        raise ConformanceFailure("runtime_profile_unverified") from None
    if verified != 2:
        raise ConformanceFailure("runtime_profile_unverified")


def _failure_checks(
    cipher: EnvelopeCipher,
    envelope: EncryptedEnvelope,
    artifact: bytes,
    config: RecoveryConfig,
) -> None:
    _expect_custody_failure(
        lambda: cipher.unseal(replace(envelope, key_reference=config.unavailable_key)),
        "unavailable_key_not_denied",
    )
    _expect_custody_failure(
        lambda: cipher.unseal(replace(envelope, organization_id="other-tenant")),
        "tenant_context_mismatch_not_denied",
    )
    ciphertext = envelope.ciphertext
    if not ciphertext:
        raise ConformanceFailure("pre_rotation_envelope_invalid")
    _expect_custody_failure(
        lambda: cipher.unseal(replace(envelope, ciphertext=ciphertext[:-1] + bytes([ciphertext[-1] ^ 1]))),
        "altered_encrypted_material_not_denied",
    )
    try:
        corrupted = json.loads(artifact.decode("utf-8"))
        entries = corrupted["entries"]
        entries[0]["event"]["actor_id"] = "corrupted-recovery-fixture"
        parse_audit_anchor_artifact(json.dumps(corrupted, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    except CustodyError:
        return
    except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
        raise ConformanceFailure("pre_rotation_artifact_invalid") from None
    raise ConformanceFailure("invalid_audit_chain_not_denied")


def run_conformance(
    config: RecoveryConfig,
    *,
    attest_target: Callable[[], dict[str, str]] = attest_target_from_environment,
    attest_runner: Callable[[], dict[str, str]] = attest_runner_from_environment,
    runtime_factory: Callable[[RecoveryConfig], RecoveryRuntime] = create_runtime,
) -> dict[str, object]:
    """Run the bounded pre-rotation encryption, rotation, and fresh recovery proof."""

    total_started = time.monotonic_ns()
    target = attest_target()
    runner = attest_runner()
    runtime = runtime_factory(config)
    _require_runtime_rotation_denied(runtime, config.provider_key)
    _require_runtime_rotation_denied(runtime, config.data_key)
    _require_rotation_administrator_scope(runtime, config.provider_key)
    _require_rotation_administrator_scope(runtime, config.data_key)
    _require_key_profile(runtime, config)
    cipher = EnvelopeCipher(runtime.provider)
    envelope = cipher.seal(
        _CREDENTIAL_FIXTURE,
        organization_id=_ORGANIZATION_ID,
        purpose=KEY_PURPOSE_PROVIDER_CREDENTIAL,
        key_reference=config.provider_key,
    )
    serialized_envelope = serialize_envelope(envelope)
    artifact = build_audit_anchor_artifact([_conformance_event()], organization_id=_ORGANIZATION_ID)
    encoded_artifact = serialize_audit_anchor_artifact(artifact)
    try:
        receipt = runtime.sink.anchor(
            encoded_artifact,
            artifact_id=artifact["artifact_id"],
            organization_id=_ORGANIZATION_ID,
            head_digest=artifact["head_digest"],
            retention_until=datetime.now(timezone.utc) + timedelta(days=config.retention_days),
            legal_hold=False,
        )
    except CustodyError:
        raise ConformanceFailure("pre_rotation_audit_anchor_failed") from None
    if not isinstance(receipt.object_version, str) or not receipt.object_version:
        raise ConformanceFailure("pre_rotation_audit_anchor_failed")
    setup_ms = _duration_ms(total_started)

    rotation_started = time.monotonic_ns()
    _rotate_key_versions(runtime, config)
    rotation_ms = _duration_ms(rotation_started)

    recovery_started = time.monotonic_ns()
    recovery_runtime = runtime_factory(config)
    recovery_cipher = EnvelopeCipher(recovery_runtime.provider)
    try:
        recovered_secret = recovery_cipher.unseal(parse_envelope(serialized_envelope))
        rewrapped = recovery_cipher.rewrap(envelope, destination_key_reference=config.provider_key)
        rewrapped_secret = recovery_cipher.unseal(rewrapped)
        recovered_artifact = recovery_runtime.sink.recover(receipt, organization_id=_ORGANIZATION_ID)
    except CustodyError:
        raise ConformanceFailure("fresh_recovery_failed") from None
    if (
        not hmac.compare_digest(recovered_secret, _CREDENTIAL_FIXTURE)
        or not hmac.compare_digest(rewrapped_secret, _CREDENTIAL_FIXTURE)
        or rewrapped.key_reference != config.provider_key
        or hmac.compare_digest(rewrapped.encrypted_data_key, envelope.encrypted_data_key)
        or not hmac.compare_digest(recovered_artifact, encoded_artifact)
    ):
        raise ConformanceFailure("fresh_recovery_failed")
    recovery_ms = _duration_ms(recovery_started)

    failures_started = time.monotonic_ns()
    _failure_checks(recovery_cipher, envelope, recovered_artifact, config)
    failure_ms = _duration_ms(failures_started)
    return {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "status": "passed",
        "executed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "scope": SCOPE,
        "target": target,
        "runner": runner,
        "checks": list(_REQUIRED_CHECKS),
        "durations_ms": {
            "pre_rotation_setup": setup_ms,
            "key_version_rotation": rotation_ms,
            "fresh_recovery": recovery_ms,
            "failure_checks": failure_ms,
            "total": _duration_ms(total_started),
        },
        "retention_days": config.retention_days,
        "nonclaims": list(_NONCLAIMS),
    }


def validate_evidence(evidence: Mapping[str, object]) -> None:
    """Reject every incomplete, non-pinned, or content-bearing evidence shape."""

    if set(evidence) != {
        "schema_id",
        "schema_version",
        "status",
        "executed_at",
        "scope",
        "target",
        "runner",
        "checks",
        "durations_ms",
        "retention_days",
        "nonclaims",
    }:
        raise ConformanceFailure("evidence_invalid")
    if (
        evidence.get("schema_id") != SCHEMA_ID
        or evidence.get("schema_version") != SCHEMA_VERSION
        or evidence.get("status") != "passed"
        or evidence.get("scope") != SCOPE
        or evidence.get("checks") != list(_REQUIRED_CHECKS)
        or evidence.get("nonclaims") != list(_NONCLAIMS)
    ):
        raise ConformanceFailure("evidence_invalid")
    timestamp = evidence.get("executed_at")
    if not isinstance(timestamp, str) or not timestamp.endswith("Z"):
        raise ConformanceFailure("evidence_invalid")
    try:
        parsed_timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        raise ConformanceFailure("evidence_invalid") from None
    if parsed_timestamp.tzinfo is None or parsed_timestamp.utcoffset() != timedelta(0):
        raise ConformanceFailure("evidence_invalid")
    target = evidence.get("target")
    if not isinstance(target, Mapping) or dict(target) != {
        "image_reference": TARGET_IMAGE_REFERENCE,
        "image_digest": TARGET_IMAGE_DIGEST,
        "release": TARGET_RELEASE,
        "platform": target.get("platform"),
    } or target.get("platform") not in {"linux/amd64", "linux/arm64"}:
        raise ConformanceFailure("evidence_invalid")
    runner = evidence.get("runner")
    if (
        not isinstance(runner, Mapping)
        or set(runner) != {"image_digest", "platform"}
        or not is_sha256_digest(runner.get("image_digest"))
        or runner.get("platform") != RUNNER_PLATFORM
    ):
        raise ConformanceFailure("evidence_invalid")
    durations = evidence.get("durations_ms")
    if (
        not isinstance(durations, Mapping)
        or set(durations) != set(_DURATION_KEYS)
        or any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in durations.values())
    ):
        raise ConformanceFailure("evidence_invalid")
    retention_days = evidence.get("retention_days")
    if not isinstance(retention_days, int) or isinstance(retention_days, bool) or not 1 <= retention_days <= 36499:
        raise ConformanceFailure("evidence_invalid")


def write_evidence(path: Path, evidence: Mapping[str, object]) -> None:
    """Atomically publish one private, strict, content-free success record."""

    validate_evidence(evidence)
    if path.exists() or path.is_symlink():
        raise ConformanceFailure("evidence_output_exists")
    try:
        write_private_json_evidence(path, evidence)
    except OSError:
        raise ConformanceFailure("evidence_write_failed") from None


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-out", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        evidence = run_conformance(configuration_from_environment(os.environ))
        write_evidence(args.evidence_out, evidence)
    except ConformanceFailure as error:
        print(f"ceph_custody_rotation_recovery=failed code={error.code}", file=sys.stderr)
        return 1
    except Exception:
        print("ceph_custody_rotation_recovery=failed code=conformance_runtime_failed", file=sys.stderr)
        return 1
    print("ceph_custody_rotation_recovery=passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
