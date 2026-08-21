from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from hormuz.config import ConfigError, Identity, _identity_capabilities
from hormuz.store import SecurityStoreError, UsageStore
from hormuz.usage_access import UsageReportAccessError, authorize_usage_report


def _identity(*capabilities: str) -> Identity:
    return Identity(
        token_env="TEST_TOKEN",
        token="test-token-not-used",
        actor_id="alice",
        actor_name="Alice",
        team_id="engineering",
        team_name="Engineering",
        organization_id="xpounder",
        capabilities=capabilities,
    )


class UsageReportAccessTests(unittest.TestCase):
    def test_organization_scopes_preserve_legacy_and_new_capability_behavior(self) -> None:
        for capability in ("usage_viewer", "usage_organization_viewer"):
            with self.subTest(capability=capability):
                access = authorize_usage_report(
                    _identity(capability),
                    group_by="person",
                    actor_id="bob",
                    team_id="marketing",
                )
                self.assertEqual(access.scope, "organization")
                self.assertEqual(access.actor_id, "bob")
                self.assertEqual(access.team_id, "marketing")

    def test_self_scope_is_forced_to_the_authenticated_person(self) -> None:
        access = authorize_usage_report(
            _identity("usage_self_viewer"),
            group_by="person",
            actor_id=None,
            team_id=None,
        )
        self.assertEqual(access.scope, "self")
        self.assertEqual(access.actor_id, "alice")
        self.assertIsNone(access.team_id)

        with self.assertRaisesRegex(UsageReportAccessError, "usage_report_scope_forbidden"):
            authorize_usage_report(
                _identity("usage_self_viewer"),
                group_by="person",
                actor_id="bob",
                team_id=None,
            )

    def test_team_scope_allows_only_current_team_aggregates(self) -> None:
        access = authorize_usage_report(
            _identity("usage_team_viewer"),
            group_by="model",
            actor_id=None,
            team_id=None,
        )
        self.assertEqual(access.scope, "team")
        self.assertIsNone(access.actor_id)
        self.assertEqual(access.team_id, "engineering")

        for group_by, actor_id, team_id in (
            ("person", None, None),
            ("model", "alice", None),
            ("model", None, "marketing"),
        ):
            with self.subTest(group_by=group_by, actor_id=actor_id, team_id=team_id):
                with self.assertRaisesRegex(
                    UsageReportAccessError,
                    "usage_report_scope_forbidden",
                ):
                    authorize_usage_report(
                        _identity("usage_team_viewer"),
                        group_by=group_by,
                        actor_id=actor_id,
                        team_id=team_id,
                    )

    def test_finance_scope_has_no_actor_or_team_drill_down(self) -> None:
        access = authorize_usage_report(
            _identity("usage_finance_viewer"),
            group_by="provider",
            actor_id=None,
            team_id=None,
        )
        self.assertEqual(access.scope, "finance")
        self.assertIsNone(access.actor_id)
        self.assertIsNone(access.team_id)

        for group_by in ("requested_model", "actual_model", "policy", "status"):
            with self.subTest(allowed_group_by=group_by):
                allowed = authorize_usage_report(
                    _identity("usage_finance_viewer"),
                    group_by=group_by,
                    actor_id=None,
                    team_id=None,
                )
                self.assertEqual(allowed.scope, "finance")

        for group_by, actor_id, team_id in (
            ("person", None, None),
            ("team", None, None),
            ("model", "alice", None),
            ("model", None, "engineering"),
        ):
            with self.subTest(group_by=group_by, actor_id=actor_id, team_id=team_id):
                with self.assertRaisesRegex(
                    UsageReportAccessError,
                    "usage_report_scope_forbidden",
                ):
                    authorize_usage_report(
                        _identity("usage_finance_viewer"),
                        group_by=group_by,
                        actor_id=actor_id,
                        team_id=team_id,
                    )

    def test_missing_or_ambiguous_scope_fails_closed(self) -> None:
        with self.assertRaisesRegex(
            UsageReportAccessError,
            "usage_viewer_capability_required",
        ):
            authorize_usage_report(
                _identity("policy_admin"),
                group_by="organization",
                actor_id=None,
                team_id=None,
            )
        with self.assertRaisesRegex(
            UsageReportAccessError,
            "usage_report_scope_ambiguous",
        ):
            authorize_usage_report(
                _identity("usage_self_viewer", "usage_team_viewer"),
                group_by="organization",
                actor_id=None,
                team_id=None,
            )

    def test_configuration_rejects_mixed_usage_view_scopes(self) -> None:
        self.assertEqual(
            _identity_capabilities(
                ["policy_admin", "usage_self_viewer"],
                "identities[0].capabilities",
            ),
            ("policy_admin", "usage_self_viewer"),
        )
        with self.assertRaisesRegex(
            ConfigError,
            "must select at most one usage reporting scope",
        ):
            _identity_capabilities(
                ["usage_viewer", "usage_finance_viewer"],
                "identities[0].capabilities",
            )

    def test_sqlite_usage_audit_rechecks_the_effective_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = UsageStore(Path(temporary) / "usage.sqlite3")
            manager = _identity("usage_team_viewer")
            event_id = store.record_admin_usage_read(
                administrator=manager,
                access_scope="team",
                group_by="model",
                actor_filter=None,
                team_filter="engineering",
                window_start="2026-08-01T00:00:00+00:00",
                window_end="2026-08-01T00:01:00+00:00",
                result_count=1,
            )
            self.assertTrue(event_id)
            with self.assertRaisesRegex(
                SecurityStoreError,
                "usage_report_scope_forbidden",
            ):
                store.record_admin_usage_read(
                    administrator=manager,
                    access_scope="team",
                    group_by="person",
                    actor_filter=None,
                    team_filter="engineering",
                    window_start="2026-08-01T00:00:00+00:00",
                    window_end="2026-08-01T00:01:00+00:00",
                    result_count=1,
                )
            with self.assertRaisesRegex(
                SecurityStoreError,
                "usage_admin_audit_scope_mismatch",
            ):
                store.record_admin_usage_read(
                    administrator=manager,
                    access_scope="organization",
                    group_by="model",
                    actor_filter=None,
                    team_filter="engineering",
                    window_start="2026-08-01T00:00:00+00:00",
                    window_end="2026-08-01T00:01:00+00:00",
                    result_count=1,
                )


if __name__ == "__main__":
    unittest.main()
