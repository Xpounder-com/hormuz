from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from hormuz.config import Identity
from hormuz.store import ReservationDenied, ReservationScope, UsageStore


class UsageStoreMigrationTests(unittest.TestCase):
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
                policy_action="allowed+redacted",
                status="succeeded",
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


if __name__ == "__main__":
    unittest.main()
