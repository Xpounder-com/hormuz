"""Private local profile contract for the independently versioned personal release."""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .adapters import adapter_for
from .compaction_formats import strict_json_loads
from .credential_store import CredentialStoreError, validate_profile


PERSONAL_RELEASE_VERSION = "0.1.0"
PERSONAL_PROFILE_SCHEMA_VERSION = 1
MAX_PROFILE_BYTES = 16 * 1024
Mode = Literal["direct", "managed"]
Provider = Literal["openai", "anthropic", "hormuz"]


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
    allow_insecure_http: bool = False
    managed_profile: str | None = None
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
            "allow_insecure_http": self.allow_insecure_http,
            "managed_profile": self.managed_profile,
            "transform_version": self.transform_version,
        }


class PersonalProfileStore:
    def __init__(self, state_directory: Path):
        self.state_directory = Path(os.path.abspath(state_directory.expanduser()))
        self.directory = self.state_directory / "personal-profiles"

    def path_for(self, key: str) -> Path:
        return self.directory / (validate_personal_key(key) + ".json")

    def save(self, profile: PersonalProfile) -> None:
        validate_personal_profile(profile)
        self._prepare(create=True)
        destination = self.path_for(profile.key)
        if destination.exists() or destination.is_symlink():
            raise PersonalProfileError("personal_profile_exists")
        data = json.dumps(
            profile.to_dict(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        temporary: str | None = None
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
            raise PersonalProfileError("personal_profile_write_failed") from error
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

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
            loaded = self.load(key)
            if loaded.key != key:
                raise PersonalProfileError("personal_profile_invalid")
            path.unlink()
            directory_fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            return True
        except FileNotFoundError:
            return False
        except PersonalProfileError as error:
            if error.code == "personal_profile_unavailable" and not path.exists():
                return False
            raise
        except OSError as error:
            raise PersonalProfileError("personal_profile_remove_failed") from error

    def _prepare(self, *, create: bool) -> None:
        if create:
            _private_directory(self.state_directory, create=True)
            _private_directory(self.directory, create=True)
            return
        _private_directory(self.state_directory, create=False)
        _private_directory(self.directory, create=False)


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
        "transform_version",
    }
    if (
        not isinstance(value, dict)
        or set(value) != expected
        or value.get("schema_version") != PERSONAL_PROFILE_SCHEMA_VERSION
        or value.get("release_version") != PERSONAL_RELEASE_VERSION
    ):
        raise PersonalProfileError("personal_profile_invalid")
    try:
        profile = PersonalProfile(
            key=validate_personal_key(value.get("key")),
            mode=value.get("mode"),  # type: ignore[arg-type]
            agent=value.get("agent"),  # type: ignore[arg-type]
            provider=value.get("provider"),  # type: ignore[arg-type]
            endpoint=value.get("endpoint"),  # type: ignore[arg-type]
            model=value.get("model"),  # type: ignore[arg-type]
            allow_insecure_http=value.get("allow_insecure_http"),  # type: ignore[arg-type]
            managed_profile=value.get("managed_profile"),  # type: ignore[arg-type]
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
        or not isinstance(profile.allow_insecure_http, bool)
        or profile.transform_version != "structural-v1"
    ):
        raise PersonalProfileError("personal_profile_invalid")
    endpoint = validate_personal_endpoint(
        profile.endpoint, allow_insecure_http=profile.allow_insecure_http
    )
    if endpoint != profile.endpoint:
        raise PersonalProfileError("personal_profile_invalid")
    if profile.mode == "managed":
        if profile.provider != "hormuz" or profile.managed_profile is None:
            raise PersonalProfileError("personal_profile_invalid")
        try:
            validate_profile(profile.managed_profile)
        except CredentialStoreError as error:
            raise PersonalProfileError("personal_profile_invalid") from error
        if profile.agent == "aider":
            # The managed gateway does not expose Chat Completions.
            raise PersonalProfileError("managed_agent_unsupported")
    elif profile.managed_profile is not None or profile.provider == "hormuz":
        raise PersonalProfileError("personal_profile_invalid")
    if profile.provider == "openai" and "anthropic" in adapter.capabilities.protocols:
        raise PersonalProfileError("provider_protocol_mismatch")
    if profile.provider == "anthropic" and "anthropic" not in adapter.capabilities.protocols:
        raise PersonalProfileError("provider_protocol_mismatch")
    return profile


def validate_personal_key(value: object) -> str:
    if not isinstance(value, str):
        raise PersonalProfileError("invalid_personal_profile_key")
    try:
        return validate_profile(value)
    except CredentialStoreError as error:
        raise PersonalProfileError("invalid_personal_profile_key") from error


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
