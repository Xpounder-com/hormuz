"""Credential-free built-in policy templates.

Templates are intentionally small domain constructors. They project only
configured organization IDs, identity client allowlists, and model aliases
into a strict policy document; they never resolve credentials or invent
tenant scopes, provider routes, or fallback behavior.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Policy, PolicyValidationContext
from .policy_document import OpenAIEgressPolicy, PolicyDocument


@dataclass(frozen=True)
class PolicyTemplate:
    name: str
    description: str
    max_output_tokens: int
    secret_mode: str
    deny_all: bool = False


class PolicyTemplateError(ValueError):
    """Stable, content-safe failure for template selection or construction."""

    def __init__(self, code: str, reason: str, *, hint: str | None = None) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason
        self.hint = hint


_POLICY_TEMPLATES = (
    PolicyTemplate(
        name="standard",
        description=(
            "Balanced daily-use policy using configured clients and models, "
            "secret redaction, and a 16,000-token output cap."
        ),
        max_output_tokens=16_000,
        secret_mode="redact",
    ),
    PolicyTemplate(
        name="strict",
        description=(
            "Conservative policy using configured clients and models, "
            "secret denial, and a 4,000-token output cap."
        ),
        max_output_tokens=4_000,
        secret_mode="deny",
    ),
    PolicyTemplate(
        name="lockdown",
        description=(
            "Emergency deny-all policy with empty client and model allowlists "
            "and secret denial."
        ),
        max_output_tokens=4_000,
        secret_mode="deny",
        deny_all=True,
    ),
)
_POLICY_TEMPLATES_BY_NAME = {template.name: template for template in _POLICY_TEMPLATES}


def policy_templates() -> tuple[PolicyTemplate, ...]:
    """Return the stable built-in template catalog in display order."""

    return _POLICY_TEMPLATES


def create_policy_document(
    *,
    template_name: str,
    context: PolicyValidationContext,
    organization_id: str | None = None,
    monthly_budget_usd: float | None = None,
    per_actor_monthly_budget_usd: float | None = None,
) -> PolicyDocument:
    """Construct and strictly validate one complete v1 policy document."""

    template = _POLICY_TEMPLATES_BY_NAME.get(template_name)
    if template is None:
        raise PolicyTemplateError(
            "policy_template_unknown",
            "the requested policy template is not available",
            hint="Run `hormuz policy templates` to list the built-in templates.",
        )
    selected_organization = _select_organization(context, organization_id)
    if template.deny_all:
        allowed_clients: tuple[str, ...] | None = ()
        allowed_models: tuple[str, ...] = ()
    else:
        organization_identities = tuple(
            identity
            for identity in context.identities_by_actor.values()
            if identity.organization_id == selected_organization
        )
        # An empty identity allowlist means unrestricted client access. The
        # organization-level equivalent is None: any concrete union would
        # accidentally narrow that identity, while individual non-empty
        # identity allowlists continue to enforce their own restrictions.
        allowed_clients = (
            None
            if any(not identity.allowed_clients for identity in organization_identities)
            else tuple(
                sorted(
                    {
                        client
                        for identity in organization_identities
                        for client in identity.allowed_clients
                    }
                )
            )
        )
        allowed_models = tuple(sorted(context.model_routes))

    document = PolicyDocument(
        organization_id=selected_organization,
        organization_policy=Policy(
            allowed_clients=allowed_clients,
            allowed_models=allowed_models,
            max_output_tokens=template.max_output_tokens,
            monthly_budget_usd=monthly_budget_usd,
            per_actor_monthly_budget_usd=per_actor_monthly_budget_usd,
        ),
        team_policies={},
        actor_policies={},
        openai_egress=OpenAIEgressPolicy(
            allow_response_storage=False,
            allow_background=False,
        ),
        secret_mode=template.secret_mode,
    )
    # Route the generated mapping back through the same parser and reference
    # validation used by offline validation and governed staging.
    return PolicyDocument.from_mapping(document.to_mapping(), config=context)


def _select_organization(
    context: PolicyValidationContext,
    requested_organization: str | None,
) -> str:
    if requested_organization is not None:
        if requested_organization not in context.organization_ids:
            raise PolicyTemplateError(
                "policy_template_organization_unknown",
                "the requested organization is not configured",
                hint="Use an organization ID already present in the Hormuz identity configuration.",
            )
        return requested_organization
    if len(context.organization_ids) == 1:
        return context.organization_ids[0]
    raise PolicyTemplateError(
        "policy_template_organization_required",
        "multiple organizations are configured",
        hint="Pass --organization with the tenant that should own the policy document.",
    )
