"""Named, shared v1 ledger scenarios run against both real storage adapters."""

from __future__ import annotations

import json
import unittest
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest import mock

from hormuz._persistence import RequestAttemptStateError, ReservationDenied, ReservationScope, UsageRepository
from hormuz.audit_chain import build_audit_chain_checkpoint
from hormuz.config import Identity


class LedgerClock(datetime):
    # Stay inside the actual UTC month so the unchanged default-month helper
    # remains real. Advancing this clock never sleeps or crosses a month edge.
    current = datetime.now(timezone.utc).replace(day=15, hour=12, minute=0, second=0, microsecond=0)

    @classmethod
    def now(cls, tz=None):
        return cls.current.replace(tzinfo=None) if tz is None else cls.current.astimezone(tz)


@contextmanager
def ledger_clock():
    LedgerClock.current = datetime.now(timezone.utc).replace(
        day=15, hour=12, minute=0, second=0, microsecond=0,
    )
    with (
        mock.patch("hormuz.store.datetime", LedgerClock),
        mock.patch("hormuz.postgres_usage_store.datetime", LedgerClock),
    ):
        yield


def ledger_identity(organization_id: str = "acme") -> Identity:
    return Identity(
        token_env="UNUSED_LEDGER_TEST_TOKEN",
        token="synthetic-unused-secret",
        actor_id="alice",
        actor_name="Alice",
        team_id="engineering",
        team_name="Engineering",
        organization_id=organization_id,
        identity_type="human",
        authentication_source="oidc",
    )


def _exact_decimal_integer(value: object) -> int:
    # PostgreSQL SUM(bigint) yields numeric; only exact integer aggregates are
    # normalized. Do not stringify arbitrary values or round away differences.
    if not isinstance(value, Decimal) or not value.is_finite() or value != value.to_integral_value():
        raise TypeError("unexpected non-integral repository result")
    return int(value)


def read_usage_repository(
    store: UsageRepository,
    organization_id: str = "acme",
    *,
    verify_chain: bool = True,
) -> dict[str, object]:
    """Exercise only genuinely read-only v1 operations, including on an empty DB.

    audit_chain_head is intentionally absent: its legacy contract initializes
    an empty epoch. verify_audit_chain provides the non-initializing read, but
    PostgreSQL's shared lock requires a normal transaction; callers testing
    SQL READ ONLY mode exercise that verifier separately.
    """

    store.verify_ready()
    start = LedgerClock.current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    anchor = store.audit_chain_anchor_status(organization_id=organization_id)
    anchor_reference = anchor.oldest_unanchored_at or anchor.latest_checkpoint_at or LedgerClock.current
    anchor = store.audit_chain_anchor_status(
        organization_id=organization_id, maximum_age_seconds=60,
        now=anchor_reference + timedelta(seconds=30),
    )
    verified = store.verify_audit_chain(organization_id=organization_id) if verify_chain else None
    events: dict[str, object] = {}
    for kind in ("all", "usage", "security"):
        normalized = [
            {key: value for key, value in event.items() if key not in {"id", "occurred_at"}}
            for event in store.audit_events(
                since=start.isoformat(), kind=kind, organization_id=organization_id,
            )
        ]
        events[kind] = sorted(normalized, key=lambda event: json.dumps(event, sort_keys=True))
    return {
        "totals": asdict(store.monthly_totals(organization_id=organization_id)),
        "period_totals": asdict(store.monthly_totals(
            organization_id=organization_id, starts_at=start, ends_before=end,
        )),
        "actor_totals": asdict(store.monthly_totals(
            organization_id=organization_id, actor_id="alice", team_id="engineering",
        )),
        "other_actor_totals": asdict(store.monthly_totals(
            organization_id=organization_id, actor_id="nobody",
        )),
        "secret_totals": asdict(store.monthly_secret_totals(organization_id=organization_id)),
        "actor_secret_totals": asdict(store.monthly_secret_totals(
            organization_id=organization_id, actor_id="alice", team_id="engineering",
        )),
        "summary": store.summary_rows(organization_id=organization_id),
        "reports": {
            dimension: store.report_rows(
                group_by=dimension, organization_id=organization_id,
                actor_id="alice", team_id="engineering",
            )
            for dimension in ("organization", "team", "person", "model", "client", "provider")
        },
        "events": events,
        "reservations": store.active_budget_reservations(organization_id=organization_id),
        "verified_head": (
            (verified.chain_version, verified.chain_epoch, verified.sequence, bool(verified.head_digest))
            if verified is not None else None
        ),
        "anchor": (
            anchor.chain_epoch, anchor.sequence, anchor.overdue,
            bool(anchor.latest_checkpoint_at), bool(anchor.oldest_unanchored_at),
        ),
    }


