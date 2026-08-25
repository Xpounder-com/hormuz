#!/usr/bin/env python3
"""Run and validate Hormuz's bounded coordinated multi-replica proof."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import sys
import time
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

try:
    from verify_helm_profile import (
        HORMUZ_IMAGE,
        HelmProfileError,
        load_json as load_kubernetes_evidence,
        validate_evidence as validate_kubernetes_evidence,
    )
except ModuleNotFoundError:  # Imported as tools.verify_multi_replica_operation in tests.
    from tools.verify_helm_profile import (
        HORMUZ_IMAGE,
        HelmProfileError,
        load_json as load_kubernetes_evidence,
        validate_evidence as validate_kubernetes_evidence,
    )


STATE_SCHEMA_ID = "hormuz.multi-replica-state-proof"
OPERATION_SCHEMA_ID = "hormuz.multi-replica-operation-proof"
SCHEMA_VERSION = 1
PROFILE = "kubernetes-enterprise-reference"
PLATFORM = "linux/amd64"
ROOT = Path(__file__).resolve().parents[1]
STATE_POSTGRES_IMAGE = (
    "postgres@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777"
)
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")

STATE_TESTS = {
    "atomic_budget_and_tenant_isolation": (
        "tests.test_postgres_multi_instance.PostgresMultiInstanceTests."
        "test_two_gateway_instances_share_atomic_organization_budget_reservations"
    ),
    "immutable_policy_activation": (
        "tests.test_postgres_multi_instance.PostgresMultiInstanceTests."
        "test_two_gateway_instances_converge_on_policy_activation_and_rollback"
    ),
    "request_attempt_ledger": (
        "tests.test_postgres_request_attempts.PostgresRequestAttemptTests."
        "test_attempt_ledger_is_append_only_tenant_scoped_and_conservative"
    ),
    "concurrent_audit_chain": (
        "tests.test_postgres_audit_chain.PostgresAuditChainTests."
        "test_commit_time_audit_chain_serializes_multi_instance_writes_and_is_tenant_isolated"
    ),
    "two_person_atomic_custody_projection": (
        "tests.test_postgres_custody_lifecycle.PostgresCustodyLifecycleTests."
        "test_two_person_disablement_appends_metadata_only_evidence_and_projects_atomically"
    ),
    "coordinated_custody_barrier": (
        "tests.test_postgres_custody_lifecycle.PostgresCustodyLifecycleTests."
        "test_two_live_replicas_acknowledge_before_atomic_restriction_activation"
    ),
    "partition_fencing": (
        "tests.test_postgres_custody_lifecycle.PostgresCustodyLifecycleTests."
        "test_partitioned_replica_fences_locally_before_its_lease_can_be_excluded"
    ),
    "duplicate_notification_and_stale_ack": (
        "tests.test_postgres_custody_lifecycle.PostgresCustodyLifecycleTests."
        "test_confirmed_not_applied_resolution_releases_only_an_uncommitted_prepared_barrier"
    ),
    "ambiguous_transport_no_replay": (
        "tests.test_gateway.GatewayIntegrationTests."
        "test_ambiguous_provider_transport_keeps_a_conservative_unknown_attempt"
    ),
    "interrupted_stream_no_replay": (
        "tests.test_gateway.GatewayIntegrationTests."
        "test_interrupted_provider_response_becomes_unknown_without_replay"
    ),
}

STATE_LIMITATIONS = [
    "disposable_postgresql_backend_only",
    "process_replicas_not_kubernetes_pods",
    "no_postgresql_failover_claim",
    "no_customer_sla",
]

OPERATION_EVENTS = [
    "service_baseline_verified",
    "restrictive_rollout_started",
    "restrictive_generation_converged",
    "rollback_started",
    "permissive_generation_converged",
    "graceful_inflight_started",
    "graceful_replica_withdrew_readiness",
    "sibling_service_request_succeeded",
    "graceful_inflight_finalized",
    "graceful_replacement_ready",
    "ambiguous_inflight_started",
    "owning_replica_force_deletion_requested",
    "abrupt_gateway_connection_closed",
    "abrupt_replacement_ready",
    "ambiguous_attempt_preserved_unknown",
    "final_service_and_evidence_verified",
]

TIMING_LIMITS_MS = {
    "restrictive_rollout_convergence": 600_000,
    "rollback_convergence": 600_000,
    "graceful_readiness_withdrawal": 30_000,
    "graceful_inflight_drain": 660_000,
    "abrupt_replacement_convergence": 600_000,
}

OPERATION_CHECKS = {
    "abrupt_loss_preserves_unknown",
    "approval_boundary",
    "atomic_budget_reservation",
    "audit_chain_serialization",
    "browser_sessions_excluded",
    "coordinated_custody_restriction",
    "duplicate_notification_idempotence",
    "graceful_inflight_drain",
    "immutable_policy_activation",
    "no_automatic_provider_replay",
    "readiness_gated_rollout",
    "replacement_startup",
    "request_attempt_ledger",
    "service_level_traffic",
    "stale_ack_rejected",
    "tenant_isolation",
    "usage_evidence_persistence",
}

OPERATION_LIMITATIONS = [
    "browser_sessions_out_of_scope",
    "compose_has_no_multi_replica_or_ha_claim",
    "disposable_kind_and_postgresql_fixtures_only",
    "no_broad_cni_portability_claim",
    "no_customer_sla",
    "no_multi_region_or_zone_failure_claim",
    "no_postgresql_leader_failover_claim",
    "no_provider_exactly_once_claim",
    "no_zero_interruption_claim_for_force_killed_inflight_streams",
]

FORBIDDEN_EVIDENCE = (
    re.compile(r"/Users/"),
    re.compile(r"/home/runner/"),
    re.compile(r"/private/tmp/"),
    re.compile(r"postgres(?:ql)?://", re.IGNORECASE),
    re.compile(r"\bBearer\s+", re.IGNORECASE),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{16,}\b"),
)


class MultiReplicaProofError(RuntimeError):
    """Raised when multi-replica evidence is missing, broad, or malformed."""


class _TimedResult(unittest.TextTestResult):
    def startTest(self, test: unittest.case.TestCase) -> None:  # noqa: N802
        self._started_at = time.monotonic_ns()
        super().startTest(test)

    def stopTest(self, test: unittest.case.TestCase) -> None:  # noqa: N802
        elapsed = time.monotonic_ns() - self._started_at
        self.durations_ms[test.id()] = max(1, (elapsed + 999_999) // 1_000_000)
        super().stopTest(test)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.durations_ms: dict[str, int] = {}
        self._started_at = 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    state = subparsers.add_parser("run-state-proof")
    state.add_argument("--output", required=True, type=Path)
    state.add_argument("--postgres-image", required=True)
    state.add_argument("--source-commit", required=True)

    operation = subparsers.add_parser("write-operation-proof")
    operation.add_argument("--output", required=True, type=Path)
    operation.add_argument("--source-commit", required=True)
    operation.add_argument("--kubernetes-evidence", required=True, type=Path)
    operation.add_argument("--state-evidence", required=True, type=Path)
    operation.add_argument("--event-log", required=True, type=Path)
    for timing in TIMING_LIMITS_MS:
        operation.add_argument(f"--{timing.replace('_', '-')}-ms", required=True, type=int)
    operation.add_argument("--successful-requests", required=True, type=int)
    operation.add_argument("--policy-denials", required=True, type=int)
    operation.add_argument("--provider-requests", required=True, type=int)
    operation.add_argument("--usage-events", required=True, type=int)
    operation.add_argument("--outcome-unknown-attempts", required=True, type=int)
    operation.add_argument("--uncertain-reservations", required=True, type=int)

    validate = subparsers.add_parser("validate")
    validate.add_argument("--evidence", required=True, type=Path)

    args = parser.parse_args(argv)
    try:
        if args.command == "run-state-proof":
            evidence = run_state_proof(
                source_commit=args.source_commit,
                postgres_image=args.postgres_image,
            )
            _write_exclusive(args.output, evidence)
            print(
                "verified shared multi-replica state: "
                f"tests={len(STATE_TESTS)} backend=postgresql"
            )
        elif args.command == "write-operation-proof":
            timings = {
                name: getattr(args, f"{name}_ms")
                for name in TIMING_LIMITS_MS
            }
            evidence = build_operation_evidence(
                source_commit=args.source_commit,
                kubernetes_evidence_path=args.kubernetes_evidence,
                state_evidence_path=args.state_evidence,
                event_log_path=args.event_log,
                timings_ms=timings,
                successful_requests=args.successful_requests,
                policy_denials=args.policy_denials,
                provider_requests=args.provider_requests,
                usage_events=args.usage_events,
                outcome_unknown_attempts=args.outcome_unknown_attempts,
                uncertain_reservations=args.uncertain_reservations,
            )
            validate_operation_evidence(evidence)
            _write_exclusive(args.output, evidence)
            print(
                "verified coordinated multi-replica operation: "
                f"events={len(OPERATION_EVENTS)} outcome_unknown={args.outcome_unknown_attempts}"
            )
        else:
            evidence = _load_json(args.evidence)
            schema_id = evidence.get("schema_id")
            if schema_id == STATE_SCHEMA_ID:
                validate_state_evidence(evidence)
            elif schema_id == OPERATION_SCHEMA_ID:
                validate_operation_evidence(evidence)
            else:
                raise MultiReplicaProofError("evidence_schema_unsupported")
            print(f"verified multi-replica evidence: schema={schema_id}")
    except (
        MultiReplicaProofError,
        HelmProfileError,
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as error:
        print(f"Multi-replica proof failed: {error}", file=sys.stderr)
        return 1
    return 0


def run_state_proof(*, source_commit: str, postgres_image: str) -> dict[str, Any]:
    _commit(source_commit)
    if postgres_image != STATE_POSTGRES_IMAGE:
        raise MultiReplicaProofError("state_postgres_image_invalid")
    if not os.environ.get("HORMUZ_TEST_POSTGRES_DSN"):
        raise MultiReplicaProofError("postgres_test_dsn_missing")
    root_text = str(ROOT)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for test_id in STATE_TESTS.values():
        loaded = loader.loadTestsFromName(test_id)
        if loaded.countTestCases() != 1:
            raise MultiReplicaProofError("state_test_unavailable")
        suite.addTests(loaded)
    if loader.errors:
        raise MultiReplicaProofError("state_test_import_failed")
    runner = unittest.TextTestRunner(stream=sys.stderr, verbosity=2, resultclass=_TimedResult)
    result = runner.run(suite)
    if (
        not result.wasSuccessful()
        or result.testsRun != len(STATE_TESTS)
        or result.skipped
        or result.expectedFailures
        or result.unexpectedSuccesses
    ):
        raise MultiReplicaProofError("state_proof_failed")
    assert isinstance(result, _TimedResult)
    checks = {
        name: {
            "test_id": test_id,
            "status": "passed",
            "duration_ms": result.durations_ms[test_id],
        }
        for name, test_id in sorted(STATE_TESTS.items())
    }
    evidence = {
        "schema_id": STATE_SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "observed_at": _now(),
        "source_commit": source_commit,
        "runner": {
            "os": platform.system().lower(),
            "architecture": platform.machine().lower(),
            "python": platform.python_version(),
        },
        "database": {
            "backend": "postgresql",
            "image": postgres_image,
        },
        "checks": checks,
        "limitations": STATE_LIMITATIONS,
        "verdict": "verified_shared_state_process_replicas",
    }
    validate_state_evidence(evidence)
    return evidence


def validate_state_evidence(value: Mapping[str, Any]) -> None:
    _keys(
        value,
        {
            "schema_id",
            "schema_version",
            "observed_at",
            "source_commit",
            "runner",
            "database",
            "checks",
            "limitations",
            "verdict",
        },
        "state_evidence_keys",
    )
    if (
        value.get("schema_id") != STATE_SCHEMA_ID
        or value.get("schema_version") != SCHEMA_VERSION
        or value.get("verdict") != "verified_shared_state_process_replicas"
    ):
        raise MultiReplicaProofError("state_evidence_identity")
    _timestamp(value.get("observed_at"))
    _commit(value.get("source_commit"))
    runner = _mapping(value.get("runner"), "state_runner")
    if set(runner) != {"os", "architecture", "python"}:
        raise MultiReplicaProofError("state_runner")
    for field in runner.values():
        if not isinstance(field, str) or not field:
            raise MultiReplicaProofError("state_runner")
    database = _mapping(value.get("database"), "state_database")
    if database != {"backend": "postgresql", "image": STATE_POSTGRES_IMAGE}:
        raise MultiReplicaProofError("state_database")
    checks = _mapping(value.get("checks"), "state_checks")
    if set(checks) != set(STATE_TESTS):
        raise MultiReplicaProofError("state_checks")
    for name, test_id in STATE_TESTS.items():
        check = _mapping(checks.get(name), "state_check")
        if set(check) != {"test_id", "status", "duration_ms"}:
            raise MultiReplicaProofError("state_check_shape")
        if check.get("test_id") != test_id or check.get("status") != "passed":
            raise MultiReplicaProofError("state_check_result")
        _positive_integer(check.get("duration_ms"), "state_check_duration")
    if value.get("limitations") != STATE_LIMITATIONS:
        raise MultiReplicaProofError("state_limitations")
    _safe_evidence(value)


def build_operation_evidence(
    *,
    source_commit: str,
    kubernetes_evidence_path: Path,
    state_evidence_path: Path,
    event_log_path: Path,
    timings_ms: Mapping[str, int],
    successful_requests: int,
    policy_denials: int,
    provider_requests: int,
    usage_events: int,
    outcome_unknown_attempts: int,
    uncertain_reservations: int,
) -> dict[str, Any]:
    _commit(source_commit)
    kubernetes = load_kubernetes_evidence(kubernetes_evidence_path)
    validate_kubernetes_evidence(kubernetes)
    state = _load_json(state_evidence_path)
    validate_state_evidence(state)
    if state.get("source_commit") != source_commit:
        raise MultiReplicaProofError("state_source_commit_mismatch")
    state_runner = _mapping(state.get("runner"), "state_runner")
    if state_runner.get("os") != "linux" or state_runner.get("architecture") not in {
        "amd64",
        "x86_64",
    }:
        raise MultiReplicaProofError("state_runner_not_linux_amd64")
    events = _load_event_log(event_log_path)
    if events != OPERATION_EVENTS:
        raise MultiReplicaProofError("operation_event_sequence")
    chart = _mapping(kubernetes.get("chart"), "kubernetes_chart")
    topology = _mapping(kubernetes.get("topology"), "kubernetes_topology")
    evidence = {
        "schema_id": OPERATION_SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "observed_at": _now(),
        "source_commit": source_commit,
        "profile": PROFILE,
        "platform": PLATFORM,
        "inputs": {
            "application_image": chart.get("application_image"),
            "chart_package_sha256": chart.get("package_sha256"),
            "kubernetes_evidence_sha256": _sha256(kubernetes_evidence_path),
            "state_evidence_sha256": _sha256(state_evidence_path),
        },
        "topology": {
            "gateway_replicas": topology.get("gateway_replicas"),
            "distinct_gateway_nodes": topology.get("distinct_gateway_nodes"),
            "service_type": topology.get("service_type"),
        },
        "retry_and_sessions": {
            "automatic_provider_replay": False,
            "client_retry_treatment": "new_attempt",
            "supported_client_idempotency_key": None,
            "browser_sessions": "excluded",
        },
        "events": [
            {"sequence": index, "name": name}
            for index, name in enumerate(events, start=1)
        ],
        "timings_ms": dict(timings_ms),
        "state": {
            "successful_requests": successful_requests,
            "policy_denials": policy_denials,
            "provider_requests": provider_requests,
            "usage_events": usage_events,
            "outcome_unknown_attempts": outcome_unknown_attempts,
            "uncertain_reservations": uncertain_reservations,
        },
        "checks": {name: True for name in sorted(OPERATION_CHECKS)},
        "limitations": OPERATION_LIMITATIONS,
        "verdict": "verified_coordinated_multi_replica_operation",
    }
    return evidence


def validate_operation_evidence(value: Mapping[str, Any]) -> None:
    _keys(
        value,
        {
            "schema_id",
            "schema_version",
            "observed_at",
            "source_commit",
            "profile",
            "platform",
            "inputs",
            "topology",
            "retry_and_sessions",
            "events",
            "timings_ms",
            "state",
            "checks",
            "limitations",
            "verdict",
        },
        "operation_evidence_keys",
    )
    if (
        value.get("schema_id") != OPERATION_SCHEMA_ID
        or value.get("schema_version") != SCHEMA_VERSION
        or value.get("profile") != PROFILE
        or value.get("platform") != PLATFORM
        or value.get("verdict") != "verified_coordinated_multi_replica_operation"
    ):
        raise MultiReplicaProofError("operation_evidence_identity")
    _timestamp(value.get("observed_at"))
    _commit(value.get("source_commit"))
    inputs = _mapping(value.get("inputs"), "operation_inputs")
    if set(inputs) != {
        "application_image",
        "chart_package_sha256",
        "kubernetes_evidence_sha256",
        "state_evidence_sha256",
    }:
        raise MultiReplicaProofError("operation_inputs")
    if inputs.get("application_image") != HORMUZ_IMAGE:
        raise MultiReplicaProofError("operation_image")
    for name in (
        "chart_package_sha256",
        "kubernetes_evidence_sha256",
        "state_evidence_sha256",
    ):
        if not SHA256_PATTERN.fullmatch(str(inputs.get(name, ""))):
            raise MultiReplicaProofError("operation_digest")
    topology = _mapping(value.get("topology"), "operation_topology")
    if topology != {
        "gateway_replicas": 2,
        "distinct_gateway_nodes": 2,
        "service_type": "ClusterIP",
    }:
        raise MultiReplicaProofError("operation_topology")
    if value.get("retry_and_sessions") != {
        "automatic_provider_replay": False,
        "client_retry_treatment": "new_attempt",
        "supported_client_idempotency_key": None,
        "browser_sessions": "excluded",
    }:
        raise MultiReplicaProofError("operation_retry_sessions")
    raw_events = value.get("events")
    if not isinstance(raw_events, list):
        raise MultiReplicaProofError("operation_events")
    expected_events = [
        {"sequence": index, "name": name}
        for index, name in enumerate(OPERATION_EVENTS, start=1)
    ]
    if raw_events != expected_events:
        raise MultiReplicaProofError("operation_events")
    timings = _mapping(value.get("timings_ms"), "operation_timings")
    if set(timings) != set(TIMING_LIMITS_MS):
        raise MultiReplicaProofError("operation_timings")
    for name, limit in TIMING_LIMITS_MS.items():
        observed = _positive_integer(timings.get(name), "operation_timing")
        if observed > limit:
            raise MultiReplicaProofError(f"operation_timing_exceeded:{name}")
    state = _mapping(value.get("state"), "operation_state")
    _keys(
        state,
        {
            "successful_requests",
            "policy_denials",
            "provider_requests",
            "usage_events",
            "outcome_unknown_attempts",
            "uncertain_reservations",
        },
        "operation_state_keys",
    )
    successful = _positive_integer(state.get("successful_requests"), "successful_requests")
    denials = _positive_integer(state.get("policy_denials"), "policy_denials")
    provider = _positive_integer(state.get("provider_requests"), "provider_requests")
    usage = _positive_integer(state.get("usage_events"), "usage_events")
    unknown = _positive_integer(state.get("outcome_unknown_attempts"), "outcome_unknown_attempts")
    uncertain = _positive_integer(state.get("uncertain_reservations"), "uncertain_reservations")
    if unknown != 1 or uncertain != unknown:
        raise MultiReplicaProofError("operation_unknown_state")
    if provider != successful + unknown or usage < successful or denials < 1:
        raise MultiReplicaProofError("operation_state_counts")
    checks = _mapping(value.get("checks"), "operation_checks")
    if set(checks) != OPERATION_CHECKS or any(item is not True for item in checks.values()):
        raise MultiReplicaProofError("operation_checks")
    if value.get("limitations") != OPERATION_LIMITATIONS:
        raise MultiReplicaProofError("operation_limitations")
    _safe_evidence(value)


def _load_event_log(path: Path) -> list[str]:
    events: list[str] = []
    for expected_sequence, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        fields = line.split("|", maxsplit=1)
        if len(fields) != 2 or fields[0] != str(expected_sequence):
            raise MultiReplicaProofError("operation_event_log_invalid")
        events.append(fields[1])
    return events


def _load_json(path: Path) -> dict[str, Any]:
    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise MultiReplicaProofError("duplicate_json_key")
            value[key] = item
        return value

    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=object_pairs)
    if not isinstance(value, dict):
        raise MultiReplicaProofError("json_root_not_object")
    return value


def _write_exclusive(path: Path, value: Mapping[str, Any]) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def _safe_evidence(value: Mapping[str, Any]) -> None:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if any(pattern.search(encoded) for pattern in FORBIDDEN_EVIDENCE):
        raise MultiReplicaProofError("evidence_contains_sensitive_value")
    lowered = encoded.lower()
    for forbidden in ("prompt", "request_body", "response_body", "employee_email", "secret_value"):
        if f'"{forbidden}"' in lowered:
            raise MultiReplicaProofError("evidence_contains_content_field")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _timestamp(value: Any) -> None:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise MultiReplicaProofError("timestamp_invalid")
    try:
        datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as error:
        raise MultiReplicaProofError("timestamp_invalid") from error


def _commit(value: Any) -> str:
    if not isinstance(value, str) or not COMMIT_PATTERN.fullmatch(value):
        raise MultiReplicaProofError("source_commit_invalid")
    return value


def _mapping(value: Any, error: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MultiReplicaProofError(error)
    return value


def _keys(value: Mapping[str, Any], expected: set[str], error: str) -> None:
    if set(value) != expected:
        raise MultiReplicaProofError(error)


def _positive_integer(value: Any, error: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise MultiReplicaProofError(error)
    return value


if __name__ == "__main__":
    raise SystemExit(main())
