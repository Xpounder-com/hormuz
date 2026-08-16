from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from hormuz.config import Identity
from hormuz.store import ReservationDenied, ReservationScope, UsageStore


class UsageStoreMigrationTests(unittest.TestCase):
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
            connection.close()
            self.assertEqual(dict(row), {
                "actual_model": None,
                "billable_tokens": 122,
                "cost_basis": "estimated_legacy",
                "currency": "USD",
                "rate_card_version": "unversioned",
                "provider_usage_json": "{}",
            })

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
