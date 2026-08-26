#!/usr/bin/env python3
"""Validate Hormuz's bounded Helm contract and content-free live evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_ID = "hormuz.kubernetes-reference-proof"
SCHEMA_VERSION = 1
CONTRACT_SCHEMA = "hormuz.kubernetes-profile.v1"
PROFILE = "multi-replica-kubernetes-reference"
PLATFORM = "linux/amd64"
CHART_VERSION = "0.1.0"
HORMUZ_IMAGE = (
    "ghcr.io/xpounder-com/hormuz@"
    "sha256:8ac24f5c7afb8ce09ec133616de06702f568a2e70594d8034146a131d86e5b67"
)
POSTGRES_IMAGE = (
    "postgres@sha256:7a396fd264a2067788b6551122b50f162bf6136312c7fc9d74381cb92c648382"
)
KIND_VERSION = "v0.32.0"
KIND_BINARY_SHA256 = "50030de23cf40a18505f20426f6a8506bedf13c6e509244bd1fa9463721b0f54"
KUBECTL_VERSION = "v1.36.1"
KUBECTL_BINARY_SHA256 = "629d3f410e09bf49b64ae7079f7f0bda1191efed311f7d37fdbab0ad5b0ec2b7"
HELM_VERSION = "v3.21.4"
HELM_BINARY_SHA256 = "61f88ab166748cb19604d7884cb100ae9ccb13804ddeb98e08af167eacbb6a14"
KUBERNETES_VERSION = "v1.36.1"
KIND_NODE_IMAGE = (
    "kindest/node:v1.36.1@"
    "sha256:3489c7674813ba5d8b1a9977baea8a6e553784dab7b84759d1014dbd78f7ebd5"
)
CILIUM_VERSION = "1.20.1"
CILIUM_CHART_SHA256 = "06210eef7c23d15f7699c79e2fe3a1ec9c389024c5c5c006ea04022d322449a2"
CILIUM_AGENT_IMAGE = (
    "quay.io/cilium/cilium:v1.20.1@"
    "sha256:ae9ea21f7427fe24bc6ea7247eb552157a1b0a431744045d3f641545ca71d11b"
)
CILIUM_OPERATOR_IMAGE = (
    "quay.io/cilium/operator-generic:v1.20.1@"
    "sha256:6c3885fc7b629099fdbe2a5c87869c86feb825fa18fae299eac0f61918d16ecf"
)
EXPECTED_CHART_FILES = {
    "Chart.yaml",
    "templates/NOTES.txt",
    "templates/_helpers.tpl",
    "templates/deployment.yaml",
    "templates/networkpolicy.yaml",
    "templates/poddisruptionbudget.yaml",
    "templates/service.yaml",
    "values.schema.json",
    "values.yaml",
}
EXPECTED_CHECKS = {
    "authenticated_private_ingress",
    "chart_static_contract",
    "cilium_network_policy_enforcement",
    "clean_removal",
    "configuration_preflight",
    "configuration_replacement",
    "configuration_rollback",
    "customer_owned_postgres_secret",
    "default_deny_egress",
    "default_deny_ingress",
    "digest_pinned_runtime",
    "fake_provider_traffic",
    "linux_amd64_gate",
    "policy_evidence_persistence",
    "replica_replacement",
    "secret_non_disclosure",
    "startup_readiness",
    "termination_grace_configured",
    "topology_spread",
    "two_gateway_replicas",
}
EXPECTED_LIMITATIONS = [
    "account_free_disposable_kind_environment_only",
    "browser_sessions_out_of_scope",
    "cilium_first_tested_cni_not_a_dependency",
    "customer_tls_idp_providers_and_postgresql_not_certified",
    "kubernetes_helm_and_cilium_not_certified",
    "no_broad_cni_portability_claim",
    "no_high_availability_claim",
    "no_multi_region_or_zone_failure_claim",
    "no_production_operations_claim",
    "no_rpo_or_rto_claim",
]
SHA256_PATTERN = re.compile(r"(?:sha256:)?[0-9a-f]{64}\Z")
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
PRIVILEGED_RUNTIME_ENV_SUFFIX_PATTERN = (
    r"(_ADMIN_TOKEN|_BREAK_GLASS_TOKEN|_MIGRATION_DSN|_CONTROL_DSN|_EXECUTOR_DSN)$"
)
PRIVILEGED_RUNTIME_ENV_PATTERN = re.compile(PRIVILEGED_RUNTIME_ENV_SUFFIX_PATTERN)


class HelmProfileError(RuntimeError):
    """Raised when a chart, rendered model, or proof violates the contract."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    chart_parser = subparsers.add_parser("validate-chart")
    chart_parser.add_argument("--chart", required=True, type=Path)

    manifest_parser = subparsers.add_parser("validate-manifest")
    manifest_parser.add_argument("--manifest", required=True, type=Path)
    manifest_parser.add_argument("--configuration", required=True)
    manifest_parser.add_argument("--configuration-sha256", required=True)
    manifest_parser.add_argument("--runtime-secret", required=True)
    manifest_parser.add_argument("--runtime-secret-revision", required=True)

    rollout_parser = subparsers.add_parser("validate-serving-generation")
    rollout_parser.add_argument("--manifest", required=True, type=Path)
    rollout_parser.add_argument("--configuration", required=True)
    rollout_parser.add_argument("--configuration-sha256", required=True)
    rollout_parser.add_argument("--runtime-secret", required=True)
    rollout_parser.add_argument("--runtime-secret-revision", required=True)

    evidence_parser = subparsers.add_parser("validate-evidence")
    evidence_parser.add_argument("--evidence", required=True, type=Path)

    scan_parser = subparsers.add_parser("assert-no-secrets")
    scan_parser.add_argument("--artifact", action="append", type=Path, default=[])
    scan_parser.add_argument("--artifact-root", type=Path)
    scan_parser.add_argument("--secret-root", required=True, type=Path)

    write_parser = subparsers.add_parser("write-evidence")
    write_parser.add_argument("--output", required=True, type=Path)
    write_parser.add_argument("--docker-engine", required=True)
    write_parser.add_argument("--chart-package-sha256", required=True)
    write_parser.add_argument("--gateway-replicas", required=True, type=int)
    write_parser.add_argument("--distinct-gateway-nodes", required=True, type=int)
    write_parser.add_argument("--successful-requests", required=True, type=int)
    write_parser.add_argument("--policy-denials", required=True, type=int)
    write_parser.add_argument("--provider-requests", required=True, type=int)
    write_parser.add_argument("--usage-events", required=True, type=int)

    args = parser.parse_args(argv)
    try:
        if args.command == "validate-chart":
            digest = validate_chart(args.chart)
            print(
                "verified Helm chart: "
                f"contract={CONTRACT_SCHEMA} version={CHART_VERSION} source_sha256={digest}"
            )
        elif args.command == "validate-manifest":
            value = load_json(args.manifest)
            validate_manifest(
                value,
                expected_configuration=args.configuration,
                expected_configuration_sha256=args.configuration_sha256,
                expected_runtime_secret=args.runtime_secret,
                expected_runtime_secret_revision=args.runtime_secret_revision,
            )
            print(
                "verified rendered Kubernetes contract: "
                f"profile={PROFILE} platform={PLATFORM}"
            )
        elif args.command == "validate-evidence":
            value = load_json(args.evidence)
            validate_evidence(value)
            print(
                "verified content-free Kubernetes proof: "
                f"profile={PROFILE} verdict={value['verdict']}"
            )
        elif args.command == "validate-serving-generation":
            value = load_json(args.manifest)
            validate_serving_generation(
                value,
                expected_configuration=args.configuration,
                expected_configuration_sha256=args.configuration_sha256,
                expected_runtime_secret=args.runtime_secret,
                expected_runtime_secret_revision=args.runtime_secret_revision,
            )
            print(
                "verified serving Kubernetes generation: "
                f"configuration_sha256={args.configuration_sha256}"
            )
        elif args.command == "assert-no-secrets":
            artifacts = list(args.artifact)
            if args.artifact_root is not None:
                if not args.artifact_root.is_dir() or args.artifact_root.is_symlink():
                    raise HelmProfileError("artifact_root_invalid")
                artifacts.extend(
                    sorted(path for path in args.artifact_root.rglob("*") if path.is_file())
                )
            if not artifacts:
                raise HelmProfileError("artifact_missing")
            assert_no_secrets(artifacts, secret_values=read_secret_values(args.secret_root))
            print(f"verified secret non-disclosure across {len(artifacts)} artifacts")
        else:
            evidence = build_evidence(
                docker_engine=args.docker_engine,
                chart_package_sha256=args.chart_package_sha256,
                gateway_replicas=args.gateway_replicas,
                distinct_gateway_nodes=args.distinct_gateway_nodes,
                successful_requests=args.successful_requests,
                policy_denials=args.policy_denials,
                provider_requests=args.provider_requests,
                usage_events=args.usage_events,
            )
            validate_evidence(evidence)
            write_evidence(args.output, evidence)
            print(f"wrote content-free Kubernetes proof: {args.output}")
    except (HelmProfileError, OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        print(f"Helm profile verification failed: {error}", file=sys.stderr)
        return 1
    return 0


def load_json(path: Path) -> dict[str, Any]:
    """Read a JSON object while rejecting duplicate members."""

    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise HelmProfileError("duplicate_json_key")
            value[key] = item
        return value

    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=object_pairs)
    if not isinstance(value, dict):
        raise HelmProfileError("json_root_not_object")
    return value


