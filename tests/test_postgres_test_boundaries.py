from __future__ import annotations

import importlib
from pathlib import Path
import unittest

if __package__:
    from ._postgres_fixture import PostgresTestCase
else:  # Isolated wheel compatibility discovery uses the tests directory as its import root.
    from _postgres_fixture import PostgresTestCase


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_OWNERS = {
    "test_postgres_migration_rls": (
        "PostgresMigrationRLSTests",
        {
            "test_migration_is_visible_to_the_restricted_runtime_role",
            "test_policy_control_role_verifies_only_the_shared_migration_ledger",
            "test_schema_v2_upgrade_preserves_evidence_and_rejects_an_old_reader",
            "test_partial_v3_upgrade_from_schema_v2_fails_before_materializing_ledger_tables",
            "test_incomplete_schema_v2_fails_before_v3_can_advance_the_ledger",
            "test_noncontiguous_v2_migration_ledger_fails_before_v3_can_advance",
            "test_configured_router_uses_only_the_runtime_dsn",
            "test_storage_cli_uses_the_operator_dsn_only_for_migration",
            "test_runtime_role_fails_closed_without_an_organization_context",
            "test_newer_or_partial_schema_fails_closed_without_mutating_evidence",
        },
    ),
    "test_postgres_policy_control": (
        "PostgresPolicyControlTests",
        {
            "test_policy_control_bootstrap_activation_rollback_and_request_pinning",
            "test_policy_cli_uses_the_authenticated_service_boundary",
            "test_policy_bootstrap_cannot_drift_and_non_administrator_cannot_change_policy",
            "test_explicit_oidc_administrator_is_separate_from_runtime_entitlement",
            "test_policy_roles_are_separated_and_break_glass_requires_admin_loss",
        },
    ),
    "test_postgres_custody_control": (
        "PostgresCustodyControlTests",
        {
            "test_bootstrap_authority_is_separate_from_runtime_policy_and_kms_entitlement",
            "test_routine_authorization_is_content_free_and_initial_enrollment_uses_only_a_handle_digest",
            "test_destructive_authorization_requires_two_distinct_active_administrators_and_cannot_replay",
            "test_destructive_authorization_requires_every_approver_to_remain_active",
            "test_expired_or_invalid_authorization_rolls_back_without_a_second_approval",
            "test_last_administrator_protection_and_all_admin_loss_require_separate_break_glass",
            "test_tenant_isolation_and_append_only_authorization_history_are_database_enforced",
        },
    ),
    "test_postgres_usage": (
        "PostgresUsageEvidenceTests",
        {
            "test_sqlite_and_postgres_have_equivalent_normalized_outcomes",
            "test_contract_fixtures_and_historical_version_are_materialized",
            "test_malformed_evidence_fails_closed_without_content",
            "test_tenant_scope_and_budget_reservation_concurrency_match_sqlite_contract",
            "test_unknown_organization_fails_closed",
        },
    ),
    "test_postgres_audit_chain": (
        "PostgresAuditChainTests",
        {
            "test_commit_time_audit_chain_serializes_multi_instance_writes_and_is_tenant_isolated",
            "test_commit_time_audit_chain_rolls_back_and_runtime_cannot_rewrite_history",
        },
    ),
    "test_postgres_request_attempts": (
        "PostgresRequestAttemptTests",
        {"test_attempt_ledger_is_append_only_tenant_scoped_and_conservative"},
    ),
    "test_postgres_multi_instance": (
        "PostgresMultiInstanceTests",
        {
            "test_two_gateway_instances_share_atomic_organization_budget_reservations",
            "test_two_gateway_instances_converge_on_policy_activation_and_rollback",
        },
    ),
    "test_postgres_runtime_recovery": (
        "PostgresRuntimeRecoveryTests",
        {
            "test_failed_replica_fails_closed_while_sibling_and_replacement_remain_usable",
            "test_rolling_runtime_login_rotation_keeps_ready_replacement_and_tenant_isolation",
            "test_terminated_idle_backend_connection_is_replaced_before_replica_egress",
        },
    ),
    "test_postgres_pooling": (
        "PostgresPoolingTests",
        {
            "test_runtime_pool_reuses_connections_without_tenant_state_leakage",
            "test_runtime_pool_saturation_fails_closed_before_tenant_query",
            "test_runtime_pool_replaces_a_terminated_idle_connection",
        },
    ),
}


class PostgresTestBoundaryTests(unittest.TestCase):
    def test_every_live_postgres_case_has_exactly_one_behavioral_owner(self) -> None:
        owned: set[str] = set()
        module_prefix = f"{__package__}." if __package__ else ""
        for short_module_name, (class_name, expected_methods) in EXPECTED_OWNERS.items():
            module_name = f"{module_prefix}{short_module_name}"
            module = importlib.import_module(module_name)
            owner = getattr(module, class_name)
            self.assertTrue(issubclass(owner, PostgresTestCase))
            actual_methods = {
                name
                for name, value in owner.__dict__.items()
                if name.startswith("test_") and callable(value)
            }
            self.assertEqual(actual_methods, expected_methods, module_name)
            self.assertTrue(owned.isdisjoint(actual_methods), module_name)
            owned.update(actual_methods)

            suite = unittest.defaultTestLoader.loadTestsFromName(f"{module_name}.{class_name}")
            self.assertEqual(suite.countTestCases(), len(expected_methods), module_name)

        self.assertEqual(len(owned), 38)
        self.assertFalse((ROOT / "tests" / "test_postgres.py").exists())
        self.assertFalse(
            any(name.startswith("test_") for name in PostgresTestCase.__dict__),
            "the shared fixture must not own behavioral tests",
        )


if __name__ == "__main__":
    unittest.main()
