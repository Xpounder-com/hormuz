from __future__ import annotations

import io
import json
import os
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from hormuz._hosted_config import HostedError, at_directory, load_profile
from hormuz._sqlite_schema import initialize_sqlite_schema
from hormuz._hosted_state import (
    DATABASES, MARKER, check_initialized, check_recovered_closed, initialize, restore, sessions,
    snapshot, state_lock,
)
from hormuz.hosted import main, proxy_settings, stop_child
from hormuz.onboarding import TeamDirectory
from hormuz.session_store import SessionStoreError
from hormuz.store import StorageSchemaError, UsageStore
from tests._console_fixtures import activate_member
from tests._hosted_fixtures import console_credential, directory_setup, profile


@unittest.skipUnless(os.name == "posix", "The staging runtime uses POSIX file locks and permissions")
class HostedStateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.config, self.settings, self.document = profile(self.root)

    def replace_usage_store(self, *, version: int) -> None:
        path = self.config.database_path
        for suffix in ("", "-shm", "-wal"):
            path.with_name(path.name + suffix).unlink(missing_ok=True)
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.row_factory = sqlite3.Row
            initialize_sqlite_schema(
                connection,
                schema_version=version,
                maximum_supported_schema_version=version,
                apply_migration=UsageStore._apply_migration,
                error_factory=StorageSchemaError,
            )
            connection.execute(
                """
                INSERT INTO gateway_budget_reservations (
                    id, created_at, expires_at, organization_id, actor_id,
                    team_id, reserved_tokens, reserved_cost_microusd, attempt_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    "pre-migration-hold",
                    "2026-09-01T00:00:00+00:00",
                    "2026-09-02T00:00:00+00:00",
                    "customer-a",
                    "member-a",
                    "customer-a-eng",
                    1,
                    1,
                ),
            )
        path.chmod(0o600)

    @staticmethod
    def usage_schema_version(path: Path) -> int:
        with closing(sqlite3.connect(path)) as connection:
            return int(connection.execute(
                "SELECT max(version) FROM hormuz_schema_migrations WHERE state = 'applied'"
            ).fetchone()[0])

    def test_profile_is_provider_free_and_rejects_expansion(self):
        self.assertEqual(self.config.upstreams, {})
        self.assertEqual(self.config.model_routes, {})
        self.assertEqual(self.config.identities_by_token, {})
        mutations = [
            {"public_origin": "http://gateway.example.test"},
            {"public_origin": "https://gateway.example.test/path"},
            {"oidc_issuer": "http://idp.example.test"},
            {"oidc_issuer": "https://user:secret@idp.example.test"},
            {"upstreams": {}}, {"identities": []}, {"state_directory": "./state"},
            {"trusted_parent_path": "./runtime"}, {"trusted_parent_path": "/"},
        ]
        for change in mutations:
            with self.subTest(change=change):
                raw = {**self.document, **change}
                self.config.source_path.write_text(json.dumps(raw))
                with self.assertRaises(HostedError):
                    load_profile(self.config.source_path, self.settings)

    def test_invalid_or_reused_secrets_fail_without_writing_state(self):
        for change in ({"HORMUZ_INGRESS_CREDENTIAL": "bad\nvalue"},
                       {"HORMUZ_SESSION_MASTER_KEY": "not-base64"},
                       {"HORMUZ_OIDC_CLIENT_SECRET": ""},
                       {"HORMUZ_OIDC_CLIENT_SECRET": self.settings["HORMUZ_INGRESS_CREDENTIAL"]}):
            with self.subTest(change=list(change)), self.assertRaises(ValueError if "HORMUZ_SESSION_MASTER_KEY" in change or change.get("HORMUZ_OIDC_CLIENT_SECRET") == "" else HostedError):
                load_profile(self.config.source_path, {**self.settings, **change})
        self.assertFalse(self.config.database_path.parent.exists())

    def test_explicit_initialization_is_empty_private_and_never_repeated(self):
        with self.assertRaises(FileNotFoundError):
            check_initialized(self.config)
        self.assertFalse(self.config.database_path.parent.exists())
        initialize(self.config)
        check_initialized(self.config)
        store = sessions(self.config)
        with store._connection() as connection:
            for table in ("onboarding_organizations", "onboarding_memberships", "onboarding_invitations", "console_grants", "human_sessions"):
                self.assertEqual(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0)
        for name in (MARKER, *DATABASES):
            self.assertEqual((self.config.database_path.parent / name).stat().st_mode & 0o777, 0o600)
        before = (self.config.database_path.parent / MARKER).read_bytes()
        with self.assertRaises(FileExistsError):
            initialize(self.config)
        self.assertEqual(before, (self.config.database_path.parent / MARKER).read_bytes())

    def test_wrong_key_origin_or_client_and_missing_database_do_not_reinitialize(self):
        initialize(self.config)
        issuer = next(iter(self.config.oidc_issuers.values()))
        for config in (
            replace(self.config, session_broker=replace(self.config.session_broker, master_key=b"x" * 32)),
            replace(self.config, session_broker=replace(self.config.session_broker, public_base_url="https://other.example.test")),
            replace(self.config, oidc_issuers={issuer.issuer: replace(issuer, login=replace(issuer.login, client_id="other-client"))}),
        ):
            with self.assertRaisesRegex(HostedError, "binding_mismatch"):
                check_initialized(config)
        self.config.database_path.unlink()
        with self.assertRaises(FileNotFoundError):
            check_initialized(self.config)
        self.assertFalse(self.config.database_path.exists())

    def test_partial_and_symlinked_state_are_refused(self):
        initialize(self.config)
        path = self.config.database_path
        saved = self.root / "saved.sqlite3"
        path.rename(saved)
        path.symlink_to(saved)
        with self.assertRaisesRegex(HostedError, "permissions_invalid"):
            check_initialized(self.config)
        path.unlink()
        path.write_bytes(b"SQLite format 3\x00")
        path.chmod(0o600)
        with self.assertRaises(Exception):
            check_initialized(self.config)
        self.assertEqual(path.stat().st_size, 16)

    def test_running_gateway_or_operator_blocks_offline_lifecycle(self):
        initialize(self.config)
        with state_lock(self.config, exclusive=False):
            with state_lock(self.config, exclusive=False):
                check_initialized(self.config)
            with self.assertRaisesRegex(HostedError, "state_in_use"):
                snapshot(self.config, self.root / "blocked")
        self.assertFalse((self.root / "blocked").exists())
        snapshot(self.config, self.root / "snapshot")

    def test_usage_migration_is_explicit_maintenance_only_and_snapshotted(self):
        initialize(self.config)
        self.replace_usage_store(version=6)
        with self.assertRaisesRegex(HostedError, "schema_mismatch"):
            check_initialized(self.config)

        snapshot_directory = self.root / "pre-migration"
        output, error = io.StringIO(), io.StringIO()
        with (
            patch.dict(os.environ, self.settings, clear=True),
            patch("hormuz.hosted.logging.disable"),
            redirect_stdout(output),
            redirect_stderr(error),
        ):
            status = main([
                "--config", str(self.config.source_path), "migrate",
                "--snapshot-directory", str(snapshot_directory),
            ])
        self.assertEqual(status, 1)
        self.assertEqual(json.loads(error.getvalue())["code"], "hosted_migration_requires_maintenance")
        self.assertEqual(output.getvalue(), "")
        self.assertFalse(snapshot_directory.exists())
        self.assertEqual(self.usage_schema_version(self.config.database_path), 6)

        output, error = io.StringIO(), io.StringIO()
        maintenance = {**self.settings, "HORMUZ_HOSTED_MODE": "maintenance"}
        with (
            patch.dict(os.environ, maintenance, clear=True),
            patch("hormuz.hosted.logging.disable"),
            redirect_stdout(output),
            redirect_stderr(error),
        ):
            status = main([
                "--config", str(self.config.source_path), "migrate",
                "--snapshot-directory", str(snapshot_directory),
            ])
        self.assertEqual((status, error.getvalue()), (0, ""))
        event = json.loads(output.getvalue())
        self.assertEqual(event["operation"], "migrate")
        self.assertFalse(event["inference_enabled"])
        self.assertTrue(event["snapshot_created"])
        self.assertEqual(event["source_usage_schema_version"], 6)
        self.assertEqual(event["target_usage_schema_version"], UsageStore.schema_version)
        self.assertEqual(self.usage_schema_version(snapshot_directory / "usage.sqlite3"), 6)
        self.assertEqual(self.usage_schema_version(self.config.database_path), UsageStore.schema_version)
        for path in (snapshot_directory / "usage.sqlite3", self.config.database_path):
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(connection.execute(
                    "SELECT count(*) FROM gateway_budget_reservations WHERE id = ?",
                    ("pre-migration-hold",),
                ).fetchone()[0], 1)
        check_initialized(self.config)

        second = self.root / "unnecessary-migration"
        output, error = io.StringIO(), io.StringIO()
        with (
            patch.dict(os.environ, maintenance, clear=True),
            patch("hormuz.hosted.logging.disable"),
            redirect_stdout(output),
            redirect_stderr(error),
        ):
            status = main([
                "--config", str(self.config.source_path), "migrate",
                "--snapshot-directory", str(second),
            ])
        self.assertEqual(status, 1)
        self.assertEqual(json.loads(error.getvalue())["code"], "hosted_state_migration_not_required")
        self.assertFalse(second.exists())

    def test_restart_preserves_access_but_stale_restore_revokes_every_authority(self):
        initialize(self.config)
        store = sessions(self.config)
        directory = TeamDirectory(self.config, store)
        directory_setup(directory, self.config)
        admin, native = activate_member(store, directory)
        member, member_native = activate_member(store, directory, subject="member-subject", email="member@example.test")
        from hormuz.console_store import ConsoleStore
        console = ConsoleStore(store, directory)
        console.grant(organization_id="customer-a", membership_id=admin.membership_id, role="member_admin")
        console, cookie = console_credential(store, directory)
        invite = directory.invite(organization_id="customer-a", team_id="customer-a-eng", email="pending@example.test", name="Pending", allowed_clients=("codex",))
        pending_state, pending_cookie = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        console.begin_login(organization_id="customer-a", state=pending_state, browser_cookie=pending_cookie,
                            nonce=secrets.token_urlsafe(32), pkce_verifier=secrets.token_urlsafe(64))
        restarted = sessions(self.config)
        self.assertEqual(restarted.authenticate_access(native.access_token).membership_id, admin.membership_id)
        backup = self.root / "snapshot"
        snapshot(self.config, backup)
        directory.disable_member(organization_id="customer-a", membership_id=member.membership_id)
        with self.assertRaises(SessionStoreError):
            sessions(self.config).authenticate_access(member_native.access_token)
        recovered = at_directory(self.config, self.root / "recovered")
        restore(recovered, backup)
        check_initialized(recovered)
        recovery = check_recovered_closed(recovered)
        self.assertTrue(recovery.pop("recovered_closed"))
        self.assertEqual(set(recovery.values()), {0})
        restored_store = sessions(recovered)
        restored_directory = TeamDirectory(recovered, restored_store)
        restored_console = ConsoleStore(restored_store, restored_directory)
        for token in (native.access_token, member_native.access_token):
            with self.assertRaises(SessionStoreError):
                restored_store.authenticate_access(token)
        for token in (native.refresh_token, member_native.refresh_token):
            with self.assertRaises(SessionStoreError):
                restored_store.refresh(token)
        with self.assertRaises(SessionStoreError):
            restored_console.authenticate(cookie)
        with self.assertRaises(SessionStoreError):
            restored_console.consume_callback(state=pending_state, browser_cookie=pending_cookie)
        with restored_store._connection() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM onboarding_memberships WHERE status != 'disabled'").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM onboarding_invitations WHERE status = 'pending'").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM console_grants WHERE status = 'active'").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT subject FROM onboarding_memberships WHERE id = ?", (admin.membership_id,)).fetchone()[0], "admin-subject")
        # Reinvitation preserves subject binding, requires a fresh login and does
        # not silently restore administrator privileges or old native tokens.
        invitation = restored_directory.reinvite(organization_id="customer-a", membership_id=admin.membership_id)
        with self.assertRaisesRegex(HostedError, "recovery_authority_open"):
            check_recovered_closed(recovered)
        _, fresh = activate_member(restored_store, restored_directory, invitation=invitation)
        self.assertEqual(restored_store.authenticate_access(fresh.access_token).membership_id, admin.membership_id)
        with restored_store._connection() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM console_grants WHERE status = 'active'").fetchone()[0], 0)
        with self.assertRaises(SessionStoreError):
            restored_store.authenticate_access(native.access_token)
        with self.assertRaises(FileExistsError):
            restore(recovered, backup)

    def test_snapshot_integrity_and_untracked_sidecars_fail_closed(self):
        initialize(self.config)
        source = self.root / "snapshot"
        snapshot(self.config, source)
        destination = at_directory(self.config, self.root / "recovered")
        (source / "sessions.sqlite3-wal").write_bytes(b"synthetic untracked sidecar")
        with self.assertRaisesRegex(HostedError, "snapshot_files_invalid"):
            restore(destination, source)
        (source / "sessions.sqlite3-wal").unlink()
        path = source / "usage.sqlite3"
        with path.open("ab") as file:
            file.write(b"synthetic corruption")
        with self.assertRaisesRegex(HostedError, "digest_mismatch"):
            restore(destination, source)
        self.assertFalse(destination.database_path.parent.exists())

    def test_interrupted_restore_never_writes_an_activatable_marker(self):
        initialize(self.config)
        source = self.root / "snapshot"
        snapshot(self.config, source)
        destination = at_directory(self.config, self.root / "recovered")
        with patch("hormuz._hosted_state._write", side_effect=OSError("synthetic interrupted final write")):
            with self.assertRaises(OSError):
                restore(destination, source)
        self.assertTrue(destination.database_path.exists())
        self.assertFalse((destination.database_path.parent / MARKER).exists())
        with self.assertRaises(FileNotFoundError):
            check_initialized(destination)
        with self.assertRaises(FileExistsError):
            initialize(destination)

    def test_snapshot_requires_the_original_identity_key_binding(self):
        initialize(self.config)
        source = self.root / "snapshot"
        snapshot(self.config, source)
        destination = at_directory(self.config, self.root / "recovered")
        wrong = replace(destination, session_broker=replace(destination.session_broker, master_key=b"x" * 32))
        with self.assertRaisesRegex(HostedError, "snapshot_binding_mismatch"):
            restore(wrong, source)
        self.assertFalse(destination.database_path.parent.exists())

    def test_proxy_inherits_only_its_own_secret_and_port_is_bounded(self):
        settings = {**self.settings, "HTTP_PROXY": "https://untrusted.example.test", "PROVIDER_API_KEY": "synthetic-provider"}
        public = proxy_settings(settings, active=True)
        self.assertEqual(set(public), {"PORT", "HORMUZ_INGRESS_CREDENTIAL", "XDG_CONFIG_HOME", "XDG_DATA_HOME"})
        self.assertNotIn("HORMUZ_INGRESS_CREDENTIAL", proxy_settings(settings, active=False))
        for port in ("80", "8787", "65536", "10000\n", "{env.SECRET}"):
            with self.subTest(port=port), self.assertRaises(HostedError):
                proxy_settings({**settings, "PORT": port}, active=True)

    def test_stubborn_child_is_killed_within_the_shutdown_budget(self):
        process = subprocess.Popen([sys.executable, "-c", "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('ready',flush=True); time.sleep(60)"], stdout=subprocess.PIPE)
        self.addCleanup(lambda: process.kill() if process.poll() is None else None)
        self.assertEqual(process.stdout.readline(), b"ready\n")
        started = time.monotonic()
        self.assertFalse(stop_child(process, 0.1))
        self.assertLess(time.monotonic() - started, 3)
        self.assertIsNotNone(process.poll())
        process.stdout.close()
