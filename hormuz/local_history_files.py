"""Private, bounded local files for the separately consented history store."""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import stat
import sys
import tempfile
import time


class LocalHistoryError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


MAX_FILE_BYTES = 262_144


def local_path(path: Path) -> Path:
    path = path.expanduser().absolute()
    if sys.platform == "darwin" and path.parts[1:2] in {("var",), ("tmp",)}:
        return Path("/private") / path.relative_to("/")
    return path


def safe_path(path: Path, *, private: bool = False) -> bool:
    """Reject user-controlled links and writable ancestors before local I/O.

    macOS's root-owned /var and /tmp aliases are resolved by the caller before
    reaching this boundary. A root-owned sticky temporary directory is safe as
    an ancestor; the history directory itself must always be private.
    """
    if os.name != "posix":
        raise LocalHistoryError("local_history_platform_unsupported")
    exists = True
    for item in reversed((path, *path.parents)):
        try:
            info = item.lstat()
        except FileNotFoundError:
            exists = False
            continue
        root_temporary = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in {0, os.getuid()}
                or (info.st_mode & 0o022 and not root_temporary)):
            raise LocalHistoryError("local_history_directory_unsafe")
        if item == path and private and (info.st_uid != os.getuid() or info.st_mode & 0o077):
            raise LocalHistoryError("local_history_directory_unsafe")
    return exists


def private_directory(path: Path) -> None:
    safe_path(path)
    missing = []
    for item in (path, *path.parents):
        if item.exists():
            break
        missing.append(item)
    for item in reversed(missing):
        try:
            item.mkdir(mode=0o700)
        except FileExistsError:
            pass
        safe_path(item, private=True)
    safe_path(path, private=True)


def _open(path: Path, flags: int, *, maximum: int = MAX_FILE_BYTES) -> int:
    try:
        descriptor = os.open(path, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    except FileNotFoundError:
        raise
    except OSError:
        raise LocalHistoryError("local_history_file_unsafe") from None
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_nlink != 1 or info.st_mode & 0o077 or info.st_size > maximum):
            raise LocalHistoryError("local_history_file_unsafe")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def read_private(path: Path, *, maximum: int = MAX_FILE_BYTES) -> bytes | None:
    try:
        descriptor = _open(path, os.O_RDONLY, maximum=maximum)
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, "rb") as stream:
        value = stream.read(maximum + 1)
    if len(value) > maximum:
        raise LocalHistoryError("local_history_file_unsafe")
    return value


def sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_private(path: Path, value: bytes) -> None:
    """Only ciphertext or a random key is passed to this atomic writer."""
    if len(value) > MAX_FILE_BYTES:
        raise LocalHistoryError("local_history_entry_too_large")
    safe_path(path.parent, private=True)
    # Check an existing target without following links before replacing it.
    read_private(path)
    descriptor, temporary = tempfile.mkstemp(prefix=".history-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def locked(directory: Path, *, create: bool = False):
    """One process/thread lock, at most two seconds, no background workers."""
    if not safe_path(directory, private=True):
        if not create:
            yield False
            return
        private_directory(directory)
    import fcntl
    descriptor = _open(directory / "lock", os.O_CREAT | os.O_RDWR, maximum=0)
    try:
        deadline = time.monotonic() + 2
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise LocalHistoryError("local_history_busy") from None
                time.sleep(0.025)
        safe_path(directory, private=True)
        yield True
    finally:
        os.close(descriptor)


def export_private(path: Path, value: bytes) -> None:
    safe_path(path.parent)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        raise LocalHistoryError("local_history_export_exists") from None
    except OSError:
        raise LocalHistoryError("local_history_export_unavailable") from None
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        sync_directory(path.parent)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
