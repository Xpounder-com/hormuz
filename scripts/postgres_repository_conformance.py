#!/usr/bin/env python3
"""Cross-check Hormuz's supported local and PostgreSQL repository contracts.

This module is called by the disposable PostgreSQL foundation integration
runner.  It does not start a database or write checked-in evidence itself: the
caller supplies a real runtime DSN and owns the evidence envelope.

The comparison is deliberately semantic.  Opaque record IDs, session
credentials, and write timestamps are implementation details; tenant scope,
authorization outcomes, normalized metadata, and observable accounting state
are the public repository contract.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
from typing import Any, Callable

from hormuz.billing import ProviderCostItem, ProviderCostReport
from hormuz.config import (
    Identity,
    ResolvedSCIMGroupAuthorization,
    SCIMGroupAuthorizationError,
)
from hormuz.directory import (
    HORMUZ_GROUP_EXTENSION,
    HORMUZ_USER_EXTENSION,
    SCIM_GROUP_SCHEMA,
    SCIM_USER_SCHEMA,
    SQLiteDirectoryStore,
)
from hormuz.postgres_directory import PostgresDirectoryStore
from hormuz.postgres_security_store import PostgresSecurityStore
from hormuz.postgres_session_store import PostgresSessionStore
from hormuz.postgres_usage_store import PostgresUsageStore
from hormuz.session_store import SQLiteSessionStore
from hormuz.store import (
    ReservationDenied,
    ReservationScope,
    UsageStore,
)


PRIMARY_ORGANIZATION = "tenant-a"
SECONDARY_ORGANIZATION = "tenant-b"
CONFORMANCE_ISSUER = "https://repository-conformance.identity.invalid"
CONFORMANCE_HUMAN_SUBJECT = "repository-conformance-human"
CONFORMANCE_WORKLOAD_SUBJECT = "repository-conformance-workload"
CONFORMANCE_ACTOR = "repository-conformance-human"
CONFORMANCE_TEAM = "repository-conformance-team"


class RepositoryConformanceError(RuntimeError):
    """Stable failure emitted when two supported adapters diverge."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class _ConformancePolicyResolver:
    """A small policy-owned resolver for adapter-level SCIM conformance.

    The group payload still carries only membership.  The resolver supplies the
    pre-approved authorization profile, which mirrors Hormuz's production
    policy ownership boundary without depending on a particular IdP.
    """

    _profile = ResolvedSCIMGroupAuthorization(
        team_id=CONFORMANCE_TEAM,
        team_name="Repository Conformance",
        policy_id="repository-conformance-standard",
        clearance="internal",
        allowed_clients=("codex",),
        capabilities=(),
    )

    def resolve_scim_group_authorization(
        self,
        organization_id: str,
        scim_group_external_ids: tuple[str, ...],
    ) -> ResolvedSCIMGroupAuthorization:
        if (
            organization_id != PRIMARY_ORGANIZATION
            or tuple(sorted(scim_group_external_ids))
            != ("repository-conformance-engineering",)
        ):
            raise SCIMGroupAuthorizationError("directory_subject_unassigned")
        return self._profile


def _identity(
    *,
    organization_id: str,
    actor_id: str,
    team_id: str = CONFORMANCE_TEAM,
    capabilities: tuple[str, ...] = (),
) -> Identity:
    return Identity(
        token_env="INTEGRATION_ONLY",
        token="integration-only-not-persisted",
        actor_id=actor_id,
        actor_name="Repository Conformance",
        team_id=team_id,
        team_name="Repository Conformance",
        organization_id=organization_id,
        clearance="internal",
        allowed_clients=("codex",),
        capabilities=capabilities,
    )


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise RepositoryConformanceError(code)


def _same(left: object, right: object, code: str) -> None:
    if left != right:
        raise RepositoryConformanceError(code)


def _expect_code(
    action: Callable[[], object], *, expected: str, code: str
) -> str:
    try:
        action()
    except Exception as error:
        observed = str(getattr(error, "code", error))
        if observed == expected or type(error).__name__ == expected:
            return expected
    raise RepositoryConformanceError(code)


def _summary_row(store: Any) -> dict[str, object]:
    rows = [
        dict(row)
        for row in store.summary_rows()
        if str(row.get("actor_id")) == CONFORMANCE_ACTOR
    ]
    _require(len(rows) == 1, "repository_contract_summary_row_missing")
    return rows[0]


