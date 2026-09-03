"""#8 provider collection preflight contract tests."""

from __future__ import annotations

import contextlib
import copy
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest import mock

from tools import verify_finance_collection_transition_plan as verifier
from tools.verify_finance_collection_transition_plan import (
    FinanceCollectionTransitionError,
)


ROOT = Path(__file__).resolve().parents[1]


class FinanceCollectionTransitionPlanTests(unittest.TestCase):
    def plan(self):
        return json.loads((ROOT / verifier.PLAN_PATH).read_bytes())

    def contract(self):
        return json.loads((ROOT / verifier.CONTRACT_PATH).read_bytes())

    def test_checkpoint_is_preflight_only_and_reserves_next_versions(self):
        with self.assertRaisesRegex(
            FinanceCollectionTransitionError,
            "finance_collection_runtime_present_in_preflight",
        ):
            verifier.verify_finance_collection_transition_plan(ROOT)
        plan = self.plan()
        self.assertEqual(plan["feature_issue"], 8)
        self.assertEqual(plan["gate_issue"], 214)
        self.assertEqual(plan["transitions"], {"sqlite": {"from": 11, "to": 12}, "postgresql": {"from": 15, "to": 16}})
        self.assertEqual(len(plan["planned_storage"]["tables"]), 7)
        self.assertFalse(plan["collection_preflight_accepted"])
        self.assertFalse(plan["provider_collection_implemented"])

    def test_every_plan_and_contract_section_is_digest_frozen(self):
        for value, validate, code in (
            (
                self.plan(),
                verifier.validate_finance_collection_plan,
                "finance_collection_preflight_contract_changed",
            ),
            (
                self.contract(),
                verifier.validate_finance_collection_contract,
                "finance_collection_contract_changed",
            ),
        ):
            for key in value:
                with self.subTest(code=code, key=key):
                    changed = copy.deepcopy(value)
                    changed[key] = "SYNTHETIC_EXCLUDED"
                    with self.assertRaisesRegex(
                        FinanceCollectionTransitionError,
                        code,
                    ):
                        validate(changed)

    def test_validator_rejects_non_json_nonfinite_and_invalid_unicode(self):
        for validate in (
            verifier.validate_finance_collection_plan,
            verifier.validate_finance_collection_contract,
        ):
            for value in (
                object(),
                float("nan"),
                float("inf"),
                {"x": "\ud800"},
            ):
                with self.subTest(value=repr(value)), self.assertRaises(
                    FinanceCollectionTransitionError
                ) as caught:
                    validate(value)
                self.assertNotIn("SYNTHETIC_EXCLUDED", str(caught.exception))

    def test_complete_source_kit_and_absent_runtime_are_required(self):
        with mock.patch.object(verifier, "REQUIRED_FILES", ("missing-file",)):
            with self.assertRaisesRegex(
                FinanceCollectionTransitionError,
                "finance_collection_source_kit_incomplete",
            ):
                verifier.verify_finance_collection_transition_plan(ROOT)
        with mock.patch.object(
            verifier,
            "FORBIDDEN_RUNTIME_FILES",
            ("hormuz/store.py",),
        ):
            with self.assertRaisesRegex(
                FinanceCollectionTransitionError,
                "finance_collection_runtime_present_in_preflight",
            ):
                verifier.verify_finance_collection_transition_plan(ROOT)

    def test_runtime_schema_and_inventory_cannot_drift_in_preflight(self):
        with (
            mock.patch.object(
                verifier,
                "verify_finance_native_attempt_transition_plan",
                return_value={"native_request_cost_capture_implemented": True},
            ),
            mock.patch.object(verifier, "FORBIDDEN_RUNTIME_FILES", ()),
            mock.patch("hormuz._sqlite_schema.SQLITE_SCHEMA_VERSION", 12),
        ):
            with self.assertRaisesRegex(
                FinanceCollectionTransitionError,
                "finance_collection_preflight_schema_changed",
            ):
                verifier.verify_finance_collection_transition_plan(ROOT)
        for inventory in (
            (146, verifier.PREDECESSOR_RUNTIME_TREE_SHA256),
            (145, "0" * 64),
        ):
            with self.subTest(inventory=inventory), mock.patch.object(
                verifier,
                "FORBIDDEN_RUNTIME_FILES",
                (),
            ), mock.patch.object(
                verifier,
                "verify_finance_native_attempt_transition_plan",
                return_value={"native_request_cost_capture_implemented": True},
            ), mock.patch("hormuz._sqlite_schema.SQLITE_SCHEMA_VERSION", 11), mock.patch(
                "hormuz.postgres.POSTGRES_SCHEMA_VERSION", 15
            ), mock.patch.object(
                verifier,
                "_runtime_inventory",
                return_value=inventory,
            ):
                with self.assertRaisesRegex(
                    FinanceCollectionTransitionError,
                    "finance_collection_runtime_inventory_invalid",
                ):
                    verifier.verify_finance_collection_transition_plan(ROOT)

    def test_runtime_inventory_hashes_non_code_and_unknown_suffix_assets(self):
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary) / "hormuz"
            package.mkdir()
            (package / "runtime.py").write_bytes(b"SYNTHETIC_CODE\n")
            stylesheet = package / "console.css"
            stylesheet.write_bytes(b"SYNTHETIC_STYLE_A\n")
            opaque = package / "runtime.asset"
            opaque.write_bytes(b"SYNTHETIC_ASSET\n")

            count, first_digest = verifier._runtime_inventory(Path(temporary))
            self.assertEqual(count, 3)

            stylesheet.write_bytes(b"SYNTHETIC_STYLE_B\n")
            changed_count, changed_digest = verifier._runtime_inventory(
                Path(temporary)
            )
            self.assertEqual(changed_count, 3)
            self.assertNotEqual(first_digest, changed_digest)

            (package / "another.unknown").write_bytes(b"SYNTHETIC_EXTRA\n")
            extra_count, extra_digest = verifier._runtime_inventory(
                Path(temporary)
            )
            self.assertEqual(extra_count, 4)
            self.assertNotEqual(changed_digest, extra_digest)

    def test_profiles_freeze_provider_paths_units_and_private_dimensions(self):
        profiles = self.contract()["collection_profiles"]
        self.assertEqual(
            set(profiles),
            {
                "openai.organization-usage-completions.v1",
                "openai.organization-costs.v1",
                "anthropic.organization-usage-messages.v1",
                "anthropic.organization-costs.v1",
            },
        )
        self.assertEqual(
            profiles["openai.organization-costs.v1"]["path"],
            "/v1/organization/costs",
        )
        self.assertEqual(
            profiles["openai.organization-costs.v1"]["group_by"],
            ["project_id", "line_item", "api_key_id"],
        )
        self.assertIn(
            "exact_provider_native_decimal",
            profiles["openai.organization-costs.v1"]["quantity"],
        )
        self.assertEqual(
            profiles["anthropic.organization-costs.v1"]["path"],
            "/v1/organizations/cost_report",
        )
        self.assertIn(
            "user_id",
            profiles["openai.organization-usage-completions.v1"][
                "excluded_group_by"
            ],
        )
        anthropic = profiles["anthropic.organization-usage-messages.v1"]
        self.assertEqual(
            set(anthropic["excluded_group_by"]),
            {"account_id", "service_account_id", "speed"},
        )
        self.assertFalse(anthropic["beta_features_enabled"])
        self.assertIn(
            "divide_by_100",
            profiles["anthropic.organization-costs.v1"]["amount"],
        )

    def test_authority_is_bound_before_io_and_revalidated_before_publish(self):
        contract = self.contract()
        authorization = contract["authorization"]
        self.assertEqual(authorization["mutation_role"], "portfolio_admin")
        self.assertIn("commit_attempt_root_then_allow", authorization["order"])
        self.assertIn("io", authorization["order"])
        self.assertFalse(authorization["body_claims_grant_authority"])
        self.assertFalse(authorization["finance_viewer_collection_access"])
        self.assertIn(
            "credential_reference_version",
            authorization["commit_revalidation"],
        )
        binding = contract["source_binding"]
        self.assertIn("never_secret_value", binding["credential"])
        self.assertFalse(binding["network_from_unbound_or_body_supplied_account"])
        self.assertIn("cannot_publish", binding["revocation"])

    def test_collection_is_complete_bounded_and_never_replayed_automatically(self):
        contract = self.contract()
        lifecycle = contract["collection_lifecycle"]
        self.assertIn("before_external_io", lifecycle["attempt_root"])
        self.assertIn("complete_validated_page_chain", lifecycle["success"])
        self.assertIn("no_partial_observation", lifecycle["failure"])
        self.assertIn("no_automatic_network_replay", lifecycle["interruption"])
        self.assertIn("never_recontacts_provider", lifecycle["retry"])
        limits = contract["failure_and_limits"]
        self.assertEqual(limits["maximum_pages"], 32)
        self.assertEqual(limits["maximum_records"], 4096)
        self.assertEqual(limits["maximum_window_days"], 31)
        self.assertIn("repeated_cursors", limits["parser"])
        self.assertIn("never_success_or_zero", limits["coverage_on_failure"])

    def test_accounting_keeps_aggregate_invoice_and_granular_cost_distinct(self):
        contract = self.contract()
        observation = contract["observation"]
        self.assertEqual(observation["cost_basis"], "provider_reported_aggregate")
        self.assertFalse(observation["provider_final"])
        self.assertFalse(observation["invoice_final"])
        self.assertFalse(observation["request_final"])
        self.assertFalse(observation["team_or_employee_final"])
        self.assertIn("no_binary_float", observation["money"])
        self.assertIn("unknown_not_zero", observation["native_quantity"])
        self.assertIn("preserve_sign", observation["signed_amounts"])
        self.assertIn("never_zero", observation["null"])
        self.assertIn("never_dropped_or_allocated", observation["unclassified_cost"])
        self.assertIn("pin_exact_snapshots", observation["later_reconciliation"])
        numeric = contract["numeric_domains"]
        self.assertEqual(
            numeric["validation_order"],
            "reject_before_canonical_digest_idempotency_comparison_or_persistence",
        )
        self.assertEqual(numeric["source_numeric_lexeme_maximum_bytes"], 128)
        self.assertEqual(
            numeric["money"],
            {
                "type": "finite_exact_decimal",
                "minimum_exclusive": "-1000000000000000000",
                "maximum_exclusive": "1000000000000000000",
                "maximum_integer_digits": 18,
                "maximum_fractional_digits": 18,
                "maximum_significant_digits": 36,
                "normalized_nonzero_exponent_minimum": -18,
                "normalized_nonzero_exponent_maximum": 17,
                "provider_native_and_canonical_major_value_must_each_fit": True,
                "rounding": "forbidden",
            },
        )
        self.assertEqual(
            numeric["provider_quantity"],
            {
                "type": "finite_exact_decimal",
                "minimum_exclusive": "-1000000000000000000",
                "maximum_exclusive": "1000000000000000000",
                "maximum_integer_digits": 18,
                "maximum_fractional_digits": 18,
                "maximum_significant_digits": 36,
                "normalized_nonzero_exponent_minimum": -18,
                "normalized_nonzero_exponent_maximum": 17,
                "provider_native_value_must_fit": True,
                "unit_handling": "retain_allowlisted_quantity_unit_or_null_no_conversion",
                "rounding": "forbidden",
            },
        )
        self.assertEqual(
            numeric["usage_count"],
            {
                "type": "integer_not_boolean",
                "minimum": 0,
                "maximum": 9223372036854775807,
                "derived_sum_overflow": "reject",
            },
        )
        self.assertEqual(
            numeric["coverage_observation_count"],
            {
                "type": "integer_not_boolean",
                "minimum": 0,
                "maximum": 4096,
            },
        )

    def test_refreshes_select_exact_buckets_and_file_import_never_claims_live_scope(self):
        snapshot = self.contract()["snapshot"]
        self.assertTrue(snapshot["immutable"])
        self.assertTrue(snapshot["published_only_when_complete"])
        self.assertIn(
            "partial_overlap_never_supersedes",
            snapshot["whole_snapshot_supersession"],
        )
        self.assertIn("exact_provider_native_bucket_start_and_end", snapshot["overlap"])
        self.assertIn("never_sums_duplicate_intervals", snapshot["overlap"])
        self.assertIn(
            "within_one_organization_binding_id_and_version",
            snapshot["overlap"],
        )
        self.assertIn("nonidentical_bucket_intervals", snapshot["nonidentical_overlap"])
        self.assertIn("bucket_coverage", snapshot["selection"])
        self.assertIn("commit_sequence", snapshot["selection"])
        self.assertIn(
            "no_observation_coverage_suppresses",
            snapshot["empty_bucket_selection"],
        )
        self.assertIn("never_zero", snapshot["empty_bucket_selection"])
        self.assertIn("never_live_verified", snapshot["file_evidence"])
        self.assertIn(
            "including_organization_binding_id_and_version_collection_profile_and_query_window",
            snapshot["content_digest"],
        )
        self.assertIn(
            "excluding_attempt_identity_page_size_page_boundaries_cursors_and_page_chain_mechanics",
            snapshot["content_digest"],
        )
        self.assertIn(
            "including_requested_page_size_and_returned_page_boundaries_or_counts",
            snapshot["page_chain_digest"],
        )
        self.assertIn(
            "distinct_attempt_and_page_chain_provenance_with_a_distinct_page_chain_digest",
            self.contract()["idempotency"]["same_content_different_page_size"],
        )
        coverage = self.contract()["bucket_coverage"]
        self.assertEqual(
            coverage["table"],
            "portfolio_finance_snapshot_bucket_coverage",
        )
        self.assertEqual(coverage["states"], ["observed", "no_observation"])
        self.assertIn(
            "newest_complete_snapshot_from_coverage_first",
            coverage["selection_authority"],
        )
        self.assertIn("no_observation_requires_count_zero", coverage["empty"])
        self.assertFalse(coverage["numeric_zero_claim"])
        self.assertIn(
            "exact_provider_native_bucket_start_and_end",
            self.contract()["observation"]["granularity"],
        )
        idempotency = self.contract()["idempotency"]
        self.assertIn("without_new_snapshot_or_provider_io", idempotency["same_identity_same_canonical_content"])
        self.assertIn("fail_conflict", idempotency["same_identity_different_content"])
        self.assertIn("two_replica", idempotency["concurrency"])

    def test_raw_content_person_dimensions_and_cross_tenant_links_are_excluded(self):
        privacy = self.contract()["privacy"]
        self.assertTrue(
            {
                "raw_JSON",
                "raw_file_bytes",
                "HTTP_headers",
                "opaque_cursors_after_page_chain_validation",
                "credential_values",
                "authorization_headers",
                "provider_free_text",
                "free_form_errors",
                "OpenAI_user_id",
                "Anthropic_account_id",
                "Anthropic_service_account_id",
            }.issubset(set(privacy["discarded"]))
        )
        self.assertIn("prevents_cross_tenant_correlation", privacy["keyed_fingerprint"])
        self.assertFalse(privacy["employee_surveillance"])
        self.assertFalse(privacy["actor_attribution_from_provider_identifiers"])
        unsupported = self.contract()["unsupported_coverage"]
        self.assertIn("Anthropic_Priority_Tier_costs", unsupported)
        self.assertIn(
            "Anthropic_Claude_Platform_on_AWS_programmatic_reporting",
            unsupported,
        )

    def test_plan_reserves_only_additive_storage_and_no_current_surface(self):
        plan = self.plan()
        self.assertEqual(
            plan["transitions"],
            {
                "sqlite": {"from": 11, "to": 12},
                "postgresql": {"from": 15, "to": 16},
            },
        )
        self.assertEqual(
            plan["planned_storage"]["tables"],
            list(verifier.PLANNED_TABLES),
        )
        self.assertEqual(
            set(plan["planned_storage"]["altered_tables"]),
            {"gateway_audit_chain_entries"},
        )
        self.assertEqual(
            plan["planned_storage"]["audit_source_schemas"],
            self.contract()["audit_source_schemas"],
        )
        self.assertEqual(
            plan["planned_storage"]["audit_source_schemas"],
            verifier.AUDIT_SOURCE_SCHEMAS,
        )
        self.assertIn(
            "all_seven_collection_tables_reject_update_and_delete",
            plan["planned_storage"]["mutation_protection"],
        )
        self.assertIn(
            "PostgreSQL_rejects_TRUNCATE",
            plan["planned_storage"]["mutation_protection"],
        )
        self.assertIn(
            "SQLite_rejects_INSERT_OR_REPLACE",
            plan["planned_storage"]["mutation_protection"],
        )
        compatibility = plan["compatibility"]
        self.assertEqual(compatibility["new_http_routes"], [])
        self.assertEqual(compatibility["preflight_new_cli_commands"], [])
        self.assertFalse(compatibility["provider_model_request"])
        self.assertFalse(compatibility["automatic_provider_replay"])
        self.assertFalse(compatibility["role_expansion"])
        self.assertFalse(plan["implementation_decision"]["automatic_allocation"])

    def test_predecessor_fixture_and_plan_bind_same_archive(self):
        from tests._finance_collection_predecessor_fixture import (
            ARCHIVE_PREFIX,
            ARCHIVE_SHA256,
            RUNTIME_FILE_COUNT,
            RUNTIME_TREE_SHA256,
            SOURCE_COMMIT,
        )

        predecessor = self.plan()["predecessor"]
        self.assertEqual(predecessor["source_commit"], SOURCE_COMMIT)
        self.assertEqual(predecessor["archive_sha256"], ARCHIVE_SHA256)
        self.assertEqual(predecessor["archive_prefix"], ARCHIVE_PREFIX)
        self.assertEqual(predecessor["verified_runtime_file_count"], RUNTIME_FILE_COUNT)
        self.assertEqual(predecessor["runtime_tree_sha256"], RUNTIME_TREE_SHA256)

    def test_changed_missing_and_extra_installed_predecessor_files_refuse(self):
        from tests._finance_collection_predecessor_fixture import (
            verify_installed_runtime,
        )

        source = b"SYNTHETIC_NON_CODE_RUNTIME_ASSET\n"
        for mutation in ("none", "changed", "missing", "extra"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                if mutation != "missing":
                    (root / "console.css").write_bytes(
                        source if mutation != "changed" else b"SYNTHETIC_CHANGED\n"
                    )
                if mutation == "extra":
                    (root / "unexpected.runtime-asset").write_bytes(source)
                stream = io.BytesIO()
                with tarfile.open(fileobj=stream, mode="w") as archive:
                    member = tarfile.TarInfo(
                        verifier.PREDECESSOR_ARCHIVE_PREFIX
                        + "hormuz/console.css"
                    )
                    member.size = len(source)
                    archive.addfile(member, io.BytesIO(source))
                stream.seek(0)
                with tarfile.open(fileobj=stream, mode="r:") as archive:
                    with mock.patch(
                        "tests._finance_collection_predecessor_fixture.RUNTIME_FILE_COUNT",
                        1,
                    ):
                        if mutation == "none":
                            self.assertEqual(
                                verify_installed_runtime(archive, root),
                                1,
                            )
                        else:
                            with self.assertRaisesRegex(
                                RuntimeError,
                                "finance_collection_predecessor_runtime_mismatch",
                            ):
                                verify_installed_runtime(archive, root)

    def test_cli_rejects_missing_wrong_or_oversized_predecessor_archive(self):
        for payload in (
            None,
            b"SYNTHETIC_EXCLUDED",
            b"0" * (verifier.MAX_ARCHIVE_BYTES + 1),
        ):
            with self.subTest(size=None if payload is None else len(payload)), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "predecessor.tar"
                if payload is not None:
                    path.write_bytes(payload)
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(
                        verifier.main(["--predecessor-archive", str(path)]),
                        1,
                    )
                self.assertEqual(
                    output.getvalue().strip(),
                    "finance_collection_predecessor_archive_invalid",
                )


if __name__ == "__main__":
    unittest.main()
