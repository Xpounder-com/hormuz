#!/usr/bin/env python3
"""Prove the optional Ceph RGW Object Lock custody target on a local host.

This is a release-gate harness, not a Hormuz runtime dependency.  It only
accepts a loopback RGW/OpenBao lab and attests the running RGW and OpenBao
containers to specific releases and immutable image digests before it writes
any retained test objects.  The resulting evidence record contains no
endpoint, bucket, organization, credential, prompt, or response data.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlparse

from hormuz.custody import (
    KEY_PURPOSE_DATA_ENCRYPTION,
    KEY_PURPOSE_PROVIDER_CREDENTIAL,
    EnvelopeCipher,
    build_audit_anchor_artifact,
    parse_audit_anchor_artifact,
    parse_envelope,
    serialize_audit_anchor_artifact,
)
from hormuz.openbao_custody import OpenBaoTransitDataKeyProvider, verify_openbao_transit_profile
from hormuz.self_hosted_custody import create_s3_compatible_object_lock_anchor_sink

try:
    from tools._verification_runtime import (
        is_sha256_digest,
        run_container_command,
        write_private_json_evidence,
    )
except ModuleNotFoundError:  # Direct execution resolves helpers beside this script.
    from _verification_runtime import (  # type: ignore[no-redef]
        is_sha256_digest,
        run_container_command,
        write_private_json_evidence,
    )


SCHEMA_ID = "hormuz.ceph-rgw-custody-conformance"
SCHEMA_VERSION = 3
_LEGACY_SCHEMA_VERSION = 1
_PREVIOUS_SCHEMA_VERSION = 2
TARGET_RELEASE = "20.2.3"
TARGET_IMAGE_DIGEST = "sha256:d195020de02512030118e772cef7859e92904e91eb4cb21acb503f8b94118137"
TARGET_IMAGE_REFERENCE = f"quay.io/ceph/ceph@{TARGET_IMAGE_DIGEST}"
TARGET_VERSION_OUTPUT = "ceph version 20.2.3 (06c2f9c35b67055a8a6fb99d1be236b3c4832ace) tentacle (stable)"
OPENBAO_TARGET_IMAGE_DIGEST = "sha256:436eaf9778cad75507ff70ea26ace30dcbe15606e619ac3823495663d7f7c115"
OPENBAO_TARGET_IMAGE_REFERENCE = f"openbao/openbao@{OPENBAO_TARGET_IMAGE_DIGEST}"
OPENBAO_TARGET_PLATFORM = "linux/arm64"
OPENBAO_TARGET_VERSION_OUTPUT = "OpenBao v2.5.4 (4f6d47246a053375271a5fd8af85c3b75695aa46), built 2026-05-20T16:08:53Z"
OPT_IN_ENV = "HORMUZ_RUN_CEPH_RGW_CUSTODY_CONFORMANCE"
CONFIRMATION_ENV = "HORMUZ_CEPH_RGW_CUSTODY_CONFIRMATION"
CONFIRMATION_VALUE = "I_UNDERSTAND_DISPOSABLE_OBJECT_LOCK_RETENTION"
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_CONTAINER_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_DENIED_CODES = frozenset(
    {
        "AccessDenied",
        "AccessDeniedException",
        "InvalidRequest",
        "MethodNotAllowed",
        "ObjectLockConflict",
        "ObjectLocked",
        "OperationNotPermitted",
    }
)
_PREVIOUS_REQUIRED_CHECKS = (
    "local_container_release_and_digest_attested",
    "openbao_tenant_bound_data_key_operations",
    "bucket_versioning_and_object_lock_configuration",
    "unprotected_control_version_deletion_permitted",
    "encrypted_metadata_only_audit_artifact_recovery",
    "compliance_retention_present",
    "retention_extension_permitted",
    "retention_reduction_denied",
    "protected_version_deletion_denied",
    "legal_hold_present",
)
_REQUIRED_CHECKS = (
    "local_container_release_and_digest_attested",
    "openbao_container_release_and_digest_attested",
    *_PREVIOUS_REQUIRED_CHECKS[1:],
)
_NONCLAIMS = (
    "not_production_immutability",
    "not_host_root_or_disk_administrator_protection",
    "not_multi_host_availability_or_recovery_certification",
)
_CURRENT_NONCLAIMS = _NONCLAIMS + ("not_native_arm64_runtime_conformance",)
_HEX_DIGEST = re.compile(r"[0-9a-f]{64}$")


class ConformanceFailure(RuntimeError):
    """A content-free failure that is safe to report to an operator."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ConformanceConfig:
    """Explicit inputs for one disposable, local Ceph RGW proof run."""

    rgw_endpoint: str
    region: str
    bucket: str
    prefix: str
    access_key: str
    secret_key: str
    rgw_container: str
    openbao_container: str
    openbao_endpoint: str
    openbao_token: str
    transit_mount: str
    provider_key: str
    data_key: str
    retention_days: int


