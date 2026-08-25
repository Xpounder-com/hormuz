from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest import mock

from hormuz.config import GatewayConfig
from tools import verify_postgres_ha_reference as ha


ROOT = Path(__file__).resolve().parents[1]
HA_ROOT = ROOT / "deploy" / "kubernetes" / "conformance" / "postgres-ha"


def snapshot(*, usage: int, security: int, attempts: int, chain: int) -> dict[str, object]:
    return {
        "schema_id": ha.STATE_SCHEMA_ID,
        "schema_version": 1,
        "command": "snapshot",
        "control_fingerprint": "a" * 64,
        "policy_generation": 1,
        "policy_administrator_count": 1,
        "custody_administrator_count": 2,
        "custody_projection_version": 1,
        "custody_restriction": "provider_credential_disabled",
        "usage_events": usage,
        "security_events": security,
        "request_attempts": attempts,
        "pending_attempts": 1,
        "outcome_unknown_attempts": 0,
        "uncertain_reservations": 1,
        "audit_chain_sequence": chain,
        "audit_chain_verified": True,
        "isolation_tenant_rows": 0,
    }


def valid_observations() -> dict[str, object]:
    before = snapshot(usage=1, security=1, attempts=2, chain=10)
    after_recovery = snapshot(usage=2, security=2, attempts=3, chain=12)
    return {
        "source_commit": "b" * 40,
        "docker_engine": "28.4.0",
        "helm_chart_sha256": "c" * 64,
        "topology": {
            "kind_nodes": 6,
            "worker_nodes": 5,
            "postgresql_worker_nodes": 3,
            "gateway_worker_nodes": 2,
            "postgresql_instances": 3,
            "distinct_postgresql_nodes": 3,
            "gateway_replicas": 2,
            "synchronous_method": "any",
            "synchronous_number": 1,
            "data_durability": "required",
            "failover_quorum": True,
            "isolation_check": True,
            "primary_lease": {
                "lease_duration_seconds": 15,
                "renew_deadline_seconds": 10,
                "retry_period_seconds": 2,
                "released_lease_duration_seconds": 1,
            },
        },
        "pool_bounds": {
            "minimum_connections_per_replica": 1,
            "maximum_connections_per_replica": 4,
            "acquire_timeout_seconds": 5,
            "maximum_waiting_per_replica": 8,
            "reconnect_horizon_seconds": 15,
        },
        "primary_loss": {
            "trigger": "unexpected_worker_pause",
            "previous_primary_changed": True,
            "lease_holder_matches_current_primary": True,
            "rw_endpoint_matches_current_primary": True,
            "former_primary_rejoined_as_replica": True,
            "former_primary_fenced_before_rejoin": True,
            "gateway_replicas_observed": 2,
            "gateways_not_ready": 2,
            "backpressure_requests": 32,
            "gateway_storage_denials": 32,
            "provider_requests_before_denials": 2,
            "provider_requests_after_denials": 2,
            "provider_requests_after_recovery": 3,
            "gateway_processes_reused": True,
            "ambiguous_attempts_preserved": 1,
            "uncertain_reservations_preserved": 1,
            "automatic_provider_replays": 0,
        },
        "quorum_loss": {
            "trigger": "primary_and_one_replica_worker_pause",
            "unavailable_postgresql_instances": 2,
            "promotion_prevented": True,
            "failover_quorum_reported_insufficient": True,
            "rw_ready_addresses": 0,
            "stale_primary_endpoint_absent": True,
            "gateway_replicas_observed": 2,
            "gateways_not_ready": 2,
            "backpressure_requests": 32,
            "gateway_storage_denials": 32,
            "provider_requests_before_denials": 3,
            "provider_requests_after_denials": 3,
            "gateway_processes_reused_after_recovery": True,
        },
        "state": {
            "before": before,
            "after_failover": copy.deepcopy(before),
            "after_recovery": after_recovery,
            "after_quorum_recovery": copy.deepcopy(after_recovery),
        },
        "timings_ms": {
            "positive_fail_closed": 10_000,
            "primary_promotion": 45_000,
            "gateway_recovery": 5_000,
            "former_primary_rejoin": 30_000,
            "negative_fail_closed": 10_000,
            "quorum_refusal_observation": 30_000,
            "negative_recovery": 40_000,
            "maximum_storage_denial": 6_000,
        },
        "events": [
            {"sequence": sequence, "event": event}
            for sequence, event in enumerate(ha.EVENTS, start=1)
        ],
    }