def _usage_audit_projection(events: list[dict[str, object]]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for event in events:
        event_type = str(event.get("event_type"))
        if event_type == "usage" and event.get("actor_id") == CONFORMANCE_ACTOR:
            result.append(
                {
                    "event_type": event_type,
                    **{
                        key: event.get(key)
                        for key in (
                            "schema_version",
                            "organization_id",
                            "actor_id",
                            "actor_name",
                            "identity_type",
                            "team_id",
                            "team_name",
                            "client",
                            "protocol",
                            "requested_model",
                            "resolved_alias",
                            "upstream_model",
                            "actual_model",
                            "policy_action",
                            "status",
                            "input_tokens",
                            "output_tokens",
                            "cache_read_tokens",
                            "cache_write_tokens",
                            "reasoning_tokens",
                            "billable_tokens",
                            "cost_microusd",
                            "cost_basis",
                            "currency",
                            "rate_card_version",
                            "provider_usage",
                            "redaction_count",
                            "redaction_rules",
                            "governance_policy_version",
                        )
                    },
                }
            )
        elif event_type in {
            "security.admin.usage_read",
            "security.admin.audit_read",
        } and event.get("decision_actor_id") in {
            "repository-conformance-usage-admin",
            "repository-conformance-audit-admin",
        }:
            result.append(
                {
                    "event_type": event_type,
                    **{
                        key: event.get(key)
                        for key in (
                            "schema_version",
                            "organization_id",
                            "decision_actor_id",
                            "decision_actor_name",
                            "action",
                            "group_by",
                            "actor_filter_sha256",
                            "team_filter_sha256",
                            "result_count",
                        )
                    },
                }
            )
    return sorted(result, key=lambda item: str(item["event_type"]))


def _security_audit_projection(events: list[dict[str, object]]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for event in events:
        event_type = str(event.get("event_type"))
        if event_type in {"security.secret", "security.dlp"} and event.get(
            "actor_id"
        ) == CONFORMANCE_ACTOR:
            result.append(
                {
                    "event_type": event_type,
                    **{
                        key: event.get(key)
                        for key in (
                            "schema_version",
                            "organization_id",
                            "actor_id",
                            "actor_name",
                            "team_id",
                            "team_name",
                            "client",
                            "protocol",
                            "requested_model",
                            "routed_model",
                            "action",
                            "detection_count",
                            "redaction_count",
                            "rules",
                            "policy_version",
                            "findings",
                        )
                    },
                }
            )
        elif event_type == "security.dlp.approval" and event.get(
            "actor_id"
        ) == CONFORMANCE_ACTOR:
            result.append(
                {
                    "event_type": event_type,
                    **{
                        key: event.get(key)
                        for key in (
                            "schema_version",
                            "organization_id",
                            "actor_id",
                            "actor_name",
                            "team_id",
                            "team_name",
                            "decision_actor_id",
                            "decision_actor_name",
                            "client",
                            "protocol",
                            "requested_model",
                            "routed_model",
                            "actual_model",
                            "policy_version",
                            "rules",
                            "action",
                        )
                    },
                }
            )
    return sorted(
        result,
        key=lambda item: (str(item["event_type"]), str(item.get("action", ""))),
    )


def _import_projection(result: object) -> dict[str, object]:
    value = result.to_dict()  # type: ignore[union-attr]
    return {
        key: item
        for key, item in value.items()
        if key not in {"import_id", "query_start", "query_end"}
    }


def _reconciliation_projection(value: dict[str, object]) -> dict[str, object]:
    return {
        key: value[key]
        for key in (
            "schema_version",
            "organization_id",
            "provider",
            "source_sha256",
            "page_count",
            "bucket_count",
            "provider_items",
            "scoped_provider_items",
            "unscoped_provider_items",
            "negative_provider_items",
            "zero_provider_items",
            "unclassified_provider_items",
            "provider_cost_basis",
            "provider_cost_usd",
            "gateway_cost_basis",
            "gateway_estimated_cost_usd",
            "variance_usd",
            "possible_unobserved_or_adjusted_cost_usd",
            "gateway_requests",
            "gateway_succeeded",
            "gateway_failed",
            "gateway_denied",
            "gateway_unpriced_requests",
            "active_actors",
            "active_teams",
            "legacy_unattributed_gateway_requests",
            "gateway_scope_status",
            "provider_report_completeness",
            "coverage_status",
            "provider_scope_attribution",
            "provider_source_kind",
            "provider_api_contract",
            "query_scope",
            "person_cost_basis",
            "request_final_cost_available",
            "variance_proves_gateway_bypass",
            "raw_payload_retained",
            "credential_retained",
        )
    }


def _exercise_usage_security_contract(
    *, accounting: Any, security: Any, base: datetime
) -> dict[str, object]:
    """Exercise the shared SQLite / split-PostgreSQL metadata ledger contract."""

    identity = _identity(
        organization_id=PRIMARY_ORGANIZATION,
        actor_id=CONFORMANCE_ACTOR,
    )
    month_start = base.replace(day=1, hour=0, minute=0, second=0).isoformat()
    window_end = (base + timedelta(days=1)).isoformat()
    governance_policy_version = "hpv_v1_" + "c" * 64
    accounting.record(
        identity=identity,
        client="codex",
        protocol="anthropic",
        requested_model="repository-contract-model",
        resolved_alias="repository-contract-model",
        upstream_model="repository-contract-upstream",
        actual_model="repository-contract-actual",
        policy_action="allowed",
        status="succeeded",
        input_tokens=17,
        output_tokens=9,
        cache_read_tokens=3,
        cache_write_tokens=2,
        reasoning_tokens=4,
        billable_tokens=26,
        cost_microusd=12_345,
        cost_basis="estimated",
        rate_card_version="repository-contract-v1",
        redaction_count=1,
        redaction_rules=("repository.contract.redaction",),
        gateway_latency_milliseconds=11,
        policy_latency_milliseconds=3,
        provider_latency_milliseconds=7,
        governance_policy_version=governance_policy_version,
    )

    totals = asdict(
        accounting.monthly_totals(
            organization_id=PRIMARY_ORGANIZATION,
            actor_id=CONFORMANCE_ACTOR,
        )
    )
    report_rows = accounting.report_rows(
        group_by="person",
        organization_id=PRIMARY_ORGANIZATION,
        actor_id=CONFORMANCE_ACTOR,
        include_latency=True,
    )
    _require(len(report_rows) == 1, "repository_contract_report_row_missing")
    coverage = accounting.coverage_summary(
        organization_id=PRIMARY_ORGANIZATION,
        actor_id=CONFORMANCE_ACTOR,
    )

    scopes = (
        ReservationScope(
            name="person",
            actor_id=CONFORMANCE_ACTOR,
            token_limit=100,
        ),
    )
    reservation_id = accounting.reserve_budget(
        identity=identity,
        scopes=scopes,
        reserved_tokens=50,
        reserved_cost_microusd=0,
        ttl_seconds=60,
    )
    _require(reservation_id is not None, "repository_contract_reservation_missing")
    denied = _expect_code(
        lambda: accounting.reserve_budget(
            identity=identity,
            scopes=scopes,
            reserved_tokens=100,
            reserved_cost_microusd=0,
            ttl_seconds=60,
        ),
        expected=ReservationDenied.__name__,
        code="repository_contract_reservation_denial_missing",
    )
    # ReservationDenied has intentionally descriptive text rather than a
    # machine-code constructor.  The exception type is the stable contract.
    _same(denied, ReservationDenied.__name__, "repository_contract_reservation_denial_invalid")
    active_after_reserve = accounting.active_budget_reservations(
        organization_id=PRIMARY_ORGANIZATION
    )
    accounting.refresh_budget_reservation(
        reservation_id,
        ttl_seconds=60,
        organization_id=SECONDARY_ORGANIZATION,
    )
    accounting.release_budget_reservation(
        reservation_id,
        organization_id=SECONDARY_ORGANIZATION,
    )
    cross_tenant_reservation_preserved = (
        accounting.active_budget_reservations(organization_id=PRIMARY_ORGANIZATION)
        == active_after_reserve
    )
    accounting.refresh_budget_reservation(
        reservation_id,
        ttl_seconds=60,
        organization_id=PRIMARY_ORGANIZATION,
    )
    accounting.release_budget_reservation(
        reservation_id,
        organization_id=PRIMARY_ORGANIZATION,
    )
    active_after_release = accounting.active_budget_reservations(
        organization_id=PRIMARY_ORGANIZATION
    )

    report = ProviderCostReport(
        provider="anthropic",
        source_sha256="c" * 64,
        page_count=1,
        bucket_count=1,
        report_start=base.isoformat(),
        report_end=window_end,
        items=(
            ProviderCostItem(
                bucket_start=base.isoformat(),
                bucket_end=window_end,
                amount_usd="0.5",
                currency="USD",
                provider_scope_kind="unscoped",
                provider_scope_id=None,
            ),
        ),
    )
    imported = accounting.import_provider_cost_report(
        organization_id=PRIMARY_ORGANIZATION,
        report=report,
    )
    duplicate = accounting.import_provider_cost_report(
        organization_id=PRIMARY_ORGANIZATION,
        report=report,
    )
    _require(
        imported.import_id == duplicate.import_id,
        "repository_contract_provider_import_not_idempotent",
    )
    reconciliation = accounting.reconcile_provider_costs(
        organization_id=PRIMARY_ORGANIZATION,
        provider="anthropic",
        import_id=imported.import_id,
    )

    usage_administrator = _identity(
        organization_id=PRIMARY_ORGANIZATION,
        actor_id="repository-conformance-usage-admin",
        capabilities=("usage_viewer",),
    )
    audit_administrator = _identity(
        organization_id=PRIMARY_ORGANIZATION,
        actor_id="repository-conformance-audit-admin",
        capabilities=("audit_viewer",),
    )
    accounting.record_admin_usage_read(
        administrator=usage_administrator,
        access_scope="organization",
        group_by="organization",
        actor_filter=None,
        team_filter=None,
        window_start=month_start,
        window_end=window_end,
        result_count=1,
    )
    accounting.record_admin_audit_read(
        administrator=audit_administrator,
        kind="all",
        window_start=month_start,
        window_end=window_end,
        result_count=1,
    )
    audit_denied = _expect_code(
        lambda: accounting.record_admin_audit_read(
            administrator=identity,
            kind="all",
            window_start=month_start,
            window_end=window_end,
            result_count=1,
        ),
        expected="audit_viewer_capability_required",
        code="repository_contract_audit_authorization_missing",
    )

    security.record_secret_event(
        identity=identity,
        client="codex",
        protocol="anthropic",
        requested_model="repository-contract-model",
        action="redacted",
        detection_count=2,
        rules=("repository.contract.secret",),
    )
    security.record_dlp_event(
        identity=identity,
        client="codex",
        protocol="anthropic",
        requested_model="repository-contract-model",
        routed_model="repository-contract-upstream",
        action="approval_required",
        redaction_count=0,
        policy_version="repository-contract-dlp-v1",
        findings=(
            {
                "rule_id": "repository.contract.dlp",
                "category": "conformance",
                "confidence": "high",
                "action": "require_approval",
                "count": 1,
            },
        ),
    )
    approval_arguments = {
        "identity": identity,
        "client": "codex",
        "protocol": "anthropic",
        "requested_model": "repository-contract-model",
        "routed_model": "repository-contract-upstream",
        "policy_version": "repository-contract-dlp-v1",
        "payload_fingerprint": "hdf_v1_" + "c" * 64,
        "rules": ("repository.contract.dlp",),
        "detection_count": 1,
        "ttl_seconds": 900,
    }
    pending = security.authorize_or_request_dlp_approval(
        **approval_arguments,
        now=base,
    )
    cross_tenant_approval = _expect_code(
        lambda: security.get_dlp_approval_request(
            pending.request_id,
            organization_id=SECONDARY_ORGANIZATION,
            now=base,
        ),
        expected="approval_request_not_found",
        code="repository_contract_approval_cross_tenant_visible",
    )
    approver = _identity(
        organization_id=PRIMARY_ORGANIZATION,
        actor_id="repository-conformance-approver",
        capabilities=("dlp_approver",),
    )
    approved = security.approve_dlp_approval_request(
        pending.request_id,
        approver=approver,
        ttl_seconds=900,
        now=base + timedelta(seconds=1),
    )
    consumed = security.authorize_or_request_dlp_approval(
        **approval_arguments,
        now=base + timedelta(seconds=2),
    )
    security.record_dlp_approval_model_mismatch(
        consumed.request_id,
        organization_id=PRIMARY_ORGANIZATION,
        actual_model="repository-contract-actual-mismatch",
        now=base + timedelta(seconds=3),
    )
    approval_request = security.get_dlp_approval_request(
        pending.request_id,
        organization_id=PRIMARY_ORGANIZATION,
        now=base + timedelta(seconds=3),
    )
    secret_totals = asdict(
        security.monthly_secret_totals(
            organization_id=PRIMARY_ORGANIZATION,
            actor_id=CONFORMANCE_ACTOR,
        )
    )
    approval_totals = asdict(
        security.monthly_dlp_approval_totals(
            organization_id=PRIMARY_ORGANIZATION,
            actor_id=CONFORMANCE_ACTOR,
        )
    )
    usage_events = accounting.audit_events(
        since=month_start,
        until=window_end,
        kind="all",
        organization_id=PRIMARY_ORGANIZATION,
    )
    security_events = security.audit_events(
        since=month_start,
        until=window_end,
        kind="security",
        organization_id=PRIMARY_ORGANIZATION,
    )
    foreign_usage_events = accounting.audit_events(
        since=month_start,
        until=window_end,
        kind="all",
        organization_id=SECONDARY_ORGANIZATION,
    )
    foreign_security_events = security.audit_events(
        since=month_start,
        until=window_end,
        kind="security",
        organization_id=SECONDARY_ORGANIZATION,
    )

    return {
        "monthly_totals": totals,
        "summary_row": _summary_row(accounting),
        "report_row": dict(report_rows[0]),
        "coverage": coverage,
        "reservation": {
            "active_after_reserve": active_after_reserve,
            "denial_type": denied,
            "cross_tenant_release_preserved": cross_tenant_reservation_preserved,
            "active_after_release": active_after_release,
        },
        "provider_import": {
            "first": _import_projection(imported),
            "duplicate": _import_projection(duplicate),
            "reconciliation": _reconciliation_projection(reconciliation),
        },
        "admin_audit": {
            "unauthorized_code": audit_denied,
            "events": _usage_audit_projection(usage_events),
        },
        "security": {
            "secret_totals": secret_totals,
            "approval_totals": approval_totals,
            "pending_status": pending.status,
            "approved_status": approved.status,
            "consumed_status": consumed.status,
            "approval_request": {
                "organization_id": approval_request.organization_id,
                "actor_id": approval_request.actor_id,
                "team_id": approval_request.team_id,
                "client": approval_request.client,
                "protocol": approval_request.protocol,
                "requested_model": approval_request.requested_model,
                "routed_model": approval_request.routed_model,
                "policy_version": approval_request.policy_version,
                "rules": approval_request.rules,
                "detection_count": approval_request.detection_count,
                "status": approval_request.status,
                "approved_by_actor_id": approval_request.approved_by_actor_id,
            },
            "cross_tenant_code": cross_tenant_approval,
            "events": _security_audit_projection(security_events),
        },
        "cross_tenant_audit_hidden": not _usage_audit_projection(
            foreign_usage_events
        )
        and not _security_audit_projection(foreign_security_events),
    }


def _directory_user_value() -> dict[str, object]:
    return {
        "schemas": [SCIM_USER_SCHEMA, HORMUZ_USER_EXTENSION],
        "externalId": "repository-conformance-user",
        "userName": "repository-conformance@example.invalid",
        "displayName": "Repository Conformance Human",
        "active": True,
        HORMUZ_USER_EXTENSION: {
            "issuer": CONFORMANCE_ISSUER,
            "subject": CONFORMANCE_HUMAN_SUBJECT,
        },
    }


def _directory_group_value(user_id: str, *, members: tuple[str, ...]) -> dict[str, object]:
    return {
        "schemas": [SCIM_GROUP_SCHEMA, HORMUZ_GROUP_EXTENSION],
        "externalId": "repository-conformance-engineering",
        "displayName": "Repository Conformance Engineering",
        "members": [{"value": member} for member in members],
        HORMUZ_GROUP_EXTENSION: {"active": True},
    }


def _directory_workload_value(*, display_name: str) -> dict[str, object]:
    return {
        "externalId": "repository-conformance-workload",
        "displayName": display_name,
        "identityType": "ci",
        "active": True,
        "issuer": CONFORMANCE_ISSUER,
        "subject": CONFORMANCE_WORKLOAD_SUBJECT,
        "teamId": CONFORMANCE_TEAM,
        "teamName": "Repository Conformance",
        "clearance": "internal",
        "allowedClients": ["codex"],
        "capabilities": [],
    }


def _directory_resource_projection(resource: dict[str, object]) -> dict[str, object]:
    return {
        key: resource.get(key)
        for key in (
            "externalId",
            "userName",
            "displayName",
            "identityType",
            "active",
            "issuer",
            "subject",
            "teamId",
            "teamName",
            "clearance",
            "allowedClients",
            "capabilities",
        )
    } | {
        "resource_type": resource.get("meta", {}).get("resourceType")
        if isinstance(resource.get("meta"), dict)
        else None,
        "version": resource.get("meta", {}).get("version")
        if isinstance(resource.get("meta"), dict)
        else None,
        "group_members": tuple(
            str(item.get("value"))
            for item in resource.get("members", [])
            if isinstance(item, dict)
        ),
        "groups": tuple(
            (str(item.get("value")), str(item.get("display")))
            for item in resource.get("groups", [])
            if isinstance(item, dict)
        ),
    }


def _identity_projection(identity: Identity) -> dict[str, object]:
    return {
        key: getattr(identity, key)
        for key in (
            "actor_id",
            "actor_name",
            "team_id",
            "team_name",
            "organization_id",
            "clearance",
            "allowed_clients",
            "capabilities",
            "identity_type",
            "authorization_profile_id",
        )
    }


def _prepare_directory_contract(store: Any) -> dict[str, object]:
    admin = _identity(
        organization_id=PRIMARY_ORGANIZATION,
        actor_id="repository-conformance-directory-admin",
        team_id="security",
        capabilities=("identity_admin", "policy_admin"),
    )
    user = store.create_user(administrator=admin, value=_directory_user_value())
    repeated_user = store.create_user(administrator=admin, value=_directory_user_value())
    user_id = str(user.resource["id"])
    group_value = _directory_group_value(user_id, members=(user_id,))
    group = store.create_group(administrator=admin, value=group_value)
    repeated_group = store.create_group(administrator=admin, value=group_value)
    human_identity = store.identity_for_subject(
        CONFORMANCE_ISSUER,
        CONFORMANCE_HUMAN_SUBJECT,
    )
    _require(human_identity is not None, "repository_contract_directory_human_missing")
    workload = store.create_workload(
        administrator=admin,
        value=_directory_workload_value(display_name="Repository Conformance Workload"),
    )
    workload_identity = store.identity_for_subject(
        CONFORMANCE_ISSUER,
        CONFORMANCE_WORKLOAD_SUBJECT,
    )
    _require(
        workload_identity is not None,
        "repository_contract_directory_workload_missing",
    )
    return {
        "store": store,
        "admin": admin,
        "user": user,
        "group": group,
        "workload": workload,
        "human_identity": human_identity,
        "workload_identity": workload_identity,
        "user_id": user_id,
        "repeated_user_changed": repeated_user.changed,
        "repeated_group_changed": repeated_group.changed,
    }


def _finalize_directory_contract(state: dict[str, object]) -> dict[str, object]:
    store = state["store"]
    admin = state["admin"]
    user = state["user"]
    group = state["group"]
    workload = state["workload"]
    user_id = str(state["user_id"])
    group_id = str(group.resource["id"])
    group_without_members = _directory_group_value(user_id, members=())
    removed = store.replace_group(
        administrator=admin,
        resource_id=group_id,
        value=group_without_members,
        if_match=str(group.resource["meta"]["version"]),
    )
    unassigned = _expect_code(
        lambda: store.identity_for_subject(
            CONFORMANCE_ISSUER,
            CONFORMANCE_HUMAN_SUBJECT,
        ),
        expected="directory_subject_unassigned",
        code="repository_contract_directory_unassignment_not_denied",
    )
    restored = store.replace_group(
        administrator=admin,
        resource_id=group_id,
        value=_directory_group_value(user_id, members=(user_id,)),
        if_match=str(removed.resource["meta"]["version"]),
    )
    restored_identity = store.identity_for_subject(
        CONFORMANCE_ISSUER,
        CONFORMANCE_HUMAN_SUBJECT,
    )
    _require(
        restored_identity is not None,
        "repository_contract_directory_reassignment_missing",
    )
    workload_id = str(workload.resource["id"])
    updated_workload = store.replace_workload(
        administrator=admin,
        resource_id=workload_id,
        value=_directory_workload_value(
            display_name="Repository Conformance Workload Updated"
        ),
        if_match=str(workload.resource["meta"]["version"]),
    )
    inactive_workload = store.deactivate(
        administrator=admin,
        resource_type="Workload",
        resource_id=workload_id,
        if_match=str(updated_workload.resource["meta"]["version"]),
    )
    workload_inactive = _expect_code(
        lambda: store.identity_for_subject(
            CONFORMANCE_ISSUER,
            CONFORMANCE_WORKLOAD_SUBJECT,
        ),
        expected="directory_identity_inactive",
        code="repository_contract_directory_workload_deactivation_not_denied",
    )
    cross_tenant_read = _expect_code(
        lambda: store.get(
            organization_id=SECONDARY_ORGANIZATION,
            resource_type="User",
            resource_id=str(user.resource["id"]),
        ),
        expected="scim_resource_not_found",
        code="repository_contract_directory_cross_tenant_visible",
    )
    primary_counts = {
        resource_type: int(
            store.list(
                organization_id=PRIMARY_ORGANIZATION,
                resource_type=resource_type,
            )["totalResults"]
        )
        for resource_type in ("User", "Group", "Workload")
    }
    secondary_counts = {
        resource_type: int(
            store.list(
                organization_id=SECONDARY_ORGANIZATION,
                resource_type=resource_type,
            )["totalResults"]
        )
        for resource_type in ("User", "Group", "Workload")
    }
    return {
        "user_created": bool(state["user"].changed),
        "user_idempotent": not bool(state["repeated_user_changed"]),
        "group_created": bool(state["group"].changed),
        "group_idempotent": not bool(state["repeated_group_changed"]),
        "human_identity": _identity_projection(state["human_identity"]),
        "workload_identity": _identity_projection(state["workload_identity"]),
        "group_unassignment_denied": unassigned,
        "group_reassigned": restored.changed,
        "restored_human_identity": _identity_projection(restored_identity),
        "workload_updated": _directory_resource_projection(updated_workload.resource),
        "workload_deactivated": inactive_workload.changed,
        "workload_deactivation_denied": workload_inactive,
        "cross_tenant_read_denied": cross_tenant_read,
        "primary_counts": primary_counts,
        "secondary_counts": secondary_counts,
        "issuer_organizations": store.organizations_for_issuer(CONFORMANCE_ISSUER),
    }


def _state_for_enrollment(store: Any, enrollment_id: str, label: str) -> str:
    maker = getattr(store, "new_authorization_state", None)
    if callable(maker):
        return str(maker(enrollment_id))
    return "repository-conformance-state-" + label + "-" + "s" * 32


def _issue_session(store: Any, identity: Identity, *, label: str) -> object:
    secret = "repository-conformance-enrollment-" + label + "-" + "e" * 32
    enrollment = store.create_enrollment(
        issuer=CONFORMANCE_ISSUER,
        client_name="codex",
        enrollment_secret=secret,
        organization_id=identity.organization_id,
    )
    state = _state_for_enrollment(store, enrollment.enrollment_id, label)
    nonce = "repository-conformance-nonce-" + label + "-" + "n" * 32
    verifier = "repository-conformance-pkce-" + label + "-" + "p" * 48
    store.begin_authorization(
        enrollment_id=enrollment.enrollment_id,
        state=state,
        browser_cookie="repository-conformance-browser-" + label + "-" + "b" * 32,
        nonce=nonce,
        pkce_verifier=verifier,
    )
    flow = store.consume_callback(
        state=state,
        browser_cookie="repository-conformance-browser-" + label + "-" + "b" * 32,
    )
    _require(
        flow.nonce == nonce and flow.pkce_verifier == verifier,
        "repository_contract_session_flow_mismatch",
    )
    store.authorize_enrollment(
        enrollment_id=enrollment.enrollment_id,
        subject=CONFORMANCE_HUMAN_SUBJECT,
        organization_id=identity.organization_id,
        actor_id=identity.actor_id,
        team_id=identity.team_id,
        clearance=identity.clearance,
    )
    return store.redeem_enrollment(
        enrollment_id=enrollment.enrollment_id,
        enrollment_secret=secret,
    )


def _exercise_session_contract(store: Any, identity: Identity) -> dict[str, object]:
    first = _issue_session(store, identity, label="first")
    first_principal = store.authenticate_access(first.access_token)
    refreshed = store.refresh(first.refresh_token)
    refresh_replay = _expect_code(
        lambda: store.refresh(first.refresh_token),
        expected="refresh_replay_detected",
        code="repository_contract_refresh_replay_not_denied",
    )
    refreshed_access_revoked = _expect_code(
        lambda: store.authenticate_access(refreshed.access_token),
        expected="invalid_session_credential",
        code="repository_contract_refresh_family_not_revoked",
    )

    second = _issue_session(store, identity, label="second")
    second_principal = store.authenticate_access(second.access_token)
    logout_recorded = store.revoke(second.access_token)
    logout_revoked = _expect_code(
        lambda: store.authenticate_access(second.access_token),
        expected="invalid_session_credential",
        code="repository_contract_logout_not_revoked",
    )

    third = _issue_session(store, identity, label="third")
    third_principal = store.authenticate_access(third.access_token)
    store.revoke_session(
        third_principal.session_id,
        event_type="authorization_mapping_removed",
        organization_id=identity.organization_id,
    )
    mapping_revoked = _expect_code(
        lambda: store.authenticate_access(third.access_token),
        expected="invalid_session_credential",
        code="repository_contract_mapping_revocation_not_enforced",
    )

    fourth = _issue_session(store, identity, label="fourth")
    active_before, _ = store.list_active_sessions(
        organization_id=identity.organization_id,
        actor_id=identity.actor_id,
        limit=10,
    )
    administratively_revoked = store.revoke_administratively(
        organization_id=identity.organization_id,
        decision_actor_id="repository-conformance-session-admin",
        reason_code="administrative",
        actor_id=identity.actor_id,
    )
    admin_revoked = _expect_code(
        lambda: store.authenticate_access(fourth.access_token),
        expected="invalid_session_credential",
        code="repository_contract_administrative_revocation_not_enforced",
    )
    active_after, _ = store.list_active_sessions(
        organization_id=identity.organization_id,
        actor_id=identity.actor_id,
        limit=10,
    )
    cross_tenant_active, _ = store.list_active_sessions(
        organization_id=SECONDARY_ORGANIZATION,
        actor_id=identity.actor_id,
        limit=10,
    )

    failed_secret = "repository-conformance-failed-enrollment-" + "f" * 32
    failed_enrollment = store.create_enrollment(
        issuer=CONFORMANCE_ISSUER,
        client_name="codex",
        enrollment_secret=failed_secret,
        organization_id=identity.organization_id,
    )
    failed_state = _state_for_enrollment(store, failed_enrollment.enrollment_id, "failed")
    failed_browser = "repository-conformance-browser-failed-" + "b" * 32
    store.begin_authorization(
        enrollment_id=failed_enrollment.enrollment_id,
        state=failed_state,
        browser_cookie=failed_browser,
        nonce="repository-conformance-nonce-failed-" + "n" * 32,
        pkce_verifier="repository-conformance-pkce-failed-" + "p" * 48,
    )
    store.fail_enrollment(enrollment_id=failed_enrollment.enrollment_id)
    failed_enrollment_denied = _expect_code(
        lambda: store.redeem_enrollment(
            enrollment_id=failed_enrollment.enrollment_id,
            enrollment_secret=failed_secret,
        ),
        expected="enrollment_not_redeemable",
        code="repository_contract_failed_enrollment_redeemed",
    )
    security_events, _ = store.list_security_events(
        organization_id=identity.organization_id,
        actor_id=identity.actor_id,
        limit=20,
    )
    event_types = sorted(event.event_type for event in security_events)
    return {
        "initial_principal": {
            "organization_id": first_principal.organization_id,
            "actor_id": first_principal.actor_id,
            "team_id": first_principal.team_id,
            "clearance": first_principal.clearance,
        },
        "refresh_replay": refresh_replay,
        "refreshed_access_revoked": refreshed_access_revoked,
        "logout_recorded": bool(logout_recorded),
        "logout_revoked": logout_revoked,
        "mapping_revoked": mapping_revoked,
        "active_before_administrative_revocation": len(active_before),
        "administratively_revoked": administratively_revoked,
        "administrative_revoked": admin_revoked,
        "active_after_administrative_revocation": len(active_after),
        "cross_tenant_active_sessions": len(cross_tenant_active),
        "failed_enrollment_denied": failed_enrollment_denied,
        "security_event_types": event_types,
        "second_session_organization": second_principal.organization_id,
    }


def prove_repository_conformance(runtime_dsn: str) -> dict[str, object]:
    """Run all currently supported SQLite/PostgreSQL repository comparisons."""

    resolver = _ConformancePolicyResolver()
    accounting_base = datetime.now(timezone.utc).replace(microsecond=0)
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        sqlite_usage = UsageStore(root / "usage.sqlite3")
        postgres_usage = PostgresUsageStore(
            runtime_dsn,
            organization_ids=(PRIMARY_ORGANIZATION, SECONDARY_ORGANIZATION),
        )
        postgres_security = PostgresSecurityStore(
            runtime_dsn,
            organization_ids=(PRIMARY_ORGANIZATION, SECONDARY_ORGANIZATION),
        )
        sqlite_usage_security = _exercise_usage_security_contract(
            accounting=sqlite_usage,
            security=sqlite_usage,
            base=accounting_base,
        )
        postgres_usage_security = _exercise_usage_security_contract(
            accounting=postgres_usage,
            security=postgres_security,
            base=accounting_base,
        )
        _same(
            sqlite_usage_security,
            postgres_usage_security,
            "repository_contract_usage_security_mismatch",
        )

        sqlite_directory = SQLiteDirectoryStore(
            root / "directory.sqlite3",
            trusted_issuers=(CONFORMANCE_ISSUER,),
            authorization_resolver=resolver,
        )
        postgres_directory = PostgresDirectoryStore(
            runtime_dsn,
            trusted_issuers=(CONFORMANCE_ISSUER,),
            routing_key=b"repository-conformance-routing-key"[:32].ljust(32, b"r"),
            authorization_resolver=resolver,
        )
        sqlite_directory_state = _prepare_directory_contract(sqlite_directory)
        postgres_directory_state = _prepare_directory_contract(postgres_directory)

        clock = lambda: datetime.now(timezone.utc).replace(microsecond=0)
        session_key = b"repository-conformance-session-key"[:32].ljust(32, b"s")
        sqlite_session = SQLiteSessionStore(
            root / "sessions.sqlite3",
            master_key=session_key,
            access_ttl_seconds=600,
            absolute_ttl_seconds=43_200,
            enrollment_ttl_seconds=300,
            clock=clock,
        )
        postgres_session = PostgresSessionStore(
            runtime_dsn,
            organization_ids=(PRIMARY_ORGANIZATION, SECONDARY_ORGANIZATION),
            master_key=session_key,
            access_ttl_seconds=600,
            absolute_ttl_seconds=43_200,
            enrollment_ttl_seconds=300,
            clock=clock,
        )
        sqlite_session_result = _exercise_session_contract(
            sqlite_session,
            sqlite_directory_state["human_identity"],
        )
        postgres_session_result = _exercise_session_contract(
            postgres_session,
            postgres_directory_state["human_identity"],
        )
        _same(
            sqlite_session_result,
            postgres_session_result,
            "repository_contract_session_mismatch",
        )

        sqlite_directory_result = _finalize_directory_contract(sqlite_directory_state)
        postgres_directory_result = _finalize_directory_contract(postgres_directory_state)
        _same(
            sqlite_directory_result,
            postgres_directory_result,
            "repository_contract_directory_mismatch",
        )

    return {
        "sqlite_postgresql_semantic_parity": True,
        "usage_security_contract": True,
        "session_contract": True,
        "directory_contract": True,
        "tenant_scoped_negative_reads": True,
        "postgresql_only_contracts": (
            "policy_administration",
            "tenant_lifecycle",
        ),
        "excluded_contracts": ("deprecated_builtin_context",),
    }
