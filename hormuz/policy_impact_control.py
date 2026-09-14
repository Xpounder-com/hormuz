"""Session-authorized output-cap proposals using the existing policy controller."""
from __future__ import annotations

from dataclasses import asdict
import hmac
import json
import secrets

from .policy_control import PolicyControlService
from .policy_document import PolicyDocument
from .policy_impact import ImpactStore, PREVIEW_TTL, RETENTION, compare, iso, utcnow, routing_fingerprint
from .policy_repository import PolicyControlError


def candidate_document(baseline: PolicyDocument, *, config, team_id: str, model_alias: str, cap: int) -> PolicyDocument:
    if type(cap) is not int or not 1 <= cap <= 1_000_000:
        raise PolicyControlError("impact_invalid_request")
    value = baseline.to_mapping()
    value["schema_version"] = 2
    limits = value["policies"].setdefault("team_model_output_limits", {})
    limits.setdefault(team_id, {})[model_alias] = cap
    return PolicyDocument.from_mapping(value, config=config)


class PolicyImpactControl:
    def __init__(self, *, config, sessions, controller: PolicyControlService, store: ImpactStore):
        self.config, self.sessions, self.controller, self.store = config, sessions, controller, store

    def baseline(self, credential: str):
        principal, caller = self.sessions.policy_identity(credential)
        record, generation = self.controller.browser_baseline(caller)
        return principal, caller, record.document, generation

    def scopes(self, credential: str) -> dict:
        principal, _, baseline, generation = self.baseline(credential)
        teams = self.sessions.directory.list_records("teams", organization_id=principal.organization_id, limit=100)
        return {"schema_id": "hormuz.policy-impact-scopes", "schema_version": 1,
                "organization_id": principal.organization_id, "policy_version": baseline.version_id,
                "generation": generation, "teams": teams["items"], "teams_next_cursor": teams["next_cursor"],
                "models": [{"alias": alias, "protocol": route.protocol} for alias, route in sorted(self.config.model_routes.items())][:128]}

    def _require_scope(self, principal, team_id: str, model_alias: str) -> None:
        from .onboarding import _identifier
        _identifier(team_id)
        if model_alias not in self.config.model_routes:
            raise PolicyControlError("impact_scope_unavailable")
        with self.sessions.store._connection() as db:
            if db.execute("SELECT id FROM onboarding_teams WHERE organization_id=? AND id=?", (principal.organization_id, team_id)).fetchone() is None:
                raise PolicyControlError("impact_scope_unavailable")

    def preview(self, credential: str, *, team_id: str, model_alias: str, cap: int) -> dict:
        principal, _, baseline, generation = self.baseline(credential)
        self._require_scope(principal, team_id, model_alias)
        policy = baseline.organization_policy.overlaid(baseline.team_policies.get(team_id))
        if policy.allowed_models is not None and model_alias not in policy.allowed_models:
            raise PolicyControlError("impact_scope_unavailable")
        limits = [n for n in (policy.max_output_tokens, baseline.team_model_output_limits.get(team_id, {}).get(model_alias)) if n is not None]
        previous_limit = min(limits) if limits else None
        if type(cap) is not int or not 1 <= cap <= 1_000_000 or previous_limit is not None and cap >= previous_limit:
            raise PolicyControlError("impact_limit_not_lower")
        candidate = candidate_document(baseline, config=self.config, team_id=team_id, model_alias=model_alias, cap=cap)
        observations = self.store.observations(organization_id=principal.organization_id, team_id=team_id,
                                             model_alias=model_alias, policy_version=baseline.version_id)
        observations = tuple(o for o in observations if o.routing_fingerprint == routing_fingerprint(self.config))
        comparison = compare(observations, cap)
        value = {"schema_id": "hormuz.policy-impact-preview", "schema_version": 1,
                 "preview_id": "pip_" + secrets.token_urlsafe(24), "organization_id": principal.organization_id,
                 "team_id": team_id, "model_alias": model_alias, "current_limit": previous_limit,
                 "proposed_limit": cap, "baseline_version": baseline.version_id, "baseline_generation": generation,
                 "candidate_version": candidate.version_id, "routing_fingerprint": routing_fingerprint(self.config), "created_at": iso(utcnow()), "expires_at": iso(utcnow() + PREVIEW_TTL),
                 "comparison": comparison, "observed_since": min((o.started_at for o in observations), default=None),
                 "can_apply": bool(observations)}
        self.store.save_preview(preview_id=value["preview_id"], organization_id=principal.organization_id,
                                membership_id=principal.membership_id, expires_at=iso(utcnow() + RETENTION),
                                value={"preview": value, "signature": self._signature(value, principal.membership_id)})
        return value

    def _signature(self, value, membership_id):
        material = json.dumps([membership_id, value], sort_keys=True, separators=(",", ":")).encode()
        return self.sessions.store._digest("policy-impact-preview", material.decode()).hex()

    def _reviewed(self, principal, preview_id, *, allow_expired=False):
        if not isinstance(preview_id, str) or len(preview_id) > 64:
            raise PolicyControlError("impact_invalid_request")
        stored = self.store.preview(preview_id=preview_id, organization_id=principal.organization_id, membership_id=principal.membership_id)
        value, signature = stored.get("preview"), stored.get("signature")
        if not isinstance(value, dict) or not isinstance(signature, str) or not hmac.compare_digest(signature, self._signature(value, principal.membership_id)):
            raise PolicyControlError("impact_preview_expired")
        if not allow_expired and (value["expires_at"] <= iso(utcnow()) or value["routing_fingerprint"] != routing_fingerprint(self.config)):
            raise PolicyControlError("impact_preview_expired")
        self._require_scope(principal, value["team_id"], value["model_alias"])
        return value

    def review(self, credential: str, preview_id: str) -> dict:
        principal, _, baseline, generation = self.baseline(credential)
        preview = self._reviewed(principal, preview_id)
        if baseline.version_id != preview["baseline_version"] or generation != preview["baseline_generation"]:
            raise PolicyControlError("policy_active_version_mismatch")
        return preview

    def apply(self, credential: str, *, preview_id: str, acknowledged: bool) -> dict:
        if acknowledged is not True:
            raise PolicyControlError("impact_acknowledgement_required")
        principal, caller, baseline, generation = self.baseline(credential)
        preview = self._reviewed(principal, preview_id)
        if not preview["can_apply"]:
            raise PolicyControlError("impact_no_observations")
        # A lost response may be retried only at the exact immediately resulting
        # activation. Any intervening activation, including ABA, invalidates it.
        if baseline.version_id == preview["candidate_version"] and generation == preview["baseline_generation"] + 1:
            return self.results(credential, preview_id)
        if baseline.version_id != preview["baseline_version"] or generation != preview["baseline_generation"]:
            raise PolicyControlError("policy_active_version_mismatch")
        candidate = candidate_document(baseline, config=self.config, team_id=preview["team_id"], model_alias=preview["model_alias"], cap=preview["proposed_limit"])
        if candidate.version_id != preview["candidate_version"]:
            raise PolicyControlError("policy_active_version_mismatch")
        self.controller.browser_apply(caller, candidate, baseline_version=baseline.version_id, generation=generation)
        return self.results(credential, preview_id)

    def rollback(self, credential: str, *, preview_id: str, acknowledged: bool) -> dict:
        if acknowledged is not True:
            raise PolicyControlError("impact_acknowledgement_required")
        principal, caller, baseline, generation = self.baseline(credential)
        preview = self._reviewed(principal, preview_id, allow_expired=True)
        if baseline.version_id == preview["baseline_version"] and generation == preview["baseline_generation"] + 2:
            return self.results(credential, preview_id)
        if baseline.version_id != preview["candidate_version"] or generation != preview["baseline_generation"] + 1:
            raise PolicyControlError("policy_active_version_mismatch")
        self.controller.browser_rollback(caller, target_version=preview["baseline_version"], active_version=baseline.version_id, generation=generation)
        return self.results(credential, preview_id)

    def activity(self, credential: str) -> tuple[object, tuple[dict, ...]]:
        principal, caller, _, _ = self.baseline(credential)
        history = self.controller.browser_history(caller)
        proposals = []
        for preview_id in self.store.recent_preview_ids(organization_id=principal.organization_id, membership_id=principal.membership_id):
            try:
                proposals.append(self._reviewed(principal, preview_id, allow_expired=True))
            except PolicyControlError:
                continue
        return history, tuple(proposals)

    def results(self, credential: str, preview_id: str) -> dict:
        principal, _, baseline, generation = self.baseline(credential)
        preview = self._reviewed(principal, preview_id, allow_expired=True)
        # Results report this exact candidate, never all subsequent traffic.
        observations = self.store.observations(organization_id=principal.organization_id, team_id=preview["team_id"],
                                             model_alias=preview["model_alias"], policy_version=preview["candidate_version"])
        observations = tuple(o for o in observations if o.started_at >= preview["created_at"] and o.routing_fingerprint == preview["routing_fingerprint"])
        return {"schema_id": "hormuz.policy-impact-results", "schema_version": 1,
                "preview": preview, "active_version": baseline.version_id, "active_generation": generation,
                "candidate_active": baseline.version_id == preview["candidate_version"],
                "rollback_available": baseline.version_id == preview["candidate_version"] and generation == preview["baseline_generation"] + 1,
                "captured_requests": len(observations), "known_errors": sum(o.status in {"failed", "rate_limited"} for o in observations),
                "unknown_outcomes": sum(o.status == "pending" for o in observations),
                "estimated_cost_microusd": sum(o.cost_microusd for o in observations if o.cost_microusd is not None),
                "cost_known_requests": sum(o.cost_microusd is not None for o in observations),
                "receipts": [asdict(o) for o in observations[:20]], "coverage": "bounded_captured_sample_only",
                "cost_basis": "configured_rate_card_estimate", "savings": None, "quality_effect": None}
