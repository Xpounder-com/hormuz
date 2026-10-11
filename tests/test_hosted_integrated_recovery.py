"""Actual closed restores and independent authority tampering; no network."""

from contextlib import closing, redirect_stderr, redirect_stdout
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from hormuz._hosted_backup import export_backup, restore_backup
from hormuz._hosted_config import HostedError, at_directory
from hormuz._hosted_state import check_recovered_closed, state_lock
from hormuz._hosted_work import initialize, load_profile
from hormuz.config import Identity
from hormuz.hosted import main
from hormuz.work_recovery import BILLING_DATABASE, WORK_DATABASE, owner_lock
from hormuz.work_runtime import WorkRuntime
from tests._hosted_fixtures import provider_profile


@unittest.skipUnless(os.name == "posix", "Hosted recovery uses POSIX owner locks")
class HostedIntegratedRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(logging.disable, logging.root.manager.disable)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        _, _, self.environment, self.document = provider_profile(self.root)
        self.document["usage_storage"] = {"backend": "sqlite"}
        broker = self.document["authentication"]["session_broker"]
        broker.update(workspace_enabled=True, workspace_signup_issuer="https://idp.example.test")
        self.document["ai_work"] = {
            "enabled": True, "database": str(self.root / "state" / WORK_DATABASE),
            "billing_price_id": "price_Reviewed", "require_paid": True,
            "billing_bindings": [{"organization_id": "customer-a", "customer_id": "cus_Reviewed", "subscription_id": "sub_Reviewed"}],
        }
        self.environment.update({
            "HORMUZ_WORK_BILLING_WEBHOOK_SECRET": "whsec_synthetic_recovery_fixture",
            "HORMUZ_WORK_BILLING_API_KEY": "rk_" + "live_" + "A" * 32,
            "HORMUZ_HOSTED_MODE": "maintenance",
        })
        source = self.root / "full-work.json"
        source.write_text(json.dumps(self.document))
        source.chmod(0o600)
        self.config, selected = load_profile(source, self.environment)
        initialize(self.config, selected)
        identity = Identity("", "", "owner", "Owner", "team", "Team", "customer-a")
        runtime = WorkRuntime(self.config.ai_work.database_path, self.config)
        try:
            self.work = runtime.create_work(identity, "fixture/repository")["work_id"]
            runtime.reserve(identity, self.work, "unknown-request", "openai-primary", "openai", 500)
            self.completed = runtime.create_work(identity, "fixture/completed")["work_id"]
            self.stopped = runtime.create_work(identity, "fixture/stopped")["work_id"]
        finally:
            runtime.close()
        self.execute(self.config, WORK_DATABASE,
            "UPDATE ai_work_jobs SET state='completed' WHERE work_id=?", (self.completed,))
        self.execute(self.config, WORK_DATABASE,
            "UPDATE ai_work_jobs SET state='stopped' WHERE work_id=?", (self.stopped,))
        self.execute(self.config, WORK_DATABASE,
            "INSERT INTO ai_work_bridge_events(event_digest,delivery_digest,organization_id,connector_id,provider,object_digest,container_digest,event_type,object_type,quality_state,event_at,created_at) "
            "VALUES('event-fixture','delivery-fixture','customer-a','connector-fixture','github','object-digest','container-digest','completed','pull_request_lifecycle','accepted',3,3)")
        self.execute(self.config, BILLING_DATABASE,
            "UPDATE work_entitlements SET subscription_state='active',paid_until=100,subscription_until=100")
        self.execute(self.config, BILLING_DATABASE,
            "INSERT INTO work_activation(organization_id,actor_id,model,client,state,generation,updated_at) "
            "VALUES('customer-a','owner','openai-primary','codex','active','fixture-generation',1)")
        self.execute(self.config, BILLING_DATABASE,
            "INSERT INTO work_checkouts(reference,organization_id,generation,price_id,state,url,expires_at,created_at) "
            "VALUES('hormuz_checkout_fixture','customer-a','fixture-generation','price_Reviewed','open','https://checkout.stripe.com/c/pay/synthetic',100,1)")
        self.execute(self.config, BILLING_DATABASE,
            "INSERT INTO work_payment_facts VALUES('cus_Reviewed','sub_Reviewed','subscription',1,'evt_fixture','{}')")
        self.archive = self.root / "whole-state.hzb"
        export_backup(self.config, self.archive, b"b" * 32)
        self.recovered = self.restore("recovered")

    def restore(self, name):
        recovered = at_directory(self.config, self.root / name)
        result = restore_backup(recovered, self.archive, b"b" * 32)
        self.assertTrue(result["recovered_closed"])
        archive_metadata = {"recovered_closed", "archive_bytes", "archive_sha256", "backup_schema"}
        self.assertTrue(all(value == 0 for key, value in result.items() if key not in archive_metadata))
        return recovered

    def execute(self, config, database, statement, values=()):
        with closing(sqlite3.connect(config.database_path.parent / database)) as connection, connection:
            connection.execute(statement, values)

    def hashes(self, config):
        return {name: hashlib.sha256((config.database_path.parent / name).read_bytes()).hexdigest()
                for name in (WORK_DATABASE, BILLING_DATABASE)}

    def read(self, config, database, statement, values=()):
        path = config.database_path.parent / database
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)) as connection:
            return connection.execute(statement, values).fetchall()

    def test_actual_whole_state_restore_is_closed_without_changing_unknown_spend(self):
        before = self.hashes(self.recovered)
        result = check_recovered_closed(self.recovered)
        self.assertTrue(result.pop("recovered_closed"))
        self.assertTrue(all(value == 0 for value in result.values()))
        self.assertEqual(set(result).intersection({
            "pending_work_attempts", "unclosed_work_jobs", "pending_work_bridge_events",
            "unclosed_work_entitlements", "unclosed_work_activation", "unclosed_work_checkouts", "retained_work_payment_facts",
        }), {
            "pending_work_attempts", "unclosed_work_jobs", "pending_work_bridge_events",
            "unclosed_work_entitlements", "unclosed_work_activation", "unclosed_work_checkouts", "retained_work_payment_facts",
        })
        self.assertEqual(self.read(self.recovered, WORK_DATABASE,
            "SELECT state,reserved_microusd FROM ai_work_attempts"), [("unknown", 500)])
        self.assertEqual(self.read(self.recovered, WORK_DATABASE,
            "SELECT state,pause_reason FROM ai_work_jobs WHERE work_id=?", (self.work,)), [("paused", "recovery_required")])
        self.assertEqual(self.read(self.recovered, WORK_DATABASE,
            "SELECT state FROM ai_work_jobs WHERE state IN ('stopped','completed') ORDER BY state"), [("completed",), ("stopped",)])
        self.assertEqual(self.hashes(self.recovered), before)

    def test_every_independently_reopened_work_or_payment_authority_fails_without_repair(self):
        cases = [
            (WORK_DATABASE, "UPDATE ai_work_attempts SET state='pending'"),
            (WORK_DATABASE, "UPDATE ai_work_jobs SET state='active' WHERE pause_reason='recovery_required'"),
            (WORK_DATABASE, "UPDATE ai_work_jobs SET pause_reason=NULL WHERE state='paused'"),
            (WORK_DATABASE, "UPDATE ai_work_bridge_events SET status='pending'"),
            (BILLING_DATABASE, "UPDATE work_entitlements SET subscription_state='active'"),
            (BILLING_DATABASE, "UPDATE work_entitlements SET paid_until=1"),
            (BILLING_DATABASE, "UPDATE work_entitlements SET subscription_until=1"),
            (BILLING_DATABASE, "UPDATE work_activation SET state='qualified'"),
            (BILLING_DATABASE, "UPDATE work_checkouts SET state='open'"),
            (BILLING_DATABASE, "UPDATE work_checkouts SET url='https://checkout.stripe.com/c/pay/synthetic'"),
            (BILLING_DATABASE, "INSERT INTO work_payment_facts VALUES('cus_Reviewed','sub_Reviewed','invoice',2,'evt_new_fixture','{}')"),
        ]
        for index, (name, statement) in enumerate(cases):
            with self.subTest(index=index):
                recovered = self.restore("reopened-" + str(index))
                self.execute(recovered, name, statement)
                before = self.hashes(recovered)
                with self.assertRaisesRegex(HostedError, "^hosted_recovery_authority_open$"):
                    check_recovered_closed(recovered)
                self.assertEqual(self.hashes(recovered), before)

    def test_missing_owned_stores_are_refused_and_never_recreated(self):
        for index, name in enumerate((WORK_DATABASE, BILLING_DATABASE)):
            with self.subTest(database=name):
                recovered = self.restore("missing-" + str(index))
                missing = recovered.database_path.parent / name
                missing.unlink()
                other = BILLING_DATABASE if name == WORK_DATABASE else WORK_DATABASE
                other_path = recovered.database_path.parent / other
                before = other_path.read_bytes()
                with self.assertRaisesRegex(HostedError, "^hosted_work_recovery_store_unavailable$"):
                    check_recovered_closed(recovered)
                self.assertFalse(missing.exists())
                self.assertEqual(other_path.read_bytes(), before)

    def test_corrupt_integrity_and_missing_schema_fail_without_mutating_stores(self):
        cases = [
            (WORK_DATABASE, "UPDATE ai_work_attempts SET work_id='missing-job'", "integrity_invalid"),
            (WORK_DATABASE, "UPDATE ai_work_schema SET version=2", "schema_invalid"),
            (BILLING_DATABASE, "DROP TABLE work_checkouts", "schema_invalid"),
        ]
        for index, (name, statement, code) in enumerate(cases):
            with self.subTest(index=index):
                recovered = self.restore("schema-" + str(index))
                self.execute(recovered, name, statement)
                before = self.hashes(recovered)
                with self.assertRaisesRegex(HostedError, code):
                    check_recovered_closed(recovered)
                self.assertEqual(self.hashes(recovered), before)
        for index, name in enumerate((WORK_DATABASE, BILLING_DATABASE)):
            with self.subTest(corrupt_database=name):
                recovered = self.restore("corrupt-" + str(index))
                (recovered.database_path.parent / name).write_bytes(b"synthetic corrupted database")
                before = self.hashes(recovered)
                with self.assertRaisesRegex(HostedError, "database_invalid"):
                    check_recovered_closed(recovered)
                self.assertEqual(self.hashes(recovered), before)

    def test_unsafe_symlinks_hardlinks_and_permissions_are_refused(self):
        for index, unsafe in enumerate(("symlink", "hardlink", "group-readable")):
            with self.subTest(unsafe=unsafe):
                recovered = self.restore("unsafe-" + str(index))
                path = recovered.ai_work.database_path
                if unsafe == "symlink":
                    original = path.with_name("saved-work.sqlite3")
                    path.rename(original)
                    path.symlink_to(original)
                elif unsafe == "hardlink":
                    os.link(path, path.with_name("linked-work.sqlite3"))
                else:
                    path.chmod(0o640)
                with self.assertRaisesRegex(HostedError, "permissions_invalid"):
                    check_recovered_closed(recovered)

    def test_store_swapped_while_later_owned_store_is_checked_fails_closed(self):
        from hormuz import work_recovery
        validate = work_recovery._validate_store
        def swap_earlier_store(connection, *, billing=False):
            result = validate(connection, billing=billing)
            if billing:
                path = self.recovered.ai_work.database_path
                replacement = path.with_name("private-replacement.sqlite3")
                replacement.write_bytes(path.read_bytes())
                replacement.chmod(0o600)
                replacement.replace(path)
            return result
        with patch("hormuz.work_recovery._validate_store", side_effect=swap_earlier_store):
            with self.assertRaisesRegex(HostedError, "^hosted_work_recovery_store_changed$"):
                check_recovered_closed(self.recovered)

    def test_live_hosted_or_work_owner_is_refused_and_locks_release_after_failure(self):
        before = self.hashes(self.recovered)
        with state_lock(self.recovered, exclusive=False):
            with self.assertRaisesRegex(HostedError, "^hosted_state_in_use$"):
                check_recovered_closed(self.recovered)
        with owner_lock(self.recovered.ai_work.database_path):
            with self.assertRaisesRegex(HostedError, "^hosted_work_owner_active$"):
                check_recovered_closed(self.recovered)
        self.assertTrue(check_recovered_closed(self.recovered)["recovered_closed"])
        self.assertEqual(self.hashes(self.recovered), before)

    def test_optional_legacy_bridge_table_absence_remains_supported(self):
        self.execute(self.recovered, WORK_DATABASE, "DROP TABLE ai_work_bridge_events")
        result = check_recovered_closed(self.recovered)
        self.assertEqual(result["pending_work_bridge_events"], 0)

    def test_direct_and_offline_cli_checks_use_same_closed_authority_validation(self):
        self.document["database"] = str(self.recovered.database_path)
        self.document["authentication"]["session_broker"]["database"] = str(self.recovered.session_broker.database_path)
        self.document["ai_work"]["database"] = str(self.recovered.ai_work.database_path)
        source = self.root / "recovered-profile.json"
        source.write_text(json.dumps(self.document))
        source.chmod(0o600)
        args = ["--config", str(source), "--ai-work-profile", "recovery-check"]
        output, error = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, self.environment, clear=True), patch("hormuz.hosted._spawn") as spawn, redirect_stdout(output), redirect_stderr(error):
            self.assertEqual(main(args), 0)
            self.assertEqual(json.loads(output.getvalue())["unclosed_work_entitlements"], 0)
            self.execute(self.recovered, WORK_DATABASE, "UPDATE ai_work_attempts SET state='pending'")
            before = self.hashes(self.recovered)
            self.assertEqual(main(args), 1)
            self.assertEqual(json.loads(error.getvalue())["code"], "hosted_recovery_authority_open")
            spawn.assert_not_called()
        self.assertEqual(self.hashes(self.recovered), before)


if __name__ == "__main__":
    unittest.main()
