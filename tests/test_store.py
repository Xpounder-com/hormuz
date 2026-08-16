from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hormuz.config import Identity
from hormuz.dlp_approval import payload_fingerprint
from hormuz.store import (
    ContextLineage,
    DLPApprovalStoreError,
    ReservationDenied,
    ReservationScope,
    UsageStore,
)


class UsageStoreMigrationTests(unittest.TestCase):
    def test_usage_store_rejects_unbounded_provider_identifiers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = UsageStore(Path(temporary) / "usage.sqlite3")
            identity = Identity(
                token_env="ALICE_TOKEN",
                token="alice-employee-token",
                actor_id="alice",
                actor_name="Alice",
                team_id="engineering",
                team_name="Engineering",
                organization_id="org-a",
            )
            common = {
                "identity": identity,
                "client": "codex",
                "protocol": "openai",
                "requested_model": "gpt-fast",
                "resolved_alias": "gpt-fast",
                "upstream_model": "gpt-upstream",
                "policy_action": "allowed",
                "status": "succeeded",
                "cost_basis": "estimated",
            }

            for provider_request_id in (
                "req_safe\r\nX-Injected: yes",
                "request id with content",
                "req-🚀",
                "r" * 257,
            ):
                with self.subTest(provider_request_id=provider_request_id[:32]):
                    with self.assertRaisesRegex(ValueError, "provider request ID"):
                        store.record(
                            **common,
                            provider_request_id=provider_request_id,
                        )

            for actual_model in (
                "model with content",
                "unsafe-model-🚀",
                "m" * 257,
            ):
                with self.subTest(actual_model=actual_model[:32]):
                    with self.assertRaisesRegex(ValueError, "actual model"):
                        store.record(
                            **common,
                            actual_model=actual_model,
                        )

            store.record(**common, provider_request_id="req_safe-123_ABC")
            event = store.audit_events(
                since="2000-01-01T00:00:00+00:00",
                kind="usage",
            )[0]
            self.assertEqual(event["provider_request_id"], "req_safe-123_ABC")

    def test_usage_context_lineage_is_metadata_only_and_exported_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = UsageStore(Path(temporary) / "usage.sqlite3")
            identity = Identity(
                token_env="ALICE_TOKEN",
                token="alice-employee-token",
                actor_id="alice",
                actor_name="Alice",
                team_id="engineering",
                team_name="Engineering",
                organization_id="org-a",
            )
            store.record(
                identity=identity,
                client="codex",
                protocol="openai",
                requested_model="gpt-fast",
                resolved_alias="gpt-fast",
                upstream_model="gpt-upstream",
                policy_action="allowed+context-injected",
                status="succeeded",
                cost_basis="estimated",
                context_lineage=ContextLineage(
                    mode="optional",
                    outcome="injected",
                    reason="pack_injected",
                    pack_id="ctxpack_0123456789abcdef01234567",
                    record_ids=("record-a", "record-b"),
                    policy_version="context-v1",
                    retrieval_version="lexical-v1",
                    render_version="user-reference-json-v1",
                    repository_revision=None,
                    estimated_tokens=123,
                    assembly_milliseconds=4,
                    reuse_status="fresh",
                ),
            )

            event = store.audit_events(
                since="2000-01-01T00:00:00+00:00",
                kind="usage",
            )[0]

            self.assertEqual(event["schema_version"], 2)
            self.assertEqual(event["context_injection_mode"], "optional")
            self.assertEqual(event["context_injection_outcome"], "injected")
            self.assertEqual(event["context_pack_id"], "ctxpack_0123456789abcdef01234567")
            self.assertEqual(event["context_record_ids"], ["record-a", "record-b"])
            self.assertEqual(event["context_estimated_tokens"], 123)
            self.assertEqual(event["context_assembly_milliseconds"], 4)
            self.assertNotIn("query", event)
            self.assertNotIn("content", event)
            report = store.report_rows(group_by="person")[0]
            self.assertEqual(report["context_injected_requests"], 1)
            self.assertEqual(report["context_required_denials"], 0)
            self.assertEqual(report["context_estimated_tokens"], 123)
            self.assertEqual(report["context_packs_used"], 1)

    def test_dlp_approval_is_metadata_only_non_self_exact_and_single_use(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = UsageStore(Path(temporary) / "usage.sqlite3")
            alice = Identity(
                token_env="ALICE_TOKEN",
                token="alice-employee-token",
                actor_id="alice",
                actor_name="Alice",
                team_id="engineering",
                team_name="Engineering",
                organization_id="org-a",
                capabilities=("dlp_approver",),
            )
            bob = Identity(
                token_env="BOB_TOKEN",
                token="bob-approver-token",
                actor_id="bob",
                actor_name="Bob",
                team_id="security",
                team_name="Security",
                organization_id="org-a",
                capabilities=("dlp_approver",),
            )
            charlie = Identity(
                token_env="CHARLIE_TOKEN",
                token="charlie-employee-token",
                actor_id="charlie",
                actor_name="Charlie",
                team_id="security",
                team_name="Security",
                organization_id="org-a",
            )
            mallory = Identity(
                token_env="MALLORY_TOKEN",
                token="mallory-approver-token",
                actor_id="mallory",
                actor_name="Mallory",
                team_id="security",
                team_name="Security",
                organization_id="org-b",
                capabilities=("dlp_approver",),
            )
            protected = "PROJECT-TRIDENT-MUST-NOT-PERSIST"
            fingerprint = payload_fingerprint(
                {"model": "gpt-test", "input": protected},
                key=b"f" * 32,
            )
            base = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
            arguments = {
                "identity": alice,
                "client": "codex",
                "protocol": "openai",
                "requested_model": "engineering-fast",
                "routed_model": "gpt-test",
                "policy_version": "dlp-v1",
                "payload_fingerprint": fingerprint,
                "rules": ("company.codename",),
                "detection_count": 1,
                "ttl_seconds": 900,
            }

            first = store.authorize_or_request_dlp_approval(**arguments, now=base)
            duplicate = store.authorize_or_request_dlp_approval(
                **arguments,
                now=base + timedelta(seconds=1),
            )
            self.assertEqual(first.request_id, duplicate.request_id)
            self.assertFalse(first.authorized)

            with self.assertRaisesRegex(
                DLPApprovalStoreError,
                "approval_self_approval_forbidden",
            ):
                store.approve_dlp_approval_request(
                    first.request_id,
                    approver=alice,
                    ttl_seconds=900,
                    now=base + timedelta(seconds=2),
                )
            with self.assertRaisesRegex(
                DLPApprovalStoreError,
                "approval_capability_required",
            ):
                store.approve_dlp_approval_request(
                    first.request_id,
                    approver=charlie,
                    ttl_seconds=900,
                    now=base + timedelta(seconds=2),
                )
            with self.assertRaisesRegex(
                DLPApprovalStoreError,
                "approval_request_not_found",
            ):
                store.approve_dlp_approval_request(
                    first.request_id,
                    approver=mallory,
                    ttl_seconds=900,
                    now=base + timedelta(seconds=2),
                )

            approved = store.approve_dlp_approval_request(
                first.request_id,
                approver=bob,
                ttl_seconds=900,
                now=base + timedelta(seconds=2),
            )
            idempotent = store.approve_dlp_approval_request(
                first.request_id,
                approver=bob,
                ttl_seconds=900,
                now=base + timedelta(seconds=3),
            )
            self.assertEqual(approved, idempotent)
            self.assertEqual(approved.status, "approved")

            mutated = store.authorize_or_request_dlp_approval(
                **{
                    **arguments,
                    "payload_fingerprint": payload_fingerprint(
                        {"model": "gpt-test", "input": protected + " changed"},
                        key=b"f" * 32,
                    ),
                },
                now=base + timedelta(seconds=4),
            )
            self.assertFalse(mutated.authorized)
            self.assertNotEqual(mutated.request_id, first.request_id)

            consumed = store.authorize_or_request_dlp_approval(
                **arguments,
                now=base + timedelta(seconds=5),
            )
            replay = store.authorize_or_request_dlp_approval(
                **arguments,
                now=base + timedelta(seconds=6),
            )
            self.assertTrue(consumed.authorized)
            self.assertEqual(consumed.request_id, first.request_id)
            self.assertFalse(replay.authorized)
            self.assertNotEqual(replay.request_id, first.request_id)

            events = store.audit_events(
                since="2000-01-01T00:00:00+00:00",
                kind="security",
            )
            approval_events = [
                event for event in events if event["event_type"] == "security.dlp.approval"
            ]
            self.assertEqual(
                [event["action"] for event in approval_events],
                ["requested", "approved", "requested", "consumed", "requested"],
            )
            serialized = repr(events)
            self.assertNotIn(protected, serialized)
            self.assertNotIn(protected.encode("utf-8"), store.path.read_bytes())

    def test_dlp_approval_expiry_and_concurrent_retry_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = UsageStore(Path(temporary) / "usage.sqlite3")
            alice = Identity(
                token_env="ALICE_TOKEN",
                token="alice-employee-token",
                actor_id="alice",
                actor_name="Alice",
                team_id="engineering",
                team_name="Engineering",
                organization_id="org-a",
            )
            bob = Identity(
                token_env="BOB_TOKEN",
                token="bob-approver-token",
                actor_id="bob",
                actor_name="Bob",
                team_id="security",
                team_name="Security",
                organization_id="org-a",
                capabilities=("dlp_approver",),
            )
            base = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
            arguments = {
                "identity": alice,
                "client": "codex",
                "protocol": "openai",
                "requested_model": "engineering-fast",
                "routed_model": "gpt-test",
                "policy_version": "dlp-v1",
                "payload_fingerprint": payload_fingerprint(
                    {"model": "gpt-test", "input": "protected"},
                    key=b"f" * 32,
                ),
                "rules": ("company.codename",),
                "detection_count": 1,
                "ttl_seconds": 900,
            }
            pending = store.authorize_or_request_dlp_approval(**arguments, now=base)
            store.approve_dlp_approval_request(
                pending.request_id,
                approver=bob,
                ttl_seconds=900,
                now=base,
            )

            barrier = threading.Barrier(2)
            results = []

            def consume() -> None:
                barrier.wait()
                results.append(
                    store.authorize_or_request_dlp_approval(
                        **arguments,
                        now=base + timedelta(seconds=1),
                    )
                )

            threads = [threading.Thread(target=consume) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(sum(result.authorized for result in results), 1)
            self.assertEqual(sum(not result.authorized for result in results), 1)

            pending_after_replay = next(result for result in results if not result.authorized)
            store.approve_dlp_approval_request(
                pending_after_replay.request_id,
                approver=bob,
                ttl_seconds=900,
                now=base + timedelta(seconds=2),
            )
            after_expiry = store.authorize_or_request_dlp_approval(
                **arguments,
                now=base + timedelta(seconds=903),
            )
            self.assertFalse(after_expiry.authorized)
            self.assertNotEqual(after_expiry.request_id, pending_after_replay.request_id)
            expired = store.get_dlp_approval_request(
                pending_after_replay.request_id,
                organization_id="org-a",
                now=base + timedelta(seconds=903),
            )
            self.assertEqual(expired.status, "expired")

    def test_existing_usage_rows_receive_conservative_accounting_backfills(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "usage.sqlite3"
            connection = sqlite3.connect(path)
            connection.execute(
                """
                CREATE TABLE gateway_usage_events (
                    id TEXT PRIMARY KEY,
                    occurred_at TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    actor_name TEXT NOT NULL,
                    team_id TEXT NOT NULL,
                    team_name TEXT NOT NULL,
                    client TEXT NOT NULL,
                    protocol TEXT NOT NULL,
                    requested_model TEXT NOT NULL,
                    resolved_alias TEXT,
                    upstream_model TEXT,
                    policy_action TEXT NOT NULL,
                    status TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
                    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
                    reasoning_tokens INTEGER NOT NULL DEFAULT 0,
                    cost_microusd INTEGER NOT NULL DEFAULT 0,
                    provider_request_id TEXT
                )
                """
            )
            connection.execute(
                """
                INSERT INTO gateway_usage_events (
                    id, occurred_at, actor_id, actor_name, team_id, team_name,
                    client, protocol, requested_model, policy_action, status,
                    input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
                    cost_microusd
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "legacy-anthropic",
                    "2026-08-15T00:00:00+00:00",
                    "alice",
                    "Alice",
                    "engineering",
                    "Engineering",
                    "claude-code",
                    "anthropic",
                    "claude-test",
                    "allowed",
                    "succeeded",
                    80,
                    12,
                    20,
                    10,
                    1234,
                ),
            )
            connection.commit()
            connection.close()

            UsageStore(path)

            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                """
                SELECT actual_model, billable_tokens, cost_basis, currency, rate_card_version,
                    provider_usage_json
                FROM gateway_usage_events
                WHERE id = 'legacy-anthropic'
                """
            ).fetchone()
            tables = {
                item[0]
                for item in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            connection.close()
            self.assertEqual(dict(row), {
                "actual_model": None,
                "billable_tokens": 122,
                "cost_basis": "estimated_legacy",
                "currency": "USD",
                "rate_card_version": "unversioned",
                "provider_usage_json": "{}",
            })
            self.assertIn("gateway_dlp_approval_requests", tables)
            self.assertIn("gateway_dlp_approval_events", tables)

    def test_existing_usage_database_gains_redaction_metadata_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "usage.sqlite3"
            connection = sqlite3.connect(path)
            connection.execute(
                """
                CREATE TABLE gateway_usage_events (
                    id TEXT PRIMARY KEY,
                    occurred_at TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    actor_name TEXT NOT NULL,
                    team_id TEXT NOT NULL,
                    team_name TEXT NOT NULL,
                    client TEXT NOT NULL,
                    protocol TEXT NOT NULL,
                    requested_model TEXT NOT NULL,
                    resolved_alias TEXT,
                    upstream_model TEXT,
                    policy_action TEXT NOT NULL,
                    status TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
                    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
                    reasoning_tokens INTEGER NOT NULL DEFAULT 0,
                    cost_microusd INTEGER NOT NULL DEFAULT 0,
                    provider_request_id TEXT
                )
                """
            )
            connection.commit()
            connection.close()

            store = UsageStore(path)
            identity = Identity(
                token_env="TEST_TOKEN",
                token="employee-token",
                actor_id="alice",
                actor_name="Alice",
                team_id="engineering",
                team_name="Engineering",
            )
            store.record(
                identity=identity,
                client="codex",
                protocol="openai",
                requested_model="gpt-test",
                resolved_alias="gpt-test",
                upstream_model="gpt-test",
                actual_model="gpt-provider-versioned",
                policy_action="allowed+redacted",
                status="succeeded",
                billable_tokens=120,
                cost_microusd=1_000,
                cost_basis="estimated",
                rate_card_version="rates-v1",
                provider_usage={
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "request_body": "must-not-export",
                },
                redaction_count=2,
                redaction_rules=("openai_api_key",),
            )

            self.assertEqual(store.monthly_totals().redaction_count, 2)
            self.assertEqual(store.summary_rows()[0]["redactions"], 2)
            store.record_secret_event(
                identity=identity,
                client="codex",
                protocol="openai",
                requested_model="gpt-test",
                action="redacted",
                detection_count=2,
                rules=("openai_api_key",),
            )
            secret_totals = store.monthly_secret_totals()
            self.assertEqual(secret_totals.events, 1)
            self.assertEqual(secret_totals.detections, 2)

            # The export contract is an allowlist: even a future content-bearing
            # database column must not appear automatically in audit output.
            connection = sqlite3.connect(path)
            connection.execute("ALTER TABLE gateway_usage_events ADD COLUMN prompt TEXT")
            connection.execute("UPDATE gateway_usage_events SET prompt = 'must-not-export'")
            connection.execute("ALTER TABLE gateway_secret_events ADD COLUMN matched_value TEXT")
            connection.execute("UPDATE gateway_secret_events SET matched_value = 'must-not-export'")
            connection.commit()
            connection.close()

            audit = store.audit_events(since="2000-01-01T00:00:00+00:00")
            self.assertEqual([event["event_type"] for event in audit], ["usage", "security.secret"])
            self.assertEqual(audit[0]["redaction_rules"], ["openai_api_key"])
            self.assertEqual(audit[0]["billable_tokens"], 120)
            self.assertEqual(audit[0]["cost_basis"], "estimated")
            self.assertEqual(audit[0]["currency"], "USD")
            self.assertEqual(audit[0]["rate_card_version"], "rates-v1")
            self.assertEqual(audit[0]["actual_model"], "gpt-provider-versioned")
            self.assertEqual(
                audit[0]["provider_usage"],
                {"input_tokens": 100, "output_tokens": 20},
            )
            self.assertEqual(audit[0]["schema_version"], 2)
            self.assertEqual(audit[0]["context_injection_mode"], "off")
            self.assertEqual(audit[0]["context_injection_outcome"], "not_evaluated")
            self.assertEqual(audit[0]["context_record_ids"], [])
            self.assertEqual(audit[1]["rules"], ["openai_api_key"])
            self.assertNotIn("prompt", audit[0])
            self.assertNotIn("response", audit[0])
            self.assertNotIn("matched_value", audit[1])
            self.assertNotIn("must-not-export", repr(audit))
            self.assertEqual(
                [event["event_type"] for event in store.audit_events(
                    since="2000-01-01T00:00:00+00:00",
                    kind="usage",
                )],
                ["usage"],
            )
            self.assertEqual(
                [event["event_type"] for event in store.audit_events(
                    since="2000-01-01T00:00:00+00:00",
                    kind="security",
                )],
                ["security.secret"],
            )
            self.assertEqual(
                store.audit_events(since="2999-01-01T00:00:00+00:00"),
                [],
            )
            with self.assertRaises(ValueError):
                store.audit_events(since="2000-01-01T00:00:00+00:00", kind="unsupported")

    def test_existing_secret_events_gain_metadata_only_dlp_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "usage.sqlite3"
            connection = sqlite3.connect(path)
            connection.execute(
                """
                CREATE TABLE gateway_secret_events (
                    id TEXT PRIMARY KEY,
                    occurred_at TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    actor_name TEXT NOT NULL,
                    team_id TEXT NOT NULL,
                    team_name TEXT NOT NULL,
                    client TEXT NOT NULL,
                    protocol TEXT NOT NULL,
                    requested_model TEXT NOT NULL,
                    action TEXT NOT NULL,
                    detection_count INTEGER NOT NULL,
                    rules TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT INTO gateway_secret_events (
                    id, occurred_at, actor_id, actor_name, team_id, team_name,
                    client, protocol, requested_model, action, detection_count, rules
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "legacy-secret",
                    "2026-08-15T00:00:00+00:00",
                    "alice",
                    "Alice",
                    "engineering",
                    "Engineering",
                    "codex",
                    "openai",
                    "gpt-test",
                    "redacted",
                    1,
                    '["openai_api_key"]',
                ),
            )
            connection.commit()
            connection.close()

            store = UsageStore(path)
            event = store.audit_events(
                since="2000-01-01T00:00:00+00:00",
                kind="security",
            )[0]

            self.assertEqual(event["event_type"], "security.secret")
            self.assertEqual(event["policy_version"], "legacy-secret-v1")
            self.assertEqual(event["findings"], [])
            self.assertEqual(event["redaction_count"], 0)
            self.assertIsNone(event["routed_model"])

    def test_dlp_events_store_only_normalized_findings_and_support_new_totals(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "usage.sqlite3"
            store = UsageStore(path)
            identity = Identity(
                token_env="TEST_TOKEN",
                token="employee-token",
                actor_id="alice",
                actor_name="Alice",
                team_id="engineering",
                team_name="Engineering",
            )
            store.record_dlp_event(
                identity=identity,
                client="codex",
                protocol="openai",
                requested_model="engineering-fast",
                routed_model="gpt-test-fast",
                action="detected",
                redaction_count=0,
                policy_version="company-dlp-v2",
                findings=(
                    {
                        "rule_id": "email_address",
                        "category": "pii",
                        "confidence": "low",
                        "action": "detect",
                        "count": 2,
                    },
                ),
            )
            store.record_dlp_event(
                identity=identity,
                client="codex",
                protocol="openai",
                requested_model="engineering-fast",
                routed_model="gpt-test-fast",
                action="approval_required",
                redaction_count=0,
                policy_version="company-dlp-v2",
                findings=(
                    {
                        "rule_id": "company.codename",
                        "category": "company_dictionary",
                        "confidence": "high",
                        "action": "require_approval",
                        "count": 1,
                    },
                ),
            )

            totals = store.monthly_secret_totals(actor_id="alice")
            events = store.audit_events(
                since="2000-01-01T00:00:00+00:00",
                kind="security",
            )

            self.assertEqual(totals.events, 2)
            self.assertEqual(totals.detections, 3)
            self.assertEqual(totals.detected_requests, 1)
            self.assertEqual(totals.approval_required_requests, 1)
            self.assertEqual({event["event_type"] for event in events}, {"security.dlp"})
            self.assertEqual(events[0]["routed_model"], "gpt-test-fast")
            self.assertEqual(events[0]["policy_version"], "company-dlp-v2")
            self.assertEqual(events[0]["findings"][0]["rule_id"], "email_address")
            serialized = repr(events)
            self.assertNotIn("matched_value", serialized)
            self.assertNotIn("prompt", serialized)

            with self.assertRaisesRegex(ValueError, "metadata-only finding schema"):
                store.record_dlp_event(
                    identity=identity,
                    client="codex",
                    protocol="openai",
                    requested_model="engineering-fast",
                    routed_model="gpt-test-fast",
                    action="denied",
                    redaction_count=0,
                    policy_version="company-dlp-v2",
                    findings=(
                        {
                            "rule_id": "company.codename",
                            "category": "company_dictionary",
                            "confidence": "high",
                            "action": "deny",
                            "count": 1,
                            "matched_value": "must-never-persist",
                        },
                    ),
                )

    def test_usage_reports_group_and_filter_without_content_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = UsageStore(Path(temporary) / "usage.sqlite3")
            alice = Identity(
                token_env="ALICE_TOKEN",
                token="alice-employee-token",
                actor_id="alice",
                actor_name="Alice",
                team_id="engineering",
                team_name="Engineering",
            )
            bob = Identity(
                token_env="BOB_TOKEN",
                token="bob-employee-token-long",
                actor_id="bob",
                actor_name="Bob",
                team_id="marketing",
                team_name="Marketing",
            )
            store.record(
                identity=alice,
                client="codex",
                protocol="openai",
                requested_model="gpt-fast",
                resolved_alias="gpt-fast",
                upstream_model="gpt-upstream",
                policy_action="allowed+redacted",
                status="succeeded",
                input_tokens=100,
                output_tokens=20,
                cache_read_tokens=10,
                reasoning_tokens=5,
                cost_microusd=1_000,
                cost_basis="estimated",
                rate_card_version="rates-v1",
                redaction_count=1,
                redaction_rules=("openai_api_key",),
            )
            store.record(
                identity=bob,
                client="claude-code",
                protocol="anthropic",
                requested_model="claude-fast",
                resolved_alias="claude-fast",
                upstream_model="claude-upstream",
                policy_action="allowed",
                status="succeeded",
                input_tokens=50,
                output_tokens=10,
                cache_write_tokens=5,
                cost_microusd=2_000,
                cost_basis="estimated",
                rate_card_version="rates-v2",
            )
            store.record(
                identity=bob,
                client="claude-code",
                protocol="anthropic",
                requested_model="claude-blocked",
                resolved_alias=None,
                upstream_model=None,
                policy_action="denied",
                status="denied",
                cost_basis="not_applicable",
            )

            organization = store.report_rows(group_by="organization")
            self.assertEqual(len(organization), 1)
            self.assertEqual(organization[0]["requests"], 3)
            self.assertEqual(organization[0]["succeeded"], 2)
            self.assertEqual(organization[0]["denied"], 1)
            self.assertEqual(organization[0]["total_tokens"], 180)
            self.assertEqual(organization[0]["billable_tokens"], 185)
            self.assertEqual(organization[0]["estimated_cost_microusd"], 3_000)
            self.assertEqual(organization[0]["unpriced_requests"], 0)
            self.assertEqual(
                organization[0]["cost_bases"],
                ["estimated", "not_applicable"],
            )
            self.assertEqual(organization[0]["currencies"], ["USD"])
            self.assertEqual(
                organization[0]["rate_card_versions"],
                ["rates-v1", "rates-v2", "unversioned"],
            )
            self.assertEqual(organization[0]["active_actors"], 2)

            teams = {row["scope_id"]: row for row in store.report_rows(group_by="team")}
            self.assertEqual(teams["engineering"]["input_tokens"], 100)
            self.assertEqual(teams["marketing"]["requests"], 2)

            people = {row["scope_id"]: row for row in store.report_rows(group_by="person")}
            self.assertEqual(people["alice"]["cache_read_tokens"], 10)
            self.assertEqual(people["bob"]["cache_write_tokens"], 5)

            models = {row["scope_id"]: row for row in store.report_rows(group_by="model")}
            self.assertEqual(models["gpt-upstream"]["protocol"], "openai")
            self.assertEqual(models["claude-blocked"]["denied"], 1)

            alice_only = store.report_rows(group_by="organization", actor_id="alice")
            self.assertEqual(alice_only[0]["requests"], 1)
            self.assertEqual(alice_only[0]["cost_microusd"], 1_000)

            with self.assertRaises(ValueError):
                store.report_rows(group_by="unsupported")

    def test_rate_card_snapshots_keep_historical_estimates_stable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = UsageStore(Path(temporary) / "usage.sqlite3")
            identity = Identity(
                token_env="TEST_TOKEN",
                token="employee-token-long",
                actor_id="alice",
                actor_name="Alice",
                team_id="engineering",
                team_name="Engineering",
            )
            for version, cost in (("rates-v1", 1_000), ("rates-v2", 2_000)):
                store.record(
                    identity=identity,
                    client="codex",
                    protocol="openai",
                    requested_model="gpt-fast",
                    resolved_alias="gpt-fast",
                    upstream_model="gpt-upstream",
                    policy_action="allowed",
                    status="succeeded",
                    input_tokens=100,
                    output_tokens=20,
                    billable_tokens=120,
                    cost_microusd=cost,
                    cost_basis="estimated",
                    rate_card_version=version,
                )

            report = store.report_rows(group_by="organization")[0]
            self.assertEqual(report["cost_microusd"], 3_000)
            self.assertEqual(report["rate_card_versions"], ["rates-v1", "rates-v2"])
            audit = store.audit_events(since="2000-01-01T00:00:00+00:00", kind="usage")
            self.assertEqual(
                {(event["rate_card_version"], event["cost_microusd"]) for event in audit},
                {("rates-v1", 1_000), ("rates-v2", 2_000)},
            )

    def test_usage_security_and_reservations_are_isolated_by_organization(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = UsageStore(Path(temporary) / "usage.sqlite3")
            first = Identity(
                token_env="FIRST_TOKEN",
                token="first-token-long",
                actor_id="shared-actor",
                actor_name="First Actor",
                team_id="engineering",
                team_name="Engineering",
                organization_id="first-company",
            )
            second = Identity(
                token_env="SECOND_TOKEN",
                token="second-token-long",
                actor_id="shared-actor",
                actor_name="Second Actor",
                team_id="engineering",
                team_name="Engineering",
                organization_id="second-company",
            )
            for identity, cost in ((first, 1_000), (second, 9_000)):
                store.record(
                    identity=identity,
                    client="codex",
                    protocol="openai",
                    requested_model="gpt-test",
                    resolved_alias="gpt-test",
                    upstream_model="gpt-test",
                    policy_action="allowed",
                    status="succeeded",
                    input_tokens=10,
                    output_tokens=2,
                    billable_tokens=12,
                    cost_microusd=cost,
                    cost_basis="estimated",
                )
                store.record_secret_event(
                    identity=identity,
                    client="codex",
                    protocol="openai",
                    requested_model="gpt-test",
                    action="denied",
                    detection_count=1,
                    rules=("openai_api_key",),
                )

            first_totals = store.monthly_totals(
                organization_id="first-company",
                actor_id="shared-actor",
            )
            self.assertEqual(first_totals.requests, 1)
            self.assertEqual(first_totals.cost_microusd, 1_000)
            self.assertEqual(
                store.monthly_secret_totals(
                    organization_id="first-company",
                    actor_id="shared-actor",
                ).events,
                1,
            )
            report = store.report_rows(
                group_by="organization",
                organization_id="first-company",
            )
            self.assertEqual(len(report), 1)
            self.assertEqual(report[0]["scope_id"], "first-company")
            self.assertEqual(report[0]["cost_microusd"], 1_000)

            budget_store = UsageStore(Path(temporary) / "budget.sqlite3")
            scope = ReservationScope(name="organization", cost_limit_microusd=1_000)
            first_reservation = budget_store.reserve_budget(
                identity=first,
                scopes=(scope,),
                reserved_tokens=0,
                reserved_cost_microusd=600,
                ttl_seconds=60,
            )
            second_reservation = budget_store.reserve_budget(
                identity=second,
                scopes=(scope,),
                reserved_tokens=0,
                reserved_cost_microusd=600,
                ttl_seconds=60,
            )
            self.assertIsNotNone(first_reservation)
            self.assertIsNotNone(second_reservation)

    def test_atomic_budget_reservation_allows_only_one_competing_request(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "usage.sqlite3"
            stores = (UsageStore(path), UsageStore(path))
            identity = Identity(
                token_env="TEST_TOKEN",
                token="employee-token-long",
                actor_id="alice",
                actor_name="Alice",
                team_id="engineering",
                team_name="Engineering",
            )
            scope = ReservationScope(name="organization", cost_limit_microusd=1_000)
            barrier = threading.Barrier(2)
            outcomes: list[tuple[str, str | None]] = []
            outcome_lock = threading.Lock()

            def reserve(store: UsageStore) -> None:
                barrier.wait()
                try:
                    reservation_id = store.reserve_budget(
                        identity=identity,
                        scopes=(scope,),
                        reserved_tokens=100,
                        reserved_cost_microusd=600,
                        ttl_seconds=60,
                    )
                    outcome = ("allowed", reservation_id)
                except ReservationDenied:
                    outcome = ("denied", None)
                with outcome_lock:
                    outcomes.append(outcome)

            threads = [threading.Thread(target=reserve, args=(store,)) for store in stores]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)

            self.assertEqual(sorted(outcome for outcome, _ in outcomes), ["allowed", "denied"])
            self.assertEqual(stores[0].active_budget_reservations(), 1)
            allowed_id = next(reservation_id for outcome, reservation_id in outcomes if outcome == "allowed")
            connection = sqlite3.connect(path)
            connection.execute(
                "UPDATE gateway_budget_reservations SET expires_at = ? WHERE id = ?",
                ("2000-01-01T00:00:00+00:00", allowed_id),
            )
            connection.commit()
            connection.close()
            self.assertEqual(stores[0].active_budget_reservations(), 0)
            stores[0].refresh_budget_reservation(allowed_id, ttl_seconds=60)
            self.assertEqual(stores[0].active_budget_reservations(), 1)
            stores[0].release_budget_reservation(allowed_id)
            self.assertEqual(stores[1].active_budget_reservations(), 0)


if __name__ == "__main__":
    unittest.main()
