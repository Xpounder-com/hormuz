"""Red-first PostgreSQL 15-to-16 provider-collection transition proof."""

from __future__ import annotations

import hashlib
import os
import subprocess
import unittest
from unittest import mock
from uuid import uuid4

import hormuz.postgres as postgres_module
from hormuz.postgres import PostgresStorageError, migrate_postgres
from hormuz.postgres_usage_store import PostgresUsageStore

if __package__:
    from ._finance_collection_predecessor_fixture import (
        finance_collection_predecessor_call,
    )
    from ._finance_collection_transition_fixture import (
        seed_postgres_collection_predecessor,
    )
    from ._postgres_fixture import PostgresTestCase
else:
    from _finance_collection_predecessor_fixture import (
        finance_collection_predecessor_call,
    )
    from _finance_collection_transition_fixture import (
        seed_postgres_collection_predecessor,
    )
    from _postgres_fixture import PostgresTestCase


PLANNED_TABLES = (
    "portfolio_finance_source_binding_versions",
    "portfolio_finance_collection_attempts",
    "portfolio_finance_collection_events",
    "portfolio_finance_snapshots",
    "portfolio_finance_usage_observations",
    "portfolio_finance_cost_observations",
)

AUDIT_SOURCE_SCHEMAS = (
    "hormuz.finance-source-binding-version",
    "hormuz.finance-collection-event",
    "hormuz.finance-snapshot",
)


