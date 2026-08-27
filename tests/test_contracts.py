from __future__ import annotations

import json
import unittest
from pathlib import Path

from hormuz.contracts import (
    AUDIT_ANCHOR_SCHEMA_ID,
    AUDIT_ANCHOR_SCHEMA_VERSION,
    AUDIT_CHAIN_CHECKPOINT_SCHEMA_ID,
    AUDIT_CHAIN_CHECKPOINT_SCHEMA_VERSION,
    AUDIT_CHAIN_ENTRY_SCHEMA_ID,
    AUDIT_CHAIN_ENTRY_SCHEMA_VERSION,
    AUDIT_EVENT_SCHEMA_ID,
    AUDIT_EVENT_SCHEMA_VERSION,
    ContractValidationError,
    CUSTODY_CONTROL_EVENT_SCHEMA_ID,
    CUSTODY_CONTROL_EVENT_SCHEMA_VERSION,
    CUSTODY_CONTROL_STATUS_SCHEMA_ID,
    CUSTODY_CONTROL_STATUS_SCHEMA_VERSION,
    CUSTODY_EXECUTION_EVENT_SCHEMA_ID,
    CUSTODY_EXECUTION_EVENT_SCHEMA_VERSION,
    CUSTODY_LIFECYCLE_EVENT_SCHEMA_ID,
    CUSTODY_LIFECYCLE_EVENT_SCHEMA_VERSION,
    CUSTODY_EXECUTION_SCHEMA_ID,
    CUSTODY_EXECUTION_SCHEMA_VERSION,
    CUSTODY_DELETION_EVENT_SCHEMA_ID,
    CUSTODY_DELETION_EVENT_SCHEMA_VERSION,
    CUSTODY_ENVELOPE_ATTESTATION_SCHEMA_ID,
    CUSTODY_ENVELOPE_ATTESTATION_SCHEMA_VERSION,
    CUSTODY_EVIDENCE_EXPORT_SCHEMA_ID,
    CUSTODY_EVIDENCE_EXPORT_SCHEMA_VERSION,
    ERROR_SCHEMA_ID,
    ERROR_SCHEMA_VERSION,
    POLICY_COMPARISON_SCHEMA_ID,
    POLICY_COMPARISON_SCHEMA_VERSION,
    POLICY_EVALUATION_SCHEMA_ID,
    POLICY_EVALUATION_SCHEMA_VERSION,
    POLICY_HISTORY_SCHEMA_ID,
    POLICY_HISTORY_SCHEMA_VERSION,
    POLICY_PREVIEW_SCHEMA_ID,
    POLICY_PREVIEW_SCHEMA_VERSION,
    POLICY_SCENARIO_SUITE_SCHEMA_ID,
    POLICY_SCENARIO_SUITE_SCHEMA_VERSION,
    READINESS_SCHEMA_ID,
    READINESS_SCHEMA_VERSION,
    REQUEST_ATTEMPT_EVENT_SCHEMA_ID,
    REQUEST_ATTEMPT_EVENT_SCHEMA_VERSION,
    REQUEST_ATTEMPT_SCHEMA_ID,
    REQUEST_ATTEMPT_SCHEMA_VERSION,
    contract_envelope,
    contract_manifest,
    relay_contract_header,
    validate_audit_event,
    validate_contract,
    validate_contract_manifest,
    validate_custody_control_event,
    validate_custody_execution_attempt,
    validate_custody_execution_event,
    validate_custody_deletion_event,
    validate_custody_envelope_attestation,
    validate_custody_evidence_export,
    validate_custody_lifecycle_event,
    validate_policy_control_event,
    validate_request_attempt,
    validate_request_attempt_event,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "contracts"


class PolicyEvidenceContractTests(unittest.TestCase):
    def test_valid_compatibility_fixtures_cover_every_current_json_surface(self) -> None:
        fixtures = json.loads((FIXTURES / "valid-v1.json").read_text(encoding="utf-8"))

        for name in (
            "health",
            "readiness_ready",
            "readiness_not_ready",
            "identity",
            "usage_summary",
            "error",
            "error_v2",
            "error_v3",
            "policy_decision",
            "policy_control_status",
            "policy_comparison",
            "policy_preview",
            "policy_scenario_suite",
            "policy_evaluation",
            "policy_history",
            "custody_control_status",
            "custody_control_status_v2",
            "custody_control_status_v3",
            "usage_report",
            "audit_anchor_v1",
            "audit_chain_entry_v1",
            "audit_chain_checkpoint_v1",
        ):
            validate_contract(fixtures[name])
        for name in ("audit_usage_v1", "audit_security_v1", "audit_usage_v2", "audit_security_v2"):
            validate_audit_event(fixtures[name])
        validate_policy_control_event(fixtures["policy_control_event"])
        validate_custody_control_event(fixtures["custody_control_event"])
        validate_custody_execution_attempt(fixtures["custody_execution_attempt_v1"])
        validate_custody_execution_event(fixtures["custody_execution_event_pending_v1"])
        validate_custody_execution_event(fixtures["custody_execution_event_unknown_v1"])
        validate_custody_lifecycle_event(fixtures["custody_lifecycle_event_v1"])
        validate_request_attempt(fixtures["request_attempt_v1"])
        validate_request_attempt_event(fixtures["request_attempt_pending_v1"])
        validate_request_attempt_event(fixtures["request_attempt_unknown_v1"])
        self.assertEqual(fixtures["relay_contract_header"], relay_contract_header())

    def test_invalid_compatibility_fixtures_fail_closed(self) -> None:
        fixtures = json.loads((FIXTURES / "invalid-v1.json").read_text(encoding="utf-8"))
        valid = json.loads((FIXTURES / "valid-v1.json").read_text(encoding="utf-8"))

        with self.assertRaises(ContractValidationError):
            validate_contract(fixtures["policy_decision_unknown_field"])
        with self.assertRaises(ContractValidationError):
            validate_contract(fixtures["error_v2_unknown_code"])
        with self.assertRaises(ContractValidationError):
            validate_contract(fixtures["readiness_reason_mismatch"])
        with self.assertRaises(ContractValidationError):
            validate_audit_event(fixtures["audit_usage_unknown_field"])
        invalid_event = json.loads(json.dumps(valid["policy_control_event"]))
        invalid_event["change_summary"]["scopes"]["organization"]["fields"] = ["do-not-store-content"]
        with self.assertRaises(ContractValidationError):
            validate_policy_control_event(invalid_event)
        invalid_history = json.loads(json.dumps(valid["policy_history"]))
        invalid_history["events"][0]["plaintext"] = "must-never-appear"
        with self.assertRaises(ContractValidationError):
            validate_contract(invalid_history)
        invalid_history_generation = json.loads(json.dumps(valid["policy_history"]))
        invalid_history_generation["events"][0]["generation"] = None
        with self.assertRaises(ContractValidationError):
            validate_contract(invalid_history_generation)
        invalid_comparison = json.loads(json.dumps(valid["policy_comparison"]))
        invalid_comparison["changes"][0]["path"] = "submitted_prompt"
        with self.assertRaises(ContractValidationError):
            validate_contract(invalid_comparison)
        invalid_comparison_order = json.loads(json.dumps(valid["policy_comparison"]))
        invalid_comparison_order["changes"].append(
            {
                "path": "egress_controls.secrets.mode",
                "change_type": "changed",
                "before": "redact",
                "after": "deny",
            }
        )
        with self.assertRaises(ContractValidationError):
            validate_contract(invalid_comparison_order)
        invalid_comparison_number = json.loads(json.dumps(valid["policy_comparison"]))
        invalid_comparison_number["changes"][0]["before"] = 10**400
        with self.assertRaises(ContractValidationError):
            validate_contract(invalid_comparison_number)
        invalid_preview = json.loads(json.dumps(valid["policy_preview"]))
        invalid_preview["usage_basis"] = "saved"
        with self.assertRaises(ContractValidationError):
            validate_contract(invalid_preview)
        invalid_preview_period = json.loads(json.dumps(valid["policy_preview"]))
        invalid_preview_period["usage_period"]["starts_at"] = "2026-07-01T00:00:00+00:00"
        with self.assertRaises(ContractValidationError):
            validate_contract(invalid_preview_period)
        invalid_preview_timezone = json.loads(json.dumps(valid["policy_preview"]))
        invalid_preview_timezone["evaluated_at"] = "2026-08-27T06:30:00+01:00"
        with self.assertRaises(ContractValidationError):
            validate_contract(invalid_preview_timezone)
        invalid_preview_version = json.loads(json.dumps(valid["policy_preview"]))
        invalid_preview_version["candidate"]["decision"]["policy_version"] = invalid_preview_version["baseline"]["version_id"]
        with self.assertRaises(ContractValidationError):
            validate_contract(invalid_preview_version)
        invalid_suite = json.loads(json.dumps(valid["policy_scenario_suite"]))
        invalid_suite["scenarios"].append(json.loads(json.dumps(invalid_suite["scenarios"][0])))
        with self.assertRaises(ContractValidationError):
            validate_contract(invalid_suite)
        invalid_evaluation = json.loads(json.dumps(valid["policy_evaluation"]))
        invalid_evaluation["scenarios"][0]["changed"] = False
        with self.assertRaises(ContractValidationError):
            validate_contract(invalid_evaluation)
        invalid_evaluation_summary = json.loads(json.dumps(valid["policy_evaluation"]))
        invalid_evaluation_summary["summary"]["changed_count"] = 0
        with self.assertRaises(ContractValidationError):
            validate_contract(invalid_evaluation_summary)
        invalid_evaluation_suite = json.loads(json.dumps(valid["policy_evaluation"]))
        invalid_evaluation_suite["suite"]["suite_id"] = "sha256:" + "f" * 64
        invalid_evaluation_suite["suite"]["content_sha256"] = "f" * 64
        with self.assertRaises(ContractValidationError):
            validate_contract(invalid_evaluation_suite)
        invalid_custody_event = json.loads(json.dumps(valid["custody_control_event"]))
        invalid_custody_event["plaintext"] = "must-never-appear"
        with self.assertRaises(ContractValidationError):
            validate_custody_control_event(invalid_custody_event)
        invalid_execution = json.loads(json.dumps(valid["custody_execution_attempt_v1"]))
        invalid_execution["plaintext"] = "must-never-appear"
        with self.assertRaises(ContractValidationError):
            validate_custody_execution_attempt(invalid_execution)
        invalid_execution_status = json.loads(json.dumps(valid["custody_control_status_v2"]))
        invalid_execution_status["execution_attempts"][0]["target"] = "/private/must-not-persist"
        with self.assertRaises(ContractValidationError):
            validate_contract(invalid_execution_status)
        invalid_execution_event = json.loads(json.dumps(valid["custody_execution_event_unknown_v1"]))
        invalid_execution_event["reason_code"] = "unreviewed_reason"
        with self.assertRaises(ContractValidationError):
            validate_custody_execution_event(invalid_execution_event)
        invalid_lifecycle_event = json.loads(json.dumps(valid["custody_lifecycle_event_v1"]))
        invalid_lifecycle_event["binding"] = {"path": "/private/must-not-persist"}
        with self.assertRaises(ContractValidationError):
            validate_custody_lifecycle_event(invalid_lifecycle_event)
        invalid_anchor = json.loads(json.dumps(valid["audit_anchor_v1"]))
        invalid_anchor["unexpected"] = "field"
        with self.assertRaises(ContractValidationError):
            validate_contract(invalid_anchor)
        invalid_checkpoint = json.loads(json.dumps(valid["audit_chain_checkpoint_v1"]))
        invalid_checkpoint["unexpected"] = "field"
        with self.assertRaises(ContractValidationError):
            validate_contract(invalid_checkpoint)
        legacy_storage_error = {**valid["error_v2"], "schema_version": 1}
        with self.assertRaises(ContractValidationError):
            validate_contract(legacy_storage_error)

    def test_v2_custody_contract_fixtures_are_strict_and_legacy_entries_remain_accepted(self) -> None:
        legacy = json.loads((FIXTURES / "valid-v1.json").read_text(encoding="utf-8"))
        fixtures = json.loads((FIXTURES / "custody-v2.json").read_text(encoding="utf-8"))

        validate_contract(legacy["audit_chain_entry_v1"])
        validate_custody_control_event(fixtures["custody_control_event"])
        validate_contract(fixtures["audit_chain_entry_v2"])
        validate_custody_envelope_attestation(fixtures["custody_envelope_attestation_v1"])
        validate_custody_deletion_event(fixtures["custody_deletion_event_v1"])
        validate_contract(fixtures["custody_evidence_export_v1"])
        validate_custody_evidence_export(fixtures["custody_evidence_export_v1"])

        unsupported_entry = json.loads(json.dumps(fixtures["audit_chain_entry_v2"]))
        unsupported_entry["schema_version"] = 3
        with self.assertRaises(ContractValidationError):
            validate_contract(unsupported_entry)
        arbitrary_source = json.loads(json.dumps(fixtures["audit_chain_entry_v2"]))
        arbitrary_source["source_schema_id"] = "example.unreviewed-record"
        with self.assertRaises(ContractValidationError):
            validate_contract(arbitrary_source)
        leaked_export = json.loads(json.dumps(fixtures["custody_evidence_export_v1"]))
        leaked_export["records"][0]["entry"]["event"]["plaintext"] = "must-never-appear"
        with self.assertRaises(ContractValidationError):
            validate_custody_evidence_export(leaked_export)

    def test_manifest_enumerates_current_contract_versions(self) -> None:
        manifest = contract_manifest()
        schemas = {
            (item["schema_id"], item["schema_version"])
            for item in manifest["schemas"]
        }
        self.assertIn((AUDIT_EVENT_SCHEMA_ID, AUDIT_EVENT_SCHEMA_VERSION), schemas)
        self.assertIn((AUDIT_ANCHOR_SCHEMA_ID, AUDIT_ANCHOR_SCHEMA_VERSION), schemas)
        self.assertIn((AUDIT_CHAIN_ENTRY_SCHEMA_ID, AUDIT_CHAIN_ENTRY_SCHEMA_VERSION), schemas)
        self.assertIn((AUDIT_CHAIN_CHECKPOINT_SCHEMA_ID, AUDIT_CHAIN_CHECKPOINT_SCHEMA_VERSION), schemas)
        self.assertIn((ERROR_SCHEMA_ID, ERROR_SCHEMA_VERSION), schemas)
        self.assertIn((READINESS_SCHEMA_ID, READINESS_SCHEMA_VERSION), schemas)
        self.assertIn(("hormuz.policy-decision", 1), schemas)
        self.assertIn(("hormuz.policy-control-status", 1), schemas)
        self.assertIn((POLICY_COMPARISON_SCHEMA_ID, POLICY_COMPARISON_SCHEMA_VERSION), schemas)
        self.assertIn((POLICY_PREVIEW_SCHEMA_ID, POLICY_PREVIEW_SCHEMA_VERSION), schemas)
        self.assertIn((POLICY_SCENARIO_SUITE_SCHEMA_ID, POLICY_SCENARIO_SUITE_SCHEMA_VERSION), schemas)
        self.assertIn((POLICY_EVALUATION_SCHEMA_ID, POLICY_EVALUATION_SCHEMA_VERSION), schemas)
        self.assertIn((POLICY_HISTORY_SCHEMA_ID, POLICY_HISTORY_SCHEMA_VERSION), schemas)
        self.assertIn(("hormuz.policy-document", 1), schemas)
        self.assertIn(("hormuz.policy-control-event", 1), schemas)
        self.assertIn((CUSTODY_CONTROL_STATUS_SCHEMA_ID, CUSTODY_CONTROL_STATUS_SCHEMA_VERSION), schemas)
        self.assertIn((CUSTODY_CONTROL_EVENT_SCHEMA_ID, CUSTODY_CONTROL_EVENT_SCHEMA_VERSION), schemas)
        self.assertIn((CUSTODY_EXECUTION_SCHEMA_ID, CUSTODY_EXECUTION_SCHEMA_VERSION), schemas)
        self.assertIn((CUSTODY_EXECUTION_EVENT_SCHEMA_ID, CUSTODY_EXECUTION_EVENT_SCHEMA_VERSION), schemas)
        self.assertIn((CUSTODY_LIFECYCLE_EVENT_SCHEMA_ID, CUSTODY_LIFECYCLE_EVENT_SCHEMA_VERSION), schemas)
        self.assertIn((CUSTODY_ENVELOPE_ATTESTATION_SCHEMA_ID, CUSTODY_ENVELOPE_ATTESTATION_SCHEMA_VERSION), schemas)
        self.assertIn((CUSTODY_DELETION_EVENT_SCHEMA_ID, CUSTODY_DELETION_EVENT_SCHEMA_VERSION), schemas)
        self.assertIn((CUSTODY_EVIDENCE_EXPORT_SCHEMA_ID, CUSTODY_EVIDENCE_EXPORT_SCHEMA_VERSION), schemas)
        self.assertIn((REQUEST_ATTEMPT_SCHEMA_ID, REQUEST_ATTEMPT_SCHEMA_VERSION), schemas)
        self.assertIn((REQUEST_ATTEMPT_EVENT_SCHEMA_ID, REQUEST_ATTEMPT_EVENT_SCHEMA_VERSION), schemas)
        self.assertEqual(manifest["schema_id"], "hormuz.policy-evidence-manifest")
        self.assertEqual(manifest["schema_version"], 1)
        validate_contract_manifest(manifest)
        json.dumps(manifest, sort_keys=True)

    def test_manifest_rejects_an_undeclared_field(self) -> None:
        manifest = contract_manifest()
        manifest["undeclared"] = True
        with self.assertRaises(ContractValidationError):
            validate_contract_manifest(manifest)

    def test_contract_envelope_rejects_unknown_schema(self) -> None:
        with self.assertRaises(ContractValidationError):
            contract_envelope("hormuz.unknown", {})

    def test_request_attempt_evidence_contract_is_strict_and_content_free(self) -> None:
        attempt = {
            "evidence_schema_id": REQUEST_ATTEMPT_SCHEMA_ID,
            "evidence_schema_version": REQUEST_ATTEMPT_SCHEMA_VERSION,
            "attempt_id": "attempt-123",
            "created_at": "2026-08-22T00:00:00+00:00",
            "organization_id": "xpounder",
            "actor_id": "alice",
            "actor_name": "Alice Example",
            "team_id": "engineering",
            "team_name": "Engineering",
            "identity_type": "human",
            "authentication_source": "oidc:https://identity.example",
            "client": "codex",
            "protocol": "openai",
            "requested_model": "engineering-fast",
            "resolved_alias": "engineering-fast",
            "upstream_model": "gpt-provider-fast",
            "policy_version": "policy-v1",
            "policy_action": "allowed",
            "redaction_count": 1,
            "redaction_rules": ["openai_api_key"],
            "reserved_tokens": 100,
            "reserved_cost_microusd": 1234,
        }
        pending = {
            "event_schema_id": REQUEST_ATTEMPT_EVENT_SCHEMA_ID,
            "event_schema_version": REQUEST_ATTEMPT_EVENT_SCHEMA_VERSION,
            "id": "attempt-event-1",
            "attempt_id": "attempt-123",
            "organization_id": "xpounder",
            "occurred_at": "2026-08-22T00:00:00+00:00",
            "sequence": 1,
            "state": "pending",
            "reason_code": None,
            "usage_event_id": None,
        }
        terminal = {
            **pending,
            "id": "attempt-event-2",
            "sequence": 2,
            "state": "succeeded",
            "usage_event_id": "usage-event-1",
        }
        unknown = {
            **pending,
            "id": "attempt-event-3",
            "sequence": 2,
            "state": "outcome_unknown",
            "reason_code": "provider_transport_ambiguous",
        }
        validate_request_attempt(attempt)
        validate_request_attempt_event(pending)
        validate_request_attempt_event(terminal)
        validate_request_attempt_event(unknown)

        malformed = {**attempt, "prompt": "must-not-enter-durable-evidence"}
        with self.assertRaises(ContractValidationError):
            validate_request_attempt(malformed)
        malformed_unknown = {**unknown, "reason_code": None}
        with self.assertRaises(ContractValidationError):
            validate_request_attempt_event(malformed_unknown)
        for malformed_transition in ({**terminal, "sequence": 1}, {**unknown, "sequence": 1}):
            with self.assertRaises(ContractValidationError):
                validate_request_attempt_event(malformed_transition)


if __name__ == "__main__":
    unittest.main()
