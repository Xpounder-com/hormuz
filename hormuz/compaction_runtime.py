"""Local preferences and tokenizer resources for context optimization."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from .compaction_formats import strict_json_loads
from .credential_store import CredentialStoreError, validate_profile


SETTINGS_SCHEMA_VERSION = 1
MAX_SETTINGS_BYTES = 4 * 1024
TOKENIZER_VERSION = "0.12.0"
ENCODING_URLS = {
    "cl100k_base": "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken",
    "o200k_base": "https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken",
}
ENCODING_SHA256 = {
    "cl100k_base": "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7",
    "o200k_base": "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d",
}


class ContextRuntimeError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ContextPreference:
    enabled: bool = False
    schema_version: int = SETTINGS_SCHEMA_VERSION


def default_state_directory() -> Path:
    override = os.environ.get("HORMUZ_CLIENT_STATE_DIRECTORY")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Hormuz"
    root = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    return root / "hormuz"


class ContextPreferenceStore:
    def __init__(self, directory: Path, profile: str):
        try:
            validate_profile(profile)
        except CredentialStoreError as error:
            raise ContextRuntimeError("invalid_profile") from error
        self.directory = Path(os.path.abspath(directory.expanduser()))
        self.profile = profile
        self.path = self.directory / f"context-optimization-{profile}.json"
        self.lock_path = self.directory / "context-optimization.lock"

    def load(self) -> ContextPreference:
        self._prepare_directory(create=False)
        try:
            self.path.lstat()
        except FileNotFoundError:
            return ContextPreference()
        except OSError as error:
            raise ContextRuntimeError("settings_invalid") from error
        data = self._read_regular(self.path, MAX_SETTINGS_BYTES)
        try:
            value = strict_json_loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as error:
            raise ContextRuntimeError("settings_invalid") from error
        if (
            not isinstance(value, dict)
            or set(value) != {"schema_version", "enabled"}
            or type(value.get("schema_version")) is not int
            or value["schema_version"] != SETTINGS_SCHEMA_VERSION
            or not isinstance(value.get("enabled"), bool)
        ):
            raise ContextRuntimeError("settings_invalid")
        canonical = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if data != canonical:
            raise ContextRuntimeError("settings_invalid")
        return ContextPreference(enabled=value["enabled"])

    def save(self, enabled: bool) -> ContextPreference:
        if not isinstance(enabled, bool):
            raise ContextRuntimeError("enabled_must_be_boolean")
        self._prepare_directory(create=True)
        lock_fd = self._open_lock()
        try:
            self._lock(lock_fd)
            self.load()
            body = json.dumps(
                {"schema_version": SETTINGS_SCHEMA_VERSION, "enabled": enabled},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            temporary_fd, temporary_name = tempfile.mkstemp(
                prefix=".context-optimization-", dir=self.directory
            )
            try:
                os.fchmod(temporary_fd, 0o600)
                with os.fdopen(temporary_fd, "wb", closefd=True) as stream:
                    stream.write(body)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary_name, self.path)
                directory_fd = os.open(self.directory, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except Exception:
                try:
                    os.unlink(temporary_name)
                except OSError:
                    pass
                raise
            result = self.load()
            if result.enabled != enabled:
                # The prior state remains the meaningful state if a hostile or
                # concurrent edit defeated verification.
                raise ContextRuntimeError("settings_write_failed")
            return result
        except ContextRuntimeError:
            raise
        except OSError as error:
            raise ContextRuntimeError("settings_write_failed") from error
        finally:
            self._unlock(lock_fd)
            os.close(lock_fd)

    def _prepare_directory(self, *, create: bool) -> None:
        if create:
            try:
                self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            except OSError as error:
                raise ContextRuntimeError("settings_directory_unavailable") from error
        try:
            info = self.directory.lstat()
        except FileNotFoundError:
            return
        except OSError as error:
            raise ContextRuntimeError("settings_directory_unavailable") from error
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or self.directory.is_symlink()
        ):
            raise ContextRuntimeError("settings_directory_unsafe")

    @staticmethod
    def _read_regular(path: Path, maximum: int) -> bytes:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(path, flags)
        except OSError as error:
            raise ContextRuntimeError("settings_invalid") from error
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_nlink != 1
                or info.st_mode & 0o077
                or info.st_size > maximum
            ):
                raise ContextRuntimeError("settings_invalid")
            chunks: list[bytes] = []
            remaining = maximum + 1
            while remaining:
                chunk = os.read(fd, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
            if len(data) > maximum:
                raise ContextRuntimeError("settings_invalid")
            return data
        finally:
            os.close(fd)

    def _open_lock(self) -> int:
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(self.lock_path, flags, 0o600)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
                os.close(fd)
                raise ContextRuntimeError("settings_lock_unsafe")
            os.fchmod(fd, 0o600)
            return fd
        except ContextRuntimeError:
            raise
        except OSError as error:
            raise ContextRuntimeError("settings_lock_unavailable") from error

    @staticmethod
    def _lock(fd: int) -> None:
        if os.name == "nt":  # pragma: no cover - Windows packaging only
            import msvcrt

            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)

    @staticmethod
    def _unlock(fd: int) -> None:
        try:
            if os.name == "nt":  # pragma: no cover - Windows packaging only
                import msvcrt

                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass


def load_token_counters(cache_directory: Path | None = None) -> Mapping[str, Callable[[str], int]]:
    cache = cache_directory or _configured_tokenizer_cache()
    if cache is None or not cache.is_dir() or cache.is_symlink():
        raise ContextRuntimeError("resources_unavailable")
    for name, url in ENCODING_URLS.items():
        resource = cache / hashlib.sha1(url.encode("utf-8")).hexdigest()
        if (
            not resource.is_file()
            or resource.is_symlink()
            or resource.stat().st_size == 0
            or _sha256_file(resource) != ENCODING_SHA256[name]
        ):
            raise ContextRuntimeError("resources_unavailable")
    try:
        import tiktoken
    except ImportError as error:
        raise ContextRuntimeError("resources_unavailable") from error
    if getattr(tiktoken, "__version__", None) != TOKENIZER_VERSION:
        raise ContextRuntimeError("resources_unavailable")
    previous = os.environ.get("TIKTOKEN_CACHE_DIR")
    os.environ["TIKTOKEN_CACHE_DIR"] = str(cache)
    try:
        encodings = {name: tiktoken.get_encoding(name) for name in ENCODING_URLS}
    except Exception as error:
        raise ContextRuntimeError("resources_unavailable") from error
    finally:
        if previous is None:
            del os.environ["TIKTOKEN_CACHE_DIR"]
        else:
            os.environ["TIKTOKEN_CACHE_DIR"] = previous
    return {
        name: lambda value, encoding=encoding: len(
            encoding.encode(value, disallowed_special=())
        )
        for name, encoding in encodings.items()
    }


def _configured_tokenizer_cache() -> Path | None:
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable).resolve()
        if executable.parent.name == "Helpers" and executable.parent.parent.name == "Contents":
            return executable.parent.parent / "Resources" / "ContextTokenizers"
    value = os.environ.get("HORMUZ_CONTEXT_TOKENIZER_CACHE")
    if value:
        return Path(value).expanduser()
    return default_tokenizer_cache_directory()


def default_tokenizer_cache_directory(state_directory: Path | None = None) -> Path:
    root = default_state_directory() if state_directory is None else Path(state_directory).expanduser()
    return root / f"context-tokenizers-{TOKENIZER_VERSION}"


def _sha256_file(path: Path) -> str:
    value = hashlib.sha256()
    try:
        with path.open("rb") as source:
            while chunk := source.read(64 * 1024):
                value.update(chunk)
    except OSError as error:
        raise ContextRuntimeError("resources_unavailable") from error
    return value.hexdigest()
