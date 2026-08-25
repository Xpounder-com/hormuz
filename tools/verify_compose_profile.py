#!/usr/bin/env python3
"""Validate Hormuz's bounded single-VM Compose contract and proof evidence."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_ID = "hormuz.compose-reference-proof"
SCHEMA_VERSION = 1
CONTRACT_SCHEMA = "hormuz.compose-reference.v1"
PROFILE = "single-linux-vm-pilot"
PLATFORM = "linux/amd64"
HORMUZ_IMAGE = (
    "ghcr.io/xpounder-com/hormuz@"
    "sha256:1bbcca3490a7a5b004a880f42e8250acb91ce566a9c59f3263d7b279568efb5a"
)
POSTGRES_IMAGE = (
    "postgres@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777"
)
EXPECTED_GATEWAY_SECRETS = {
    "anthropic_api_key",
    "hormuz_identity_token",
    "hormuz_ingress_credential",
    "openai_api_key",
    "postgres_runtime_dsn",
}
EXPECTED_SECRET_FILES = {
    "anthropic_api_key": "runtime/secrets/anthropic-api-key",
    "hormuz_identity_token": "runtime/secrets/hormuz-identity-token",
    "hormuz_ingress_credential": "runtime/secrets/hormuz-ingress-credential",
    "openai_api_key": "runtime/secrets/openai-api-key",
    "postgres_migration_dsn": "runtime/secrets/postgres-migration-dsn",
    "postgres_runtime_dsn": "runtime/secrets/postgres-runtime-dsn",
    "postgres_runtime_password": "runtime/secrets/postgres-runtime-password",
    "postgres_superuser_password": "runtime/secrets/postgres-superuser-password",
}
EXPECTED_CONFIG_FILES = {
    "fake_provider": "verification/fake_provider.py",
    "gateway_healthcheck": "scripts/healthcheck.py",
    "hormuz_config": "runtime/hormuz.json",
    "postgres_init": "scripts/postgres-init.sh",
    "postgres_entrypoint": "scripts/postgres-entrypoint.sh",
    "run_hormuz": "scripts/run-hormuz.sh",
}
EXPECTED_CHECKS = {
    "architecture_gate",
    "authenticated_ingress",
    "backup_restore",
    "clean_removal",
    "configuration_replacement",
    "configuration_rollback",
    "delayed_backup_restore",
    "durable_metadata_evidence",
    "fake_provider_traffic",
    "immutable_images",
    "policy_enforcement",
    "protected_read_only_secret_mounts",
    "restart_persistence",
    "secret_non_disclosure",
    "single_gateway_replica",
    "startup_readiness",
    "static_contract",
}
EXPECTED_LIMITATIONS = [
    "customer_tls_and_network_operations_not_certified",
    "docker_linux_and_postgresql_not_certified",
    "host_failure_can_interrupt_service",
    "no_disaster_recovery_claim",
    "no_failure_domain_isolation",
    "no_high_availability_claim",
    "no_zero_downtime_upgrade_claim",
    "single_linux_vm_and_single_gateway_replica",
]
SHA256_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
VERSION_PATTERN = re.compile(r"[0-9]+(?:\.[0-9]+){0,3}(?:[-+][0-9A-Za-z.-]+)?\Z")
FORBIDDEN_EVIDENCE = (
    re.compile(r"/Users/"),
    re.compile(r"/home/runner/"),
    re.compile(r"/private/tmp/"),
    re.compile(r"postgres(?:ql)?://", re.IGNORECASE),
    re.compile(r"\bBearer\s+", re.IGNORECASE),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\b"),
)


class ComposeProfileError(RuntimeError):
    """Raised when a Compose model or proof violates the public contract."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    model_parser = subparsers.add_parser("validate-model")
    model_parser.add_argument("--model", required=True, type=Path)
    model_parser.add_argument(
        "--mode",
        required=True,
        choices=(
            "bundled",
            "external",
            "bundled-operations",
            "external-operations",
            "verification",
        ),
    )
    model_parser.add_argument("--secret-root", type=Path)

    evidence_parser = subparsers.add_parser("validate-evidence")
    evidence_parser.add_argument("--evidence", required=True, type=Path)

    scan_parser = subparsers.add_parser("assert-no-secrets")
    scan_parser.add_argument("--artifact", action="append", type=Path, default=[])
    scan_parser.add_argument("--artifact-root", type=Path)
    scan_parser.add_argument("--secret-root", required=True, type=Path)

    write_parser = subparsers.add_parser("write-evidence")
    write_parser.add_argument("--output", required=True, type=Path)
    write_parser.add_argument("--os-version", required=True)
    write_parser.add_argument("--docker-engine", required=True)
    write_parser.add_argument("--docker-compose", required=True)
    write_parser.add_argument("--hormuz-repo-digest", required=True)
    write_parser.add_argument("--hormuz-image-id", required=True)
    write_parser.add_argument("--postgres-repo-digest", required=True)
    write_parser.add_argument("--postgres-image-id", required=True)
    write_parser.add_argument("--requests-before-restart", required=True, type=int)
    write_parser.add_argument("--requests-after-restart", required=True, type=int)
    write_parser.add_argument("--usage-events-before-restart", required=True, type=int)
    write_parser.add_argument("--usage-events-at-backup", required=True, type=int)
    write_parser.add_argument("--usage-events-after-backup", required=True, type=int)
    write_parser.add_argument("--restored-usage-events", required=True, type=int)

    args = parser.parse_args(argv)
    try:
        if args.command == "validate-model":
            model = load_json(args.model)
            secret_values = read_secret_values(args.secret_root) if args.secret_root else ()
            validate_compose_model(model, mode=args.mode, secret_values=secret_values)
            print(
                "verified Compose model: "
                f"contract={CONTRACT_SCHEMA} mode={args.mode} platform={PLATFORM}"
            )
        elif args.command == "validate-evidence":
            evidence = load_json(args.evidence)
            validate_evidence(evidence)
            print(
                "verified content-free Compose proof: "
                f"profile={PROFILE} verdict={evidence['verdict']}"
            )
        elif args.command == "assert-no-secrets":
            artifacts = list(args.artifact)
            if args.artifact_root is not None:
                if not args.artifact_root.is_dir() or args.artifact_root.is_symlink():
                    raise ComposeProfileError("proof_artifact_root_invalid")
                artifacts.extend(sorted(path for path in args.artifact_root.rglob("*") if path.is_file()))
            if not artifacts:
                raise ComposeProfileError("proof_artifact_missing")
            assert_no_secrets(artifacts, secret_values=read_secret_values(args.secret_root))
            print(f"verified secret non-disclosure across {len(artifacts)} artifacts")
        else:
            evidence = build_evidence(
                os_version=args.os_version,
                docker_engine=args.docker_engine,
                docker_compose=args.docker_compose,
                hormuz_repo_digest=args.hormuz_repo_digest,
                hormuz_image_id=args.hormuz_image_id,
                postgres_repo_digest=args.postgres_repo_digest,
                postgres_image_id=args.postgres_image_id,
                requests_before_restart=args.requests_before_restart,
                requests_after_restart=args.requests_after_restart,
                usage_events_before_restart=args.usage_events_before_restart,
                usage_events_at_backup=args.usage_events_at_backup,
                usage_events_after_backup=args.usage_events_after_backup,
                restored_usage_events=args.restored_usage_events,
            )
            validate_evidence(evidence)
            write_evidence(args.output, evidence)
            print(f"wrote content-free Compose proof: {args.output}")
    except (ComposeProfileError, OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        print(f"Compose profile verification failed: {error}", file=sys.stderr)
        return 1
    return 0


def load_json(path: Path) -> dict[str, Any]:
    """Read an object while rejecting duplicate keys."""

    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ComposeProfileError("duplicate_json_key")
            value[key] = item
        return value

    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=object_pairs)
    if not isinstance(value, dict):
        raise ComposeProfileError("json_root_not_object")
    return value


