from __future__ import annotations

import unittest
from uuid import uuid4

from hormuz.postgres import PostgresStorageError, postgres_transaction
from hormuz.store import RequestAttemptStateError, ReservationDenied, ReservationScope
if __package__:
    from ._postgres_fixture import PostgresTestCase, _identity
else:  # Isolated wheel compatibility discovery uses the tests directory as its import root.
    from _postgres_fixture import PostgresTestCase, _identity


class PostgresRequestAttemptTests(PostgresTestCase):
    def test_attempt_ledger_is_append_only_tenant_scoped_and_conservative(self) -> None:
        identity = _identity("acme")
        scope = ReservationScope(name="organization", cost_limit_microusd=1_000)
        attempt = self.store.begin_request_attempt(
            identity=identity,
            client="codex",
            protocol="openai",
            requested_model="gpt-test",
            resolved_alias="gpt-test",
            upstream_model="gpt-upstream",
            policy_version="policy-v1",
            policy_action="allowed",
            redaction_count=0,
            redaction_rules=(),
            scopes=(scope,),
            reserved_tokens=20,
            reserved_cost_microusd=600,
            ttl_seconds=60,
        )

        with postgres_transaction(
            self.runtime_dsn,
            schema=self.schema,
            runtime_role=self.runtime_role,
            organization_id="acme",
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT attempt_id, reserved_cost_microusd FROM gateway_request_attempts WHERE attempt_id = %s",
                    (attempt.attempt_id,),
                )
                self.assertEqual(dict(cursor.fetchone()), {"attempt_id": attempt.attempt_id, "reserved_cost_microusd": 600})
                cursor.execute(
                    "SELECT sequence, state FROM gateway_request_attempt_events WHERE attempt_id = %s",
                    (attempt.attempt_id,),
                )
                self.assertEqual([dict(row) for row in cursor.fetchall()], [{"sequence": 1, "state": "pending"}])

        with postgres_transaction(
            self.runtime_dsn,
            schema=self.schema,
            runtime_role=self.runtime_role,
            organization_id="beta",
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) AS count FROM gateway_request_attempts")
                self.assertEqual(cursor.fetchone()["count"], 0)

        with self.assertRaises(PostgresStorageError) as raised:
            with postgres_transaction(
                self.runtime_dsn,
                schema=self.schema,
                runtime_role=self.runtime_role,
                organization_id="acme",
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE gateway_request_attempts SET policy_version = 'rewrite-attempt' WHERE attempt_id = %s",
                        (attempt.attempt_id,),
                    )
        self.assertEqual(raised.exception.code, "storage_access_denied")

        beta_usage_event_id = self.store.record(
            identity=_identity("beta"),
            client="codex",
            protocol="openai",
            requested_model="gpt-test",
            resolved_alias="gpt-test",
            upstream_model="gpt-upstream",
            policy_action="allowed",
            status="succeeded",
        )
        with self.assertRaises(PostgresStorageError):
            with postgres_transaction(
                self.runtime_dsn,
                schema=self.schema,
                runtime_role=self.runtime_role,
                organization_id="acme",
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO gateway_request_attempt_events (
                            id, attempt_id, organization_id, occurred_at,
                            event_schema_id, event_schema_version, sequence, state,
                            reason_code, usage_event_id
                        ) VALUES (%s, %s, %s, now(), %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            str(uuid4()),
                            attempt.attempt_id,
                            "acme",
                            "hormuz.request-attempt-event",
                            1,
                            2,
                            "succeeded",
                            None,
                            beta_usage_event_id,
                        ),
                    )

        with self.psycopg.connect(self.owner_dsn) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute("SELECT set_config('hormuz.organization_id', %s, true)", ("acme",))
                    cursor.execute(
                        self.sql.SQL(
                            "UPDATE {}.gateway_budget_reservations SET expires_at = %s WHERE id = %s"
                        ).format(self.sql.Identifier(self.schema)),
                        ("2000-01-01T00:00:00+00:00", attempt.reservation_id),
                    )

        self.assertEqual(self.store.sweep_stale_request_attempts(organization_id="acme"), 1)
        self.assertEqual(self.store.active_budget_reservations(organization_id="acme"), 1)
        with self.assertRaises(ReservationDenied):
            self.store.reserve_budget(
                identity=identity,
                scopes=(scope,),
                reserved_tokens=1,
                reserved_cost_microusd=500,
                ttl_seconds=60,
            )
        with self.assertRaises(RequestAttemptStateError):
            self.store.finalize_request_attempt(
                attempt=attempt,
                organization_id="acme",
                status="failed",
            )

        with postgres_transaction(
            self.runtime_dsn,
            schema=self.schema,
            runtime_role=self.runtime_role,
            organization_id="acme",
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT sequence, state, reason_code, usage_event_id "
                    "FROM gateway_request_attempt_events WHERE attempt_id = %s ORDER BY sequence",
                    (attempt.attempt_id,),
                )
                self.assertEqual(
                    [dict(row) for row in cursor.fetchall()],
                    [
                        {"sequence": 1, "state": "pending", "reason_code": None, "usage_event_id": None},
                        {
                            "sequence": 2,
                            "state": "outcome_unknown",
                            "reason_code": "stale_pending",
                            "usage_event_id": None,
                        },
                    ],
                )


if __name__ == "__main__":
    unittest.main()
