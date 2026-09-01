from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import stat
import time
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

try:
    import keyring
except ImportError:  # The gateway-only installation does not require OS keyrings.
    keyring = None


_SERVICE_NAME = "ai.hormuz.session"


class CredentialStoreError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class KeyringBackend(Protocol):
    def get_password(self, service: str, username: str) -> str | None: ...

    def set_password(self, service: str, username: str, password: str) -> None: ...

    def delete_password(self, service: str, username: str) -> None: ...


@dataclass(frozen=True)
class StoredSession:
    gateway: str
    client: str
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    access_expires_at: datetime
    session_expires_at: datetime

    @classmethod
    def from_dict(cls, value: object) -> "StoredSession":
        if not isinstance(value, dict) or value.get("version") != 1:
            raise CredentialStoreError("invalid_stored_session")
        try:
            gateway = _required_string(value.get("gateway"), maximum=2048)
            client = _required_string(value.get("client"), maximum=64)
            access_token = _required_string(value.get("access_token"), maximum=4096)
            refresh_token = _required_string(value.get("refresh_token"), maximum=4096)
            access_expires_at = _parse_time(value.get("access_expires_at"))
            session_expires_at = _parse_time(value.get("session_expires_at"))
        except (TypeError, ValueError) as error:
            raise CredentialStoreError("invalid_stored_session") from error
        if (
            not re.fullmatch(r"hox_a_[A-Za-z0-9_-]{43}", access_token)
            or not re.fullmatch(r"hox_r_[A-Za-z0-9_-]{43}", refresh_token)
            or access_expires_at > session_expires_at
        ):
            raise CredentialStoreError("invalid_stored_session")
        return cls(
            gateway=gateway,
            client=client,
            access_token=access_token,
            refresh_token=refresh_token,
            access_expires_at=access_expires_at,
            session_expires_at=session_expires_at,
        )

    def to_json(self) -> str:
        return json.dumps(
            {
                "version": 1,
                "gateway": self.gateway,
                "client": self.client,
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "access_expires_at": self.access_expires_at.astimezone(timezone.utc).isoformat(),
                "session_expires_at": self.session_expires_at.astimezone(timezone.utc).isoformat(),
            },
            sort_keys=True,
            separators=(",", ":"),
        )


class SecureCredentialStore:
    """Fail-closed adapter for OS-backed keyring implementations."""

    def __init__(
        self,
        backend: KeyringBackend | None = None,
        *,
        trust_injected_backend: bool = False,
    ):
        if backend is None:
            if keyring is None:
                raise CredentialStoreError("secure_store_dependency_missing")
            try:
                backend = keyring.get_keyring()
            except Exception:
                raise CredentialStoreError("secure_store_unavailable") from None
        self.backend = backend
        if not trust_injected_backend:
            _validate_backend(self.backend)

    def get(self, profile: str) -> StoredSession | None:
        username = _profile_username(profile)
        try:
            raw = self.backend.get_password(_SERVICE_NAME, username)
        except Exception as error:
            raise CredentialStoreError("secure_store_unavailable") from error
        if raw is None:
            return None
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as error:
            raise CredentialStoreError("invalid_stored_session") from error
        return StoredSession.from_dict(value)

    def set(self, profile: str, session: StoredSession) -> None:
        username = _profile_username(profile)
        try:
            self.backend.set_password(_SERVICE_NAME, username, session.to_json())
        except Exception as error:
            raise CredentialStoreError("secure_store_unavailable") from error

    def delete(self, profile: str) -> None:
        username = _profile_username(profile)
        try:
            self.backend.delete_password(_SERVICE_NAME, username)
        except Exception as error:
            if keyring is not None and isinstance(error, keyring.errors.PasswordDeleteError):
                return
            raise CredentialStoreError("secure_store_unavailable") from error


class CredentialLock(AbstractContextManager["CredentialLock"]):
    """A metadata-only cross-process lock around refresh credential rotation."""

    def __init__(self, profile: str, *, timeout_seconds: float = 10):
        validate_profile(profile)
        digest = hashlib.sha256(profile.encode("utf-8")).hexdigest()
        root = Path(
            os.environ.get(
                "XDG_CACHE_HOME",
                str(Path.home() / ".cache"),
            )
        ) / "hormuz"
        if root.exists() and root.is_symlink():
            raise CredentialStoreError("credential_lock_symlink_refused")
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            root.chmod(0o700)
        except OSError:
            pass
        self.path = root / f"session-{digest}.lock"
        self.timeout_seconds = timeout_seconds
        self._stream = None

    def __enter__(self) -> "CredentialLock":
        flags = (
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(self.path, flags, 0o600)
        except OSError as error:
            raise CredentialStoreError("credential_lock_open_failed") from error
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise CredentialStoreError("credential_lock_regular_file_required")
            if hasattr(os, "fchmod"):
                os.fchmod(descriptor, 0o600)
        except Exception:
            os.close(descriptor)
            raise
        if os.name == "nt" and os.fstat(descriptor).st_size == 0:  # pragma: no cover - Windows CI
            os.write(descriptor, b"\x00")
            os.lseek(descriptor, 0, os.SEEK_SET)
        if self.path.is_symlink():
            os.close(descriptor)
            raise CredentialStoreError("credential_lock_symlink_refused")
        self._stream = os.fdopen(descriptor, "a+")
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                _try_lock(self._stream)
                return self
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    self._stream.close()
                    self._stream = None
                    raise CredentialStoreError("credential_refresh_lock_timeout")
                time.sleep(0.05)

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if self._stream is not None:
            _unlock(self._stream)
            self._stream.close()
            self._stream = None
        return None


def _validate_backend(backend: object) -> None:
    module = backend.__class__.__module__
    name = backend.__class__.__name__.lower()
    if any(fragment in (module + "." + name).lower() for fragment in ("plaintext", "null", "fail")):
        raise CredentialStoreError("unsupported_secure_store")
    if module.startswith("keyring.backends.chainer"):
        children = tuple(getattr(backend, "backends", ()))
        if not children:
            raise CredentialStoreError("unsupported_secure_store")
        for child in children:
            _validate_backend(child)
        return
    system = platform.system()
    allowed_prefixes = {
        "Darwin": ("keyring.backends.macOS",),
        "Windows": ("keyring.backends.Windows",),
        "Linux": ("keyring.backends.SecretService", "keyring.backends.kwallet"),
    }.get(system, ())
    if not allowed_prefixes or not module.startswith(allowed_prefixes):
        raise CredentialStoreError("unsupported_secure_store")


def _profile_username(profile: str) -> str:
    validate_profile(profile)
    return "v1:" + hashlib.sha256(profile.encode("utf-8")).hexdigest()


def validate_profile(profile: str) -> str:
    if (
        not isinstance(profile, str)
        or not 1 <= len(profile) <= 64
        or any(not (character.isalnum() or character in {"-", "_", "."}) for character in profile)
    ):
        raise CredentialStoreError("invalid_profile")
    return profile


def _required_string(value: object, *, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > maximum:
        raise ValueError("invalid string")
    if any(character in value for character in ("\n", "\r", "\x00")):
        raise ValueError("invalid string")
    return value


def _parse_time(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("invalid timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("invalid timestamp")
    return parsed.astimezone(timezone.utc)


def _try_lock(stream) -> None:
    if os.name == "nt":  # pragma: no cover - exercised on Windows CI
        import msvcrt

        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise BlockingIOError from error
    else:
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(stream) -> None:
    if os.name == "nt":  # pragma: no cover - exercised on Windows CI
        import msvcrt

        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    else:
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
