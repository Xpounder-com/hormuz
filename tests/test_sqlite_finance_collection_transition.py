"""Red-first SQLite 11-to-12 provider-collection transition proof."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest import mock

from hormuz.store import StorageSchemaError, UsageStore

if __package__:
    from ._finance_collection_predecessor_fixture import (
        finance_collection_predecessor_call,
    )
    from ._finance_collection_transition_fixture import (
        seed_sqlite_collection_predecessor,
    )
    from ._registry_transition_fixture import sqlite_backup, sqlite_snapshot
    from ._sqlite import managed_sqlite_connection
else:
    from _finance_collection_predecessor_fixture import (
        finance_collection_predecessor_call,
    )
    from _finance_collection_transition_fixture import (
        seed_sqlite_collection_predecessor,
    )
    from _registry_transition_fixture import sqlite_backup, sqlite_snapshot
    from _sqlite import managed_sqlite_connection


PLANNED_TABLES = (
    "portfolio_finance_source_binding_versions",
    "portfolio_finance_collection_attempts",
    "portfolio_finance_collection_events",
    "portfolio_finance_snapshots",
    "portfolio_finance_snapshot_bucket_coverage",
    "portfolio_finance_usage_observations",
    "portfolio_finance_cost_observations",
)

AUDIT_SOURCE_SCHEMAS = (
    "hormuz.finance-source-binding-version",
    "hormuz.finance-collection-event",
    "hormuz.finance-snapshot",
)

BINDING_EVENT_ID = "11111111-1111-4111-8111-111111111111"
ATTEMPT_ID = "11111111-1111-4111-8111-111111111112"
COLLECTION_EVENT_ID = "11111111-1111-4111-8111-111111111113"
SNAPSHOT_ID = "11111111-1111-4111-8111-111111111114"

SQLITE_APPEND_ONLY_IDENTITIES = {
    "portfolio_finance_source_binding_versions": (
        "(existing.binding_id=NEW.binding_id AND existing.version=NEW.version)",
        "existing.binding_event_id=NEW.binding_event_id",
    ),
    "portfolio_finance_collection_attempts": (
        "existing.attempt_id=NEW.attempt_id",
    ),
    "portfolio_finance_collection_events": (
        "existing.event_id=NEW.event_id",
        "existing.attempt_id=NEW.attempt_id",
    ),
    "portfolio_finance_snapshots": (
        "existing.snapshot_id=NEW.snapshot_id",
        "existing.attempt_id=NEW.attempt_id",
    ),
    "portfolio_finance_snapshot_bucket_coverage": (
        "existing.coverage_id=NEW.coverage_id",
        "(existing.snapshot_id=NEW.snapshot_id "
        "AND existing.bucket_start_at=NEW.bucket_start_at "
        "AND existing.bucket_end_at=NEW.bucket_end_at)",
    ),
    "portfolio_finance_usage_observations": (
        "existing.observation_id=NEW.observation_id",
    ),
    "portfolio_finance_cost_observations": (
        "existing.observation_id=NEW.observation_id",
    ),
}


def _append_synthetic_audit_entry(
    connection,
    *,
    source_schema_id,
    source_event_id,
    evidence_json,
):
    head = connection.execute(
        "SELECT chain_version, chain_epoch FROM gateway_audit_chain_heads "
        "WHERE organization_id=?",
        ("acme",),
    ).fetchone()
    if head is None:
        raise AssertionError("synthetic_finance_collection_audit_head_missing")
    sequence = connection.execute(
        "SELECT COALESCE(MAX(sequence), 0) + 1 "
        "FROM gateway_audit_chain_entries WHERE organization_id=? "
        "AND chain_epoch=?",
        ("acme", head[1]),
    ).fetchone()[0]
    connection.execute(
        "INSERT INTO gateway_audit_chain_entries "
        "(organization_id, chain_version, chain_epoch, sequence, "
        "entry_schema_id, entry_schema_version, event_id, previous_digest, "
        "event_digest, event_json, appended_at, source_schema_id, "
        "source_schema_version, source_event_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "acme",
            head[0],
            head[1],
            sequence,
            "hormuz.commit-audit-chain-entry",
            2,
            source_event_id,
            None,
            "f" * 64,
            evidence_json,
            "2026-09-02T00:00:00Z",
            source_schema_id,
            1,
            source_event_id,
        ),
    )


def _seed_synthetic_collection_rows(connection):
    connection.execute(
        "INSERT INTO portfolio_finance_source_binding_versions "
        "(organization_id,binding_id,version,binding_event_id,provider,"
        "provider_account_fingerprint,scope_kind,scope_fingerprints_json,"
        "credential_reference_id,credential_reference_version,"
        "fingerprint_key_version,binding_state,previous_version,content_digest,"
        "bound_by,bound_at,evidence_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "acme", "binding", 1, BINDING_EVENT_ID, "openai", "a" * 64,
            "organization", "[]", "upstream:openai", 1, 1, "active", None,
            "b" * 64, "alice", "2026-09-02T00:00:00Z", '{"kind":"binding"}',
        ),
    )
    connection.execute(
        "INSERT INTO portfolio_finance_collection_attempts "
        "(organization_id,attempt_id,binding_id,binding_version,provider,"
        "collection_profile,source_kind,query_start_at,query_end_at,bucket_width,"
        "requested_page_size,evidence_origin,idempotency_digest,request_digest,"
        "credential_reference_id,credential_reference_version,fingerprint_key_version,"
        "prepared_by,prepared_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "acme", ATTEMPT_ID, "binding", 1, "openai",
            "openai.organization-usage-completions.v1", "usage",
            "2026-09-01T00:00:00Z", "2026-09-03T00:00:00Z", "1d", 2,
            "customer_file", "c" * 64, "d" * 64, "upstream:openai", 1, 1,
            "alice", "2026-09-02T00:00:00Z",
        ),
    )
    connection.execute(
        "INSERT INTO portfolio_finance_collection_events "
        "(organization_id,event_id,attempt_id,state,reason_code,receipt_id,"
        "snapshot_id,actor_id,occurred_at,evidence_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            "acme", COLLECTION_EVENT_ID, ATTEMPT_ID, "succeeded", "completed",
            "e" * 32, SNAPSHOT_ID, "alice", "2026-09-02T00:00:00Z",
            '{"kind":"terminal"}',
        ),
    )
    connection.execute(
        "INSERT INTO portfolio_finance_snapshots "
        "(organization_id,snapshot_id,attempt_id,binding_id,binding_version,"
        "collection_profile,source_kind,query_start_at,query_end_at,evidence_origin,"
        "scope_provenance,parser_version,page_count,record_count,requested_page_size,"
        "page_chain_digest,content_digest,supersedes_snapshot_id,commit_sequence,"
        "published_by,published_at,provider_final,invoice_final,evidence_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "acme", SNAPSHOT_ID, ATTEMPT_ID, "binding", 1,
            "openai.organization-usage-completions.v1", "usage",
            "2026-09-01T00:00:00Z", "2026-09-03T00:00:00Z", "customer_file",
            "customer_supplied_scope_unverified", 1, 1, 2, 2, "f" * 64,
            "0" * 64, None, 1, "alice", "2026-09-02T00:00:00Z", 0, 0,
            '{"kind":"snapshot"}',
        ),
    )
    for coverage in (
        (
            "coverage-observed",
            "2026-09-01T00:00:00Z",
            "2026-09-02T00:00:00Z",
            "observed",
            2,
        ),
        (
            "coverage-empty",
            "2026-08-31T00:00:00Z",
            "2026-09-01T00:00:00Z",
            "no_observation",
            0,
        ),
    ):
        connection.execute(
            "INSERT INTO portfolio_finance_snapshot_bucket_coverage "
            "(organization_id, coverage_id, snapshot_id, bucket_start_at, "
            "bucket_end_at, coverage_state, observation_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("acme", coverage[0].ljust(36, "0"), SNAPSHOT_ID, *coverage[1:]),
        )
    connection.execute(
        "INSERT INTO portfolio_finance_usage_observations "
        "(organization_id,observation_id,snapshot_id,bucket_start_at,bucket_end_at,"
        "observation_digest,input_tokens,usage_basis,provider_final) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "acme", "usage-observation".ljust(36, "0"), SNAPSHOT_ID,
            "2026-09-01T00:00:00Z", "2026-09-02T00:00:00Z", "1" * 64, 1,
            "provider_native_aggregate_observation", 0,
        ),
    )
    connection.execute(
        "INSERT INTO portfolio_finance_cost_observations "
        "(organization_id,observation_id,snapshot_id,bucket_start_at,bucket_end_at,"
        "observation_digest,free_text_classification,native_amount,canonical_amount,"
        "currency,cost_basis,provider_final,invoice_final) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "acme", "cost-observation".ljust(36, "0"), SNAPSHOT_ID,
            "2026-09-01T00:00:00Z", "2026-09-02T00:00:00Z", "2" * 64,
            "unclassified", "1", "1", "USD", "provider_reported_aggregate", 0, 0,
        ),
    )
    return (
        (
            "hormuz.finance-source-binding-version",
            BINDING_EVENT_ID,
            '{"kind":"binding"}',
        ),
        (
            "hormuz.finance-collection-event",
            COLLECTION_EVENT_ID,
            '{"kind":"terminal"}',
        ),
        (
            "hormuz.finance-snapshot",
            SNAPSHOT_ID,
            '{"kind":"snapshot"}',
        ),
    )


def _synthetic_collection_migration(connection, *, fail=False):
    connection.execute(
        """
        CREATE TABLE portfolio_finance_source_binding_versions (
            organization_id TEXT NOT NULL,
            binding_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            binding_event_id TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            PRIMARY KEY (organization_id, binding_id, version),
            UNIQUE (organization_id, binding_event_id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE portfolio_finance_collection_attempts (
            organization_id TEXT NOT NULL,
            attempt_id TEXT NOT NULL,
            binding_id TEXT NOT NULL,
            binding_version INTEGER NOT NULL,
            PRIMARY KEY (organization_id, attempt_id),
            FOREIGN KEY (organization_id, binding_id, binding_version)
                REFERENCES portfolio_finance_source_binding_versions
                    (organization_id, binding_id, version)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE portfolio_finance_collection_events (
            organization_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            attempt_id TEXT NOT NULL,
            state TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            PRIMARY KEY (organization_id, event_id),
            UNIQUE (organization_id, attempt_id),
            FOREIGN KEY (organization_id, attempt_id)
                REFERENCES portfolio_finance_collection_attempts
                    (organization_id, attempt_id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE portfolio_finance_snapshots (
            organization_id TEXT NOT NULL,
            snapshot_id TEXT NOT NULL,
            attempt_id TEXT NOT NULL,
            content_digest TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            PRIMARY KEY (organization_id, snapshot_id),
            UNIQUE (organization_id, attempt_id),
            FOREIGN KEY (organization_id, attempt_id)
                REFERENCES portfolio_finance_collection_attempts
                    (organization_id, attempt_id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE portfolio_finance_snapshot_bucket_coverage (
            organization_id TEXT NOT NULL,
            coverage_id TEXT NOT NULL,
            snapshot_id TEXT NOT NULL,
            bucket_start_at TEXT NOT NULL,
            bucket_end_at TEXT NOT NULL,
            coverage_state TEXT NOT NULL,
            observation_count INTEGER NOT NULL,
            PRIMARY KEY (organization_id, coverage_id),
            UNIQUE (
                organization_id,
                snapshot_id,
                bucket_start_at,
                bucket_end_at
            ),
            FOREIGN KEY (organization_id, snapshot_id)
                REFERENCES portfolio_finance_snapshots
                    (organization_id, snapshot_id),
            CHECK (coverage_state IN ('observed', 'no_observation')),
            CHECK (observation_count BETWEEN 0 AND 4096),
            CHECK (
                (coverage_state='no_observation' AND observation_count=0)
                OR (coverage_state='observed' AND observation_count>=1)
            )
        )
        """
    )
    for table in (
        "portfolio_finance_usage_observations",
        "portfolio_finance_cost_observations",
    ):
        connection.execute(
            f"""
            CREATE TABLE {table} (
                organization_id TEXT NOT NULL,
                observation_id TEXT NOT NULL,
                snapshot_id TEXT NOT NULL,
                bucket_start_at TEXT NOT NULL,
                bucket_end_at TEXT NOT NULL,
                PRIMARY KEY (organization_id, observation_id),
                FOREIGN KEY (organization_id, snapshot_id)
                    REFERENCES portfolio_finance_snapshots
                        (organization_id, snapshot_id)
            )
            """
        )
    for table, identities in SQLITE_APPEND_ONLY_IDENTITIES.items():
        conflict = " OR ".join(f"({identity})" for identity in identities)
        for action in ("UPDATE", "DELETE"):
            connection.execute(
                f"CREATE TRIGGER {table}_no_{action.lower()} "
                f"BEFORE {action} ON {table} "
                "BEGIN SELECT RAISE(ABORT, "
                "'finance_collection_append_only'); END"
            )
        connection.execute(
            f"CREATE TRIGGER {table}_no_replace "
            f"BEFORE INSERT ON {table} "
            f"WHEN EXISTS (SELECT 1 FROM {table} existing "
            f"WHERE existing.organization_id=NEW.organization_id "
            f"AND ({conflict})) "
            "BEGIN SELECT RAISE(ABORT, "
            "'finance_collection_replace_refused'); END"
        )
    for statement in (
        "DROP TRIGGER gateway_audit_chain_entries_no_update",
        "DROP TRIGGER gateway_audit_chain_entries_no_delete",
        "DROP TRIGGER gateway_finance_attempt_audit_source_required",
        "DROP INDEX idx_gateway_audit_chain_entries_event",
        "DROP INDEX idx_gateway_audit_chain_entries_source_identity",
    ):
        connection.execute(statement)
    connection.execute(
        """
        CREATE TABLE gateway_audit_chain_entries_v3 (
            organization_id TEXT NOT NULL,
            chain_version INTEGER NOT NULL,
            chain_epoch INTEGER NOT NULL,
            sequence INTEGER NOT NULL,
            entry_schema_id TEXT NOT NULL,
            entry_schema_version INTEGER NOT NULL,
            event_id TEXT NOT NULL,
            previous_digest TEXT,
            event_digest TEXT NOT NULL,
            event_json TEXT NOT NULL,
            appended_at TEXT NOT NULL,
            source_schema_id TEXT,
            source_schema_version INTEGER,
            source_event_id TEXT,
            PRIMARY KEY (organization_id, chain_epoch, sequence),
            UNIQUE (organization_id, event_id),
            FOREIGN KEY (organization_id, chain_epoch)
                REFERENCES gateway_audit_chain_epochs
                    (organization_id, chain_epoch),
            CHECK (chain_version = 1),
            CHECK (chain_epoch >= 1),
            CHECK (sequence >= 1),
            CHECK (entry_schema_id = 'hormuz.commit-audit-chain-entry'),
            CHECK (entry_schema_version IN (1,2)),
            CHECK (
                (entry_schema_version = 1 AND source_schema_id IS NULL AND source_schema_version IS NULL AND source_event_id IS NULL)
                OR (
                    entry_schema_version = 2 AND source_event_id IS NOT NULL AND (
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
            )
        )
        """
    )
    connection.execute(
        """
        INSERT INTO gateway_audit_chain_entries_v3 (
            organization_id, chain_version, chain_epoch, sequence,
            entry_schema_id, entry_schema_version, event_id, previous_digest,
            event_digest, event_json, appended_at, source_schema_id,
            source_schema_version, source_event_id
        )
        SELECT organization_id, chain_version, chain_epoch, sequence,
               entry_schema_id, entry_schema_version, event_id, previous_digest,
               event_digest, event_json, appended_at, source_schema_id,
               source_schema_version, source_event_id
        FROM gateway_audit_chain_entries
        """
    )
    connection.execute("DROP TABLE gateway_audit_chain_entries")
    connection.execute(
        "ALTER TABLE gateway_audit_chain_entries_v3 "
        "RENAME TO gateway_audit_chain_entries"
    )
    for statement in (
        "CREATE INDEX idx_gateway_audit_chain_entries_event ON gateway_audit_chain_entries (organization_id, event_id)",
        "CREATE UNIQUE INDEX idx_gateway_audit_chain_entries_source_identity ON gateway_audit_chain_entries (organization_id, source_schema_id, source_schema_version, source_event_id) WHERE entry_schema_version = 2",
        "CREATE TRIGGER gateway_audit_chain_entries_no_update BEFORE UPDATE ON gateway_audit_chain_entries BEGIN SELECT RAISE(ABORT, 'audit_chain_entry_immutable'); END",
        "CREATE TRIGGER gateway_audit_chain_entries_no_delete BEFORE DELETE ON gateway_audit_chain_entries BEGIN SELECT RAISE(ABORT, 'audit_chain_entry_immutable'); END",
        "CREATE TRIGGER gateway_finance_attempt_audit_source_required BEFORE INSERT ON gateway_audit_chain_entries WHEN NEW.entry_schema_version=2 AND NEW.source_schema_id='hormuz.finance-attempt-evidence' AND NOT EXISTS (SELECT 1 FROM gateway_finance_attempt_evidence f WHERE f.organization_id=NEW.organization_id AND f.evidence_event_id=NEW.source_event_id AND f.evidence_json=NEW.event_json AND NEW.event_id=NEW.source_event_id) BEGIN SELECT RAISE(ABORT, 'finance_attempt_audit_source_missing'); END",
        """CREATE TRIGGER gateway_finance_collection_audit_source_required
        BEFORE INSERT ON gateway_audit_chain_entries
        WHEN NEW.entry_schema_version=2
          AND NEW.source_schema_id IN (
              'hormuz.finance-source-binding-version',
              'hormuz.finance-collection-event',
              'hormuz.finance-snapshot'
          )
          AND NOT (
              NEW.event_id=NEW.source_event_id AND (
                  (
                      NEW.source_schema_id='hormuz.finance-source-binding-version'
                      AND EXISTS (
                          SELECT 1 FROM portfolio_finance_source_binding_versions source
                          WHERE source.organization_id=NEW.organization_id
                            AND source.binding_event_id=NEW.source_event_id
                            AND source.evidence_json=NEW.event_json
                      )
                  ) OR (
                      NEW.source_schema_id='hormuz.finance-collection-event'
                      AND EXISTS (
                          SELECT 1 FROM portfolio_finance_collection_events source
                          WHERE source.organization_id=NEW.organization_id
                            AND source.event_id=NEW.source_event_id
                            AND source.evidence_json=NEW.event_json
                      )
                  ) OR (
                      NEW.source_schema_id='hormuz.finance-snapshot'
                      AND EXISTS (
                          SELECT 1 FROM portfolio_finance_snapshots source
                          WHERE source.organization_id=NEW.organization_id
                            AND source.snapshot_id=NEW.source_event_id
                            AND source.evidence_json=NEW.event_json
                      )
                  )
              )
          )
        BEGIN
            SELECT RAISE(ABORT, 'finance_collection_audit_source_missing');
        END""",
    ):
        connection.execute(statement)
    if fail:
        raise RuntimeError("synthetic_finance_collection_migration_failure")


class SQLiteFinanceCollectionTransitionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "usage.sqlite3"
        self.assertEqual(UsageStore.schema_version, 12)
        # Build the exact v11 predecessor with the still-supported migration
        # code, then let the current binary perform the real 11-to-12 step.
        with (
            mock.patch.object(UsageStore, "schema_version", 11),
            mock.patch("hormuz._portfolio_sql.SQLITE_SCHEMA_VERSION", 11),
        ):
            self.seeded = seed_sqlite_collection_predecessor(self.path)
        self.before = sqlite_snapshot(self.path)

    def assert_audit_transition(self, snapshot):
        objects = {
            (kind, name): sql
            for kind, name, _, sql in snapshot["objects"]
        }
        audit_table = objects[("table", "gateway_audit_chain_entries")]
        for source_schema in AUDIT_SOURCE_SCHEMAS:
            self.assertIn(source_schema, audit_table)
        for object_key in (
            ("index", "idx_gateway_audit_chain_entries_event"),
            ("index", "idx_gateway_audit_chain_entries_source_identity"),
            ("trigger", "gateway_audit_chain_entries_no_update"),
            ("trigger", "gateway_audit_chain_entries_no_delete"),
            ("trigger", "gateway_finance_attempt_audit_source_required"),
            ("trigger", "gateway_finance_collection_audit_source_required"),
        ):
            self.assertIn(object_key, objects)
        for table in PLANNED_TABLES:
            for suffix in ("no_update", "no_delete", "no_replace"):
                self.assertIn(("trigger", f"{table}_{suffix}"), objects)
        self.assertEqual(
            snapshot["rows"]["gateway_audit_chain_entries"],
            self.before["rows"]["gateway_audit_chain_entries"],
        )
        for table in (
            "portfolio_finance_usage_observations",
            "portfolio_finance_cost_observations",
        ):
            table_sql = objects[("table", table)]
            self.assertIn("bucket_start_at", table_sql)
            self.assertIn("bucket_end_at", table_sql)

    def probe(self, *, fail=False, path=None):
        target = path or self.path
        original = UsageStore._apply_migration

        def apply(connection, version):
            original(connection, version)
            if version == 12 and fail:
                raise RuntimeError("synthetic_finance_collection_migration_failure")

        with mock.patch.object(UsageStore, "_apply_migration", side_effect=apply):
            UsageStore(target).verify_ready()

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
        original = UsageStore._apply_migration

        def missing(connection, version):
            if version == 12:
                raise StorageSchemaError("storage_schema_migration_unsupported")
            original(connection, version)

        with mock.patch.object(UsageStore, "_apply_migration", side_effect=missing):
            with self.assertRaises(StorageSchemaError) as caught:
                UsageStore(self.path)
        self.assertEqual(caught.exception.code, "storage_schema_migration_unsupported")
        self.assertEqual(sqlite_snapshot(self.path), self.before)

    def test_real_ddl_failure_rolls_back_and_retry_is_idempotent(self):
        with self.assertRaisesRegex(
            RuntimeError,
            "synthetic_finance_collection_migration_failure",
        ):
            self.probe(fail=True)
        self.assertEqual(sqlite_snapshot(self.path), self.before)
        self.probe()
        after = sqlite_snapshot(self.path)
        for table in PLANNED_TABLES:
            self.assertEqual(after["rows"][table], [])
        self.assert_audit_transition(after)
        self.probe()
        self.assertEqual(sqlite_snapshot(self.path), after)

    def test_audit_source_guard_requires_exact_source_identity_and_json(self):
        self.probe()
        with managed_sqlite_connection(self.path) as connection:
            for source_schema, event_id, evidence_json in (
                _seed_synthetic_collection_rows(connection)
            ):
                _append_synthetic_audit_entry(
                    connection,
                    source_schema_id=source_schema,
                    source_event_id=event_id,
                    evidence_json=evidence_json,
                )
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "finance_collection_audit_source_missing",
            ):
                _append_synthetic_audit_entry(
                    connection,
                    source_schema_id="hormuz.finance-snapshot",
                    source_event_id=SNAPSHOT_ID,
                    evidence_json='{"kind":"wrong"}',
                )
            with self.assertRaises(sqlite3.IntegrityError):
                _append_synthetic_audit_entry(
                    connection,
                    source_schema_id="hormuz.finance-unsupported",
                    source_event_id="unsupported-event",
                    evidence_json="{}",
                )
            observed = connection.execute(
                "SELECT source_schema_id FROM gateway_audit_chain_entries "
                "WHERE source_schema_id IN (?, ?, ?) ORDER BY source_schema_id",
                AUDIT_SOURCE_SCHEMAS,
            ).fetchall()
            self.assertEqual(
                [row[0] for row in observed],
                sorted(AUDIT_SOURCE_SCHEMAS),
            )

    def test_collection_tables_reject_update_delete_and_replace(self):
        self.probe()
        with managed_sqlite_connection(self.path) as connection:
            for source_schema, event_id, evidence_json in (
                _seed_synthetic_collection_rows(connection)
            ):
                _append_synthetic_audit_entry(
                    connection,
                    source_schema_id=source_schema,
                    source_event_id=event_id,
                    evidence_json=evidence_json,
                )
            for table in PLANNED_TABLES:
                with self.subTest(table=table, mutation="update"):
                    with self.assertRaisesRegex(
                        sqlite3.IntegrityError,
                        "finance_collection_append_only",
                    ):
                        connection.execute(
                            f"UPDATE {table} SET organization_id=organization_id"
                        )
                with self.subTest(table=table, mutation="delete"):
                    with self.assertRaisesRegex(
                        sqlite3.IntegrityError,
                        "finance_collection_append_only",
                    ):
                        connection.execute(f"DELETE FROM {table}")
                with self.subTest(table=table, mutation="replace"):
                    with self.assertRaisesRegex(
                        sqlite3.IntegrityError,
                        "finance_collection_replace_refused",
                    ):
                        connection.execute(
                            f"INSERT OR REPLACE INTO {table} "
                            f"SELECT * FROM {table} LIMIT 1"
                        )

    def test_empty_bucket_coverage_is_durable_without_numeric_zero(self):
        self.probe()
        with managed_sqlite_connection(self.path) as connection:
            _seed_synthetic_collection_rows(connection)
            coverage = connection.execute(
                "SELECT coverage_state, observation_count "
                "FROM portfolio_finance_snapshot_bucket_coverage "
                "WHERE organization_id=? AND coverage_id=?",
                ("acme", "coverage-empty".ljust(36, "0")),
            ).fetchone()
            self.assertEqual(coverage, ("no_observation", 0))
            for table in (
                "portfolio_finance_usage_observations",
                "portfolio_finance_cost_observations",
            ):
                observed = connection.execute(
                    f"SELECT COUNT(*) FROM {table} "
                    "WHERE organization_id=? AND bucket_start_at=? "
                    "AND bucket_end_at=?",
                    (
                        "acme",
                        "2026-08-31T00:00:00Z",
                        "2026-09-01T00:00:00Z",
                    ),
                ).fetchone()[0]
                self.assertEqual(observed, 0)

    def test_current_binary_refuses_newer_and_partial_state_without_repair(self):
        self.probe()
        candidate = sqlite_snapshot(self.path)
        with mock.patch.object(UsageStore, "schema_version", 11):
            for read_only in (False, True):
                with self.subTest(read_only=read_only), self.assertRaises(
                    StorageSchemaError
                ) as caught:
                    UsageStore(self.path, read_only=read_only)
                self.assertEqual(caught.exception.code, "storage_schema_newer_than_binary")
                self.assertEqual(sqlite_snapshot(self.path), candidate)

        with managed_sqlite_connection(self.path) as connection:
            connection.execute(
                "UPDATE hormuz_schema_migrations SET state='applying' "
                "WHERE version=12"
            )
        partial = sqlite_snapshot(self.path)
        for read_only in (False, True):
            with self.subTest(partial=True, read_only=read_only), self.assertRaises(
                StorageSchemaError
            ) as caught:
                UsageStore(self.path, read_only=read_only)
            self.assertEqual(caught.exception.code, "storage_schema_partial_upgrade")
            self.assertEqual(sqlite_snapshot(self.path), partial)

    def test_quiesced_old_pair_restore_preserves_every_accepted_byte(self):
        backup = self.root / "accepted-main-checkpoint.sqlite3"
        sqlite_backup(self.path, backup)
        self.probe()
        retained = sqlite_snapshot(self.path)
        restored = self.root / "restored-accepted-main.sqlite3"
        shutil.copyfile(backup, restored)
        with mock.patch.object(UsageStore, "schema_version", 11):
            UsageStore(restored, read_only=True).verify_ready()
        self.assertEqual(sqlite_snapshot(restored), self.before)
        self.assertEqual(sqlite_snapshot(self.path), retained)

    def test_post_checkpoint_write_requires_retained_forward_recovery(self):
        self.probe()
        with managed_sqlite_connection(self.path) as connection:
            _seed_synthetic_collection_rows(connection)
        after_write = sqlite_snapshot(self.path)
        retained = self.root / "retained-candidate.sqlite3"
        recovered = self.root / "forward-recovered.sqlite3"
        sqlite_backup(self.path, retained)
        sqlite_backup(retained, recovered)
        UsageStore(recovered, read_only=True).verify_ready()
        self.assertEqual(sqlite_snapshot(recovered), after_write)
        with mock.patch.object(UsageStore, "schema_version", 11), self.assertRaises(
            StorageSchemaError
        ) as caught:
            UsageStore(recovered, read_only=True)
        self.assertEqual(caught.exception.code, "storage_schema_newer_than_binary")


@unittest.skipUnless(
    os.environ.get("HORMUZ_TEST_FINANCE_COLLECTION_PYTHON"),
    "requires digest-pinned accepted native-finance predecessor",
)
class SQLiteFinanceCollectionExactPredecessorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "usage.sqlite3"
        # Build the exact v11 predecessor database before the installed
        # predecessor driver verifies it.  Without this patch the candidate
        # schema version (12) is used while seeding, so the supposedly exact
        # v11 archive correctly refuses the database as newer-than-binary.
        with (
            mock.patch.object(UsageStore, "schema_version", 11),
            mock.patch("hormuz._portfolio_sql.SQLITE_SCHEMA_VERSION", 11),
        ):
            seed_sqlite_collection_predecessor(self.path)

    def test_exact_predecessor_accepts_current_and_refuses_newer_and_partial(self):
        request = {"backend": "sqlite", "path": str(self.path)}
        self.assertEqual(
            finance_collection_predecessor_call(request),
            {"status": "ready", "runtime_files_verified": 145},
        )
        SQLiteFinanceCollectionTransitionTests.probe(self)
        self.assertEqual(
            finance_collection_predecessor_call(request),
            {"status": "refused", "code": "storage_schema_newer_than_binary"},
        )
        with managed_sqlite_connection(self.path) as connection:
            connection.execute(
                "UPDATE hormuz_schema_migrations SET state='applying' "
                "WHERE version=12"
            )
        self.assertEqual(
            finance_collection_predecessor_call(request),
            {"status": "refused", "code": "storage_schema_partial_upgrade"},
        )


if __name__ == "__main__":
    unittest.main()
