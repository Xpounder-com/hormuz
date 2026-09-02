from __future__ import annotations

from pathlib import Path
from dataclasses import replace
import sqlite3
import tempfile
import unittest

from hormuz._outcome_schema import TABLE_DDL
from hormuz.outcome_repository import OutcomeRepository
from hormuz.store import UsageStore
if __package__:
    from ._sqlite import managed_sqlite_connection
else:
    from _sqlite import managed_sqlite_connection

if __package__:
    from ._outcome_fixture import OutcomeAssertions
    from ._portfolio_fixture import registry_config
    from ._registry_transition_fixture import sqlite_snapshot
else:
    from _outcome_fixture import OutcomeAssertions
    from _portfolio_fixture import registry_config
    from _registry_transition_fixture import sqlite_snapshot


class SQLiteOutcomeTests(OutcomeAssertions, unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.config = registry_config(Path(temporary.name))
        self.environment = None
        self.store = UsageStore(self.config.database_path)
        self.setup_outcomes()

    def outcome_rows(self):
        return {name: rows for name, rows in sqlite_snapshot(self.config.database_path)["rows"].items() if name in TABLE_DDL}

    def legacy_rows(self):
        return {name: rows for name, rows in sqlite_snapshot(self.config.database_path)["rows"].items() if name not in TABLE_DDL}

    def test_sqlite_outcome_metadata_replay_and_rotation(self):
        self.check_atomic_metadata_receipt_replay_and_rotation()

    def test_sqlite_outcome_ordering_corrections_and_tombstones(self):
        self.check_ordering_uncertainty_and_corrections_never_rewrite_facts()

    def test_sqlite_outcome_batch_ordering_and_unsupported(self):
        self.check_batch_ordering_and_unsupported_do_not_replace_authoritative_state()

    def test_sqlite_outcome_historical_scope(self):
        self.check_historical_binding_and_missing_source_time()

    def test_sqlite_outcome_atomicity_and_audit(self):
        self.check_atomic_failure_and_no_read_before_audit()

    def test_sqlite_outcome_failed_delivery(self):
        self.check_failed_delivery_is_bounded_content_free_and_not_success()

    def test_sqlite_outcome_repository_rejection_coverage(self):
        self.check_repository_rejections_have_durable_failure_coverage()

    def test_sqlite_outcome_corrupt_cursors(self):
        self.check_corrupt_cursors_fail_without_audit_or_partial_pages()

    def test_sqlite_outcome_outage(self):
        path = self.config.database_path.with_name("absent-outcome-database.sqlite3")
        self.check_storage_outage_never_accepts_or_retries(OutcomeRepository(replace(self.config, database_path=path), dsn=""))
        self.assertFalse(path.exists())

    def test_sqlite_outcome_connector_ties_and_cursor_authority(self):
        self.check_connector_ties_and_config_or_role_changes()

    def test_sqlite_outcome_authorized_retention(self):
        self.check_authorized_retention_is_separate_append_only_and_invalidates_cursors()

    def test_sqlite_outcome_retention_replay_integrity(self):
        self.check_retention_replay_rejects_corrupt_mac_and_supports_rotation()

    def test_sqlite_outcome_authorization(self):
        self.check_authorization_and_tenant_isolation_before_lookup()

    def test_sqlite_outcome_concurrency_and_cursors(self):
        self.check_concurrent_replicas_and_frozen_pagination()

    def test_sqlite_outcome_mixed_normalizer_race(self):
        self.check_mixed_normalizer_race_returns_the_winning_receipt()

    def test_sqlite_outcome_immutable_tables(self):
        self.ingest()
        self.ingest([self.source(source_revision="5", revision_order="5")])
        page = self.page("limit=1")
        self.repository.tombstone(self.principal, "github-one", page["items"][0]["source_event_id"], idempotency_key="immutable-retention", keys=self.keys)
        self.error("invalid_request", lambda: self.ingest(raw=b'{"observations":[{}]}'))
        self.assertTrue(all(self.outcome_rows()[table] for table in TABLE_DDL))
        with managed_sqlite_connection(self.config.database_path) as connection:
            for table in TABLE_DDL:
                with self.assertRaisesRegex(sqlite3.IntegrityError, "portfolio_append_only"):
                    connection.execute(f"DELETE FROM {table}")


if __name__ == "__main__":
    unittest.main()