def _synthetic_collection_migration(quoted_schema: str, *, fail=False) -> str:
    append_only = "\n".join(
        f"""
        CREATE TRIGGER {table}_immutable
        BEFORE UPDATE OR DELETE OR TRUNCATE
        ON {quoted_schema}.{table}
        FOR EACH STATEMENT
        EXECUTE FUNCTION {quoted_schema}.portfolio_reject_mutation();
        """
        for table in PLANNED_TABLES
    )
    statement = f"""
    CREATE TABLE {quoted_schema}.portfolio_finance_source_binding_versions (
        organization_id TEXT NOT NULL,
        binding_id TEXT NOT NULL,
        version BIGINT NOT NULL,
        binding_event_id TEXT NOT NULL,
        evidence_json TEXT NOT NULL,
        PRIMARY KEY (organization_id, binding_id, version),
        UNIQUE (organization_id, binding_event_id)
    );
    CREATE TABLE {quoted_schema}.portfolio_finance_collection_attempts (
        organization_id TEXT NOT NULL,
        attempt_id TEXT NOT NULL,
        binding_id TEXT NOT NULL,
        binding_version BIGINT NOT NULL,
        PRIMARY KEY (organization_id, attempt_id),
        FOREIGN KEY (organization_id, binding_id, binding_version)
            REFERENCES {quoted_schema}.portfolio_finance_source_binding_versions
                (organization_id, binding_id, version)
    );
    CREATE TABLE {quoted_schema}.portfolio_finance_collection_events (
        organization_id TEXT NOT NULL,
        event_id TEXT NOT NULL,
        attempt_id TEXT NOT NULL,
        state TEXT NOT NULL,
        evidence_json TEXT NOT NULL,
        PRIMARY KEY (organization_id, event_id),
        UNIQUE (organization_id, attempt_id),
        FOREIGN KEY (organization_id, attempt_id)
            REFERENCES {quoted_schema}.portfolio_finance_collection_attempts
                (organization_id, attempt_id)
    );
    CREATE TABLE {quoted_schema}.portfolio_finance_snapshots (
        organization_id TEXT NOT NULL,
        snapshot_id TEXT NOT NULL,
        attempt_id TEXT NOT NULL,
        content_digest TEXT NOT NULL,
        evidence_json TEXT NOT NULL,
        PRIMARY KEY (organization_id, snapshot_id),
        UNIQUE (organization_id, attempt_id),
        FOREIGN KEY (organization_id, attempt_id)
            REFERENCES {quoted_schema}.portfolio_finance_collection_attempts
                (organization_id, attempt_id)
    );
    CREATE TABLE {quoted_schema}.portfolio_finance_usage_observations (
        organization_id TEXT NOT NULL,
        observation_id TEXT NOT NULL,
        snapshot_id TEXT NOT NULL,
        bucket_start_at TIMESTAMPTZ NOT NULL,
        bucket_end_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (organization_id, observation_id),
        FOREIGN KEY (organization_id, snapshot_id)
            REFERENCES {quoted_schema}.portfolio_finance_snapshots
                (organization_id, snapshot_id)
    );
    CREATE TABLE {quoted_schema}.portfolio_finance_cost_observations (
        organization_id TEXT NOT NULL,
        observation_id TEXT NOT NULL,
        snapshot_id TEXT NOT NULL,
        bucket_start_at TIMESTAMPTZ NOT NULL,
        bucket_end_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (organization_id, observation_id),
        FOREIGN KEY (organization_id, snapshot_id)
            REFERENCES {quoted_schema}.portfolio_finance_snapshots
                (organization_id, snapshot_id)
    );

    {append_only}

    ALTER TABLE {quoted_schema}.gateway_audit_chain_entries
        DROP CONSTRAINT gateway_audit_chain_entries_source_identity_check;
    ALTER TABLE {quoted_schema}.gateway_audit_chain_entries
        ADD CONSTRAINT gateway_audit_chain_entries_source_identity_check CHECK (
            (
                entry_schema_version = 1
                AND source_schema_id IS NULL
                AND source_schema_version IS NULL
                AND source_event_id IS NULL
            ) OR (
                entry_schema_version = 2
                AND source_event_id IS NOT NULL
                AND (
                    (source_schema_id = 'hormuz.custody-control-event' AND source_schema_version = 1)
                    OR (source_schema_id = 'hormuz.custody-execution-attempt' AND source_schema_version = 2)
                    OR (source_schema_id = 'hormuz.custody-execution-event' AND source_schema_version = 1)
                    OR (source_schema_id = 'hormuz.custody-lifecycle-event' AND source_schema_version = 1)
                    OR (source_schema_id = 'hormuz.custody-envelope-attestation' AND source_schema_version = 1)
                    OR (source_schema_id = 'hormuz.custody-deletion-event' AND source_schema_version = 1)
                    OR (source_schema_id = 'hormuz.finance-attempt-evidence' AND source_schema_version = 1)
                    OR (source_schema_id = 'hormuz.finance-source-binding-version' AND source_schema_version = 1)
                    OR (source_schema_id = 'hormuz.finance-collection-event' AND source_schema_version = 1)
                    OR (source_schema_id = 'hormuz.finance-snapshot' AND source_schema_version = 1)
                )
            )
        );

    CREATE OR REPLACE FUNCTION {quoted_schema}.enforce_custody_audit_chain_entry_insert()
    RETURNS trigger
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = pg_catalog
    AS $$
    DECLARE
        v_source_json TEXT;
    BEGIN
        IF NEW.entry_schema_version = 1 THEN
            RETURN NEW;
        END IF;
        IF NEW.entry_schema_version <> 2
           OR NEW.event_id IS DISTINCT FROM NEW.source_event_id THEN
            RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'audit chain entry is invalid';
        END IF;
        IF NOT (
            (NEW.source_schema_id = 'hormuz.custody-control-event' AND NEW.source_schema_version = 1)
            OR (NEW.source_schema_id = 'hormuz.custody-execution-attempt' AND NEW.source_schema_version = 2)
            OR (NEW.source_schema_id = 'hormuz.custody-execution-event' AND NEW.source_schema_version = 1)
            OR (NEW.source_schema_id = 'hormuz.custody-lifecycle-event' AND NEW.source_schema_version = 1)
            OR (NEW.source_schema_id = 'hormuz.custody-envelope-attestation' AND NEW.source_schema_version = 1)
            OR (NEW.source_schema_id = 'hormuz.custody-deletion-event' AND NEW.source_schema_version = 1)
            OR (NEW.source_schema_id = 'hormuz.finance-attempt-evidence' AND NEW.source_schema_version = 1)
            OR (NEW.source_schema_id = 'hormuz.finance-source-binding-version' AND NEW.source_schema_version = 1)
            OR (NEW.source_schema_id = 'hormuz.finance-collection-event' AND NEW.source_schema_version = 1)
            OR (NEW.source_schema_id = 'hormuz.finance-snapshot' AND NEW.source_schema_version = 1)
        ) THEN
            RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'audit source schema is unsupported';
        END IF;
        IF NEW.source_schema_id = 'hormuz.custody-control-event' AND NEW.source_schema_version = 1 THEN
            SELECT evidence_json INTO v_source_json
              FROM {quoted_schema}.custody_control_events
             WHERE organization_id = NEW.organization_id AND event_id = NEW.source_event_id;
        ELSIF NEW.source_schema_id = 'hormuz.custody-execution-attempt' AND NEW.source_schema_version = 2 THEN
            SELECT evidence_json INTO v_source_json
              FROM {quoted_schema}.custody_execution_attempts
             WHERE organization_id = NEW.organization_id AND execution_id = NEW.source_event_id;
        ELSIF NEW.source_schema_id = 'hormuz.custody-execution-event' AND NEW.source_schema_version = 1 THEN
            SELECT evidence_json INTO v_source_json
              FROM {quoted_schema}.custody_execution_events
             WHERE organization_id = NEW.organization_id
               AND execution_id || ':' || sequence::TEXT = NEW.source_event_id;
        ELSIF NEW.source_schema_id = 'hormuz.custody-lifecycle-event' AND NEW.source_schema_version = 1 THEN
            SELECT evidence_json INTO v_source_json
              FROM {quoted_schema}.custody_lifecycle_events
             WHERE organization_id = NEW.organization_id AND lifecycle_event_id = NEW.source_event_id;
        ELSIF NEW.source_schema_id = 'hormuz.custody-envelope-attestation' AND NEW.source_schema_version = 1 THEN
            SELECT evidence_json INTO v_source_json
              FROM {quoted_schema}.custody_envelope_attestations
             WHERE organization_id = NEW.organization_id
               AND execution_id || ':' || attestation_kind = NEW.source_event_id;
        ELSIF NEW.source_schema_id = 'hormuz.custody-deletion-event' AND NEW.source_schema_version = 1 THEN
            SELECT evidence_json INTO v_source_json
              FROM {quoted_schema}.custody_deletion_events
             WHERE organization_id = NEW.organization_id AND deletion_event_id = NEW.source_event_id;
        ELSIF NEW.source_schema_id = 'hormuz.finance-attempt-evidence' AND NEW.source_schema_version = 1 THEN
            SELECT evidence_json INTO v_source_json
              FROM {quoted_schema}.gateway_finance_attempt_evidence
             WHERE organization_id = NEW.organization_id AND evidence_event_id = NEW.source_event_id;
        ELSIF NEW.source_schema_id = 'hormuz.finance-source-binding-version' AND NEW.source_schema_version = 1 THEN
            SELECT evidence_json INTO v_source_json
              FROM {quoted_schema}.portfolio_finance_source_binding_versions
             WHERE organization_id = NEW.organization_id AND binding_event_id = NEW.source_event_id;
        ELSIF NEW.source_schema_id = 'hormuz.finance-collection-event' AND NEW.source_schema_version = 1 THEN
            SELECT evidence_json INTO v_source_json
              FROM {quoted_schema}.portfolio_finance_collection_events
             WHERE organization_id = NEW.organization_id AND event_id = NEW.source_event_id;
        ELSIF NEW.source_schema_id = 'hormuz.finance-snapshot' AND NEW.source_schema_version = 1 THEN
            SELECT evidence_json INTO v_source_json
              FROM {quoted_schema}.portfolio_finance_snapshots
             WHERE organization_id = NEW.organization_id AND snapshot_id = NEW.source_event_id;
        END IF;
        IF v_source_json IS NULL OR NEW.event_json IS DISTINCT FROM v_source_json THEN
            RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'audit source evidence mismatch';
        END IF;
        RETURN NEW;
    END;
    $$;
    """
    return statement + (" SELECT 1 / 0;" if fail else "")