def read_secret_values(root: Path) -> tuple[str, ...]:
    """Read only local proof sentinels so rendered output can be checked for leaks."""

    if not root.is_dir() or root.is_symlink():
        raise ComposeProfileError("secret_root_invalid")
    values: list[str] = []
    for path in sorted(root.iterdir()):
        if path.is_symlink() or not path.is_file():
            raise ComposeProfileError("secret_file_invalid")
        value = path.read_text(encoding="utf-8").strip()
        if not value:
            raise ComposeProfileError("secret_file_empty")
        values.append(value)
    if not values:
        raise ComposeProfileError("secret_root_empty")
    return tuple(values)


def assert_no_secrets(paths: Sequence[Path], *, secret_values: Sequence[str]) -> None:
    """Reject proof artifacts containing any mounted secret or synthetic payload."""

    forbidden = tuple(value.encode("utf-8") for value in secret_values) + (
        b"sk-proj-CCCCCCCCCCCCCCCCCCCCCCCC",
    )
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise ComposeProfileError("proof_artifact_invalid")
        content = path.read_bytes()
        if any(value in content for value in forbidden):
            raise ComposeProfileError("proof_artifact_contains_secret_value")


def build_evidence(
    *,
    os_version: str,
    docker_engine: str,
    docker_compose: str,
    hormuz_repo_digest: str,
    hormuz_image_id: str,
    postgres_repo_digest: str,
    postgres_image_id: str,
    requests_before_restart: int,
    requests_after_restart: int,
    usage_events_before_restart: int,
    usage_events_at_backup: int,
    usage_events_after_backup: int,
    restored_usage_events: int,
) -> dict[str, Any]:
    """Build the only supported content-free live-proof summary."""

    return {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "profile": PROFILE,
        "contract": CONTRACT_SCHEMA,
        "runner": {
            "os": "linux",
            "os_version": os_version,
            "architecture": "amd64",
            "docker_engine": docker_engine,
            "docker_compose": docker_compose,
        },
        "images": {
            "hormuz": {
                "reference": HORMUZ_IMAGE,
                "repo_digest": hormuz_repo_digest,
                "image_id": hormuz_image_id,
            },
            "postgres": {
                "reference": POSTGRES_IMAGE,
                "repo_digest": postgres_repo_digest,
                "image_id": postgres_image_id,
            },
        },
        "checks": {name: True for name in sorted(EXPECTED_CHECKS)},
        "state": {
            "requests_before_restart": requests_before_restart,
            "requests_after_restart": requests_after_restart,
            "usage_events_before_restart": usage_events_before_restart,
            "usage_events_at_backup": usage_events_at_backup,
            "usage_events_after_backup": usage_events_after_backup,
            "restored_usage_events": restored_usage_events,
        },
        "interruption": {
            "gateway_restart_observed": True,
            "host_failure_can_interrupt": True,
            "single_gateway_replica": True,
        },
        "limitations": EXPECTED_LIMITATIONS,
        "verdict": "verified_single_vm_pilot_reference",
    }


