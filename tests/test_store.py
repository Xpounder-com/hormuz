from __future__ import annotations

import sqlite3
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from hormuz.config import Identity
from hormuz.contracts import validate_audit_event
from hormuz.evidence import EvidenceStorageError
from hormuz.store import ReservationDenied, ReservationScope, StorageSchemaError, UsageStore


class UsageStoreMigrationTests(unittest.TestCase):
    def test_readiness_check_is_read_only_and_detects_a_tampered_migration_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "usage.sqlite3"
            store = UsageStore(path)
            store.verify_ready()

            connection = sqlite3.connect(path)
            before = connection.execute(
                "SELECT version, state FROM hormuz_schema_migrations ORDER BY version"
            ).fetchall()
            connection.close()
            store.verify_ready()
            connection = sqlite3.connect(path)
            after = connection.execute(
                "SELECT version, state FROM hormuz_schema_migrations ORDER BY version"
            ).fetchall()
            connection.execute("UPDATE hormuz_schema_migrations SET state = 'applying' WHERE version = 2")
            connection.commit()
            connection.close()
            self.assertEqual(after, before)

            with self.assertRaises(StorageSchemaError) as raised:
                store.verify_ready()
            self.assertEqual(raised.exception.code, "storage_schema_partial_upgrade")

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
            connection.execute(
                """
                INSERT INTO gateway_usage_events (
                    id, occurred_at, actor_id, actor_name, team_id, team_name,
                    client, protocol, requested_model, resolved_alias, upstream_model,
                    policy_action, status, input_tokens, output_tokens, provider_request_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "legacy-usage",
                    "2026-08-01T00:00:00+00:00",
                    "legacy-alice",
                    "Legacy Alice",
                    "engineering",
                    "Engineering",
                    "codex",
                    "openai",
                    "gpt-test",
                    "gpt-test",
                    "gpt-test",
                    "allowed",
                    "succeeded",
                    12,
                    3,
                    "legacy-provider-request",
                ),
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
                policy_action="allowed+redacted",
                status="succeeded",
                redaction_count=2,
                redaction_rules=("openai_api_key",),
            )

            self.assertEqual(store.monthly_totals().redaction_count, 2)
            summary = {row["actor_id"]: row for row in store.summary_rows()}
            self.assertEqual(summary["alice"]["redactions"], 2)
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
            self.assertEqual(
                [event["event_type"] for event in audit],
                ["usage", "usage", "security.secret"],
            )
            self.assertEqual([event["schema_version"] for event in audit], [2, 2, 2])
            self.assertEqual(audit[0]["schema_id"], "hormuz.audit-event")
            self.assertEqual(audit[0]["actor_id"], "legacy-alice")
            self.assertEqual(audit[0]["redaction_rules"], [])
            self.assertEqual(audit[1]["redaction_rules"], ["openai_api_key"])
            self.assertEqual(audit[2]["rules"], ["openai_api_key"])
            self.assertEqual(audit[0]["organization_id"], "organization")
            self.assertEqual(audit[0]["identity_type"], "human")
            self.assertEqual(audit[0]["cost_basis"], "configured_rate_card_estimate")
            self.assertIsNone(audit[0]["provider_reported_model"])
            validate_audit_event(audit[0])
            validate_audit_event(audit[1])
            validate_audit_event(audit[2])
            connection = sqlite3.connect(path)
            usage_schema = connection.execute(
                "SELECT evidence_schema_id, evidence_schema_version FROM gateway_usage_events"
            ).fetchone()
            secret_schema = connection.execute(
                "SELECT evidence_schema_id, evidence_schema_version FROM gateway_secret_events"
            ).fetchone()
            connection.close()
            self.assertEqual(usage_schema, ("hormuz.audit-event", 2))
            self.assertEqual(secret_schema, ("hormuz.audit-event", 2))
            self.assertNotIn("prompt", audit[0])
            self.assertNotIn("response", audit[0])
            self.assertNotIn("matched_value", audit[1])
            self.assertNotIn("must-not-export", repr(audit))
            self.assertEqual(
                [event["event_type"] for event in store.audit_events(
                    since="2000-01-01T00:00:00+00:00",
                    kind="usage",
                )],
                ["usage", "usage"],
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
            )

            organization = store.report_rows(group_by="organization")
            self.assertEqual(len(organization), 1)
            self.assertEqual(organization[0]["requests"], 3)
            self.assertEqual(organization[0]["succeeded"], 2)
            self.assertEqual(organization[0]["denied"], 1)
            self.assertEqual(organization[0]["total_tokens"], 180)
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

    def test_unsafe_rollback_fails_closed_without_mutating_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "usage.sqlite3"
            identity = Identity(
                token_env="TEST_TOKEN",
                token="employee-token-long",
                actor_id="alice",
                actor_name="Alice",
                team_id="engineering",
                team_name="Engineering",
                organization_id="acme",
            )
            store = UsageStore(path)
            store.record(
                identity=identity,
                client="codex",
                protocol="openai",
                requested_model="gpt-test",
                resolved_alias="gpt-test",
                upstream_model="gpt-test",
                policy_action="allowed",
                status="succeeded",
            )
            before = store.audit_events(since="2000-01-01T00:00:00+00:00")

            with self.assertRaises(StorageSchemaError) as raised:
                UsageStore(path, maximum_supported_schema_version=1)
            self.assertEqual(raised.exception.code, "storage_schema_newer_than_binary")

            after = UsageStore(path).audit_events(since="2000-01-01T00:00:00+00:00")
            self.assertEqual(after, before)

    def test_partial_upgrade_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "usage.sqlite3"
            connection = sqlite3.connect(path)
            connection.execute(
                "CREATE TABLE hormuz_schema_migrations (version INTEGER PRIMARY KEY, state TEXT NOT NULL, applied_at TEXT)"
            )
            connection.execute(
                "INSERT INTO hormuz_schema_migrations (version, state) VALUES (1, 'applying')"
            )
            connection.commit()
            connection.close()

            with self.assertRaises(StorageSchemaError) as raised:
                UsageStore(path)
            self.assertEqual(raised.exception.code, "storage_schema_partial_upgrade")

    def test_backup_restore_recovers_from_a_partial_upgrade_without_evidence_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "usage.sqlite3"
            backup = Path(temporary) / "usage-before-upgrade.sqlite3"
            identity = Identity(
                token_env="TEST_TOKEN",
                token="employee-token-long",
                actor_id="alice",
                actor_name="Alice",
                team_id="engineering",
                team_name="Engineering",
                organization_id="acme",
            )
            store = UsageStore(path)
            store.record(
                identity=identity,
                client="codex",
                protocol="openai",
                requested_model="gpt-test",
                resolved_alias="gpt-test",
                upstream_model="gpt-test",
                policy_action="allowed",
                status="succeeded",
            )
            expected = store.audit_events(since="2000-01-01T00:00:00+00:00")
            shutil.copy2(path, backup)

            connection = sqlite3.connect(path)
            connection.execute("UPDATE hormuz_schema_migrations SET state = 'applying' WHERE version = 2")
            connection.commit()
            connection.close()
            with self.assertRaises(StorageSchemaError):
                UsageStore(path)

            shutil.copy2(backup, path)
            recovered = UsageStore(path)
            self.assertEqual(
                recovered.audit_events(since="2000-01-01T00:00:00+00:00"),
                expected,
            )

    def test_tenant_scoped_usage_and_reservations_do_not_mix(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = UsageStore(Path(temporary) / "usage.sqlite3")
            acme = Identity(
                token_env="ACME_TOKEN",
                token="acme-employee-token",
                actor_id="alice",
                actor_name="Alice",
                team_id="engineering",
                team_name="Engineering",
                organization_id="acme",
            )
            beta = Identity(
                token_env="BETA_TOKEN",
                token="beta-employee-token",
                actor_id="alice",
                actor_name="Alice",
                team_id="engineering",
                team_name="Engineering",
                organization_id="beta",
            )
            for identity in (acme, beta):
                store.record(
                    identity=identity,
                    client="codex",
                    protocol="openai",
                    requested_model="gpt-test",
                    resolved_alias="gpt-test",
                    upstream_model="gpt-test",
                    policy_action="allowed",
                    status="succeeded",
                    input_tokens=100,
                    cost_microusd=400,
                )
            self.assertEqual(store.monthly_totals(organization_id="acme").requests, 1)
            self.assertEqual(store.monthly_totals(organization_id="beta").requests, 1)
            self.assertEqual(store.report_rows(group_by="organization", organization_id="acme")[0]["cost_microusd"], 400)

            scope = ReservationScope(name="organization", cost_limit_microusd=1_000)
            acme_reservation = store.reserve_budget(
                identity=acme,
                scopes=(scope,),
                reserved_tokens=1,
                reserved_cost_microusd=600,
                ttl_seconds=60,
            )
            beta_reservation = store.reserve_budget(
                identity=beta,
                scopes=(scope,),
                reserved_tokens=1,
                reserved_cost_microusd=600,
                ttl_seconds=60,
            )
            self.assertIsNotNone(acme_reservation)
            self.assertIsNotNone(beta_reservation)
            self.assertEqual(store.active_budget_reservations(organization_id="acme"), 1)
            self.assertEqual(store.active_budget_reservations(organization_id="beta"), 1)
            store.release_budget_reservation(acme_reservation, organization_id="beta")
            self.assertEqual(store.active_budget_reservations(organization_id="acme"), 1)

    def test_historical_v1_and_malformed_evidence_are_handled_without_content_leakage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "usage.sqlite3"
            store = UsageStore(path)
            identity = Identity(
                token_env="TEST_TOKEN",
                token="employee-token-long",
                actor_id="alice",
                actor_name="Alice",
                team_id="engineering",
                team_name="Engineering",
            )
            event_id = store.record(
                identity=identity,
                client="codex",
                protocol="openai",
                requested_model="gpt-test",
                resolved_alias="gpt-test",
                upstream_model="gpt-test",
                policy_action="allowed",
                status="succeeded",
            )
            connection = sqlite3.connect(path)
            connection.execute(
                "UPDATE gateway_usage_events SET evidence_schema_version = 1 WHERE id = ?",
                (event_id,),
            )
            connection.commit()
            connection.close()
            historical = store.audit_events(since="2000-01-01T00:00:00+00:00")
            self.assertEqual(historical[0]["schema_version"], 1)
            self.assertNotIn("schema_id", historical[0])
            validate_audit_event(historical[0])

            connection = sqlite3.connect(path)
            connection.execute(
                "UPDATE gateway_usage_events SET evidence_schema_version = 2, redaction_rules = ? WHERE id = ?",
                ('["must-not-leak", 1]', event_id),
            )
            connection.commit()
            connection.close()
            with self.assertRaises(EvidenceStorageError) as raised:
                store.audit_events(since="2000-01-01T00:00:00+00:00")
            self.assertEqual(raised.exception.code, "stored_evidence_malformed")
            self.assertNotIn("must-not-leak", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
