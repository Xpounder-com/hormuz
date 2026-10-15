"""Single-owner lifetime for the customer-local request ledger."""
from contextlib import contextmanager
import os
from pathlib import Path
import stat
from .work_runtime import WorkRuntimeError


@contextmanager
def owner_lock(path, *, exclusive=True):
    import fcntl
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = path.with_suffix(path.suffix + ".owner.lock")
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        info = os.fstat(descriptor)
        if info.st_uid != os.getuid() or info.st_nlink != 1 or not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise WorkRuntimeError("work_owner_lock_unsafe", 503)
        try:
            fcntl.flock(descriptor, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError:
            raise WorkRuntimeError("work_owner_active", 503) from None
        yield
    finally:
        os.close(descriptor)
