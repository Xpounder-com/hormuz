from __future__ import annotations

import unittest

from hormuz.config import Identity, ModelRoute, PolicyValidationContext
from hormuz.policy_document import PolicyDocumentError
from hormuz.policy_templates import (
    PolicyTemplateError,
    create_policy_document,
    policy_templates,
)


def _identity(actor_id: str, organization_id: str, *clients: str) -> Identity:
    return Identity(
        token_env=f"TOKEN_{actor_id.upper()}",
        token="",
        actor_id=actor_id,
        actor_name=f"Actor {actor_id}",
        team_id=f"team-{actor_id}",
        team_name=f"Team {actor_id}",
        organization_id=organization_id,
        allowed_clients=clients,
    )


def _context(*, multiple_organizations: bool = False) -> PolicyValidationContext:
    identities = {
        "alice": _identity("alice", "acme", "codex"),
        "bob": _identity("bob", "acme", "claude-code", "codex"),
    }
    organizations = ("acme",)
    if multiple_organizations:
        identities["carol"] = _identity("carol", "other", "claude-code")
        organizations = ("acme", "other")
    routes = {
        "gpt-company": ModelRoute(
            alias="gpt-company",
            protocol="openai",
            upstream_model="gpt-upstream",
        ),
        "claude-company": ModelRoute(
            alias="claude-company",
            protocol="anthropic",
            upstream_model="claude-upstream",
        ),
    }
    return PolicyValidationContext(
        organization_ids=organizations,
        identities_by_actor=identities,
        model_routes=routes,
    )


class PolicyTemplateTests(unittest.TestCase):
    def test_catalog_names_and_descriptions_are_stable(self) -> None:
        templates = policy_templates()

        self.assertEqual([template.name for template in templates], ["standard", "strict", "lockdown"])
        self.assertTrue(all(template.description for template in templates))

    def test_standard_uses_only_configured_clients_models_and_safe_egress(self) -> None:
        document = create_policy_document(template_name="standard", context=_context())

        self.assertEqual(document.organization_id, "acme")
        self.assertEqual(document.organization_policy.allowed_clients, ("claude-code", "codex"))
        self.assertEqual(document.organization_policy.allowed_models, ("claude-company", "gpt-company"))
        self.assertEqual(document.organization_policy.max_output_tokens, 16_000)
        self.assertIsNone(document.organization_policy.fallback_model)
        self.assertIsNone(document.organization_policy.fallback_models)
        self.assertIsNone(document.organization_policy.monthly_budget_usd)
        self.assertIsNone(document.organization_policy.per_actor_monthly_budget_usd)
        self.assertFalse(document.openai_egress.allow_response_storage)
        self.assertFalse(document.openai_egress.allow_background)
        self.assertEqual(document.secret_mode, "redact")
        self.assertEqual(document.team_policies, {})
        self.assertEqual(document.actor_policies, {})

    def test_strict_applies_optional_budget_overrides(self) -> None:
        document = create_policy_document(
            template_name="strict",
            context=_context(),
            monthly_budget_usd=250.0,
            per_actor_monthly_budget_usd=25.0,
        )

        self.assertEqual(document.organization_policy.max_output_tokens, 4_000)
        self.assertEqual(document.organization_policy.monthly_budget_usd, 250.0)
        self.assertEqual(document.organization_policy.per_actor_monthly_budget_usd, 25.0)
        self.assertEqual(document.secret_mode, "deny")

    def test_lockdown_is_deny_all_without_invented_scopes_or_routes(self) -> None:
        document = create_policy_document(template_name="lockdown", context=_context())

        self.assertEqual(document.organization_policy.allowed_clients, ())
        self.assertEqual(document.organization_policy.allowed_models, ())
        self.assertIsNone(document.organization_policy.fallback_model)
        self.assertIsNone(document.organization_policy.fallback_models)
        self.assertEqual(document.team_policies, {})
        self.assertEqual(document.actor_policies, {})
        self.assertEqual(document.secret_mode, "deny")

    def test_multitenant_context_requires_an_explicit_configured_organization(self) -> None:
        context = _context(multiple_organizations=True)

        with self.assertRaisesRegex(PolicyTemplateError, "multiple organizations") as missing:
            create_policy_document(template_name="standard", context=context)
        self.assertEqual(missing.exception.code, "policy_template_organization_required")

        selected = create_policy_document(
            template_name="standard",
            context=context,
            organization_id="other",
        )
        self.assertEqual(selected.organization_id, "other")
        self.assertEqual(selected.organization_policy.allowed_clients, ("claude-code",))

        with self.assertRaisesRegex(PolicyTemplateError, "not configured") as unknown:
            create_policy_document(
                template_name="standard",
                context=context,
                organization_id="missing",
            )
        self.assertEqual(unknown.exception.code, "policy_template_organization_unknown")

    def test_unknown_template_and_invalid_budget_fail_with_stable_errors(self) -> None:
        with self.assertRaises(PolicyTemplateError) as unknown:
            create_policy_document(template_name="custom", context=_context())
        self.assertEqual(unknown.exception.code, "policy_template_unknown")

        with self.assertRaisesRegex(PolicyDocumentError, "non-negative number"):
            create_policy_document(
                template_name="standard",
                context=_context(),
                monthly_budget_usd=float("nan"),
            )


if __name__ == "__main__":
    unittest.main()
