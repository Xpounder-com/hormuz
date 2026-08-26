from __future__ import annotations

import ast
import json
import stat
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from hormuz.config import GatewayConfig
from tools import verify_disaster_recovery_reference as recovery


ROOT = Path(__file__).resolve().parents[1]
DR_ROOT = ROOT / "deploy" / "kubernetes" / "conformance" / "disaster-recovery"


class DisasterRecoveryReferenceTests(unittest.TestCase):
    def admission_input(self) -> dict[str, object]:
        digest = "sha256:" + ("e" * 64)
        classes = {
            identifier: {
                "fingerprint": digest,
                "latest_committed_marker_at": "2026-08-26T11:59:55+00:00",
                "record_count": 1,
            }
            for identifier in recovery._DATABASE_STATE_CLASSES
        }
        facts = {
            "policy_active_version": "version-1",
            "policy_generation": 1,
            "policy_administrator_count": 1,
            "custody_administrator_count": 2,
            "custody_retention_days": 365,
            "custody_legal_hold": False,
            "custody_projection_version": 1,
            "custody_restriction": "provider_credential_disabled",
            "unresolved_coordination_barriers": 0,
            "outcome_unknown_attempts": 1,
            "uncertain_reservations": 1,
            "audit_chain_epoch": 1,
            "audit_chain_sequence": 2,
            "current_checkpoint_sequence": 2,
            "stale_checkpoint_sequence": 1,
            "tenant_isolation_rows": 0,
            "migration_version": 8,
        }
        snapshot = {
            "schema_id": "hormuz.disaster-recovery-state-snapshot",
            "schema_version": 1,
            "command": "snapshot",
            "organization_id": "kubernetes-proof-organization",
            "manifest_fingerprint": digest,
            "state_classes": classes,
            "admission_facts": facts,
        }
        return {
            "source_snapshot": deepcopy(snapshot),
            "recovered_snapshot": deepcopy(snapshot),
            "current_checkpoint": {
                "schema_id": "hormuz.audit-chain-checkpoint",
                "schema_version": 1,
                "checkpoint_id": "00000000-0000-4000-8000-000000000102",
                "organization_id": "kubernetes-proof-organization",
                "chain_version": 1,
                "chain_epoch": 1,
                "sequence": 2,
                "head_digest": "e" * 64,
                "created_at": "2026-08-26T11:59:56+00:00",
            },
            "configuration": {
                "source_fingerprint": digest,
                "recovered_fingerprint": digest,
                "latest_recovered_committed_marker_at": "2026-08-26T11:59:57+00:00",
            },
            "secret_envelope": {
                "source_fingerprint": digest,
                "recovered_fingerprint": digest,
                "latest_recovered_committed_marker_at": "2026-08-26T11:59:58+00:00",
            },
            "custody_key_canary_verified": True,
        }

    def observations(self) -> dict[str, object]:
        digest = "sha256:" + ("a" * 64)
        timestamps = {
            "failure_injection_at": "2026-08-26T12:00:00+00:00",
            "incident_detected_at": "2026-08-26T12:00:01+00:00",
            "incident_declared_at": "2026-08-26T12:00:02+00:00",
            "authorized_recovery_execution_started_at": "2026-08-26T12:00:03+00:00",
            "restore_started_at": "2026-08-26T12:00:04+00:00",
            "recovered_database_ready_at": "2026-08-26T12:00:30+00:00",
            "admission_passed_at": "2026-08-26T12:00:40+00:00",
            "required_failure_paths_passed_at": "2026-08-26T12:00:50+00:00",
            "recovered_environment_ready_for_promotion_at": "2026-08-26T12:01:03+00:00",
            "traffic_promoted_at": "2026-08-26T12:01:04+00:00",
            "first_successful_governed_request_after_promotion_at": "2026-08-26T12:01:05+00:00",
        }
        state_classes = {
            identifier: {
                "source_fingerprint": digest,
                "recovered_fingerprint": digest,
                "latest_recovered_committed_marker_at": "2026-08-26T11:59:55+00:00",
            }
            for identifier in recovery.STATE_CLASSES
        }
        denied = {
            "failure_observed": True,
            "admission_denied": True,
            "promotion_blocked": True,
            "provider_request_delta": 0,
        }
        return {
            "source_commit": "b" * 40,
            "docker_engine": "28.3.3",
            "helm_chart_sha256": "c" * 64,
            "timestamps": timestamps,
            "phase_durations_ms": {
                "detection": 1_000,
                "incident_declaration": 1_000,
                "recovery_authorization": 1_000,
                "recovery_environment_preparation": 1_000,
                "restore_and_wal_replay": 26_000,
                "admission_validation": 10_000,
                "required_failure_path_validation": 10_000,
                "application_startup": 13_000,
                "traffic_promotion": 1_000,
                "first_governed_request": 1_000,
            },
            "backup": {
                "method": "physical_base_backup_plus_continuous_wal",
                "base_backup_sha256": digest,
                "backup_manifest_sha256": digest,
                "wal_archive_sha256": digest,
                "wal_segment_count": 3,
                "base_backup_completed_at": "2026-08-26T11:59:40+00:00",
                "pg_verifybackup_passed": True,
                "backup_completed_before_failure": True,
                "named_restore_point_reached": True,
            },
            "retention_and_authority": {
                "base_backup_frequency_seconds": 86_400,
                "wal_archive_continuous": True,
                "backup_retention_days": 35,
                "wal_retention_days": 35,
                "encryption_at_rest_required": True,
                "backup_writer_cannot_restore_or_promote": True,
                "runtime_cannot_backup_restore_or_promote": True,
                "restore_requires_authorized_operator": True,
                "monitor_backup_age_wal_lag_and_restore_tests": True,
                "expiry_never_shortens_immutable_audit_retention": True,
            },
            "state_classes": state_classes,
            "admission": {
                "source_state_manifest_sha256": digest,
                "recovered_state_manifest_sha256": digest,
                "gateway_replicas_ready": 2,
                "readiness_withheld_until_validation": True,
                "provider_requests_before_promotion": 0,
            },
            "failure_paths": {
                identifier: dict(denied) for identifier in recovery.FAILURE_PATHS
            },
            "promotion": {
                "authorized_operator_promoted": True,
                "runtime_credential_cannot_promote": True,
                "first_governed_request_status": 200,
                "provider_requests_after_first_governed_request": 1,
                "automatic_provider_replays": 0,
                "rollback_target_preserved": True,
            },
        }

    def test_builds_strict_content_free_reference_evidence(self) -> None:
        evidence = recovery.build_evidence(self.observations())

        recovery.validate_evidence(evidence)
        self.assertEqual(evidence["schema_id"], recovery.SCHEMA_ID)
        self.assertEqual(evidence["schema_version"], 1)
        self.assertEqual(evidence["verdict"], "verified")
        self.assertEqual(evidence["objectives"]["achieved_maximum_rpo_seconds"], 5.0)
        self.assertEqual(evidence["objectives"]["achieved_internal_rto_ms"], 60_000)
        self.assertEqual(evidence["objectives"]["complete_end_to_end_recovery_ms"], 65_000)
        self.assertEqual(
            [item["id"] for item in evidence["state_coverage"]],
            list(recovery.STATE_CLASSES),
        )
        serialized = json.dumps(evidence, sort_keys=True)
        self.assertNotIn("postgresql://", serialized)
        self.assertNotIn("/private/tmp/", serialized)
        self.assertNotIn("customer document", serialized)

    def test_admission_merges_all_external_and_database_state_classes(self) -> None:
        admission = recovery.build_admission(self.admission_input())

        self.assertTrue(admission["admitted"])
        self.assertEqual(tuple(admission["state_classes"]), recovery.STATE_CLASSES)
        self.assertEqual(admission["checks"], list(recovery.ADMISSION_CHECKS))

    def test_admission_rejects_stale_checkpoint_and_unavailable_custody_key(self) -> None:
        stale = self.admission_input()
        stale["current_checkpoint"]["sequence"] = 1
        with self.assertRaisesRegex(
            recovery.DisasterRecoveryProofError, "recovery_checkpoint_not_latest"
        ):
            recovery.build_admission(stale)

        unavailable = self.admission_input()
        unavailable["custody_key_canary_verified"] = False
        with self.assertRaisesRegex(
            recovery.DisasterRecoveryProofError, "recovery_custody_key_unavailable"
        ):
            recovery.build_admission(unavailable)

    def test_admission_rejects_changed_external_generations(self) -> None:
        configuration = self.admission_input()
        configuration["configuration"]["recovered_fingerprint"] = "sha256:" + (
            "f" * 64
        )
        with self.assertRaisesRegex(
            recovery.DisasterRecoveryProofError,
            "configuration_fingerprint_mismatch",
        ):
            recovery.build_admission(configuration)

        secret = self.admission_input()
        secret["secret_envelope"]["recovered_fingerprint"] = "sha256:" + (
            "f" * 64
        )
        with self.assertRaisesRegex(
            recovery.DisasterRecoveryProofError,
            "secret_envelope_fingerprint_mismatch",
        ):
            recovery.build_admission(secret)

    def test_admission_rejects_partial_restore_failed_coordination_and_cross_tenant(self) -> None:
        partial = self.admission_input()
        partial["recovered_snapshot"]["manifest_fingerprint"] = "sha256:" + ("f" * 64)
        with self.assertRaisesRegex(
            recovery.DisasterRecoveryProofError, "recovery_state_manifest_mismatch"
        ):
            recovery.build_admission(partial)

        coordination = self.admission_input()
        coordination["source_snapshot"]["admission_facts"][
            "unresolved_coordination_barriers"
        ] = 1
        coordination["recovered_snapshot"]["admission_facts"][
            "unresolved_coordination_barriers"
        ] = 1
        with self.assertRaisesRegex(
            recovery.DisasterRecoveryProofError, "admission_invalid"
        ):
            recovery.build_admission(coordination)

        tenant = self.admission_input()
        tenant["recovered_snapshot"]["organization_id"] = "other-tenant"
        with self.assertRaisesRegex(
            recovery.DisasterRecoveryProofError, "recovered_snapshot_schema_invalid"
        ):
            recovery.build_admission(tenant)

    def test_rejects_any_state_class_missing_or_changed(self) -> None:
        missing = self.observations()
        del missing["state_classes"][recovery.STATE_CLASSES[-1]]
        with self.assertRaisesRegex(
            recovery.DisasterRecoveryProofError, "state_classes_fields_invalid"
        ):
            recovery.build_evidence(missing)

        changed = self.observations()
        changed["state_classes"][recovery.STATE_CLASSES[0]]["recovered_fingerprint"] = (
            "sha256:" + ("d" * 64)
        )
        with self.assertRaisesRegex(
            recovery.DisasterRecoveryProofError, "fingerprint_mismatch"
        ):
            recovery.build_evidence(changed)

    def test_rejects_rpo_or_internal_rto_over_target(self) -> None:
        stale = self.observations()
        stale["state_classes"][recovery.STATE_CLASSES[3]][
            "latest_recovered_committed_marker_at"
        ] = "2026-08-26T11:54:59+00:00"
        with self.assertRaisesRegex(
            recovery.DisasterRecoveryProofError,
            "rpo_schema_migration_ledger_exceeded",
        ):
            recovery.build_evidence(stale)

        slow = self.observations()
        slow["timestamps"]["recovered_environment_ready_for_promotion_at"] = (
            "2026-08-26T13:00:04+00:00"
        )
        slow["timestamps"]["traffic_promoted_at"] = "2026-08-26T13:00:05+00:00"
        slow["timestamps"]["first_successful_governed_request_after_promotion_at"] = (
            "2026-08-26T13:00:06+00:00"
        )
        slow["phase_durations_ms"]["application_startup"] = 3_554_000
        with self.assertRaisesRegex(
            recovery.DisasterRecoveryProofError, "internal_rto_exceeded"
        ):
            recovery.build_evidence(slow)

    def test_rejects_out_of_order_or_hidden_clock(self) -> None:
        observations = self.observations()
        observations["timestamps"]["incident_detected_at"] = (
            "2026-08-26T11:59:59+00:00"
        )
        with self.assertRaisesRegex(
            recovery.DisasterRecoveryProofError, "timestamps_not_ordered"
        ):
            recovery.build_evidence(observations)

        evidence = recovery.build_evidence(self.observations())
        evidence["objectives"]["achieved_internal_rto_ms"] = 1
        with self.assertRaisesRegex(
            recovery.DisasterRecoveryProofError,
            "objectives_exceeded_or_inconsistent",
        ):
            recovery.validate_evidence(evidence)

    def test_every_negative_path_must_block_promotion_and_egress(self) -> None:
        for name in recovery.FAILURE_PATHS:
            with self.subTest(name=name):
                observations = self.observations()
                observations["failure_paths"][name]["provider_request_delta"] = 1
                with self.assertRaisesRegex(
                    recovery.DisasterRecoveryProofError,
                    f"failure_path_{name}_invalid",
                ):
                    recovery.build_evidence(observations)

    def test_phase_durations_are_derived_from_the_complete_clock(self) -> None:
        observations = self.observations()
        observations["phase_durations_ms"]["required_failure_path_validation"] = 1
        with self.assertRaisesRegex(
            recovery.DisasterRecoveryProofError,
            "phase_duration_required_failure_path_validation_invalid",
        ):
            recovery.build_evidence(observations)

    def test_runtime_cannot_restore_or_promote(self) -> None:
        observations = self.observations()
        observations["retention_and_authority"][
            "runtime_cannot_backup_restore_or_promote"
        ] = False
        with self.assertRaisesRegex(
            recovery.DisasterRecoveryProofError,
            "retention_and_authority_invalid",
        ):
            recovery.build_evidence(observations)

        observations = self.observations()
        observations["promotion"]["runtime_credential_cannot_promote"] = False
        with self.assertRaisesRegex(
            recovery.DisasterRecoveryProofError, "promotion_invalid"
        ):
            recovery.build_evidence(observations)

    def test_evidence_write_is_exclusive_and_owner_only(self) -> None:
        evidence = recovery.build_evidence(self.observations())
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "evidence" / "summary.json"
            recovery.write_exclusive(output, evidence)

            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            recovery.validate_evidence(json.loads(output.read_text(encoding="utf-8")))
            with self.assertRaises(FileExistsError):
                recovery.write_exclusive(output, evidence)

    def test_evidence_validation_rejects_unexpected_fields_and_sensitive_content(self) -> None:
        evidence = recovery.build_evidence(self.observations())
        evidence["unexpected"] = True
        with self.assertRaisesRegex(
            recovery.DisasterRecoveryProofError, "evidence_fields_invalid"
        ):
            recovery.validate_evidence(evidence)

        sensitive = recovery.build_evidence(self.observations())
        sensitive["versions"]["docker_engine"] = "postgresql://secret@example/db"
        with self.assertRaisesRegex(
            recovery.DisasterRecoveryProofError, "evidence_contains_forbidden_content"
        ):
            recovery.validate_evidence(sensitive)

    def test_mutating_derived_rpo_is_rejected(self) -> None:
        evidence = deepcopy(recovery.build_evidence(self.observations()))
        evidence["state_coverage"][0]["gap_seconds"] = 4.0
        with self.assertRaisesRegex(
            recovery.DisasterRecoveryProofError,
            "rpo_runtime_configuration_generations_invalid",
        ):
            recovery.validate_evidence(evidence)

    def test_evidence_generation_cannot_predate_rehearsal_completion(self) -> None:
        evidence = recovery.build_evidence(self.observations())
        evidence["generated_at"] = "2026-08-26T12:00:00+00:00"
        with self.assertRaisesRegex(
            recovery.DisasterRecoveryProofError,
            "generated_at_precedes_rehearsal_completion",
        ):
            recovery.validate_evidence(evidence)

    def test_repository_fixtures_pin_recovery_and_runtime_authority_boundaries(self) -> None:
        runner = (ROOT / "tools" / "verify_disaster_recovery_reference.sh").read_text(
            encoding="utf-8"
        )
        state_probe_source = (DR_ROOT / "state_probe.py").read_text(encoding="utf-8")
        recovered_postgres = (DR_ROOT / "recovered-postgres.yaml").read_text(
            encoding="utf-8"
        )
        recovery_kind = (DR_ROOT / "kind-recovery.yaml.tmpl").read_text(
            encoding="utf-8"
        )
        source_backup = (DR_ROOT / "source-backup.yaml").read_text(encoding="utf-8")
        config_path = DR_ROOT / "hormuz.json"
        config_document = json.loads(config_path.read_text(encoding="utf-8"))
        gateway_config = GatewayConfig.load(
            config_path,
            environ={
                "HORMUZ_TOKEN": "disaster-recovery-alice-token",
                "HORMUZ_BOB_TOKEN": "disaster-recovery-bob-token",
                "HORMUZ_INGRESS_CREDENTIAL": "disaster-recovery-ingress-credential",
            },
        )
        helm_values = (DR_ROOT / "helm-values.yaml").read_text(encoding="utf-8")
        state_pod = (DR_ROOT / "state-pod.yaml").read_text(encoding="utf-8")
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "I_UNDERSTAND_THIS_IS_A_DISPOSABLE_DISASTER_RECOVERY_REFERENCE_PROOF",
            runner,
        )
        for image in (recovery.HORMUZ_IMAGE, recovery.POSTGRES_IMAGE, recovery.OPENBAO_IMAGE):
            self.assertIn(image, runner)
        backup_contract = runner + source_backup
        for operation in ("pg_receivewal", "pg_basebackup", "pg_verifybackup"):
            self.assertIn(operation, backup_contract)
        self.assertIn("'{.status.currentPrimary}'", runner)
        self.assertNotIn("port-forward", runner)
        self.assertIn('containerPath: /hormuz-dr-artifacts', runner)
        self.assertIn('"        readOnly: false\\n"', runner)
        self.assertIn("source-backup.yaml", runner)
        self.assertIn("pod/hormuz-dr-wal-receiver", runner)
        self.assertIn("wait_for_source_backup_receiver", runner)
        self.assertIn("--all-containers --prefix=true", runner)
        self.assertIn("hostssl replication postgres", runner)
        self.assertIn('ip_network("10.244.0.0/16")', runner)
        self.assertIn(recovery.POSTGRES_IMAGE, source_backup)
        self.assertIn("path: /hormuz-dr-artifacts", source_backup)
        self.assertIn("suspend: true", source_backup)
        self.assertEqual(source_backup.count("name: PGSSLMODE"), 2)
        self.assertEqual(source_backup.count("value: require"), 2)
        self.assertEqual(source_backup.count("runAsUser: 26"), 2)
        self.assertEqual(source_backup.count("runAsGroup: 102"), 2)
        self.assertEqual(source_backup.count("fsGroup: 102"), 2)
        self.assertNotIn("runAsGroup: 26", source_backup)
        self.assertNotIn("fsGroup: 26", source_backup)
        self.assertIn("chgrp 102 /recovery", source_backup)
        self.assertIn("chmod 0710 /recovery", source_backup)
        self.assertIn("install -d -m 0700 -o 26 -g 102", source_backup)
        self.assertIn("runAsUser: 26", recovered_postgres)
        self.assertIn("runAsGroup: 102", recovered_postgres)
        self.assertIn("fsGroup: 102", recovered_postgres)
        self.assertNotIn("runAsGroup: 26", recovered_postgres)
        self.assertNotIn("fsGroup: 26", recovered_postgres)
        self.assertNotIn("26:26", runner + source_backup + recovered_postgres)
        self.assertIn("chown -R 26:102 /negative/data", runner)
        self.assertEqual(gateway_config.usage_storage.backend, "postgresql")
        self.assertEqual(gateway_config.policy_control.mode, "postgresql")
        self.assertEqual(gateway_config.custody_control.mode, "postgresql")
        self.assertEqual(
            config_document["upstreams"]["openai"]["base_url"],
            "http://fake-provider.hormuz-dependencies.svc.cluster.local:8090",
        )
        self.assertEqual(
            config_document["key_custody"]["endpoint_url"],
            "https://openbao.hormuz-dependencies.svc.cluster.local:8200",
        )
        self.assertIn('--patch \'{"spec":{"suspend":false}}\'', runner)
        self.assertIn("readOnly: true", recovery_kind)
        self.assertIn("pg_create_restore_point('hormuz_dr_partial')", runner)
        self.assertIn("pg_create_restore_point('hormuz_dr_final')", runner)
        self.assertLess(
            runner.index('kind delete cluster --name "${SOURCE_CLUSTER}"'),
            runner.index('kind create cluster --name "${RECOVERY_CLUSTER}"'),
        )
        successful_admission = (
            'python3 "${ROOT}/tools/verify_disaster_recovery_reference.py" admit \\\n'
            '  --input "${ARTIFACT_ROOT}/admission-input.json"'
        )
        self.assertLess(
            runner.index(successful_admission),
            runner.index('helm upgrade --install hormuz "${CHART_ROOT}"'),
        )
        self.assertLess(
            runner.index('negative_provider_after="$(provider_request_count)"'),
            runner.index('helm upgrade --install hormuz "${CHART_ROOT}"'),
        )
        for failure_path in recovery.FAILURE_PATHS:
            self.assertIn(f'"{failure_path}"', runner)
        self.assertIn('[[ "${provider_after_first_request}" == "1" ]]', runner)
        self.assertIn('"automatic_provider_replays": 0', runner)
        self.assertIn('install -m 0600 "${DR_ROOT}/hormuz.json"', runner)
        self.assertIn(
            'install_state_probe "${RECOVERY_INPUTS}/config/hormuz.json"',
            runner,
        )
        state_probe_secret = runner[
            runner.index(
                "create_immutable_secret hormuz-system hormuz-disaster-recovery-state"
            ) : runner.index(
                'kubectl --namespace hormuz-system apply --filename "${DR_ROOT}/state-pod.yaml"'
            )
        ]
        self.assertIn("ingress-credential", state_probe_secret)
        self.assertIn("HORMUZ_INGRESS_CREDENTIAL", state_pod)
        self.assertIn("key: ingress-credential", state_pod)
        self.assertNotIn('X-Vault-Token: ${', runner)
        self.assertNotIn('--env "HORMUZ_POSTGRES_DSN=', runner)
        self.assertIn('--header "@${SECRET_ROOT}/openbao-runtime-header"', runner)
        self.assertIn('--env-file "${env_file}"', runner)

        runtime_secret = runner[
            runner.index(
                "create_immutable_secret hormuz-system hormuz-recovery-runtime-v1"
            ) : runner.index(
                "create_immutable_configmap hormuz-ingress hormuz-disaster-recovery-probe"
            )
        ]
        for forbidden in (
            "postgres-migration-dsn",
            "postgres-policy-control-dsn",
            "postgres-custody-control-dsn",
            "postgres-custody-executor-dsn",
            "openbao-runtime-token",
        ):
            self.assertNotIn(forbidden, runtime_secret)
            self.assertNotIn(forbidden, helm_values)
        self.assertIn("postgres-runtime-dsn", runtime_secret)
        self.assertIn("HORMUZ_POSTGRES_DSN", helm_values)

        tree = ast.parse(state_probe_source)
        class_tables_node = next(
            node
            for node in tree.body
            if isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "CLASS_TABLES"
        )
        class_tables = ast.literal_eval(class_tables_node.value)
        self.assertEqual(tuple(class_tables), recovery._DATABASE_STATE_CLASSES)
        timestamp_columns_node = next(
            node
            for node in tree.body
            if isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "COMMIT_TIMESTAMP_COLUMNS"
        )
        timestamp_columns = ast.literal_eval(timestamp_columns_node.value)
        all_tables = {
            table
            for tables in class_tables.values()
            for _, table in tables
        }
        self.assertEqual(set(timestamp_columns), all_tables)
        forbidden_time_semantics = {
            "expires_at",
            "lease_expires_at",
            "retain_until",
            "source_retain_until",
        }
        self.assertFalse(
            forbidden_time_semantics.intersection(
                column
                for columns in timestamp_columns.values()
                for column in columns
            )
        )

        self.assertEqual(recovered_postgres.count(recovery.POSTGRES_IMAGE), 2)
        self.assertIn("pg_verifybackup /recovery/base", recovered_postgres)
        self.assertIn("recovery_target_name = 'hormuz_dr_final'", recovered_postgres)
        self.assertIn("readOnly: true", recovered_postgres)
        self.assertIn("readOnly: true", recovery_kind)
        chart = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "deploy" / "helm" / "hormuz" / "templates").glob(
                "*.yaml"
            )
        )
        self.assertNotIn("postgresql.cnpg.io", chart)
        self.assertNotIn("kind: Cluster\n", chart)
        self.assertIn("name: Disaster recovery reference", workflow)
        self.assertIn("HORMUZ_DISASTER_RECOVERY_PROOF_ACK", workflow)
        source_manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
        self.assertIn("include tools/verify_disaster_recovery_reference.py", source_manifest)
        self.assertIn("include tools/verify_disaster_recovery_reference.sh", source_manifest)


if __name__ == "__main__":
    unittest.main()
