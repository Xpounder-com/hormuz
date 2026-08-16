from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from hormuz.credential_store import (
    CredentialLock,
    CredentialStoreError,
    SecureCredentialStore,
    StoredSession,
)


class MemoryBackend:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.values[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if (service, username) not in self.values:
            raise Exception("missing")
        del self.values[(service, username)]


class CredentialStoreTests(unittest.TestCase):
    def _session(self) -> StoredSession:
        now = datetime.now(timezone.utc)
        return StoredSession(
            gateway="https://hormuz.example",
            client="codex",
            access_token="hox_a_" + "a" * 43,
            refresh_token="hox_r_" + "r" * 43,
            access_expires_at=now + timedelta(minutes=10),
            session_expires_at=now + timedelta(hours=12),
        )

    def test_round_trip_uses_only_injected_secure_backend(self) -> None:
        backend = MemoryBackend()
        store = SecureCredentialStore(backend, trust_injected_backend=True)
        session = self._session()
        store.set("engineering-codex", session)

        restored = store.get("engineering-codex")
        self.assertEqual(restored, session)
        raw = next(iter(backend.values.values()))
        self.assertNotIn("engineering-codex", next(iter(backend.values))[1])
        self.assertEqual(json.loads(raw)["version"], 1)
        store.delete("engineering-codex")
        self.assertIsNone(store.get("engineering-codex"))

    def test_unapproved_or_corrupt_backends_fail_closed(self) -> None:
        with self.assertRaisesRegex(CredentialStoreError, "unsupported_secure_store"):
            SecureCredentialStore(MemoryBackend())

        backend = MemoryBackend()
        store = SecureCredentialStore(backend, trust_injected_backend=True)
        store.set("profile", self._session())
        key = next(iter(backend.values))
        backend.values[key] = '{"version":1,"access_token":"plaintext"}'
        with self.assertRaisesRegex(CredentialStoreError, "invalid_stored_session"):
            store.get("profile")

    def test_chainer_is_allowed_only_when_every_child_is_an_os_backend(self) -> None:
        secure_type = type(
            "Keyring",
            (),
            {"__module__": "keyring.backends.macOS"},
        )
        chainer_type = type(
            "ChainerBackend",
            (),
            {"__module__": "keyring.backends.chainer"},
        )
        secure_chainer = chainer_type()
        secure_chainer.backends = [secure_type()]
        with mock.patch("platform.system", return_value="Darwin"):
            SecureCredentialStore(secure_chainer)

        unsafe_chainer = chainer_type()
        unsafe_chainer.backends = [secure_type(), MemoryBackend()]
        with mock.patch("platform.system", return_value="Darwin"):
            with self.assertRaisesRegex(CredentialStoreError, "unsupported_secure_store"):
                SecureCredentialStore(unsafe_chainer)

    def test_invalid_profiles_and_plaintext_tokens_are_rejected(self) -> None:
        store = SecureCredentialStore(MemoryBackend(), trust_injected_backend=True)
        for profile in ("", "../escape", "space profile", "x" * 65):
            with self.subTest(profile=profile):
                with self.assertRaisesRegex(CredentialStoreError, "invalid_profile"):
                    store.get(profile)
        value = self._session().to_json().replace("hox_a_", "plain_")
        with self.assertRaisesRegex(CredentialStoreError, "invalid_stored_session"):
            StoredSession.from_dict(json.loads(value))

    def test_metadata_lock_is_private_and_times_out_under_contention(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.dict("os.environ", {"XDG_CACHE_HOME": temporary}):
                with CredentialLock("profile", timeout_seconds=1) as first:
                    self.assertEqual(first.path.parent, Path(temporary) / "hormuz")
                    self.assertEqual(first.path.stat().st_mode & 0o777, 0o600)
                    with self.assertRaisesRegex(
                        CredentialStoreError,
                        "credential_refresh_lock_timeout",
                    ):
                        with CredentialLock("profile", timeout_seconds=0.05):
                            pass

    @unittest.skipUnless(hasattr(os, "O_NOFOLLOW"), "platform has no O_NOFOLLOW")
    def test_metadata_lock_refuses_symlink_without_touching_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.dict("os.environ", {"XDG_CACHE_HOME": temporary}):
                lock = CredentialLock("profile", timeout_seconds=0.05)
                target = Path(temporary) / "must-not-change"
                target.write_text("preserve", encoding="utf-8")
                original_mode = target.stat().st_mode & 0o777
                lock.path.symlink_to(target)
                with self.assertRaisesRegex(
                    CredentialStoreError,
                    "credential_lock_(open_failed|symlink_refused)",
                ):
                    with lock:
                        pass
                self.assertEqual(target.read_text(encoding="utf-8"), "preserve")
                self.assertEqual(target.stat().st_mode & 0o777, original_mode)


if __name__ == "__main__":
    unittest.main()
