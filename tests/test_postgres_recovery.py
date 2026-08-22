from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools import verify_postgres_backup_restore as recovery


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "recovery"
POSTGRES_IMAGE = "postgres@sha256:" + ("b" * 64)


class PostgresRecoverySummaryTests(unittest.TestCase):
    def _state(self) -> dict[str, object]:
        return json.loads((FIXTURES / "state-v1.json").read_text(encoding="utf-8"))

    def test_compatibility_fixtures_validate(self) -> None:
        state = self._state()
        summary = json.loads((FIXTURES / "summary-v1.json").read_text(encoding="utf-8"))

        recovery._validate_state(state)
        recovery._validate_summary(summary)

    def test_summary_is_content_free_and_binds_source_to_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            dump = Path(temporary) / "hormuz.dump"
            dump.write_bytes(b"fixed custom-format test backup")
            state = self._state()

            summary = recovery.build_recovery_summary(
                database_image=POSTGRES_IMAGE,
                database_version="16.14",
                dump_path=dump,
                source_state=state,
                recovery_state=dict(state),
                negative_checks={
                    "missing_dump_rejected": True,
                    "corrupt_dump_rejected": True,
                    "partial_recovery_not_promoted": True,
                    "state_fingerprint_matches": True,
                },
                durations_ms={
                    "migrate_and_seed": 10,
                    "backup": 20,
                    "restore": 30,
                    "verify": 40,
                    "total": 100,
                },
            )

        recovery._validate_summary(summary)
        encoded = json.dumps(summary, sort_keys=True)
        self.assertEqual(summary["coverage"], "ephemeral_logical_backup_restore_only")
        self.assertEqual(summary["state"]["source_fingerprint"], summary["state"]["recovery_fingerprint"])
        self.assertNotIn("acme", encoded)
        self.assertNotIn("postgresql://", encoded)
        self.assertNotIn("token", encoded)

    def test_missing_backup_and_mismatched_recovery_state_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "missing.dump"
            with self.assertRaisesRegex(recovery.RecoveryDrillError, "backup_missing"):
                recovery._backup_metadata(missing)

        with tempfile.TemporaryDirectory() as temporary:
            source = self._state()
            mismatched_path = Path(temporary) / "mismatched-state.json"
            recovery.make_mismatched_state(source, mismatched_path)
            mismatched = json.loads(mismatched_path.read_text(encoding="utf-8"))

            with self.assertRaisesRegex(recovery.RecoveryDrillError, "fingerprint_mismatch"):
                recovery._require_matching_state(source, mismatched)
            recovery.assert_mismatch_is_rejected(source)

    def test_invalid_or_partial_evidence_cannot_produce_a_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            dump = Path(temporary) / "hormuz.dump"
            dump.write_bytes(b"fixed custom-format test backup")
            state = self._state()
            partial = dict(state)
            partial["checks"] = {"migration_ledger": True}

            with self.assertRaisesRegex(recovery.RecoveryDrillError, "state_schema_invalid"):
                recovery.build_recovery_summary(
                    database_image=POSTGRES_IMAGE,
                    database_version="16.14",
                    dump_path=dump,
                    source_state=partial,
                    recovery_state=state,
                    negative_checks={
                        "missing_dump_rejected": True,
                        "corrupt_dump_rejected": True,
                        "partial_recovery_not_promoted": True,
                        "state_fingerprint_matches": True,
                    },
                    durations_ms={
                        "migrate_and_seed": 0,
                        "backup": 0,
                        "restore": 0,
                        "verify": 0,
                        "total": 0,
                    },
                )

            with self.assertRaisesRegex(recovery.RecoveryDrillError, "negative_check_missing"):
                recovery.build_recovery_summary(
                    database_image=POSTGRES_IMAGE,
                    database_version="16.14",
                    dump_path=dump,
                    source_state=state,
                    recovery_state=state,
                    negative_checks={
                        "missing_dump_rejected": True,
                        "corrupt_dump_rejected": True,
                        "partial_recovery_not_promoted": False,
                        "state_fingerprint_matches": True,
                    },
                    durations_ms={
                        "migrate_and_seed": 0,
                        "backup": 0,
                        "restore": 0,
                        "verify": 0,
                        "total": 0,
                    },
                )

    def test_corrupt_copy_is_smaller_than_the_valid_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "hormuz.dump"
            corrupt = root / "hormuz.corrupt.dump"
            source.write_bytes(b"fixed custom-format test backup" * 16)

            recovery.make_corrupt_copy(source, corrupt)

            self.assertGreater(source.stat().st_size, corrupt.stat().st_size)
            self.assertGreater(corrupt.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