def write_evidence(path: Path, evidence: Mapping[str, Any]) -> None:
    """Create rather than replace one protected proof summary."""

    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as error:
        raise ComposeProfileError("evidence_output_exists") from error
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(evidence, stream, sort_keys=True, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def validate_compose_model(
    model: Mapping[str, Any],
    *,
    mode: str,
    secret_values: Sequence[str] = (),
) -> None:
    """Validate the fully merged Docker Compose JSON model."""

    if mode not in {
        "bundled",
        "external",
        "bundled-operations",
        "external-operations",
        "verification",
    }:
        raise ComposeProfileError("compose_mode_unsupported")
    verification = mode == "verification"
    operations = mode.endswith("-operations")
    base_mode = "bundled" if verification else mode.removesuffix("-operations")
    contract = _mapping(model.get("x-hormuz-contract"), "compose_contract_missing")
    if contract != {
        "schema": CONTRACT_SCHEMA,
        "profile": PROFILE,
        "platform": PLATFORM,
        "hormuz_image": HORMUZ_IMAGE,
        "postgres_image": POSTGRES_IMAGE,
    }:
        raise ComposeProfileError("compose_contract_mismatch")

    serialized = json.dumps(model, sort_keys=True, separators=(",", ":"), allow_nan=False)
    for secret in secret_values:
        if secret in serialized:
            raise ComposeProfileError("compose_model_contains_secret_value")

    services = _mapping(model.get("services"), "compose_services_missing")
    expected_services = {"gateway", "postgres"} if base_mode == "bundled" else {"gateway"}
    if operations:
        expected_services.add("migrate")
    if verification:
        expected_services.add("fake-provider")
    if set(services) != expected_services:
        raise ComposeProfileError("compose_service_boundary_invalid")
    _validate_gateway(
        _mapping(services["gateway"], "gateway_service_invalid"),
        mode=base_mode,
        verification=verification,
    )
    if operations:
        _validate_migrate(_mapping(services["migrate"], "migration_service_invalid"), mode=base_mode)
    if verification:
        _validate_fake_provider(_mapping(services["fake-provider"], "fake_provider_service_invalid"))

    networks = _mapping(model.get("networks"), "compose_networks_missing")
    expected_networks = {"database", "egress"} if base_mode == "bundled" else {"egress"}
    if verification:
        expected_networks.add("proof-provider")
    if set(networks) != expected_networks:
        raise ComposeProfileError("compose_network_boundary_invalid")
    if base_mode == "bundled" and _mapping(networks["database"], "database_network_invalid").get("internal") is not True:
        raise ComposeProfileError("database_network_not_internal")
    if _mapping(networks["egress"], "egress_network_invalid").get("internal") is True:
        raise ComposeProfileError("egress_network_unusable")
    if verification and _mapping(
        networks["proof-provider"], "proof_provider_network_invalid"
    ).get("internal") is not True:
        raise ComposeProfileError("proof_provider_network_not_internal")

    if base_mode == "bundled":
        _validate_postgres(_mapping(services["postgres"], "postgres_service_invalid"))
        volumes = _mapping(model.get("volumes"), "compose_volume_missing")
        if set(volumes) != {"postgres-data"}:
            raise ComposeProfileError("postgres_volume_boundary_invalid")
    elif model.get("volumes") not in (None, {}):
        raise ComposeProfileError("external_mode_retains_bundled_volume")

    top_level_secrets = _mapping(model.get("secrets"), "compose_secrets_missing")
    if base_mode == "bundled":
        expected_secrets = EXPECTED_GATEWAY_SECRETS | {
            "postgres_runtime_password",
            "postgres_superuser_password",
        }
    else:
        expected_secrets = set(EXPECTED_GATEWAY_SECRETS)
    if operations:
        expected_secrets.add("postgres_migration_dsn")
    if set(top_level_secrets) != expected_secrets:
        raise ComposeProfileError("compose_secret_boundary_invalid")
    _validate_file_sources(
        top_level_secrets,
        expected={name: EXPECTED_SECRET_FILES[name] for name in expected_secrets},
        kind="secret",
    )

    top_level_configs = _mapping(model.get("configs"), "compose_configs_missing")
    expected_config_names = {"gateway_healthcheck", "hormuz_config", "run_hormuz"}
    if base_mode == "bundled":
        expected_config_names.update({"postgres_entrypoint", "postgres_init"})
    if verification:
        expected_config_names.add("fake_provider")
    if set(top_level_configs) != expected_config_names:
        raise ComposeProfileError("compose_config_boundary_invalid")
    _validate_file_sources(
        top_level_configs,
        expected={name: EXPECTED_CONFIG_FILES[name] for name in expected_config_names},
        kind="config",
    )


def _validate_gateway(
    service: Mapping[str, Any],
    *,
    mode: str,
    verification: bool,
) -> None:
    _immutable_image(service.get("image"), HORMUZ_IMAGE, "gateway_image_invalid")
    if service.get("platform") != PLATFORM:
        raise ComposeProfileError("gateway_platform_invalid")
    if service.get("user") != "65532:65532" or service.get("read_only") is not True:
        raise ComposeProfileError("gateway_runtime_identity_invalid")
    _protected_file_group(service, "gateway")
    _reject_privilege_expansion(service, role="gateway", allowed_cap_add=set())
    if service.get("init") is not True or set(_strings(service.get("cap_drop"), "gateway_cap_drop_invalid")) != {"ALL"}:
        raise ComposeProfileError("gateway_process_boundary_invalid")
    if _strings(service.get("security_opt"), "gateway_security_opt_invalid") != ["no-new-privileges:true"]:
        raise ComposeProfileError("gateway_new_privileges_allowed")
    if service.get("restart") != "unless-stopped":
        raise ComposeProfileError("gateway_restart_policy_invalid")
    if service.get("volumes") not in (None, []):
        raise ComposeProfileError("gateway_has_writable_volume")
    tmpfs = _strings(service.get("tmpfs"), "gateway_tmpfs_invalid")
    if tmpfs != ["/tmp:rw,noexec,nosuid,nodev,mode=1777,size=64m"]:
        raise ComposeProfileError("gateway_tmpfs_invalid")
    if _positive_number(service.get("cpus"), "gateway_cpu_limit_invalid") != 2:
        raise ComposeProfileError("gateway_cpu_limit_invalid")
    if _positive_bytes(service.get("mem_limit"), "gateway_memory_limit_invalid") != 2 * 1024**3:
        raise ComposeProfileError("gateway_memory_limit_invalid")
    if _positive_integer(service.get("pids_limit"), "gateway_pid_limit_invalid") != 256:
        raise ComposeProfileError("gateway_pid_limit_invalid")
    if service.get("stop_grace_period") != "11m0s":
        raise ComposeProfileError("gateway_graceful_stop_invalid")

    ports = _sequence(service.get("ports"), "gateway_port_invalid")
    if len(ports) != 1:
        raise ComposeProfileError("gateway_port_invalid")
    port = _mapping(ports[0], "gateway_port_invalid")
    if (
        port.get("host_ip") != "127.0.0.1"
        or port.get("target") != 8787
        or str(port.get("published")) != "8787"
        or port.get("protocol") != "tcp"
    ):
        raise ComposeProfileError("gateway_port_not_loopback_restricted")
    expected_networks = {"database", "egress"} if mode == "bundled" else {"egress"}
    if verification:
        expected_networks.add("proof-provider")
    if set(_mapping(service.get("networks"), "gateway_networks_invalid")) != expected_networks:
        raise ComposeProfileError("gateway_networks_invalid")
    expected_dependencies: dict[str, dict[str, object]] = {}
    if mode == "bundled":
        expected_dependencies["postgres"] = {
            "condition": "service_healthy",
            "restart": True,
            "required": True,
        }
    if verification:
        expected_dependencies["fake-provider"] = {
            "condition": "service_healthy",
            "restart": True,
            "required": True,
        }
    if _mapping(service.get("depends_on", {}), "gateway_dependencies_invalid") != expected_dependencies:
        raise ComposeProfileError("gateway_dependencies_invalid")

    if service.get("environment") != {"HORMUZ_CONFIG": "/etc/hormuz/hormuz.json"}:
        raise ComposeProfileError("gateway_environment_boundary_invalid")
    if service.get("command") != ["serve"]:
        raise ComposeProfileError("gateway_command_invalid")
    if service.get("entrypoint") != ["/bin/sh", "/opt/hormuz-compose/run-hormuz.sh"]:
        raise ComposeProfileError("gateway_entrypoint_invalid")
    secret_mounts = _source_targets(service.get("secrets"), "gateway_secret_mount_invalid")
    if secret_mounts != {
        name: f"/run/secrets/{name}" for name in EXPECTED_GATEWAY_SECRETS
    }:
        raise ComposeProfileError("gateway_secret_mount_invalid")
    if _source_targets(service.get("configs"), "gateway_config_mount_invalid") != {
        "gateway_healthcheck": "/opt/hormuz-compose/healthcheck.py",
        "hormuz_config": "/etc/hormuz/hormuz.json",
        "run_hormuz": "/opt/hormuz-compose/run-hormuz.sh",
    }:
        raise ComposeProfileError("gateway_config_mount_invalid")
    health = _mapping(service.get("healthcheck"), "gateway_healthcheck_missing")
    if health != {
        "test": ["CMD", "/opt/hormuz/bin/python", "-I", "/opt/hormuz-compose/healthcheck.py"],
        "timeout": "5s",
        "interval": "10s",
        "retries": 6,
        "start_period": "15s",
    }:
        raise ComposeProfileError("gateway_healthcheck_invalid")
    labels = _mapping(service.get("labels"), "gateway_labels_invalid")
    if labels.get("io.hormuz.role") != "gateway" or labels.get("io.hormuz.compose-contract") != CONTRACT_SCHEMA:
        raise ComposeProfileError("gateway_role_label_invalid")


def _validate_postgres(service: Mapping[str, Any]) -> None:
    _immutable_image(service.get("image"), POSTGRES_IMAGE, "postgres_image_invalid")
    if service.get("platform") != PLATFORM or service.get("read_only") is not True:
        raise ComposeProfileError("postgres_runtime_boundary_invalid")
    if service.get("user") not in (None, ""):
        raise ComposeProfileError("postgres_startup_identity_invalid")
    if service.get("group_add") not in (None, []):
        raise ComposeProfileError("postgres_supplemental_group_invalid")
    _reject_privilege_expansion(
        service,
        role="postgres",
        allowed_cap_add={"CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID"},
    )
    if set(_strings(service.get("cap_drop"), "postgres_cap_drop_invalid")) != {"ALL"}:
        raise ComposeProfileError("postgres_cap_drop_invalid")
    if _strings(service.get("security_opt"), "postgres_security_opt_invalid") != ["no-new-privileges:true"]:
        raise ComposeProfileError("postgres_new_privileges_allowed")
    if service.get("restart") != "unless-stopped" or service.get("stop_grace_period") != "1m0s":
        raise ComposeProfileError("postgres_restart_policy_invalid")
    if service.get("ports") not in (None, []):
        raise ComposeProfileError("postgres_port_published")
    if set(_mapping(service.get("networks"), "postgres_network_invalid")) != {"database"}:
        raise ComposeProfileError("postgres_network_invalid")
    volumes = _sequence(service.get("volumes"), "postgres_volume_missing")
    if len(volumes) != 1:
        raise ComposeProfileError("postgres_volume_invalid")
    volume = _mapping(volumes[0], "postgres_volume_invalid")
    if (
        volume.get("source") != "postgres-data"
        or volume.get("target") != "/var/lib/postgresql/data"
        or volume.get("type") != "volume"
        or volume.get("volume") != {}
    ):
        raise ComposeProfileError("postgres_volume_invalid")
    if _source_targets(service.get("configs"), "postgres_config_mount_invalid") != {
        "postgres_entrypoint": "/opt/hormuz-compose/postgres-entrypoint.sh",
        "postgres_init": "/docker-entrypoint-initdb.d/10-hormuz-roles.sh",
    }:
        raise ComposeProfileError("postgres_config_mount_invalid")
    if _source_targets(service.get("secrets"), "postgres_secret_mount_invalid") != {
        "postgres_runtime_password": "/run/secrets/postgres_runtime_password",
        "postgres_superuser_password": "/run/secrets/postgres_superuser_password",
    }:
        raise ComposeProfileError("postgres_secret_mount_invalid")
    if service.get("environment") != {
        "PGDATA": "/var/lib/postgresql/data/pgdata",
        "POSTGRES_DB": "hormuz",
        "POSTGRES_PASSWORD_FILE": "/run/hormuz-bootstrap-secrets/postgres_superuser_password",
        "POSTGRES_USER": "postgres",
    }:
        raise ComposeProfileError("postgres_environment_boundary_invalid")
    if service.get("entrypoint") != ["/bin/sh", "/opt/hormuz-compose/postgres-entrypoint.sh"]:
        raise ComposeProfileError("postgres_entrypoint_invalid")
    if service.get("command") != ["postgres"]:
        raise ComposeProfileError("postgres_command_invalid")
    if _strings(service.get("tmpfs"), "postgres_tmpfs_invalid") != [
        "/tmp:rw,noexec,nosuid,nodev,mode=1777,size=64m",
        "/var/run/postgresql:rw,nosuid,nodev,mode=3775,size=16m",
        "/run/hormuz-bootstrap-secrets:rw,noexec,nosuid,nodev,mode=0700,size=1m",
    ]:
        raise ComposeProfileError("postgres_tmpfs_invalid")
    health = _mapping(service.get("healthcheck"), "postgres_healthcheck_missing")
    if health != {
        "test": [
            "CMD-SHELL",
            "test -f /var/lib/postgresql/data/pgdata/.hormuz-roles-initialized "
            "&& pg_isready --username postgres --dbname hormuz",
        ],
        "timeout": "3s",
        "interval": "5s",
        "retries": 20,
        "start_period": "10s",
    }:
        raise ComposeProfileError("postgres_healthcheck_invalid")
    if _positive_number(service.get("cpus"), "postgres_cpu_limit_invalid") != 2:
        raise ComposeProfileError("postgres_cpu_limit_invalid")
    if _positive_bytes(service.get("mem_limit"), "postgres_memory_limit_invalid") != 2 * 1024**3:
        raise ComposeProfileError("postgres_memory_limit_invalid")
    if _positive_integer(service.get("pids_limit"), "postgres_pid_limit_invalid") != 256:
        raise ComposeProfileError("postgres_pid_limit_invalid")
    if _positive_bytes(service.get("shm_size"), "postgres_shm_size_invalid") != 256 * 1024**2:
        raise ComposeProfileError("postgres_shm_size_invalid")
    labels = _mapping(service.get("labels"), "postgres_labels_invalid")
    if labels.get("io.hormuz.role") != "database" or labels.get("io.hormuz.compose-contract") != CONTRACT_SCHEMA:
        raise ComposeProfileError("postgres_role_label_invalid")


def _validate_migrate(service: Mapping[str, Any], *, mode: str) -> None:
    _immutable_image(service.get("image"), HORMUZ_IMAGE, "migration_image_invalid")
    if service.get("platform") != PLATFORM:
        raise ComposeProfileError("migration_platform_invalid")
    if service.get("user") != "65532:65532" or service.get("read_only") is not True:
        raise ComposeProfileError("migration_runtime_identity_invalid")
    _protected_file_group(service, "migration")
    _reject_privilege_expansion(service, role="migration", allowed_cap_add=set())
    if service.get("init") is not True or set(_strings(service.get("cap_drop"), "migration_cap_drop_invalid")) != {"ALL"}:
        raise ComposeProfileError("migration_process_boundary_invalid")
    if _strings(service.get("security_opt"), "migration_security_opt_invalid") != ["no-new-privileges:true"]:
        raise ComposeProfileError("migration_new_privileges_allowed")
    if service.get("ports") not in (None, []) or service.get("volumes") not in (None, []):
        raise ComposeProfileError("migration_mount_or_port_invalid")
    expected_networks = {"database", "egress"} if mode == "bundled" else {"egress"}
    if set(_mapping(service.get("networks"), "migration_networks_invalid")) != expected_networks:
        raise ComposeProfileError("migration_networks_invalid")
    expected_dependencies = (
        {
            "postgres": {
                "condition": "service_healthy",
                "restart": True,
                "required": True,
            }
        }
        if mode == "bundled"
        else {}
    )
    if _mapping(service.get("depends_on", {}), "migration_dependencies_invalid") != expected_dependencies:
        raise ComposeProfileError("migration_dependencies_invalid")
    if service.get("profiles") != ["operations"]:
        raise ComposeProfileError("migration_profile_invalid")
    if service.get("command") != ["storage", "migrate"]:
        raise ComposeProfileError("migration_command_invalid")
    if service.get("entrypoint") != ["/bin/sh", "/opt/hormuz-compose/run-hormuz.sh"]:
        raise ComposeProfileError("migration_entrypoint_invalid")
    if service.get("environment") != {
        "HORMUZ_CONFIG": "/etc/hormuz/hormuz.json",
        "HORMUZ_TOKEN": "compose-operator-identity-placeholder-not-a-credential",
        "HORMUZ_INGRESS_CREDENTIAL": "compose-operator-ingress-placeholder-not-a-credential",
        "OPENAI_API_KEY": "compose-operator-openai-placeholder-not-a-credential",
        "ANTHROPIC_API_KEY": "compose-operator-anthropic-placeholder-not-a-credential",
    }:
        raise ComposeProfileError("migration_environment_boundary_invalid")
    if _source_targets(service.get("secrets"), "migration_secret_mount_invalid") != {
        "postgres_migration_dsn": "/run/secrets/postgres_migration_dsn",
        "postgres_runtime_dsn": "/run/secrets/postgres_runtime_dsn",
    }:
        raise ComposeProfileError("migration_secret_mount_invalid")
    if _source_targets(service.get("configs"), "migration_config_mount_invalid") != {
        "hormuz_config": "/etc/hormuz/hormuz.json",
        "run_hormuz": "/opt/hormuz-compose/run-hormuz.sh",
    }:
        raise ComposeProfileError("migration_config_mount_invalid")
    if _strings(service.get("tmpfs"), "migration_tmpfs_invalid") != [
        "/tmp:rw,noexec,nosuid,nodev,mode=1777,size=64m"
    ]:
        raise ComposeProfileError("migration_tmpfs_invalid")
    if _positive_number(service.get("cpus"), "migration_cpu_limit_invalid") != 1:
        raise ComposeProfileError("migration_cpu_limit_invalid")
    if _positive_bytes(service.get("mem_limit"), "migration_memory_limit_invalid") != 1024**3:
        raise ComposeProfileError("migration_memory_limit_invalid")
    if _positive_integer(service.get("pids_limit"), "migration_pid_limit_invalid") != 128:
        raise ComposeProfileError("migration_pid_limit_invalid")
    labels = _mapping(service.get("labels"), "migration_labels_invalid")
    if labels.get("io.hormuz.role") != "migration" or labels.get("io.hormuz.compose-contract") != CONTRACT_SCHEMA:
        raise ComposeProfileError("migration_role_label_invalid")


def _validate_fake_provider(service: Mapping[str, Any]) -> None:
    _immutable_image(service.get("image"), HORMUZ_IMAGE, "fake_provider_image_invalid")
    if service.get("platform") != PLATFORM:
        raise ComposeProfileError("fake_provider_platform_invalid")
    if service.get("user") != "65532:65532" or service.get("read_only") is not True:
        raise ComposeProfileError("fake_provider_runtime_identity_invalid")
    _reject_privilege_expansion(service, role="fake_provider", allowed_cap_add=set())
    if service.get("init") is not True:
        raise ComposeProfileError("fake_provider_init_invalid")
    if set(_strings(service.get("cap_drop"), "fake_provider_cap_drop_invalid")) != {"ALL"}:
        raise ComposeProfileError("fake_provider_cap_drop_invalid")
    if _strings(service.get("security_opt"), "fake_provider_security_opt_invalid") != [
        "no-new-privileges:true"
    ]:
        raise ComposeProfileError("fake_provider_new_privileges_allowed")
    for field in ("environment", "ports", "secrets", "tmpfs", "volumes"):
        if service.get(field) not in (None, [], {}):
            raise ComposeProfileError("fake_provider_external_surface_invalid")
    if set(_mapping(service.get("networks"), "fake_provider_network_invalid")) != {
        "proof-provider"
    }:
        raise ComposeProfileError("fake_provider_network_invalid")
    if service.get("entrypoint") != [
        "/opt/hormuz/bin/python",
        "-I",
        "/opt/hormuz-compose/fake-provider.py",
    ]:
        raise ComposeProfileError("fake_provider_entrypoint_invalid")
    if service.get("command") not in (None, []):
        raise ComposeProfileError("fake_provider_command_invalid")
    if _source_targets(service.get("configs"), "fake_provider_config_mount_invalid") != {
        "fake_provider": "/opt/hormuz-compose/fake-provider.py"
    }:
        raise ComposeProfileError("fake_provider_config_mount_invalid")
    if _positive_number(service.get("cpus"), "fake_provider_cpu_limit_invalid") != 0.5:
        raise ComposeProfileError("fake_provider_cpu_limit_invalid")
    if _positive_bytes(service.get("mem_limit"), "fake_provider_memory_limit_invalid") != 256 * 1024**2:
        raise ComposeProfileError("fake_provider_memory_limit_invalid")
    if _positive_integer(service.get("pids_limit"), "fake_provider_pid_limit_invalid") != 64:
        raise ComposeProfileError("fake_provider_pid_limit_invalid")
    health = _mapping(service.get("healthcheck"), "fake_provider_healthcheck_missing")
    if health != {
        "test": [
            "CMD",
            "/opt/hormuz/bin/python",
            "-I",
            "-c",
            "from urllib.request import urlopen; raise SystemExit(0 if urlopen('http://127.0.0.1:8090/health', timeout=2).status == 200 else 1)",
        ],
        "timeout": "2s",
        "interval": "2s",
        "retries": 15,
        "start_period": "2s",
    }:
        raise ComposeProfileError("fake_provider_healthcheck_invalid")
    labels = _mapping(service.get("labels"), "fake_provider_labels_invalid")
    if (
        labels.get("io.hormuz.role") != "disposable-proof-provider"
        or labels.get("io.hormuz.compose-contract") != CONTRACT_SCHEMA
    ):
        raise ComposeProfileError("fake_provider_role_label_invalid")


def validate_evidence(value: Mapping[str, Any]) -> None:
    """Validate one strict metadata-only live proof summary."""

    _keys(
        value,
        {
            "schema_id",
            "schema_version",
            "observed_at",
            "profile",
            "contract",
            "runner",
            "images",
            "checks",
            "state",
            "interruption",
            "limitations",
            "verdict",
        },
        "evidence_root",
    )
    if value["schema_id"] != SCHEMA_ID or value["schema_version"] != SCHEMA_VERSION:
        raise ComposeProfileError("evidence_schema_unsupported")
    if value["profile"] != PROFILE or value["contract"] != CONTRACT_SCHEMA:
        raise ComposeProfileError("evidence_profile_invalid")
    _timestamp(value["observed_at"])
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if any(pattern.search(encoded) for pattern in FORBIDDEN_EVIDENCE):
        raise ComposeProfileError("evidence_contains_forbidden_content")

    runner = _mapping(value["runner"], "evidence_runner_invalid")
    _keys(runner, {"os", "os_version", "architecture", "docker_engine", "docker_compose"}, "evidence_runner")
    if runner["os"] != "linux" or runner["architecture"] != "amd64":
        raise ComposeProfileError("evidence_runner_platform_invalid")
    for field in ("os_version", "docker_engine", "docker_compose"):
        if not isinstance(runner[field], str) or VERSION_PATTERN.fullmatch(runner[field]) is None:
            raise ComposeProfileError("evidence_runner_version_invalid")

    images = _mapping(value["images"], "evidence_images_invalid")
    _keys(images, {"hormuz", "postgres"}, "evidence_images")
    _validate_image_evidence(_mapping(images["hormuz"], "evidence_hormuz_image_invalid"), HORMUZ_IMAGE)
    _validate_image_evidence(_mapping(images["postgres"], "evidence_postgres_image_invalid"), POSTGRES_IMAGE)

    checks = _mapping(value["checks"], "evidence_checks_invalid")
    if set(checks) != EXPECTED_CHECKS or any(result is not True for result in checks.values()):
        raise ComposeProfileError("evidence_checks_incomplete")
    state = _mapping(value["state"], "evidence_state_invalid")
    _keys(
        state,
        {
            "requests_before_restart",
            "requests_after_restart",
            "usage_events_before_restart",
            "usage_events_at_backup",
            "usage_events_after_backup",
            "restored_usage_events",
        },
        "evidence_state",
    )
    for field, item in state.items():
        if _nonnegative_integer(item, f"evidence_state_{field}_invalid") < 1:
            raise ComposeProfileError("evidence_state_incomplete")
    if state["requests_before_restart"] != 1 or state["requests_after_restart"] != 4:
        raise ComposeProfileError("evidence_request_sequence_invalid")
    if state["usage_events_before_restart"] < state["requests_before_restart"]:
        raise ComposeProfileError("evidence_usage_count_invalid")
    if state["usage_events_at_backup"] < state["requests_after_restart"] - 1:
        raise ComposeProfileError("evidence_usage_count_invalid")
    if state["usage_events_after_backup"] < state["requests_after_restart"]:
        raise ComposeProfileError("evidence_usage_count_invalid")
    if state["usage_events_at_backup"] <= state["usage_events_before_restart"]:
        raise ComposeProfileError("evidence_restart_persistence_invalid")
    if state["usage_events_after_backup"] <= state["usage_events_at_backup"]:
        raise ComposeProfileError("evidence_delayed_restore_not_exercised")
    if state["restored_usage_events"] != state["usage_events_at_backup"]:
        raise ComposeProfileError("evidence_restore_invalid")

    interruption = _mapping(value["interruption"], "evidence_interruption_invalid")
    if interruption != {
        "gateway_restart_observed": True,
        "host_failure_can_interrupt": True,
        "single_gateway_replica": True,
    }:
        raise ComposeProfileError("evidence_interruption_invalid")
    if value["limitations"] != EXPECTED_LIMITATIONS:
        raise ComposeProfileError("evidence_limitations_invalid")
    if value["verdict"] != "verified_single_vm_pilot_reference":
        raise ComposeProfileError("evidence_verdict_invalid")


def _validate_image_evidence(value: Mapping[str, Any], expected: str) -> None:
    _keys(value, {"reference", "repo_digest", "image_id"}, "evidence_image")
    if value["reference"] != expected:
        raise ComposeProfileError("evidence_image_reference_invalid")
    for field in ("repo_digest", "image_id"):
        item = value[field]
        if not isinstance(item, str) or SHA256_PATTERN.fullmatch(item) is None:
            raise ComposeProfileError("evidence_image_digest_invalid")
    if value["repo_digest"] != expected.rsplit("@", 1)[1]:
        raise ComposeProfileError("evidence_image_repo_digest_mismatch")


def _reject_privilege_expansion(
    service: Mapping[str, Any],
    *,
    role: str,
    allowed_cap_add: set[str],
) -> None:
    if service.get("privileged") not in (None, False):
        raise ComposeProfileError(f"{role}_privileged")
    cap_add = _strings(service.get("cap_add", []), f"{role}_cap_add_invalid")
    if set(cap_add) != allowed_cap_add or len(cap_add) != len(allowed_cap_add):
        raise ComposeProfileError(f"{role}_cap_add_invalid")
    for field in (
        "build",
        "device_cgroup_rules",
        "devices",
        "ipc",
        "network_mode",
        "pid",
        "runtime",
        "use_api_socket",
        "userns_mode",
        "uts",
        "volumes_from",
    ):
        if service.get(field) not in (None, [], {}):
            raise ComposeProfileError(f"{role}_privilege_surface_invalid")


def _source_targets(value: Any, error: str) -> dict[str, str]:
    result: dict[str, str] = {}
    items = _sequence(value, error)
    for item in items:
        mount = _mapping(item, error)
        if set(mount) != {"source", "target"}:
            raise ComposeProfileError(error)
        source = mount["source"]
        target = mount["target"]
        if not isinstance(source, str) or not isinstance(target, str) or source in result:
            raise ComposeProfileError(error)
        result[source] = target
    return result


def _validate_file_sources(
    values: Mapping[str, Any],
    *,
    expected: Mapping[str, str],
    kind: str,
) -> None:
    for name, relative_path in expected.items():
        source = _mapping(values.get(name), f"compose_{kind}_source_invalid")
        if set(source) != {"name", "file"}:
            raise ComposeProfileError(f"compose_{kind}_source_invalid")
        platform_name = source["name"]
        file_name = source["file"]
        if not isinstance(platform_name, str) or not platform_name.endswith(f"_{name}"):
            raise ComposeProfileError(f"compose_{kind}_source_invalid")
        expected_suffix = f"/deploy/compose/{relative_path}"
        if not isinstance(file_name, str) or not Path(file_name).as_posix().endswith(expected_suffix):
            raise ComposeProfileError(f"compose_{kind}_source_invalid")


def _protected_file_group(service: Mapping[str, Any], role: str) -> None:
    groups = _strings(service.get("group_add"), f"{role}_protected_file_group_invalid")
    if len(groups) != 1 or not groups[0].isdigit():
        raise ComposeProfileError(f"{role}_protected_file_group_invalid")


def _immutable_image(value: Any, expected: str, error: str) -> None:
    if value != expected or "@sha256:" not in expected:
        raise ComposeProfileError(error)


def _mapping(value: Any, error: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ComposeProfileError(error)
    return value


def _sequence(value: Any, error: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ComposeProfileError(error)
    return value


def _strings(value: Any, error: str) -> list[str]:
    items = _sequence(value, error)
    if any(not isinstance(item, str) for item in items):
        raise ComposeProfileError(error)
    return list(items)


def _positive_number(value: Any, error: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ComposeProfileError(error)
    return float(value)


def _positive_integer(value: Any, error: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ComposeProfileError(error)
    return value


def _positive_bytes(value: Any, error: str) -> int:
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    return _positive_integer(value, error)


def _nonnegative_integer(value: Any, error: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ComposeProfileError(error)
    return value


def _keys(value: Mapping[str, Any], expected: set[str], error: str) -> None:
    if set(value) != expected:
        raise ComposeProfileError(error)


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ComposeProfileError("evidence_timestamp_invalid")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ComposeProfileError("evidence_timestamp_invalid") from error
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise ComposeProfileError("evidence_timestamp_invalid")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