@dataclass(frozen=True)
class ConformanceRuntime:
    """The live, explicit dependencies needed by the harness."""

    provider: OpenBaoTransitDataKeyProvider
    sink: Any
    client: Any


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


def configuration_from_environment(environ: Mapping[str, str]) -> ConformanceConfig:
    """Read the narrow opt-in configuration without echoing sensitive values."""

    if environ.get(OPT_IN_ENV) != "1":
        raise ConformanceFailure("not_opted_in")
    if environ.get(CONFIRMATION_ENV) != CONFIRMATION_VALUE:
        raise ConformanceFailure("retention_acknowledgement_required")

    rgw_endpoint = _required(environ, "HORMUZ_CEPH_RGW_ENDPOINT")
    openbao_endpoint = _required(environ, "HORMUZ_CEPH_OPENBAO_ENDPOINT")
    if not _loopback_endpoint(rgw_endpoint) or not _loopback_endpoint(openbao_endpoint):
        raise ConformanceFailure("local_endpoint_required")

    try:
        retention_days = int(environ.get("HORMUZ_CEPH_RGW_RETENTION_DAYS", "1"))
    except ValueError:
        raise ConformanceFailure("retention_days_invalid") from None
    # The live proof must extend retention after writing the compliant object.
    # Preserve a safety margin below the conventional 100-year S3 ceiling.
    if retention_days < 1 or retention_days > 36499:
        raise ConformanceFailure("retention_days_invalid")

    container = _required(environ, "HORMUZ_CEPH_RGW_CONTAINER")
    if not _CONTAINER_NAME.fullmatch(container):
        raise ConformanceFailure("rgw_container_invalid")
    openbao_container = _required(environ, "HORMUZ_CEPH_OPENBAO_CONTAINER")
    if not _CONTAINER_NAME.fullmatch(openbao_container):
        raise ConformanceFailure("openbao_container_invalid")

    prefix = environ.get("HORMUZ_CEPH_RGW_PREFIX", "hormuz/conformance").strip("/")
    if not prefix:
        raise ConformanceFailure("prefix_invalid")

    transit_mount = environ.get("HORMUZ_CEPH_OPENBAO_TRANSIT_MOUNT", "transit")
    provider_key = _required(environ, "HORMUZ_CEPH_OPENBAO_PROVIDER_KEY")
    data_key = _required(environ, "HORMUZ_CEPH_OPENBAO_DATA_KEY")
    if not transit_mount or "/" in transit_mount:
        raise ConformanceFailure("transit_mount_invalid")
    if provider_key == data_key:
        raise ConformanceFailure("key_purposes_not_separated")

    return ConformanceConfig(
        rgw_endpoint=rgw_endpoint,
        region=_required(environ, "HORMUZ_CEPH_RGW_REGION"),
        bucket=_required(environ, "HORMUZ_CEPH_RGW_BUCKET"),
        prefix=prefix,
        access_key=_required(environ, "HORMUZ_CEPH_RGW_ACCESS_KEY"),
        secret_key=_required(environ, "HORMUZ_CEPH_RGW_SECRET_KEY"),
        rgw_container=container,
        openbao_container=openbao_container,
        openbao_endpoint=openbao_endpoint,
        openbao_token=_required(environ, "HORMUZ_CEPH_OPENBAO_TOKEN"),
        transit_mount=transit_mount,
        provider_key=provider_key,
        data_key=data_key,
        retention_days=retention_days,
    )


