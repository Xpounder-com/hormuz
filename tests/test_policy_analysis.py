from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import unittest
from unittest import mock

from hormuz.config import GatewayConfig, Identity
from hormuz.policy_analysis import (
    PolicyAnalysisError,
    compare_policy_documents,
    evaluate_policy_scenario_suite,
    preview_policy_request,
)
from hormuz.policy_document import PolicyDocument
from hormuz.policy_scenarios import PolicyScenarioSuite
from hormuz.store import MonthlyTotals


ROOT = Path(__file__).resolve().parents[1]
POLICY_FIXTURE = ROOT / "tests" / "fixtures" / "policies" / "policy-document-v1.json"


class PolicyAnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.context = GatewayConfig.load_policy_validation_context(ROOT / "config.example.json")
        cls.config = GatewayConfig.load(
            ROOT / "config.example.json",
            environ={"HORMUZ_TOKEN": "test-identity-token"},
        )

    def _mapping(self) -> dict[str, object]:
        return json.loads(POLICY_FIXTURE.read_text(encoding="utf-8"))

    def _document(self, mapping: dict[str, object] | None = None) -> PolicyDocument:
        return PolicyDocument.from_mapping(mapping or self._mapping(), config=self.context)

    def test_comparison_ignores_map_and_allowlist_order_but_retains_digests(self) -> None:
        baseline = self._document()
        reordered = self._mapping()
        policies = reordered["policies"]
        assert isinstance(policies, dict)
        organization = policies["organization"]
        teams = policies["teams"]
        assert isinstance(organization, dict) and isinstance(teams, dict)
        organization["allowed_clients"] = list(reversed(organization["allowed_clients"]))
        organization["allowed_models"] = list(reversed(organization["allowed_models"]))
        engineering = teams["engineering"]
        assert isinstance(engineering, dict)
        engineering["allowed_models"] = list(reversed(engineering["allowed_models"]))
        candidate = self._document(reordered)

        comparison = compare_policy_documents(baseline, candidate)

        self.assertTrue(comparison.identical)
        self.assertEqual(comparison.changes, ())
        self.assertNotEqual(comparison.baseline.version_id, comparison.candidate.version_id)
        self.assertEqual(comparison.baseline.version_id, f"sha256:{comparison.baseline.content_sha256}")
        self.assertEqual(comparison.candidate.version_id, f"sha256:{comparison.candidate.content_sha256}")

    def test_comparison_reports_sorted_semantic_paths_and_change_types(self) -> None:
        baseline = self._document()
        changed = self._mapping()
        policies = changed["policies"]
        assert isinstance(policies, dict)
        organization = policies["organization"]
        teams = policies["teams"]
        actors = policies["actors"]
        assert isinstance(organization, dict) and isinstance(teams, dict) and isinstance(actors, dict)
        organization["max_output_tokens"] = 4_000
        organization.pop("monthly_budget_usd")
        actors["alice"] = {"allowed_models": []}
        teams["0team"] = {"allowed_clients": ["codex"]}
        teams["alpha"] = {"allowed_clients": ["codex"]}
        teams["platform.ai"] = {"allowed_clients": ["codex"]}
        candidate = self._document(changed)

        comparison = compare_policy_documents(baseline, candidate)
        changes = {change.path: change for change in comparison.changes}

        self.assertFalse(comparison.identical)
        self.assertEqual(list(changes), sorted(changes))
        self.assertEqual(
            changes["policies.organization.max_output_tokens"].change_type,
            "changed",
        )
        self.assertEqual(changes["policies.organization.max_output_tokens"].before, 32_000)
        self.assertEqual(changes["policies.organization.max_output_tokens"].after, 4_000)
        self.assertEqual(changes["policies.organization.monthly_budget_usd"].change_type, "removed")
        self.assertEqual(changes["policies.actors.alice.allowed_models"].change_type, "added")
        self.assertEqual(
            changes['policies.teams["platform.ai"].allowed_clients'].change_type,
            "added",
        )
        self.assertEqual(
            changes["policies.teams.alpha.allowed_clients"].change_type,
            "added",
        )
        self.assertEqual(
            changes['policies.teams["0team"].allowed_clients'].change_type,
            "added",
        )

    def test_preview_reuses_one_current_usage_snapshot_for_both_documents(self) -> None:
        baseline = self._document()
        denied_mapping = self._mapping()
        policies = denied_mapping["policies"]
        assert isinstance(policies, dict)
        actors = policies["actors"]
        assert isinstance(actors, dict)
        actors["alice"] = {"allowed_models": []}
        candidate = self._document(denied_mapping)
        usage_store = mock.Mock()
        usage_store.monthly_totals.return_value = MonthlyTotals()
        evaluated_at = datetime(2026, 8, 27, 5, 30, tzinfo=timezone.utc)

        preview = preview_policy_request(
            config=self.config,
            usage_store=usage_store,
            identity=self.config.identities_by_actor["alice"],
            baseline=baseline,
            candidate=candidate,
            client="codex",
            protocol="openai",
            requested_model="gpt-5.4-mini",
            requested_output_tokens=1_000,
            evaluated_at=evaluated_at,
        )

        self.assertTrue(preview.baseline_decision.allowed)
        self.assertFalse(preview.candidate_decision.allowed)
        self.assertEqual(preview.usage_basis, "current")
        self.assertEqual(preview.evaluated_at, evaluated_at)
        self.assertEqual(preview.usage_period.starts_at.isoformat(), "2026-08-01T00:00:00+00:00")
        self.assertEqual(preview.usage_period.ends_before.isoformat(), "2026-09-01T00:00:00+00:00")
        self.assertEqual(usage_store.monthly_totals.call_count, 3)
        self.assertEqual(
            usage_store.monthly_totals.call_args_list,
            [
                mock.call(
                    actor_id="alice",
                    team_id=None,
                    organization_id="xpounder",
                    starts_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
                    ends_before=datetime(2026, 9, 1, tzinfo=timezone.utc),
                ),
                mock.call(
                    actor_id=None,
                    team_id=None,
                    organization_id="xpounder",
                    starts_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
                    ends_before=datetime(2026, 9, 1, tzinfo=timezone.utc),
                ),
                mock.call(
                    actor_id=None,
                    team_id="engineering",
                    organization_id="xpounder",
                    starts_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
                    ends_before=datetime(2026, 9, 1, tzinfo=timezone.utc),
                ),
            ],
        )
        self.assertEqual(preview.baseline_decision.policy_version, baseline.version_id)
        self.assertEqual(preview.candidate_decision.policy_version, candidate.version_id)

    def test_preview_domain_rejects_invalid_request_dimensions_before_usage_reads(self) -> None:
        document = self._document()
        usage_store = mock.Mock()

        for field, value, code in (
            ("client", "unknown-client", "policy_preview_client_invalid"),
            ("protocol", "unknown-protocol", "policy_preview_protocol_invalid"),
            ("requested_model", "model\ninjection", "policy_preview_model_invalid"),
            ("requested_output_tokens", 0, "policy_preview_output_tokens_invalid"),
        ):
            arguments = {
                "config": self.config,
                "usage_store": usage_store,
                "identity": self.config.identities_by_actor["alice"],
                "baseline": document,
                "candidate": document,
                "client": "codex",
                "protocol": "openai",
                "requested_model": "gpt-5.4-mini",
                "requested_output_tokens": 1_000,
            }
            arguments[field] = value
            with self.subTest(field=field), self.assertRaises(PolicyAnalysisError) as raised:
                preview_policy_request(**arguments)
            self.assertEqual(raised.exception.code, code)

        usage_store.monthly_totals.assert_not_called()

    def test_scenario_evaluation_pins_one_usage_snapshot_per_actor(self) -> None:
        alice = self.config.identities_by_actor["alice"]
        bob = Identity(
            token_env="HORMUZ_BOB_TOKEN",
            token="bob-token",
            actor_id="bob",
            actor_name="Bob Example",
            team_id=alice.team_id,
            team_name=alice.team_name,
            organization_id=alice.organization_id,
            allowed_clients=("codex",),
        )
        config = replace(
            self.config,
            identities_by_token={**self.config.identities_by_token, bob.token: bob},
        )
        baseline = self._document()
        candidate_mapping = self._mapping()
        policies = candidate_mapping["policies"]
        assert isinstance(policies, dict)
        actors = policies["actors"]
        assert isinstance(actors, dict)
        actors["alice"] = {"allowed_models": []}
        candidate = self._document(candidate_mapping)
        suite = PolicyScenarioSuite.from_mapping(
            {
                "schema_id": "hormuz.policy-scenario-suite",
                "schema_version": 1,
                "organization_id": "xpounder",
                "scenarios": [
                    {
                        "id": "alice-large",
                        "actor_id": "alice",
                        "client": "codex",
                        "protocol": "openai",
                        "requested_model": "gpt-5.4-mini",
                        "requested_output_tokens": 20_000,
                    },
                    {
                        "id": "bob-default",
                        "actor_id": "bob",
                        "client": "codex",
                        "protocol": "openai",
                        "requested_model": "gpt-5.4-mini",
                        "requested_output_tokens": 1_000,
                    },
                    {
                        "id": "alice-default",
                        "actor_id": "alice",
                        "client": "codex",
                        "protocol": "openai",
                        "requested_model": "gpt-5.4-mini",
                        "requested_output_tokens": 1_000,
                    },
                ],
            }
        )
        usage_store = mock.Mock()
        usage_store.monthly_totals.return_value = MonthlyTotals()
        evaluated_at = datetime(2026, 8, 27, 5, 30, tzinfo=timezone.utc)

        with mock.patch("hormuz.policy.PolicyRuntime") as create_policy_runtime:
            evaluation = evaluate_policy_scenario_suite(
                config=config,
                usage_store=usage_store,
                suite=suite,
                baseline=baseline,
                candidate=candidate,
                evaluated_at=evaluated_at,
            )

        self.assertEqual(
            [result.scenario.scenario_id for result in evaluation.scenarios],
            ["alice-default", "alice-large", "bob-default"],
        )
        self.assertEqual(evaluation.changed_count, 2)
        self.assertEqual(evaluation.baseline_allowed_count, 3)
        self.assertEqual(evaluation.candidate_allowed_count, 1)
        self.assertFalse(evaluation.identical)
        self.assertEqual(usage_store.monthly_totals.call_count, 6)
        actor_reads = [
            call
            for call in usage_store.monthly_totals.call_args_list
            if call.kwargs["actor_id"] is not None
        ]
        self.assertEqual(
            sorted(call.kwargs["actor_id"] for call in actor_reads),
            ["alice", "bob"],
        )
        create_policy_runtime.assert_not_called()

    def test_scenario_evaluation_rejects_every_unknown_actor_before_usage_reads(self) -> None:
        document = self._document()
        suite = PolicyScenarioSuite.from_mapping(
            {
                "schema_id": "hormuz.policy-scenario-suite",
                "schema_version": 1,
                "organization_id": "xpounder",
                "scenarios": [
                    {
                        "id": "missing-actor",
                        "actor_id": "missing",
                        "client": "codex",
                        "protocol": "openai",
                        "requested_model": "gpt-5.4-mini",
                        "requested_output_tokens": 1_000,
                    }
                ],
            }
        )
        usage_store = mock.Mock()

        with self.assertRaises(PolicyAnalysisError) as raised:
            evaluate_policy_scenario_suite(
                config=self.config,
                usage_store=usage_store,
                suite=suite,
                baseline=document,
                candidate=document,
            )

        self.assertEqual(raised.exception.code, "policy_evaluation_actor_not_found")
        usage_store.monthly_totals.assert_not_called()


if __name__ == "__main__":
    unittest.main()
