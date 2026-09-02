from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from hormuz._attribution_schema import TABLE_DDL
from hormuz.store import UsageStore
if __package__:
    from ._sqlite import managed_sqlite_connection
else:
    from _sqlite import managed_sqlite_connection

if __package__:
    from ._attribution_fixture import AttributionAssertions
    from ._portfolio_fixture import registry_config
    from ._registry_transition_fixture import sqlite_snapshot
else:
    from _attribution_fixture import AttributionAssertions
    from _portfolio_fixture import registry_config
    from _registry_transition_fixture import sqlite_snapshot


class SQLiteAttributionTests(AttributionAssertions, unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.config = registry_config(Path(temporary.name))
        self.environment = None
        self.store = UsageStore(self.config.database_path)
        self.setup_attribution()

    def attribution_rows(self):
        return {name: rows for name, rows in sqlite_snapshot(self.config.database_path)["rows"].items() if name in TABLE_DDL}

    def v1_rows(self):
        return {name: rows for name, rows in sqlite_snapshot(self.config.database_path)["rows"].items() if name.startswith("gateway_")}

    def test_sqlite_attribution_sources_and_immutable_facts(self):
        self.check_attribution_sources_and_immutable_facts()

    def test_sqlite_attribution_corrections_voids_and_idempotency(self):
        self.check_append_only_corrections_voids_and_idempotency()

    def test_sqlite_attribution_authority_before_lookup_and_join(self):
        self.check_authority_precedes_lookup_and_tenant_join()

    def test_sqlite_attribution_scope_race(self):
        self.check_scope_race_fails_and_never_retargets()

    def test_sqlite_attribution_concurrency(self):
        self.check_admission_and_correction_concurrency()

    def test_sqlite_attribution_atomicity_and_read_audit(self):
        self.check_atomicity_and_audit_before_delivery()

    def test_sqlite_attribution_frozen_pagination(self):
        self.check_frozen_pagination_and_role_bound_cursors()

    def test_sqlite_attribution_rejection_coverage(self):
        self.check_rejections_are_not_fabricated_attempts_or_work_content()

    def test_sqlite_attribution_invalid_requests(self):
        self.check_invalid_requests_cannot_reach_storage()

    def test_sqlite_attribution_append_only_and_attempt_binding(self):
        self.attempt()
        with managed_sqlite_connection(self.config.database_path) as connection:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "portfolio_append_only"):
                connection.execute("DELETE FROM portfolio_attribution_events")
            with self.assertRaisesRegex(sqlite3.IntegrityError, "attribution_attempt_invalid"):
                connection.execute("INSERT INTO portfolio_attribution_events SELECT 'beta','foreign',request_attempt_id,work_scope_id,work_scope_version,confidence,state,supersedes_event_id,actor_id,reason_code,event_at,observed_at,ingested_at,sequence FROM portfolio_attribution_events")


if __name__ == "__main__":
    unittest.main()
