from __future__ import annotations

import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from hormuz.cli import main
from tests._session_fixtures import fixture_environment, session_config


@unittest.skipIf(os.name == "nt", "local operator commands require POSIX private-file permissions")
class TeamCommandTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "hormuz.json"
        self.value = session_config(self.root, "http://127.0.0.1:9000", "http://127.0.0.1:8787")
        self.value["authentication"]["session_broker"]["onboarding_enabled"] = True
        self.value["authentication"]["oidc"]["issuers"][0]["login"]["scopes"] = ["openid", "email"]
        self.value["authentication"]["oidc"]["issuers"][0]["subjects"] = []
        self.path.write_text(json.dumps(self.value))
        self.email = self.root / "recipient.txt"
        self.email.write_text("new@example.test\n")
        self.email.chmod(0o600)
        self.output = self.root / "invitation.json"
        self.assertEqual(self.command("organization", "create", "--organization", "customer-a", "--name", "Customer A", "--issuer", "http://127.0.0.1:9000")[0], 0)
        self.assertEqual(self.command("create", "--organization", "customer-a", "--team", "customer-a-eng", "--name", "Engineering")[0], 0)

    def command(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict("os.environ", fixture_environment(), clear=True), redirect_stdout(out), redirect_stderr(err):
            status = main(["--config", str(self.path), "team", *args])
        return status, out.getvalue(), err.getvalue()

    def invite(self):
        return self.command("invite", "--organization", "customer-a", "--team", "customer-a-eng",
                            "--name", "New Member", "--email-file", str(self.email), "--client", "codex", "--output", str(self.output))

    def count_invitations(self):
        with closing(sqlite3.connect(self.root / "sessions.sqlite3")) as connection:
            return connection.execute("SELECT COUNT(*) FROM onboarding_invitations").fetchone()[0]

    def test_operator_setup_invite_list_revoke_and_reinvite_use_private_files(self):
        status, out, err = self.invite()
        self.assertEqual((status, err), (0, ""))
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)
        invitation = json.loads(self.output.read_text())
        self.assertFalse(invitation["invitation_code"] in out)
        self.assertNotIn("new@example.test", out)
        self.assertTrue(json.loads(out)["private_file_written"])
        status, out, _ = self.command("members", "list", "--organization", "customer-a", "--limit", "1")
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(out)["items"][0]["status"], "pending")
        self.assertNotIn("email", out)
        self.assertNotIn("subject", out)
        status, _, _ = self.command("invitations", "revoke", "--organization", "customer-a", "--invitation", invitation["invitation_id"])
        self.assertEqual(status, 0)
        reissued_path = self.root / "reissued.json"
        status, out, _ = self.command("members", "reinvite", "--organization", "customer-a", "--member", invitation["membership_id"], "--output", str(reissued_path))
        self.assertEqual(status, 0)
        reissued = json.loads(reissued_path.read_text())
        self.assertFalse(reissued["invitation_code"] == invitation["invitation_code"])
        self.assertEqual(reissued["membership_id"], invitation["membership_id"])
        self.assertEqual(self.command("members", "disable", "--organization", "customer-a", "--member", invitation["membership_id"])[0], 0)
        status, out, _ = self.command("events", "--organization", "customer-a")
        self.assertEqual(status, 0)
        self.assertTrue(all(event["decision_actor"] == "server_local_operator" for event in json.loads(out)["items"]))

    def test_existing_or_symlink_output_is_refused_before_issuing_an_invitation(self):
        target = self.root / "existing.json"
        target.write_text("preserve existing bytes")
        for symlink in (False, True):
            if symlink:
                self.output.symlink_to(target)
            else:
                self.output.write_text("preserve existing bytes")
            status, out, err = self.invite()
            self.assertEqual(status, 2)
            self.assertEqual(out, "")
            self.assertIn("onboarding_private_output_unavailable", err)
            self.assertEqual(self.count_invitations(), 0)
            self.assertEqual(target.read_text(), "preserve existing bytes")
            self.output.unlink()

    def test_invalid_or_world_readable_email_file_is_rejected_without_output(self):
        for value, mode in (("new@example.test\nextra", 0o600), ("new@example.test", 0o644), ("x" * 1024, 0o600)):
            self.email.write_text(value)
            self.email.chmod(mode)
            status, out, err = self.invite()
            self.assertEqual(status, 2)
            self.assertEqual(out, "")
            self.assertFalse(value in err)
            self.assertFalse(self.output.exists())
            self.assertEqual(self.count_invitations(), 0)

    def test_output_write_failure_revokes_invitation_and_cleans_partial_file(self):
        with mock.patch("hormuz.commands.onboarding.os.fsync", side_effect=OSError("synthetic failure")):
            status, out, err = self.invite()
        self.assertEqual(status, 2)
        self.assertEqual(out, "")
        self.assertIn("onboarding_private_output_unavailable", err)
        self.assertFalse(self.output.exists())
        with closing(sqlite3.connect(self.root / "sessions.sqlite3")) as connection:
            self.assertEqual(connection.execute("SELECT status, secret_hash FROM onboarding_invitations").fetchone(), ("revoked", None))
            self.assertEqual(connection.execute("SELECT status FROM onboarding_memberships").fetchone()[0], "disabled")

    def test_partial_output_is_removed_even_if_compensating_revocation_is_unavailable(self):
        from hormuz.session_store import SessionStoreError
        with mock.patch("hormuz.commands.onboarding.os.fsync", side_effect=OSError("synthetic failure")), \
             mock.patch("hormuz.onboarding.TeamDirectory.revoke_invitation", side_effect=SessionStoreError("session_store_unavailable")):
            status, out, err = self.invite()
        self.assertEqual((status, out), (2, ""))
        self.assertIn("session_store_unavailable", err)
        self.assertFalse(self.output.exists())

    def test_employee_session_token_is_not_an_operator_authentication_option(self):
        # Administration requires server secrets. The public login helper is not
        # consulted and the machine credential helper is never executed.
        from hormuz.cli import build_parser
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            build_parser().parse_args(["team", "members", "list", "--organization", "customer-a", "--credential", "hox_a_" + "x" * 43])
        self.value["authentication"]["session_broker"]["onboarding_enabled"] = False
        self.path.write_text(json.dumps(self.value))
        status, out, _ = self.command("members", "list", "--organization", "customer-a")
        self.assertEqual((status, out), (2, ""))

    def test_console_grants_are_explicit_operator_actions_even_while_web_console_is_off(self):
        from hormuz.config import GatewayConfig
        from hormuz.onboarding import TeamDirectory
        from hormuz.session_store import SQLiteSessionStore
        from tests._console_fixtures import activate_member
        config = GatewayConfig.load(self.path, environ=fixture_environment())
        settings = config.session_broker
        self.assertFalse(settings.console_enabled)
        store = SQLiteSessionStore(settings.database_path, master_key=settings.master_key, audience=settings.public_base_url,
                                    access_ttl_seconds=600, absolute_ttl_seconds=43200, enrollment_ttl_seconds=300)
        directory = TeamDirectory(config, store)
        member, _ = activate_member(store, directory)
        arguments = ("--organization", "customer-a", "--member", member.membership_id)
        status, out, err = self.command("administrators", "grant", *arguments, "--role", "member_admin")
        self.assertEqual((status, err), (0, ""))
        self.assertTrue(json.loads(out)["changed"])
        self.assertFalse(json.loads(self.command("administrators", "grant", *arguments, "--role", "member_admin")[1])["changed"])
        status, out, _ = self.command("administrators", "list", "--organization", "customer-a")
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(out)["items"][0]["membership_id"], member.membership_id)
        self.assertNotIn("subject", out)
        self.assertNotIn("email", out)
        self.assertEqual(self.command("administrators", "revoke", *arguments)[0], 0)
        self.assertFalse(json.loads(self.command("administrators", "revoke", *arguments)[1])["changed"])


if __name__ == "__main__":
    unittest.main()
