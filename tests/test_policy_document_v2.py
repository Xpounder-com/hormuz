from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from hormuz.config import GatewayConfig
from hormuz.policy import PolicyEngine
from hormuz.policy_document import PolicyDocument, PolicyDocumentError
from hormuz.policy_impact_control import candidate_document
from hormuz.store import UsageStore
from tests.test_policy_document import _document, ROOT


class PolicyDocumentV2Tests(unittest.TestCase):
    def setUp(self):
        self.config = GatewayConfig.load(ROOT / "config.example.json", environ={"HORMUZ_TOKEN": "test-identity-token"})
        self.baseline = PolicyDocument.from_mapping(_document(), config=self.config)
        self.candidate = candidate_document(self.baseline, config=self.config, team_id="engineering",
                                            model_alias="gpt-5.4", cap=4000)

    def test_v1_bytes_are_preserved_and_only_selected_binding_changes(self):
        self.assertEqual(self.baseline.to_mapping()["schema_version"], 1)
        self.assertNotIn("team_model_output_limits", self.baseline.to_mapping()["policies"])
        fixture = PolicyDocument.from_json_bytes((ROOT / "tests/fixtures/policies/policy-document-v2.json").read_bytes(), config=self.config)
        self.assertEqual(fixture.canonical_json, self.candidate.canonical_json)
        result = self.candidate.to_mapping()
        self.assertEqual(result["policies"].pop("team_model_output_limits"), {"engineering": {"gpt-5.4": 4000}})
        result["schema_version"] = 1
        self.assertEqual(result, self.baseline.to_mapping())
        reparsed = PolicyDocument.from_json_bytes(self.candidate.canonical_json.encode(), config=self.config)
        self.assertEqual(reparsed.version_id, self.candidate.version_id)

    def test_team_model_cap_and_stricter_actor_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = PolicyEngine(self.config, UsageStore(Path(directory) / "usage.sqlite3"))
            alice = self.config.identities_by_actor["alice"]
            def decision(identity, alias, document=self.candidate):
                return engine.evaluate(identity=identity, client="codex", protocol="openai", requested_model=alias,
                                       requested_output_tokens=8000, snapshot=document.snapshot_for(identity))
            self.assertEqual(decision(alice, "gpt-5.4").max_output_tokens, 4000)
            self.assertEqual(decision(alice, "gpt-5.4-mini").max_output_tokens, 16000)
            self.assertEqual(decision(replace(alice, team_id="other"), "gpt-5.4").max_output_tokens, 32000)
            value = self.candidate.to_mapping()
            value["policies"]["actors"]["alice"] = {"max_output_tokens": 1000}
            strict = PolicyDocument.from_mapping(value, config=self.config)
            self.assertEqual(decision(alice, "gpt-5.4", strict).max_output_tokens, 1000)

    def test_v2_binding_validation_and_frozen_v1_closed_keys(self):
        for cap in (0, -1, True, 1.5, "4000", 1_000_001):
            value = self.candidate.to_mapping()
            value["policies"]["team_model_output_limits"]["engineering"]["gpt-5.4"] = cap
            with self.subTest(cap=cap), self.assertRaises(PolicyDocumentError):
                PolicyDocument.from_mapping(value, config=self.config)
        value = self.candidate.to_mapping()
        value["schema_version"] = 1
        with self.assertRaises(PolicyDocumentError):
            PolicyDocument.from_mapping(value, config=self.config)

    def test_v2_summary_has_only_structural_counts(self):
        from hormuz._contract_schemas.policy import _validate_redacted_change_summary
        summary = self.candidate.redacted_change_summary()
        _validate_redacted_change_summary(summary)
        self.assertEqual(summary["model_output_limits"], {"team_count": 1, "binding_count": 1})
        self.assertNotIn("4000", json.dumps(summary))
        self.assertNotIn("gpt-5.4", json.dumps(summary))

    def test_failover_preserves_both_original_and_destination_caps(self):
        routes = dict(self.config.model_routes)
        routes["gpt-5.4"] = replace(routes["gpt-5.4"], failover_alias="gpt-5.4-mini")
        config = replace(self.config, model_routes=routes)
        document = candidate_document(self.candidate, config=config, team_id="engineering", model_alias="gpt-5.4-mini", cap=2000)
        with tempfile.TemporaryDirectory() as directory:
            engine = PolicyEngine(config, UsageStore(Path(directory) / "usage.sqlite3"))
            decision = engine.evaluate(identity=config.identities_by_actor["alice"], client="codex", protocol="openai",
                requested_model="gpt-5.4", requested_output_tokens=8000, snapshot=document.snapshot_for(config.identities_by_actor["alice"]))
            self.assertEqual(decision.max_output_tokens, 4000)
            fallback = engine.operational_failover(decision)
            self.assertEqual(fallback.max_output_tokens, 2000)
            self.assertEqual(fallback.policy_version, decision.policy_version)

    def test_cli_semantic_comparison_supports_v2_without_inventing_empty_changes(self):
        from hormuz.policy_analysis import compare_policy_documents
        from hormuz.commands.policy import _policy_comparison_payload
        comparison = _policy_comparison_payload(compare_policy_documents(self.baseline, self.candidate))
        self.assertEqual(len(comparison["changes"]), 1)
        self.assertEqual(comparison["changes"][0]["after"], 4000)
        empty = self.baseline.to_mapping()
        empty["schema_version"] = 2
        empty["policies"]["team_model_output_limits"] = {}
        document = PolicyDocument.from_mapping(empty, config=self.config)
        self.assertTrue(compare_policy_documents(self.baseline, document).identical)
        self.assertNotEqual(self.baseline.version_id, document.version_id)