def validate_chart(chart: Path) -> str:
    """Validate the source chart without requiring Helm or a cluster."""

    if not chart.is_dir() or chart.is_symlink():
        raise HelmProfileError("chart_root_invalid")
    files = {
        path.relative_to(chart).as_posix()
        for path in chart.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    if files != EXPECTED_CHART_FILES:
        raise HelmProfileError("chart_file_set_invalid")

    chart_yaml = (chart / "Chart.yaml").read_text(encoding="utf-8")
    for line in (
        "apiVersion: v2",
        "name: hormuz",
        f"version: {CHART_VERSION}",
        "appVersion: \"0.1.3\"",
        f"  io.hormuz.contract: {CONTRACT_SCHEMA}",
    ):
        if line not in chart_yaml.splitlines():
            raise HelmProfileError("chart_metadata_invalid")

    schema = load_json(chart / "values.schema.json")
    if schema.get("additionalProperties") is not False:
        raise HelmProfileError("values_schema_not_strict")
    properties = _mapping(schema.get("properties"), "values_schema_properties")
    contract = _mapping(properties.get("contract"), "values_schema_contract")
    image = _mapping(properties.get("image"), "values_schema_image")
    runtime_secret = _mapping(
        properties.get("runtimeSecret"), "values_schema_runtime_secret"
    )
    runtime_env = _mapping(
        _mapping(
            runtime_secret.get("properties"), "values_schema_runtime_secret_properties"
        ).get("env"),
        "values_schema_runtime_env",
    )
    digest = _mapping(image.get("properties"), "values_schema_image_properties").get("digest")
    if digest != {"const": HORMUZ_IMAGE.partition("@")[2]}:
        raise HelmProfileError("values_schema_image_digest")
    contract_properties = _mapping(
        contract.get("properties"), "values_schema_contract_properties"
    )
    if contract_properties.get("schema") != {"const": CONTRACT_SCHEMA}:
        raise HelmProfileError("values_schema_contract")
    if contract_properties.get("platform") != {"const": PLATFORM}:
        raise HelmProfileError("values_schema_platform")
    if runtime_env.get("propertyNames") != {
        "not": {"pattern": PRIVILEGED_RUNTIME_ENV_SUFFIX_PATTERN}
    }:
        raise HelmProfileError("values_schema_privileged_runtime_env")

    values_text = (chart / "values.yaml").read_text(encoding="utf-8")
    if HORMUZ_IMAGE.partition("@")[2] not in values_text:
        raise HelmProfileError("values_image_digest")
    if re.search(r"(?m)^\s+tag\s*:", values_text):
        raise HelmProfileError("mutable_image_tag")
    if "networkPolicy:\n  enabled: true" not in values_text:
        raise HelmProfileError("default_deny_not_enabled")
    if "    HORMUZ_TOKEN:" in values_text:
        raise HelmProfileError("static_identity_enabled_by_default")

    rendered_sources = "\n".join(
        (chart / name).read_text(encoding="utf-8")
        for name in sorted(files)
        if name.startswith("templates/")
    )
    for forbidden in (
        "apiVersion: cilium.io/",
        "kind: CiliumNetworkPolicy",
        "kind: Ingress",
        "kind: Secret",
        "type: LoadBalancer",
        "type: NodePort",
    ):
        if forbidden in rendered_sources:
            raise HelmProfileError("vendor_or_public_resource_in_chart")
    for required in (
        "apiVersion: networking.k8s.io/v1",
        "kind: Deployment",
        "kind: Service",
        "type: ClusterIP",
        "kind: PodDisruptionBudget",
        "automountServiceAccountToken: false",
        "readOnlyRootFilesystem: true",
        "drop: [\"ALL\"]",
        "whenUnsatisfiable: DoNotSchedule",
        "nodeAffinityPolicy: Honor",
        "nodeTaintsPolicy: Honor",
        "maxUnavailable: 0",
        "name: {{ $name | quote }}",
        "name: {{ $.Values.runtimeSecret.name | quote }}",
        "key: {{ $key | quote }}",
        "name: {{ .Values.configuration.name | quote }}",
        "key: {{ .Values.configuration.key | quote }}",
        "name: {{ .name | quote }}",
    ):
        if required not in rendered_sources:
            raise HelmProfileError("required_chart_control_missing")
    return chart_source_sha256(chart)


def chart_source_sha256(chart: Path) -> str:
    digest = hashlib.sha256()
    for relative in sorted(EXPECTED_CHART_FILES):
        path = chart / relative
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def validate_manifest(
    value: Mapping[str, Any],
    *,
    expected_configuration: str,
    expected_configuration_sha256: str | None = None,
    expected_runtime_secret: str,
    expected_runtime_secret_revision: str | None = None,
) -> None:
    """Validate chart-owned resources retrieved as a Kubernetes List."""

    if value.get("kind") != "List" or value.get("apiVersion") != "v1":
        raise HelmProfileError("manifest_list_invalid")
    items = _sequence(value.get("items"), "manifest_items")
    resources: dict[str, list[Mapping[str, Any]]] = {}
    for raw in items:
        item = _mapping(raw, "manifest_item")
        kind = _string(item.get("kind"), "manifest_kind")
        if kind in {"Secret", "ConfigMap", "Ingress", "ServiceAccount", "Role", "RoleBinding"}:
            raise HelmProfileError("chart_owns_customer_dependency")
        if str(item.get("apiVersion", "")).startswith("cilium.io/"):
            raise HelmProfileError("cni_specific_resource")
        resources.setdefault(kind, []).append(item)
    if {kind: len(entries) for kind, entries in resources.items()} != {
        "Deployment": 1,
        "NetworkPolicy": 4,
        "PodDisruptionBudget": 1,
        "Service": 1,
    }:
        raise HelmProfileError("manifest_resource_set_invalid")

    deployment = resources["Deployment"][0]
    replicas = _validate_deployment(
        deployment,
        expected_configuration=expected_configuration,
        expected_configuration_sha256=expected_configuration_sha256,
        expected_runtime_secret=expected_runtime_secret,
        expected_runtime_secret_revision=expected_runtime_secret_revision,
    )
    _validate_service(resources["Service"][0])
    _validate_pdb(resources["PodDisruptionBudget"][0], replicas=replicas)
    _validate_network_policies(resources["NetworkPolicy"])


def validate_serving_generation(
    value: Mapping[str, Any],
    *,
    expected_configuration: str,
    expected_configuration_sha256: str,
    expected_runtime_secret: str,
    expected_runtime_secret_revision: str,
) -> None:
    """Require a complete rollout with every ready Pod on one input generation."""

    if value.get("kind") != "List" or value.get("apiVersion") != "v1":
        raise HelmProfileError("rollout_list_invalid")
    items = _sequence(value.get("items"), "rollout_items")
    deployments: list[Mapping[str, Any]] = []
    pods: list[Mapping[str, Any]] = []
    for raw in items:
        item = _mapping(raw, "rollout_item")
        kind = _string(item.get("kind"), "rollout_kind")
        if kind == "Deployment":
            deployments.append(item)
        elif kind == "Pod":
            pods.append(item)
        else:
            raise HelmProfileError("rollout_resource_set_invalid")
    if len(deployments) != 1 or not pods:
        raise HelmProfileError("rollout_resource_set_invalid")

    deployment = deployments[0]
    replicas = _validate_deployment(
        deployment,
        expected_configuration=expected_configuration,
        expected_configuration_sha256=expected_configuration_sha256,
        expected_runtime_secret=expected_runtime_secret,
        expected_runtime_secret_revision=expected_runtime_secret_revision,
    )
    metadata = _mapping(deployment.get("metadata"), "rollout_deployment_metadata")
    generation = _integer(metadata.get("generation"), "rollout_generation")
    if generation < 1:
        raise HelmProfileError("rollout_generation")
    status = _mapping(deployment.get("status"), "rollout_deployment_status")
    if (
        _integer(status.get("observedGeneration"), "rollout_observed_generation")
        != generation
    ):
        raise HelmProfileError("rollout_observed_generation")
    for field, error in (
        ("replicas", "rollout_replicas"),
        ("updatedReplicas", "rollout_updated_replicas"),
        ("readyReplicas", "rollout_ready_replicas"),
        ("availableReplicas", "rollout_available_replicas"),
    ):
        if _integer(status.get(field), error) != replicas:
            raise HelmProfileError(error)
    if status.get("unavailableReplicas", 0) != 0:
        raise HelmProfileError("rollout_unavailable_replicas")

    selector = _mapping(
        _mapping(deployment.get("spec"), "rollout_deployment_spec").get("selector"),
        "rollout_selector",
    )
    match_labels = _mapping(selector.get("matchLabels"), "rollout_match_labels")
    ready_pods: list[Mapping[str, Any]] = []
    for pod in pods:
        if pod.get("apiVersion") != "v1":
            raise HelmProfileError("rollout_pod_api")
        pod_metadata = _mapping(pod.get("metadata"), "rollout_pod_metadata")
        if pod_metadata.get("deletionTimestamp") is not None:
            continue
        labels = _mapping(pod_metadata.get("labels"), "rollout_pod_labels")
        if any(labels.get(key) != item for key, item in match_labels.items()):
            raise HelmProfileError("rollout_pod_selector")
        pod_status = _mapping(pod.get("status"), "rollout_pod_status")
        conditions: dict[str, Mapping[str, Any]] = {}
        for raw_condition in _sequence(
            pod_status.get("conditions"), "rollout_pod_conditions"
        ):
            condition = _mapping(raw_condition, "rollout_pod_condition")
            condition_type = _string(condition.get("type"), "condition_type")
            if condition_type in conditions:
                raise HelmProfileError("rollout_pod_condition_duplicate")
            conditions[condition_type] = condition
        ready = _mapping(conditions.get("Ready"), "rollout_pod_ready")
        if pod_status.get("phase") != "Running" or ready.get("status") != "True":
            raise HelmProfileError("rollout_pod_not_ready")
        _validate_serving_pod(
            pod,
            expected_configuration=expected_configuration,
            expected_configuration_sha256=expected_configuration_sha256,
            expected_runtime_secret=expected_runtime_secret,
            expected_runtime_secret_revision=expected_runtime_secret_revision,
        )
        ready_pods.append(pod)
    if len(ready_pods) != replicas:
        raise HelmProfileError("rollout_pod_count")


def _validate_serving_pod(
    pod: Mapping[str, Any],
    *,
    expected_configuration: str,
    expected_configuration_sha256: str,
    expected_runtime_secret: str,
    expected_runtime_secret_revision: str,
) -> None:
    metadata = _mapping(pod.get("metadata"), "serving_pod_metadata")
    annotations = _mapping(metadata.get("annotations"), "serving_pod_annotations")
    if annotations.get("io.hormuz/config-sha256") != expected_configuration_sha256:
        raise HelmProfileError("pod_configuration_sha256")
    if (
        annotations.get("io.hormuz/runtime-secret-revision")
        != expected_runtime_secret_revision
        or annotations.get("io.hormuz/image-digest") != HORMUZ_IMAGE.partition("@")[2]
    ):
        raise HelmProfileError("pod_runtime_input_annotations")

    pod_spec = _mapping(pod.get("spec"), "serving_pod_spec")
    volumes = _sequence(pod_spec.get("volumes"), "serving_pod_volumes")
    by_name: dict[str, Mapping[str, Any]] = {}
    for raw_volume in volumes:
        volume = _mapping(raw_volume, "serving_pod_volume")
        name = _string(volume.get("name"), "serving_pod_volume_name")
        if name in by_name:
            raise HelmProfileError("serving_pod_volume_duplicate")
        by_name[name] = volume
    configuration = _mapping(
        by_name.get("configuration"), "serving_configuration_volume"
    )
    config_map = _mapping(
        configuration.get("configMap"), "serving_configuration_configmap"
    )
    if config_map.get("name") != expected_configuration:
        raise HelmProfileError("pod_configuration_reference")

    secret_references: set[str] = set()
    for category in ("initContainers", "containers"):
        for raw in _sequence(pod_spec.get(category), f"serving_pod_{category}"):
            container = _mapping(raw, "serving_pod_container")
            if container.get("image") != HORMUZ_IMAGE:
                raise HelmProfileError("pod_image")
            for raw_env in _sequence(container.get("env"), "serving_pod_env"):
                env = _mapping(raw_env, "serving_pod_env_item")
                value_from = env.get("valueFrom")
                if value_from is None:
                    continue
                secret_key = _mapping(
                    _mapping(value_from, "serving_pod_value_from").get("secretKeyRef"),
                    "serving_pod_secret_key_ref",
                )
                secret_references.add(
                    _string(secret_key.get("name"), "serving_pod_secret_name")
                )
    if secret_references != {expected_runtime_secret}:
        raise HelmProfileError("pod_runtime_secret_reference")


def _validate_deployment(
    deployment: Mapping[str, Any],
    *,
    expected_configuration: str,
    expected_configuration_sha256: str | None,
    expected_runtime_secret: str,
    expected_runtime_secret_revision: str | None,
) -> int:
    if deployment.get("apiVersion") != "apps/v1":
        raise HelmProfileError("deployment_api")
    metadata = _mapping(deployment.get("metadata"), "deployment_metadata")
    annotations = _mapping(metadata.get("annotations", {}), "deployment_annotations")
    if annotations.get("io.hormuz/contract") != CONTRACT_SCHEMA:
        raise HelmProfileError("deployment_contract")
    spec = _mapping(deployment.get("spec"), "deployment_spec")
    replicas = _integer(spec.get("replicas"), "deployment_replicas")
    if replicas < 2:
        raise HelmProfileError("deployment_replicas")
    strategy = _mapping(spec.get("strategy"), "deployment_strategy")
    rolling = _mapping(strategy.get("rollingUpdate"), "deployment_rolling_update")
    if strategy.get("type") != "RollingUpdate" or rolling != {"maxSurge": 1, "maxUnavailable": 0}:
        raise HelmProfileError("deployment_rolling_update")
    if _integer(spec.get("minReadySeconds"), "deployment_min_ready") < 5:
        raise HelmProfileError("deployment_min_ready")

    template = _mapping(spec.get("template"), "deployment_template")
    template_metadata = _mapping(template.get("metadata"), "deployment_template_metadata")
    template_annotations = _mapping(
        template_metadata.get("annotations"), "deployment_template_annotations"
    )
    configuration_sha256 = _string(
        template_annotations.get("io.hormuz/config-sha256"), "configuration_sha256"
    )
    secret_revision = _string(
        template_annotations.get("io.hormuz/runtime-secret-revision"), "runtime_secret_revision"
    )
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", configuration_sha256):
        raise HelmProfileError("configuration_sha256")
    if expected_configuration_sha256 is not None and configuration_sha256 != expected_configuration_sha256:
        raise HelmProfileError("configuration_sha256")
    if (
        template_annotations.get("io.hormuz/image-digest") != HORMUZ_IMAGE.partition("@")[2]
        or (expected_runtime_secret_revision is not None and secret_revision != expected_runtime_secret_revision)
    ):
        raise HelmProfileError("runtime_input_annotations")
    pod_spec = _mapping(template.get("spec"), "deployment_pod_spec")
    if pod_spec.get("automountServiceAccountToken") is not False:
        raise HelmProfileError("service_account_token")
    if pod_spec.get("enableServiceLinks") is not False:
        raise HelmProfileError("service_links")
    if _integer(pod_spec.get("terminationGracePeriodSeconds"), "termination_grace") < 60:
        raise HelmProfileError("termination_grace")
    node_selector = _mapping(pod_spec.get("nodeSelector"), "node_selector")
    if node_selector != {"kubernetes.io/arch": "amd64", "kubernetes.io/os": "linux"}:
        raise HelmProfileError("platform_gate")
    security = _mapping(pod_spec.get("securityContext"), "pod_security")
    if (
        security.get("runAsNonRoot") is not True
        or security.get("runAsUser") != 65532
        or security.get("runAsGroup") != 65532
        or _mapping(security.get("seccompProfile"), "seccomp").get("type") != "RuntimeDefault"
    ):
        raise HelmProfileError("pod_security")
    spread = _sequence(pod_spec.get("topologySpreadConstraints"), "topology_spread")
    if len(spread) != 1:
        raise HelmProfileError("topology_spread")
    spread_item = _mapping(spread[0], "topology_spread_item")
    if (
        spread_item.get("whenUnsatisfiable") != "DoNotSchedule"
        or spread_item.get("topologyKey") != "kubernetes.io/hostname"
        or spread_item.get("maxSkew") != 1
        or spread_item.get("nodeAffinityPolicy") != "Honor"
        or spread_item.get("nodeTaintsPolicy") != "Honor"
    ):
        raise HelmProfileError("topology_spread")

    init_containers = _sequence(pod_spec.get("initContainers"), "init_containers")
    containers = _sequence(pod_spec.get("containers"), "containers")
    if len(init_containers) != 1 or len(containers) != 1:
        raise HelmProfileError("container_set")
    preflight = _mapping(init_containers[0], "preflight")
    gateway = _mapping(containers[0], "gateway")
    if preflight.get("name") != "configuration-preflight" or preflight.get("args") != ["doctor"]:
        raise HelmProfileError("configuration_preflight")
    if gateway.get("name") != "gateway" or gateway.get("args") != ["serve"]:
        raise HelmProfileError("gateway_command")
    for container, role in ((preflight, "preflight"), (gateway, "gateway")):
        if container.get("image") != HORMUZ_IMAGE:
            raise HelmProfileError(f"{role}_image")
        _validate_container_security(container, role=role)
        _validate_container_env(
            container,
            role=role,
            expected_runtime_secret=expected_runtime_secret,
        )
        _validate_resources(container, role=role)
    for probe_name in ("livenessProbe", "readinessProbe"):
        probe = _mapping(gateway.get(probe_name), f"gateway_{probe_name}")
        command = _sequence(
            _mapping(probe.get("exec"), f"gateway_{probe_name}_exec").get("command"),
            f"gateway_{probe_name}_command",
        )
        if command[:3] != ["/opt/hormuz/bin/python", "-I", "-c"]:
            raise HelmProfileError("authenticated_probe")
        code = _string(command[3] if len(command) == 4 else None, "authenticated_probe_code")
        if "HORMUZ_INGRESS_CREDENTIAL" not in code:
            raise HelmProfileError("authenticated_probe")

    volumes = _sequence(pod_spec.get("volumes"), "volumes")
    if len(volumes) != 2:
        raise HelmProfileError("volume_set")
    by_name = {_string(_mapping(item, "volume").get("name"), "volume_name"): _mapping(item, "volume") for item in volumes}
    config_volume = _mapping(by_name.get("configuration"), "configuration_volume")
    config_map = _mapping(config_volume.get("configMap"), "configuration_configmap")
    if config_map.get("name") != expected_configuration:
        raise HelmProfileError("configuration_reference")
    temporary = _mapping(by_name.get("temporary"), "temporary_volume")
    empty_dir = _mapping(temporary.get("emptyDir"), "temporary_emptydir")
    if empty_dir.get("medium") != "Memory" or empty_dir.get("sizeLimit") != "64Mi":
        raise HelmProfileError("temporary_volume")
    return replicas


def _validate_container_security(container: Mapping[str, Any], *, role: str) -> None:
    security = _mapping(container.get("securityContext"), f"{role}_security")
    capabilities = _mapping(security.get("capabilities"), f"{role}_capabilities")
    if (
        security.get("allowPrivilegeEscalation") is not False
        or security.get("readOnlyRootFilesystem") is not True
        or capabilities.get("drop") != ["ALL"]
        or security.get("privileged") is True
    ):
        raise HelmProfileError(f"{role}_security")


def _validate_container_env(
    container: Mapping[str, Any],
    *,
    role: str,
    expected_runtime_secret: str,
) -> None:
    env = _sequence(container.get("env"), f"{role}_env")
    names: set[str] = set()
    for raw in env:
        item = _mapping(raw, f"{role}_env_item")
        name = _string(item.get("name"), f"{role}_env_name")
        if name in names:
            raise HelmProfileError(f"{role}_duplicate_env")
        names.add(name)
        if name == "HORMUZ_CONFIG":
            if item != {"name": "HORMUZ_CONFIG", "value": "/etc/hormuz/hormuz.json"}:
                raise HelmProfileError(f"{role}_config_env")
            continue
        if PRIVILEGED_RUNTIME_ENV_PATTERN.search(name):
            raise HelmProfileError(f"{role}_privileged_env")
        if "value" in item:
            raise HelmProfileError(f"{role}_literal_secret")
        secret_ref = _mapping(
            _mapping(item.get("valueFrom"), f"{role}_value_from").get("secretKeyRef"),
            f"{role}_secret_ref",
        )
        if secret_ref.get("name") != expected_runtime_secret or not secret_ref.get("key"):
            raise HelmProfileError(f"{role}_secret_ref")
    if not {"HORMUZ_CONFIG", "HORMUZ_POSTGRES_DSN", "HORMUZ_INGRESS_CREDENTIAL"}.issubset(names):
        raise HelmProfileError(f"{role}_required_env")


def _validate_resources(container: Mapping[str, Any], *, role: str) -> None:
    resources = _mapping(container.get("resources"), f"{role}_resources")
    for boundary in ("requests", "limits"):
        value = _mapping(resources.get(boundary), f"{role}_{boundary}")
        if set(value) != {"cpu", "memory"} or not all(value.values()):
            raise HelmProfileError(f"{role}_{boundary}")


def _validate_service(service: Mapping[str, Any]) -> None:
    if service.get("apiVersion") != "v1":
        raise HelmProfileError("service_api")
    spec = _mapping(service.get("spec"), "service_spec")
    if spec.get("type") != "ClusterIP":
        raise HelmProfileError("service_exposure")
    if spec.get("externalIPs") or spec.get("loadBalancerIP") or spec.get("loadBalancerClass"):
        raise HelmProfileError("service_exposure")
    ports = _sequence(spec.get("ports"), "service_ports")
    if len(ports) != 1:
        raise HelmProfileError("service_ports")
    port = _mapping(ports[0], "service_port")
    if port.get("port") != 8787 or port.get("targetPort") != "http-private" or "nodePort" in port:
        raise HelmProfileError("service_ports")


def _validate_pdb(pdb: Mapping[str, Any], *, replicas: int) -> None:
    if pdb.get("apiVersion") != "policy/v1":
        raise HelmProfileError("pdb_api")
    minimum = _integer(_mapping(pdb.get("spec"), "pdb_spec").get("minAvailable"), "pdb_minimum")
    if minimum < 1 or minimum >= replicas:
        raise HelmProfileError("pdb_minimum")


def _validate_network_policies(policies: Sequence[Mapping[str, Any]]) -> None:
    by_role: dict[str, Mapping[str, Any]] = {}
    for policy in policies:
        if policy.get("apiVersion") != "networking.k8s.io/v1":
            raise HelmProfileError("network_policy_api")
        metadata = _mapping(policy.get("metadata"), "network_policy_metadata")
        role = _string(
            _mapping(metadata.get("annotations"), "network_policy_annotations").get(
                "io.hormuz/network-policy-role"
            ),
            "network_policy_role",
        )
        if role in by_role:
            raise HelmProfileError("network_policy_role_duplicate")
        by_role[role] = policy
    if set(by_role) != {"default-deny", "dns-egress", "customer-ingress", "customer-egress"}:
        raise HelmProfileError("network_policy_roles")

    default = _mapping(by_role["default-deny"].get("spec"), "default_deny_spec")
    # The Kubernetes API omits explicitly empty rule lists when it canonicalizes
    # stored NetworkPolicy objects. Absent and empty both deny every direction
    # named in policyTypes; a null or non-empty value remains invalid.
    if (
        default.get("policyTypes") != ["Ingress", "Egress"]
        or default.get("ingress", []) != []
        or default.get("egress", []) != []
    ):
        raise HelmProfileError("default_deny")

    dns = _mapping(by_role["dns-egress"].get("spec"), "dns_policy_spec")
    dns_rules = _sequence(dns.get("egress"), "dns_rules")
    if dns.get("policyTypes") != ["Egress"] or len(dns_rules) != 1:
        raise HelmProfileError("dns_policy")
    dns_rule = _mapping(dns_rules[0], "dns_rule")
    dns_ports = _sequence(dns_rule.get("ports"), "dns_ports")
    observed = {
        (_mapping(port, "dns_port").get("protocol"), _mapping(port, "dns_port").get("port"))
        for port in dns_ports
    }
    if observed != {("TCP", 53), ("UDP", 53)}:
        raise HelmProfileError("dns_policy")

    ingress = _mapping(by_role["customer-ingress"].get("spec"), "ingress_policy_spec")
    egress = _mapping(by_role["customer-egress"].get("spec"), "egress_policy_spec")
    ingress_rules = _sequence(ingress.get("ingress"), "customer_ingress_rules")
    egress_rules = _sequence(egress.get("egress"), "customer_egress_rules")
    if ingress.get("policyTypes") != ["Ingress"] or not ingress_rules:
        raise HelmProfileError("customer_ingress_policy")
    if egress.get("policyTypes") != ["Egress"] or not egress_rules:
        raise HelmProfileError("customer_egress_policy")
    _validate_standard_rules(ingress_rules, peer_key="from", role="ingress")
    _validate_standard_rules(egress_rules, peer_key="to", role="egress")


def _validate_standard_rules(rules: Sequence[Any], *, peer_key: str, role: str) -> None:
    for raw_rule in rules:
        rule = _mapping(raw_rule, f"{role}_rule")
        if set(rule) != {peer_key, "ports"}:
            raise HelmProfileError(f"{role}_rule_shape")
        peers = _sequence(rule.get(peer_key), f"{role}_peers")
        ports = _sequence(rule.get("ports"), f"{role}_ports")
        if not peers or not ports:
            raise HelmProfileError(f"{role}_rule_empty")
        for raw_peer in peers:
            peer = _mapping(raw_peer, f"{role}_peer")
            if not peer or not set(peer).issubset({"namespaceSelector", "podSelector", "ipBlock"}):
                raise HelmProfileError(f"{role}_peer")
            if "ipBlock" in peer:
                if len(peer) != 1:
                    raise HelmProfileError(f"{role}_ipblock_combined")
                cidr = _string(_mapping(peer["ipBlock"], f"{role}_ipblock").get("cidr"), f"{role}_cidr")
                if cidr in {"0.0.0.0/0", "::/0"}:
                    raise HelmProfileError(f"{role}_broad_cidr")


def read_secret_values(root: Path) -> tuple[str, ...]:
    if not root.is_dir() or root.is_symlink():
        raise HelmProfileError("secret_root_invalid")
    values: list[str] = []
    for path in sorted(root.iterdir()):
        if path.is_symlink() or not path.is_file():
            raise HelmProfileError("secret_file_invalid")
        value = path.read_text(encoding="utf-8").strip()
        if not value:
            raise HelmProfileError("secret_file_empty")
        values.append(value)
    if not values:
        raise HelmProfileError("secret_root_empty")
    return tuple(values)


def assert_no_secrets(paths: Sequence[Path], *, secret_values: Sequence[str]) -> None:
    forbidden = tuple(value.encode("utf-8") for value in secret_values) + (
        b"sk-proj-KKKKKKKKKKKKKKKKKKKKKKKK",
    )
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise HelmProfileError("artifact_invalid")
        content = path.read_bytes()
        if any(value in content for value in forbidden):
            raise HelmProfileError("artifact_contains_secret_value")


def build_evidence(
    *,
    docker_engine: str,
    chart_package_sha256: str,
    gateway_replicas: int,
    distinct_gateway_nodes: int,
    successful_requests: int,
    policy_denials: int,
    provider_requests: int,
    usage_events: int,
) -> dict[str, Any]:
    return {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "profile": PROFILE,
        "contract": CONTRACT_SCHEMA,
        "runner": {
            "os": "linux",
            "architecture": "amd64",
            "docker_engine": docker_engine,
        },
        "tools": {
            "kind": {"version": KIND_VERSION, "binary_sha256": KIND_BINARY_SHA256},
            "kubectl": {"version": KUBECTL_VERSION, "binary_sha256": KUBECTL_BINARY_SHA256},
            "helm": {"version": HELM_VERSION, "binary_sha256": HELM_BINARY_SHA256},
        },
        "cluster": {
            "implementation": "kind",
            "kubernetes_version": KUBERNETES_VERSION,
            "node_image": KIND_NODE_IMAGE,
            "control_plane_nodes": 1,
            "worker_nodes": 2,
            "cni": {
                "first_tested_implementation": "cilium",
                "version": CILIUM_VERSION,
                "chart_sha256": CILIUM_CHART_SHA256,
                "agent_image": CILIUM_AGENT_IMAGE,
                "operator_image": CILIUM_OPERATOR_IMAGE,
                "product_dependency": False,
            },
        },
        "chart": {
            "name": "hormuz",
            "version": CHART_VERSION,
            "package_sha256": chart_package_sha256,
            "application_image": HORMUZ_IMAGE,
            "standard_kubernetes_apis_only": True,
        },
        "dependencies": {
            "postgres_image": POSTGRES_IMAGE,
            "postgres_ownership": "customer_fixture_outside_chart",
            "tls_ownership": "customer_controlled_outside_chart",
            "oidc_contract": "generic_bearer_jwt_resource_server",
        },
        "topology": {
            "gateway_replicas": gateway_replicas,
            "distinct_gateway_nodes": distinct_gateway_nodes,
            "service_type": "ClusterIP",
        },
        "state": {
            "successful_requests": successful_requests,
            "policy_denials": policy_denials,
            "provider_requests": provider_requests,
            "usage_events": usage_events,
        },
        "checks": {name: True for name in sorted(EXPECTED_CHECKS)},
        "limitations": EXPECTED_LIMITATIONS,
        "verdict": "verified_disposable_multi_replica_reference",
    }


def validate_evidence(value: Mapping[str, Any]) -> None:
    _keys(
        value,
        {
            "schema_id",
            "schema_version",
            "observed_at",
            "profile",
            "contract",
            "runner",
            "tools",
            "cluster",
            "chart",
            "dependencies",
            "topology",
            "state",
            "checks",
            "limitations",
            "verdict",
        },
        "evidence_keys",
    )
    if (
        value.get("schema_id") != SCHEMA_ID
        or value.get("schema_version") != SCHEMA_VERSION
        or value.get("profile") != PROFILE
        or value.get("contract") != CONTRACT_SCHEMA
        or value.get("verdict") != "verified_disposable_multi_replica_reference"
    ):
        raise HelmProfileError("evidence_identity")
    _timestamp(value.get("observed_at"))
    runner = _mapping(value.get("runner"), "runner")
    if runner.get("os") != "linux" or runner.get("architecture") != "amd64" or not runner.get("docker_engine"):
        raise HelmProfileError("runner")

    tools = _mapping(value.get("tools"), "tools")
    expected_tools = {
        "kind": (KIND_VERSION, KIND_BINARY_SHA256),
        "kubectl": (KUBECTL_VERSION, KUBECTL_BINARY_SHA256),
        "helm": (HELM_VERSION, HELM_BINARY_SHA256),
    }
    if set(tools) != set(expected_tools):
        raise HelmProfileError("tools")
    for name, (version, digest) in expected_tools.items():
        tool = _mapping(tools[name], f"tool_{name}")
        if tool != {"version": version, "binary_sha256": digest}:
            raise HelmProfileError(f"tool_{name}")

    cluster = _mapping(value.get("cluster"), "cluster")
    if (
        cluster.get("implementation") != "kind"
        or cluster.get("kubernetes_version") != KUBERNETES_VERSION
        or cluster.get("node_image") != KIND_NODE_IMAGE
        or cluster.get("control_plane_nodes") != 1
        or cluster.get("worker_nodes") != 2
    ):
        raise HelmProfileError("cluster")
    cni = _mapping(cluster.get("cni"), "cluster_cni")
    if cni != {
        "first_tested_implementation": "cilium",
        "version": CILIUM_VERSION,
        "chart_sha256": CILIUM_CHART_SHA256,
        "agent_image": CILIUM_AGENT_IMAGE,
        "operator_image": CILIUM_OPERATOR_IMAGE,
        "product_dependency": False,
    }:
        raise HelmProfileError("cluster_cni")

    chart = _mapping(value.get("chart"), "chart")
    if (
        chart.get("name") != "hormuz"
        or chart.get("version") != CHART_VERSION
        or chart.get("application_image") != HORMUZ_IMAGE
        or chart.get("standard_kubernetes_apis_only") is not True
        or not SHA256_PATTERN.fullmatch(str(chart.get("package_sha256", "")))
    ):
        raise HelmProfileError("chart")
    dependencies = _mapping(value.get("dependencies"), "dependencies")
    if dependencies != {
        "postgres_image": POSTGRES_IMAGE,
        "postgres_ownership": "customer_fixture_outside_chart",
        "tls_ownership": "customer_controlled_outside_chart",
        "oidc_contract": "generic_bearer_jwt_resource_server",
    }:
        raise HelmProfileError("dependencies")
    topology = _mapping(value.get("topology"), "topology")
    if (
        topology.get("gateway_replicas") != 2
        or topology.get("distinct_gateway_nodes") != 2
        or topology.get("service_type") != "ClusterIP"
    ):
        raise HelmProfileError("topology")
    state = _mapping(value.get("state"), "state")
    _keys(
        state,
        {"successful_requests", "policy_denials", "provider_requests", "usage_events"},
        "state_keys",
    )
    successful = _positive_integer(state.get("successful_requests"), "successful_requests")
    denials = _positive_integer(state.get("policy_denials"), "policy_denials")
    provider = _positive_integer(state.get("provider_requests"), "provider_requests")
    usage = _positive_integer(state.get("usage_events"), "usage_events")
    if provider != successful or usage < successful or denials < 1:
        raise HelmProfileError("state_counts")
    checks = _mapping(value.get("checks"), "checks")
    if set(checks) != EXPECTED_CHECKS or any(item is not True for item in checks.values()):
        raise HelmProfileError("checks")
    if value.get("limitations") != EXPECTED_LIMITATIONS:
        raise HelmProfileError("limitations")

    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if any(pattern.search(encoded) for pattern in FORBIDDEN_EVIDENCE):
        raise HelmProfileError("evidence_contains_sensitive_value")
    lowered = encoded.lower()
    for forbidden_key in ("prompt", "response_body", "request_body", "employee_email", "secret_value"):
        if f'"{forbidden_key}"' in lowered:
            raise HelmProfileError("evidence_contains_content_field")


def write_evidence(path: Path, evidence: Mapping[str, Any]) -> None:
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
        raise HelmProfileError("evidence_output_exists") from error
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


def _mapping(value: Any, error: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise HelmProfileError(error)
    return value


def _sequence(value: Any, error: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise HelmProfileError(error)
    return value


def _string(value: Any, error: str) -> str:
    if not isinstance(value, str) or not value:
        raise HelmProfileError(error)
    return value


def _integer(value: Any, error: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise HelmProfileError(error)
    return value


def _positive_integer(value: Any, error: str) -> int:
    result = _integer(value, error)
    if result <= 0:
        raise HelmProfileError(error)
    return result


def _keys(value: Mapping[str, Any], expected: set[str], error: str) -> None:
    if set(value) != expected:
        raise HelmProfileError(error)


def _timestamp(value: Any) -> datetime:
    text = _string(value, "timestamp")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise HelmProfileError("timestamp") from error
    if parsed.tzinfo is None:
        raise HelmProfileError("timestamp")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
