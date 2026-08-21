from __future__ import annotations

import tempfile
from pathlib import Path
import sqlite3
import unittest

from hormuz.audit_access import AuditAccessError, authorize_audit_read
from hormuz.config import Identity, _identity_capabilities
from hormuz.store import SecurityStoreError, UsageStore


def _identity(*capabilities: str) -> Identity:
    return Identity(
        token_env="TEST_TOKEN",
        token="test-token-not-used",
        actor_id="auditor",
        actor_name="Auditor",
        team_id="security",
        team_name="Security",
        organization_id="xpounder",
        capabilities=capabilities,
    )


class AuditAccessTests(unittest.TestCase):
    def test_capability_is_independent_and_configuration_accepts_it(self) -> None:
        authorize_audit_read(_identity("audit_viewer"))
        with self.assertRaisesRegex(AuditAccessError, "audit_viewer_capability_required"):
            authorize_audit_read(_identity("policy_admin"))
        self.assertEqual(
            _identity_capabilities(["audit_viewer"], "identities[0].capabilities"),
            ("audit_viewer",),
        )

    def test_sqlite_audit_reads_are_tenant_scoped_and_self_audited(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = UsageStore(Path(temporary) / "usage.sqlite3")
            event_id = store.record_admin_audit_read(
                administrator=_identity("audit_viewer"),
                kind="security",
                window_start="2026-08-01T00:00:00+00:00",
                window_end="2026-08-01T00:01:00+00:00",
                result_count=0,
            )
            events = store.audit_events(
                since="2000-01-01T00:00:00+00:00",
                kind="security",
                organization_id="xpounder",
            )
            self.assertEqual(
                [event["event_type"] for event in events],
                ["security.admin.audit_read"],
            )
            self.assertEqual(events[0]["id"], event_id)
            self.assertEqual(events[0]["decision_actor_id"], "auditor")
            self.assertEqual(events[0]["group_by"], "security")
            self.assertEqual(
                store.audit_events(
                    since="2000-01-01T00:00:00+00:00",
                    kind="security",
                    organization_id="other-company",
                ),
                [],
            )
            with self.assertRaisesRegex(SecurityStoreError, "audit_viewer_capability_required"):
                store.record_admin_audit_read(
                    administrator=_identity("policy_admin"),
                    kind="security",
                    window_start="2026-08-01T00:00:00+00:00",
                    window_end="2026-08-01T00:01:00+00:00",
                    result_count=0,
                )

    def test_legacy_sqlite_admin_access_schema_migrates_before_an_audit_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "usage.sqlite3"
            connection = sqlite3.connect(path)
            connection.execute(
                """
                CREATE TABLE gateway_admin_access_events (
                    id TEXT PRIMARY KEY,
                    occurred_at TEXT NOT NULL,
                    organization_id TEXT NOT NULL,
                    decision_actor_id TEXT NOT NULL,
                    decision_actor_name TEXT NOT NULL,
                    action TEXT NOT NULL CHECK (action = 'usage.report.read'),
                    group_by TEXT NOT NULL,
                    actor_filter_sha256 TEXT,
                    team_filter_sha256 TEXT,
                    window_start TEXT NOT NULL,
                    window_end TEXT NOT NULL,
                    result_count INTEGER NOT NULL CHECK (result_count >= 0)
                )
                """
            )
            connection.commit()
            connection.close()

            store = UsageStore(path)
            event_id = store.record_admin_audit_read(
                administrator=_identity("audit_viewer"),
                kind="all",
                window_start="2026-08-01T00:00:00+00:00",
                window_end="2026-08-01T00:01:00+00:00",
                result_count=1,
            )
            self.assertTrue(event_id)
            connection = sqlite3.connect(path)
            schema = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
                ("gateway_admin_access_events",),
            ).fetchone()[0]
            connection.close()
            self.assertIn("audit.events.read", schema)


if __name__ == "__main__":
    unittest.main()
