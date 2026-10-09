"""Whole-state quiescent backups and closed atomic recovery with unknown holds retained."""
from contextlib import closing
from contextlib import redirect_stdout
import base64
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from hormuz._hosted_backup import export_backup, restore_backup, verify_backup
from hormuz._hosted_config import HostedError, at_directory
from hormuz._hosted_state import initialize, snapshot
from hormuz.config import AIWorkConfig, Identity, ModelRoute, UsageStorageConfig
from hormuz.work_billing import WorkBilling
from hormuz.work_recovery import owner_lock
from hormuz.work_runtime import WorkRuntime
from tests._hosted_fixtures import profile


class WorkRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        config, _, _ = profile(self.root)
        initialize(config)
        self.config = replace(config, ai_work=AIWorkConfig(enabled=True,
            database_path=config.database_path.parent / "hormuz-work.sqlite3", billing_price_id="price_Approved"))
        self.runtime = WorkRuntime(self.config.ai_work.database_path, self.config)
        self.addCleanup(self.runtime.close)
        self.billing = WorkBilling(self.runtime.path.with_suffix(".billing.sqlite3"), "price_Approved", [("company", "cus_Approved", "sub_Approved")], "whsec_synthetic_not_real")
        self.identity = Identity("", "", "owner", "Owner", "team", "Team", "company")
        self.work = self.runtime.create_work(self.identity, "fixture/repository")["work_id"]
        self.runtime.reserve(self.identity, self.work, "request-fixture", "model-fixture", "openai", 500)
        self.archive = self.root / "archive.hzb"
        self.key = b"b" * 32

    def test_v2_archive_contains_all_owned_stores_and_restores_closed_without_releasing_hold(self):
        summary = export_backup(self.config, self.archive, self.key)
        self.assertEqual(summary, verify_backup(self.archive, self.key))
        recovered = at_directory(self.config, self.root / "recovered")
        result = restore_backup(recovered, self.archive, self.key)
        self.assertTrue(result["recovered_closed"])
        restored = WorkRuntime(recovered.ai_work.database_path, recovered)
        self.addCleanup(restored.close)
        job = restored.get_work(self.identity, self.work)
        self.assertEqual("paused", job["state"])
        self.assertEqual("unknown", job["attempts"][0]["state"])
        self.assertEqual(500, job["costs"]["uncertain_microusd"])
        billing = WorkBilling(restored.path.with_suffix(".billing.sqlite3"), "price_Approved", [("company", "cus_Approved", "sub_Approved")], "whsec_synthetic_not_real")
        self.assertFalse(billing.entitled("company"))
        with billing._connect() as connection:
            self.assertGreater(connection.execute("SELECT recovery_after FROM work_entitlements").fetchone()[0], 0)

    def test_live_gateway_owner_blocks_snapshot(self):
        with owner_lock(self.runtime.path):
            with self.assertRaisesRegex(HostedError, "owner_active"):
                snapshot(self.config, self.root / "blocked")

    def test_failure_before_publish_leaves_no_partial_destination(self):
        export_backup(self.config, self.archive, self.key)
        recovered = at_directory(self.config, self.root / "recovered")
        with patch("hormuz._hosted_state._write", side_effect=OSError("synthetic final write failure")):
            with self.assertRaises(OSError):
                restore_backup(recovered, self.archive, self.key)
        self.assertFalse(recovered.database_path.parent.exists())
        # Retry the same validated archive can publish a complete state.
        self.assertTrue(restore_backup(recovered, self.archive, self.key)["recovered_closed"])

    def test_postgres_mode_cannot_silently_backup_dormant_sqlite_usage(self):
        postgres = replace(self.config, usage_storage=UsageStorageConfig(backend="postgresql"))
        with self.assertRaisesRegex(HostedError, "requires_sqlite"):
            export_backup(postgres, self.archive, self.key)
        self.assertFalse(self.archive.exists())

    def test_restore_closes_pending_workflow_events_and_fences_old_source_time(self):
        from hormuz.work_workflow import WorkWorkflow
        WorkWorkflow(self.runtime, self.config)
        with self.runtime._transaction(write=True) as connection:
            connection.execute("INSERT INTO ai_work_bindings(binding_id,organization_id,actor_id,work_id,provider,connector_id,object_digest,container_digest,completion_condition,created_at,latest_event_at) VALUES('binding-fixture','company','owner',?,'github','connector-fixture','object-digest','container-digest','github.pull_request.merged.v1',1,2)", (self.work,))
            connection.execute("INSERT INTO ai_work_bridge_events(event_digest,delivery_digest,organization_id,connector_id,provider,object_digest,container_digest,event_type,object_type,quality_state,event_at,created_at) VALUES('event-digest','delivery-digest','company','connector-fixture','github','object-digest','container-digest','completed','pull_request_lifecycle','accepted',3,3)")
        export_backup(self.config, self.archive, self.key)
        recovered = at_directory(self.config, self.root / "recovered")
        restore_backup(recovered, self.archive, self.key)
        with closing(sqlite3.connect(recovered.ai_work.database_path)) as connection:
            self.assertEqual(("processed", "recovery_closed"), connection.execute("SELECT status,reason FROM ai_work_bridge_events").fetchone())
            self.assertGreater(connection.execute("SELECT latest_event_at FROM ai_work_bindings").fetchone()[0], 3)

    def test_actual_offline_gateway_profile_cli_captures_and_recovers_all_sqlite_owners(self):
        from hormuz.hosted import main
        from tests._hosted_fixtures import provider_profile
        _, _, settings, document = provider_profile(self.root)
        document["usage_storage"] = {"backend": "sqlite"}
        document["ai_work"] = {"enabled": True, "database": str(self.runtime.path), "billing_price_id": "price_Approved"}
        source = self.root / "full-gateway.json"
        source.write_text(json.dumps(document))
        source.chmod(0o600)
        key_file = self.root / "backup-key"
        key_file.write_bytes(base64.b64encode(self.key) + b"\n")
        key_file.chmod(0o600)
        args = ["--config", str(source), "--gateway-profile"]
        with patch.dict(os.environ, settings, clear=True), redirect_stdout(io.StringIO()):
            self.assertEqual(0, main([*args, "snapshot", "--output-directory", str(self.root / "snapshot")]))
            self.assertEqual(0, main([*args, "backup-export", "--key-file", str(key_file), "--output-file", str(self.archive)]))
            self.assertEqual(0, main([*args, "backup-verify", "--key-file", str(key_file), "--archive-file", str(self.archive)]))
            recovered = at_directory(self.config, self.root / "recovered")
            document["database"] = str(recovered.database_path)
            document["authentication"]["session_broker"]["database"] = str(recovered.session_broker.database_path)
            document["ai_work"]["database"] = str(recovered.ai_work.database_path)
            source.write_text(json.dumps(document))
            self.assertEqual(0, main([*args, "backup-restore", "--key-file", str(key_file), "--archive-file", str(self.archive)]))
            self.assertEqual(0, main([*args, "recovery-check"]))
        self.assertTrue((self.root / "snapshot" / "hormuz-work.billing.sqlite3").is_file())
        restored = WorkRuntime(recovered.ai_work.database_path, recovered)
        self.addCleanup(restored.close)
        self.assertEqual(500, restored.get_work(self.identity, self.work)["costs"]["uncertain_microusd"])
