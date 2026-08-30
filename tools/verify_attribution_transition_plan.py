#!/usr/bin/env python3
"""Validate the #216 implementation plan, never final-candidate acceptance."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

try:
    from tools.verify_registry_transition_plan import (
        RegistryTransitionError, verify_registry_transition_plan, verify_released_baseline,
    )
except ModuleNotFoundError:
    from verify_registry_transition_plan import (  # type: ignore[no-redef]
        RegistryTransitionError, verify_registry_transition_plan, verify_released_baseline,
    )


ROOT = Path(__file__).resolve().parents[1]
# A fixed allowlist, not a caller-supplied declaration of success.
EXPECTED = json.loads(r'''{
  "schema_id": "hormuz.attribution-transition-plan",
  "schema_version": 1,
  "stage": "pre_implementation",
  "target_release": "1.1.0",
  "feature_issue": 216,
  "gate_issue": 214,
  "attribution_implemented": false,
  "final_candidate_accepted": false,
  "registry_source_commit": "b8cec8faba8d8e48d515dfcc3ec8eeaa78fc7926",
  "transitions": {
    "sqlite": {
      "from": 5,
      "to": 6
    },
    "postgresql": {
      "from": 9,
      "to": 10
    }
  },
  "compatibility": {
    "existing_v1_behavior": "unchanged_when_not_opted_in",
    "v1_evidence": "immutable_no_rewrite_or_backfill",
    "registry_history": "preserve_all_existing_tables_and_result_references",
    "source_precedence": [
      "authenticated_optional_request_metadata",
      "server_side_default_binding",
      "unattributed"
    ],
    "active_primary_use_cases_per_attempt": 1,
    "authority": "operator_bound_identity_client_and_exact_use_case_version_in_authenticated_tenant",
    "authorization_time": "before_budget_reservation_and_provider_egress",
    "actual_model_source": "immutable_v1_attempt_event_usage_link_never_requested_alias",
    "transaction_boundary": "no_cross_repository_atomicity_claim_no_egress_until_attribution_commits",
    "uncertain_reservations": "retain_until_explicit_reconciliation",
    "provider_replay": "never_automatic",
    "privileged_read_audit": "commit_before_delivery"
  },
  "admission_headers": {
    "request_name": "X-Hormuz-Work-Scope",
    "request_grammar": "v1;work_scope_id=<opaque_id>;version=<canonical_positive_integer>",
    "maximum_request_bytes": 192,
    "response_name": "X-Hormuz-Work-Scope-Result",
    "response_grammar": "v1;status=<fixed_status>;reason=<fixed_reason>",
    "response_statuses": [
      "attributed",
      "unattributed",
      "ambiguous",
      "rejected",
      "unavailable"
    ],
    "response_reasons": [
      "bound",
      "missing_evidence",
      "ambiguous",
      "invalid_reference",
      "stale_version",
      "unsupported",
      "unauthorized_scope",
      "dependency_unavailable"
    ],
    "duplicate_headers": "reject_before_reservation_and_egress",
    "provider_forwarding": "never",
    "provider_body": "preserve_native_shape",
    "emitted_only": "opted_in_attribution_admission",
    "reflected_input": "never"
  },
  "admission_coverage": {
    "enabled_by": "explicit_operator_configuration_or_explicit_request_header",
    "defaults": "operator_authorized_identity_client_use_case_binding",
    "missing": "unattributed_unless_explicit_active_attribution_policy_requires_a_scope",
    "invalid": "fixed_class_rejection_receipt_without_inventing_a_request_attempt",
    "admitted_unattributed": "explicit_nonprimary_event_with_unattributed_or_ambiguous_confidence",
    "rejected_admissions": "visible_separately_from_eligible_governed_attempt_denominator",
    "unsupported_or_missing_actual_model": "visible_never_substitute_alias_or_drop",
    "failed_storage": "no_provider_egress_preserve_unproven_holds"
  },
  "rollback": {
    "migration_execution": "serialized_operator_with_all_writers_and_pools_stopped",
    "old_binary_on_new_schema": "storage_schema_newer_than_binary",
    "partial_schema": "storage_schema_partial_upgrade",
    "in_place_downgrade": false,
    "before_writes": "restore_verified_application_database_pair_in_fresh_destination",
    "after_writes": "retain_candidate_and_recover_forward",
    "unknown_write_count": "retain_candidate_and_recover_forward",
    "retain_candidate_snapshot": true
  },
  "attribution_routes": [
    "GET /v1/admin/portfolio/attributions",
    "POST /v1/admin/portfolio/attributions"
  ],
  "required_preflight_cases": [
    "missing_next_migration_is_red",
    "probe_failure_rollback_and_retry",
    "populated_registry_and_v1_state_preserved",
    "partial_state_fails_closed",
    "old_current_and_released_binaries_refuse_next_schema",
    "quiesced_registry_pair_restore",
    "post_checkpoint_writes_require_forward_recovery",
    "legacy_and_approved_wire_digests_unchanged"
  ],
  "required_implementation_cases": [
    "real_additive_migrations_replace_test_probes",
    "explicit_default_unattributed_precedence",
    "duplicate_invalid_foreign_stale_unsupported_admission",
    "authorized_scope_before_budget_and_egress",
    "two_provider_native_bodies_and_header_stripping",
    "immutable_attempt_snapshot_and_actual_model_join",
    "one_primary_cas_idempotency_and_concurrency",
    "safe_privileged_read_audit_and_frozen_pagination",
    "rls_and_metadata_content_exclusion",
    "storage_outage_no_egress_or_automatic_replay",
    "actual_populated_forward_restore_and_old_pair_limits"
  ]
}''')

IMPLEMENTED = {
    **EXPECTED, "schema_version": 2, "stage": "feature_implementation", "attribution_implemented": True,
    "preflight_main_commit": "3fd46a4979fb3ff7fa798cc2d87be179e433f129",
    "registry_archive_sha256": "f8cb9c0493aa54e04e4706eddd111a90b54f2c70bf9f0e6af38911ba1d03995c",
    "registry_archive_kind": "deterministic_git_snapshot_not_published_release",
    "registry_archive_prefix": "hormuz-registry-baseline/",
    "registry_archive_tar_umask": "0000",
}


class AttributionTransitionError(ValueError):
    """Safe, fixed preflight diagnostics."""


def validate_attribution_transition_plan(value: object) -> None:
    try:
        actual = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
        baseline = IMPLEMENTED if isinstance(value, dict) and value.get("schema_version") == 2 else EXPECTED
        expected = json.dumps(baseline, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError, RecursionError):
        raise AttributionTransitionError("attribution_plan_invalid") from None
    if actual != expected:
        raise AttributionTransitionError("attribution_preflight_contract_changed")


def verify_attribution_transition_plan(root: Path = ROOT) -> dict[str, object]:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise AttributionTransitionError("attribution_plan_duplicate_member")
            result[key] = value
        return result

    try:
        with (root / "docs/attribution-transition-plan-v2.json").open("rb") as source:
            payload = source.read(32769)
        if len(payload) > 32768:
            raise AttributionTransitionError("attribution_plan_too_large")
        value = json.loads(payload, object_pairs_hook=unique)
        validate_attribution_transition_plan(value)
        if value["schema_version"] != 2:
            raise AttributionTransitionError("attribution_implementation_plan_required")
        registry = verify_registry_transition_plan(root)
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
        raise AttributionTransitionError("attribution_plan_unreadable") from None
    except RegistryTransitionError:
        raise AttributionTransitionError("attribution_baseline_contract_invalid") from None
    return {
        "schema_id": EXPECTED["schema_id"], "schema_version": 2,
        "status": "attribution_implementation_plan_verified", "target_release": "1.1.0",
        "feature_issue": 216, "attribution_route_count": 2,
        "registry_source_commit": EXPECTED["registry_source_commit"],
        "baseline_archive_sha256": registry["baseline_archive_sha256"],
        "registry_archive_sha256": IMPLEMENTED["registry_archive_sha256"],
        "attribution_implemented": True, "final_candidate_accepted": False,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-archive", type=Path)
    parser.add_argument("--baseline-manifest", type=Path)
    parser.add_argument("--registry-archive", type=Path)
    args = parser.parse_args(argv)
    try:
        if (args.baseline_archive is None) != (args.baseline_manifest is None):
            raise AttributionTransitionError("attribution_baseline_pair_required")
        result = verify_attribution_transition_plan()
        if args.baseline_archive is not None:
            verify_released_baseline(args.baseline_archive, args.baseline_manifest)
        if args.registry_archive is not None:
            try:
                if (args.registry_archive.stat().st_size > 32 * 1024 * 1024 or
                        hashlib.sha256(args.registry_archive.read_bytes()).hexdigest() != IMPLEMENTED["registry_archive_sha256"]):
                    raise AttributionTransitionError("attribution_registry_archive_invalid")
            except OSError:
                raise AttributionTransitionError("attribution_registry_archive_invalid") from None
        result["released_baseline_archive_verified"] = args.baseline_archive is not None
        result["registry_baseline_archive_verified"] = args.registry_archive is not None
        print(json.dumps(result, sort_keys=True))
        return 0
    except (AttributionTransitionError, RegistryTransitionError) as error:
        print(str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