def _command_output(command: Sequence[str]) -> str:
    """Run a fixed local-Docker read command without exposing its stderr."""

    try:
        completed = run_container_command(command, timeout_seconds=30)
    except (OSError, subprocess.SubprocessError, ValueError):
        raise ConformanceFailure("candidate_attestation_unavailable") from None
    if completed.returncode != 0:
        raise ConformanceFailure("candidate_attestation_unavailable") from None
    return completed.stdout.strip()


def attest_local_rgw_container(
    container: str,
    *,
    command_output: Callable[[Sequence[str]], str] = _command_output,
) -> dict[str, str]:
    """Bind the S3 endpoint to the exact local Ceph image under test.

    Cephadm normally runs RGW in a container.  A local Docker inspection is
    intentionally part of this first self-hosted target: it prevents a test
    against an arbitrary endpoint from being recorded as Tentacle evidence.
    """

    state_and_image = command_output(
        ["docker", "inspect", "--format", "{{.State.Running}}|{{.Image}}", container]
    )
    running, separator, image_id = state_and_image.partition("|")
    if running != "true" or not separator or not image_id.startswith("sha256:"):
        raise ConformanceFailure("candidate_container_unverified")

    digests_raw = command_output(["docker", "image", "inspect", "--format", "{{json .RepoDigests}}", image_id])
    try:
        digests = json.loads(digests_raw)
    except json.JSONDecodeError:
        raise ConformanceFailure("candidate_container_unverified") from None
    if not isinstance(digests, list) or TARGET_IMAGE_REFERENCE not in digests:
        raise ConformanceFailure("candidate_digest_mismatch")

    version = command_output(["docker", "exec", container, "ceph", "--version"])
    if version != TARGET_VERSION_OUTPUT:
        raise ConformanceFailure("candidate_release_mismatch")

    platform = command_output(["docker", "image", "inspect", "--format", "{{.Os}}/{{.Architecture}}", image_id])
    if platform not in {"linux/amd64", "linux/arm64"}:
        raise ConformanceFailure("candidate_platform_invalid")

    return {
        "image_reference": TARGET_IMAGE_REFERENCE,
        "image_digest": TARGET_IMAGE_DIGEST,
        "release": TARGET_RELEASE,
        "platform": platform,
    }


def attest_local_openbao_container(
    container: str,
    *,
    command_output: Callable[[Sequence[str]], str] = _command_output,
) -> dict[str, str]:
    """Bind the key authority to the exact local OpenBao image under test."""

    state_and_image = command_output(
        ["docker", "inspect", "--format", "{{.State.Running}}|{{.Image}}", container]
    )
    running, separator, image_id = state_and_image.partition("|")
    if running != "true" or not separator or not image_id.startswith("sha256:"):
        raise ConformanceFailure("openbao_container_unverified")

    digests_raw = command_output(["docker", "image", "inspect", "--format", "{{json .RepoDigests}}", image_id])
    try:
        digests = json.loads(digests_raw)
    except json.JSONDecodeError:
        raise ConformanceFailure("openbao_container_unverified") from None
    if not isinstance(digests, list) or OPENBAO_TARGET_IMAGE_REFERENCE not in digests:
        raise ConformanceFailure("openbao_digest_mismatch")

    version = command_output(["docker", "exec", container, "bao", "version"])
    if version != OPENBAO_TARGET_VERSION_OUTPUT:
        raise ConformanceFailure("openbao_release_mismatch")

    platform = command_output(["docker", "image", "inspect", "--format", "{{.Os}}/{{.Architecture}}", image_id])
    if platform != OPENBAO_TARGET_PLATFORM:
        raise ConformanceFailure("openbao_platform_mismatch")

    return {
        "image_reference": OPENBAO_TARGET_IMAGE_REFERENCE,
        "image_digest": OPENBAO_TARGET_IMAGE_DIGEST,
        "version": OPENBAO_TARGET_VERSION_OUTPUT,
        "platform": OPENBAO_TARGET_PLATFORM,
    }