def exercise_usage_repository(case: unittest.TestCase, store: UsageRepository) -> dict[str, object]:
    """Characterize all 21 v1 operations, retry failures, tenant scope, and holds."""

    identity = ledger_identity()
    scopes = (ReservationScope(name="organization", token_limit=1000, cost_limit_microusd=10000),)
    with ledger_clock():
        store.verify_ready()
        empty = store.verify_audit_chain(organization_id="acme")
        case.assertEqual((empty.chain_epoch, empty.sequence, empty.head_digest), (1, 0, None))
        # This is the existing initializing operation, not a read-only alias.
        case.assertEqual(store.audit_chain_head(organization_id="acme"), empty)

        event_id = store.record(
            identity=identity, client="codex", protocol="openai", requested_model="ledger-fast",
            resolved_alias="ledger-fast", upstream_model="provider-fast", provider_reported_model="provider-fast-v1",
            policy_version="policy-v1", policy_action="allowed+redacted", status="succeeded",
            input_tokens=12, output_tokens=3, cache_read_tokens=2, cache_write_tokens=1,
            reasoning_tokens=1, cost_microusd=200, provider_request_id="synthetic-request-1",
            redaction_count=2, redaction_rules=("openai_api_key",),
        )
        secret_id = store.record_secret_event(
            identity=identity, client="codex", protocol="openai", requested_model="ledger-fast",
            policy_version="policy-v1", action="redacted", detection_count=2, rules=("openai_api_key",),
        )
        case.assertIsInstance(event_id, str)
        case.assertIsInstance(secret_id, str)
        case.assertNotEqual(event_id, secret_id)
        store.record(
            identity=ledger_identity("beta"), client="claude-code", protocol="anthropic",
            requested_model="other-model", resolved_alias=None, upstream_model=None,
            policy_action="denied", status="denied",
        )

        case.assertIsNone(store.reserve_budget(
            identity=identity, scopes=(), reserved_tokens=10, reserved_cost_microusd=20, ttl_seconds=5,
        ))
        reservation = store.reserve_budget(
            identity=identity, scopes=scopes, reserved_tokens=50, reserved_cost_microusd=500, ttl_seconds=5,
        )
        case.assertIsInstance(reservation, str)
        case.assertEqual(store.active_budget_reservations(organization_id="acme"), 1)
        store.refresh_budget_reservation(reservation, organization_id="acme", ttl_seconds=60)
        LedgerClock.current += timedelta(seconds=6)
        case.assertEqual(store.active_budget_reservations(organization_id="acme"), 1)
        store.release_budget_reservation(reservation, organization_id="beta")
        case.assertEqual(store.active_budget_reservations(organization_id="acme"), 1)
        store.release_budget_reservation(reservation, organization_id="acme")
        store.release_budget_reservation(None, organization_id="acme")
        store.refresh_budget_reservation(None, organization_id="acme", ttl_seconds=1)
        case.assertEqual(store.active_budget_reservations(organization_id="acme"), 0)
        with case.assertRaises(ReservationDenied):
            store.reserve_budget(
                identity=identity, scopes=(ReservationScope(name="organization", token_limit=15),),
                reserved_tokens=1, reserved_cost_microusd=0, ttl_seconds=60,
            )
        case.assertEqual(store.active_budget_reservations(organization_id="acme"), 0)

        terminal = store.begin_request_attempt(
            identity=identity, client="codex", protocol="openai", requested_model="ledger-fast",
            resolved_alias="ledger-fast", upstream_model="provider-fast", policy_version="policy-v1",
            policy_action="allowed", redaction_count=0, redaction_rules=(), scopes=scopes,
            reserved_tokens=25, reserved_cost_microusd=350, ttl_seconds=60,
        )
        case.assertEqual(terminal.attempt_id, terminal.reservation_id)
        with case.assertRaisesRegex(RuntimeError, "request_attempt_not_found"):
            store.finalize_request_attempt(attempt=terminal, organization_id="beta", status="succeeded")
        case.assertEqual(store.active_budget_reservations(organization_id="acme"), 1)
        store.finalize_request_attempt(
            attempt=terminal, organization_id="acme", status="succeeded",
            provider_reported_model="provider-fast-v1", input_tokens=20, output_tokens=4,
            cost_microusd=300, provider_request_id="synthetic-request-2",
        )
        with case.assertRaisesRegex(RequestAttemptStateError, "request_attempt_not_pending"):
            store.finalize_request_attempt(attempt=terminal, organization_id="acme", status="succeeded")
        case.assertEqual(store.active_budget_reservations(organization_id="acme"), 0)

        unknown = store.begin_request_attempt(
            identity=identity, client="codex", protocol="openai", requested_model="unknown-model",
            resolved_alias=None, upstream_model="provider-fast", policy_version="policy-v1",
            policy_action="allowed", redaction_count=0, redaction_rules=(), scopes=scopes,
            reserved_tokens=8, reserved_cost_microusd=400, ttl_seconds=5,
        )
        case.assertTrue(store.mark_request_attempt_outcome_unknown(
            attempt=unknown, organization_id="acme", reason_code="provider_transport_ambiguous",
        ))
        case.assertFalse(store.mark_request_attempt_outcome_unknown(
            attempt=unknown, organization_id="acme", reason_code="provider_transport_ambiguous",
        ))
        store.release_budget_reservation(unknown.reservation_id, organization_id="acme")
        case.assertEqual(store.active_budget_reservations(organization_id="acme"), 1)
        store.begin_request_attempt(
            identity=identity, client="codex", protocol="openai", requested_model="stale-model",
            resolved_alias=None, upstream_model="provider-fast", policy_version="policy-v1",
            policy_action="allowed", redaction_count=0, redaction_rules=(), scopes=scopes,
            reserved_tokens=7, reserved_cost_microusd=300, ttl_seconds=5,
        )
        LedgerClock.current += timedelta(seconds=6)
        case.assertEqual(store.sweep_stale_request_attempts(organization_id="beta"), 0)
        case.assertEqual(store.sweep_stale_request_attempts(organization_id="acme"), 1)
        case.assertEqual(store.sweep_stale_request_attempts(organization_id="acme"), 0)
        case.assertEqual(store.active_budget_reservations(organization_id="acme"), 2)

        head = store.audit_chain_head(organization_id="acme")
        # Three frozen v1 usage/security entries plus one immutable finance
        # sidecar for each of the terminal, unknown, and stale attempts.
        case.assertEqual((head.chain_epoch, head.sequence), (1, 6))
        checkpoint = build_audit_chain_checkpoint(head, created_at=LedgerClock.current)
        case.assertEqual(store.verify_audit_chain(organization_id="acme", checkpoint=checkpoint), head)
        unanchored = store.audit_chain_anchor_status(organization_id="acme", now=LedgerClock.current)
        case.assertIsNotNone(unanchored.oldest_unanchored_at)
        # PostgreSQL owns commit-time appended_at; SQLite uses its adapter
        # clock. Exercise identical age rules against each actual timestamp.
        case.assertTrue(store.audit_chain_anchor_status(
            organization_id="acme", maximum_age_seconds=1,
            now=unanchored.oldest_unanchored_at + timedelta(seconds=2),
        ).overdue)
        for _ in range(2):
            store.record_audit_chain_checkpoint(
                checkpoint=checkpoint, artifact_sha256="a" * 64, anchor_backend="synthetic-object-lock",
                object_version="synthetic-object-version", anchored_at=LedgerClock.current,
            )
        with case.assertRaisesRegex(RuntimeError, "audit_chain_checkpoint_conflict"):
            store.record_audit_chain_checkpoint(
                checkpoint=checkpoint, artifact_sha256="b" * 64, anchor_backend="synthetic-object-lock",
                object_version="synthetic-object-version", anchored_at=LedgerClock.current,
            )
        case.assertFalse(store.audit_chain_anchor_status(
            organization_id="acme", maximum_age_seconds=1, now=LedgerClock.current + timedelta(days=1),
        ).overdue)
        with case.assertRaisesRegex(RuntimeError, "audit_chain_epoch_reason_invalid"):
            store.begin_audit_chain_epoch(checkpoint=checkpoint, reason_code="not-allowed")
        restored = store.begin_audit_chain_epoch(checkpoint=checkpoint, reason_code="restore")
        case.assertEqual((restored.chain_epoch, restored.sequence, restored.head_digest), (2, 0, head.head_digest))
        store.record(
            identity=identity, client="codex", protocol="openai", requested_model="ledger-fast",
            resolved_alias="ledger-fast", upstream_model="provider-fast", policy_version="policy-v1",
            policy_action="allowed", status="rate_limited",
        )
        case.assertEqual(store.verify_audit_chain(organization_id="acme", checkpoint=checkpoint).chain_epoch, 2)

        result = {"acme": read_usage_repository(store), "beta": read_usage_repository(store, "beta")}
        totals = store.monthly_totals(organization_id="acme")
        case.assertEqual((totals.requests, totals.total_tokens, totals.cost_microusd), (3, 39, 500))
        case.assertEqual((totals.denied_requests, totals.rate_limited_requests, totals.redaction_count), (0, 1, 2))
        case.assertEqual(store.monthly_totals(organization_id="beta").denied_requests, 1)
        case.assertEqual(store.monthly_secret_totals(organization_id="acme").detections, 2)
        case.assertEqual(store.monthly_totals(organization_id="acme", actor_id="nobody").requests, 0)
        serialized = json.dumps(result, sort_keys=True, default=_exact_decimal_integer)
        case.assertNotIn(identity.token, serialized)
        for forbidden in ("prompt", "response_body", "matched_secret", "source_content"):
            case.assertNotIn('"' + forbidden + '"', serialized)
        return json.loads(serialized)