@unittest.skipUnless(
    os.environ.get("HORMUZ_TEST_POSTGRES_DSN"),
    "requires disposable PostgreSQL",
)
class PostgresFinanceCollectionTransitionTests(PostgresTestCase):
    def migrate(self):
        return migrate_postgres(
            self.owner_dsn,
            schema=self.schema,
            runtime_role=self.runtime_role,
            policy_control_role=self.policy_control_role,
            custody_control_role=self.custody_control_role,
            custody_executor_role=self.custody_executor_role,
        )

    def runtime(self, dsn=None):
        return PostgresUsageStore(
            dsn or self.runtime_dsn,
            schema=self.schema,
            runtime_role=self.runtime_role,
            organization_ids=("acme", "beta"),
        )

    def setUp(self):
        self.assertEqual(postgres_module.POSTGRES_SCHEMA_VERSION, 15)
        self._drop_schema(self.schema)
        self.seeded = seed_postgres_collection_predecessor(
            owner_dsn=self.owner_dsn,
            runtime_dsn=self.runtime_dsn,
            schema=self.schema,
            runtime_role=self.runtime_role,
            policy_control_role=self.policy_control_role,
            custody_control_role=self.custody_control_role,
            custody_executor_role=self.custody_executor_role,
        )
        self.before = self.snapshot()

    def assert_audit_transition(self, snapshot):
        constraint = next(
            row[3]
            for row in snapshot["constraints"]
            if row[0] == "gateway_audit_chain_entries"
            and row[1] == "gateway_audit_chain_entries_source_identity_check"
        )
        self.assertEqual(len(snapshot["functions"]), 1)
        function_name, security_definer, configuration, function = snapshot[
            "functions"
        ][0]
        self.assertEqual(
            (
                function_name,
                security_definer,
                configuration,
            ),
            (
                "enforce_custody_audit_chain_entry_insert",
                True,
                ["search_path=pg_catalog"],
            ),
        )
        for source_schema in AUDIT_SOURCE_SCHEMAS:
            self.assertIn(source_schema, constraint)
            self.assertGreaterEqual(function.count(source_schema), 2)
        self.assertTrue(
            any(
                row[0] == "gateway_audit_chain_entries"
                and row[1] == "gateway_audit_chain_entries_v2_source_required"
                for row in snapshot["triggers"]
            )
        )
        triggers = {
            (table, trigger): definition
            for table, trigger, _, definition in snapshot["triggers"]
        }
        for table in PLANNED_TABLES:
            trigger = triggers[(table, f"{table}_immutable")]
            for operation in ("UPDATE", "DELETE", "TRUNCATE"):
                self.assertIn(operation, trigger)
            self.assertIn("FOR EACH STATEMENT", trigger)
            self.assertIn("portfolio_reject_mutation()", trigger)
        self.assertNotEqual(snapshot["functions"], self.before["functions"])
        self.assertEqual(
            snapshot["rows"]["gateway_audit_chain_entries"],
            self.before["rows"]["gateway_audit_chain_entries"],
        )
        for table in (
            "portfolio_finance_usage_observations",
            "portfolio_finance_cost_observations",
        ):
            constraints = [
                row for row in snapshot["constraints"] if row[0] == table
            ]
            self.assertTrue(constraints)

    def append_synthetic_audit_entry(
        self,
        connection,
        *,
        source_schema_id,
        source_event_id,
        evidence_json,
    ):
        connection.execute(
            self.sql.SQL(
                "INSERT INTO {}.gateway_audit_chain_entries "
                "(organization_id, chain_version, chain_epoch, sequence, "
                "entry_schema_id, entry_schema_version, event_id, "
                "previous_digest, event_digest, event_json, appended_at, "
                "source_schema_id, source_schema_version, source_event_id) "
                "SELECT %s, head.chain_version, head.chain_epoch, "
                "(SELECT COALESCE(MAX(entry.sequence), 0) + 1 "
                "FROM {}.gateway_audit_chain_entries entry "
                "WHERE entry.organization_id=%s "
                "AND entry.chain_epoch=head.chain_epoch), "
                "%s, %s, %s, %s, %s, %s, clock_timestamp(), %s, %s, %s "
                "FROM {}.gateway_audit_chain_heads head "
                "WHERE head.organization_id=%s"
            ).format(
                self.sql.Identifier(self.schema),
                self.sql.Identifier(self.schema),
                self.sql.Identifier(self.schema),
            ),
            (
                "acme",
                "acme",
                "hormuz.commit-audit-chain-entry",
                2,
                source_event_id,
                None,
                "f" * 64,
                evidence_json,
                source_schema_id,
                1,
                source_event_id,
                "acme",
            ),
        )

    def seed_synthetic_collection_rows(self, connection):
        binding_event = "11111111-1111-4111-8111-111111111118"
        collection_event = "11111111-1111-4111-8111-111111111119"
        snapshot_event = "11111111-1111-4111-8111-111111111120"
        connection.execute(
            self.sql.SQL(
                "INSERT INTO {}.portfolio_finance_source_binding_versions "
                "(organization_id, binding_id, version, binding_event_id, "
                "evidence_json) VALUES (%s, %s, %s, %s, %s)"
            ).format(self.sql.Identifier(self.schema)),
            (
                "acme",
                "binding",
                1,
                binding_event,
                '{"kind":"binding"}',
            ),
        )
        connection.execute(
            self.sql.SQL(
                "INSERT INTO {}.portfolio_finance_collection_attempts "
                "(organization_id, attempt_id, binding_id, binding_version) "
                "VALUES (%s, %s, %s, %s)"
            ).format(self.sql.Identifier(self.schema)),
            ("acme", "attempt", "binding", 1),
        )
        connection.execute(
            self.sql.SQL(
                "INSERT INTO {}.portfolio_finance_collection_events "
                "(organization_id, event_id, attempt_id, state, "
                "evidence_json) VALUES (%s, %s, %s, %s, %s)"
            ).format(self.sql.Identifier(self.schema)),
            (
                "acme",
                collection_event,
                "attempt",
                "succeeded",
                '{"kind":"terminal"}',
            ),
        )
        connection.execute(
            self.sql.SQL(
                "INSERT INTO {}.portfolio_finance_snapshots "
                "(organization_id, snapshot_id, attempt_id, "
                "content_digest, evidence_json) "
                "VALUES (%s, %s, %s, %s, %s)"
            ).format(self.sql.Identifier(self.schema)),
            (
                "acme",
                snapshot_event,
                "attempt",
                "a" * 64,
                '{"kind":"snapshot"}',
            ),
        )
        for table, observation_id in (
            (
                "portfolio_finance_usage_observations",
                "11111111-1111-4111-8111-111111111124",
            ),
            (
                "portfolio_finance_cost_observations",
                "11111111-1111-4111-8111-111111111125",
            ),
        ):
            connection.execute(
                self.sql.SQL(
                    "INSERT INTO {}.{} "
                    "(organization_id, observation_id, snapshot_id, "
                    "bucket_start_at, bucket_end_at) "
                    "VALUES (%s, %s, %s, %s, %s)"
                ).format(
                    self.sql.Identifier(self.schema),
                    self.sql.Identifier(table),
                ),
                (
                    "acme",
                    observation_id,
                    snapshot_event,
                    "2026-09-01T00:00:00Z",
                    "2026-09-02T00:00:00Z",
                ),
            )
        return (
            (
                "hormuz.finance-source-binding-version",
                binding_event,
                '{"kind":"binding"}',
            ),
            (
                "hormuz.finance-collection-event",
                collection_event,
                '{"kind":"terminal"}',
            ),
            (
                "hormuz.finance-snapshot",
                snapshot_event,
                '{"kind":"snapshot"}',
            ),
        )

    def request(self, runtime_dsn=None):
        return {
            "backend": "postgresql",
            "runtime_dsn": runtime_dsn or self.runtime_dsn,
            "schema": self.schema,
            "runtime_role": self.runtime_role,
        }

    def snapshot(self, dsn=None):
        with self.psycopg.connect(dsn or self.owner_dsn) as connection:
            tables = connection.execute(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname=%s ORDER BY tablename",
                (self.schema,),
            ).fetchall()
            rows = {
                table: connection.execute(
                    self.sql.SQL(
                        "SELECT row_to_json(t)::text FROM {}.{} t "
                        "ORDER BY row_to_json(t)::text"
                    ).format(
                        self.sql.Identifier(self.schema),
                        self.sql.Identifier(table),
                    )
                ).fetchall()
                for (table,) in tables
            }
            shape = connection.execute(
                "SELECT c.relname,c.relkind,c.relrowsecurity,"
                "c.relforcerowsecurity,c.relacl::text "
                "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname=%s ORDER BY c.relname",
                (self.schema,),
            ).fetchall()
            constraints = connection.execute(
                "SELECT rel.relname,con.conname,con.contype,"
                "pg_get_constraintdef(con.oid, true) "
                "FROM pg_constraint con "
                "JOIN pg_class rel ON rel.oid=con.conrelid "
                "JOIN pg_namespace n ON n.oid=rel.relnamespace "
                "WHERE n.nspname=%s "
                "ORDER BY rel.relname,con.conname",
                (self.schema,),
            ).fetchall()
            functions = connection.execute(
                "SELECT p.proname,p.prosecdef,p.proconfig,"
                "pg_get_functiondef(p.oid) "
                "FROM pg_proc p "
                "JOIN pg_namespace n ON n.oid=p.pronamespace "
                "WHERE n.nspname=%s "
                "AND p.proname='enforce_custody_audit_chain_entry_insert' "
                "ORDER BY p.proname,pg_get_function_identity_arguments(p.oid)",
                (self.schema,),
            ).fetchall()
            triggers = connection.execute(
                "SELECT c.relname,t.tgname,t.tgenabled,"
                "pg_get_triggerdef(t.oid, true) "
                "FROM pg_trigger t "
                "JOIN pg_class c ON c.oid=t.tgrelid "
                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname=%s AND NOT t.tgisinternal "
                "ORDER BY c.relname,t.tgname",
                (self.schema,),
            ).fetchall()
        return {
            "rows": rows,
            "shape": shape,
            "constraints": constraints,
            "functions": functions,
            "triggers": triggers,
        }

    def probe(self, *, fail=False, dsn=None):
        original = postgres_module._migration_sql

        def migration(version, schema, *roles):
            if version == 16:
                return _synthetic_collection_migration(schema, fail=fail)
            return original(version, schema, *roles)

        original_dsn = self.owner_dsn
        if dsn is not None:
            self.owner_dsn = dsn
        try:
            with (
                mock.patch.object(postgres_module, "POSTGRES_SCHEMA_VERSION", 16),
                mock.patch.object(
                    postgres_module,
                    "_migration_sql",
                    side_effect=migration,
                ),
            ):
                self.assertEqual(self.migrate().version, 16)
        finally:
            self.owner_dsn = original_dsn

    def test_predecessor_has_every_accepted_populated_domain(self):
        rows = self.before["rows"]
        for table in (
            "portfolio_work_scope_versions",
            "portfolio_attribution_events",
            "portfolio_outcome_events",
            "portfolio_finance_rate_cards",
            "portfolio_work_budget_plan_versions",
            "gateway_provider_attempt_metrics",
            "gateway_finance_attempt_evidence",
            "gateway_audit_chain_entries",
        ):
            with self.subTest(table=table):
                self.assertIn(table, rows)
                self.assertTrue(rows[table])
        self.assertEqual(self.seeded["budget"]["active_version"], 1)
        self.assertEqual(self.seeded["outcome_delivery_count"], 3)

    def test_missing_migration_is_red_without_partial_state(self):
        with mock.patch.object(postgres_module, "POSTGRES_SCHEMA_VERSION", 16):
            with self.assertRaises(PostgresStorageError) as caught:
                self.migrate()
        self.assertEqual(caught.exception.code, "storage_schema_migration_unsupported")
        self.assertEqual(self.snapshot(), self.before)

    def test_synthetic_ddl_failure_rolls_back_and_retry_is_idempotent(self):
        with self.assertRaises(PostgresStorageError) as caught:
            self.probe(fail=True)
        self.assertEqual(caught.exception.code, "storage_unavailable")
        self.assertEqual(self.snapshot(), self.before)
        self.probe()
        after = self.snapshot()
        for table in PLANNED_TABLES:
            self.assertEqual(after["rows"][table], [])
        self.assert_audit_transition(after)
        self.probe()
        self.assertEqual(self.snapshot(), after)

    def test_audit_source_guard_requires_exact_source_identity_and_json(self):
        self.probe()
        mismatch_event = "11111111-1111-4111-8111-111111111121"
        unsupported_event = "11111111-1111-4111-8111-111111111122"
        with self.psycopg.connect(self.owner_dsn) as connection:
            for source_schema, event_id, evidence_json in (
                self.seed_synthetic_collection_rows(connection)
            ):
                self.append_synthetic_audit_entry(
                    connection,
                    source_schema_id=source_schema,
                    source_event_id=event_id,
                    evidence_json=evidence_json,
                )
            connection.execute(
                self.sql.SQL(
                    "INSERT INTO {}.portfolio_finance_source_binding_versions "
                    "(organization_id, binding_id, version, binding_event_id, "
                    "evidence_json) VALUES (%s, %s, %s, %s, %s)"
                ).format(self.sql.Identifier(self.schema)),
                (
                    "acme",
                    "binding",
                    2,
                    mismatch_event,
                    '{"kind":"binding-2"}',
                ),
            )
            with self.assertRaisesRegex(
                self.psycopg.errors.CheckViolation,
                "audit source evidence mismatch",
            ):
                with connection.transaction():
                    self.append_synthetic_audit_entry(
                        connection,
                        source_schema_id="hormuz.finance-source-binding-version",
                        source_event_id=mismatch_event,
                        evidence_json='{"kind":"wrong"}',
                    )
            with self.assertRaises(self.psycopg.errors.CheckViolation):
                with connection.transaction():
                    self.append_synthetic_audit_entry(
                        connection,
                        source_schema_id="hormuz.finance-unsupported",
                        source_event_id=unsupported_event,
                        evidence_json="{}",
                    )
            observed = connection.execute(
                self.sql.SQL(
                    "SELECT source_schema_id "
                    "FROM {}.gateway_audit_chain_entries "
                    "WHERE source_schema_id=ANY(%s) ORDER BY source_schema_id"
                ).format(self.sql.Identifier(self.schema)),
                (list(AUDIT_SOURCE_SCHEMAS),),
            ).fetchall()
            self.assertEqual(
                [row[0] for row in observed],
                sorted(AUDIT_SOURCE_SCHEMAS),
            )

    def test_collection_tables_reject_update_delete_and_truncate(self):
        self.probe()
        with self.psycopg.connect(self.owner_dsn) as connection:
            for source_schema, event_id, evidence_json in (
                self.seed_synthetic_collection_rows(connection)
            ):
                self.append_synthetic_audit_entry(
                    connection,
                    source_schema_id=source_schema,
                    source_event_id=event_id,
                    evidence_json=evidence_json,
                )
            for table in PLANNED_TABLES:
                statements = (
                    (
                        "update",
                        self.sql.SQL(
                            "UPDATE {}.{} SET organization_id=organization_id"
                        ).format(
                            self.sql.Identifier(self.schema),
                            self.sql.Identifier(table),
                        ),
                    ),
                    (
                        "delete",
                        self.sql.SQL("DELETE FROM {}.{}").format(
                            self.sql.Identifier(self.schema),
                            self.sql.Identifier(table),
                        ),
                    ),
                    (
                        "truncate",
                        self.sql.SQL("TRUNCATE {}.{} CASCADE").format(
                            self.sql.Identifier(self.schema),
                            self.sql.Identifier(table),
                        ),
                    ),
                )
                for mutation, statement in statements:
                    with self.subTest(table=table, mutation=mutation):
                        with self.assertRaisesRegex(
                            self.psycopg.errors.CheckViolation,
                            "portfolio_append_only",
                        ):
                            with connection.transaction():
                                connection.execute(statement)
        snapshot = self.snapshot()
        for table in PLANNED_TABLES:
            self.assertEqual(len(snapshot["rows"][table]), 1)

    def test_current_binary_refuses_newer_and_partial_state_without_repair(self):
        self.probe()
        candidate = self.snapshot()
        with self.assertRaises(PostgresStorageError) as caught:
            self.runtime()
        self.assertEqual(caught.exception.code, "storage_schema_newer_than_binary")
        self.assertEqual(self.snapshot(), candidate)

        with self.psycopg.connect(self.owner_dsn) as connection:
            connection.execute(
                self.sql.SQL(
                    "UPDATE {}.hormuz_schema_migrations "
                    "SET state='applying' WHERE version=16"
                ).format(self.sql.Identifier(self.schema))
            )
        partial = self.snapshot()
        with self.assertRaises(PostgresStorageError) as caught:
            self.runtime()
        self.assertEqual(caught.exception.code, "storage_schema_partial_upgrade")
        self.assertEqual(self.snapshot(), partial)

    @unittest.skipUnless(
        os.environ.get("HORMUZ_TEST_FINANCE_COLLECTION_PYTHON"),
        "requires digest-pinned accepted native-finance predecessor",
    )
    def test_exact_predecessor_accepts_current_and_refuses_newer_and_partial(self):
        self.assertEqual(
            finance_collection_predecessor_call(self.request()),
            {"status": "ready", "runtime_files_verified": 144},
        )
        self.probe()
        self.assertEqual(
            finance_collection_predecessor_call(self.request()),
            {"status": "refused", "code": "storage_schema_newer_than_binary"},
        )
        with self.psycopg.connect(self.owner_dsn) as connection:
            connection.execute(
                self.sql.SQL(
                    "UPDATE {}.hormuz_schema_migrations "
                    "SET state='applying' WHERE version=16"
                ).format(self.sql.Identifier(self.schema))
            )
        self.assertEqual(
            finance_collection_predecessor_call(self.request()),
            {"status": "refused", "code": "storage_schema_partial_upgrade"},
        )

    def backup(self):
        info = self.psycopg.conninfo.conninfo_to_dict(self.owner_dsn)
        result = subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                os.environ["HORMUZ_TEST_PG_CONTAINER"],
                "pg_dump",
                "-U",
                info["user"],
                "-d",
                info["dbname"],
                "--schema",
                self.schema,
                "--format=custom",
            ],
            capture_output=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, "test_pg_dump_failed")
        self.assertTrue(result.stdout.startswith(b"PGDMP"))
        return result.stdout

    def restore(self, backup):
        database = "finance_collection_restore_" + uuid4().hex[:12]
        with self.psycopg.connect(
            self.owner_dsn,
            autocommit=True,
        ) as connection:
            connection.execute(
                self.sql.SQL("CREATE DATABASE {}").format(
                    self.sql.Identifier(database)
                )
            )

        def cleanup():
            with self.psycopg.connect(
                self.owner_dsn,
                autocommit=True,
            ) as connection:
                connection.execute(
                    self.sql.SQL("DROP DATABASE {}").format(
                        self.sql.Identifier(database)
                    )
                )

        self.addCleanup(cleanup)
        info = self.psycopg.conninfo.conninfo_to_dict(self.owner_dsn)
        digest = hashlib.sha256(backup).hexdigest()
        result = subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                os.environ["HORMUZ_TEST_PG_CONTAINER"],
                "pg_restore",
                "-U",
                info["user"],
                "-d",
                database,
                "--no-owner",
                "--exit-on-error",
            ],
            input=backup,
            capture_output=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, "test_pg_restore_failed")
        self.assertEqual(hashlib.sha256(backup).hexdigest(), digest)
        return (
            self.psycopg.conninfo.make_conninfo(self.owner_dsn, dbname=database),
            self.psycopg.conninfo.make_conninfo(self.runtime_dsn, dbname=database),
        )

    @unittest.skipUnless(
        os.environ.get("HORMUZ_TEST_PG_CONTAINER"),
        "requires matched disposable backup tools",
    )
    def test_quiesced_old_pair_restore_preserves_every_accepted_byte(self):
        backup = self.backup()
        self.probe()
        retained = self.snapshot()
        owner, runtime = self.restore(backup)
        PostgresUsageStore(
            runtime,
            schema=self.schema,
            runtime_role=self.runtime_role,
            organization_ids=("acme", "beta"),
        ).verify_ready()
        self.assertEqual(self.snapshot(owner), self.before)
        self.assertEqual(self.snapshot(), retained)

    @unittest.skipUnless(
        os.environ.get("HORMUZ_TEST_PG_CONTAINER"),
        "requires matched disposable backup tools",
    )
    def test_post_checkpoint_write_requires_retained_forward_recovery(self):
        self.probe()
        with self.psycopg.connect(self.owner_dsn) as connection:
            connection.execute(
                self.sql.SQL(
                    "INSERT INTO {}.portfolio_finance_source_binding_versions "
                    "(organization_id, binding_id, version, binding_event_id, "
                    "evidence_json) VALUES (%s, %s, %s, %s, %s)"
                ).format(self.sql.Identifier(self.schema)),
                (
                    "acme",
                    "synthetic-binding",
                    1,
                    "11111111-1111-4111-8111-111111111123",
                    '{"kind":"binding"}',
                ),
            )
        after_write = self.snapshot()
        backup = self.backup()
        owner, runtime = self.restore(backup)
        self.probe(dsn=owner)
        self.assertEqual(self.snapshot(owner), after_write)
        with self.assertRaises(PostgresStorageError) as caught:
            PostgresUsageStore(
                runtime,
                schema=self.schema,
                runtime_role=self.runtime_role,
                organization_ids=("acme", "beta"),
            )
        self.assertEqual(caught.exception.code, "storage_schema_newer_than_binary")


if __name__ == "__main__":
    unittest.main()
