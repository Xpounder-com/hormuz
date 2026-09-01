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
    "test_postgres_attribution": (
        "PostgresAttributionTests",
        {
            "test_postgres_attribution_sources_and_immutable_facts",
            "test_postgres_attribution_corrections_voids_and_idempotency",
            "test_postgres_attribution_authority_before_lookup_and_join",
            "test_postgres_attribution_scope_race",
            "test_postgres_attribution_concurrency",
            "test_postgres_attribution_atomicity_and_read_audit",
            "test_postgres_attribution_frozen_pagination",
            "test_postgres_attribution_rejection_coverage",
            "test_postgres_attribution_invalid_requests",
            "test_postgres_attribution_forced_rls_append_only_and_missing_shape",
        },
    ),
    "test_postgres_attribution_gateway": (
        "PostgresAttributionGatewayTests",
        {
            "test_postgres_native_success_bodies_models_and_header_stripping",
            "test_postgres_rejection_precedes_budget_policy_and_provider",
            "test_postgres_failed_atomic_attribution_commit_rolls_back_before_egress",
            "test_postgres_unbounded_output_never_egresses_under_active_work_budget",
            "test_postgres_failover_creates_distinct_attribution_and_work_budget_evidence",
            "test_postgres_provider_side_input_never_egresses_under_active_work_budget",
            "test_postgres_unbound_identity_cannot_bypass_an_active_work_budget",
            "test_postgres_unbound_identity_uses_database_clock",
            "test_postgres_scope_change_before_atomic_reservation_never_egresses",
            "test_postgres_unattributed_and_nonaccounted_behavior",
            "test_postgres_identity_cannot_gain_attribution_authority",
            "test_postgres_admin_http_correction_contract",
        },
    ),
    "test_postgres_attribution_transition": (
        "PostgresAttributionTransitionTests",
        {
            "test_postgres_attribution_migration_is_additive_and_idempotent",
            "test_postgres_attribution_failure_and_retry_preserve_populated_registry",
            "test_postgres_attribution_partial_state_refuses_before_repair",
            "test_postgres_attribution_old_processes_refuse_next_schema",
            "test_postgres_attribution_quiesced_registry_pair_restore_preserves_replays",
            "test_postgres_attribution_post_checkpoint_writes_require_forward_recovery",
        },
    ),
    "test_postgres_portfolio_registry": (
        "PostgresPortfolioRegistryTests",
        {
            "test_postgres_registry_lifecycle_and_hierarchy",
            "test_postgres_registry_authorization_before_access",
            "test_postgres_registry_idempotency_and_versions",
            "test_postgres_registry_frozen_pagination",
            "test_postgres_registry_authorized_bindings",
            "test_postgres_registry_concurrent_writers",
            "test_postgres_registry_atomic_failure_and_safe_audit",
            "test_postgres_registry_strict_input",
            "test_postgres_registry_forced_rls_and_append_only",
            "test_postgres_registry_pool_reuse_and_rls_shape_fail_closed",
        },
    ),
    "test_postgres_registry_transition": (
        "PostgresRegistryTransitionTests",
        {
            "test_registry_postgres_migration_is_additive_and_idempotent",
            "test_postgres_registry_failure_rolls_back_and_retry_preserves_v1_rows",
            "test_postgres_partial_upgrade_refuses_migration_and_runtime_without_changes",
            "test_released_postgres_binary_preserves_old_state_and_refuses_newer_or_partial_state",
            "test_postgres_quiesced_verified_pair_restore_keeps_unknown_holds",
            "test_postgres_candidate_writes_remain_present_for_forward_recovery",
        },
    ),
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
            "test_schema_v8_missing_custody_evidence_trigger_fails_closed",
        },
    ),
    "test_postgres_policy_control": (
        "PostgresPolicyControlTests",
        {
            "test_atomic_apply_idempotency_guard_and_generation_rollback",
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
            "test_retained_custody_evidence_exports_through_control_and_deletion_is_only_blocked",
            "test_unlinked_deletion_evidence_rolls_back_at_commit",
            "test_direct_custody_writer_cannot_use_evidence_as_a_json_side_channel",
            "test_runtime_cannot_insert_an_arbitrary_v2_audit_chain_entry",
        },
    ),
    "test_postgres_custody_executor": (
        "PostgresCustodyExecutorTests",
        {
            "test_executor_claims_exact_routine_intent_then_finalizes_once_and_exposes_metadata_status",
            "test_authority_mismatch_expiry_and_requester_revocation_fail_closed_before_runner",
            "test_ambiguous_effect_remains_pending_until_swept_unknown_without_replay",
            "test_post_effect_finalization_failure_preserves_pending_for_unknown_recovery_without_replay",
            "test_executor_role_is_tenant_isolated_and_cannot_rewrite_or_mismatch_execution_evidence",
        },
    ),
    "test_postgres_custody_lifecycle": (
        "PostgresCustodyLifecycleTests",
        {
            "test_two_person_disablement_appends_metadata_only_evidence_and_projects_atomically",
            "test_two_live_replicas_acknowledge_before_atomic_restriction_activation",
            "test_partitioned_replica_fences_locally_before_its_lease_can_be_excluded",
            "test_revoking_one_destructive_approver_blocks_the_machine_before_lifecycle_execution",
            "test_committed_disablement_denies_a_new_gateway_request_before_provider_egress",
            "test_retired_envelope_blocks_runtime_selection",
            "test_key_retirement_without_rewrap_and_restore_proof_leaves_no_terminal_event_or_projection",
            "test_key_retirement_requires_attested_rewrap_and_restore_then_write_retires_only_old_key",
            "test_recovery_resolution_appends_a_new_event_without_rewriting_unknown_attempt",
            "test_confirmed_not_applied_resolution_releases_only_an_uncommitted_prepared_barrier",
            "test_gateway_startup_requires_registered_immutable_catalog_and_rejects_rebinding",
        },
    ),
    "test_postgres_usage": (
        "PostgresUsageEvidenceTests",
        {
            "test_all_v1_operations_have_equivalent_normalized_outcomes",
            "test_read_operations_preserve_rows_and_the_existing_locking_contract",
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
            "test_normalized_cross_backend_corruption_fixtures_have_exact_error_parity",
            "test_cross_backend_recovery_epoch_has_checkpoint_parity",
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

        self.assertEqual(len(owned), 108)
        self.assertFalse((ROOT / "tests" / "test_postgres.py").exists())
        self.assertFalse(
            any(name.startswith("test_") for name in PostgresTestCase.__dict__),
            "the shared fixture must not own behavioral tests",
        )


if __name__ == "__main__":
    unittest.main()
