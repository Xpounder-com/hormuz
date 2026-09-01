from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import os
import shutil
import sqlite3
import struct
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from hormuz._hosted_backup import (
    BACKUP_FILES, BACKUP_MAGIC, BACKUP_SCHEMA, NONCE_BYTES,
    export_backup, read_backup_key, restore_backup, verify_backup,
)
from hormuz._hosted_config import HostedError, at_directory
from hormuz._hosted_state import MARKER, _read, initialize, sessions
from hormuz.hosted import main
from hormuz.onboarding import TeamDirectory
from hormuz.session_store import SessionStoreError
from tests._console_fixtures import activate_member
from tests._hosted_fixtures import directory_setup, profile


@unittest.skipUnless(os.name == "posix", "Encrypted hosted backups require POSIX private-file permissions")
class HostedBackupTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(logging.disable, logging.root.manager.disable)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.config, self.settings, _ = profile(self.root)
        initialize(self.config)
        self.key = b"b" * 32
        self.key_file = self.root / "backup.key"
        self.key_file.write_bytes(base64.b64encode(self.key) + b"\n")
        self.key_file.chmod(0o600)
        self.archive = self.root / "hosted-backup.hzb"

    def seed(self):
        store = sessions(self.config)
        directory = TeamDirectory(self.config, store)
        directory_setup(directory, self.config)
        invitation, native = activate_member(
            store,
            directory,
            subject="private-subject",
            email="private-person@example.test",
            name="Private Person",
        )
        return store, directory, invitation, native

    def write_plain_archive(self, path: Path, plaintext: bytes) -> None:
        nonce = b"n" * NONCE_BYTES
        encrypted = AESGCM(self.key).encrypt(nonce, plaintext, BACKUP_MAGIC)
        path.write_bytes(BACKUP_MAGIC + nonce + encrypted)
        path.chmod(0o600)

    def write_archive(self, path: Path, files: list[dict[str, object]], payload: bytes) -> None:
        manifest = json.dumps(
            {"schema": BACKUP_SCHEMA, "version": 1, "files": files},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        self.write_plain_archive(path, struct.pack(">I", len(manifest)) + manifest + payload)

    def test_encrypted_round_trip_restores_only_closed_authority(self):
        store, _, invitation, native = self.seed()
        result = export_backup(self.config, self.archive, self.key)
        self.assertEqual(self.archive.stat().st_mode & 0o777, 0o600)
        self.assertEqual(result["archive_bytes"], self.archive.stat().st_size)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(self.archive.read_bytes()).hexdigest())
        ciphertext = self.archive.read_bytes()
        for secret in (
            b"SQLite format 3",
            b"private-subject",
            b"private-person@example.test",
            invitation.code.encode(),
            native.access_token.encode(),
            native.refresh_token.encode(),
        ):
            self.assertNotIn(secret, ciphertext)
        self.assertEqual(verify_backup(self.archive, self.key), result)

        recovered = at_directory(self.config, self.root / "recovered")
        restored = restore_backup(recovered, self.archive, self.key)
        self.assertTrue(restored["recovered_closed"])
        self.assertEqual(
            {value for name, value in restored.items() if name.endswith(("memberships", "invitations", "enrollments", "sessions", "grants", "flows"))},
            {0},
        )
        self.assertTrue(_read(recovered.database_path.parent / MARKER)["recovered_closed"])
        recovered_store = sessions(recovered)
        for token in (native.access_token, native.refresh_token):
            with self.assertRaises(SessionStoreError):
                if token == native.access_token:
                    recovered_store.authenticate_access(token)
                else:
                    recovered_store.refresh(token)
        with closing(sqlite3.connect(recovered.session_broker.database_path)) as connection, connection:
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM onboarding_memberships WHERE status != 'disabled'"
            ).fetchone()[0], 0)
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM onboarding_invitations WHERE status = 'pending'"
            ).fetchone()[0], 0)
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM human_sessions WHERE revoked_at IS NULL"
            ).fetchone()[0], 0)
        self.assertEqual(store.authenticate_access(native.access_token).subject, "private-subject")

    def test_wrong_key_tampering_and_truncation_fail_before_destination_writes(self):
        self.seed()
        export_backup(self.config, self.archive, self.key)
        cases: list[tuple[Path, bytes, str]] = [(self.archive, b"w" * 32, "wrong-key")]

        tampered = self.root / "tampered.hzb"
        shutil.copyfile(self.archive, tampered)
        tampered.chmod(0o600)
        value = bytearray(tampered.read_bytes())
        value[len(value) // 2] ^= 1
        tampered.write_bytes(value)
        cases.append((tampered, self.key, "tampered"))

        truncated = self.root / "truncated.hzb"
        truncated.write_bytes(self.archive.read_bytes()[:-8])
        truncated.chmod(0o600)
        cases.append((truncated, self.key, "truncated"))

        for source, key, name in cases:
            with self.subTest(name=name):
                destination = at_directory(self.config, self.root / ("recovered-" + name))
                with self.assertRaises(HostedError):
                    restore_backup(destination, source, key)
                self.assertFalse(destination.database_path.parent.exists())

    def test_private_key_and_exclusive_output_boundaries(self):
        self.key_file.chmod(0o644)
        with self.assertRaisesRegex(HostedError, "key_file_invalid"):
            read_backup_key(self.key_file)
        self.key_file.chmod(0o600)
        self.assertEqual(read_backup_key(self.key_file), self.key)
        with self.assertRaisesRegex(HostedError, "key_reused"):
            export_backup(self.config, self.archive, self.config.session_broker.master_key)
        self.assertFalse(self.archive.exists())

        target = self.root / "existing.hzb"
        target.write_bytes(b"preserve")
        target.chmod(0o600)
        for output in (target, self.root / "link.hzb"):
            if output != target:
                output.symlink_to(target)
            with self.subTest(output=output.name), self.assertRaisesRegex(HostedError, "output_(?:exists|invalid)"):
                export_backup(self.config, output, self.key)
            self.assertEqual(target.read_bytes(), b"preserve")

    def test_authenticated_archive_still_rejects_names_lengths_and_extra_payload(self):
        empty_digest = hashlib.sha256(b"").hexdigest()
        empty_files = [
            {"name": name, "size": 0, "sha256": empty_digest}
            for name in BACKUP_FILES
        ]
        unsafe_name = self.root / "unsafe-name.hzb"
        unsafe_files = [dict(entry) for entry in empty_files]
        unsafe_files[0]["name"] = "../../snapshot.json"
        self.write_archive(unsafe_name, unsafe_files, b"")

        invalid_sizes = self.root / "invalid-sizes.hzb"
        self.write_archive(invalid_sizes, empty_files, b"")

        duplicate = self.root / "duplicate-manifest.hzb"
        duplicate_manifest = (
            '{"schema":"' + BACKUP_SCHEMA + '","schema":"' + BACKUP_SCHEMA
            + '","version":1,"files":[]}'
        ).encode()
        self.write_plain_archive(
            duplicate,
            struct.pack(">I", len(duplicate_manifest)) + duplicate_manifest,
        )

        marker = json.dumps({
            "version": 1, "instance": "1" * 32,
            "recovered_closed": False, "binding": "2" * 64,
        }, sort_keys=True).encode()
        database = b"SQLite format 3\x00"
        snapshot_document = {
            "version": 1,
            "files": {
                "initialized.json": hashlib.sha256(marker).hexdigest(),
                "sessions.sqlite3": hashlib.sha256(database).hexdigest(),
                "usage.sqlite3": hashlib.sha256(database).hexdigest(),
            },
            "binding": "3" * 64,
        }
        snapshot_bytes = json.dumps(snapshot_document, sort_keys=True).encode()
        payloads = [snapshot_bytes, marker, database, database]
        framed_files = [
            {"name": name, "size": len(value), "sha256": hashlib.sha256(value).hexdigest()}
            for name, value in zip(BACKUP_FILES, payloads, strict=True)
        ]
        extra = self.root / "extra.hzb"
        self.write_archive(extra, framed_files, b"".join(payloads) + b"extra")

        for source in (unsafe_name, invalid_sizes, duplicate, extra):
            with self.subTest(source=source.name), self.assertRaises(HostedError):
                verify_backup(source, self.key)

    def test_cli_reports_only_content_free_archive_metadata(self):
        self.seed()
        output, error = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, self.settings, clear=True), redirect_stdout(output), redirect_stderr(error):
            status = main([
                "--config", str(self.config.source_path), "backup-export",
                "--key-file", str(self.key_file), "--output-file", str(self.archive),
            ])
        self.assertEqual((status, error.getvalue()), (0, ""))
        event = json.loads(output.getvalue())
        self.assertEqual(event["operation"], "backup-export")
        self.assertFalse(event["inference_enabled"])
        for secret in ("private-subject", "private-person@example.test", base64.b64encode(self.key).decode()):
            self.assertNotIn(secret, output.getvalue())

        output, error = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {}, clear=True), redirect_stdout(output), redirect_stderr(error):
            status = main([
                "backup-verify", "--key-file", str(self.key_file),
                "--archive-file", str(self.archive),
            ])
        self.assertEqual((status, error.getvalue()), (0, ""))
        self.assertEqual(json.loads(output.getvalue())["archive_sha256"], event["archive_sha256"])
