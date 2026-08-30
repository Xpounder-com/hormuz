"""Shared, explicitly synthetic attribution behavior across both adapters."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
import json
import threading
from unittest import mock

from hormuz._portfolio_sql import PortfolioSQL
from hormuz.attribution_admission import Admission, AdmissionError, select_admission
from hormuz.attribution_config import AttributionBinding, AttributionConfig, WorkScopeRef
from hormuz.attribution_repository import AttributionRepository
from hormuz.portfolio_config import PortfolioRoleBinding
from hormuz.portfolio_repository import create_portfolio_repository
from hormuz.portfolio_service import PortfolioService
from hormuz.portfolio_wire import ATTRIBUTIONS, SCOPES, PortfolioError, canonical, validate
from hormuz.store import ReservationScope
from hormuz.store_router import create_repository_bundle

if __package__:
    from ._portfolio_fixture import ADMIN, OTHER, VIEWER, SELF, create_request, version_request
else:
    from _portfolio_fixture import ADMIN, OTHER, VIEWER, SELF, create_request, version_request


def attribution_request(attempt_id, scope, previous=None, **changes):
    return {"schema_id": "hormuz.governed-run-attribution-request", "schema_version": 1,
            "request_attempt_id": attempt_id,
            "work_scope": None if scope is None else {"work_scope_id": scope["work_scope_id"], "version": scope["version"]},
            "expected_attribution_event_id": previous, "state": "active",
            "reason_code": "corrected" if previous else "bound", **changes}


def attributed_config(config, scope):
    reference = WorkScopeRef(scope["work_scope_id"], scope["version"])
    return replace(config, attribution_control=AttributionConfig(tuple(
        AttributionBinding("acme", "alice", client, (reference,), (), False) for client in ("codex", "claude-code")
    )))


def seed_attribution_metadata(config, *, environ=None):
    """Populate every real attribution-owned table for isolated recovery proof."""
    group = create_repository_bundle(config, portfolio_factory=create_portfolio_repository, environ=environ)
    service = PortfolioService(config, group.portfolio)
    scope = service.dispatch(ADMIN, "POST", SCOPES, body=canonical(create_request()).encode(), idempotency_key="attribution-recovery-scope")[1]
    config = attributed_config(config, scope)
    owner = create_portfolio_repository(config, environ=environ)
    service = PortfolioService(config, owner)
    identity = config.identities_by_token[ADMIN]
    attempt = group.usage.begin_request_attempt(
        identity=identity, client="codex", protocol="openai", requested_model="recovery-alias",
        resolved_alias="recovery-alias", upstream_model="recovery-route", policy_version="recovery-policy-v1",
        policy_action="allowed", redaction_count=0, redaction_rules=(), scopes=(ReservationScope(name="organization"),),
        reserved_tokens=20, reserved_cost_microusd=40, ttl_seconds=60,
    )
    selected = select_admission(config, identity, "codex", [f'v1;work_scope_id={scope["work_scope_id"]};version=1'], account_usage=True)
    event = owner.attributions.admit(identity, "codex", "openai", selected, attempt.attempt_id)
    group.usage.finalize_request_attempt(attempt=attempt, organization_id="acme", status="succeeded", provider_reported_model="recovery-actual-v1", cost_microusd=11)
    body = attribution_request(attempt.attempt_id, scope, event["attribution_event_id"])
    result = service.dispatch(ADMIN, "POST", ATTRIBUTIONS, body=canonical(body).encode(), idempotency_key="attribution-recovery-correction")
    owner.attributions.record_rejection(identity, "codex", "openai", AdmissionError("invalid_reference"))
    page = service.dispatch(ADMIN, "GET", ATTRIBUTIONS, query="limit=1")[1]
    return config, (body, "attribution-recovery-correction", result), page, attempt.attempt_id


class AttributionAssertions:
    def setup_attribution(self):
        group = create_portfolio_repository(self.config, environ=self.environment)
        service = PortfolioService(self.config, group)
        self.scope = service.dispatch(ADMIN, "POST", SCOPES, body=canonical(create_request()).encode(), idempotency_key="scope")[1]
        self.config = attributed_config(self.config, self.scope)
        self.group = create_portfolio_repository(self.config, environ=self.environment)
        self.repository = self.group.attributions
        self.service = PortfolioService(self.config, self.group)
        self.identity = self.config.identities_by_token[ADMIN]
        self.principal = self.service.authenticate(ADMIN)

    def call(self, body=None, *, token=ADMIN, key="correction", query=""):
        status, result = self.service.dispatch(token, "POST" if body is not None else "GET", ATTRIBUTIONS,
                                              body=canonical(body).encode() if body is not None else b"", idempotency_key=key, query=query)
        self.assertEqual(status, 201 if body is not None else 200)
        validate(result, result["schema_id"])
        return result

    def attempt(self, *, identity=None, client="codex", protocol="openai", attribute=True, explicit=True):
        identity = identity or self.identity
        attempt = self.store.begin_request_attempt(
            identity=identity, client=client, protocol=protocol, requested_model="requested-alias",
            resolved_alias="resolved-alias", upstream_model="routed-model", policy_version="event-policy-v1",
            policy_action="allowed", redaction_count=0, redaction_rules=(), scopes=(ReservationScope(name="organization"),),
            reserved_tokens=20, reserved_cost_microusd=40, ttl_seconds=60,
        )
        event = None
        if attribute:
            headers = [f'v1;work_scope_id={self.scope["work_scope_id"]};version={self.scope["version"]}'] if explicit else []
            admission = select_admission(self.config, identity, client, headers, account_usage=True)
            self.repository.preflight(identity, client, protocol, admission)
            event = self.repository.admit(identity, client, protocol, admission, attempt.attempt_id)
        return attempt, event

    def finish(self, attempt, actual="actual-model-version", *, organization="acme"):
        self.store.finalize_request_attempt(attempt=attempt, organization_id=organization, status="succeeded",
                                          provider_reported_model=actual, input_tokens=7, output_tokens=3, cost_microusd=11)

    def error(self, code, operation):
        with self.assertRaises(PortfolioError) as caught:
            operation()
        self.assertEqual(caught.exception.code, code)

    def check_attribution_sources_and_immutable_facts(self):
        attempt, event = self.attempt()
        self.assertEqual(event["confidence"], "explicit_authorized")
        self.assertIsNone(event["actor_id"])
        pending = self.repository.attempt_facts(self.principal, attempt.attempt_id)
        self.assertIsNone(pending["provider_reported_model"])
        self.assertIsNone(pending["cost_microusd"])
        self.finish(attempt)
        facts = self.repository.attempt_facts(self.principal, attempt.attempt_id)
        self.assertEqual((facts["requested_model"], facts["resolved_alias"], facts["upstream_model"], facts["provider_reported_model"]),
                         ("requested-alias", "resolved-alias", "routed-model", "actual-model-version"))
        self.assertEqual((facts["actor_id"], facts["team_id"], facts["client"], facts["policy_version"]),
                         ("alice", "engineering", "codex", "event-policy-v1"))
        self.assertEqual((facts["cost_microusd"], facts["cost_basis"]), (11, "configured_rate_card_estimate"))
        changed_identity = replace(self.identity, team_id="new-team")
        changed = replace(self.config, identities_by_token={**self.config.identities_by_token, ADMIN: changed_identity})
        reader = create_portfolio_repository(changed, environ=self.environment).attributions
        self.assertEqual(reader.attempt_facts(self.principal, attempt.attempt_id), facts)
        unattributed, missing = self.attempt(explicit=False)
        self.finish(unattributed, actual=None)
        self.assertEqual((missing["work_scope"], missing["confidence"], missing["reason_code"]), (None, "unattributed", "missing_evidence"))
        self.assertIsNone(self.repository.attempt_facts(self.principal, unattributed.attempt_id)["provider_reported_model"])
        legacy, _ = self.attempt(attribute=False)
        self.store.mark_request_attempt_outcome_unknown(attempt=legacy, organization_id="acme", reason_code="provider_transport_ambiguous")
        unknown = self.repository.attempt_facts(self.principal, legacy.attempt_id)
        self.assertEqual(unknown["state"], "outcome_unknown")
        self.assertIsNone(unknown["attribution"])
        self.assertIsNone(unknown["cost_microusd"])
        self.assertEqual(self.store.active_budget_reservations(organization_id="acme"), 1)

        # Exercise stored defaults/ambiguity, not only the pure selector.
        other = self.service.dispatch(ADMIN, "POST", SCOPES, body=canonical(create_request()).encode(), idempotency_key="other-default")[1]
        first = WorkScopeRef(self.scope["work_scope_id"], 1)
        second = WorkScopeRef(other["work_scope_id"], 1)
        for defaults, confidence in (((second,), "server_side_default"), ((first, second), "ambiguous")):
            binding = AttributionBinding("acme", "alice", "codex", (first, second), defaults, False)
            configured = replace(self.config, attribution_control=AttributionConfig((binding,)))
            repository = create_portfolio_repository(configured, environ=self.environment).attributions
            fresh, _ = self.attempt(attribute=False)
            admission = select_admission(configured, self.identity, "codex", [], account_usage=True)
            repository.preflight(self.identity, "codex", "openai", admission)
            selected = repository.admit(self.identity, "codex", "openai", admission, fresh.attempt_id)
            self.assertEqual(selected["confidence"], confidence)
            self.assertEqual(selected["work_scope"], {"work_scope_id": second.work_scope_id, "version": 1} if len(defaults) == 1 else None)
            self.finish(fresh)

    def check_append_only_corrections_voids_and_idempotency(self):
        attempt, original = self.attempt()
        self.finish(attempt)
        before_v1 = self.v1_rows()
        request = attribution_request(attempt.attempt_id, self.scope, original["attribution_event_id"])
        corrected = self.call(request)
        self.assertEqual((corrected["actor_id"], corrected["confidence"]), ("alice", "authorized_post_run"))
        before = self.attribution_rows()
        self.assertEqual(self.call(request), corrected)
        self.assertEqual(self.attribution_rows(), before)
        self.error("idempotency_conflict", lambda: self.call({**request, "reason_code": "bound"}))
        self.error("version_conflict", lambda: self.call(request, key="stale"))
        voided = self.call(attribution_request(attempt.attempt_id, None, corrected["attribution_event_id"], state="voided", reason_code="voided"), key="void")
        rebound = self.call(attribution_request(attempt.attempt_id, self.scope, voided["attribution_event_id"]), key="rebind")
        self.assertEqual(self.repository.attempt_facts(self.principal, attempt.attempt_id)["attribution"], rebound)
        events = self.call()["items"]
        self.assertIn(original, events)
        self.assertIn(corrected, events)
        self.assertEqual(len(events), 4)
        self.assertEqual(self.v1_rows(), before_v1)
        legacy, _ = self.attempt(attribute=False)
        self.finish(legacy)
        self.assertEqual(self.call(attribution_request(legacy.attempt_id, self.scope), key="late")["reason_code"], "bound")
        pending, _ = self.attempt(attribute=False)
        self.error("version_conflict", lambda: self.call(attribution_request(pending.attempt_id, self.scope), key="pending"))

    def check_authority_precedes_lookup_and_tenant_join(self):
        with mock.patch.object(self.repository, "_transaction", side_effect=AssertionError("unauthorized_storage")):
            for token, code in (("invalid", "unauthenticated"), (VIEWER, "forbidden"), (SELF, "forbidden")):
                self.error(code, lambda token=token: self.call(token=token))
            forged = Admission(WorkScopeRef(self.scope["work_scope_id"], 1), "explicit_authorized", "bound")
            with self.assertRaises(AdmissionError):
                self.repository.preflight(self.config.identities_by_token[OTHER], "codex", "openai", forged)
            readonly = AttributionRepository(self.config, dsn="", read_only=True)
            self.error("forbidden", lambda: readonly.attempt_facts(self.principal, "unknown"))
        foreign, _ = self.attempt(identity=self.config.identities_by_token[OTHER], attribute=False)
        self.finish(foreign, organization="beta")
        self.error("not_found", lambda: self.call(attribution_request(foreign.attempt_id, self.scope)))
        self.error("not_found", lambda: self.repository.attempt_facts(self.principal, foreign.attempt_id))
        self.assertEqual(self.call(token=OTHER)["items"], [])
        mismatched, _ = self.attempt(identity=replace(self.identity, team_id="other-team"), attribute=False)
        admission = select_admission(self.config, self.identity, "codex", [], account_usage=True)
        with self.assertRaises(AdmissionError) as caught:
            self.repository.admit(self.identity, "codex", "openai", admission, mismatched.attempt_id)
        self.assertEqual(caught.exception.reason, "unauthorized_scope")
        self.assertEqual(self.call()["items"], [])

    def check_scope_race_fails_and_never_retargets(self):
        attempt, _ = self.attempt(attribute=False)
        admission = select_admission(self.config, self.identity, "codex", [f'v1;work_scope_id={self.scope["work_scope_id"]};version=1'], account_usage=True)
        self.repository.preflight(self.identity, "codex", "openai", admission)
        self.service.dispatch(ADMIN, "POST", SCOPES + "/" + self.scope["work_scope_id"] + "/versions",
                              body=canonical(version_request(self.scope)).encode(), idempotency_key="advance")
        before = self.attribution_rows()
        with self.assertRaises(AdmissionError) as caught:
            self.repository.admit(self.identity, "codex", "openai", admission, attempt.attempt_id)
        self.assertEqual(caught.exception.reason, "stale_version")
        self.assertEqual(self.attribution_rows(), before)
        self.assertEqual(self.store.active_budget_reservations(organization_id="acme"), 1)

    def check_admission_and_correction_concurrency(self):
        attempt, _ = self.attempt(attribute=False)
        admission = select_admission(self.config, self.identity, "codex", [], account_usage=True)
        barrier = threading.Barrier(6)
        def admit(_):
            barrier.wait(timeout=10)
            return self.repository.admit(self.identity, "codex", "openai", admission, attempt.attempt_id)
        with ThreadPoolExecutor(max_workers=6) as pool:
            events = list(pool.map(admit, range(6)))
        self.assertTrue(all(event == events[0] for event in events))
        self.assertEqual(len(self.call()["items"]), 1)
        self.finish(attempt)
        barrier = threading.Barrier(2)
        request = attribution_request(attempt.attempt_id, self.scope, events[0]["attribution_event_id"])
        def correct(key):
            barrier.wait(timeout=10)
            try:
                return self.service.dispatch(ADMIN, "POST", ATTRIBUTIONS, body=canonical(request).encode(), idempotency_key=key)[0]
            except PortfolioError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(list(pool.map(correct, ("a", "b"))), [201, "version_conflict"])
        self.assertEqual(len(self.call()["items"]), 2)

    def check_atomicity_and_audit_before_delivery(self):
        attempt, original = self.attempt()
        self.finish(attempt)
        before = self.attribution_rows()
        insert = PortfolioSQL.insert
        def fail(sql, table, row):
            if table == "portfolio_attribution_idempotency":
                raise RuntimeError("synthetic_before_commit")
            return insert(sql, table, row)
        with mock.patch.object(PortfolioSQL, "insert", fail):
            with self.assertRaisesRegex(RuntimeError, "synthetic_before_commit"):
                self.call(attribution_request(attempt.attempt_id, self.scope, original["attribution_event_id"]))
        self.assertEqual(self.attribution_rows(), before)
        with mock.patch.object(self.repository, "_audit", side_effect=RuntimeError("synthetic_audit_failure")):
            with self.assertRaisesRegex(RuntimeError, "synthetic_audit_failure"):
                self.call()
            with self.assertRaisesRegex(RuntimeError, "synthetic_audit_failure"):
                self.repository.attempt_facts(self.principal, attempt.attempt_id)
        self.assertEqual(self.attribution_rows(), before)
        with mock.patch.object(self.repository, "_event", return_value={"schema_id": "hormuz.governed-run-attribution-event", "excluded": "SYNTHETIC_EXCLUDED"}):
            self.error("unavailable", self.call)
        self.assertEqual(self.attribution_rows(), before)

    def check_frozen_pagination_and_role_bound_cursors(self):
        originals = []
        for _ in range(3):
            attempt, event = self.attempt()
            self.finish(attempt)
            originals.append(event)
        page = self.call(query="limit=1")
        self.assertTrue(page["has_more"])
        self.attempt()
        seen, cursor = page["items"][:], page["next_cursor"]
        while cursor:
            result = self.call(query="limit=1&cursor=" + cursor)
            self.assertEqual(result["as_of"], page["as_of"])
            seen.extend(result["items"])
            cursor = result["next_cursor"]
        self.assertEqual(seen, sorted(originals, key=lambda row: (row["event_at"], row["attribution_event_id"]), reverse=True))
        for query in ("cursor=forged", "cursor=" + page["next_cursor"] + "&work_scope_id=" + self.scope["work_scope_id"]):
            self.error("cursor_invalid", lambda query=query: self.call(query=query))
        self.error("cursor_invalid", lambda: self.call(token=OTHER, query="cursor=" + page["next_cursor"]))
        control = self.config.portfolio_control
        roles = (replace(control.role_bindings[0], roles=("platform_viewer", "portfolio_admin")), *control.role_bindings[1:])
        changed = replace(self.config, portfolio_control=replace(control, role_bindings=roles))
        service = PortfolioService(changed, create_portfolio_repository(changed, environ=self.environment))
        self.error("cursor_invalid", lambda: service.dispatch(ADMIN, "GET", ATTRIBUTIONS, query="cursor=" + page["next_cursor"]))
        expired = (datetime.fromisoformat(page["as_of"]) + timedelta(seconds=3601)).isoformat().replace("+00:00", "Z")
        with mock.patch.object(PortfolioSQL, "now", return_value=expired):
            self.error("cursor_invalid", lambda: self.call(query="cursor=" + page["next_cursor"]))
        self.assertEqual(len(self.call(query="work_scope_id=" + self.scope["work_scope_id"])["items"]), 4)

    def check_rejections_are_not_fabricated_attempts_or_work_content(self):
        before = self.v1_rows()
        for reason in ("ambiguous", "invalid_reference", "stale_version", "unsupported", "unauthorized_scope", "missing_evidence"):
            self.repository.record_rejection(self.identity, "codex", "openai", AdmissionError(reason))
        self.assertEqual(self.v1_rows(), before)
        self.assertEqual(self.call()["items"], [])
        counts = self.repository.rejection_counts(self.principal)
        self.assertEqual(sum(row["receipts"] for row in counts), 6)
        self.assertEqual(self.repository.rejection_counts(self.service.authenticate(OTHER)), [])
        self.assertNotIn("SYNTHETIC_EXCLUDED", canonical(self.attribution_rows()))

    def check_invalid_requests_cannot_reach_storage(self):
        with mock.patch.object(self.repository, "_transaction", side_effect=AssertionError("invalid_storage")):
            for body in ({}, {**attribution_request("attempt", self.scope), "prompt": "SYNTHETIC_EXCLUDED"}):
                self.error("invalid_request", lambda body=body: self.call(body))
            for query in ("organization_id=beta", "model_id=wrong", "limit=101", "limit=1&limit=2", "start_at=2026-01-01T00:00:00Z"):
                self.error("invalid_request", lambda query=query: self.call(query=query))
            for key in (None, "", "path/value", "x" * 129):
                self.error("invalid_request", lambda key=key: self.call(attribution_request("attempt", self.scope), key=key))
