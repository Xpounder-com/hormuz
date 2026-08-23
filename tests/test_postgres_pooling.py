from __future__ import annotations

import threading
import unittest

from hormuz.postgres import PostgresStorageError, postgres_transaction
from hormuz.postgres_usage_store import PostgresUsageStore
if __package__:
    from ._postgres_fixture import PostgresTestCase, _identity
else:  # Isolated wheel compatibility discovery uses the tests directory as its import root.
    from _postgres_fixture import PostgresTestCase, _identity


class PostgresPoolingTests(PostgresTestCase):
    def test_runtime_pool_reuses_connections_without_tenant_state_leakage(self) -> None:
        pool = self._runtime_pool(
            min_connections=1,
            max_connections=1,
            acquire_timeout_seconds=2,
            max_waiting=2,
            max_lifetime_seconds=1800,
            max_idle_seconds=120,
        )
        store = PostgresUsageStore(
            self.runtime_dsn,
            organization_ids=("acme", "beta"),
            schema=self.schema,
            runtime_role=self.runtime_role,
            connection_pool=pool,
        )
        for organization_id in ("acme", "beta"):
            store.record(
                identity=_identity(organization_id),
                client="codex",
                protocol="openai",
                requested_model="gpt-test",
                resolved_alias="gpt-test",
                upstream_model="gpt-upstream",
                policy_action="allowed",
                status="succeeded",
            )
        store.verify_ready()

        observations: list[tuple[int, str, int]] = []
        for organization_id in ("acme", "beta"):
            with postgres_transaction(
                self.runtime_dsn,
                schema=self.schema,
                runtime_role=self.runtime_role,
                organization_id=organization_id,
                connection_pool=pool,
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT pg_backend_pid() AS backend_pid,
                               current_setting('hormuz.organization_id', true) AS organization_id
                        """
                    )
                    row = cursor.fetchone()
                    cursor.execute("SELECT COUNT(*) AS event_count FROM gateway_usage_events")
                    count = cursor.fetchone()
            assert row is not None and count is not None
            observations.append((int(row["backend_pid"]), str(row["organization_id"]), int(count["event_count"])))

        self.assertEqual(observations[0][0], observations[1][0])
        self.assertEqual(observations, [(observations[0][0], "acme", 1), (observations[0][0], "beta", 1)])

        with pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT current_setting('hormuz.organization_id', true) AS organization_id,
                           current_setting('search_path') AS search_path
                    """
                )
                reset = cursor.fetchone()
        assert reset is not None
        self.assertIn(reset["organization_id"], (None, ""))
        self.assertNotIn(self.schema, str(reset["search_path"]))
    def test_runtime_pool_saturation_fails_closed_before_tenant_query(self) -> None:
        pool = self._runtime_pool(
            min_connections=1,
            max_connections=1,
            acquire_timeout_seconds=1,
            max_waiting=1,
            max_lifetime_seconds=1800,
            max_idle_seconds=120,
        )
        outcomes: list[str] = []
        finished = threading.Event()

        def acquire_second_connection() -> None:
            try:
                with postgres_transaction(
                    self.runtime_dsn,
                    schema=self.schema,
                    runtime_role=self.runtime_role,
                    organization_id="acme",
                    connection_pool=pool,
                ):
                    outcomes.append("unexpected_connection")
            except PostgresStorageError as error:
                outcomes.append(error.code)
            finally:
                finished.set()

        with pool.connection():
            worker = threading.Thread(target=acquire_second_connection)
            worker.start()
            self.assertTrue(finished.wait(timeout=5))
            worker.join(timeout=5)
        self.assertEqual(outcomes, ["storage_pool_exhausted"])
    def test_runtime_pool_replaces_a_terminated_idle_connection(self) -> None:
        pool = self._runtime_pool(
            min_connections=1,
            max_connections=1,
            acquire_timeout_seconds=3,
            max_waiting=2,
            max_lifetime_seconds=1800,
            max_idle_seconds=120,
        )
        with postgres_transaction(
            self.runtime_dsn,
            schema=self.schema,
            runtime_role=self.runtime_role,
            organization_id="acme",
            connection_pool=pool,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid() AS backend_pid")
                first = cursor.fetchone()
        assert first is not None
        first_pid = int(first["backend_pid"])

        with self.psycopg.connect(self.owner_dsn, autocommit=True) as owner:
            with owner.cursor() as cursor:
                cursor.execute("SELECT pg_terminate_backend(%s)", (first_pid,))
                self.assertTrue(cursor.fetchone()[0])

        with postgres_transaction(
            self.runtime_dsn,
            schema=self.schema,
            runtime_role=self.runtime_role,
            organization_id="acme",
            connection_pool=pool,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid() AS backend_pid")
                second = cursor.fetchone()
        assert second is not None
        self.assertNotEqual(first_pid, int(second["backend_pid"]))


if __name__ == "__main__":
    unittest.main()
