"""Private local profile contract for the independently versioned personal release."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import tempfile
import threading
import urllib.parse
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .adapters import adapter_for
from .compaction_formats import strict_json_loads
from .credential_store import CredentialStoreError, validate_profile


PERSONAL_RELEASE_VERSION = "0.1.0"
PERSONAL_PROFILE_SCHEMA_VERSION = 3
PERSONAL_REMOVAL_STATE_SCHEMA_VERSION = 2
MAX_PROFILE_BYTES = 16 * 1024
MAX_REMOVAL_STATE_BYTES = 1024
Mode = Literal["direct", "managed"]
Provider = Literal["openai", "anthropic", "hormuz"]
PreferenceAction = Literal["clear", "enable", "disable", "preserve"]


_PROFILE_THREAD_LOCKS: dict[str, threading.Lock] = {}
_PROFILE_THREAD_LOCKS_GUARD = threading.Lock()


class PersonalProfileError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PersonalProfile:
    key: str
    mode: Mode
    agent: str
    provider: Provider
    endpoint: str
    model: str
    generation: str
    allow_insecure_http: bool = False
    managed_profile: str | None = None
    credential_env: str | None = None
    previous_preference_enabled: bool | None = None
    transform_version: str = "structural-v1"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": PERSONAL_PROFILE_SCHEMA_VERSION,
            "release_version": PERSONAL_RELEASE_VERSION,
            "key": self.key,
            "mode": self.mode,
            "agent": self.agent,
            "provider": self.provider,
            "endpoint": self.endpoint,
            "model": self.model,
            "generation": self.generation,
            "allow_insecure_http": self.allow_insecure_http,
            "managed_profile": self.managed_profile,
            "credential_env": self.credential_env,
            "previous_preference_enabled": self.previous_preference_enabled,
            "transform_version": self.transform_version,
        }


@dataclass(frozen=True)
class PersonalRemovalState:
    """Content-free state needed to finish an interrupted profile removal."""

    key: str
    credential_cleanup_required: bool
    preference_action: PreferenceAction
    profile_generation: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": PERSONAL_REMOVAL_STATE_SCHEMA_VERSION,
            "key": self.key,
            "credential_cleanup_required": self.credential_cleanup_required,
            "preference_action": self.preference_action,
            "profile_generation": self.profile_generation,
        }


class PersonalProfileStore:
    def __init__(self, state_directory: Path):
        self.state_directory = Path(os.path.abspath(state_directory.expanduser()))
        self.directory = self.state_directory / "personal-profiles"

    def path_for(self, key: str) -> Path:
        return self.directory / (validate_personal_key(key) + ".json")

    def removal_state_path_for(self, key: str) -> Path:
        validated = validate_personal_key(key)
        digest = hashlib.sha256(validated.encode("utf-8")).hexdigest()
        return self.directory / (".removal-" + digest + ".json")

    def entry_exists(self, key: str) -> bool:
        """Return whether the validated profile entry itself is present.

        ``lstat`` deliberately treats corrupt files and dangling symlinks as
        present so removal can recover them without following their targets.
        """

        self._prepare(create=False)
        try:
            self.path_for(key).lstat()
        except FileNotFoundError:
            return False
        except OSError as error:
            raise PersonalProfileError("personal_profile_unavailable") from error
        return True

    @contextmanager
    def transaction(self, key: str, *, create: bool) -> Iterator[None]:
        """Serialize a profile's connect/remove transaction across processes."""

        validated = validate_personal_key(key)
        self._prepare(create=create)
        digest = hashlib.sha256(validated.encode("utf-8")).hexdigest()
        lock_path = self.directory / (".profile-" + digest + ".lock")
        thread_key = os.fspath(lock_path)
        with _PROFILE_THREAD_LOCKS_GUARD:
            thread_lock = _PROFILE_THREAD_LOCKS.setdefault(thread_key, threading.Lock())
        with thread_lock:
            with _ProfileFileLock(lock_path):
                yield

    def save(self, profile: PersonalProfile) -> None:
        validate_personal_profile(profile)
        self._prepare(create=True)
        destination = self.path_for(profile.key)
        if destination.exists() or destination.is_symlink():
            raise PersonalProfileError("personal_profile_exists")
        data = json.dumps(
            profile.to_dict(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        if len(data) > MAX_PROFILE_BYTES:
            raise PersonalProfileError("personal_profile_invalid")
        temporary: str | None = None
        destination_linked = False
        try:
            descriptor, temporary = tempfile.mkstemp(
                prefix=".personal-profile-", dir=self.directory
            )
            with os.fdopen(descriptor, "wb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary, destination, follow_symlinks=False)
            destination_linked = True
            os.unlink(temporary)
            temporary = None
            directory_fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except FileExistsError as error:
            raise PersonalProfileError("personal_profile_exists") from error
        except OSError as error:
            if destination_linked:
                try:
                    destination.unlink()
                except OSError:
                    pass
            raise PersonalProfileError("personal_profile_write_failed") from error
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def save_removal_state(self, state: PersonalRemovalState) -> None:
        validate_personal_removal_state(state)
        self._prepare(create=True)
        destination = self.removal_state_path_for(state.key)
        data = json.dumps(
            state.to_dict(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        if len(data) > MAX_REMOVAL_STATE_BYTES:
            raise PersonalProfileError("personal_removal_state_invalid")
        temporary: str | None = None
        try:
            descriptor, temporary = tempfile.mkstemp(
                prefix=".personal-removal-", dir=self.directory
            )
            with os.fdopen(descriptor, "wb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
            temporary = None
            directory_fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as error:
            raise PersonalProfileError("personal_removal_state_write_failed") from error
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def load_removal_state(self, key: str) -> PersonalRemovalState | None:
        self._prepare(create=False)
        path = self.removal_state_path_for(key)
        try:
            descriptor = os.open(
                path,
                os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            )
        except FileNotFoundError:
            return None
        except OSError as error:
            raise PersonalProfileError("personal_removal_state_unavailable") from error
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_nlink != 1
                or info.st_mode & 0o077
                or info.st_size > MAX_REMOVAL_STATE_BYTES
            ):
                raise PersonalProfileError("personal_removal_state_invalid")
            chunks: list[bytes] = []
            remaining = MAX_REMOVAL_STATE_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
        finally:
            os.close(descriptor)
        if len(data) > MAX_REMOVAL_STATE_BYTES:
            raise PersonalProfileError("personal_removal_state_invalid")
        try:
            value = strict_json_loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as error:
            raise PersonalProfileError("personal_removal_state_invalid") from error
        state = parse_personal_removal_state(value)
        if state.key != key:
            raise PersonalProfileError("personal_removal_state_invalid")
        return state

    def clear_removal_state(self, key: str) -> bool:
        self._prepare(create=False)
        try:
            self.removal_state_path_for(key).unlink()
            directory_fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            return True
        except FileNotFoundError:
            return False
        except OSError as error:
            raise PersonalProfileError("personal_removal_state_remove_failed") from error

    def load(self, key: str) -> PersonalProfile:
        self._prepare(create=False)
        path = self.path_for(key)
        try:
            descriptor = os.open(
                path,
                os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            )
        except OSError as error:
            raise PersonalProfileError("personal_profile_unavailable") from error
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_nlink != 1
                or info.st_mode & 0o077
                or info.st_size > MAX_PROFILE_BYTES
            ):
                raise PersonalProfileError("personal_profile_invalid")
            chunks: list[bytes] = []
            remaining = MAX_PROFILE_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
        finally:
            os.close(descriptor)
        if len(data) > MAX_PROFILE_BYTES:
            raise PersonalProfileError("personal_profile_invalid")
        try:
            value = strict_json_loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as error:
            raise PersonalProfileError("personal_profile_invalid") from error
        profile = parse_personal_profile(value)
        if profile.key != key:
            raise PersonalProfileError("personal_profile_invalid")
        return profile

    def remove(self, key: str) -> bool:
        self._prepare(create=False)
        path = self.path_for(key)
        try:
            # Unlinking a validated entry name never follows its target. This
            # deliberately permits recovery from corrupt files, unsafe link
            # counts/permissions, and dangling symlinks that cannot be loaded.
            path.unlink()
            directory_fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            return True
        except FileNotFoundError:
            return False
        except OSError as error:
            raise PersonalProfileError("personal_profile_remove_failed") from error

    def _prepare(self, *, create: bool) -> None:
        if create:
            _private_directory(self.state_directory, create=True)
            _private_directory(self.directory, create=True)
            return
        _private_directory(self.state_directory, create=False)
        _private_directory(self.directory, create=False)


class _ProfileFileLock(AbstractContextManager["_ProfileFileLock"]):
    def __init__(self, path: Path):
        self.path = path
        self._stream = None

    def __enter__(self) -> "_ProfileFileLock":
        flags = (
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(self.path, flags, 0o600)
        except OSError as error:
            raise PersonalProfileError("personal_profile_lock_unavailable") from error
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_nlink != 1
                or info.st_mode & 0o077
            ):
                raise PersonalProfileError("personal_profile_lock_unsafe")
            os.fchmod(descriptor, 0o600)
            if os.name == "nt" and info.st_size == 0:  # pragma: no cover - Windows CI
                os.write(descriptor, b"\x00")
                os.lseek(descriptor, 0, os.SEEK_SET)
            self._stream = os.fdopen(descriptor, "a+")
            descriptor = -1
            _lock_profile_stream(self._stream)
            return self
        except Exception:
            if self._stream is not None:
                self._stream.close()
                self._stream = None
            elif descriptor >= 0:
                os.close(descriptor)
            raise

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if self._stream is not None:
            _unlock_profile_stream(self._stream)
            self._stream.close()
            self._stream = None
        return None


def _lock_profile_stream(stream) -> None:
    if os.name == "nt":  # pragma: no cover - Windows CI
        import msvcrt

        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
    else:
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)


def _unlock_profile_stream(stream) -> None:
    if os.name == "nt":  # pragma: no cover - Windows CI
        import msvcrt

        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    else:
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def parse_personal_removal_state(value: object) -> PersonalRemovalState:
    expected = {
        "schema_version",
        "key",
        "credential_cleanup_required",
        "preference_action",
    }
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("credential_cleanup_required"), bool)
    ):
        raise PersonalProfileError("personal_removal_state_invalid")
    schema_version = value.get("schema_version")
    if schema_version == 1:
        profile_generation = None
    elif schema_version == PERSONAL_REMOVAL_STATE_SCHEMA_VERSION:
        expected.add("profile_generation")
        profile_generation = value.get("profile_generation")
    else:
        raise PersonalProfileError("personal_removal_state_invalid")
    if set(value) != expected:
        raise PersonalProfileError("personal_removal_state_invalid")
    try:
        state = PersonalRemovalState(
            key=validate_personal_key(value.get("key")),
            credential_cleanup_required=value["credential_cleanup_required"],
            preference_action=value.get("preference_action"),  # type: ignore[arg-type]
            profile_generation=profile_generation,  # type: ignore[arg-type]
        )
        return validate_personal_removal_state(state)
    except (TypeError, CredentialStoreError, PersonalProfileError) as error:
        raise PersonalProfileError("personal_removal_state_invalid") from error


def validate_personal_removal_state(
    state: PersonalRemovalState,
) -> PersonalRemovalState:
    try:
        validate_personal_key(state.key)
    except PersonalProfileError as error:
        raise PersonalProfileError("personal_removal_state_invalid") from error
    if (
        not isinstance(state.credential_cleanup_required, bool)
        or state.preference_action not in {"clear", "enable", "disable", "preserve"}
        or (
            state.profile_generation is not None
            and not _is_profile_generation(state.profile_generation)
        )
    ):
        raise PersonalProfileError("personal_removal_state_invalid")
    return state


def parse_personal_profile(value: object) -> PersonalProfile:
    expected = {
        "schema_version",
        "release_version",
        "key",
        "mode",
        "agent",
        "provider",
        "endpoint",
        "model",
        "allow_insecure_http",
        "managed_profile",
        "credential_env",
        "transform_version",
    }
    if (
        not isinstance(value, dict)
        or value.get("release_version") != PERSONAL_RELEASE_VERSION
    ):
        raise PersonalProfileError("personal_profile_invalid")
    schema_version = value.get("schema_version")
    if schema_version == 1:
        previous_preference_enabled = None
        generation = _legacy_profile_generation(value)
    elif schema_version == 2:
        expected.add("previous_preference_enabled")
        previous_preference_enabled = value.get("previous_preference_enabled")
        generation = _legacy_profile_generation(value)
    elif schema_version == PERSONAL_PROFILE_SCHEMA_VERSION:
        expected.update({"previous_preference_enabled", "generation"})
        previous_preference_enabled = value.get("previous_preference_enabled")
        generation = value.get("generation")
    else:
        raise PersonalProfileError("personal_profile_invalid")
    if set(value) != expected:
        raise PersonalProfileError("personal_profile_invalid")
    try:
        profile = PersonalProfile(
            key=validate_personal_key(value.get("key")),
            mode=value.get("mode"),  # type: ignore[arg-type]
            agent=value.get("agent"),  # type: ignore[arg-type]
            provider=value.get("provider"),  # type: ignore[arg-type]
            endpoint=value.get("endpoint"),  # type: ignore[arg-type]
            model=value.get("model"),  # type: ignore[arg-type]
            generation=generation,  # type: ignore[arg-type]
            allow_insecure_http=value.get("allow_insecure_http"),  # type: ignore[arg-type]
            managed_profile=value.get("managed_profile"),  # type: ignore[arg-type]
            credential_env=value.get("credential_env"),  # type: ignore[arg-type]
            previous_preference_enabled=previous_preference_enabled,  # type: ignore[arg-type]
            transform_version=value.get("transform_version"),  # type: ignore[arg-type]
        )
        return validate_personal_profile(profile)
    except (TypeError, CredentialStoreError) as error:
        raise PersonalProfileError("personal_profile_invalid") from error


def validate_personal_profile(profile: PersonalProfile) -> PersonalProfile:
    try:
        validate_personal_key(profile.key)
        adapter = adapter_for(profile.agent)
    except (CredentialStoreError, ValueError) as error:
        raise PersonalProfileError("personal_profile_invalid") from error
    if (
        profile.mode not in {"direct", "managed"}
        or profile.provider not in {"openai", "anthropic", "hormuz"}
        or not isinstance(profile.model, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,191}", profile.model) is None
        or not _is_profile_generation(profile.generation)
        or not isinstance(profile.allow_insecure_http, bool)
        or (
            profile.previous_preference_enabled is not None
            and not isinstance(profile.previous_preference_enabled, bool)
        )
        or profile.transform_version != "structural-v1"
    ):
        raise PersonalProfileError("personal_profile_invalid")
    endpoint = validate_personal_endpoint(
        profile.endpoint, allow_insecure_http=profile.allow_insecure_http
    )
    if endpoint != profile.endpoint:
        raise PersonalProfileError("personal_profile_invalid")
    if profile.mode == "managed":
        if (
            profile.provider != "hormuz"
            or profile.managed_profile is None
            or profile.credential_env is not None
        ):
            raise PersonalProfileError("personal_profile_invalid")
        try:
            validate_profile(profile.managed_profile)
        except CredentialStoreError as error:
            raise PersonalProfileError("personal_profile_invalid") from error
        if profile.agent == "aider":
            # The managed gateway does not expose Chat Completions.
            raise PersonalProfileError("managed_agent_unsupported")
    elif (
        profile.managed_profile is not None
        or profile.provider == "hormuz"
        or not isinstance(profile.credential_env, str)
        or re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", profile.credential_env) is None
    ):
        raise PersonalProfileError("personal_profile_invalid")
    if profile.provider == "openai" and "anthropic" in adapter.capabilities.protocols:
        raise PersonalProfileError("provider_protocol_mismatch")
    if profile.provider == "anthropic" and "anthropic" not in adapter.capabilities.protocols:
        raise PersonalProfileError("provider_protocol_mismatch")
    return profile


def new_personal_profile_generation() -> str:
    """Return a content-free immutable identity for one profile lifetime."""

    return secrets.token_hex(32)


def validate_personal_profile_generation(value: object) -> str:
    if not _is_profile_generation(value):
        raise PersonalProfileError("personal_profile_invalid")
    return value


def _is_profile_generation(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _legacy_profile_generation(value: dict[str, object]) -> str:
    """Derive a stable identity for strict schema-v1/v2 profile documents."""

    try:
        canonical = json.dumps(value, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    except (TypeError, ValueError) as error:
        raise PersonalProfileError("personal_profile_invalid") from error
    return hashlib.sha256(canonical).hexdigest()


def validate_personal_key(value: object) -> str:
    if not isinstance(value, str):
        raise PersonalProfileError("invalid_personal_profile_key")
    try:
        validated = validate_profile(value)
    except CredentialStoreError as error:
        raise PersonalProfileError("invalid_personal_profile_key") from error
    # Personal state spans profile, preference, metrics, keyring, and lock
    # namespaces.  A lowercase ASCII canonical form prevents case-insensitive
    # filesystems from mapping two accepted keys to one state entry while the
    # transaction layer derives different locks.
    if re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", validated) is None:
        raise PersonalProfileError("invalid_personal_profile_key")
    return validated


def validate_personal_endpoint(value: object, *, allow_insecure_http: bool) -> str:
    if not isinstance(value, str) or value != value.rstrip("/"):
        raise PersonalProfileError("invalid_personal_endpoint")
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise PersonalProfileError("invalid_personal_endpoint") from error
    loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or port is not None and not 1 <= port <= 65535
        or any(character in value for character in ("\r", "\n", "\x00"))
        or parsed.scheme not in {"https", "http"}
        or parsed.scheme == "http" and not (allow_insecure_http and loopback)
        or parsed.scheme == "https" and allow_insecure_http
    ):
        raise PersonalProfileError("invalid_personal_endpoint")
    return value


def _private_directory(path: Path, *, create: bool) -> None:
    try:
        if create:
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = path.lstat()
    except OSError as error:
        raise PersonalProfileError("personal_state_unavailable") from error
    if (
        not stat.S_ISDIR(info.st_mode)
        or path.is_symlink()
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        raise PersonalProfileError("personal_state_unsafe")


__all__ = [
    "PERSONAL_RELEASE_VERSION",
    "PersonalProfile",
    "PersonalProfileError",
    "PersonalProfileStore",
    "parse_personal_profile",
    "validate_personal_endpoint",
    "validate_personal_key",
    "validate_personal_profile",
]