def attest_target_from_environment(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Validate content-free target metadata pre-attested by the local launcher."""

    source = os.environ if environ is None else environ
    target = {
        "image_reference": source.get("HORMUZ_CEPH_RGW_TARGET_IMAGE_REFERENCE", ""),
        "image_digest": source.get("HORMUZ_CEPH_RGW_TARGET_IMAGE_DIGEST", ""),
        "release": source.get("HORMUZ_CEPH_RGW_TARGET_RELEASE", ""),
        "platform": source.get("HORMUZ_CEPH_RGW_TARGET_PLATFORM", ""),
    }
    if (
        target["image_reference"] != TARGET_IMAGE_REFERENCE
        or target["image_digest"] != TARGET_IMAGE_DIGEST
        or target["release"] != TARGET_RELEASE
        or target["platform"] not in {"linux/amd64", "linux/arm64"}
    ):
        raise ConformanceFailure("pre_attested_target_invalid")
    return target


def attest_configured_target(container: str) -> dict[str, str]:
    """Use host Docker attestation directly or a wrapper-provided attestation."""

    if os.environ.get("HORMUZ_CEPH_RGW_TARGET_ATTESTED") == "1":
        return attest_target_from_environment()
    return attest_local_rgw_container(container)


def attest_openbao_target_from_environment(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Validate content-free OpenBao target metadata pre-attested by the launcher."""

    source = os.environ if environ is None else environ
    target = {
        "image_reference": source.get("HORMUZ_CEPH_OPENBAO_TARGET_IMAGE_REFERENCE", ""),
        "image_digest": source.get("HORMUZ_CEPH_OPENBAO_TARGET_IMAGE_DIGEST", ""),
        "version": source.get("HORMUZ_CEPH_OPENBAO_TARGET_VERSION", ""),
        "platform": source.get("HORMUZ_CEPH_OPENBAO_TARGET_PLATFORM", ""),
    }
    if (
        source.get("HORMUZ_CEPH_OPENBAO_TARGET_ATTESTED") != "1"
        or target["image_reference"] != OPENBAO_TARGET_IMAGE_REFERENCE
        or target["image_digest"] != OPENBAO_TARGET_IMAGE_DIGEST
        or target["version"] != OPENBAO_TARGET_VERSION_OUTPUT
        or target["platform"] != OPENBAO_TARGET_PLATFORM
    ):
        raise ConformanceFailure("pre_attested_openbao_target_invalid")
    return target


def attest_configured_openbao_target(container: str) -> dict[str, str]:
    """Use host Docker attestation directly or a wrapper-provided OpenBao attestation."""

    if os.environ.get("HORMUZ_CEPH_OPENBAO_TARGET_ATTESTED") == "1":
        return attest_openbao_target_from_environment()
    return attest_local_openbao_container(container)


def attest_runner_from_environment(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Validate the pinned x86_64 runner metadata injected by the launcher.

    The container launcher derives the image ID after a local, content-addressed
    image build and passes it only for the current invocation. These values are
    evidence provenance, not credentials; strict validation prevents an
    accidental native-ARM run from being labeled as the x86_64 reference.
    """

    source = os.environ if environ is None else environ
    image_digest = source.get("HORMUZ_CEPH_RGW_RUNNER_IMAGE_DIGEST", "")
    platform = source.get("HORMUZ_CEPH_RGW_RUNNER_PLATFORM", "")
    if not is_sha256_digest(image_digest):
        raise ConformanceFailure("runner_attestation_invalid")
    if platform != "linux/amd64":
        raise ConformanceFailure("runner_platform_invalid")
    return {"image_digest": image_digest, "platform": platform}


def create_runtime(config: ConformanceConfig) -> ConformanceRuntime:
    """Construct explicit OpenBao and S3 clients; never use ambient AWS state."""

    provider = OpenBaoTransitDataKeyProvider(
        endpoint_url=config.openbao_endpoint,
        token=config.openbao_token,
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
    # The harness intentionally verifies the exact stored object version.  The
    # production sink keeps this dependency private because ordinary gateway
    # operation never needs a read-back API.
    return ConformanceRuntime(provider=provider, sink=sink, client=sink._client)  # noqa: SLF001


def _conformance_event() -> dict[str, object]:
    return {
        "schema_id": "hormuz.audit-event",
        "schema_version": 2,
        "event_type": "security.secret",
        "id": f"ceph-rgw-conformance-{uuid.uuid4()}",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "organization_id": "hormuz-ceph-rgw-conformance",
        "actor_id": "ceph-rgw-conformance",
        "actor_name": "Ceph RGW conformance",
        "team_id": "platform",
        "team_name": "Platform",
        "identity_type": "service_account",
        "authentication_source": "ceph-rgw-conformance",
        "client": "codex",
        "protocol": "openai",
        "requested_model": "conformance",
        "policy_version": "ceph-rgw-conformance",
        "coverage": "gateway_captured_requests_only",
        "action": "redacted",
        "detection_count": 0,
        "rules": [],
    }


def _require_mapping(value: object, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConformanceFailure(code)
    return value


def _require_object_version(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ConformanceFailure("object_version_missing")
    return value


def _retention_matches(value: object, expected: datetime) -> bool:
    if not isinstance(value, datetime) or value.tzinfo is None:
        return False
    return value >= expected - timedelta(seconds=5)


def _s3_error_code(error: BaseException) -> str | None:
    response = getattr(error, "response", None)
    if isinstance(response, Mapping):
        detail = response.get("Error")
        if isinstance(detail, Mapping):
            code = detail.get("Code")
            if isinstance(code, str):
                return code
    return None


def _expect_denied(operation: Callable[[], object], failure_code: str) -> None:
    try:
        operation()
    except Exception as error:
        if _s3_error_code(error) in _DENIED_CODES:
            return
        raise ConformanceFailure(failure_code) from None
    raise ConformanceFailure(failure_code)


def _prove_unprotected_control_delete_allowed(runtime: ConformanceRuntime, config: ConformanceConfig) -> None:
    """Separate Object Lock enforcement from a merely underprivileged credential."""

    control_key = f"{config.prefix}/control/{uuid.uuid4()}.bin"
    try:
        created = _require_mapping(
            runtime.client.put_object(
                Bucket=config.bucket,
                Key=control_key,
                Body=b"hormuz-ceph-rgw-control",
                ContentType="application/octet-stream",
            ),
            "control_write_invalid",
        )
        control_version = _require_object_version(created.get("VersionId"))
        runtime.client.delete_object(Bucket=config.bucket, Key=control_key, VersionId=control_version)
    except ConformanceFailure:
        raise
    except Exception:
        raise ConformanceFailure("control_delete_not_permitted") from None


def _prove_retention_extension_allowed(
    runtime: ConformanceRuntime,
    config: ConformanceConfig,
    *,
    object_key: str,
    version: str,
    retention_until: datetime,
) -> datetime:
    """Prove the credential may change retention before testing reduction denial."""

    extended_until = retention_until + timedelta(minutes=5)
    try:
        runtime.client.put_object_retention(
            Bucket=config.bucket,
            Key=object_key,
            VersionId=version,
            Retention={"Mode": "COMPLIANCE", "RetainUntilDate": extended_until},
        )
        retained = _require_mapping(
            runtime.client.get_object_retention(Bucket=config.bucket, Key=object_key, VersionId=version),
            "object_retention_invalid",
        )
    except ConformanceFailure:
        raise
    except Exception:
        raise ConformanceFailure("retention_extension_not_permitted") from None
    retention = _require_mapping(retained.get("Retention"), "object_retention_invalid")
    if retention.get("Mode") != "COMPLIANCE" or not _retention_matches(
        retention.get("RetainUntilDate"), extended_until
    ):
        raise ConformanceFailure("retention_extension_not_permitted")
    return extended_until


def _anchor_and_recover(
    runtime: ConformanceRuntime,
    config: ConformanceConfig,
    *,
    legal_hold: bool,
) -> tuple[dict[str, str], datetime, str, str]:
    organization_id = "hormuz-ceph-rgw-conformance"
    artifact = build_audit_anchor_artifact([_conformance_event()], organization_id=organization_id)
    encoded = serialize_audit_anchor_artifact(artifact)
    retention_until = datetime.now(timezone.utc) + timedelta(days=config.retention_days)
    receipt = runtime.sink.anchor(
        encoded,
        artifact_id=artifact["artifact_id"],
        organization_id=organization_id,
        head_digest=artifact["head_digest"],
        retention_until=retention_until,
        legal_hold=legal_hold,
    )
    version = _require_object_version(receipt.object_version)
    object_key = runtime.sink._object_key(  # noqa: SLF001 - retained-object verification is deliberate.
        organization_id=organization_id,
        artifact_id=receipt.artifact_id,
    )

    head = _require_mapping(
        runtime.client.head_object(Bucket=config.bucket, Key=object_key, VersionId=version),
        "object_head_invalid",
    )
    if head.get("ObjectLockMode") != "COMPLIANCE" or not _retention_matches(
        head.get("ObjectLockRetainUntilDate"), retention_until
    ):
        raise ConformanceFailure("compliance_retention_missing")
    metadata = _require_mapping(head.get("Metadata"), "object_metadata_missing")
    if not hmac.compare_digest(str(metadata.get("hormuz-artifact-sha256", "")), receipt.artifact_sha256) or not hmac.compare_digest(
        str(metadata.get("hormuz-head-digest", "")), receipt.head_digest
    ):
        raise ConformanceFailure("audit_metadata_mismatch")

    retained = _require_mapping(
        runtime.client.get_object_retention(Bucket=config.bucket, Key=object_key, VersionId=version),
        "object_retention_invalid",
    )
    retention = _require_mapping(retained.get("Retention"), "object_retention_invalid")
    if retention.get("Mode") != "COMPLIANCE" or not _retention_matches(retention.get("RetainUntilDate"), retention_until):
        raise ConformanceFailure("compliance_retention_missing")

    fetched = _require_mapping(
        runtime.client.get_object(Bucket=config.bucket, Key=object_key, VersionId=version),
        "artifact_recovery_invalid",
    )
    body = fetched.get("Body")
    read = getattr(body, "read", None)
    if not callable(read):
        raise ConformanceFailure("artifact_recovery_invalid")
    sealed = read()
    if not isinstance(sealed, bytes) or b"Ceph RGW conformance" in sealed:
        raise ConformanceFailure("artifact_encryption_invalid")
    recovered = EnvelopeCipher(runtime.provider).unseal(parse_envelope(sealed))
    parsed = parse_audit_anchor_artifact(recovered)
    if (
        serialize_audit_anchor_artifact(parsed) != encoded
        or parsed.get("head_digest") != receipt.head_digest
        or parsed.get("event_count") != 1
    ):
        raise ConformanceFailure("artifact_recovery_invalid")

    record = {
        "artifact_id": receipt.artifact_id,
        "artifact_sha256": receipt.artifact_sha256,
        "head_digest": receipt.head_digest,
        "object_version_sha256": hashlib.sha256(version.encode("utf-8")).hexdigest(),
    }
    return record, retention_until, object_key, version


def run_conformance(
    config: ConformanceConfig,
    *,
    attest: Callable[[str], dict[str, str]] = attest_configured_target,
    attest_openbao: Callable[[str], dict[str, str]] = attest_configured_openbao_target,
    attest_runner: Callable[[], dict[str, str]] = attest_runner_from_environment,
    runtime_factory: Callable[[ConformanceConfig], ConformanceRuntime] = create_runtime,
) -> dict[str, object]:
    """Run the full live proof and return a strict, content-free record."""

    target = attest(config.rgw_container)
    openbao_target = attest_openbao(config.openbao_container)
    runner = attest_runner()
    runtime = runtime_factory(config)
    verified_purposes = verify_openbao_transit_profile(
        runtime.provider,
        {
            KEY_PURPOSE_PROVIDER_CREDENTIAL: config.provider_key,
            KEY_PURPOSE_DATA_ENCRYPTION: config.data_key,
        },
        organization_id="hormuz-ceph-rgw-conformance",
    )
    if verified_purposes != 2:
        raise ConformanceFailure("openbao_key_verification_incomplete")
    runtime.sink.verify_configuration()
    _prove_unprotected_control_delete_allowed(runtime, config)

    compliance_record, retention_until, object_key, version = _anchor_and_recover(
        runtime, config, legal_hold=False
    )
    _prove_retention_extension_allowed(
        runtime,
        config,
        object_key=object_key,
        version=version,
        retention_until=retention_until,
    )
    _expect_denied(
        lambda: runtime.client.put_object_retention(
            Bucket=config.bucket,
            Key=object_key,
            VersionId=version,
            Retention={
                "Mode": "COMPLIANCE",
                "RetainUntilDate": retention_until,
            },
        ),
        "retention_reduction_not_denied",
    )
    _expect_denied(
        lambda: runtime.client.delete_object(Bucket=config.bucket, Key=object_key, VersionId=version),
        "protected_delete_not_denied",
    )

    legal_hold_record, _, legal_hold_key, legal_hold_version = _anchor_and_recover(runtime, config, legal_hold=True)
    legal_hold = _require_mapping(
        runtime.client.get_object_legal_hold(
            Bucket=config.bucket,
            Key=legal_hold_key,
            VersionId=legal_hold_version,
        ),
        "legal_hold_invalid",
    )
    hold = _require_mapping(legal_hold.get("LegalHold"), "legal_hold_invalid")
    if hold.get("Status") != "ON":
        raise ConformanceFailure("legal_hold_missing")

    return {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "status": "passed",
        "executed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "scope": "single_host_rgw_enforcement_only",
        "target": target,
        "openbao_target": openbao_target,
        "runner": runner,
        "checks": list(_REQUIRED_CHECKS),
        "retained_artifacts": [compliance_record, legal_hold_record],
        "retention_days": config.retention_days,
        "nonclaims": list(_CURRENT_NONCLAIMS),
    }


def validate_evidence(evidence: Mapping[str, object]) -> None:
    """Fail closed unless a record matches the strict v1, v2, or current v3 shape."""

    version = evidence.get("schema_version")
    expected_keys = {
        "schema_id",
        "schema_version",
        "status",
        "executed_at",
        "scope",
        "target",
        "checks",
        "retained_artifacts",
        "retention_days",
        "nonclaims",
    }
    expected_nonclaims = _NONCLAIMS
    expected_checks = _PREVIOUS_REQUIRED_CHECKS
    if version == SCHEMA_VERSION:
        expected_keys.add("runner")
        expected_keys.add("openbao_target")
        expected_nonclaims = _CURRENT_NONCLAIMS
        expected_checks = _REQUIRED_CHECKS
    elif version == _PREVIOUS_SCHEMA_VERSION:
        expected_keys.add("runner")
        expected_nonclaims = _CURRENT_NONCLAIMS
    elif version != _LEGACY_SCHEMA_VERSION:
        raise ConformanceFailure("evidence_invalid")
    if set(evidence) != expected_keys:
        raise ConformanceFailure("evidence_invalid")
    checks = evidence.get("checks")
    nonclaims = evidence.get("nonclaims")
    if (
        evidence.get("schema_id") != SCHEMA_ID
        or evidence.get("status") != "passed"
        or evidence.get("scope") != "single_host_rgw_enforcement_only"
        or not isinstance(evidence.get("executed_at"), str)
        or not isinstance(checks, list)
        or checks != list(expected_checks)
        or not isinstance(nonclaims, list)
        or nonclaims != list(expected_nonclaims)
    ):
        raise ConformanceFailure("evidence_invalid")
    executed_at = str(evidence["executed_at"])
    try:
        parsed_time = datetime.fromisoformat(executed_at.replace("Z", "+00:00"))
    except ValueError:
        raise ConformanceFailure("evidence_invalid") from None
    if not executed_at.endswith("Z") or parsed_time.tzinfo is None or parsed_time.utcoffset() != timedelta(0):
        raise ConformanceFailure("evidence_invalid")

    target = _require_mapping(evidence.get("target"), "evidence_invalid")
    if (
        set(target) != {"image_reference", "image_digest", "release", "platform"}
        or target.get("image_reference") != TARGET_IMAGE_REFERENCE
        or target.get("image_digest") != TARGET_IMAGE_DIGEST
        or target.get("release") != TARGET_RELEASE
        or target.get("platform") not in {"linux/amd64", "linux/arm64"}
    ):
        raise ConformanceFailure("evidence_invalid")

    if version in {_PREVIOUS_SCHEMA_VERSION, SCHEMA_VERSION}:
        runner = _require_mapping(evidence.get("runner"), "evidence_invalid")
        if (
            set(runner) != {"image_digest", "platform"}
            or not is_sha256_digest(runner.get("image_digest"))
            or runner.get("platform") != "linux/amd64"
        ):
            raise ConformanceFailure("evidence_invalid")

    if version == SCHEMA_VERSION:
        openbao_target = _require_mapping(evidence.get("openbao_target"), "evidence_invalid")
        if dict(openbao_target) != {
            "image_reference": OPENBAO_TARGET_IMAGE_REFERENCE,
            "image_digest": OPENBAO_TARGET_IMAGE_DIGEST,
            "version": OPENBAO_TARGET_VERSION_OUTPUT,
            "platform": OPENBAO_TARGET_PLATFORM,
        }:
            raise ConformanceFailure("evidence_invalid")

    retention_days = evidence.get("retention_days")
    if not isinstance(retention_days, int) or isinstance(retention_days, bool) or not 1 <= retention_days <= 36500:
        raise ConformanceFailure("evidence_invalid")
    artifacts = evidence.get("retained_artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != 2:
        raise ConformanceFailure("evidence_invalid")
    for artifact in artifacts:
        value = _require_mapping(artifact, "evidence_invalid")
        if set(value) != {"artifact_id", "artifact_sha256", "head_digest", "object_version_sha256"}:
            raise ConformanceFailure("evidence_invalid")
        try:
            uuid.UUID(str(value.get("artifact_id")))
        except (TypeError, ValueError, AttributeError):
            raise ConformanceFailure("evidence_invalid") from None
        if not (
            isinstance(value.get("artifact_sha256"), str)
            and _HEX_DIGEST.fullmatch(value["artifact_sha256"])
            and isinstance(value.get("head_digest"), str)
            and _HEX_DIGEST.fullmatch(value["head_digest"])
            and isinstance(value.get("object_version_sha256"), str)
            and _HEX_DIGEST.fullmatch(value["object_version_sha256"])
        ):
            raise ConformanceFailure("evidence_invalid")
    artifact_ids = [str(_require_mapping(item, "evidence_invalid")["artifact_id"]) for item in artifacts]
    version_hashes = [str(_require_mapping(item, "evidence_invalid")["object_version_sha256"]) for item in artifacts]
    if len(set(artifact_ids)) != len(artifact_ids) or len(set(version_hashes)) != len(version_hashes):
        raise ConformanceFailure("evidence_invalid")


def write_evidence(path: Path, evidence: Mapping[str, object]) -> None:
    """Atomically write a private, content-free JSON evidence record."""

    validate_evidence(evidence)
    write_private_json_evidence(path, evidence)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the optional local Ceph RGW Object Lock custody conformance gate."
    )
    parser.add_argument(
        "--evidence-out",
        required=True,
        type=Path,
        help="private output path for the content-free successful evidence JSON",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        config = configuration_from_environment(os.environ)
        evidence = run_conformance(config)
        write_evidence(args.evidence_out, evidence)
    except ConformanceFailure as error:
        print(f"ceph_rgw_custody_conformance=failed code={error.code}", file=sys.stderr)
        return 1
    except Exception:
        print("ceph_rgw_custody_conformance=failed code=conformance_runtime_failed", file=sys.stderr)
        return 1
    print("ceph_rgw_custody_conformance=passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
