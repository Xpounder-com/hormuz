"""Strict, content-free policy documents for the governed control plane.

Policy documents deliberately contain only allowlisted routing, budget, and
supported egress controls. They do not accept prompts, responses, secret
values, arbitrary notes, group claims, or source content.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Mapping

from .config import GatewayConfig, Identity, Policy
from .contracts import POLICY_DOCUMENT_SCHEMA_ID, POLICY_DOCUMENT_SCHEMA_VERSION


_POLICY_FIELDS = frozenset(
    {
        "allowed_clients",
        "allowed_models",
        "fallback_model",
        "fallback_models",
        "max_output_tokens",
        "monthly_token_limit",
        "monthly_budget_usd",
        "per_actor_monthly_budget_usd",
    }
)
_EGRESS_FIELDS = ("openai.allow_background", "openai.allow_response_storage", "secrets.mode")


class PolicyDocumentError(ValueError):
    """A stable validation error which does not repeat submitted content."""

    code = "policy_document_invalid"


@dataclass(frozen=True)
class OpenAIEgressPolicy:
    allow_response_storage: bool
    allow_background: bool


@dataclass(frozen=True)
class PolicySnapshot:
    """One request-bound view of an immutable active policy version."""

    policy_version: str
    content_sha256: str | None
    organization_policy: Policy
    team_policy: Policy | None
    actor_policy: Policy | None
    effective_policy: Policy
    openai_egress: OpenAIEgressPolicy
    secret_mode: str


@dataclass(frozen=True)
class PolicyDocument:
    organization_id: str
    organization_policy: Policy
    team_policies: dict[str, Policy]
    actor_policies: dict[str, Policy]
    openai_egress: OpenAIEgressPolicy
    secret_mode: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], *, config: GatewayConfig) -> "PolicyDocument":
        _exact_keys(
            value,
            {"schema_id", "schema_version", "organization_id", "policies", "egress_controls"},
            "policy document",
        )
        if _string(value.get("schema_id"), "schema_id") != POLICY_DOCUMENT_SCHEMA_ID:
            raise PolicyDocumentError("policy document schema_id is unsupported")
        if _integer(value.get("schema_version"), "schema_version", minimum=1) != POLICY_DOCUMENT_SCHEMA_VERSION:
            raise PolicyDocumentError("policy document schema_version is unsupported")
        organization_id = _identifier(value.get("organization_id"), "organization_id")
        if organization_id not in config.organization_ids:
            raise PolicyDocumentError("policy document organization is not configured")

        policies = _mapping(value.get("policies"), "policies")
        _exact_keys(policies, {"organization", "teams", "actors"}, "policies")
        organization_policy = _policy(policies.get("organization"), "policies.organization")
        team_policies = _policy_map(policies.get("teams"), "policies.teams")
        actor_policies = _policy_map(policies.get("actors"), "policies.actors")

        egress_controls = _mapping(value.get("egress_controls"), "egress_controls")
        _exact_keys(egress_controls, {"openai", "secrets"}, "egress_controls")
        openai = _mapping(egress_controls.get("openai"), "egress_controls.openai")
        _exact_keys(openai, {"allow_response_storage", "allow_background"}, "egress_controls.openai")
        openai_egress = OpenAIEgressPolicy(
            allow_response_storage=_boolean(
                openai.get("allow_response_storage"), "egress_controls.openai.allow_response_storage"
            ),
            allow_background=_boolean(openai.get("allow_background"), "egress_controls.openai.allow_background"),
        )
        secrets = _mapping(egress_controls.get("secrets"), "egress_controls.secrets")
        _exact_keys(secrets, {"mode"}, "egress_controls.secrets")
        secret_mode = _string(secrets.get("mode"), "egress_controls.secrets.mode")
        if secret_mode not in {"off", "redact", "deny"}:
            raise PolicyDocumentError("egress_controls.secrets.mode is unsupported")

        document = cls(
            organization_id=organization_id,
            organization_policy=organization_policy,
            team_policies=team_policies,
            actor_policies=actor_policies,
            openai_egress=openai_egress,
            secret_mode=secret_mode,
        )
        document._validate_references(config)
        return document

    @classmethod
    def from_json_bytes(cls, value: bytes, *, config: GatewayConfig) -> "PolicyDocument":
        try:
            decoded = json.loads(value.decode("utf-8"), object_pairs_hook=_unique_json_object)
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as error:
            raise PolicyDocumentError("policy document is not valid JSON") from error
        if not isinstance(decoded, Mapping):
            raise PolicyDocumentError("policy document must be a JSON object")
        return cls.from_mapping(decoded, config=config)

    @property
    def content_sha256(self) -> str:
        return hashlib.sha256(self.canonical_json.encode("utf-8")).hexdigest()

    @property
    def version_id(self) -> str:
        return f"sha256:{self.content_sha256}"

    @property
    def canonical_json(self) -> str:
        return json.dumps(self.to_mapping(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_id": POLICY_DOCUMENT_SCHEMA_ID,
            "schema_version": POLICY_DOCUMENT_SCHEMA_VERSION,
            "organization_id": self.organization_id,
            "policies": {
                "organization": _policy_mapping(self.organization_policy),
                "teams": {
                    scope_id: _policy_mapping(policy)
                    for scope_id, policy in sorted(self.team_policies.items())
                },
                "actors": {
                    scope_id: _policy_mapping(policy)
                    for scope_id, policy in sorted(self.actor_policies.items())
                },
            },
            "egress_controls": {
                "openai": {
                    "allow_response_storage": self.openai_egress.allow_response_storage,
                    "allow_background": self.openai_egress.allow_background,
                },
                "secrets": {"mode": self.secret_mode},
            },
        }

    def redacted_change_summary(self) -> dict[str, object]:
        """Return structural metadata only; no submitted free text or values."""

        summary = {
            "summary_version": 1,
            "scopes": {
                "organization": {"fields": _policy_field_names(self.organization_policy)},
                "teams": {
                    "count": len(self.team_policies),
                    "fields": sorted({field for policy in self.team_policies.values() for field in _policy_field_names(policy)}),
                },
                "actors": {
                    "count": len(self.actor_policies),
                    "fields": sorted({field for policy in self.actor_policies.values() for field in _policy_field_names(policy)}),
                },
            },
            "egress_fields": list(_EGRESS_FIELDS),
        }
        validate_redacted_change_summary(summary)
        return summary

    def snapshot_for(self, identity: Identity) -> PolicySnapshot:
        if identity.organization_id != self.organization_id:
            raise PolicyDocumentError("policy document organization does not match authenticated identity")
        team_policy = self.team_policies.get(identity.team_id)
        actor_policy = self.actor_policies.get(identity.actor_id)
        effective = self.organization_policy.overlaid(team_policy).overlaid(actor_policy)
        return PolicySnapshot(
            policy_version=self.version_id,
            content_sha256=self.content_sha256,
            organization_policy=self.organization_policy,
            team_policy=team_policy,
            actor_policy=actor_policy,
            effective_policy=effective,
            openai_egress=self.openai_egress,
            secret_mode=self.secret_mode,
        )

    def _validate_references(self, config: GatewayConfig) -> None:
        policies = [self.organization_policy, *self.team_policies.values(), *self.actor_policies.values()]
        for policy in policies:
            for alias in policy.allowed_models or ():
                if alias not in config.model_routes:
                    raise PolicyDocumentError("policy document references an unknown model alias")
            if policy.fallback_model is not None:
                if policy.fallback_model not in config.model_routes:
                    raise PolicyDocumentError("policy document references an unknown fallback model")
            for protocol, alias in (policy.fallback_models or {}).items():
                route = config.model_routes.get(alias)
                if route is None or route.protocol != protocol:
                    raise PolicyDocumentError("policy document fallback does not match an available provider route")
        for identity in config.identities_by_actor.values():
            if identity.organization_id != self.organization_id:
                continue
            snapshot = self.snapshot_for(identity)
            bounded = any(
                policy is not None
                and (
                    policy.monthly_token_limit is not None
                    or policy.monthly_budget_usd is not None
                    or policy.per_actor_monthly_budget_usd is not None
                )
                for policy in (snapshot.organization_policy, snapshot.team_policy, snapshot.actor_policy)
            )
            if bounded and snapshot.effective_policy.max_output_tokens is None:
                raise PolicyDocumentError("a budgeted identity needs an effective max_output_tokens policy")


def local_policy_snapshot(config: GatewayConfig, identity: Identity) -> PolicySnapshot:
    """Adapt the single-process configuration policy to the shared snapshot API."""

    return PolicySnapshot(
        policy_version=config.policy_version,
        content_sha256=None,
        organization_policy=config.organization_policy,
        team_policy=config.team_policies.get(identity.team_id),
        actor_policy=config.actor_policies.get(identity.actor_id),
        effective_policy=config.resolved_policy(identity),
        openai_egress=OpenAIEgressPolicy(
            allow_response_storage=config.upstreams["openai"].allow_response_storage,
            allow_background=config.upstreams["openai"].allow_background,
        ),
        secret_mode=config.secret_controls.mode,
    )


def _policy_map(value: object, path: str) -> dict[str, Policy]:
    mapping = _mapping(value, path)
    return {
        _identifier(scope_id, f"{path} key"): _policy(raw_policy, f"{path}.{scope_id}")
        for scope_id, raw_policy in mapping.items()
    }


def _policy(value: object, path: str) -> Policy:
    item = _mapping(value, path)
    _subset_keys(item, set(_POLICY_FIELDS), path)
    return Policy(
        allowed_clients=_optional_string_list(item, "allowed_clients", path),
        allowed_models=_optional_string_list(item, "allowed_models", path),
        fallback_model=_optional_string(item, "fallback_model", path),
        fallback_models=_optional_fallback_models(item, path),
        max_output_tokens=_optional_integer(item, "max_output_tokens", path, minimum=1),
        monthly_token_limit=_optional_integer(item, "monthly_token_limit", path, minimum=1),
        monthly_budget_usd=_optional_number(item, "monthly_budget_usd", path),
        per_actor_monthly_budget_usd=_optional_number(item, "per_actor_monthly_budget_usd", path),
    )


def _policy_mapping(policy: Policy) -> dict[str, object]:
    result: dict[str, object] = {}
    if policy.allowed_clients is not None:
        result["allowed_clients"] = list(policy.allowed_clients)
    if policy.allowed_models is not None:
        result["allowed_models"] = list(policy.allowed_models)
    if policy.fallback_model is not None:
        result["fallback_model"] = policy.fallback_model
    if policy.fallback_models is not None:
        result["fallback_models"] = dict(sorted(policy.fallback_models.items()))
    if policy.max_output_tokens is not None:
        result["max_output_tokens"] = policy.max_output_tokens
    if policy.monthly_token_limit is not None:
        result["monthly_token_limit"] = policy.monthly_token_limit
    if policy.monthly_budget_usd is not None:
        result["monthly_budget_usd"] = policy.monthly_budget_usd
    if policy.per_actor_monthly_budget_usd is not None:
        result["per_actor_monthly_budget_usd"] = policy.per_actor_monthly_budget_usd
    return result


def _policy_field_names(policy: Policy) -> list[str]:
    return sorted(_policy_mapping(policy))


def validate_redacted_change_summary(value: object) -> None:
    """Validate structural metadata returned from immutable policy history.

    This is deliberately independent of submitted document values: a persisted
    summary may state policy-field names and scope counts, but cannot carry
    model aliases, budget amounts, notes, prompts, or secrets.
    """

    summary = _mapping(value, "change_summary")
    _exact_keys(summary, {"summary_version", "scopes", "egress_fields"}, "change_summary")
    if _integer(summary.get("summary_version"), "change_summary.summary_version", minimum=1) != 1:
        raise PolicyDocumentError("change_summary.summary_version is unsupported")
    egress_fields = _string_list(summary.get("egress_fields"), "change_summary.egress_fields")
    if tuple(egress_fields) != _EGRESS_FIELDS:
        raise PolicyDocumentError("change_summary.egress_fields is invalid")
    scopes = _mapping(summary.get("scopes"), "change_summary.scopes")
    _exact_keys(scopes, {"organization", "teams", "actors"}, "change_summary.scopes")
    organization = _mapping(scopes.get("organization"), "change_summary.scopes.organization")
    _exact_keys(organization, {"fields"}, "change_summary.scopes.organization")
    _policy_field_summary(organization.get("fields"), "change_summary.scopes.organization.fields")
    for scope in ("teams", "actors"):
        item = _mapping(scopes.get(scope), f"change_summary.scopes.{scope}")
        _exact_keys(item, {"count", "fields"}, f"change_summary.scopes.{scope}")
        _integer(item.get("count"), f"change_summary.scopes.{scope}.count", minimum=0)
        _policy_field_summary(item.get("fields"), f"change_summary.scopes.{scope}.fields")


def _policy_field_summary(value: object, path: str) -> list[str]:
    fields = _string_list(value, path)
    if (
        len(fields) != len(set(fields))
        or any(field not in _POLICY_FIELDS for field in fields)
        or fields != sorted(fields)
    ):
        raise PolicyDocumentError(f"{path} is invalid")
    return fields


def _string_list(value: object, path: str) -> list[str]:
    if not isinstance(value, list):
        raise PolicyDocumentError(f"{path} must be an array")
    return [_string(item, f"{path}[]") for item in value]


def _mapping(value: object, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PolicyDocumentError(f"{path} must be an object")
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], path: str) -> None:
    if set(value) != expected:
        raise PolicyDocumentError(f"{path} has unsupported or missing fields")


def _subset_keys(value: Mapping[str, Any], allowed: set[str], path: str) -> None:
    if set(value).difference(allowed):
        raise PolicyDocumentError(f"{path} has unsupported fields")


def _string(value: object, path: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\n" in value or "\r" in value:
        raise PolicyDocumentError(f"{path} must be a non-empty single-line string")
    return value


def _identifier(value: object, path: str) -> str:
    result = _string(value, path)
    if len(result) > 256:
        raise PolicyDocumentError(f"{path} is too long")
    return result


def _integer(value: object, path: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise PolicyDocumentError(f"{path} must be an integer")
    return value


def _boolean(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        raise PolicyDocumentError(f"{path} must be a boolean")
    return value


def _number(value: object, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise PolicyDocumentError(f"{path} must be a non-negative number")
    try:
        result = float(value)
    except OverflowError as error:
        raise PolicyDocumentError(f"{path} must be a non-negative number") from error
    if not math.isfinite(result):
        raise PolicyDocumentError(f"{path} must be a non-negative number")
    return result


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _optional_string(item: Mapping[str, Any], field: str, path: str) -> str | None:
    return None if field not in item else _identifier(item[field], f"{path}.{field}")


def _optional_integer(item: Mapping[str, Any], field: str, path: str, *, minimum: int) -> int | None:
    return None if field not in item else _integer(item[field], f"{path}.{field}", minimum=minimum)


def _optional_number(item: Mapping[str, Any], field: str, path: str) -> float | None:
    return None if field not in item else _number(item[field], f"{path}.{field}")


def _optional_string_list(item: Mapping[str, Any], field: str, path: str) -> tuple[str, ...] | None:
    if field not in item:
        return None
    value = item[field]
    if not isinstance(value, list):
        raise PolicyDocumentError(f"{path}.{field} must be an array")
    result = tuple(_identifier(entry, f"{path}.{field}[]") for entry in value)
    if len(result) != len(set(result)):
        raise PolicyDocumentError(f"{path}.{field} cannot contain duplicates")
    return result


def _optional_fallback_models(item: Mapping[str, Any], path: str) -> dict[str, str] | None:
    if "fallback_models" not in item:
        return None
    value = _mapping(item["fallback_models"], f"{path}.fallback_models")
    if set(value).difference({"openai", "anthropic"}):
        raise PolicyDocumentError(f"{path}.fallback_models has unsupported provider")
    result = {protocol: _identifier(alias, f"{path}.fallback_models.{protocol}") for protocol, alias in value.items()}
    return result or None