class PostgresHAReferenceTests(unittest.TestCase):
    def test_exact_observations_build_a_strict_content_free_reference(self) -> None:
        evidence = ha.build_evidence(valid_observations())
        ha.validate_evidence(evidence)
        serialized = json.dumps(evidence, sort_keys=True)
        self.assertNotIn("postgresql://", serialized)
        self.assertEqual(evidence["product_boundary"]["helm_chart_installs_postgresql"], False)
        self.assertEqual(evidence["versions"]["cloudnativepg"], "1.30.0")
        self.assertIn("no_customer_sla", evidence["limitations"])

    def test_evidence_rejects_unsafe_promotion_replay_and_state_rewrite(self) -> None:
        for mutation in ("promotion", "replay", "state"):
            with self.subTest(mutation=mutation):
                value = valid_observations()
                if mutation == "promotion":
                    value["quorum_loss"]["promotion_prevented"] = False  # type: ignore[index]
                elif mutation == "replay":
                    value["primary_loss"]["provider_requests_after_denials"] = 3  # type: ignore[index]
                else:
                    value["state"]["after_failover"]["control_fingerprint"] = "d" * 64  # type: ignore[index]
                with self.assertRaises(ha.PostgresHAProofError):
                    ha.build_evidence(value)

    def test_operator_manifest_checksum_is_verified_before_exact_image_pin(self) -> None:
        source = (
            f"env: {ha.CNPG_MUTABLE_IMAGE}\n"
            f"image: {ha.CNPG_MUTABLE_IMAGE}\n"
        ).encode("utf-8")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_path = root / "operator.yaml"
            output_path = root / "operator-pinned.yaml"
            input_path.write_bytes(source)
            with mock.patch.object(ha, "CNPG_MANIFEST_SHA256", hashlib.sha256(source).hexdigest()):
                ha.prepare_operator_manifest(input_path, output_path)
            pinned = output_path.read_text(encoding="utf-8")
            self.assertEqual(pinned.count(ha.CNPG_OPERATOR_AMD64_IMAGE), 2)
            self.assertEqual(oct(output_path.stat().st_mode & 0o777), "0o600")

            other_path = root / "operator-other.yaml"
            other_path.write_bytes(source + b"# drift\n")
            with self.assertRaisesRegex(ha.PostgresHAProofError, "operator_manifest_checksum_invalid"):
                ha.prepare_operator_manifest(other_path, root / "rejected.yaml")

    def test_duplicate_members_and_existing_outputs_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            duplicate = root / "duplicate.json"
            duplicate.write_text('{"schema_id":"one","schema_id":"two"}', encoding="utf-8")
            with self.assertRaisesRegex(ha.PostgresHAProofError, "duplicate_json_key"):
                ha.load_json(duplicate)
            output = root / "summary.json"
            output.write_text("occupied", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                ha.write_exclusive(output, {"safe": True})

    def test_fixtures_pin_the_approved_topology_without_changing_the_chart_contract(self) -> None:
        cluster = (HA_ROOT / "cluster.yaml").read_text(encoding="utf-8")
        kind = (HA_ROOT / "kind.yaml").read_text(encoding="utf-8")
        values = (HA_ROOT / "helm-values.yaml").read_text(encoding="utf-8")
        runner = (ROOT / "tools" / "verify_postgres_ha_reference.sh").read_text(encoding="utf-8")
        bootstrap = (HA_ROOT / "bootstrap.py").read_text(encoding="utf-8")
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        reference = (HA_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("instances: 3", cluster)
        self.assertIn("method: any", cluster)
        self.assertIn("number: 1", cluster)
        self.assertIn("dataDurability: required", cluster)
        self.assertIn("failoverQuorum: true", cluster)
        self.assertIn("isolationCheck:", cluster)
        self.assertIn(ha.POSTGRES_IMAGE, cluster)
        self.assertEqual(kind.count("role: worker"), 5)
        self.assertIn("io.hormuz.proof-role: postgres", values)
        self.assertIn("docker pause", runner)
        self.assertIn("rw_ready_addresses", runner)
        self.assertIn("storage-backpressure", runner)
        self.assertIn("blocking-request", runner)
        self.assertIn("failoverquorum", runner)
        self.assertIn("wait_for_failover_quorum_ready", runner)
        self.assertIn("wait_for_job_complete", runner)
        self.assertIn("for attempt in $(seq 1 120)", runner)
        self.assertIn('status.get("method") == "ANY"', runner)
        self.assertIn('"schema_version": status.version', bootstrap)
        self.assertIn('"schema_complete": status.complete', bootstrap)
        self.assertNotIn("len(applied)", bootstrap)
        self.assertLess(
            runner.index("provider_control POST /control/block/release"),
            runner.index("gateway_fail_closed positive"),
        )
        self.assertIn("name: PostgreSQL HA failover reference", workflow)
        self.assertIn("CloudNativePG `1.30.0`", reference)
        self.assertIn("verification infrastructure", reference)
        self.assertIn("not part of the", reference)
        self.assertIn("product contract", reference)
        chart = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "deploy" / "helm" / "hormuz" / "templates").glob("*.yaml")
        )
        self.assertNotIn("postgresql.cnpg.io", chart)
        self.assertNotIn("kind: Cluster\n", chart)

    def test_state_configuration_is_valid_with_separate_control_credentials(self) -> None:
        names = (
            "HORMUZ_POSTGRES_DSN",
            "HORMUZ_POSTGRES_MIGRATION_DSN",
            "HORMUZ_POLICY_CONTROL_DSN",
            "HORMUZ_CUSTODY_CONTROL_DSN",
            "HORMUZ_CUSTODY_EXECUTOR_DSN",
            "HORMUZ_TOKEN",
            "HORMUZ_BOB_TOKEN",
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "HORMUZ_OPENBAO_TOKEN",
        )
        environment = {
            name: f"synthetic-{index:02d}-credential-value"
            for index, name in enumerate(names)
        }
        config = GatewayConfig.load(HA_ROOT / "state-config.json", environ=environment)
        self.assertEqual(config.organization_ids, ("kubernetes-proof-organization",))
        self.assertEqual(config.policy_control.mode, "postgresql")
        self.assertEqual(config.custody_control.mode, "postgresql")

    def test_probe_applies_concurrent_bounded_storage_backpressure(self) -> None:
        probe = runpy.run_path(
            str(ROOT / "deploy" / "kubernetes" / "conformance" / "probe.py")
        )
        run_backpressure = probe["_run_storage_backpressure"]
        request = mock.Mock(return_value={"status": 503})
        original = run_backpressure.__globals__["_governed_request"]
        run_backpressure.__globals__["_governed_request"] = request
        output = io.StringIO()
        try:
            with (
                mock.patch.object(probe["time"], "monotonic_ns", side_effect=(0, 6_000_000_000)),
                contextlib.redirect_stdout(output),
            ):
                self.assertEqual(
                    run_backpressure(
                        target="http://hormuz.invalid",
                        headers={"Authorization": "synthetic"},
                        concurrency=16,
                        expected_status=503,
                    ),
                    0,
                )
        finally:
            run_backpressure.__globals__["_governed_request"] = original
        value = json.loads(output.getvalue())
        self.assertEqual(
            value,
            {
                "command": "storage-backpressure",
                "duration_ms": 6000,
                "requests": 16,
                "status": 503,
                "storage_denials": 16,
            },
        )
        self.assertEqual(request.call_count, 16)


if __name__ == "__main__":
    unittest.main()
